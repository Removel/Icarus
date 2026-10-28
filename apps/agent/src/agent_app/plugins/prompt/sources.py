"""Bounded workspace instruction discovery, without recursion or imports."""

import logging
import os
from pathlib import Path
import stat

from agent_app.contracts.prompt import PromptError, PromptSection
from agent_app.plugins.prompt.composer import validate_instructions


def load_workspace_section(workspace_path: Path, *, max_tokens: int, logger: logging.Logger) -> PromptSection | None:
    root = workspace_path.resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        candidate = root / name
        if not os.path.lexists(candidate):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not stat.S_ISREG(resolved.stat().st_mode):
                raise ValueError("Not a regular file")
            with resolved.open("rb") as stream:
                content = stream.read(3 * max_tokens + 1).decode("utf-8")
            validate_instructions(content, max_tokens=max_tokens)
            return PromptSection("workspace", "workspace", content) if content else None
        except (OSError, ValueError, UnicodeError, PromptError):
            logger.warning("Workspace prompt omitted: file=%s", name)
            return None
    return None
