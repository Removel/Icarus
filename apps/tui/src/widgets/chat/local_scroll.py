"""Scrollable local text region with wheel handoff at its edges."""

from textual.containers import VerticalScroll


class LocalScroll(VerticalScroll):
    DEFAULT_CSS = "LocalScroll { height: auto; scrollbar-size-vertical: 1; }"

    def restore_offset(self, x, y):
        self._pending_read_offset = (x, y)
        self.call_after_refresh(self._restore_pending_offset)

    def _size_updated(self, size, virtual_size, container_size, layout=True):
        # max_scroll_y also depends on the non-reactive container size. A
        # virtual_size watcher alone can run before the final layout geometry.
        changed = super()._size_updated(size, virtual_size, container_size, layout)
        self._restore_pending_offset()
        return changed

    def watch_virtual_size(self):
        if getattr(self, "_pending_read_offset", None) is not None:
            self.call_after_refresh(self._restore_pending_offset)

    def _restore_pending_offset(self):
        offset = getattr(self, "_pending_read_offset", None)
        if (
            offset is not None
            and self.virtual_size.height > 0
            and self.max_scroll_y >= offset[1]
        ):
            self._pending_read_offset = None
            self.scroll_to(x=offset[0], y=offset[1], animate=False, immediate=True)

    def _on_mouse_scroll_up(self, event):
        if self.scroll_y > 0:
            super()._on_mouse_scroll_up(event)
            event.stop()

    def _on_mouse_scroll_down(self, event):
        if self.scroll_y < self.max_scroll_y:
            super()._on_mouse_scroll_down(event)
            event.stop()
