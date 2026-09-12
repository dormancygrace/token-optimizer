"""Codex telemetry and config regression cases, with isolated runtime data."""
import json
import os
import sys
from pathlib import Path
import pytest
try:
    import tomllib
except ImportError:
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'))
import codex_session as cs
import codex_compact_prompt as cp

SID = '01234567-1234-1234-1234-123456789abc'

def write_session(path, totals=(100, 200)):
    records = [{'type': 'session_meta', 'payload': {'id': SID, 'cwd': '/project'}},
               {'type': 'turn_context', 'payload': {'model': 'gpt-5.4'}}]
    for n in totals:
        usage = dict(input_tokens=n, cached_input_tokens=n // 2,
                     output_tokens=n // 5, reasoning_output_tokens=n // 10)
        records += [{'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'hello'}},
                    {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                        'total_token_usage': usage, 'last_token_usage': usage}}}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    return path

def test_inclusive_token_counts_and_duplicate_usage(tmp_path):
    p = write_session(tmp_path / 'session.jsonl', (100, 100, 200))
    parsed = cs.parse_session_jsonl(p)
    assert parsed['total_input_tokens'] == 200
    assert parsed['total_output_tokens'] == 40
    assert parsed['total_cache_read'] == 100
    assert parsed['cache_hit_rate'] == 0.5
    parts = dict(parsed['model_usage_breakdown']['gpt-5.4'])
    assert len(parts.pop('requests')) == 2
    assert parts == {
        'fresh_input': 100, 'cache_read': 100, 'cache_create': 0, 'output': 40}
    turns = cs.parse_session_turns(p)
    assert turns[-1]['input_tokens'] == 200
    assert turns[-1]['output_tokens'] == 40
    assert cs.parse_jsonl_for_quality(p)['context_tokens'] == 240

def test_latest_session_is_selected_before_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, 'session_roots', lambda: (tmp_path,))
    old = write_session(tmp_path / 'old' / 'a.jsonl')
    new = write_session(tmp_path / 'new' / 'z.jsonl')
    os.utime(old, (1, 1))
    assert cs.find_all_jsonl_files(days=90, max_files=1)[0][0] == new

def test_large_log_samples_recent_usage_without_cumulative_overcount(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, 'MAX_PARSE_FILE_BYTES', 2048)
    monkeypatch.setattr(cs, 'LARGE_FILE_TAIL_BYTES', 1500)
    p = tmp_path / 'large.jsonl'
    meta = {'type': 'session_meta', 'payload': {'id': SID}}
    context = {'type': 'turn_context', 'payload': {'thread_settings': {'model': 'gpt-6-astra'}}}
    usage = {'input_tokens': 100, 'cached_input_tokens': 50, 'output_tokens': 20}
    event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': usage,
        'total_token_usage': {k: v * 1000 for k, v in usage.items()}}}}
    p.write_text(json.dumps(meta) + '\n' + 'x' * 10000 + '\n' +
                 '\n'.join(map(json.dumps, [context, event, event])), encoding='utf-8')
    parsed = cs.parse_session_jsonl(p)
    assert parsed['incomplete'] and parsed['scan_mode'] == 'recent_tail'
    assert parsed['slug'] == SID
    assert parsed['total_input_tokens'] == 100
    assert parsed['total_output_tokens'] == 20
    assert list(parsed['model_usage']) == ['gpt-6-astra']

def test_oversized_record_does_not_hide_following_records(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, 'MAX_JSONL_LINE_CHARS', 128)
    p = tmp_path / 'lines.jsonl'
    p.write_bytes(b'x' * 1000 + b'\n{"payload":{"model":"gpt-6-astra"}}\n')
    assert list(cs._iter_json_records(p)) == [{'payload': {'model': 'gpt-6-astra'}}]

def test_astra_pricing_uses_request_context_not_session_sum(measure):
    m = measure
    assert m._normalize_openai_model_name('gpt-6-astra-2026-09-01') == 'gpt-6-astra'
    small = {'fresh_input': 100000, 'cache_read': 100000, 'output': 1000}
    parts = {k: v * 3 for k, v in small.items()}
    parts['requests'] = [small] * 3
    assert m._cost_from_model_breakdown({'gpt-6-astra': parts}) == pytest.approx(3.45)
    assert m._get_model_cost('gpt-6-astra', 200000, 1000, 100000, 0) == pytest.approx(4.275)

def test_rollout_nudge_identity_matches_only_its_live_task(measure, tmp_path, monkeypatch):
    m = measure
    transcript = write_session(tmp_path / f'rollout-2026-09-06-{SID}.jsonl')
    cache_path = tmp_path / f'quality-cache-{transcript.stem}.json'
    cache_path.write_text('{}')
    monkeypatch.setattr(m, '_quality_cache_path_for', lambda fp=None: cache_path)
    monkeypatch.setattr(m, '_read_quality_cache', lambda cp: {
        'fill_pct': 47, 'score': 73, 'session_efficiency': 60,
        'nudge_count': 0, 'last_nudge_time': 0})
    monkeypatch.setattr(m, '_log_savings_event', lambda *a, **kw: None)
    assert m.run_verbosity_steer(str(transcript), quiet=True, session_id=SID)
    assert not m.run_verbosity_steer(str(transcript), quiet=True,
                                    session_id='11234567-1234-1234-1234-123456789abc')

