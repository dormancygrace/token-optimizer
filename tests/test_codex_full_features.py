import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'
sys.path.insert(0, str(SCRIPTS))
import codex_session as session
import codex_log_index as index
import codex_command_compress as compression


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    monkeypatch.setattr(index, 'resolve_snapshot_dir', lambda: tmp_path / 'index')
    monkeypatch.setattr(session, 'MAX_PARSE_FILE_BYTES', 100)
    return tmp_path / 'session.jsonl'


def log_records(total=100):
    usage = {'input_tokens': total, 'cached_input_tokens': total // 2, 'output_tokens': total // 5}
    return [{'type': 'session_meta', 'payload': {'id': '11111111-1111-1111-1111-111111111111'}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-6-astra'}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': usage}}}]


def write(path, records):
    path.write_text('\n'.join(map(json.dumps, records)) + '\n', encoding='utf-8')


def test_full_index_counts_all_usage_and_only_appended_changes(indexed):
    write(indexed, log_records())
    first = session.parse_session_jsonl(indexed)
    assert first['total_input_tokens'] == 100 and not first['incomplete']
    assert first['scan_mode'] == 'indexed_full'
    assert not index.pending(indexed)
    with indexed.open('a') as handle:
        handle.write(json.dumps(log_records(200)[-1]) + '\n')
    assert index.pending(indexed)
    result = session.parse_session_jsonl(indexed)
    assert result['total_input_tokens'] == 200
    assert result['total_output_tokens'] == 40
    assert result == session.parse_session_jsonl(indexed)


def test_index_drains_unchanged_file_in_bounded_passes(indexed, monkeypatch):
    monkeypatch.setattr(index, 'PASS_BYTES', 200)
    write(indexed, log_records() + [log_records(200)[-1], log_records(300)[-1]])
    for _ in range(10):
        parsed = session.parse_session_jsonl(indexed)
        if not index.pending(indexed):
            break
    assert parsed['total_input_tokens'] == 300
    assert not parsed['incomplete']


def test_index_revisits_schema_changes_and_same_size_rewrites(indexed, monkeypatch):
    write(indexed, log_records(200))
    session.parse_session_jsonl(indexed)
    assert not index.pending(indexed)
    monkeypatch.setattr(index, 'SCHEMA_VERSION', index.SCHEMA_VERSION + 1)
    assert index.pending(indexed)
    session.parse_session_jsonl(indexed)
    assert not index.pending(indexed)
    old = indexed.stat()
    write(indexed, log_records(300))
    assert indexed.stat().st_size == old.st_size
    os.utime(indexed, ns=(old.st_atime_ns, old.st_mtime_ns + 1000000))
    assert index.pending(indexed)
    assert session.parse_session_jsonl(indexed)['total_input_tokens'] == 300


def test_index_recovers_partial_record_and_truncation(indexed):
    write(indexed, log_records())
    assert session.parse_session_jsonl(indexed)['total_input_tokens'] == 100
    with indexed.open('a') as handle:
        handle.write('{"type":')
    assert session.parse_session_jsonl(indexed)['incomplete']
    write(indexed, log_records(50))
    assert session.parse_session_jsonl(indexed)['total_input_tokens'] == 50


def test_index_preserves_estimated_character_counts_without_copying_output(indexed):
    raw = 'PRIVATE_TEST_OUTPUT_' * 1000
    write(indexed, log_records()[:2] + [{'type': 'response_item', 'payload': {
        'type': 'function_call_output', 'output': raw}}, {'type': 'event_msg',
        'payload': {'type': 'user_message', 'message': 'inspect'}}])
    parsed = session.parse_session_jsonl(indexed)
    assert parsed['total_input_tokens'] == (len(raw) + len('inspect')) // 4
    conn = index._connect()
    text = ''.join(row[0] for row in conn.execute('SELECT data FROM records'))
    conn.close()
    assert 'PRIVATE_TEST_OUTPUT_' not in text


@pytest.mark.parametrize('command', ['git branch new-branch', 'find . -delete', 'rg --pre=evil x',
    'git log --output=written', 'python -m arbitrary', 'Get-Content file; Remove-Item file',
    'Get-Content $(evil)', 'Get-ChildItem | Remove-Item'])
def test_rewrite_does_not_allow_write_or_expression_commands(command):
    assert not compression.eligible(command)


