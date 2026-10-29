"""App-owned portable regressions; Linux additionally runs the complete suite."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
TESTS = (
    "agent_app/plugins/memory/test_factory.py",
    "agent_app/plugins/memory/test_plugin.py",
    "agent_app/plugins/memory/test_adapter.py",
    "agent_app/plugins/memory/test_tools.py",
    "agent_app/plugins/builtin_tools/test_tools.py",
    "agent_app/application/test_memory_context.py",
    "agent_app/application/test_agent_runtime.py",
)


if __name__ == "__main__":
    raise SystemExit(subprocess.call([
        sys.executable, "-m", "pytest",
        *(str(ROOT / "apps/agent/test" / path) for path in TESTS), *sys.argv[1:],
    ], cwd=ROOT))
