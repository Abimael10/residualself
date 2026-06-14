"""Generate an offline SVG screenshot of the ResidualSelf card (no network).

Usage: python tools/make_demo_screenshot.py
Writes docs/demo.svg using fabricated sample items, so it needs neither auth
nor a GitHub connection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from residualself.models import CiStatus, ItemKind, WorkItem
from residualself.ui import app as app_module
from residualself.ui.app import ResidualSelfApp

_DOCS = Path(__file__).resolve().parent.parent / "docs"


def _sample_items() -> list[WorkItem]:
    return [
        WorkItem(
            id="acme/api#412",
            kind=ItemKind.CI_FAILURE,
            title="Fix flaky auth-retry test on CI",
            repo="acme/api",
            number=412,
            url="https://github.com/acme/api/pull/412",
            reason="ci_activity",
            author="dana",
            status="open",
            review_decision="CHANGES_REQUESTED",
            ci_status=CiStatus.FAILURE,
            is_pr=True,
            reviewers=("you",),
            snippet="The retry test is timing out intermittently on macOS runners.",
            last_activity="2026-06-12T09:00:00Z",
        ),
        WorkItem(
            id="acme/web#1180",
            kind=ItemKind.REVIEW_REQUEST,
            title="Add keyboard shortcuts to the settings panel",
            repo="acme/web",
            number=1180,
            url="https://github.com/acme/web/pull/1180",
            reason="review_requested",
            author="lee",
            status="open",
            ci_status=CiStatus.SUCCESS,
            is_pr=True,
            who_is_waiting=("lee",),
            last_activity="2026-06-13T14:30:00Z",
        ),
    ]


async def _main() -> None:
    async def loader(token: str, *, include_mentions: bool = False) -> list[WorkItem]:
        return _sample_items()

    app_module.queue.fetch_enriched = loader  # type: ignore[assignment]
    _DOCS.mkdir(parents=True, exist_ok=True)
    app = ResidualSelfApp("demo-token", db_path=":memory:", enable_polling=False)
    async with app.run_test(size=(96, 28)) as pilot:
        await pilot.pause()
        app.save_screenshot(filename="demo.svg", path=str(_DOCS))
    print(f"Wrote {_DOCS / 'demo.svg'}")


if __name__ == "__main__":
    asyncio.run(_main())
