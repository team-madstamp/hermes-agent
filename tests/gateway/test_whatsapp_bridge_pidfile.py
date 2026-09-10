"""Regression tests: the WhatsApp stale-bridge cleanup must never kill a stranger.

The bridge records its PID in ``bridge.pid``. On the next start the gateway
SIGTERMs that PID to reap an orphaned bridge. The original code checked only
that the PID was *alive* — but once the bridge exits and is reaped the kernel
can recycle its number onto an unrelated process. Because the WhatsApp bridge
crash-loops, this cleanup ran constantly, and a recycled PID that had landed on
the user's browser main process got SIGTERMed, closing the browser at irregular
intervals (no crash, no coredump — a clean kill of a stranger).

These tests prove the identity guard: a PID is only signalled when its exact
bridge role and recorded kernel start time both match. A recycled, unrelated,
or legacy-unbound PID is left alone.
"""

import subprocess
import sys
import time

import pytest

import os
import socket
from unittest.mock import patch

import psutil

from plugins.platforms.whatsapp.adapter import (
    _kill_stale_bridge_by_pidfile,
    _listener_pids_on_port,
    _write_bridge_pidfile,
)
from gateway.status import get_process_start_time, _pid_exists


def _spawn_sleeper(*extra_argv) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", *extra_argv]
    )


def _wait_dead(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


class TestWriteAndRoundTrip:
    def test_pidfile_records_pid_and_start_time(self, tmp_path):
        proc = _spawn_sleeper()
        try:
            _write_bridge_pidfile(tmp_path, proc.pid)
            lines = (tmp_path / "bridge.pid").read_text().split("\n")
            assert int(lines[0]) == proc.pid
            # Line 2 is the kernel start time (present on Linux).
            assert int(lines[1]) == get_process_start_time(proc.pid)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


class TestIdentityGuard:
    def test_matching_start_time_without_bridge_identity_is_not_signalled(
        self, tmp_path
    ):
        proc = _spawn_sleeper()
        try:
            _write_bridge_pidfile(tmp_path, proc.pid)
            _kill_stale_bridge_by_pidfile(
                tmp_path, tmp_path / "bridge.js", 3000
            )
            assert proc.poll() is None
            assert not (tmp_path / "bridge.pid").exists()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def test_kills_when_runtime_identity_and_start_time_match(self, tmp_path):
        proc = _spawn_sleeper()
        try:
            _write_bridge_pidfile(tmp_path, proc.pid)
            identity_proc = psutil.Process(proc.pid)
            with patch(
                "plugins.platforms.whatsapp.adapter._verified_bridge_process",
                return_value=identity_proc,
            ):
                _kill_stale_bridge_by_pidfile(
                    tmp_path, tmp_path / "bridge.js", 3000
                )
            assert _wait_dead(proc), "the real bridge process should be killed"
            assert not (tmp_path / "bridge.pid").exists()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


    def test_legacy_pidfile_without_start_time_is_not_signalled(self, tmp_path):
        proc = _spawn_sleeper("node", str(tmp_path))
        try:
            (tmp_path / "bridge.pid").write_text(str(proc.pid))  # legacy: pid only
            _kill_stale_bridge_by_pidfile(
                tmp_path, tmp_path / "bridge.js", 3000
            )
            assert proc.poll() is None
            assert not (tmp_path / "bridge.pid").exists()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestKillPortProcess:
    """Freeing the bridge port must target only LISTENers, never clients.

    Root cause of the live Firefox kills: ``lsof -ti :PORT`` (and ``fuser
    PORT/tcp``) also returned *client* sockets whose connection merely involved
    the port number. The WhatsApp bridge uses port 3000 by default — a common
    local dev-server port — so a browser tab on ``localhost:3000`` was matched
    and SIGTERMed every time the (crash-looping) bridge restarted.
    """

    def test_listener_lookup_excludes_client_process(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(5)
        # A separate process holding a *client* connection to that port.
        client = subprocess.Popen([
            sys.executable, "-c",
            "import socket,time; c=socket.create_connection(('127.0.0.1',%d)); time.sleep(0.2)" % port,
        ])
        try:
            conn, _ = srv.accept()  # establish the client connection
            pids = _listener_pids_on_port(port)
            if os.getpid() not in pids:
                pytest.skip("neither lsof nor ss detected the listener here")
            # The listener (this process) is found; the client process is NOT —
            # the LISTEN filter is what spares unrelated clients like a browser.
            assert client.pid not in pids
            conn.close()
        finally:
            if client.poll() is None:
                client.kill()
            client.wait()
            srv.close()