def test_new_codex_task_never_reads_or_checkpoints_another_task(measure, tmp_path, monkeypatch):
    other = write_session(tmp_path / 'sessions' / f'rollout-2026-09-06-{SID}.jsonl')
    missing = '11234567-1234-1234-1234-123456789abc'
    monkeypatch.setattr(measure, '_find_current_session_jsonl', lambda: other)
    monkeypatch.setattr(measure, 'CHECKPOINT_DIR', tmp_path / 'checkpoints')
    assert cs.resolve_session(str(other), missing) is None
    assert measure.quality_cache(session_jsonl=str(other), session_id=missing) is None
    assert measure.compact_capture(str(other), missing) is None
    assert not measure.run_verbosity_steer(str(other), session_id=missing)
    assert not list((tmp_path / 'checkpoints').glob('*.md'))

def test_live_identity_overrides_latest_database_task(measure, monkeypatch):
    import codex_state
    monkeypatch.setattr(codex_state, '_is_codex', lambda: True)
    monkeypatch.setenv('TOKEN_OPTIMIZER_SESSION_ID', SID)
    monkeypatch.setattr(codex_state, '_find_versioned_db', lambda *a: pytest.fail('must not choose another database task'))
    assert codex_state.current_thread_id() == SID
    assert measure.sanitize_session_id(f'rollout-2026-09-06-{SID}') == SID

def test_client_model_catalog_limits_and_visibility(measure, monkeypatch, tmp_path):
    import codex_models as models
    monkeypatch.setattr(models, 'codex_home', lambda: tmp_path)
    records = [{'slug': 'gpt-6-astra', 'context_window': 272000,
                'effective_context_window_percent': 95, 'visibility': 'list'},
               {'slug': 'gpt-5.3-codex-spark', 'context_window': 128000,
                'effective_context_window_percent': 95, 'visibility': 'list'},
               {'slug': 'codex-auto-review', 'context_window': 272000, 'visibility': 'hide'}]
    path = tmp_path / 'models_cache.json'
    path.write_text(json.dumps({'models': records}))
    assert models.effective_window('gpt-6-astra') == 258400
    assert models.effective_window('gpt-5.3-codex-spark') == 121600
    assert len(models.visible_models()) == 2
    records[0]['context_window'] = 400000
    path.write_text(json.dumps({'models': records}))
    assert models.effective_window('gpt-6-astra') == 380000
    assert models.effective_window('unknown') is None

def test_codex_global_consolidated_hooks_are_recognized(measure, monkeypatch, tmp_path):
    import codex_doctor as doctor
    monkeypatch.setattr(doctor, 'codex_home', lambda: tmp_path)
    hook = {'hooks': [{'type': 'command', 'command': 'python -c encoded token-optimizer/scripts/windows-launcher'}]}
    (tmp_path / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [hook], 'UserPromptSubmit': [hook]}}))
    checks = {c['name']: c['status'] for c in doctor._project_feature_checks(tmp_path / 'project')}
    assert checks['Feature: Session continuity and dashboard refresh'] == 'OK'
    assert checks['Optional feature: Prompt quality nudges'] == 'OK'
    monkeypatch.setattr(measure, 'codex_home', lambda: tmp_path)
    assert measure._collect_codex_hook_status_for_dashboard()['codex_balanced_profile']['installed']

def test_codex_daemon_uses_native_lifecycle(measure, monkeypatch):
    m = measure
    monkeypatch.setattr(m, '_daemon_snapshot_sandboxed', lambda: False)
    monkeypatch.setattr(m, '_read_config_flag', lambda key, default=None: default)
    monkeypatch.setattr(m, '_daemon_install_failed_marker_present', lambda: False)
    monkeypatch.setattr(m, '_normalized_platform', lambda: 'Windows')
    monkeypatch.setattr(m, '_daemon_service_installed', lambda system: True)
    monkeypatch.setattr(m, '_verify_daemon_port', lambda **kw: True)
    assert m._ensure_dashboard_daemon() == 'noop-healthy'

def test_compact_prompt_is_root_key_and_preserves_tables():
    original = 'model = "gpt-5.4"\n[plugins.example]\nenabled = true\n'
    updated, _ = cp._replace_or_append_config(original, Path('/prompt.md'), force=False)
    parsed = tomllib.loads(updated)
    assert parsed.pop('experimental_compact_prompt_file') == str(Path('/prompt.md'))
    assert parsed == tomllib.loads(original)

