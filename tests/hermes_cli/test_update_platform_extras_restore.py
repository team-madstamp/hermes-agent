"""Update must restore messaging-platform extras a core reinstall strips (2026-09-14 outage).

``hermes setup`` installs telegram/slack/etc. as pyproject extras; the update flow restored
lazy backends, ``hermes tools`` deps and memory-provider bridges — but not these, so a
venv-affecting update left the gateway with dead adapters and no auto-heal when
``security.allow_lazy_installs=false`` (which only blocks slack's lazy path; telegram had none).
"""

from __future__ import annotations

import hermes_cli.main as main_mod
import hermes_cli.update_cmd_deps as deps


def _fake_importable(table: dict[str, bool]):
    return lambda python, module, env: table.get(module, False)


def test_capture_snapshots_only_importable_extras(monkeypatch):
    monkeypatch.setattr(
        deps, "_module_importable_in",
        _fake_importable({"telegram": True, "slack_bolt": False, "mautrix": True, "defusedxml": False}))
    assert deps._capture_installed_platform_extras() == ["messaging", "matrix"]


def test_capture_indeterminate_probe_reads_as_absent(monkeypatch):
    # A failed probe must under-capture (skip restore), never invent an install.
    monkeypatch.setattr(deps, "_module_importable_in", lambda python, module, env: False)
    assert deps._capture_installed_platform_extras() == []


def test_restore_reinstalls_only_missing_extras(monkeypatch, capsys):
    monkeypatch.setattr(deps, "_module_importable_in", _fake_importable({"telegram": True, "slack_bolt": False}))
    monkeypatch.setattr(
        deps, "_platform_extras_requirements",
        lambda extra: {
            "messaging": ["python-telegram-bot[webhooks]==22.8"],
            "slack": ["slack-bolt==1.30.0", "slack-sdk==3.43.0"],
        }[extra])
    monkeypatch.setattr(main_mod, "_resolve_install_target_python", lambda prefix, env: "/venv/python")
    installs: list[list[str]] = []
    monkeypatch.setattr(main_mod, "_run_package_only_install", lambda cmd, env=None: installs.append(cmd))

    deps._restore_installed_platform_extras(["messaging", "slack"], ["uv", "pip"])

    assert installs == [["uv", "pip", "install", "slack-bolt==1.30.0", "slack-sdk==3.43.0", "--quiet"]]
    assert "1 restored" in capsys.readouterr().out


def test_restore_is_noop_when_everything_survived(monkeypatch):
    monkeypatch.setattr(deps, "_module_importable_in", _fake_importable({"telegram": True, "slack_bolt": True}))
    monkeypatch.setattr(deps, "_platform_extras_requirements", lambda extra: ["pkg==1"])
    monkeypatch.setattr(main_mod, "_resolve_install_target_python", lambda prefix, env: "/venv/python")
    installs: list[list[str]] = []
    monkeypatch.setattr(main_mod, "_run_package_only_install", lambda cmd, env=None: installs.append(cmd))

    deps._restore_installed_platform_extras(["messaging", "slack"], ["uv", "pip"])

    assert installs == []


def test_restore_reports_failure_without_raising(monkeypatch, capsys):
    monkeypatch.setattr(deps, "_module_importable_in", _fake_importable({"slack_bolt": False}))
    monkeypatch.setattr(deps, "_platform_extras_requirements", lambda extra: ["slack-bolt==1.30.0"])
    monkeypatch.setattr(main_mod, "_resolve_install_target_python", lambda prefix, env: "/venv/python")

    def boom(cmd, env=None):
        raise RuntimeError("wheel 500")

    monkeypatch.setattr(main_mod, "_run_package_only_install", boom)

    deps._restore_installed_platform_extras(["slack"], ["uv", "pip"])  # must not raise
    assert "failed to restore" in capsys.readouterr().out


def test_restore_skips_extra_missing_from_manifest(monkeypatch):
    monkeypatch.setattr(deps, "_module_importable_in", _fake_importable({"telegram": False}))
    monkeypatch.setattr(deps, "_platform_extras_requirements", lambda extra: [])
    monkeypatch.setattr(main_mod, "_resolve_install_target_python", lambda prefix, env: "/venv/python")
    installs: list[list[str]] = []
    monkeypatch.setattr(main_mod, "_run_package_only_install", lambda cmd, env=None: installs.append(cmd))

    deps._restore_installed_platform_extras(["messaging"], ["uv", "pip"])

    assert installs == []


def test_requirements_come_from_real_pyproject():
    reqs = deps._platform_extras_requirements("messaging")
    assert any(r.startswith("python-telegram-bot") for r in reqs)
    assert any(r.startswith("slack-bolt") for r in reqs)
    assert deps._platform_extras_requirements("no-such-extra") == []


def test_restore_empty_extras_is_noop():
    deps._restore_installed_platform_extras([], ["uv", "pip"])
    deps._restore_installed_platform_extras(None, ["uv", "pip"])  # type: ignore[arg-type]
