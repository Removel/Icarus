"""Trusted initial discovery defaults; executed by the same restricted rule engine."""
from pathlib import Path

DEFAULT_DISCOVERY_RULE = '''def main(event, channel):
    text = event["content"].get("text", "")
    candidates = channel.get("candidates", [])
    if not text.strip():
        return {"action": "ignore", "reason": "empty message"}
    result = capability("semantic", {"mode": "discovery", "text": text})
    decision = result["discovery"]
    decision["reason"] = result.get("reason", "discovery judgement")
    return decision
'''

INITIAL_CHANNEL_RULE = '''def main(event, channel):
    return {"passed": False, "reason": "initial event already delivered; Agent must publish the next observation rule"}
'''


def load_discovery_rule(path: str | None = None) -> str:
    from argus_app.rule_sdk.engine import lint
    source = Path(path).read_text(encoding='utf-8') if path else DEFAULT_DISCOVERY_RULE
    lint(source)
    return source
