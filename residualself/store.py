"""SQLite persistence: parked items, re-entry notes, poll cache.

Park/snooze is local — GitHub has no native snooze API. A parked item is hidden
until its ``parked_until`` passes, or until new activity arrives (mirroring
Octobox's "archived returns" idea), at which point its card leads with the
re-entry note so coming back is not a cold start.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import WorkItem
from .session import FocusSession

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parked (
    item_id      TEXT PRIMARY KEY,
    parked_until TEXT,
    reentry_note TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS poll_state (
    key                   TEXT PRIMARY KEY,
    etag_or_last_modified TEXT,
    last_poll_at          TEXT,
    poll_interval         INTEGER
);
CREATE TABLE IF NOT EXISTS session_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    items_done INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class ParkRecord:
    item_id: str
    parked_until: str | None
    reentry_note: str | None
    created_at: str


def default_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "residualself" / "residualself.db"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and initialize) the SQLite database."""
    target = str(db_path) if db_path is not None else str(default_db_path())
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def compute_until(choice: str, now: datetime) -> datetime | None:
    """Translate a remind-in choice into an absolute time (or None = indefinite)."""
    deltas = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "tomorrow": timedelta(days=1)}
    if choice in deltas:
        return now + deltas[choice]
    return None  # "none" or unknown -> park until new activity / manual unpark.


def park_item(
    conn: sqlite3.Connection,
    item_id: str,
    reentry_note: str | None,
    parked_until: str | None,
    *,
    now: datetime | None = None,
) -> None:
    """Persist a parked item (replacing any prior park for the same id)."""
    if not item_id:
        raise ValueError("park_item requires an item_id")
    created_at = to_iso(now if now is not None else now_utc())
    conn.execute(
        "INSERT OR REPLACE INTO parked(item_id, parked_until, reentry_note, created_at) "
        "VALUES (?, ?, ?, ?)",
        (item_id, parked_until, reentry_note, created_at),
    )
    conn.commit()


def unpark_item(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute("DELETE FROM parked WHERE item_id = ?", (item_id,))
    conn.commit()


def log_session(conn: sqlite3.Connection, session: FocusSession) -> None:
    """Record a finished focus session (optional history)."""
    ended = to_iso(session.ended_at) if session.ended_at is not None else None
    conn.execute(
        "INSERT INTO session_log(started_at, ended_at, items_done) VALUES (?, ?, ?)",
        (to_iso(session.started_at), ended, session.items_done),
    )
    conn.commit()


def get_poll_state(conn: sqlite3.Connection, key: str) -> tuple[str | None, int | None]:
    """Return (etag_or_last_modified, poll_interval) for a poll key."""
    row = conn.execute(
        "SELECT etag_or_last_modified, poll_interval FROM poll_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None, None
    return row["etag_or_last_modified"], row["poll_interval"]


def set_poll_state(
    conn: sqlite3.Connection,
    key: str,
    etag_or_last_modified: str | None,
    poll_interval: int | None,
) -> None:
    """Persist the conditional-request cursor and poll interval for a key."""
    if not key:
        raise ValueError("set_poll_state requires a key")
    conn.execute(
        "INSERT OR REPLACE INTO poll_state(key, etag_or_last_modified, last_poll_at, "
        "poll_interval) VALUES (?, ?, ?, ?)",
        (key, etag_or_last_modified, to_iso(now_utc()), poll_interval),
    )
    conn.commit()


def get_parked(conn: sqlite3.Connection) -> dict[str, ParkRecord]:
    rows = conn.execute(
        "SELECT item_id, parked_until, reentry_note, created_at FROM parked"
    ).fetchall()
    return {
        row["item_id"]: ParkRecord(
            item_id=row["item_id"],
            parked_until=row["parked_until"],
            reentry_note=row["reentry_note"],
            created_at=row["created_at"],
        )
        for row in rows
    }


def _is_due(record: ParkRecord, now: datetime) -> bool:
    until = _parse_iso(record.parked_until)
    return until is not None and until <= now


def _has_new_activity(item: WorkItem, record: ParkRecord) -> bool:
    activity = _parse_iso(item.last_activity)
    created = _parse_iso(record.created_at)
    return activity is not None and created is not None and activity > created


def apply_parking(
    conn: sqlite3.Connection, items: list[WorkItem], now: datetime
) -> list[WorkItem]:
    """Drop still-parked items; surface due/reactivated ones with their note first."""
    records = get_parked(conn)
    visible: list[WorkItem] = []
    for item in items:  # Rule 2: bounded by the deduped item list.
        record = records.get(item.id)
        if record is None:
            visible.append(item)
            continue
        if _is_due(record, now) or _has_new_activity(item, record):
            item.reentry_note = record.reentry_note
            visible.append(item)
        # else: still parked (future deadline or indefinite) -> hidden.
    return visible
