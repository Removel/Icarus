"""Value hooks may not modify expiry policies or corrupt intermediate tool history."""

from dataclasses import replace
from datetime import UTC, datetime
import pytest
from icarus.core.hooks.points import preserve_event_control
from icarus.core.hooks.values import preserve_agent_control, LLMCallValue
from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry
from icarus.core.run_control import TaskContextInputEvent
from icarus.core.agents import AgentResponse
from icarus.core.models.messages import Message, TextPart, ToolCall


def test_hook_cannot_remove_context_expiration():
    event = TaskContextInputEvent(content="stale", expires_at=datetime(2020, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        preserve_event_control(event, replace(event, expires_at=None))


def test_agent_after_hook_cannot_rewrite_intermediate_tool_identity():
    final = Message("assistant", [TextPart("done")])
    call = Message("assistant", [], [ToolCall("read-id", "read", {"path": "file"})])
    tool = Message("tool", [TextPart("value")], tool_call_id="read-id")
    response = AgentResponse(message=final, messages=[Message("user", [TextPart("hello")]), call, tool, final], task_message_start=0)
    forged = replace(response, messages=[response.messages[0], Message("assistant", [], [ToolCall("other", "bash", {})]), tool, final])
    with pytest.raises(ValueError):
        preserve_agent_control(response, forged)


def test_invalid_llm_hook_field_type_rolls_back():
    from icarus.core.hooks.wrappers import ObservableLLM
    from apps.agent.test.agent_app.test_assembly import StubLLM
    registry = HookRegistry()
    dispatcher = HookDispatcher(registry)
    llm = ObservableLLM(StubLLM("worker"), dispatcher)
    class Bad(BaseHook):
        def handle(self, event, value):
            if event.phase == "before":
                return replace(value, messages=None)
            return value
    registry.register("llm.invoke", Bad(), priority=0)
    messages = [Message("user", [TextPart("valid")])]
    value = dispatcher.trigger("llm.invoke", "before", LLMCallValue(messages, None))
    assert value.messages == messages
