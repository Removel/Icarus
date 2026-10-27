"""Pure parsing and registration for commands handled by the TUI."""

from __future__ import annotations

from dataclasses import dataclass
import re


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
        )

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
