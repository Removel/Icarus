import base64
import hashlib
import json

from errors import ServiceError


def _fingerprint(filters: dict) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def encode_cursor(last: str, filters: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps([last, _fingerprint(filters)]).encode()).decode()


def decode_cursor(cursor: str | None, filters: dict) -> str:
    if cursor is None:
        return ""
    try:
        if len(cursor) > 4096:
            raise ValueError
        last, fingerprint = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(last, str) or fingerprint != _fingerprint(filters):
            raise ValueError
        return last
    except (ValueError, TypeError, UnicodeError):
        raise ServiceError("VALIDATION_ERROR", "invalid cursor or changed query filters") from None


def page(items: list[dict], limit: int, filters: dict, key: str) -> dict:
    more = len(items) > limit
    visible = items[:limit]
    return {"items": visible, "has_more": more,
            "next_cursor": encode_cursor(str(visible[-1][key]), filters) if more else None}
