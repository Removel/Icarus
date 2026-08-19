"""Read-only projection of local messages waiting for Runtime submit."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, Static


def message_preview(message: str, *, limit: int = 120) -> str:
    compact = " ↵ ".join(part.strip() for part in message.splitlines())
    compact = " ".join(compact.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


class QueuePanel(VerticalScroll):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="is-empty")
        self._items: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield Label("Queued 0", id="queue-title")
        yield VerticalScroll(id="queue-items")

    @property
    def items(self) -> tuple[str, ...]:
        return self._items

    async def show_pending(self, messages: tuple[str, ...]) -> None:
        self._items = messages
        self.set_class(not messages, "is-empty")
        self.query_one("#queue-title", Label).update(
            f"Queued {len(messages)}"
        )
        container = self.query_one("#queue-items", VerticalScroll)
        await container.remove_children()
        if messages:
            await container.mount(
                *(
                    Static(
                        f"{index}. {message_preview(message)}",
                        markup=False,
                        classes="queue-item",
                    )
                    for index, message in enumerate(messages, start=1)
                )
            )
