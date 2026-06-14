"""Focus-session countdown / progress display."""

from __future__ import annotations

from datetime import datetime

from textual.widgets import Static

from ..session import FocusSession


class SessionBar(Static):
    """Shows time remaining (or item progress) while a session is active."""

    def update_session(self, focus: FocusSession | None, current: datetime) -> None:
        if focus is None:
            self.display = False
            self.update("")
            return
        self.display = True
        self.update(self._format(focus, current))

    @staticmethod
    def _format(focus: FocusSession, current: datetime) -> str:
        if focus.duration is not None:
            remaining = max(focus.remaining_seconds(current) or 0, 0)
            minutes, seconds = divmod(remaining, 60)
            base = f"⏱  {minutes:02d}:{seconds:02d} left"
        else:
            base = "🎯 focus"
        progress = f"{focus.items_done}"
        if focus.target_items:
            progress += f"/{focus.target_items}"
        return f"[b]{base}[/b]  ·  {progress} done"
