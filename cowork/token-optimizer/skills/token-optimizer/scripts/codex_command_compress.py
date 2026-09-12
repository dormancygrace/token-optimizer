"""Codex command rewrite adapter. Execute once, retain failures and archive originals."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

from bash_whitelist import has_dangerous_chars, is_whitelisted

MAX_COMPRESS_BYTES = 8 * 1024 * 1024


def eligible(command):
    if not isinstance(command, str) or not command.strip() or len(command) > 16000:
        return False
    if has_dangerous_chars(command) or 'codex_command_compress' in command:
        return False
    # Literal read-only PowerShell cmdlets. No script blocks, expressions or
    # profile functions; the original text is passed intact to the same shell.
    if re.match(r'^(Get-Content|Get-ChildItem)\s', command, re.I):
        return not re.search(r'[{}()]|\s-(?:Wait|Stream)\b', command, re.I)
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if not args:
        return False
    # The shared Claude list also contains builds, tests and write-capable
    # subcommands. Codex 'allow' must only rewrite inspection commands.
    if args[0] == 'git':
        return len(args) > 1 and args[1] in ('status', 'log') and not any(
            a.startswith(('--output', '--ext-diff', '--textconv')) for a in args[2:])
    if args[0] in ('rg', 'grep', 'ls', 'tree', 'wc', 'head', 'tail'):
        return not any(a.startswith(('--pre', '--hostname-bin')) for a in args[1:]) and is_whitelisted(command)
    return False


def _shell(tool_input):
    requested = tool_input.get('shell')
    if requested:
        shell = shutil.which(requested) or (requested if Path(requested).is_file() else None)
    else:
        shell = shutil.which('pwsh') or shutil.which('powershell') if os.name == 'nt' else shutil.which('bash')
    if not shell or Path(shell).stem.lower() not in ('pwsh', 'powershell', 'bash', 'sh', 'zsh'):
        return None
    return shell


def rewrite(payload):
    from plugin_env import is_v5_flag_enabled
    if not is_v5_flag_enabled('v5_bash_compress', 'TOKEN_OPTIMIZER_BASH_COMPRESS', default=True):
        return None
    if payload.get('tool_name') != 'Bash':
        return None
    tool_input = payload.get('tool_input') or {}
    command = tool_input.get('command')
    shell = _shell(tool_input)
    if not eligible(command) or not shell:
        return None
    plan = {'command': command, 'shell': shell, 'session_id': payload.get('session_id'),
            'model': payload.get('model'), 'cwd': payload.get('cwd')}
    encoded = base64.b64encode(json.dumps(plan).encode()).decode()
    argv = [sys.executable, str(Path(__file__).resolve()), '--run', encoded]
    if Path(shell).stem.lower() in ('pwsh', 'powershell'):
        rewritten = '& ' + ' '.join("'" + a.replace("'", "''") + "'" for a in argv) + '; exit $LASTEXITCODE'
    else:
        rewritten = shlex.join(argv)
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                  'updatedInput': {**tool_input, 'command': rewritten}}}


def run(plan):
    command, shell = plan['command'], plan['shell']
    # Revalidate at execution time: never turn a trusted wrapper into a generic
    # command launcher. Ineligible commands are not executed by this wrapper.
    if not eligible(command):
        print('Token Optimizer: command is not eligible', file=sys.stderr)
        return 2
    os.environ['TOKEN_OPTIMIZER_RUNTIME'] = 'codex'
    if plan.get('session_id'):
        os.environ['TOKEN_OPTIMIZER_SESSION_ID'] = str(plan['session_id'])
    if Path(shell).stem.lower() in ('pwsh', 'powershell'):
        tail = '; $toSucceeded=$?; $toExit=$LASTEXITCODE; if ($null -ne $toExit) { exit $toExit }; if (-not $toSucceeded) { exit 1 }'
        argv = [shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command + tail]
    else:
        argv = [shell, '-c', command]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(argv, cwd=plan.get('cwd') or None, stdout=output, stderr=errors,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output.seek(0, 2)
        size = output.tell()
        output.seek(0)
        errors.seek(0)
        # Failure/oversized output is streamed verbatim, never buffered in RAM.
        if result.returncode != 0 or size > MAX_COMPRESS_BYTES:
            shutil.copyfileobj(output, sys.stdout.buffer)
            shutil.copyfileobj(errors, sys.stderr.buffer)
            return result.returncode
        raw_bytes = output.read()
        error_bytes = errors.read(MAX_COMPRESS_BYTES + 1)
        if error_bytes:  # preserve warnings too; no simplification on stderr
            sys.stdout.buffer.write(raw_bytes)
            sys.stderr.buffer.write(error_bytes)
            shutil.copyfileobj(errors, sys.stderr.buffer)
            return result.returncode
        try:
            raw = raw_bytes.decode('utf-8', errors='strict')
        except UnicodeError:
            sys.stdout.buffer.write(raw_bytes)
            return result.returncode
        try:
            from bash_compress import compress
            from plugin_env import resolve_snapshot_dir
            from token_estimate import estimate_tokens
            short = compress(command, raw)
            if short != raw and len(short) < len(raw) * 0.9:
                archive = resolve_snapshot_dir() / 'codex-command-output'
                archive.mkdir(parents=True, exist_ok=True)
                target = archive / (uuid.uuid4().hex + '.txt')
                with target.open('xb') as handle:
                    handle.write(raw_bytes)
                short += f'\n[Token Optimizer: full command output saved to {target}]\n'
                if estimate_tokens(short) < estimate_tokens(raw):
                    sys.stdout.buffer.write(short.encode('utf-8'))
                    sys.stdout.buffer.flush()
                    try:
                        from compression_log import log_compression_event
                        log_compression_event(feature='codex_command_compress', original_text=raw,
                            compressed_text=short, session_id=plan.get('session_id'),
                            model=plan.get('model') or 'unknown', command_pattern='read-only command',
                            verified=True, tier='measured')
                    except Exception:
                        pass
                    return 0
        except Exception:
            pass
        sys.stdout.buffer.write(raw_bytes)
        return result.returncode


def main():
    from utf8_io import enforce_utf8_io
    enforce_utf8_io()
    if len(sys.argv) == 3 and sys.argv[1] == '--run':
        return run(json.loads(base64.b64decode(sys.argv[2])))
    from hook_io import read_stdin_hook_input
    result = rewrite(read_stdin_hook_input() or {})
    if result:
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
