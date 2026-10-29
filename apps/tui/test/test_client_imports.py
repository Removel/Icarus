"""Client imports must work independently of the Textual/Agent runtime."""

import json
from pathlib import Path
import subprocess
import sys


def test_gateway_imports_without_ui_or_agent():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; from apps.tui.src.client.gateway import GatewayClient; from apps.tui.src.client.gateway.ports import UpdateSubscriptionPort; print(json.dumps([n for n in sys.modules if n.split('.')[0] in {'textual','agent_app','openai','anthropic'}]))",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []
