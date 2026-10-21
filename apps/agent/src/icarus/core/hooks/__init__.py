"""Agent Hook 接口框架。"""

from icarus.core.hooks.base_hook import BaseHook
from icarus.core.hooks.hook_context import (
    HookContext,
    get_hook_context,
    hook_context,
)
from icarus.core.hooks.hook_dispatcher import (
    HookDispatcher,
)
from icarus.core.hooks.hook_event import HookEvent
from icarus.core.hooks.hook_registry import HookRegistry

__all__ = [
    "BaseHook",
    "HookContext",
    "HookDispatcher",
    "HookEvent",
    "HookRegistry",
    "get_hook_context",
    "hook_context",
]
