"""Declared before hooks rewrite actual model requests, including streams."""

from dataclasses import replace

from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry
from icarus.core.hooks.wrappers import ObservableLLM
from icarus.core.models.messages import Message, TextPart
from apps.agent.test.agent_app.test_assembly import StubLLM


def test_prompt_rewrite_reaches_actual_llm():
    registry = HookRegistry()
    dispatcher = HookDispatcher(registry)
    seen = []
    class Model(StubLLM):
        def invoke(self, messages, tools=None):
            seen.extend(messages)
            return super().invoke(messages, tools)
    class Rewrite(BaseHook):
        def handle(self, event, value):
            return replace(value, messages=[Message("user", [TextPart("rewritten")])])
    registry.register("llm.invoke", Rewrite(), priority=1)
    llm = ObservableLLM(Model("worker"), dispatcher)
    llm.invoke([Message("user", [TextPart("original")])])
    assert seen == [Message("user", [TextPart("rewritten")])]
