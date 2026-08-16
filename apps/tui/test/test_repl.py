import asyncio
from collections import deque
from io import StringIO

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.plugins import (
    InputAccepted,
    InputFinishedEvent,
)
from apps.agent.src.model_provider.types import Message, TextPart
from apps.tui.main import run_repl


class ServiceStub:
    def __init__(self, task_events) -> None:
        self.task_events = deque(task_events)
        self.submissions = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def submit(self, prompt, history_messages, input_images=None):
        task_id = f"task-{len(self.submissions) + 1}"
        self.submissions.append(
            {
                "task_id": task_id,
                "prompt": prompt,
                "history_messages": list(history_messages),
                "input_images": input_images,
            }
        )
        return InputAccepted(task_id=task_id, queue_position=0)

    async def next_event(self):
        return self.task_events.popleft()

    async def stop(self, timeout=30) -> None:
        self.stopped = True


def make_completed_events(task_id: str, text: str):
    message = Message("assistant", [TextPart(text)])
    return [
        (
            "agent",
            AgentTextDeltaEvent(
                correlation_id=task_id,
                step=1,
                text=text,
            ),
        ),
        (
            "agent",
            AgentCompletedEvent(
                correlation_id=task_id,
                step=1,
                response=AgentResponse(
                    message=message,
                    finish_reason="stop",
                    steps=1,
                ),
            ),
        ),
        (
            "user-input",
            InputFinishedEvent(
                correlation_id=task_id,
                task_id=task_id,
                status="completed",
            ),
        ),
    ]


def input_reader(values: list[object]):
    pending = deque(values)

    def read(prompt: str) -> str:
        value = pending.popleft()
        if isinstance(value, BaseException):
            raise value
        return value

    return read


def test_run_repl_串行执行多轮并将成功历史传入下一轮():
    unrelated = AgentTextDeltaEvent(
        correlation_id="other-task",
        step=1,
        text="不应展示",
    )
    service = ServiceStub(
        [
            ("agent", unrelated),
            *make_completed_events("task-1", "first-answer"),
            *make_completed_events("task-2", "second-answer"),
        ]
    )
    output = StringIO()

    result = asyncio.run(
        run_repl(
            service,
            input_reader=input_reader(["first", "second", "quit"]),
            output=output,
        )
    )

    assert result == 0
    assert service.started is True
    assert service.stopped is True
    assert [item["prompt"] for item in service.submissions] == ["first", "second"]
    assert service.submissions[0]["history_messages"] == []
    second_history = service.submissions[1]["history_messages"]
    assert [message.role for message in second_history] == ["user", "assistant"]
    assert second_history[0].content == [TextPart("first")]
    assert second_history[1].content == [TextPart("first-answer")]
    assert output.getvalue() == "first-answer\nsecond-answer\n"
    assert "不应展示" not in output.getvalue()


def test_run_repl_失败任务不污染下一轮历史():
    service = ServiceStub(
        [
            (
                "agent",
                AgentErrorEvent(
                    correlation_id="task-1",
                    step=1,
                    error_type="RuntimeError",
                    error_message="failed",
                ),
            ),
            (
                "user-input",
                InputFinishedEvent(
                    correlation_id="task-1",
                    task_id="task-1",
                    status="failed",
                ),
            ),
            *make_completed_events("task-2", "recovered"),
        ]
    )
    output = StringIO()

    asyncio.run(
        run_repl(
            service,
            input_reader=input_reader(["first", "second", "exit"]),
            output=output,
        )
    )

    assert service.submissions[1]["history_messages"] == []
    assert output.getvalue() == (
        "[error] RuntimeError: failed\n"
        "[task] failed\n"
        "recovered\n"
    )


def test_run_repl_忽略空输入并在EOF时关闭服务():
    service = ServiceStub([])
    output = StringIO()

    result = asyncio.run(
        run_repl(
            service,
            input_reader=input_reader(["   ", EOFError()]),
            output=output,
        )
    )

    assert result == 0
    assert service.submissions == []
    assert service.stopped is True
    assert output.getvalue() == ""


def test_run_repl_输入阶段收到KeyboardInterrupt时换行并关闭服务():
    service = ServiceStub([])
    output = StringIO()

    result = asyncio.run(
        run_repl(
            service,
            input_reader=input_reader([KeyboardInterrupt()]),
            output=output,
        )
    )

    assert result == 0
    assert service.stopped is True
    assert output.getvalue() == "\n"


def test_run_repl_任务异常时仍关闭服务():
    class FailingService(ServiceStub):
        async def next_event(self):
            raise RuntimeError("stream failed")

    service = FailingService([])

    async def run():
        try:
            await run_repl(
                service,
                input_reader=input_reader(["hello"]),
                output=StringIO(),
            )
        except RuntimeError as error:
            return error
        raise AssertionError("expected RuntimeError")

    error = asyncio.run(run())

    assert str(error) == "stream failed"
    assert service.stopped is True
