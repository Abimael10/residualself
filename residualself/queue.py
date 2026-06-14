"""Fetch + unify + dedupe -> [WorkItem]; classify.

Phase 1: pull the four Search buckets, turn raw items into WorkItems, and dedupe
by url. ``order_by_mode`` (Phase 3) and enrichment (Phase 4) build on this.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from . import config, github_client
from .github_client import GitHubError
from .models import CiStatus, ItemKind, Mode, WorkItem

# The four Search buckets (SPEC "Actionable queue"). Each uses a single user
# qualifier, so the 2025 advanced-search AND-vs-OR change does not affect them.
SEARCH_QUERIES: dict[ItemKind, str] = {
    ItemKind.REVIEW_REQUEST: "is:open is:pr review-requested:@me",
    ItemKind.ASSIGNED_ISSUE: "is:open assignee:@me",
    ItemKind.MY_PR_ACTIVITY: "is:open is:pr author:@me",
    ItemKind.MENTION: "is:open mentions:@me",
}

_BUCKET_REASON: dict[ItemKind, str] = {
    ItemKind.REVIEW_REQUEST: "review_requested",
    ItemKind.ASSIGNED_ISSUE: "assign",
    ItemKind.MY_PR_ACTIVITY: "authored",
    ItemKind.MENTION: "mention",
}

# Dedupe/ordering priority: lower number wins (mirrors NEXT-mode blend).
_KIND_PRIORITY: dict[ItemKind, int] = {
    ItemKind.CI_FAILURE: 0,
    ItemKind.REVIEW_REQUEST: 1,
    ItemKind.ASSIGNED_ISSUE: 2,
    ItemKind.MY_PR_ACTIVITY: 3,
    ItemKind.MENTION: 4,
}


def _kind_priority(kind: ItemKind) -> int:
    return _KIND_PRIORITY.get(kind, 99)


# Labels that hint an item is likely a quick win.
_QUICK_KEYWORDS = ("good first", "small", "quick", "easy", "trivial", "docs", "typo")
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


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


def _activity_key(item: WorkItem) -> datetime:
    """Sort key for "oldest first"; unknown activity sorts last."""
    return _parse_iso(item.last_activity) or _FAR_FUTURE


def _next_key(item: WorkItem) -> tuple[int, int, datetime]:
    blocking = 0 if item.who_is_waiting else 1
    return (blocking, _kind_priority(item.kind), _activity_key(item))


def _is_quick_win(item: WorkItem) -> bool:
    for label in item._labels:  # Rule 2: bounded by an item's label list.
        low = label.lower()
        if any(keyword in low for keyword in _QUICK_KEYWORDS):
            return True
    return False


def order_by_mode(items: list[WorkItem], mode: Mode) -> list[WorkItem]:
    """Order (and for focused modes, filter) the queue for the given mode."""
    pool = list(items)  # never mutate the caller's list (Rule 6 spirit).
    if mode is Mode.NEXT:
        return sorted(pool, key=_next_key)
    if mode is Mode.UNBLOCK_OTHERS:
        reviews = sorted(
            (i for i in pool if i.kind is ItemKind.REVIEW_REQUEST), key=_activity_key
        )
        waiting = sorted(
            (i for i in pool if i.kind is not ItemKind.REVIEW_REQUEST and i.who_is_waiting),
            key=_activity_key,
        )
        return reviews + waiting
    if mode is Mode.QUICK_WINS:
        return sorted(pool, key=lambda i: (0 if _is_quick_win(i) else 1, _activity_key(i)))
    if mode is Mode.DEEP_WORK:
        deep_kinds = (ItemKind.ASSIGNED_ISSUE, ItemKind.MY_PR_ACTIVITY)
        return sorted((i for i in pool if i.kind in deep_kinds), key=_activity_key)
    return pool


def _require(raw: dict, key: str) -> object:
    """Return a non-empty field from a raw search item (Rule 7)."""
    value = raw.get(key)
    if value in (None, ""):
        raise GitHubError(f"search item missing '{key}'")
    return value


def _repo_full_name(raw: dict) -> str:
    """Derive ``owner/name`` from a raw search item."""
    repo_url = str(raw.get("repository_url", ""))
    marker = "/repos/"
    if marker in repo_url:
        return repo_url.split(marker, 1)[1]
    html_url = str(_require(raw, "html_url"))  # e.g. .../owner/name/pull/123
    parts = html_url.split("/")
    if len(parts) >= 5:
        return f"{parts[3]}/{parts[4]}"
    raise GitHubError(f"cannot derive repo from item: {html_url!r}")


def classify(raw: dict, bucket: ItemKind) -> ItemKind:
    """Derive the WorkItem kind from its search bucket.

    Phase 1: the kind is the bucket it came from. Phase 4 enrichment upgrades a
    failing-CI authored PR to CI_FAILURE once a CI rollup is available.
    """
    assert bucket in SEARCH_QUERIES, f"unknown bucket: {bucket!r}"
    return bucket


def work_item_from_search(raw: dict, bucket: ItemKind) -> WorkItem:
    """Build a WorkItem from one raw Search API item."""
    repo = _repo_full_name(raw)
    number = int(_require(raw, "number"))
    is_pr = "pull_request" in raw
    author = (raw.get("user") or {}).get("login")
    status = "draft" if raw.get("draft") else raw.get("state")
    labels = tuple(lbl.get("name") or "" for lbl in (raw.get("labels") or []))
    assignees = tuple(
        a["login"] for a in (raw.get("assignees") or []) if a.get("login")
    )
    return WorkItem(
        id=f"{repo}#{number}",
        kind=classify(raw, bucket),
        title=str(raw.get("title", "")),
        repo=repo,
        number=number,
        url=str(_require(raw, "html_url")),
        reason=_BUCKET_REASON.get(bucket),
        author=author,
        status=status,
        last_activity=raw.get("updated_at"),
        node_id=raw.get("node_id"),
        is_pr=is_pr,
        assignees=assignees,
        _labels=labels,
    )


def dedupe(items: list[WorkItem]) -> list[WorkItem]:
    """Collapse items sharing a url, keeping the highest-priority kind."""
    by_key: dict[str, WorkItem] = {}
    for item in items:  # Rule 2: bounded by the (paged) input list.
        key = item.url or item.id
        existing = by_key.get(key)
        if existing is None or _kind_priority(item.kind) < _kind_priority(existing.kind):
            by_key[key] = item
    return list(by_key.values())


def _subject_repo_number(subject_url: str) -> tuple[str, int] | None:
    """Parse ``owner/name`` + number from a notification ``subject.url``.

    e.g. ``https://api.github.com/repos/octo/widgets/pulls/123`` -> (octo/widgets, 123).
    """
    marker = "/repos/"
    if not subject_url or marker not in subject_url:
        return None
    tail = subject_url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 4 or not parts[-1].isdigit():
        return None
    return f"{parts[0]}/{parts[1]}", int(parts[-1])


def attach_thread_ids(items: list[WorkItem], notifications: list[dict]) -> list[WorkItem]:
    """Attach notification thread ids (and refine reason) onto matching items."""
    index: dict[tuple[str, int], dict] = {}
    for notif in notifications:  # Rule 2: bounded by the notifications page.
        subject = notif.get("subject") or {}
        key = _subject_repo_number(str(subject.get("url") or ""))
        if key is not None:
            index[key] = notif
    for item in items:  # Rule 2: bounded by the deduped item list.
        notif = index.get((item.repo, item.number))
        if notif is None:
            continue
        item.thread_id = notif.get("id")
        reason = notif.get("reason")
        if reason:
            item.reason = reason
    return items


async def fetch_with_notifications(
    token: str, *, include_mentions: bool = False
) -> list[WorkItem]:
    """Fetch the queue and attach notification thread ids for mark-done."""
    items = await fetch(token, include_mentions=include_mentions)
    try:
        notifications = await github_client.list_notifications(token)
    except (GitHubError, httpx.HTTPError):
        # Deliberate degradation (Rule 7 exception): notifications are auxiliary;
        # without them mark-done falls back to local removal, queue still works.
        return items
    return attach_thread_ids(items, notifications)


# GraphQL: enrich PRs and issues in one query, keyed by global node id.
_GQL_ENRICH = """
query Enrich($ids: [ID!]!) {
  nodes(ids: $ids) {
    __typename
    ... on PullRequest {
      id
      isDraft
      reviewDecision
      author { login }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      reviewRequests(first: 20) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Team { name }
          }
        }
      }
      comments(last: 1) { nodes { bodyText } }
      labels(first: 20) { nodes { name } }
    }
    ... on Issue {
      id
      assignees(first: 10) { nodes { login } }
      labels(first: 20) { nodes { name } }
      comments(last: 1) { nodes { bodyText } }
    }
  }
}
"""

_STATUS_TO_CI = {
    "SUCCESS": CiStatus.SUCCESS,
    "FAILURE": CiStatus.FAILURE,
    "ERROR": CiStatus.FAILURE,
    "PENDING": CiStatus.PENDING,
    "EXPECTED": CiStatus.PENDING,
}


def _chunks(values: list[str], size: int) -> list[list[str]]:
    assert size > 0, "chunk size must be positive"
    return [values[i : i + size] for i in range(0, len(values), size)]


def _ci_from_node(node: dict) -> CiStatus:
    commits = (node.get("commits") or {}).get("nodes") or []
    if not commits:
        return CiStatus.NONE
    rollup = (commits[-1].get("commit") or {}).get("statusCheckRollup")
    if not rollup:
        return CiStatus.NONE
    return _STATUS_TO_CI.get(rollup.get("state"), CiStatus.NONE)


def _requested_reviewers(node: dict) -> tuple[str, ...]:
    nodes = (node.get("reviewRequests") or {}).get("nodes") or []
    out = []
    for entry in nodes:  # Rule 2: bounded by the requested reviewer page.
        reviewer = entry.get("requestedReviewer") or {}
        login = reviewer.get("login") or reviewer.get("name")
        if login:
            out.append(login)
    return tuple(out)


def _latest_comment(node: dict) -> str | None:
    comments = (node.get("comments") or {}).get("nodes") or []
    if not comments:
        return None
    body = comments[-1].get("bodyText") or ""
    return body[:200] or None


def _names(connection: dict | None, field: str) -> tuple[str, ...]:
    """Pluck a tuple of ``field`` values from a GraphQL connection's nodes."""
    nodes = (connection or {}).get("nodes") or []
    return tuple(n[field] for n in nodes if n.get(field))


