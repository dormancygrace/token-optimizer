"""Execute generated marketplace hooks under native cmd.exe, not CRT quoting."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'))
import codex_install as ci


@pytest.mark.skipif(sys.platform != 'win32', reason='requires native cmd.exe')
@pytest.mark.parametrize('upgrade', [False, True])
def test_marketplace_command_runs_with_stdin_and_environment(tmp_path, monkeypatch, upgrade):
    base = tmp_path / "plugin space & (test) ! apostrophe'"
    root = base / '5.9.0'
    for version in ('5.9.0', '5.10.0', 'latest'):
        hooks = base / version / 'hooks'
        hooks.mkdir(parents=True)
        (hooks / 'run.py').write_text(
            "import json, os, sys\n"
            "print(json.dumps(dict(root=os.environ['TOKEN_OPTIMIZER_RUNTIME_ROOT'], "
            "runtime=os.environ['TOKEN_OPTIMIZER_RUNTIME'], extra=os.environ['TO_TEST'], "
            "args=sys.argv[1:], stdin=sys.stdin.read())))\n", encoding='utf-8')
    monkeypatch.setattr(ci, '_repo_root', lambda: root)
    argument = 'space & pipe| quote" percent% bang!'
    command = ci._hook_command('hooks/test.py', argument, extra_env={'TO_TEST': argument})
    assert ci._is_token_optimizer_group({'command': command})
    if upgrade:
        (root / 'hooks/run.py').unlink()
        (root / 'hooks').rmdir()
        root.rmdir()
    # A list would apply CRT escaping (backslash-double-quote), which cmd.exe
    # does not understand. Pass the raw /C command line used by a shell.
    result = subprocess.run(
        f'cmd.exe /d /s /c "{command}"', input='{"test": true}',
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert Path(data['root']) == base / '5.10.0'
    assert data['runtime'] == 'codex'
    assert data['extra'] == argument
    assert data['args'] == ['hooks/test.py', argument]
    assert data['stdin'] == '{"test": true}'


def test_legacy_windows_groups_are_replaced(monkeypatch):
    old = {'hooks': [{'command': 'set "TOKEN_OPTIMIZER_RUNTIME_ROOT=C:\\old" && python runner'}]}
    own = {'hooks': [{'command': 'echo my hook'}]}
    monkeypatch.setattr(ci, '_managed_hooks', lambda **kwargs: {'Stop': [own]})
    merged = ci._merge_hooks({'hooks': {'SessionStart': [old, own]}})
    assert merged['hooks']['SessionStart'] == [own]
    assert ci._remove_hooks({'hooks': {'SessionStart': [old, own]}}) == {
        'hooks': {'SessionStart': [own]}}


@pytest.mark.skipif(sys.platform != 'win32', reason='requires native cmd.exe')
def test_quiet_command_executes_without_output(tmp_path, monkeypatch):
    root = tmp_path / '5.0.0'
    hooks = root / 'hooks'
    hooks.mkdir(parents=True)
    marker = root / 'ran'
    (hooks / 'run.py').write_text(
        f"from pathlib import Path; Path({str(marker)!r}).touch(); print('noise')",
        encoding='utf-8')
    monkeypatch.setattr(ci, '_repo_root', lambda: root)
    command = ci._hook_command('hooks/stop_runner.py', redirect_quiet=True)
    result = subprocess.run(f'cmd.exe /d /s /c "{command}"',
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert marker.exists()
    assert result.stdout == result.stderr == ''
