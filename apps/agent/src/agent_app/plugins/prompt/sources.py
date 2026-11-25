"""Bounded workspace instruction discovery, without recursion or imports."""

import logging
import os
from pathlib import Path
import stat

from agent_app.contracts.prompt import PromptError, PromptSection
from agent_app.plugins.prompt.composer import validate_instructions
from icarus.core.tools.result_budget import conservative_token_count


def load_workspace_section(workspace_path: Path, *, max_tokens: int, logger: logging.Logger) -> PromptSection | None:
    root = workspace_path.resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        candidate = root / name
        if not os.path.lexists(candidate):
            continue
        reason = "invalid_path"
        byte_count = 0
        units = None
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not stat.S_ISREG(resolved.stat().st_mode):
                raise ValueError("Not a regular file")
            reason = "unreadable"
            with resolved.open("rb") as stream:
                raw = stream.read(3 * max_tokens + 1)
            byte_count = len(raw)
            if byte_count > 3 * max_tokens:
                reason = "byte_limit"
                raise ValueError("Workspace instructions exceed byte limit")
            reason = "invalid_utf8"
            content = raw.decode("utf-8")
            units = conservative_token_count(content)
            reason = "section_budget"
            validate_instructions(content, max_tokens=max_tokens)
            if content:
                return PromptSection("workspace", "workspace", content)
            reason = "empty"
        except (OSError, ValueError, UnicodeError, PromptError):
            pass
        logger.warning("Workspace prompt omitted: file=%s reason=%s bytes_read=%s units=%s max_tokens=%s",
                       name, reason, byte_count, units, max_tokens)
        return None
    return None
