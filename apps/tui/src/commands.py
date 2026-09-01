"""Pure parsing for commands handled entirely by the TUI."""

from typing import Literal, TypeAlias


LocalCommand: TypeAlias = Literal["resume", "clear"]


def parse_local_command(text: str) -> LocalCommand | None:
    """Return an exact, case-insensitive local Session command."""

    command = text.strip().lower()
    if command == "/resume":
        return "resume"
    if command == "/clear":
        return "clear"
    return None
