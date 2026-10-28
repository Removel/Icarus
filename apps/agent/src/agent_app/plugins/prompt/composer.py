"""Deterministic whole-section prompt composition and budget validation."""

from collections.abc import Callable, Mapping
import logging

from agent_app.contracts.prompt import PromptError, PromptSection
from icarus.core.contracts.context import ContextBlock
from icarus.core.tools.result_budget import conservative_token_count

logger = logging.getLogger(__name__)


def validate_instructions(instructions: str | None, *, max_tokens: int) -> None:
    if instructions is None:
        return
    if not isinstance(instructions, str):
        raise PromptError("prompt_invalid", "Session instructions must be a string")
    try:
        size = conservative_token_count(instructions)
    except UnicodeError:
        raise PromptError("prompt_invalid", "Session instructions must be valid UTF-8") from None
    if size > max_tokens:
        raise PromptError("prompt_too_large", "Session instructions exceed the prompt section budget")


def prompt_context_block(content: str) -> ContextBlock:
    return ContextBlock(source_plugin_id="prompt", context_type="prompt", content=content)


def compose_prompt(
    sections_by_source: Mapping[str, tuple[PromptSection, ...]],
    *,
    layers: tuple[str, ...],
    required_sources: frozenset[str],
    max_tokens: int,
    max_section_tokens: int,
    cost: Callable[[str], int] = conservative_token_count,
) -> str:
    entries: list[tuple[str, PromptSection]] = []
    for source, sections in sorted(sections_by_source.items()):
        try:
            ids = set()
            for section in sections:
                if not isinstance(section, PromptSection) or section.layer not in layers or section.section_id in ids:
                    raise PromptError("prompt_invalid", "Invalid or duplicate prompt section")
                ids.add(section.section_id)
                try:
                    validate_instructions(section.content, max_tokens=max_section_tokens)
                except PromptError as error:
                    if source in required_sources:
                        raise
                    logger.warning("Optional prompt section discarded: source=%s section=%s code=%s", source, section.section_id, error.code)
                    continue
                if section.content:
                    entries.append((source, section))
        except PromptError:
            if source in required_sources:
                raise
            entries = [entry for entry in entries if entry[0] != source]
            logger.warning("Optional prompt contribution discarded: source=%s", source)
    entries.sort(key=lambda entry: (layers.index(entry[1].layer), entry[0], entry[1].section_id))

    def render() -> str:
        return "\n\n".join(f"## {section.layer}/{source}/{section.section_id}\n{section.content}" for source, section in entries)

    result = render()
    for index in range(len(entries) - 1, -1, -1):
        if cost(result) <= max_tokens:
            return result
        if entries[index][0] not in required_sources:
            source, section = entries.pop(index)
            logger.warning("Prompt section omitted for total budget: source=%s section=%s", source, section.section_id)
            result = render()
    if cost(result) > max_tokens:
        raise PromptError("prompt_too_large", "Required prompt content exceeds the total budget")
    return result
