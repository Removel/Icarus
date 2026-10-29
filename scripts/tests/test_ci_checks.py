"""Exercise CI checks against real invalid input, without changing this repository."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/ci/checks.py"


def run_check(*args, cwd=None):
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    if os.name == "nt":
        environment["PATH"] = str(Path(shutil.which("git")).parents[1] / "bin") + os.pathsep + environment["PATH"]
    return subprocess.run(
        [sys.executable, str(CHECK), *args], cwd=cwd, env=environment,
        capture_output=True, text=True, encoding="utf-8",
    )


def test_shell_check_rejects_invalid_second_file(tmp_path):
    good = tmp_path / "first script.sh"
    bad = tmp_path / "第二个.sh"
    good.write_text("echo ok\n", encoding="utf-8")
    bad.write_text("if then\n", encoding="utf-8")
    result = run_check("shell", str(good), str(bad))
    assert result.returncode == 1, result.stderr
    assert "第二个.sh" in result.stdout


def test_shell_check_accepts_valid_scripts(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text("echo ok\n", encoding="utf-8")
    result = run_check("shell", str(script))
    assert result.returncode == 0, result.stderr


def test_diff_check_detects_whitespace_between_supplied_trees(tmp_path):
    # --no-index supplies the same real Git whitespace checker without creating commits.
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    before.write_text("valid\n", encoding="utf-8")
    after.write_text("valid\ninvalid trailing space \n", encoding="utf-8")
    result = run_check("diff", "--no-index", str(before), str(after))
    assert result.returncode != 0
    assert "trailing whitespace" in result.stdout


def test_compile_check_rejects_missing_source_directory(tmp_path):
    result = run_check("compile", str(tmp_path / "missing"))
    assert result.returncode == 1
    assert "missing" in result.stdout
