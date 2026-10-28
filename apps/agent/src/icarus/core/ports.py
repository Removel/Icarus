"""Host-only plugin ports; plugins never receive a sibling implementation."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from icarus.core.contracts.input import InputAccepted
from icarus.core.models.messages import ImagePart
from icarus.core.run_control import TaskOperationResult, TaskCancelRequestedEvent, TaskSteerRequestedEvent, TaskContextInputEvent


@runtime_checkable
class InputSubmission(Protocol):
    @property
    def queued_count(self) -> int: ...
    async def submit(self, prompt: str, input_images: list[ImagePart | str | Path] | None = None,
                     *, task_id: str | None = None) -> InputAccepted: ...


@runtime_checkable
class TaskControl(Protocol):
    def handle_task_operation(self, source_id: str,
        event: TaskCancelRequestedEvent | TaskSteerRequestedEvent | TaskContextInputEvent,
    ) -> TaskOperationResult: ...


class ReleaseHandle(Protocol):
    def release(self) -> None: ...
