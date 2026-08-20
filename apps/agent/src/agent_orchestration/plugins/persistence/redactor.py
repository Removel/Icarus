"""Trace 数据递归脱敏。"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import enum
from pathlib import Path
from typing import Any


DEFAULT_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "token",
        "cookie",
        "password",
        "secret",
        "credential",
    }
)


class Redactor:
    def __init__(
        self,
        sensitive_fields: set[str] | frozenset[str] | None = None,
        replacement: str = "[REDACTED]",
    ) -> None:
        fields = set(DEFAULT_SENSITIVE_FIELDS)
        fields.update(sensitive_fields or set())
        self.sensitive_fields = frozenset(field.lower() for field in fields)
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
            return value
        return repr(value)

    def _is_sensitive(self, field_name: str) -> bool:
        normalized = field_name.lower()
        return any(
            normalized == field
            or normalized.endswith(f"_{field}")
            or normalized.startswith(f"{field}_")
            for field in self.sensitive_fields
        )
