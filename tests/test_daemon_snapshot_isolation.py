"""An explicit snapshot directory must isolate daemon lifecycle side effects."""

import importlib
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "token-optimizer" / "scripts"


@pytest.fixture()
def measure(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path / "sandbox"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    previous = sys.modules.pop("measure", None)
    mod = importlib.import_module("measure")
    yield mod
    sys.modules.pop("measure", None)
    if previous is not None:
        sys.modules["measure"] = previous


def _unexpected(name):
    def fail(*args, **kwargs):
        pytest.fail(f"sandbox daemon guard reached {name}")
    return fail


@pytest.mark.parametrize("force", [False, True])
def test_automatic_ensure_never_reaches_os_daemon_state(measure, monkeypatch, force):
    monkeypatch.setattr(measure, "_daemon_service_installed", _unexpected("service probe"))
    monkeypatch.setattr(measure, "_verify_daemon_port", _unexpected("port probe"))
    monkeypatch.setattr(measure, "_restart_dashboard_daemon", _unexpected("restart"))
    monkeypatch.setattr(measure, "_install_launchd_daemon", _unexpected("launchd install"))
    assert measure._ensure_dashboard_daemon(force=force) == "noop-sandbox"


def test_revive_dispatchers_never_spawn_a_child(measure, monkeypatch):
    monkeypatch.setattr(measure, "spawn_detached", _unexpected("daemon-revive spawn"))
    assert measure._ensure_health_daemon_revive_first() == "noop-sandbox"
    assert measure._daemon_midsession_pulse() == "noop-sandbox"


@pytest.mark.parametrize("uninstall", [False, True])
def test_direct_setup_never_reaches_an_os_scheduler(measure, monkeypatch, uninstall):
    for name in (
        "_install_launchd_daemon", "_install_task_scheduler_daemon",
        "_install_systemd_user_daemon", "_uninstall_launchd_daemon",
        "_uninstall_task_scheduler_daemon", "_uninstall_systemd_user_daemon",
    ):
        monkeypatch.setattr(measure, name, _unexpected(name))
    monkeypatch.setattr(measure, "_persist_dashboard_host", _unexpected("host persistence"))
    monkeypatch.setattr(measure, "_set_daemon_disabled", _unexpected("daemon identity config"))
    assert measure.setup_daemon(uninstall=uninstall) == "noop-sandbox"


def test_direct_restart_is_blocked_before_subprocesses(measure, monkeypatch):
    monkeypatch.setattr(measure, "_daemon_served_version", _unexpected("version probe"))
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", _unexpected("port reclaim"))
    assert measure._restart_dashboard_daemon("Darwin") == "noop-sandbox"
    assert measure._daemon_resurrection_blocked() == "sandbox"


@pytest.mark.parametrize(
    "helper_name",
    [
        "_install_launchd_daemon",
        "_uninstall_launchd_daemon",
        "_install_task_scheduler_daemon",
        "_uninstall_task_scheduler_daemon",
        "_install_systemd_user_daemon",
        "_uninstall_systemd_user_daemon",
    ],
)
def test_low_level_os_helpers_stop_before_scheduler_or_identity_sweep(
        measure, monkeypatch, helper_name):
    monkeypatch.setattr(measure, "_daemon_identity_snapshot_dirs", _unexpected("identity sweep"))
    monkeypatch.setattr(measure, "_scheduler_names_to_sweep", _unexpected("scheduler sweep"))
    monkeypatch.setattr(measure, "_write_uninstall_tombstone", _unexpected("tombstone write"))
    monkeypatch.setattr(measure, "_ensure_dashboard_file", _unexpected("dashboard write"))
    monkeypatch.setattr(measure, "_systemd_user_unit_path", _unexpected("systemd path"))
    monkeypatch.setattr(measure.subprocess, "run", _unexpected("scheduler subprocess"))

    assert getattr(measure, helper_name)() is False
