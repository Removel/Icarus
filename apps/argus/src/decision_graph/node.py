"""Explicit keyword arguments at the node boundary."""
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from .graph import RepeatBlock

NodeCallable = Callable[..., Awaitable[object]]
InputBinder = Callable[[object, Mapping[str, object]], Mapping[str, object]]


@dataclass(frozen=True)
class NodeBinding:
    id: str
    evaluate: 'NodeCallable | RepeatBlock'
    bind_input: InputBinder
    requires: tuple[str, ...] = ()
    join: Literal['all', 'any', 'selected'] = 'all'
    timeout_seconds: float = 10.0
