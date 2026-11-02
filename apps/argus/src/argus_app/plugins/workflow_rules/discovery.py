"""Trusted initial discovery defaults; executed by the same restricted rule engine."""
from pathlib import Path

DEFAULT_DISCOVERY_RULE = '''def decide(event, channel):
    text = event["content"].get("text", "").lower()
    requested = any(word in text for word in ["请帮", "帮我", "请跟进", "需要跟进", "请处理", "提醒我", "please help", "please track", "remind me"])
    return {"passed": requested, "reason": "explicit request" if requested else "not an explicit request"}
'''

INITIAL_CHANNEL_RULE = '''def decide(event, channel):
    return {"passed": False, "reason": "initial event already delivered; Agent must publish the next observation rule"}
'''


def load_discovery_rule(path: str | None = None) -> str:
    from argus_app.rule_sdk.engine import lint
    source = Path(path).read_text(encoding='utf-8') if path else DEFAULT_DISCOVERY_RULE
    lint(source)
    return source
