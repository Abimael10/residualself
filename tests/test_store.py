"""Phase 3 tests: park persistence + resurface timing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from residualself import store
from residualself.models import ItemKind, WorkItem


def _item(number: int, *, last_activity: str | None = None) -> WorkItem:
    return WorkItem(
        id=f"octo/widgets#{number}",
        kind=ItemKind.REVIEW_REQUEST,
        title=f"PR {number}",
        repo="octo/widgets",
        number=number,
        url=f"https://github.com/octo/widgets/pull/{number}",
        last_activity=last_activity,
    )


@pytest.fixture
def conn():
    connection = store.connect(":memory:")
    yield connection
    connection.close()


def test_compute_until_math() -> None:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    assert store.compute_until("1h", now) == now + timedelta(hours=1)
    assert store.compute_until("4h", now) == now + timedelta(hours=4)
    assert store.compute_until("tomorrow", now) == now + timedelta(days=1)
    assert store.compute_until("none", now) is None


def test_park_roundtrip(conn) -> None:
    store.park_item(conn, "octo/widgets#1", "pick up here", None)
    parked = store.get_parked(conn)
    assert parked["octo/widgets#1"].reentry_note == "pick up here"


def test_poll_state_roundtrip(conn) -> None:
    store.set_poll_state(conn, "notifications", "Wed, 10 Jun 2026 10:00:00 GMT", 90)
    last_modified, interval = store.get_poll_state(conn, "notifications")
    assert last_modified == "Wed, 10 Jun 2026 10:00:00 GMT"
    assert interval == 90


def test_poll_state_missing_returns_none(conn) -> None:
    assert store.get_poll_state(conn, "absent") == (None, None)


def test_apply_parking_hides_future_and_shows_due(conn) -> None:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    future = store.to_iso(now + timedelta(hours=2))
    past = store.to_iso(now - timedelta(hours=2))
    store.park_item(conn, "octo/widgets#1", "future note", future)
    store.park_item(conn, "octo/widgets#2", "due note", past)
    visible = store.apply_parking(conn, [_item(1), _item(2)], now)
    ids = [i.id for i in visible]
    assert ids == ["octo/widgets#2"]  # #1 still parked, #2 is due
    assert visible[0].reentry_note == "due note"  # card leads with the note


def test_apply_parking_resurfaces_on_new_activity(conn) -> None:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    store.park_item(conn, "octo/widgets#1", "indefinite", None, now=now)  # no deadline
    fresh = store.to_iso(now + timedelta(minutes=5))  # activity after parking
    visible = store.apply_parking(conn, [_item(1, last_activity=fresh)], now)
    assert [i.id for i in visible] == ["octo/widgets#1"]
    assert visible[0].reentry_note == "indefinite"


def test_apply_parking_keeps_indefinite_without_activity_hidden(conn) -> None:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    store.park_item(conn, "octo/widgets#1", "indefinite", None, now=now)
    stale = store.to_iso(now - timedelta(days=1))  # older than the park
    visible = store.apply_parking(conn, [_item(1, last_activity=stale)], now)
    assert visible == []
