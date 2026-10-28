"""Rendered selection endpoints and only the selected range's text snapshots.

Offsets refer to rendered Content, never terminal cells or Markdown source.
ConversationView supplies semantic body keys in reading order; retained bodies
are the selected range, not a duplicate transcript store.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectedBody:
    text: str
    start: int
    end: int

    @property
    def selected(self):
        return self.text[self.start:self.end]


def offset_index(text, offset, *, end=False):
    if offset is None:
        return len(text) if end else 0
    lines = text.splitlines(keepends=True)
    if offset.y >= len(lines):
        return len(text)
    return sum(len(line) for line in lines[:offset.y]) + min(offset.x, len(lines[offset.y].rstrip("\r\n")))


class ConversationSelection:
    def __init__(self):
        self.clear()

    @property
    def active(self):
        return any(body.start != body.end for body in self.fragments.values())

    def clear(self):
        self.fragments = {}
        self._native_signature = None
        self.keyboard = False
        self.anchor = None
        self.focus = None
        self.dragging = False
        self.order = {}

    def _remember_order(self, bodies):
        counts = {}
        unit_order = {}
        units_per_turn = {}
        for key, _, _ in bodies:
            group = key[:2]
            if group not in unit_order:
                rank = units_per_turn.get(key[0], 0)
                units_per_turn[key[0]] = rank + 1
                unit_order[group] = rank
            ordinal = counts.get(group, 0)
            counts[group] = ordinal + 1
            self.order[key] = (key[0], unit_order[group], ordinal)

    def capture(self, bodies, selections):
        self._remember_order(bodies)
        signature = tuple((id(widget), selection) for widget, selection in selections.items())
        if not signature or signature == self._native_signature:
            return
        captured = {}
        for key, widget, text in bodies:
            if (selection := selections.get(widget)) is not None:
                start = offset_index(text, selection.start)
                end = offset_index(text, selection.end, end=True)
                if start != end:
                    captured[key] = SelectedBody(text, start, end)
        if captured:
            self.fragments = captured
            self._native_signature = signature

    def extend(self, bodies, key, index):
        self._remember_order(bodies)
        self.focus = (key, index)
        if self.anchor is None:
            self.anchor = self.focus
        (start_key, start), (end_key, end) = sorted((self.anchor, self.focus), key=lambda endpoint: (self.order[endpoint[0]], endpoint[1]))
        texts = {body_key: value.text for body_key, value in self.fragments.items()}
        texts.update({body_key: text for body_key, _, text in bodies})
        fragments = {}
        for body_key, text in texts.items():
            if self.order[start_key] <= self.order[body_key] <= self.order[end_key]:
                left = start if body_key == start_key else 0
                right = end if body_key == end_key else len(text)
                fragments[body_key] = SelectedBody(text, left, right)
        self.fragments = fragments

    def text(self):
        messages = {}
        for key, body in sorted(self.fragments.items(), key=lambda item: self.order[item[0]]):
            if body.selected:
                messages.setdefault(key[:2], []).append(body.selected)
        return "\n\n".join("\n".join(parts) for parts in messages.values()) or None
