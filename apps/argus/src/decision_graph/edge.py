"""Control edges can collect an output before their target runs."""
from collections.abc import Callable
from dataclasses import dataclass
from .node import NodeCallable, InputBinder


def always(_: object) -> bool:
    return True


@dataclass(frozen=True)
class EdgeAction:
    action: NodeCallable
    bind_input: InputBinder


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    condition: Callable[[object], bool] = always
    collect: EdgeAction | None = None
    id: str | None = None
    timeout_seconds: float = 10.0