def _apply_node(item: WorkItem, node: dict) -> None:
    """Mutate a WorkItem with enrichment data from its GraphQL node."""
    item.snippet = _latest_comment(node) or item.snippet
    labels = _names(node.get("labels"), "name")
    if labels:
        item._labels = labels
    if node.get("__typename") == "PullRequest":
        item.is_pr = True
        if node.get("isDraft"):
            item.status = "draft"
        if node.get("reviewDecision"):
            item.review_decision = node["reviewDecision"]
        item.ci_status = _ci_from_node(node)
        item.reviewers = _requested_reviewers(node)
        if item.kind is ItemKind.REVIEW_REQUEST:
            author = (node.get("author") or {}).get("login") or item.author
            item.who_is_waiting = (author,) if author else ()
        if item.kind is ItemKind.MY_PR_ACTIVITY and item.ci_status is CiStatus.FAILURE:
            item.kind = ItemKind.CI_FAILURE  # Phase 4: failing CI on your PR.
    elif node.get("__typename") == "Issue":
        assignees = _names(node.get("assignees"), "login")
        if assignees:
            item.assignees = assignees


async def enrich(token: str, items: list[WorkItem]) -> list[WorkItem]:
    """Enrich items in place via one GraphQL query per batch of node ids."""
    by_id = {i.node_id: i for i in items if i.node_id}
    if not by_id:
        return items
    timeout = httpx.Timeout(config.HTTP_TIMEOUT)
    async with httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout) as client:
        for batch in _chunks(list(by_id.keys()), config.GRAPHQL_BATCH):  # Rule 2: bounded.
            data = await github_client.graphql(token, _GQL_ENRICH, {"ids": batch}, client=client)
            for node in data.get("nodes") or []:
                item = by_id.get((node or {}).get("id"))
                if item is not None:
                    _apply_node(item, node)
    return items


async def fetch_enriched(token: str, *, include_mentions: bool = False) -> list[WorkItem]:
    """Full pipeline: search + notifications + GraphQL enrichment."""
    items = await fetch_with_notifications(token, include_mentions=include_mentions)
    try:
        await enrich(token, items)
    except (GitHubError, httpx.HTTPError):
        # Rule 7 exception: enrichment is best-effort; cards degrade to search data.
        pass
    return items


async def fetch(token: str, *, include_mentions: bool = False) -> list[WorkItem]:
    """Run the Search buckets and return a deduped list of WorkItems."""
    if not token:
        raise GitHubError("no token; run `residualself auth` first")
    buckets = dict(SEARCH_QUERIES)
    if not include_mentions:
        buckets.pop(ItemKind.MENTION, None)
    collected: list[WorkItem] = []
    timeout = httpx.Timeout(config.HTTP_TIMEOUT)
    async with httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout) as client:
        for bucket, query in buckets.items():  # Rule 2: <= 4 buckets.
            raw_items = await github_client.search(token, query, client=client)
            for raw in raw_items:
                collected.append(work_item_from_search(raw, bucket))
    return dedupe(collected)
