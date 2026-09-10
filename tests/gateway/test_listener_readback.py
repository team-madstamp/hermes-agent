import subprocess
from unittest.mock import MagicMock

from gateway.platforms._shared import read_tcp_listener_pids


def _result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_lsof_field_output_preserves_all_distinct_pids():
    runner = MagicMock(
        return_value=_result(0, "p303\nf4\np101\nf5\np303\nf6\n")
    )

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "present"
    assert readback.pids == (101, 303)
    assert readback.backend == "lsof"
    assert readback.reason_code == "HOST_LISTENER_PRESENT"
    assert runner.call_args.args[0] == [
        "lsof",
        "-nP",
        "-Fp",
        "-iTCP:8789",
        "-sTCP:LISTEN",
    ]


def test_empty_lsof_no_match_is_absent_without_fallback():
    runner = MagicMock(return_value=_result(1))

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "absent"
    assert readback.pids == ()
    assert readback.reason_code == "HOST_LISTENER_ABSENT"
    assert runner.call_count == 1


def test_error_bearing_lsof_exit_is_not_absent():
    runner = MagicMock(
        side_effect=[_result(1, stderr="permission denied"), FileNotFoundError("ss")]
    )

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "unavailable"
    assert readback.reason_code == "HOST_LISTENER_READBACK_UNAVAILABLE"
    assert readback.detail == "LSOF_EXIT_1,SS_NOT_FOUND"


def test_malformed_lsof_can_fall_back_to_authoritative_ss_result():
    runner = MagicMock(
        side_effect=[
            _result(0, "303\n"),
            _result(0, 'users:(("node",pid=303,fd=3),("node",pid=101,fd=4))'),
        ]
    )

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "present"
    assert readback.pids == (101, 303)
    assert readback.backend == "ss"


def test_lsof_timeout_can_fall_back_to_ss_absence():
    runner = MagicMock(
        side_effect=[subprocess.TimeoutExpired(["lsof"], 5), _result(0)]
    )

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "absent"
    assert readback.backend == "ss"
    assert readback.reason_code == "HOST_LISTENER_ABSENT"


def test_ss_socket_without_visible_pid_is_unavailable():
    runner = MagicMock(
        side_effect=[FileNotFoundError("lsof"), _result(0, "LISTEN 0 128 127.0.0.1:8789")]
    )

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "unavailable"
    assert readback.pids == ()
    assert readback.detail == "LSOF_NOT_FOUND,SS_OWNER_UNAVAILABLE"


def test_both_tools_missing_is_unavailable():
    runner = MagicMock(side_effect=[FileNotFoundError("lsof"), FileNotFoundError("ss")])

    readback = read_tcp_listener_pids(8789, runner=runner)

    assert readback.state == "unavailable"
    assert readback.backend == "lsof,ss"
    assert readback.detail == "LSOF_NOT_FOUND,SS_NOT_FOUND"
