from dataclasses import asdict

from icarus.core.events import TaskErrorEvent
from icarus.core.models.messages import Message, TextPart, Usage


def test_task_error_event可序列化且不携带异常对象():
    event = TaskErrorEvent(
        task_id="task-1",
        fatal=True,
        code="max_steps_exceeded",
        error_type="MaxStepsExceededError",
        error_message="stopped before step 257",
        step=256,
        run_id="run-1",
        task_messages=(Message("user", [TextPart("work")]),),
        last_usage=Usage(100, 20),
    )

    value = asdict(event)

    assert value["fatal"] is True
    assert value["code"] == "max_steps_exceeded"
    assert value["run_id"] == "run-1"
    assert "exception" not in value
    assert "traceback" not in value
