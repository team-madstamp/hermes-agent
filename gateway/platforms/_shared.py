"""Cross-adapter helpers shared by gateway/platforms/* and plugins/platforms/*.

Kept dependency-light (stdlib + ``agent.secret_scope``) so every adapter can
import it at module top level without cycles.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Literal

# Profile-scoped secret reader for multiplexing support (PR #50094)
from agent.secret_scope import UnscopedSecretError as _UnscopedSecretError
from agent.secret_scope import get_secret as _scoped_get_secret


@dataclass(frozen=True)
class ListenerReadback:
    state: Literal["present", "absent", "unavailable"]
    pids: tuple[int, ...]
    backend: str
    reason_code: str
    detail: str = ""


def _parse_lsof_pid_fields(stdout: str) -> tuple[int, ...] | None:
    pids: list[int] = []
    process_seen = False
    for field in (line for line in stdout.splitlines() if line):
        if field.startswith("p"):
            value = field[1:]
            if not value.isdigit() or int(value) <= 0:
                return None
            pids.append(int(value))
            process_seen = True
            continue
        if field.startswith("f") and process_seen and len(field) > 1:
            continue
        return None
    return tuple(sorted(set(pids)))


def read_tcp_listener_pids(
    port: int,
    *,
    runner: Callable[..., Any] | None = None,
    timeout: float = 5.0,
) -> ListenerReadback:
    run = runner or subprocess.run
    failures: list[str] = []
    commands = (
        ("lsof", ["lsof", "-nP", "-Fp", f"-iTCP:{port}", "-sTCP:LISTEN"]),
        ("ss", ["ss", "-ltnHp", f"sport = :{port}"]),
    )
    for backend, command in commands:
        try:
            result = run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            failures.append(f"{backend.upper()}_NOT_FOUND")
            continue
        except subprocess.TimeoutExpired:
            failures.append(f"{backend.upper()}_TIMEOUT")
            continue
        except OSError as exc:
            failures.append(f"{backend.upper()}_OSERROR_{type(exc).__name__}")
            continue

        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        if backend == "lsof":
            if result.returncode == 1 and not stdout.strip() and not stderr.strip():
                return ListenerReadback(
                    "absent", (), backend, "HOST_LISTENER_ABSENT"
                )
            if result.returncode == 0:
                pids = _parse_lsof_pid_fields(stdout)
                if pids is not None:
                    return ListenerReadback(
                        "present" if pids else "absent",
                        pids,
                        backend,
                        "HOST_LISTENER_PRESENT" if pids else "HOST_LISTENER_ABSENT",
                    )
                failures.append("LSOF_MALFORMED_OUTPUT")
                continue
            failures.append(f"LSOF_EXIT_{result.returncode}")
            continue

        if result.returncode == 0:
            pids = tuple(sorted({
                int(match.group(1))
                for match in re.finditer(r"pid=(\d+)", stdout)
            }))
            if pids:
                return ListenerReadback(
                    "present", pids, backend, "HOST_LISTENER_PRESENT"
                )
            if not stdout.strip():
                return ListenerReadback(
                    "absent", (), backend, "HOST_LISTENER_ABSENT"
                )
            failures.append("SS_OWNER_UNAVAILABLE")
            continue
        failures.append(f"SS_EXIT_{result.returncode}")

    return ListenerReadback(
        "unavailable",
        (),
        ",".join(backend for backend, _ in commands),
        "HOST_LISTENER_READBACK_UNAVAILABLE",
        ",".join(failures),
    )


def get_scoped_secret(name: str, default: Any = None) -> Any:
    """Scope-aware credential read with the default-profile startup fallback.

    An installed profile secret scope is authoritative: a scoped miss returns
    ``default`` (never borrow another profile's value from ``os.environ``).
    The DEFAULT profile constructs and sends *unscoped* under multiplexing,
    where a bare ``get_secret`` raises ``UnscopedSecretError``; there
    ``os.environ`` is that profile's own value, so fall back to it.
    """
    try:
        val = _scoped_get_secret(name, default)
    except _UnscopedSecretError:
        val = os.getenv(name)
    return val if val is not None else default


def profile_scoped() -> bool:
    # --------------------------------------------------------------------------- YAML → env config bridge
    # (apply_yaml_config_fn, #25443)
    # ---------------------------------------------------------------------------
    """True when running inside a multiplexed secondary profile's scope.

    Secondary-profile adapters are constructed/connected inside
    ``_profile_runtime_scope`` (secret scope installed + multiplex active).
    The DEFAULT profile under multiplexing runs unscoped and keeps the legacy
    ``os.environ`` precedence, so YAML->env bridges must skip only when True.
    """
    try:
        from agent.secret_scope import current_secret_scope, is_multiplex_active
        return bool(is_multiplex_active() and current_secret_scope() is not None)
    except Exception:
        return False


def coerce_port(value: Any, default: int) -> int:
    """``int(value)`` or ``default`` when unparseable."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
