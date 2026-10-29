"""Pure parsing and registration for commands handled by the TUI."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandInvocation:
    name: str
    arguments: str = ""


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    summary: str
    handler_name: str
    accepts_arguments: bool = False
    allow_attachments: bool = False
    requires_idle: bool = False
    usage: str = ""


class CommandRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, CommandDefinition] = {}

    @property
    def definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(self._definitions.values())

    def register(self, definition: CommandDefinition) -> None:
        normalized = definition.name.strip().lower()
        if (
            not normalized.startswith("/")
            or len(normalized) == 1
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("Command name must be one slash-prefixed token")
        if not definition.handler_name.strip():
            raise ValueError("Command handler_name cannot be empty")
        if normalized in self._definitions:
            raise ValueError(f"Command is already registered: {normalized}")
        self._definitions[normalized] = CommandDefinition(
            name=normalized,
            summary=definition.summary,
            handler_name=definition.handler_name,
            accepts_arguments=definition.accepts_arguments,
            allow_attachments=definition.allow_attachments,
            requires_idle=definition.requires_idle,
            usage=definition.usage,
        )

    def match(self, query: str):
        query = query.lower().lstrip("/")

        def matches(name):
            chars = iter(name.lower().lstrip("/"))
            return all(any(candidate == ch for candidate in chars) for ch in query)

        return tuple(item for item in self.definitions if matches(item.name))

    @staticmethod
    def parse(text: str) -> CommandInvocation | None:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        match = re.fullmatch(r"(\S+)(?:\s+(.*))?", stripped, re.DOTALL)
        if match is None:
            return CommandInvocation(stripped.lower())
        return CommandInvocation(
            name=match.group(1).lower(),
            arguments=match.group(2) or "",
        )

    def resolve(self, name: str) -> CommandDefinition | None:
        return self._definitions.get(name.strip().lower())


def create_default_command_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(
        CommandDefinition(
            "/clear",
            "Start a new conversation",
            "clear_session",
            requires_idle=True,
        )
    )
    registry.register(
        CommandDefinition(
            "/resume",
            "Resume a conversation",
            "resume_session",
            requires_idle=True,
        )
    )
    registry.register(
        CommandDefinition(
            "/exit",
            "Exit Icarus",
            "exit_app",
        )
    )
    return registry


from typing import TypeAlias

from apps.tui.src.domain.chat_state import RuntimePhase
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


SubmissionDecision = SubmissionPlan


def route_submission(submission, commands, *, phase, can_run_session_command):
    local_text = submission.text
    for image in submission.images:
        local_text = local_text.replace(image.marker, "")
    invocation = commands.parse(local_text)
    if invocation is not None:
        definition = commands.resolve(invocation.name)
        if definition is None:
            return Reject(f"Unknown command: {invocation.name}", "Unknown command")
        if invocation.arguments and not definition.accepts_arguments:
            return Reject(
                f"{definition.name} does not accept arguments.", "Command not run"
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
            "Session commands are only available while idle.", "Session command not run"
        )
    return Enqueue(submission)
