"""Session-scoped background process Plugin."""

from agent_app.plugins.process.config import ProcessConfig
from agent_app.plugins.process.cursor import (
    CursorError,
    ProcessCursorCodec,
)
from agent_app.plugins.process.manager import (
    ProcessManager,
    ProcessOperationError,
)
from icarus.core.contracts.process import (
    ProcessUpdatedEvent,
)
from icarus.core.contracts.process import ProcessSnapshot
from agent_app.plugins.process.models import ProcessStatus
from agent_app.plugins.process.plugin import ProcessPlugin
from agent_app.plugins.process.tools import (
    BackgroundProcessTool,
)

__all__ = [
    "CursorError",
    "ProcessConfig",
    "ProcessCursorCodec",
    "ProcessManager",
    "ProcessOperationError",
    "ProcessPlugin",
    "ProcessSnapshot",
    "ProcessStatus",
    "ProcessUpdatedEvent",
    "BackgroundProcessTool",
]
