"""Validate whole tool requests before any relationship write."""

import json
import math
import re
from typing import Any

from agent_app.plugins.relationship.models import split_identity


_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


def keys(arguments: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    if required - arguments.keys():
        raise ValueError("missing required arguments")
    if arguments.keys() - required - (optional or set()):
        raise ValueError("unknown arguments")


def text(value: Any, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value.strip()


def identifier(value: Any, name: str) -> str:
    value = text(value, name, 128)
    if not _ID.fullmatch(value):
        raise ValueError(f"{name} has invalid format")
    return value


def attribute_key(value: Any) -> str:
    value = text(value, "profile key", 128)
    if not _KEY.fullmatch(value):
        raise ValueError("profile key has invalid format")
    return value


def identities(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError("identities must be a list of at most 10 identities")
    result = []
    for item in value:
        parsed = split_identity(text(item, "identity", 289))
        if parsed is None:
            raise ValueError("identity requires a non-name namespace prefix")
        namespace, raw = parsed
        raw = text(raw, "identity value")
        normalized = re.sub(r"[^0-9]", "", raw) if namespace in {"qq", "phone"} else raw if namespace == "wechat" else raw.lower()
        if not normalized:
            raise ValueError("identity normalizes to an empty value")
        identity = f"{namespace}:{normalized}"
        if identity not in result:
            result.append(identity)
    return result


def profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > 50:
        raise ValueError("profile must be an object with at most 50 keys")
    for key, item in value.items():
        if attribute_key(key) != key:
            raise ValueError("profile key must not have surrounding whitespace")
        _json_value(item)
        if len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 65536:
            raise ValueError("profile value exceeds 64 KiB")
    return value


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 50:
        raise ValueError("profile value nesting exceeds safety limit")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("profile values must be valid JSON")
