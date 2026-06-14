"""Single-focus card widget — renders exactly ONE WorkItem."""

from __future__ import annotations

from rich.markup import escape
from textual.widgets import Static

from ..models import CiStatus, WorkItem

_KIND_COLOR = {
    "CI_FAILURE": "red",
    "REVIEW_REQUEST": "magenta",
    "ASSIGNED_ISSUE": "cyan",
    "MY_PR_ACTIVITY": "green",
    "MENTION": "yellow",
}

_CI_COLOR = {
    CiStatus.SUCCESS: "green",
    CiStatus.FAILURE: "red",
    CiStatus.PENDING: "yellow",
    CiStatus.NONE: "dim",
}

_EMPTY = "[b green]Inbox zero[/b green]\n\nNothing is on you right now. Take a break."


class Card(Static):
    """Shows a single WorkItem, or an inbox-zero message when empty."""

    def show(self, item: WorkItem | None) -> None:
        self.update(_EMPTY if item is None else _render(item))


def _render(item: WorkItem) -> str:
    color = _KIND_COLOR.get(item.kind.value, "white")
    kind_of = "PR" if item.is_pr else "Issue"
    rows = [
        f"[b]{escape(item.title)}[/b]",
        f"[{color}]{item.kind.value}[/{color}] "
        f"[dim]· {escape(item.repo)}#{item.number} · {kind_of}[/dim]",
        "",
    ]
    if item.reentry_note:  # Re-entry leads, so coming back is not a cold start.
        rows.append(f"[yellow]↩ Re-entry note:[/yellow] {escape(item.reentry_note)}")
        rows.append("")
    rows.extend(_detail_rows(item))
    rows.append("")
    rows.append(f"[dim underline]{escape(item.url)}[/dim underline]")
    return "\n".join(rows)


def _detail_rows(item: WorkItem) -> list[str]:
    rows: list[str] = []
    if item.author:
        rows.append(f"[dim]Author:[/dim] {escape(item.author)}")
    if item.who_is_waiting:
        rows.append(f"[dim]Waiting:[/dim] {escape(', '.join(item.who_is_waiting))}")
    if item.status:
        rows.append(f"[dim]Status:[/dim] {escape(str(item.status))}")
    if item.review_decision:
        rows.append(f"[dim]Review:[/dim] {escape(item.review_decision)}")
    if item.reviewers:
        rows.append(f"[dim]Reviewers:[/dim] {escape(', '.join(item.reviewers))}")
    if item.ci_status is not CiStatus.NONE:
        ci_color = _CI_COLOR.get(item.ci_status, "dim")
        rows.append(f"[dim]CI:[/dim] [{ci_color}]{item.ci_status.value}[/{ci_color}]")
    if item.assignees:
        rows.append(f"[dim]Assignee(s):[/dim] {escape(', '.join(item.assignees))}")
    if item._labels:
        rows.append(f"[dim]Labels:[/dim] {escape(', '.join(item._labels))}")
    if item.reason:
        rows.append(f"[dim]Reason:[/dim] {escape(item.reason)}")
    return rows
