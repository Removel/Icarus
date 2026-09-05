"""Trace 数据递归脱敏。"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import enum
from pathlib import Path
import re
from typing import Any


DEFAULT_SENSITIVE_FIELDS = frozenset(
    {
        "auth",
        "api_key",
        "authorization",
        "token",
        "cookie",
        "password",
        "secret",
        "credential",
    }
)
_AUTHORIZATION_HEADER_VALUE = re.compile(
    r"(?im)(\b(?:authorization|proxy[_-]?authorization)\s*:\s*)[^\r\n]+"
)
_AUTHORIZATION_PARAM_VALUE = re.compile(
    r"(?i)(\b(?:authorization|proxy[_-]?authorization)\s*=\s*)"
    r"[^&\s,;}]+"
)
_SENSITIVE_TEXT_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|x[_-]?api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|session[_-]?token|client[_-]?secret|private[_-]?key|token|cookie|"
    r"password|secret|credential)"
    r"[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?([^\"'\s&,;}]+)"
)
_BEARER_VALUE = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")


class Redactor:
    def __init__(
        self,
        sensitive_fields: set[str] | frozenset[str] | None = None,
        replacement: str = "[REDACTED]",
    ) -> None:
        fields = set(DEFAULT_SENSITIVE_FIELDS)
        fields.update(sensitive_fields or set())
        self.sensitive_fields = frozenset(
            _normalize_field_name(field) for field in fields
        )
        self.replacement = replacement

    def redact(self, value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return self.redact(asdict(value))
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return {
                "type": "bytes",
                "size": len(value),
            }
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if self._is_sensitive(key_text):
                    result[key_text] = self.replacement
                else:
                    result[key_text] = self.redact(item)
            return result
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return [self.redact(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return self.redact_text(value) if isinstance(value, str) else value
        return repr(value)

    def redact_text(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = __import__("json").loads(value)
            except (ValueError, TypeError):
                pass
            else:
                return __import__("json").dumps(
                    self.redact(parsed),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        redacted = _AUTHORIZATION_HEADER_VALUE.sub(
            lambda match: match.group(1) + self.replacement, value
        )
        redacted = _AUTHORIZATION_PARAM_VALUE.sub(
            lambda match: match.group(1) + self.replacement, redacted
        )
        redacted = _SENSITIVE_TEXT_VALUE.sub(
            lambda match: match.group(1) + self.replacement, redacted
        )
        return _BEARER_VALUE.sub(
            lambda match: match.group(1) + self.replacement, redacted
        )

    def _is_sensitive(self, field_name: str) -> bool:
        normalized = _normalize_field_name(field_name)
        return any(
            normalized == field
            or normalized.endswith(f"_{field}")
            or normalized.startswith(f"{field}_")
            for field in self.sensitive_fields
        )


def _normalize_field_name(value: str) -> str:
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    normalized = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])", "_", normalized
    ).replace("-", "_").lower()
    return re.sub(r"_+", "_", normalized).strip("_")
