"""The Gateway launcher forwards focused pytest arguments and real exit status."""

import json
from pathlib import Path
import shutil
import subprocess


def test_gateway_test_script_forwards_arguments_and_failure(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'scripts/test.sh'
    script = tmp_path / 'apps/gateway/scripts/test.sh'
    script.parent.mkdir(parents=True)
    shutil.copyfile(source, script)
    python = tmp_path / 'apps/gateway/.venv/bin/python'
    python.parent.mkdir(parents=True)
    calls = tmp_path / 'calls.jsonl'
    python.write_text('#!/usr/bin/env python3\nimport json,pathlib,sys\n'
        f'with pathlib.Path({str(calls)!r}).open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
        'raise SystemExit(7 if "focused.py" in sys.argv else 0)\n')
    python.chmod(0o700)
    result = subprocess.run(['bash', str(script), 'focused.py', '-x'], capture_output=True)
    assert result.returncode == 7
    assert [json.loads(line) for line in calls.read_text().splitlines()] == [['-m', 'pytest', 'focused.py', '-x']]
