"""TUI-only citation rendering; raw Markdown remains available for replay."""

import re

from markdown_it import MarkdownIt

_REFERENCE = re.compile(r"\[(\d+)\]")
_MATH = re.compile(r"(?<!\\)(?:\\\\)*(\${1,2})(?!\$)(.+?)(?<!\\)\1(?!\$)", re.DOTALL)


def citation_parser(indices: set[int], *, streaming: bool) -> MarkdownIt:
    parser = MarkdownIt("gfm-like")
    prefixes = {str(index)[:length] for index in indices for length in range(len(str(index)) + 1)}
    cached_source = None
    math_ranges: list[tuple[int, int]] = []

    def citation(state, silent):
        nonlocal cached_source, math_ranges
        if state.src[state.pos:state.pos + 1] != "[" or state.linkLevel:
            return False
        # Textual has no math renderer; preserve literal dollar-delimited math.
        if cached_source != state.src:
            cached_source = state.src
            math_ranges = [(match.start(), match.end()) for match in _MATH.finditer(state.src)]
        if any(start <= state.pos < end for start, end in math_ranges):
            return False
        match = _REFERENCE.match(state.src, state.pos, state.posMax)
        if match and len(match[1]) <= 16 and int(match[1]) in indices:
            if not silent:
                state.pos = match.end()
            return True
        tail = state.src[state.pos + 1:state.posMax]
        if streaming and indices and tail in prefixes:
            if not silent:
                state.pos = state.posMax
            return True
        return False

    # Let Markdown links resolve first; escape/code rules already consumed their
    # content. Plain [n] falls through here without changing code or link labels.
    parser.inline.ruler.after("link", "web_citation", citation)
    return parser
