"""Incremental, bounded-memory telemetry index for large Codex transcripts.

Raw tool output is never copied into the index. Each pass commits at most
64 MiB of new input; pending files are revisited even when their mtime is idle.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

from plugin_env import resolve_snapshot_dir

PASS_BYTES = 64 * 1024 * 1024
LINE_BYTES = 16 * 1024 * 1024
SCHEMA_VERSION = 2


def _connect():
    root = resolve_snapshot_dir()
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / 'codex-log-index.db', timeout=0.2)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, identity TEXT, offset INTEGER,
        size INTEGER, skipped INTEGER, revision INTEGER, mtime INTEGER);
      CREATE TABLE IF NOT EXISTS records(path TEXT, offset INTEGER, data TEXT,
        PRIMARY KEY(path, offset));
    ''')
    if 'mtime' not in {row[1] for row in conn.execute('PRAGMA table_info(files)')}:
        conn.execute('ALTER TABLE files ADD COLUMN mtime INTEGER')
    return conn


def pending(filepath):
    path = Path(filepath).resolve()
    try:
        conn = _connect()
        try:
            row = conn.execute('SELECT offset,revision,mtime FROM files WHERE path=?', (str(path),)).fetchone()
            stat = path.stat()
            return (row is None or row[0] != stat.st_size or
                    row[1] != SCHEMA_VERSION or row[2] != stat.st_mtime_ns)
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return True


def _slim(record):
    from codex_session import _extract_text, _event_output_text
    payload = record.get('payload')
    if not isinstance(payload, dict):
        return None
    kind = payload.get('type')
    if record.get('type') in ('session_meta', 'turn_context'):
        fields = ('id', 'cwd', 'cli_version', 'model', 'effort', 'thread_settings', 'collaboration_mode')
        payload = {k: payload[k] for k in fields if k in payload}
    elif kind in ('user_message', 'agent_message', 'message'):
        text = _extract_text(payload)
        payload = {'type': kind, 'role': payload.get('role'), 'message': text[:1000],
                   'content': text[:1000], '_optimizer_chars': len(text)}
    elif kind in ('function_call_output', 'custom_tool_call_output', 'exec_command_end', 'patch_apply_end'):
        chars = len(str(payload.get('output') or '')) if kind.endswith('call_output') else len(_event_output_text(payload))
        payload = {'type': kind, '_optimizer_output_chars': chars, 'duration': payload.get('duration')}
    elif kind in ('function_call', 'custom_tool_call'):
        payload = {'type': kind, 'name': payload.get('name'),
                   'arguments': payload.get('arguments') if payload.get('name') == 'spawn_agent' else None}
    elif kind not in ('token_count', 'task_complete', 'collab_agent_spawn_end', 'mcp_tool_call_end'):
        return None
    # Bound metadata as well as outputs; abnormal records are diagnosed as skipped.
    compact = {'type': record.get('type'), 'timestamp': record.get('timestamp'), 'payload': payload}
    return compact


def records(filepath):
    path = Path(filepath).resolve()
    key = str(path)
    conn = _connect()
    try:
        with path.open('rb') as handle:
            stat = path.stat()
            identity = f'{SCHEMA_VERSION}:{stat.st_dev}:{stat.st_ino}:' + hashlib.sha256(handle.readline(65536)).hexdigest()
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT identity,offset,skipped,mtime FROM files WHERE path=?', (key,)).fetchone()
            if not row or row[0] != identity or row[1] > stat.st_size or (row[1] == stat.st_size and row[3] != stat.st_mtime_ns):
                conn.execute('DELETE FROM records WHERE path=?', (key,))
                offset, skipped = 0, 0
            else:
                offset, skipped = row[1], row[2]
            handle.seek(offset)
            end = min(stat.st_size, offset + PASS_BYTES)
            while handle.tell() < end:
                start = handle.tell()
                line = handle.readline(LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > LINE_BYTES:
                    while line and not line.endswith(b'\n'):
                        line = handle.readline(65536)
                    if not line or not line.endswith(b'\n'):
                        handle.seek(start)
                        break
                    skipped += 1
                else:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError):
                        if not line.endswith(b'\n'):
                            handle.seek(start)  # writer has not completed this record
                            break
                        skipped += 1
                        record = None
                    if isinstance(record, dict):
                        compact = _slim(record)
                        if compact:
                            value = json.dumps(compact, ensure_ascii=False)
                            if len(value) <= 65536:
                                conn.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (key, start, value))
                            else:
                                skipped += 1
                offset = handle.tell()
            conn.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                         (key, identity, offset, stat.st_size, skipped, SCHEMA_VERSION, stat.st_mtime_ns))
            conn.commit()
        info = {'incomplete': offset < stat.st_size or skipped > 0,
                'scan_mode': 'indexing' if offset < stat.st_size else 'indexed_full',
                'indexed_bytes': offset, 'source_bytes': stat.st_size, 'skipped_records': skipped}
        # Cursor iteration keeps memory bounded even for years of tool events.
        def iterate():
            try:
                for (value,) in conn.execute('SELECT data FROM records WHERE path=? ORDER BY offset', (key,)):
                    yield json.loads(value)
            finally:
                conn.close()
        return iterate(), info
    except Exception:
        conn.close()
        raise
