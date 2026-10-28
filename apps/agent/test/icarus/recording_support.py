"""In-memory implementation of the recording port for component assertions."""

from icarus.core.hooks.hook_context import get_hook_context
from icarus.core.hooks.hook_event import HookEvent


class CaptureRecords:
    def __init__(self):
        self.events = []

    def record(self, name, phase, *, data):
        context = get_hook_context()
        self.events.append(HookEvent.create(
            name=name, phase=phase, run_id=context.run_id if context else None,
            context=context.data if context else None, data=data,
        ))
