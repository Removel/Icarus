"""Run the actual test launcher against a controlled interpreter executable."""

import json
from pathlib import Path
import shutil
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test.sh"


def test_test_script_forwards_focused_arguments_and_failure_status(tmp_path):
    root = tmp_path / "repository"
    script = root / "apps" / "agent" / "scripts" / "test.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    python = root / "apps" / "agent" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    capture = tmp_path / "calls.jsonl"
    python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"p = pathlib.Path({str(capture)!r})\n"
        "with p.open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(7 if 'focused_failure.py' in sys.argv else 0)\n"
    )
    python.chmod(0o700)
    completed = subprocess.run(["bash", str(script), "focused_failure.py", "-x"], capture_output=True)
    assert completed.returncode == 7
    assert [json.loads(line) for line in capture.read_text().splitlines()] == [
        ["-m", "pytest", "focused_failure.py", "-x"]
    ]

    capture.unlink()
    completed = subprocess.run(["bash", str(script)], capture_output=True)
    assert completed.returncode == 0
    assert [json.loads(line) for line in capture.read_text().splitlines()] == [
        ["-m", "pytest", "apps/agent/test", "-q"],
        ["-m", "compileall", "-q", "apps/agent/src", "apps/agent/test", "packages"],
    ]
