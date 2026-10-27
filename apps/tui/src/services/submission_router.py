"""Composer submission and local-command routing without Textual."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.domain.commands import CommandDefinition, CommandRegistry
from apps.tui.src.domain.submission import PendingMessage


@dataclass(frozen=True)
class Enqueue:
    submission: PendingMessage


@dataclass(frozen=True)
class RunCommand:
    definition: CommandDefinition


@dataclass(frozen=True)
class Reject:
    reason: str
    title: str


@dataclass(frozen=True)
class Unavailable:
    reason: str
    title: str


SubmissionPlan: TypeAlias = Enqueue | RunCommand | Reject | Unavailable


class SubmissionRouter:
    """Turn a composer submission into one screen action."""

    def __init__(self, commands: CommandRegistry) -> None:
        self._commands = commands

    def route(
        self,
        submission: PendingMessage,
        *,
        phase: RuntimePhase,
        can_run_session_command: bool,
    ) -> SubmissionPlan:
        local_text = submission.text
        for image in submission.images:
            local_text = local_text.replace(image.marker, "")

        invocation = self._commands.parse(local_text)
        if invocation is not None:
            definition = self._commands.resolve(invocation.name)
            if definition is None:
                return Reject(
                    f"Unknown command: {invocation.name}",
                    "Unknown command",
                )
            if invocation.arguments and not definition.accepts_arguments:
                return Reject(
                    f"{definition.name} does not accept arguments.",
                    "Command not run",
                )
            if submission.images and not definition.allow_attachments:
                return Reject(
                    "Remove image attachments before running this command.",
                    "Command not run",
                )
            if definition.requires_idle and not can_run_session_command:
                return Unavailable(
                    "Session commands are only available while idle.",
                    "Session command not run",
                )
            return RunCommand(definition)

        if phase == RuntimePhase.SWITCHING:
            return Unavailable(
                "Session commands are only available while idle.",
                "Session command not run",
            )
        return Enqueue(submission)
