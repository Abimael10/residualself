"""Focus-session (Pomodoro) state.

A session is a commitment to either M minutes or N items. It is pure state +
small queries; the UI drives the countdown and records completed items.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class FocusSession:
    """A time- or count-bounded focus commitment."""

    started_at: datetime
    duration: timedelta | None = None
    target_items: int | None = None
    items_done: int = 0
    ended_at: datetime | None = None

    def remaining_seconds(self, current: datetime) -> int | None:
        """Seconds left for a time-based session, else None."""
        if self.duration is None:
            return None
        end = self.started_at + self.duration
        return int((end - current).total_seconds())

    def is_complete(self, current: datetime) -> bool:
        if self.duration is not None and current >= self.started_at + self.duration:
            return True
        if self.target_items is not None and self.items_done >= self.target_items:
            return True
        return False

    def record_done(self) -> None:
        self.items_done += 1

    def summary(self) -> str:
        return f"{self.items_done} item(s) done"


def start_session(
    *,
    minutes: int | None = None,
    items: int | None = None,
    current: datetime | None = None,
) -> FocusSession:
    """Build a session, validating that a commitment was given (Rule 5)."""
    if not minutes and not items:
        raise ValueError("a focus session needs minutes or an item target")
    return FocusSession(
        started_at=current or now(),
        duration=timedelta(minutes=minutes) if minutes else None,
        target_items=items,
    )
