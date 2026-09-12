#!/usr/bin/env python3
"""FIX-SPEC-DAEMON req 1: a routine plugin update must NOT remove the active
daemon's manifest entry (which orphans the live daemon and lets a later sweep
tear down its plist).

The bug: ``reconcile_uninstall`` classified an entry as stale purely on
``installPath`` existence. During a plugin update the marketplace cache path
moves, so the active identity's ``installPath`` is momentarily gone and the
live daemon's manifest entry was dropped. The fix: a live dashboard daemon on
the loopback port disproves "stale", so the active entry is classified
active/kept and never removed by reconcile. A genuinely stale identity
(installPath gone AND daemon dead) is still removed. ``setup-daemon --uninstall``
still fully removes the daemon + plist (non-negotiable).

Run: python3 -m pytest tests/test_daemon_reconcile_protects_live.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install_reconcile as ir  # noqa: E402


def _write_installed(plugins_dir: Path, plugins: dict) -> Path:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    path = plugins_dir / "installed_plugins.json"
    path.write_text(json.dumps({"version": 2, "plugins": plugins}, indent=2), encoding="utf-8")
    return path


def _write_marketplaces(plugins_dir: Path, entries: dict) -> Path:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    path = plugins_dir / "known_marketplaces.json"
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path


def _our_key() -> str:
    return "token-optimizer@alexgreensh-token-optimizer"


def _setup_stale_identity(home: Path):
    """A token-optimizer identity whose installPath is GONE (the update-moved
    case) plus a gone marketplace location, and a foreign survivor."""
    plugins_dir = home / "plugins"
    cache = plugins_dir / "cache"
    our_install = cache / "alexgreensh-token-optimizer" / "token-optimizer" / "5.11.67"  # gone
    foreign_install = cache / "claude-plugins-official" / "ralph-loop" / "1.0.0"
    foreign_install.mkdir(parents=True)
    our_mkt_loc = plugins_dir / "marketplaces" / "alexgreensh-token-optimizer"  # gone
    foreign_mkt_loc = plugins_dir / "marketplaces" / "claude-plugins-official"
    foreign_mkt_loc.mkdir(parents=True)
    inst = _write_installed(plugins_dir, {
        _our_key(): [{"installPath": str(our_install), "version": "5.11.67"}],
        "ralph-loop@claude-plugins-official": [
            {"installPath": str(foreign_install), "version": "1.0.0"}
        ],
    })
    mkt = _write_marketplaces(plugins_dir, {
        "alexgreensh-token-optimizer": {"installLocation": str(our_mkt_loc)},
        "claude-plugins-official": {"installLocation": str(foreign_mkt_loc)},
    })
    return inst, mkt


def test_live_daemon_installpath_gone_is_kept(tmp_path, monkeypatch):
    """HEALTHY active daemon + installPath gone (marketplace path moved during
    update) -> the active entry is classified active/kept and NOT removed."""
    monkeypatch.setattr(ir, "_dashboard_daemon_alive", lambda: True)  # healthy
    home = tmp_path / "home"
    inst, mkt = _setup_stale_identity(home)
    before_inst = inst.read_text(encoding="utf-8")

    result = ir.reconcile_uninstall(home, dry_run=False, remove=True)

    our_reports = [r for r in result["reported"] if "token-optimizer@" in r["key"]]
    assert our_reports, "expected our plugin entry to be reported"
    assert all(not r["stale"] for r in our_reports), "live daemon must be active/kept"
    assert all(r.get("daemon_alive") is True for r in our_reports)
    # Nothing of ours removed; the live daemon's manifest entry survives.
    assert not any(_our_key() in r for r in result["removed"])
    inst_data = json.loads(inst.read_text(encoding="utf-8"))
    assert _our_key() in inst_data["plugins"], "active daemon manifest entry must survive"
    assert inst.read_text(encoding="utf-8") == before_inst


def test_dead_daemon_installpath_gone_is_stale_and_removed(tmp_path, monkeypatch):
    """A genuinely stale identity (installPath gone AND daemon dead) is still
    removed -- the protection only covers the LIVE case."""
    monkeypatch.setattr(ir, "_dashboard_daemon_alive", lambda: False)  # dead
    home = tmp_path / "home"
    inst, mkt = _setup_stale_identity(home)

    result = ir.reconcile_uninstall(home, dry_run=False, remove=True)

    our_reports = [r for r in result["reported"] if "token-optimizer@" in r["key"]]
    assert all(r["stale"] for r in our_reports), "dead daemon + gone path -> stale"
    assert any(_our_key() in r for r in result["removed"]), "stale entry must be removed"
    inst_data = json.loads(inst.read_text(encoding="utf-8"))
    assert _our_key() not in inst_data["plugins"], "stale entry must be gone"
    # Foreign survivor untouched.
    assert "ralph-loop@claude-plugins-official" in inst_data["plugins"]


def test_reconcile_protection_fails_open(tmp_path, monkeypatch):
    """Fail-open: an error in the live-daemon protection path never raises; it
    degrades to the installPath rule (stale -> removed)."""
    def _boom():
        raise RuntimeError("probe blew up")
    monkeypatch.setattr(ir, "_dashboard_daemon_alive", _boom)
    home = tmp_path / "home"
    inst, mkt = _setup_stale_identity(home)

    # Must not raise.
    result = ir.reconcile_uninstall(home, dry_run=False, remove=True)
    assert result is not None
    # Degrades to stale (probe error -> not alive) -> removed.
    assert any(_our_key() in r for r in result["removed"])


def test_report_only_with_live_daemon_tags_active_kept(tmp_path, monkeypatch):
    """Report-only mode (remove=False) with a live daemon tags our gone-path
    entry active/kept and modifies nothing."""
    monkeypatch.setattr(ir, "_dashboard_daemon_alive", lambda: True)
    home = tmp_path / "home"
    inst, mkt = _setup_stale_identity(home)
    before_inst = inst.read_text(encoding="utf-8")
    before_mkt = mkt.read_text(encoding="utf-8")

    result = ir.reconcile_uninstall(home, dry_run=False, remove=False)
    our_reports = [r for r in result["reported"] if "token-optimizer@" in r["key"]]
    assert all(not r["stale"] for r in our_reports)
    assert result["removed"] == []
    assert inst.read_text(encoding="utf-8") == before_inst
    assert mkt.read_text(encoding="utf-8") == before_mkt


@pytest.mark.skipif(sys.platform == "win32", reason="launchd plist / os.getuid is POSIX/macOS-only; Windows uninstall uses Task Scheduler")
def test_setup_daemon_uninstall_still_removes_plist(tmp_path, monkeypatch):
    """Non-negotiable: an explicit ``setup_daemon --uninstall`` still fully
    removes the daemon plist + per-identity files. The req-1 protection lives
    in the reconcile classification only; the sweep path is untouched."""
    import importlib
    import tempfile
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    plist = launch_agents / "com.token-optimizer.dashboard.plist"
    plist.write_text("<plist/>", encoding="utf-8")

    snap = tempfile.mkdtemp(prefix="to-uninstall-protect-")
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", snap)
    if "measure" in sys.modules:
        del sys.modules["measure"]
    mod = importlib.import_module("measure")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "PLIST_PATH", plist)
    monkeypatch.setattr(mod, "LAUNCH_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(mod, "_normalized_platform", lambda: "Darwin")
    # Make launchctl a no-op so the uninstall doesn't depend on the host.
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Result(0))
    # No port reclaim / process killing in the test host.
    monkeypatch.setattr(mod, "_reclaim_daemon_ports", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    # All launchd paths/process calls are isolated above; bypass only to test
    # the low-level removal semantics themselves.
    monkeypatch.setattr(mod, "_daemon_snapshot_sandboxed", lambda: False)
    # Plant a per-identity daemon file so the sweep has something to remove.
    (Path(snap) / "dashboard-server.py").write_text("# daemon", encoding="utf-8")
    (Path(snap) / "daemon-token").write_text("tok", encoding="utf-8")

    mod._uninstall_launchd_daemon(this_install_only=True)

    assert not plist.exists(), "explicit uninstall must still remove the plist"
    assert not (Path(snap) / "dashboard-server.py").exists()
    assert not (Path(snap) / "daemon-token").exists()
    if "measure" in sys.modules:
        del sys.modules["measure"]


class _Result:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


if __name__ == "__main__":
    import tempfile

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
                t(Path(td), __import__("pytest").MonkeyPatch())
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