def test_native_powershell_compression_and_failure_exit(tmp_path):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell required')
    fixture = tmp_path / 'log with spaces.txt'
    fixture.write_text(('progress: processing routine item\n' * 500) + 'Completed\n', encoding='utf-8')
    env = {**os.environ, 'TOKEN_OPTIMIZER_RUNTIME': 'codex', 'TOKEN_OPTIMIZER_SNAPSHOT_DIR': str(tmp_path / 'data')}
    for command, success in [(f"Get-Content -LiteralPath '{fixture}'", True),
                             (f"Get-Content -LiteralPath '{tmp_path / 'missing'}'", False)]:
        payload = {'tool_name': 'Bash', 'tool_input': {'command': command, 'shell': shell},
                   'cwd': str(tmp_path), 'model': 'gpt-6-astra',
                   'session_id': '11111111-1111-1111-1111-111111111111'}
        rewritten = compression.rewrite(payload)['hookSpecificOutput']['updatedInput']['command']
        result = subprocess.run([shell, '-NoProfile', '-Command', rewritten],
                                capture_output=True, env=env, timeout=30)
        assert (result.returncode == 0) == success
        if success:
            assert b'Completed' in result.stdout
            assert b'full command output saved' in result.stdout
            assert len(result.stdout) < fixture.stat().st_size / 2
            archives = list((tmp_path / 'data/codex-command-output').glob('*.txt'))
            assert len(archives) == 1 and b'Completed' in archives[0].read_bytes()
        else:
            assert result.stderr and b'full command output saved' not in result.stdout


def test_codex_manifest_does_not_load_claude_hook_bundle():
    manifest = json.loads((SCRIPTS.parents[2] / '.codex-plugin/plugin.json').read_text())
    assert manifest['hooks'] == './hooks/codex-hooks.json'
    assert json.loads((SCRIPTS.parents[2] / manifest['hooks']).read_text())['hooks'] == {}


def test_install_preserves_equivalent_version_resolver_but_not_changed_logic(monkeypatch, tmp_path):
    import codex_install as installer
    monkeypatch.setattr(installer.sys, 'platform', 'win32')
    monkeypatch.setattr(installer, '_repo_root', lambda: tmp_path / '5.13.8')
    old = installer._managed_hooks(enable_prompt_hooks=True)
    monkeypatch.setattr(installer, '_repo_root', lambda: tmp_path / '5.13.11')
    merged = installer._merge_hooks({'hooks': old}, enable_prompt_hooks=True)['hooks']
    assert merged == old
    old['Stop'][0]['hooks'][0]['command'] += ' changed'
    merged = installer._merge_hooks({'hooks': old}, enable_prompt_hooks=True)['hooks']
    assert not merged['Stop'][0]['hooks'][0]['command'].endswith(' changed')


def test_compaction_events_use_only_matching_task(monkeypatch, tmp_path):
    import codex_hook_bridge as bridge
    calls = []
    monkeypatch.setattr(bridge, 'read_stdin_hook_input', lambda: {'session_id': 'task', 'transcript_path': 'log', 'cwd': str(tmp_path)})
    monkeypatch.setattr(bridge.codex_session, 'resolve_session', lambda path, sid: tmp_path / 'log')
    monkeypatch.setattr(bridge.measure, 'compact_capture', lambda **kw: calls.append(('capture', kw)))
    monkeypatch.setattr(bridge.measure, 'quality_cache', lambda **kw: calls.append(('quality', kw)))
    bridge.handle_compaction('PreCompact')
    bridge.handle_compaction('PostCompact')
    bridge.handle_compaction('Interrupt')
    assert [kind for kind, kw in calls] == ['capture', 'quality', 'capture']
    assert all(kw['session_id'] == 'task' for kind, kw in calls)
    monkeypatch.setattr(bridge.codex_session, 'resolve_session', lambda path, sid: None)
    bridge.handle_compaction('PreCompact')
    assert len(calls) == 3


def test_codex_current_models_use_pressure_not_proxy_accuracy(monkeypatch):
    import measure
    monkeypatch.setattr(measure, 'detect_runtime', lambda: 'codex')
    for model in ('gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
                  'gpt-daybreak-blue-latest', 'gpt-5.5', 'gpt-5.3-codex-spark', 'future-model'):
        name, curve, basis = measure._quality_curve_for_model(model)
        assert 'heuristic' in name and 'proxy' not in name
        assert basis == 'fill_fraction'
