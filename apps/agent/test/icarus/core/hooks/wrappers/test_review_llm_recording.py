"""Execution summary includes model accounting without persisting message bodies."""

from icarus.core.hooks import HookDispatcher, HookRegistry
from icarus.core.hooks.wrappers import ObservableLLM
from icarus.core.models.messages import Message, TextPart, Usage, LLMResponse
from apps.agent.test.agent_app.test_assembly import StubLLM
from apps.agent.test.icarus.recording_support import CaptureRecords


def test_llm_complete_record_has_usage_finish_and_duration():
    registry = HookRegistry()
    capture = CaptureRecords(); registry.recorder = capture
    class Model(StubLLM):
        def invoke(self, messages, tools=None):
            return LLMResponse(Message("assistant", [TextPart("private output")]), usage=Usage(7, 3), finish_reason="stop")
    llm = ObservableLLM(Model("worker"), HookDispatcher(registry))
    llm.invoke([Message("user", [TextPart("private input")])])
    after = next(row for row in capture.events if row.name == "llm.invoke" and row.phase == "after")
    assert after.data["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert after.data["finish_reason"] == "stop"
    assert after.data["duration_seconds"] >= 0
    assert "private output" not in str(after.data)
