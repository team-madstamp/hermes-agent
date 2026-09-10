"""Sidecar lifecycle tests: orphan reaping and parent-death wiring.

A hard gateway exit used to leave the detached Node sidecar squatting the
loopback port with a token the next gateway run doesn't know — every
replacement spawn then died on EADDRINUSE. These tests cover the startup
reaper (`_reap_stale_sidecar`) and the stdin-pipe lifetime binding, without
spawning Node or binding ports.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.photon import adapter as photon_adapter
from plugins.platforms.photon import sidecar_paths
from plugins.platforms.photon.adapter import PhotonAdapter


def _make_adapter(monkeypatch: pytest.MonkeyPatch) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    cfg = PlatformConfig(enabled=True, token="", extra={})
    return PhotonAdapter(cfg)


class _ProbeClient:
    """Fake httpx.AsyncClient whose /healthz probe behavior is injectable."""

    connects = True

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "_ProbeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def post(self, *a: Any, **k: Any) -> Any:
        if not self.connects:
            raise photon_adapter.httpx.ConnectError("connection refused")

        class _Resp:
            status_code = 401  # orphan with a different token

        return _Resp()


def _capture_kills(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[int, int]]:
    kills: List[Tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        kills.append((pid, sig))

    monkeypatch.setattr(photon_adapter.os, "kill", _fake_kill)
    return kills


def test_find_listener_pids_normalizes_lsof_records(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Done:
        returncode = 0
        stdout = "p55555\nf4\np55555\nf5\np44444\nf6\n"

    calls: List[List[str]] = []

    def _run(cmd: List[str], **kwargs: Any) -> _Done:
        calls.append(cmd)
        return _Done()

    monkeypatch.setattr(photon_adapter.subprocess, "run", _run)

    assert PhotonAdapter._find_listener_pids(8789) == [44444, 55555]
    assert calls == [["lsof", "-nP", "-Fp", "-iTCP:8789", "-sTCP:LISTEN"]]


def test_find_listener_pids_fails_closed_on_lsof_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(cmd: List[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(photon_adapter.subprocess, "run", _run)

    assert PhotonAdapter._find_listener_pids(8789) == []


def test_find_listener_pids_falls_back_to_ss(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Done:
        returncode = 0
        stdout = 'users:(("node",pid=55555,fd=3),("node",pid=55555,fd=4))'

    calls: List[List[str]] = []

    def _run(cmd: List[str], **kwargs: Any) -> Any:
        calls.append(cmd)
        if cmd[0] == "lsof":
            raise subprocess.TimeoutExpired(cmd, 5)
        return _Done()

    monkeypatch.setattr(photon_adapter.subprocess, "run", _run)

    assert PhotonAdapter._find_listener_pids(8789) == [55555]
    assert calls == [
        ["lsof", "-nP", "-Fp", "-iTCP:8789", "-sTCP:LISTEN"],
        ["ss", "-ltnHp", "sport = :8789"],
    ]


@pytest.mark.asyncio
async def test_reap_noop_when_port_free(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _make_adapter(monkeypatch)

    class _Refused(_ProbeClient):
        connects = False

    monkeypatch.setattr(photon_adapter.httpx, "AsyncClient", _Refused)
    kills = _capture_kills(monkeypatch)

    await adapter._reap_stale_sidecar()

    assert kills == []


@pytest.mark.asyncio
async def test_reap_refuses_listener_without_profile_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    monkeypatch.setattr(photon_adapter.httpx, "AsyncClient", _ProbeClient)
    monkeypatch.setattr(
        adapter,
        "_listener_readback",
        lambda _port: photon_adapter.ListenerReadback(
            "present", (55555,), "lsof", "HOST_LISTENER_PRESENT"
        ),
    )
    monkeypatch.setattr(photon_adapter, "_read_runtime_record", lambda: None)
    kills = _capture_kills(monkeypatch)

    with pytest.raises(RuntimeError, match="OWNER_UNCONFIRMED"):
        await adapter._reap_stale_sidecar()

    assert kills == []


@pytest.mark.asyncio
async def test_reap_signals_only_the_matching_recorded_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    adapter = _make_adapter(monkeypatch)
    sidecar_dir = tmp_path / "sidecar"
    node_path = tmp_path / "bin" / "node"
    sidecar_dir.mkdir()
    node_path.parent.mkdir()
    monkeypatch.setattr(sidecar_paths, "_SIDECAR_DIR", sidecar_dir)
    adapter._node_bin = str(node_path)

    proc = MagicMock()
    proc.pid = 55555
    proc.create_time.return_value = 123.5
    proc.is_running.return_value = True
    proc.exe.return_value = str(node_path)
    proc.cwd.return_value = str(tmp_path)
    proc.cmdline.return_value = [str(node_path), str(sidecar_dir / "index.mjs")]
    record = {
        "port": adapter._sidecar_port,
        "token": "old-token",
        "pid": proc.pid,
        "process_create_time": 123.5,
    }

    monkeypatch.setattr(photon_adapter.httpx, "AsyncClient", _ProbeClient)
    monkeypatch.setattr(
        adapter,
        "_listener_readback",
        lambda _port: photon_adapter.ListenerReadback(
            "present", (proc.pid,), "lsof", "HOST_LISTENER_PRESENT"
        ),
    )
    monkeypatch.setattr(photon_adapter, "_read_runtime_record", lambda: record)
    monkeypatch.setattr("psutil.Process", lambda _pid: proc)
    monkeypatch.setattr("psutil.wait_procs", lambda processes, timeout: (processes, []))

    await adapter._reap_stale_sidecar()

    proc.terminate.assert_called_once_with()
    mismatched = {**record, "process_create_time": 999.0}
    assert adapter._verified_recorded_sidecar_process(proc.pid, mismatched) is None


@pytest.mark.asyncio
async def test_stop_sidecar_waits_after_force_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch)
    fake_proc = type(
        "FakeProc",
        (),
        {
            "pid": 4242,
            "stdin": None,
            "wait": lambda self, timeout=None: None,
            "kill": lambda self: None,
        },
    )()
    waits = iter(
        [
            subprocess.TimeoutExpired(["node"], 3.0),
            subprocess.TimeoutExpired(["node"], 2.0),
            0,
        ]
    )
    wait_calls: List[float | None] = []

    def _wait(timeout=None):
        wait_calls.append(timeout)
        result = next(waits)
        if isinstance(result, BaseException):
            raise result
        return result

    fake_proc.wait = _wait
    adapter._sidecar_proc = fake_proc
    adapter._http_client = None
    adapter._sidecar_supervisor_task = None
    monkeypatch.setattr(photon_adapter.os, "getpgid", lambda _pid: 4242)
    monkeypatch.setattr(photon_adapter.os, "killpg", lambda _pgid, _sig: None)
    monkeypatch.setattr(photon_adapter, "_delete_runtime_record", lambda: None)

    await adapter._stop_sidecar()

    assert wait_calls == [3.0, 2.0, 2.0]


@pytest.mark.asyncio
async def test_start_sidecar_spawns_with_stdin_pipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The spawn must hold a stdin pipe and enable the sidecar's EOF watch."""
    adapter = _make_adapter(monkeypatch)

    async def _no_reap() -> None:
        pass

    monkeypatch.setattr(adapter, "_reap_stale_sidecar", _no_reap)
    (tmp_path / "node_modules" / "spectrum-ts").mkdir(parents=True)
    monkeypatch.setattr(sidecar_paths, "_SIDECAR_DIR", tmp_path)

    spawned: Dict[str, Any] = {}
    hidden_flags = 0x08000000
    monkeypatch.setattr(
        "hermes_cli._subprocess_compat.windows_hide_flags",
        lambda: hidden_flags,
    )

    class _PatchResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd: List[str], **kwargs: Any) -> _PatchResult:
        spawned["patch_cmd"] = cmd
        spawned["patch_kwargs"] = kwargs
        return _PatchResult()

    monkeypatch.setattr(photon_adapter.subprocess, "run", _fake_run)

    class _FakeProc:
        pid = 999
        stdout = None
        stdin = None

        @staticmethod
        def poll() -> None:
            return None

    def _fake_popen(cmd: List[str], **kwargs: Any) -> _FakeProc:
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(photon_adapter.subprocess, "Popen", _fake_popen)

    class _HealthyClient(_ProbeClient):
        async def post(self, *a: Any, **k: Any) -> Any:
            class _Resp:
                status_code = 200

            return _Resp()

    monkeypatch.setattr(photon_adapter.httpx, "AsyncClient", _HealthyClient)

    await adapter._start_sidecar()

    kwargs = spawned["kwargs"]
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["env"]["PHOTON_SIDECAR_WATCH_STDIN"] == "1"
    assert spawned["patch_kwargs"]["creationflags"] == hidden_flags
    assert kwargs["creationflags"] == hidden_flags


