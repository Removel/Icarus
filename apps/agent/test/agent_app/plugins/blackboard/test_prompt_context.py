import asyncio
import inspect

from agent_app.contracts.prompt import PromptReadResponse
from agent_app.plugins.blackboard.plugin import BlackboardPlugin
from icarus.core.capabilities import CapabilityStatus
from icarus.core.contracts.context import BlackboardContextReadyEvent, ContextContributionEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.events import TaskErrorEvent
from icarus.core.models.messages import Message, TextPart, Usage


def test_required_prompt_failure_does_not_start_agent():
    assert "fatal_context_sources" in inspect.signature(BlackboardPlugin).parameters

    async def run():
        board = BlackboardPlugin("blackboard", required_context_sources={"prompt"}, fatal_context_sources={"prompt"})
        events = []
        async def publish(event):
            events.append(event)
        board.bind_publisher(publish)
        await board.consume("prompt", ContextContributionEvent(task_id="task", status="failed", error="prompt_source_failed"))
        await board.consume("user-input", UserInputEvent(task_id="task", prompt="hello"))
        assert any(isinstance(event, TaskErrorEvent) and event.fatal for event in events)
        assert not any(isinstance(event, BlackboardContextReadyEvent) for event in events)
    asyncio.run(run())


def test_compaction_refreshes_before_same_task_context_ready():
    assert "prompt_provider_enabled" in inspect.signature(BlackboardPlugin).parameters

    class Compactor:
        async def compact(self, messages):
            return Message("user", [TextPart("summary")]), Usage(100, 10)

    async def run():
        board = BlackboardPlugin("blackboard", required_context_sources=set(), prompt_provider_enabled=True,
            initial_messages=[Message("user", [TextPart("old")]), Message("assistant", [TextPart("answer")])], history_compactor=Compactor(), context_window=100)
        board._context_tokens = 85
        reasons = []
        async def request(event):
            reasons.append(event.reason)
            return PromptReadResponse(task_id=event.task_id, correlation_id=event.correlation_id, status=CapabilityStatus.OK, system_prompt="REFRESHED")
        board.request = request
        events = []
        async def publish(event):
            events.append(event)
        board.bind_publisher(publish)
        await board.consume("user-input", UserInputEvent(task_id="task", prompt="new"))
        assert reasons == ["refresh"]
        context = next(event for event in events if isinstance(event, BlackboardContextReadyEvent))
        assert context.system_prompt == "REFRESHED"
    asyncio.run(run())


def test_failed_post_compaction_refresh_is_retried_without_publishing_stale_system():
    class Compactor:
        async def compact(self, messages):
            return Message("user", [TextPart("summary")]), Usage(100, 10)

    async def run():
        board = BlackboardPlugin("blackboard", required_context_sources=set(), prompt_provider_enabled=True,
            initial_messages=[Message("user", [TextPart("old")]), Message("assistant", [TextPart("answer")])],
            history_compactor=Compactor(), context_window=100)
        board._context_tokens = 85
        requests = []
        async def request(event):
            requests.append(event.reason)
            if len(requests) == 1:
                return PromptReadResponse(task_id=event.task_id, correlation_id=event.correlation_id, status=CapabilityStatus.FAILED, error_code="prompt_too_large")
            return PromptReadResponse(task_id=event.task_id, correlation_id=event.correlation_id, status=CapabilityStatus.OK, system_prompt="RETRIED")
        board.request = request
        events = []
        async def publish(event):
            events.append(event)
        board.bind_publisher(publish)
        await board.consume("user-input", UserInputEvent(task_id="failed", prompt="hello"))
        assert not any(isinstance(event, BlackboardContextReadyEvent) for event in events)
        await board.consume("user-input", UserInputEvent(task_id="retry", prompt="hello"))
        assert requests == ["refresh", "refresh"]
        ready = [event for event in events if isinstance(event, BlackboardContextReadyEvent)]
        assert len(ready) == 1 and ready[0].system_prompt == "RETRIED"
    asyncio.run(run())
