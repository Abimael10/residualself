"""Park modal: capture a re-entry note + an optional remind-in choice.

Dismisses with ``(note, choice)`` where choice is one of "1h" | "4h" |
"tomorrow" | "none", or ``None`` if cancelled.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

ParkResult = tuple[str, str] | None

_BUTTON_CHOICE = {
    "park-1h": "1h",
    "park-4h": "4h",
    "park-tomorrow": "tomorrow",
    "park-none": "none",
}

_CSS = """
ParkModal { align: center middle; }
#park-dialog {
    width: 64; height: auto; padding: 1 2;
    border: thick $primary; background: $surface;
}
#park-buttons { height: auto; margin-top: 1; }
#park-buttons Button { margin-right: 1; }
"""


class ParkModal(ModalScreen[ParkResult]):
    """Ask for a re-entry note and when to resurface the item."""

    CSS = _CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="park-dialog"):
            yield Label("Park this item")
            yield Input(
                placeholder="Re-entry note: what were you doing / the next step?",
                id="park-note",
            )
            yield Label("Resurface in:")
            with Horizontal(id="park-buttons"):
                yield Button("1h", id="park-1h")
                yield Button("4h", id="park-4h")
                yield Button("Tomorrow", id="park-tomorrow")
                yield Button("No reminder", id="park-none")
                yield Button("Cancel", id="park-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#park-note", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id not in _BUTTON_CHOICE:
            self.dismiss(None)  # cancel (or any non-choice button)
            return
        note = self.query_one("#park-note", Input).value.strip()
        self.dismiss((note, _BUTTON_CHOICE[button_id]))
