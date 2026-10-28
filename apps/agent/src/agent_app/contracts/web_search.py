"""Search progress values shared by the search and public-update plugins."""

from dataclasses import dataclass
from icarus.core.events import Event


@dataclass(frozen=True, kw_only=True)
class WebSearchUpdatedEvent(Event):
    status: str
    total: int = 0
    queries: tuple[str, ...] = ()
    sources: tuple[dict, ...] = ()
    pages: tuple[dict, ...] = ()
    browsing: bool = False
    notice: str = ""


EVENTS = {"web_search.updated": WebSearchUpdatedEvent}
CAPABILITIES = ()
