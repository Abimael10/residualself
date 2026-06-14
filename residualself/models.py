"""Data model: WorkItem and the enums it depends on.

A WorkItem is the single unit shown on a card. Fields beyond Phase 1 (review
decision, CI status, who-is-waiting, thread id, parked/re-entry) are present but
default to empty until later phases populate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ItemKind(StrEnum):
    """What kind of obligation this item represents."""

    REVIEW_REQUEST = "REVIEW_REQUEST"
    ASSIGNED_ISSUE = "ASSIGNED_ISSUE"
    MY_PR_ACTIVITY = "MY_PR_ACTIVITY"
    MENTION = "MENTION"
    CI_FAILURE = "CI_FAILURE"


class Mode(StrEnum):
    """How the queue is ordered/filtered (used from Phase 3)."""

    NEXT = "NEXT"
    UNBLOCK_OTHERS = "UNBLOCK_OTHERS"
    QUICK_WINS = "QUICK_WINS"
    DEEP_WORK = "DEEP_WORK"


class CiStatus(StrEnum):
    """CI rollup state for a PR (populated by Phase 4 enrichment)."""

    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    NONE = "none"


@dataclass
class WorkItem:
    """One actionable GitHub obligation, shown one-at-a-time on a card."""

    id: str
    kind: ItemKind
    title: str
    repo: str
    number: int
    url: str
    reason: str | None = None
    author: str | None = None
    who_is_waiting: tuple[str, ...] = ()
    status: str | None = None  # "open" | "draft"
    review_decision: str | None = None
    ci_status: CiStatus = CiStatus.NONE
    last_activity: str | None = None  # ISO 8601 timestamp
    snippet: str | None = None
    thread_id: str | None = None
    node_id: str | None = None  # GraphQL global id, for enrichment
    is_pr: bool = False
    reviewers: tuple[str, ...] = ()  # requested reviewers (you may be waiting on)
    assignees: tuple[str, ...] = ()  # for issues
    # Local-only (SQLite-backed from Phase 3).
    parked_until: str | None = None
    reentry_note: str | None = None
    done: bool = False
    # Internal tag for ordering/debug; not part of the public contract.
    _labels: tuple[str, ...] = field(default=(), repr=False)
