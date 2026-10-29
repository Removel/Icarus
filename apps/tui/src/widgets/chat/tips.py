"""Single-line rotating keyboard hints owned by the input chrome."""

from textual.widgets import Static


class ComposerTip(Static):
    TIPS = (
        "Enter submit · / commands",
        "Shift+Enter / Ctrl+J newline",
        "Ctrl+V paste system text or image",
        "Terminal Paste inserts text once",
        "Ctrl+C copy selection / actions",
        "Ctrl+X cut input selection",
        "Ctrl+Shift+S select chat",
        "Shift+arrows extend · Esc cancel",
        "Ctrl+Shift+O open message links",
        "Ctrl+End jump to latest output",
    )

    def __init__(self, *, id="composer-tip"):
        super().__init__(markup=False, id=id)
        self._tip_index = 0

    def on_mount(self):
        self.update(self.TIPS[self._tip_index])
        self.set_interval(8, self.advance_tip)

    def advance_tip(self):
        if self.is_on_screen:
            self._tip_index = (self._tip_index + 1) % len(self.TIPS)
            self.update(self.TIPS[self._tip_index])
