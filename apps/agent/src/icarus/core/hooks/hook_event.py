"""Hook 事件与观测快照。"""

from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
import enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class HookEvent:
    """分发给 Hook Handler 的不可变事件信封。"""

    name: str
    phase: str
    event_id: str
    run_id: str | None
    occurred_at: datetime
    context: Mapping[str, Any]
    data: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        name: str,
        phase: str,
        run_id: str | None,
        data: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> "HookEvent":
        return cls(
            name=name,
            phase=phase,
            event_id=uuid4().hex,
            run_id=run_id,
            occurred_at=datetime.now(UTC),
            context=MappingProxyType(snapshot(dict(context or {}))),
            data=MappingProxyType(snapshot(dict(data or {}))),
        )


def snapshot(value: Any) -> Any:
    """将运行对象转换为便于持久化的观测快照。"""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: snapshot(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [snapshot(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)
