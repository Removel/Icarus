"""Minimal source-facing fact interface; no runtime plugin discovery."""
from collections.abc import Sequence
from typing import Protocol

from ..common.schemas.observation import SelectedItem


class SourceSurface(Protocol):
    source: str
    known_facts: frozenset[str]

    def build_facts(self, items: Sequence[SelectedItem], *, permission_level: int) -> dict: ...
