"""Serve-kind runtime inventory + stop/relaunch rung (#63206, campaign #91277).

A network-bound `hermes serve --host <ip>` powering a remote Desktop used to
be invisible to the update pipeline: not in the inventory, a dead-end at the
venv-holder guard, and never relaunched after `hermes update` killed it. The
fix threads the spawn ledger's structured launch identity (host/port/profile,
registered at serve startup) through inventory → guard rung → relaunch.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch  # noqa: F401 - kept for parity with siblings

import hermes_cli.update_cmd as update_cmd
import hermes_cli.update_inventory as update_inventory
from hermes_cli import main as cli_main
import hermes_cli.main_install_repair as main_install_repair
import hermes_cli.main_dashboard as main_dashboard


def _ledger_entry(**over):
    entry = {
        "pid": 4321,
        "create_time": 111.0,
        "purpose": "serve",
        "install": "inst",
        "spawner_pid": None,
        "spawner_create": None,
        "registered_at": 222.0,
        "argv": "hermes serve --host 100.94.65.93 --port 9119",
        "host": "100.94.65.93",
        "port": 9119,
        "profile": "",
    }
    entry.update(over)
    return entry


# ---------------------------------------------------------------------------
# process_identity: structured detail round-trip
# ---------------------------------------------------------------------------


def test_register_self_records_structured_detail(tmp_path, monkeypatch):
    from hermes_cli import process_identity as pi

    monkeypatch.setattr(pi, "_ledger_path", lambda: tmp_path / "ledger.json")
    monkeypatch.setattr(pi, "install_id", lambda *a, **k: "inst")
    assert pi.register_self(
        "serve", detail={"host": "100.94.65.93", "port": 9119, "profile": "work"}
    )
    entries = [
        e
        for e in pi._read_ledger(tmp_path / "ledger.json")
        if e["purpose"] == "serve"
    ]
    assert entries, "serve entry must be written"
    e = entries[-1]
    assert e["host"] == "100.94.65.93"
    assert e["port"] == 9119
    assert e["profile"] == "work"


def test_register_self_without_detail_stays_backward_compatible(
    tmp_path, monkeypatch
):
    from hermes_cli import process_identity as pi

    monkeypatch.setattr(pi, "_ledger_path", lambda: tmp_path / "ledger.json")
    monkeypatch.setattr(pi, "install_id", lambda *a, **k: "inst")
    assert pi.register_self("gateway")
    e = pi._read_ledger(tmp_path / "ledger.json")[-1]
    assert e["host"] == "" and e["port"] is None and e["profile"] == ""


# ---------------------------------------------------------------------------
# update_inventory: serve collector
# ---------------------------------------------------------------------------


def test_inventory_includes_manual_serve_from_ledger(monkeypatch):
    entry = _ledger_entry()
    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [entry],
        spawner_is_dead=lambda e: None,  # no spawner recorded → manual
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    plan = update_inventory.collect_runtime_inventory()
    serves = [r for r in plan.runtimes if r.kind == "serve"]
    assert serves, "manual serve must appear in the inventory"
    row = serves[0]
    assert row.pid == 4321
    assert row.supervisor == "manual-serve"
    assert row.restart_via == "respawn-argv"
    assert row.detail["host"] == "100.94.65.93"
    assert row.detail["port"] == 9119


def test_inventory_classifies_desktop_owned_serve(monkeypatch):
    entry = _ledger_entry(spawner_pid=999, spawner_create=1.0)
    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [entry],
        spawner_is_dead=lambda e: False,  # Electron parent alive
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    plan = update_inventory.collect_runtime_inventory()
    serves = [r for r in plan.runtimes if r.kind == "serve"]
    assert serves and serves[0].supervisor == "desktop"
    assert serves[0].restart_via == "desktop"


def test_inventory_recovers_legacy_blank_profile_from_live_process(monkeypatch):
    entry = _ledger_entry(profile="", pid=54069, create_time=123.0)
    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [entry],
        spawner_is_dead=lambda e: False,
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    monkeypatch.setattr(
        update_inventory,
        "_live_process_argv",
        lambda pid, create_time=None: [
            "/path/python", "main.py", "--profile", "team-madstamp",
            "serve", "--host", "127.0.0.1", "--port", "0",
        ],
    )

    plan = update_inventory.collect_runtime_inventory()
    serves = [r for r in plan.runtimes if r.kind == "serve"]
    assert serves and serves[0].profile == "team-madstamp"
    assert serves[0].detail["profile_source"] == "live_process_argv"


def test_inventory_prefers_live_profile_over_stale_ledger_profile(monkeypatch):
    entry = _ledger_entry(profile="team-madstamp", purpose="dashboard", pid=79754, create_time=123.0)
    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [entry],
        spawner_is_dead=lambda e: None,
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    monkeypatch.setattr(
        update_inventory,
        "_live_process_argv",
        lambda pid, create_time=None: [
            "/path/python", "main.py", "-p", "default", "dashboard",
            "--open-profile", "team-madstamp", "--port", "9119",
        ],
    )

    plan = update_inventory.collect_runtime_inventory()
    dashboards = [r for r in plan.runtimes if r.kind == "dashboard"]
    assert dashboards and dashboards[0].profile == "default"
    assert dashboards[0].detail["profile_source"] == "live_process_argv_conflict"


def test_describe_restart_mechanism_respawn_argv():
    text = update_inventory.describe_restart_mechanism("respawn-argv", "default")
    assert "relaunch" in text


def test_unknown_profile_does_not_propose_restart_command():
    assert "unresolved" in update_inventory.describe_restart_mechanism(
        "manual", update_inventory._UNKNOWN_PROFILE
    )


# ---------------------------------------------------------------------------
# update_cmd: guard rung helpers
# ---------------------------------------------------------------------------


def test_ledger_manual_serve_holders_filters_correctly(monkeypatch):
    manual = _ledger_entry(pid=100, profile="default")
    desktop_owned = _ledger_entry(pid=200, profile="default", spawner_pid=999, spawner_create=1.0)
    gateway = _ledger_entry(pid=300, profile="default", purpose="gateway")
    not_a_holder = _ledger_entry(pid=400, profile="default")

    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [manual, desktop_owned, gateway, not_a_holder],
        spawner_is_dead=lambda e: False if e["pid"] == 200 else None,
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    holders = [(100, "python.exe", "..."), (200, "python.exe", "..."), (300, "python.exe", "...")]

    result = update_cmd._ledger_manual_serve_holders(holders)
    pids = [e["pid"] for e in result]
    assert pids == [100], (
        "only the manual serve holder qualifies: desktop-owned keeps the "
        "refusal, gateways belong to the pause machinery, non-holders skipped"
    )


def test_serve_relaunch_commands_built_from_structured_identity(monkeypatch):
    monkeypatch.setattr(cli_main, "_venv_scripts_dir", lambda: None)
    monkeypatch.setattr(main_install_repair, "_venv_scripts_dir", lambda: None)
    entries = [
        _ledger_entry(profile="default"),                # default profile
        _ledger_entry(pid=5000, profile="work", port=9200, host=""),
        _ledger_entry(pid=6000, port=None),               # no port → skipped
        _ledger_entry(pid=7000, profile="default", purpose="dashboard", host="0.0.0.0", port=9300),
    ]
    cmds = update_cmd._serve_relaunch_commands(entries)
    assert ["hermes", "serve", "--host", "100.94.65.93", "--port", "9119"] in cmds
    assert ["hermes", "--profile", "work", "serve", "--port", "9200"] in cmds
    assert ["hermes", "dashboard", "--host", "0.0.0.0", "--port", "9300"] in cmds
    assert len(cmds) == 3  # the port-less entry is skipped


def test_manual_serve_holder_captures_resolved_profile_before_stop(monkeypatch):
    entry = _ledger_entry(profile="", pid=54069, create_time=123.0)
    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [entry],
        spawner_is_dead=lambda e: None,
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    monkeypatch.setattr(
        update_inventory,
        "_live_process_argv",
        lambda pid, create_time=None: [
            "/path/python", "main.py", "--profile", "team-madstamp",
            "serve", "--host", "127.0.0.1", "--port", "9119",
        ],
    )

    entries = update_cmd._ledger_manual_serve_holders(
        [(54069, "python", "hermes --profile team-madstamp serve")]
    )
    assert entries and entries[0]["profile"] == "team-madstamp"
    assert entries[0]["profile_source"] == "live_process_argv"


def test_relaunch_stopped_serves_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_main, "_respawn_dashboard_processes", lambda cmds: calls.append(cmds) or []
    )
    monkeypatch.setattr(
        main_dashboard, "_respawn_dashboard_processes", lambda cmds: calls.append(cmds) or []
    )
    monkeypatch.setattr(cli_main, "_venv_scripts_dir", lambda: None)
    monkeypatch.setattr(main_install_repair, "_venv_scripts_dir", lambda: None)
    token = {"pending": True, "entries": [_ledger_entry(profile="default")]}

    update_cmd._relaunch_stopped_serves(token)
    update_cmd._relaunch_stopped_serves(token)  # atexit double-fire

    assert len(calls) == 1, "relaunch must fire exactly once"
    assert token["pending"] is False


def test_relaunch_stopped_serves_untriggered_token_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_main, "_respawn_dashboard_processes", lambda cmds: calls.append(cmds) or []
    )
    monkeypatch.setattr(
        main_dashboard, "_respawn_dashboard_processes", lambda cmds: calls.append(cmds) or []
    )
    update_cmd._relaunch_stopped_serves({"pending": False, "entries": [_ledger_entry()]})
    assert calls == []


# ---------------------------------------------------------------------------
# dashboard_procs: ledger augmentation of the scan (#81564 half)
# ---------------------------------------------------------------------------


def test_scan_dashboard_processes_includes_ledger_only_serves(monkeypatch):
    """A profiled serve (`hermes --profile p serve ...`) matches no scan
    pattern; the ledger row must still surface it."""
    import hermes_cli.dashboard_procs as dp

    profiled = _ledger_entry(
        pid=8123,
        argv="hermes --profile work serve --host 100.94.65.93 --port 9119",
        profile="work",
    )
    fake_pi = SimpleNamespace(ledger_entries=lambda **k: [profiled])
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)

    # Force the ps/wmic scan itself to find nothing.
    fake_run = SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(
        dp.subprocess, "run", lambda *a, **k: fake_run
    )
    result = dp._scan_dashboard_processes()
    assert (8123, profiled["argv"]) in result


def test_scan_dashboard_processes_ledger_respects_exclusions(monkeypatch):
    import hermes_cli.dashboard_procs as dp

    entry = _ledger_entry(pid=8124)
    fake_pi = SimpleNamespace(ledger_entries=lambda **k: [entry])
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    fake_run = SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(dp.subprocess, "run", lambda *a, **k: fake_run)

    assert dp._scan_dashboard_processes(exclude_pids={8124}) == []


def test_inventory_records_the_serve_process_incarnation(monkeypatch):
    """The plan carries ``(pid, create_time)``, not just the PID (#92145 review).

    The post-abort survivor probe compares a planned serve against the live
    spawn ledger. With only the number to compare, a NEW serve that reused the
    old PID reads as the pre-update process that never restarted, and recovery
    stays incomplete forever.
    """
    entry = _ledger_entry(create_time=1712345678.5)
    fake_pi = SimpleNamespace(
        ledger_entries=lambda **k: [entry],
        spawner_is_dead=lambda e: None,
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.process_identity", fake_pi)
    plan = update_inventory.collect_runtime_inventory()
    serves = [r for r in plan.runtimes if r.kind == "serve"]
    assert serves and serves[0].detail["create_time"] == 1712345678.5


def test_process_scan_fallback_includes_profiled_desktop_backend(monkeypatch):
    pid = 8125
    command = (
        "/Users/yu/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main "
        "--profile team-madstamp serve --host 127.0.0.1 --port 0"
    )
    monkeypatch.setattr(update_inventory, "_process_scan_rows", lambda: [(pid, command)])
    monkeypatch.setattr(update_inventory, "_process_argv", lambda command, pid: (command.split(), "process_table"))
    monkeypatch.setattr(update_inventory, "_process_environment", lambda pid: {
        "HERMES_HOME": "/Users/yu/.hermes",
        "HERMES_DESKTOP": "1",
        "HERMES_PARENT_PID": "44668",
    })
    monkeypatch.setattr(update_inventory, "_process_home", lambda pid, env: env["HERMES_HOME"])
    monkeypatch.setattr(update_inventory, "_process_install_match", lambda command, home: "command_path")
    monkeypatch.setattr(update_inventory, "_process_create_time", lambda pid: 123.5)
    monkeypatch.setattr(
        update_inventory, "_scan_supervisor",
        lambda pid, env: ("desktop", "desktop_marker_parent_live", 44668),
    )

    plan = update_inventory.UpdatePlan()
    update_inventory._collect_process_scan_runtimes(plan, set())

    assert len(plan.runtimes) == 1
    row = plan.runtimes[0]
    assert row.kind == "serve"
    assert row.profile == "team-madstamp"
    assert row.supervisor == "desktop"
    assert row.detail["identity_source"] == "process_scan_fallback"
    assert row.detail["port"] == 0


def test_process_scan_fallback_does_not_adopt_foreign_install(monkeypatch):
    pid = 8126
    command = "/tmp/other/hermes-agent/venv/bin/python -m hermes_cli.main dashboard --port 9119"
    monkeypatch.setattr(update_inventory, "_process_scan_rows", lambda: [(pid, command)])
    monkeypatch.setattr(update_inventory, "_process_argv", lambda command, pid: (command.split(), "process_table"))
    monkeypatch.setattr(update_inventory, "_process_environment", lambda pid: {})
    monkeypatch.setattr(update_inventory, "_process_home", lambda pid, env: None)

    plan = update_inventory.UpdatePlan()
    update_inventory._collect_process_scan_runtimes(plan, set())

    assert plan.runtimes == []


def test_process_install_match_requires_source_path_boundary():
    source = "/Users/yu/.hermes/hermes-agent"
    assert update_inventory._process_install_match(
        f"{source}/venv/bin/python -m hermes_cli.main serve", None
    ) == "command_path"
    assert update_inventory._process_install_match(
        f"{source}-copy/venv/bin/python -m hermes_cli.main serve", None
    ) is None


def test_process_scan_fallback_uses_manual_review_restart_mechanism(monkeypatch):
    pid = 8127
    command = "/Users/yu/.hermes/hermes-agent/venv/bin/hermes --profile team-madstamp dashboard --port 9119"
    monkeypatch.setattr(update_inventory, "_process_scan_rows", lambda: [(pid, command)])
    monkeypatch.setattr(update_inventory, "_process_argv", lambda command, pid: (command.split(), "process_table"))
    monkeypatch.setattr(update_inventory, "_process_environment", lambda pid: {
        "HERMES_HOME": "/Users/yu/.hermes",
    })
    monkeypatch.setattr(update_inventory, "_process_home", lambda pid, env: env["HERMES_HOME"])
    monkeypatch.setattr(update_inventory, "_process_install_match", lambda command, home: "hermes_home")
    monkeypatch.setattr(update_inventory, "_process_create_time", lambda pid: 124.5)
    monkeypatch.setattr(update_inventory, "_scan_supervisor", lambda pid, env: (
        "process-scan", "process_table_no_desktop_marker", 1
    ))

    plan = update_inventory.UpdatePlan()
    update_inventory._collect_process_scan_runtimes(plan, set())

    assert plan.runtimes[0].supervisor == "process-scan"
    assert plan.runtimes[0].restart_via == "manual-review"
    assert "manual review" in update_inventory.describe_restart_mechanism(
        plan.runtimes[0].restart_via, plan.runtimes[0].profile
    )
