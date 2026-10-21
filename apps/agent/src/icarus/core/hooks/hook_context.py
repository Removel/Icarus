"""Hook 运行上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterator, Mapping
from uuid import uuid4


_RUN_ID_UNSET = object()


@dataclass(frozen=True)
class HookContext:
    run_id: str | None
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
    run_id: str | None | object = _RUN_ID_UNSET,
    new_run: bool = False,
) -> Iterator[HookContext]:
    parent = get_hook_context()
    merged_data = dict(parent.data) if parent else {}
    merged_data.update(data or {})
    if new_run:
        selected_run_id: str | None = uuid4().hex
    elif run_id is not _RUN_ID_UNSET:
        selected_run_id = run_id if isinstance(run_id, str) else None
    elif parent is not None:
        selected_run_id = parent.run_id
    else:
        selected_run_id = uuid4().hex
    context = HookContext(
        run_id=selected_run_id,
        data=MappingProxyType(merged_data),
    )
    token = set_hook_context(context)
    try:
        yield context
    finally:
        reset_hook_context(token)