def test_repair_and_uninstall_preserve_entries_inside_old_markers():
    original = ('[plugins.example]\nenabled = true\n' + cp.MANAGED_BEGIN + '\n'
                'experimental_compact_prompt_file = "/old.md"\n'
                '[hooks.state.example]\ntrusted_hash = "keep"\n' + cp.MANAGED_END + '\n')
    repaired, _ = cp._replace_or_append_config(original, Path('/new.md'), force=False)
    parsed = tomllib.loads(repaired)
    assert parsed['experimental_compact_prompt_file'] == str(Path('/new.md'))
    assert parsed['hooks']['state']['example']['trusted_hash'] == 'keep'
    assert parsed['plugins']['example'] == {'enabled': True}
    removed, _ = cp._strip_managed_block(original)
    assert tomllib.loads(removed)['hooks']['state']['example']['trusted_hash'] == 'keep'

@pytest.fixture
def measure(tmp_path, monkeypatch):
    monkeypatch.setenv('TOKEN_OPTIMIZER_RUNTIME', 'codex')
    monkeypatch.setenv('TOKEN_OPTIMIZER_SNAPSHOT_DIR', str(tmp_path / 'data'))
    import measure as m
    monkeypatch.setattr(m, 'SNAPSHOT_DIR', tmp_path / 'data')
    monkeypatch.setattr(m, 'TRENDS_DB', tmp_path / 'data/trends.db')
    monkeypatch.setattr(m, 'detect_runtime', lambda: 'codex')
    monkeypatch.setattr(cs, 'session_roots', lambda: (tmp_path / 'sessions',))
    return m

def test_model_attribution_is_session_scoped(measure, tmp_path, monkeypatch):
    write_session(tmp_path / 'sessions' / f'rollout-2026-09-06-{SID}.jsonl')
    monkeypatch.setenv('CLAUDE_MODEL', 'sonnet')
    assert measure._resolve_session_model(SID) == 'gpt-5.4'
    assert measure._resolve_session_model('missing-session') == 'unknown'
    assert measure._extract_session_uuid(f'rollout-2026-09-06-{SID}') == (SID, False)

def test_savings_use_openai_prices_and_unknown_is_not_sonnet(measure):
    measure._log_savings_event('test', 1000, SID, model='gpt-5.4')
    measure._log_savings_event('test', 1000, SID, model='gpt-future-unknown')
    c = measure._init_trends_db()
    rows = c.execute('SELECT model, cost_saved_usd FROM savings_events ORDER BY id').fetchall()
    c.close()
    assert rows[0] == ('gpt-5.4', 1000 * measure.OPENAI_MODEL_PRICING['gpt-5.4']['input'] / 1e6)
    assert rows[1] == ('gpt-future-unknown', None)

def test_claude_setting_detector_does_not_read_claude_in_codex(monkeypatch):
    from detectors import respond_to_bash as detector
    monkeypatch.setattr(detector, 'detect_runtime', lambda: 'codex')
    monkeypatch.setattr(detector, '_load_settings', lambda p: pytest.fail('read Claude settings'))
    assert detector.detect_respond_to_bash({}) == []

def test_task_duration_excludes_days_between_resumes(tmp_path):
    p = write_session(tmp_path / 'session.jsonl')
    records = [json.loads(line) for line in p.read_text().splitlines()]
    records[0]['timestamp'] = '2026-09-01T00:00:00Z'
    records.append({'timestamp': '2026-09-06T00:00:00Z', 'type': 'event_msg',
                    'payload': {'type': 'task_complete', 'duration_ms': 120000}})
    p.write_text('\n'.join(map(json.dumps, records)))
    parsed = cs.parse_session_jsonl(p)
    assert parsed['duration_minutes'] == 2
    assert parsed['wall_duration_minutes'] == 7200

def test_collect_refreshes_resumed_codex_session(measure, tmp_path, monkeypatch):
    p = write_session(tmp_path / 'sessions' / f'rollout-2026-09-06-{SID}.jsonl', (100,))
    monkeypatch.setattr(measure, '_find_all_jsonl_files', lambda days: [(p, p.stat().st_mtime, 'project')])
    monkeypatch.setattr(measure, '_find_subagent_jsonl_files', lambda p: [])
    monkeypatch.setattr(measure, '_session_stale_waste_tokens', lambda p: 0)
    monkeypatch.setattr(measure, 'score_session_quality', lambda p: {'score': 80, 'grade': 'A'})
    monkeypatch.setattr(measure, '_needs_streaming_dedup_rebuild', lambda c: False)
    monkeypatch.setattr(measure, '_needs_model_daily_rebuild', lambda c: False)
    assert measure.collect_sessions(quiet=True) == 1
    write_session(p, (100, 200))
    import time
    os.utime(p, (time.time() + 1, time.time() + 1))
    assert measure.collect_sessions(quiet=True) == 1
    c = measure._init_trends_db()
    rows = c.execute('SELECT input_tokens,output_tokens,session_uuid FROM session_log').fetchall()
    c.close()
    assert rows == [(200, 40, SID)]
