"""Hook 运行上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterator, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class HookContext:
    run_id: str
    data: Mapping[str, Any]


_current_hook_context: ContextVar[HookContext | None] = ContextVar(
    "agent_hook_context",
    default=None,
)


def get_hook_context() -> HookContext | None:
    return _current_hook_context.get()


def set_hook_context(context: HookContext) -> Token[HookContext | None]:
    return _current_hook_context.set(context)


def reset_hook_context(token: Token[HookContext | None]) -> None:
    _current_hook_context.reset(token)


@contextmanager
def hook_context(
    data: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> Iterator[HookContext]:
    context = HookContext(
        run_id=run_id or uuid4().hex,
        data=MappingProxyType(dict(data or {})),
    )
    token = set_hook_context(context)
    try:
        yield context
    finally:
        reset_hook_context(token)
