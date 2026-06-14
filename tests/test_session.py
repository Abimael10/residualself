"""Phase 5 tests: focus-session state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from residualself import session

_START = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


def test_remaining_seconds_counts_down() -> None:
    focus = session.start_session(minutes=25, current=_START)
    assert focus.remaining_seconds(_START) == 1500
    assert focus.remaining_seconds(_START + timedelta(minutes=10)) == 900


def test_remaining_seconds_none_for_count_based() -> None:
    focus = session.start_session(items=3, current=_START)
    assert focus.remaining_seconds(_START) is None


def test_is_complete_time_based() -> None:
    focus = session.start_session(minutes=25, current=_START)
    assert focus.is_complete(_START + timedelta(minutes=24)) is False
    assert focus.is_complete(_START + timedelta(minutes=25)) is True


def test_is_complete_count_based() -> None:
    focus = session.start_session(items=2, current=_START)
    assert focus.is_complete(_START) is False
    focus.record_done()
    focus.record_done()
    assert focus.is_complete(_START) is True


def test_start_session_requires_a_commitment() -> None:
    with pytest.raises(ValueError):
        session.start_session()


def test_summary_reports_items_done() -> None:
    focus = session.start_session(items=3, current=_START)
    focus.record_done()
    assert focus.summary() == "1 item(s) done"
