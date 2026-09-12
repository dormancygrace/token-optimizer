"""Execute the shipped renderer: estimates must not become measured quota savings."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_codex_activity_without_applied_compression_still_renders(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required to execute dashboard renderer')
    template = (Path(__file__).resolve().parents[1] / 'skills/token-optimizer/assets/dashboard.html').read_text(encoding='utf-8')
    body = template[template.index('  function tokensSavedCardHtml('):template.index('  function renderTrends()')]
    fixture = {'total_tokens': 0, 'period_days': 30, 'codex_estimated_tokens': 500,
               'codex_activity': {'checkpoint_restore': 2, 'loop_detection': 3}}
    script = "const data = {runtime: 'codex'}; const fn = String; const esc = String;\n" + body
    script += '\nprocess.stdout.write(tokensSavedCardHtml(' + json.dumps(fixture) + '));'
    script_path = tmp_path / 'render.cjs'
    script_path.write_text(script, encoding='utf-8')
    result = subprocess.run([node, str(script_path)], capture_output=True, text=True, check=True, timeout=10)
    assert 'Checkpoint restores: 2' in result.stdout
    assert 'Loop warnings: 3' in result.stdout
    assert 'Estimated avoided work: 500' in result.stdout
    assert 'Subscription quota impact is not exposed' in result.stdout
    assert 'tokens never sent' not in result.stdout
    assert 'metered action by action' not in result.stdout
