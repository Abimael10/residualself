"""Textual App: single-focus card, modes, navigation, park, mark-done."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from pathlib import Path

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from .. import config, github_client, queue, session, store
from ..models import Mode, WorkItem
from .card import Card
from .park_modal import ParkModal
from .session_bar import SessionBar
from .session_modal import SessionModal

log = logging.getLogger("residualself")
_POLL_KEY = "notifications"

_CSS = """
#status { padding: 0 1; color: $text-muted; }
#session { padding: 0 1; color: $accent; }
#card { padding: 1 2; border: round $primary; margin: 1 2; height: 1fr; }
"""

_MODE_CYCLE = [Mode.NEXT, Mode.UNBLOCK_OTHERS, Mode.QUICK_WINS, Mode.DEEP_WORK]


class ResidualSelfApp(App):
    """One card at a time. Navigate, open, park, mark done, switch modes."""

    CSS = _CSS
    BINDINGS = [
        Binding("n", "next", "Next"),
        Binding("b", "prev", "Prev"),
        Binding("right", "next", "Next", show=False),
        Binding("left", "prev", "Prev", show=False),
        Binding("o", "open", "Open"),
        Binding("p", "park", "Park"),
        Binding("d", "done", "Done"),
        Binding("m", "mode", "Mode"),
        Binding("s", "session", "Session"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        token: str,
        *,
        include_mentions: bool = False,
        db_path: str | Path | None = None,
        enable_polling: bool = True,
    ) -> None:
        super().__init__()
        if not token:
            raise ValueError("ResidualSelfApp requires a token")
        self._token = token
        self._include_mentions = include_mentions
        self._db_path = db_path
        self._enable_polling = enable_polling
        self._last_modified: str | None = None
        self._conn = None
        self._visible: list[WorkItem] = []  # unparked working set
        self.items: list[WorkItem] = []  # ordered/filtered display list
        self.index = 0
        self.mode = Mode.NEXT
        self._session: session.FocusSession | None = None
        self._session_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        yield SessionBar(id="session")
        yield Card(id="card")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "ResidualSelf"
        self.sub_title = self.mode.value
        self._conn = store.connect(self._db_path)
        self._last_modified, _ = store.get_poll_state(self._conn, _POLL_KEY)
        await self._load()
        self._update_session_bar()
        if self._enable_polling:
            self._poll_worker()

    def on_unmount(self) -> None:
        if self._conn is not None:
            self._conn.close()

    async def _load(self, *, preserve: bool = False) -> None:
        prev_id = None
        if preserve and (current := self._current()) is not None:
            prev_id = current.id
        self._set_status("Loading your queue…")
        try:
            raw = await queue.fetch_enriched(
                self._token, include_mentions=self._include_mentions
            )
        except (github_client.GitHubError, httpx.HTTPError) as exc:
            self._set_status(f"Error loading queue: {exc}")
            return
        self._visible = store.apply_parking(self._conn, raw, store.now_utc())
        self._apply_mode(select_id=prev_id)

    def _apply_mode(self, *, select_id: str | None = None) -> None:
        self.items = queue.order_by_mode(self._visible, self.mode)
        if select_id is not None:
            self.index = next(
                (i for i, it in enumerate(self.items) if it.id == select_id), 0
            )
        else:
            self.index = 0
        self._refresh_card()

    @work(exclusive=True, group="poll")
    async def _poll_worker(self) -> None:
        # Rule 2 exception: a deliberately non-terminating UI poller; Textual
        # cancels it on app exit. It must not terminate by accident.
        while True:
            try:
                poll = await github_client.fetch_notifications_polled(
                    self._token, last_modified=self._last_modified
                )
            except (github_client.GitHubError, httpx.HTTPError) as exc:
                log.warning("notifications poll failed: %s", exc)
                await asyncio.sleep(config.MIN_POLL_INTERVAL)
                continue
            interval = max(poll.poll_interval, config.MIN_POLL_INTERVAL)
            changed = poll.items is not None
            log.info(
                "notifications poll: interval=%ss (X-Poll-Interval) changed=%s",
                interval,
                changed,
            )
            if changed:
                self._last_modified = poll.last_modified
                store.set_poll_state(
                    self._conn, _POLL_KEY, self._last_modified, poll.poll_interval
                )
                await self._load(preserve=True)
            await asyncio.sleep(interval)

    def _current(self) -> WorkItem | None:
        if not self.items:
            return None
        self.index = max(0, min(self.index, len(self.items) - 1))
        return self.items[self.index]

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _refresh_card(self) -> None:
        self.query_one("#card", Card).show(self._current())
        total = len(self.items)
        pos = self.index + 1 if total else 0
        self._set_status(f"Mode: {self.mode.value}    {pos}/{total}")

    def _remove_current(self) -> None:
        item = self._current()
        if item is None:
            return
        self._visible = [i for i in self._visible if i.id != item.id]
        self._apply_mode()

    def action_next(self) -> None:
        if self.items and self.index < len(self.items) - 1:
            self.index += 1
            self._refresh_card()

    def action_prev(self) -> None:
        if self.items and self.index > 0:
            self.index -= 1
            self._refresh_card()

    def action_open(self) -> None:
        item = self._current()
        if item is not None and item.url:
            webbrowser.open(item.url)

    def action_mode(self) -> None:
        current = _MODE_CYCLE.index(self.mode)
        self.mode = _MODE_CYCLE[(current + 1) % len(_MODE_CYCLE)]
        self.sub_title = self.mode.value
        self._apply_mode()

    @work
    async def action_park(self) -> None:
        # @work: push_screen_wait requires a worker context.
        item = self._current()
        if item is None:
            return
        result = await self.push_screen_wait(ParkModal())
        if result is None:
            return
        note, choice = result
        until = store.compute_until(choice, store.now_utc())
        until_iso = store.to_iso(until) if until is not None else None
        store.park_item(self._conn, item.id, note or None, until_iso)
        self._remove_current()
        self.notify(f"Parked {item.repo}#{item.number}")

    async def action_done(self) -> None:
        item = self._current()
        if item is None:
            return
        if item.thread_id:
            try:
                await github_client.mark_thread_read(self._token, item.thread_id)
            except (github_client.GitHubError, httpx.HTTPError) as exc:
                self._set_status(f"Mark-done failed: {exc}")
                return
            note = "marked read on GitHub"
        else:
            note = "removed locally (no notification thread)"
        item.done = True
        self._remove_current()
        self.notify(f"Done: {item.repo}#{item.number} — {note}")
        if self._session is not None:
            self._session.record_done()
            self._update_session_bar()
            if self._session.is_complete(session.now()):
                self._end_focus_session()

    def action_quit(self) -> None:
        self.exit()

    # --- Focus session -------------------------------------------------------

    @work
    async def action_session(self) -> None:
        # @work: push_screen_wait requires a worker context.
        if self._session is not None:
            self._end_focus_session()
            return
        result = await self.push_screen_wait(SessionModal())
        if result is None:
            return
        kind, amount = result
        minutes = amount if kind == "minutes" else None
        items = amount if kind == "items" else None
        self.start_focus_session(minutes=minutes, items=items)

    def start_focus_session(
        self, *, minutes: int | None = None, items: int | None = None
    ) -> None:
        self._session = session.start_session(minutes=minutes, items=items)
        self._update_session_bar()
        if minutes:  # only time-based sessions need a ticking countdown
            self._session_timer = self.set_interval(1.0, self._tick)
        self.notify("Focus session started.")

    def _tick(self) -> None:
        if self._session is None:
            return
        self._update_session_bar()
        if self._session.is_complete(session.now()):
            self._end_focus_session()

    def _end_focus_session(self) -> None:
        if self._session is None:
            return
        finished = self._session
        finished.ended_at = session.now()
        if self._session_timer is not None:
            self._session_timer.stop()
            self._session_timer = None
        self._session = None
        store.log_session(self._conn, finished)
        self._update_session_bar()
        self.notify(f"Session complete — {finished.summary()}.")

    def _update_session_bar(self) -> None:
        self.query_one("#session", SessionBar).update_session(self._session, session.now())
