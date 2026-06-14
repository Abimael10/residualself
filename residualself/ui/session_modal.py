"""Session modal: commit to M minutes or N items.

Dismisses with ``(kind, amount)`` where kind is "minutes" | "items", or
``None`` if cancelled.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

SessionResult = tuple[str, int] | None

_BUTTON_COMMITMENT: dict[str, tuple[str, int]] = {
    "sess-25m": ("minutes", 25),
    "sess-50m": ("minutes", 50),
    "sess-3i": ("items", 3),
    "sess-5i": ("items", 5),
}

_CSS = """
SessionModal { align: center middle; }
#sess-dialog {
    width: 60; height: auto; padding: 1 2;
    border: thick $primary; background: $surface;
}
#sess-buttons { height: auto; margin-top: 1; }
#sess-buttons Button { margin-right: 1; }
"""


class SessionModal(ModalScreen[SessionResult]):
    """Pick a focus commitment."""

    CSS = _CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="sess-dialog"):
            yield Label("Start a focus session — commit to:")
            with Horizontal(id="sess-buttons"):
                yield Button("25 min", id="sess-25m")
                yield Button("50 min", id="sess-50m")
                yield Button("3 items", id="sess-3i")
                yield Button("5 items", id="sess-5i")
                yield Button("Cancel", id="sess-cancel", variant="error")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        commitment = _BUTTON_COMMITMENT.get(event.button.id or "")
        self.dismiss(commitment)