@pytest.mark.asyncio
async def test_spectrum_patch_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The node patch run must not block the shared gateway event loop.

    ``_start_sidecar`` spawns the Spectrum patch script and *waits* for it
    (``timeout=10``). Run inline it holds the loop for that whole window, so
    every other platform's traffic stalls — and ``_start_sidecar`` runs on
    every reconnect (``connect(is_reconnect=True)``), not just startup, so the
    stall recurs on a live gateway. The dep reinstall a few lines above already
    hops to a thread for exactly this reason; the patch run must too.
    """
    import threading

    adapter = _make_adapter(monkeypatch)
    main_thread = threading.current_thread()
    seen: Dict[str, Any] = {}

    # node_modules present + deps fresh, so we reach the patch run.
    monkeypatch.setattr(photon_adapter.Path, "exists", lambda self: True)
    monkeypatch.setattr(photon_adapter, "_sidecar_deps_stale", lambda: False)

    async def _no_reap() -> None:
        return None

    monkeypatch.setattr(adapter, "_reap_stale_sidecar", _no_reap)

    def _fake_run(*a: Any, **k: Any) -> Any:
        seen["thread"] = threading.current_thread()

        class _Done:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Done()

    monkeypatch.setattr(photon_adapter.subprocess, "run", _fake_run)

    class _FakeProc:
        pid = 4242
        stdin = None
        stdout = None

        def poll(self) -> int:
            # Report "exited" so the readiness health-poll loop bails out
            # immediately instead of spinning for its full 15s deadline —
            # the assertion below only cares where the patch run executed.
            return 0

    monkeypatch.setattr(
        photon_adapter.subprocess, "Popen", lambda *a, **k: _FakeProc()
    )

    try:
        await adapter._start_sidecar()
    except Exception:
        # Readiness/handshake past the patch run may fail under the fakes —
        # irrelevant here; we only assert where the patch run executed.
        pass

    assert seen.get("thread") is not None, "patch run never executed"
    assert seen["thread"] is not main_thread, (
        "Spectrum patch subprocess ran on the event-loop thread; it must be "
        "dispatched via asyncio.to_thread so a 10s node spawn can't freeze "
        "every other platform on the gateway loop"
    )
