"""Phase 2/3 app tests: boot, navigation, mark-done, mode cycle, park."""

from __future__ import annotations

import pytest

from residualself import store
from residualself.models import CiStatus, ItemKind, Mode, WorkItem
from residualself.ui import app as app_module
from residualself.ui.app import ResidualSelfApp
from residualself.ui.park_modal import ParkModal
from residualself.ui.session_modal import SessionModal


def _item(
    number: int,
    *,
    kind: ItemKind = ItemKind.REVIEW_REQUEST,
    thread_id: str | None = None,
    last_activity: str | None = None,
) -> WorkItem:
    return WorkItem(
        id=f"octo/widgets#{number}",
        kind=kind,
        title=f"Fix bug {number}",
        repo="octo/widgets",
        number=number,
        url=f"https://github.com/octo/widgets/pull/{number}",
        reason="review_requested",
        author="alice",
        status="open",
        ci_status=CiStatus.NONE,
        is_pr=True,
        thread_id=thread_id,
        last_activity=last_activity,
    )


def _loader_for(items: list[WorkItem]):
    async def fake_loader(token: str, *, include_mentions: bool = False) -> list[WorkItem]:
        return list(items)

    return fake_loader


@pytest.fixture
def _two_items(monkeypatch: pytest.MonkeyPatch) -> list[WorkItem]:
    items = [_item(1, thread_id="thread-1"), _item(2)]
    monkeypatch.setattr(app_module.queue, "fetch_enriched", _loader_for(items))
    return items


async def test_app_boots_and_shows_first_card(_two_items: list[WorkItem]) -> None:
    app = ResidualSelfApp("tok", db_path=":memory:", enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.items) == 2
        assert app.index == 0


async def test_navigation_advances_index(_two_items: list[WorkItem]) -> None:
    app = ResidualSelfApp("tok", db_path=":memory:", enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        assert app.index == 1
        await pilot.press("b")  # prev (remapped from p, which now parks)
        assert app.index == 0


async def test_done_marks_thread_and_removes_item(
    _two_items: list[WorkItem], monkeypatch: pytest.MonkeyPatch
) -> None:
    marked: list[str] = []

    async def fake_mark(token: str, thread_id: str, client: object = None) -> None:
        marked.append(thread_id)

    monkeypatch.setattr(app_module.github_client, "mark_thread_read", fake_mark)

    app = ResidualSelfApp("tok", db_path=":memory:", enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")  # item #1 has thread-1
        await pilot.pause()
    assert marked == ["thread-1"]
    assert len(app.items) == 1
    assert app.items[0].number == 2


async def test_mode_cycles_and_reorders(monkeypatch: pytest.MonkeyPatch) -> None:
    review = _item(1, kind=ItemKind.REVIEW_REQUEST)
    issue = _item(2, kind=ItemKind.ASSIGNED_ISSUE)
    monkeypatch.setattr(
        app_module.queue, "fetch_enriched", _loader_for([issue, review])
    )
    app = ResidualSelfApp("tok", db_path=":memory:", enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.mode is Mode.NEXT
        assert app.items[0].kind is ItemKind.REVIEW_REQUEST  # review outranks assigned
        await pilot.press("m")
        assert app.mode is Mode.UNBLOCK_OTHERS
        assert all(i.kind is ItemKind.REVIEW_REQUEST for i in app.items)  # filtered


async def test_park_persists_and_hides(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [_item(1, thread_id="thread-1"), _item(2)]
    monkeypatch.setattr(app_module.queue, "fetch_enriched", _loader_for(items))
    db = str(tmp_path / "residualself.db")

    app = ResidualSelfApp("tok", db_path=db, enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ParkModal)
        for ch in "later":
            await pilot.press(ch)
        await pilot.click("#park-4h")
        await pilot.pause()
        assert [i.id for i in app._visible] == ["octo/widgets#2"]

    conn = store.connect(db)
    parked = store.get_parked(conn)
    conn.close()
    assert parked["octo/widgets#1"].reentry_note == "later"


async def test_park_cancel_keeps_item(_two_items: list[WorkItem]) -> None:
    app = ResidualSelfApp("tok", db_path=":memory:", enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert len(app._visible) == 2


async def test_session_key_opens_modal(_two_items: list[WorkItem]) -> None:
    app = ResidualSelfApp("tok", db_path=":memory:", enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SessionModal)
        await pilot.press("escape")


async def test_session_completes_on_item_target(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [_item(1, thread_id="thread-1"), _item(2)]
    monkeypatch.setattr(app_module.queue, "fetch_enriched", _loader_for(items))

    async def fake_mark(token: str, thread_id: str, client: object = None) -> None:
        return None

    monkeypatch.setattr(app_module.github_client, "mark_thread_read", fake_mark)
    db = str(tmp_path / "residualself.db")

    app = ResidualSelfApp("tok", db_path=db, enable_polling=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.start_focus_session(items=1)
        assert app._session is not None
        await pilot.press("d")  # marking one item meets the target
        await pilot.pause()
        assert app._session is None  # session ended on completion

    conn = store.connect(db)
    rows = conn.execute("SELECT items_done FROM session_log").fetchall()
    conn.close()
    assert [r["items_done"] for r in rows] == [1]
