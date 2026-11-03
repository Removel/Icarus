"""Trusted initial discovery defaults; executed by the same restricted rule engine."""
from pathlib import Path
import json


def candidate_summaries(owners):
    """One trusted, UTF-8 JSON byte-bounded snapshot for both classifiers."""
    result = []
    for owner in owners:
        fields = {'title': owner.title[:120], 'goal': owner.goal[:1000],
                  'progress': owner.progress[:1000]}
        def build(limit):
            return {'id': owner.id, **{name: text[:limit] for name, text in fields.items()},
                    'lifecycle': owner.lifecycle}
        def size(value):
            return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        candidate = build(1000)
        if size(candidate) > 4096:
            low, high = 0, 1000
            while low < high:
                middle = (low + high + 1) // 2
                if size(build(middle)) <= 4096:
                    low = middle
                else:
                    high = middle - 1
            candidate = build(low)
            if size(candidate) > 4096:
                raise ValueError('discovery candidate metadata exceeds byte limit')
        result.append(candidate)
    return result

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
