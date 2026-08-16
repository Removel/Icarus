import asyncio

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.agent.src.application.agent_runtime_service import AgentRuntimeService
from apps.agent.src.model_config import (
    ConfigModel,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.types import Message, TextPart


def make_config(data_dir) -> ConfigModel:
    model = LLMConfig(
        model_name="test-model",
        max_tokens=1024,
        temperature=0,
        default_think_level=ThinkMode.LOW,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        icarus_data_dir=data_dir,
        model_settings=ModelSettings(
            thinking=model,
            perception=model,
        ),
    )


class AgentStub:
    async def astream(self, **kwargs):
        prompt = kwargs["input_prompt"]
        message = Message("assistant", [TextPart(f"answer:{prompt}")])
        yield AgentTextDeltaEvent(step=1, text=f"answer:{prompt}")
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=message,
                finish_reason="stop",
                steps=1,
            ),
        )


async def collect_task_events(
    service: AgentRuntimeService,
    task_id: str,
):
    events = []
    while True:
        source_plugin_id, event = await asyncio.wait_for(
            service.next_event(),
            timeout=1,
        )
        if event.correlation_id != task_id:
            continue
        events.append((source_plugin_id, event))
        if isinstance(event, InputFinishedEvent):
            return events


def test_runtime_service_组装固定session并转发完整任务事件(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="session-1",
            config=make_config(tmp_path / "data"),
        )
        service.agent_factory.get_agent = lambda model_role: AgentStub()
        await service.start()
        accepted = await service.submit("hello", [])
        events = await collect_task_events(service, accepted.task_id)
        running_session_id = service.session_id
        await service.stop(timeout=1)
        return (
            service,
            accepted,
            events,
            running_session_id,
            service.persistence.trace_hook.skipped_count,
        )

    service, accepted, events, running_session_id, skipped_count = asyncio.run(run())

    assert accepted.queue_position == 0
    assert running_session_id == "session-1"
    assert [source for source, _ in events] == [
        "user-input",
        "user-input",
        "user-input",
        "agent",
        "agent",
        "user-input",
    ]
    assert isinstance(events[0][1], InputQueuedEvent)
    assert isinstance(events[1][1], InputStartedEvent)
    assert isinstance(events[2][1], UserInputEvent)
    assert isinstance(events[3][1], AgentTextDeltaEvent)
    assert isinstance(events[4][1], AgentCompletedEvent)
    assert isinstance(events[5][1], InputFinishedEvent)
    assert events[5][1].status == "completed"
    assert service.is_running is False
    assert service.session_id is None
    assert skipped_count == 0


def test_runtime_service_未启动时拒绝提交和读取事件(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        with pytest.raises(RuntimeError, match="not running"):
            await service.submit("hello", [])
        with pytest.raises(RuntimeError, match="not running"):
            await service.next_event()

    asyncio.run(run())


def test_runtime_service_停止后再次启动给出明确生命周期错误(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        await service.start()
        await service.stop(timeout=1)

        with pytest.raises(RuntimeError, match="cannot be restarted"):
            await service.start()

    asyncio.run(run())


def test_runtime_service_llm关闭失败时仍清理session和持久化(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        await service.start()

        async def fail_to_close():
            raise RuntimeError("close failed")

        service.agent_factory.aclose = fail_to_close
        with pytest.raises(RuntimeError, match="close failed"):
            await service.stop(timeout=1)
        return service

    service = asyncio.run(run())

    assert service.is_running is False
    assert service.session_id is None
    assert service.persistence.is_running is False
