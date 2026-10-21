"""Task-local aggregation state, owned by PromptPlugin."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from agent_app.contracts.prompt import PromptContributionEvent


@dataclass
class PromptTask:
    contributions: dict[str, PromptContributionEvent] = field(default_factory=dict)
    received_input: bool = False
    completed: bool = False
    succeeded: bool = False
    cutoff: datetime | None = None
    received_at: dict[str, datetime] = field(default_factory=dict)
    initial_content: str = ""
    injected_content: str = ""
    live_tokens: int = 0
    deadline: asyncio.Task[None] | None = None
