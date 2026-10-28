"""Mark prepared tool calls explicitly so group scheduling never repeats hooks."""

from dataclasses import dataclass
from icarus.core.models.messages import ToolCall


@dataclass(frozen=True)
class PreparedToolCall(ToolCall):
    pass
