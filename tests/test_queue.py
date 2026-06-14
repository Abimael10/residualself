"""Phase 1 tests: classify, work-item building, dedupe, and fetch (mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx

from residualself import config, queue
from residualself.models import CiStatus, ItemKind, Mode, WorkItem


def _pr(number: int, repo: str = "octo/widgets", **extra: object) -> dict:
    raw = {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "state": "open",
        "user": {"login": "alice"},
        "pull_request": {"url": "..."},
        "updated_at": "2026-06-10T12:00:00Z",
    }
    raw.update(extra)
    return raw


def _issue(number: int, repo: str = "octo/widgets", **extra: object) -> dict:
    raw = {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "state": "open",
        "user": {"login": "bob"},
        "updated_at": "2026-06-09T12:00:00Z",
    }
    raw.update(extra)
    return raw


@pytest.fixture(autouse=True)
def _client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CLIENT_ID", "test-client-id")


def test_classify_maps_bucket_to_kind() -> None:
    assert queue.classify(_pr(1), ItemKind.REVIEW_REQUEST) is ItemKind.REVIEW_REQUEST
    assert queue.classify(_issue(2), ItemKind.ASSIGNED_ISSUE) is ItemKind.ASSIGNED_ISSUE


def test_work_item_extracts_repo_number_and_pr_flag() -> None:
    item = queue.work_item_from_search(_pr(42, draft=True), ItemKind.MY_PR_ACTIVITY)
    assert item.repo == "octo/widgets"
    assert item.number == 42
    assert item.is_pr is True
    assert item.status == "draft"
    assert item.id == "octo/widgets#42"
    assert item.reason == "authored"


def test_work_item_repo_falls_back_to_html_url() -> None:
    raw = _issue(7)
    del raw["repository_url"]
    item = queue.work_item_from_search(raw, ItemKind.ASSIGNED_ISSUE)
    assert item.repo == "octo/widgets"
    assert item.is_pr is False


def test_dedupe_keeps_highest_priority_kind() -> None:
    same_url = "https://github.com/octo/widgets/pull/99"
    review = queue.work_item_from_search(
        {**_pr(99), "html_url": same_url}, ItemKind.REVIEW_REQUEST
    )
    authored = queue.work_item_from_search(
        {**_pr(99), "html_url": same_url}, ItemKind.MY_PR_ACTIVITY
    )
    result = queue.dedupe([authored, review])
    assert len(result) == 1
    assert result[0].kind is ItemKind.REVIEW_REQUEST  # review_request outranks authored


def _search_responder(request: httpx.Request) -> httpx.Response:
    q = request.url.params.get("q", "")
    if "review-requested" in q:
        return httpx.Response(200, json={"items": [_pr(1)]})
    if "assignee" in q:
        return httpx.Response(200, json={"items": [_issue(2)]})
    if "author" in q:
        return httpx.Response(200, json={"items": [_pr(1)]})  # dup of review bucket
    return httpx.Response(200, json={"items": []})


@respx.mock
async def test_fetch_unifies_and_dedupes_buckets() -> None:
    respx.get(url__startswith=f"{config.API_BASE}/search/issues").mock(
        side_effect=_search_responder
    )
    items = await queue.fetch("gho_token", include_mentions=False)
    by_url = {it.url: it for it in items}
    # PR #1 appears in both review and author buckets -> deduped to one.
    assert len(items) == 2
    assert by_url["https://github.com/octo/widgets/pull/1"].kind is ItemKind.REVIEW_REQUEST
    assert by_url["https://github.com/octo/widgets/issues/2"].kind is ItemKind.ASSIGNED_ISSUE


@respx.mock
async def test_fetch_requires_token() -> None:
    with pytest.raises(queue.GitHubError):
        await queue.fetch("")


def _notification(repo: str, number: int, kind: str = "pulls", **extra: object) -> dict:
    notif = {
        "id": f"thread-{number}",
        "reason": "review_requested",
        "subject": {
            "title": f"subject {number}",
            "url": f"https://api.github.com/repos/{repo}/{kind}/{number}",
            "type": "PullRequest",
        },
        "repository": {"full_name": repo},
        "unread": True,
    }
    notif.update(extra)
    return notif


def test_attach_thread_ids_matches_by_repo_and_number() -> None:
    item = queue.work_item_from_search(_pr(123), ItemKind.REVIEW_REQUEST)
    notifs = [
        _notification("octo/widgets", 123),
        _notification("other/repo", 999, kind="issues"),
    ]
    queue.attach_thread_ids([item], notifs)
    assert item.thread_id == "thread-123"
    assert item.reason == "review_requested"


def test_attach_thread_ids_leaves_unmatched_items_alone() -> None:
    item = queue.work_item_from_search(_issue(5), ItemKind.ASSIGNED_ISSUE)
    queue.attach_thread_ids([item], [_notification("octo/widgets", 4, kind="issues")])
    assert item.thread_id is None


def test_subject_repo_number_parses_pulls_and_issues() -> None:
    base = "https://api.github.com/repos/octo/widgets"
    assert queue._subject_repo_number(f"{base}/pulls/12") == ("octo/widgets", 12)
    assert queue._subject_repo_number(f"{base}/issues/7") == ("octo/widgets", 7)
    assert queue._subject_repo_number("https://example.com/nope") is None


def _wi(
    number: int,
    kind: ItemKind,
    *,
    last_activity: str | None = None,
    labels: tuple[str, ...] = (),
    who_is_waiting: tuple[str, ...] = (),
    node_id: str | None = None,
    author: str | None = None,
    is_pr: bool = False,
) -> WorkItem:
    return WorkItem(
        id=f"octo/widgets#{number}",
        kind=kind,
        title=f"item {number}",
        repo="octo/widgets",
        number=number,
        url=f"https://github.com/octo/widgets/x/{number}",
        last_activity=last_activity,
        who_is_waiting=who_is_waiting,
        node_id=node_id,
        author=author,
        is_pr=is_pr,
        _labels=labels,
    )


def test_order_next_prioritizes_by_kind_then_oldest() -> None:
    review_old = _wi(1, ItemKind.REVIEW_REQUEST, last_activity="2026-06-01T00:00:00Z")
    review_new = _wi(2, ItemKind.REVIEW_REQUEST, last_activity="2026-06-10T00:00:00Z")
    assigned = _wi(3, ItemKind.ASSIGNED_ISSUE, last_activity="2026-05-01T00:00:00Z")
    ci = _wi(4, ItemKind.CI_FAILURE, last_activity="2026-06-12T00:00:00Z")
    ordered = queue.order_by_mode([assigned, review_new, review_old, ci], Mode.NEXT)
    assert [i.number for i in ordered] == [4, 1, 2, 3]  # CI, review(old, new), assigned


def test_order_next_blocking_others_floats_to_top() -> None:
    blocker = _wi(1, ItemKind.MY_PR_ACTIVITY, who_is_waiting=("bob",))
    review = _wi(2, ItemKind.REVIEW_REQUEST)
    ordered = queue.order_by_mode([review, blocker], Mode.NEXT)
    assert ordered[0].number == 1  # who_is_waiting outranks everything


def test_order_unblock_others_filters_to_reviews_and_waiters() -> None:
    review = _wi(1, ItemKind.REVIEW_REQUEST, last_activity="2026-06-05T00:00:00Z")
    waiting_pr = _wi(2, ItemKind.MY_PR_ACTIVITY, who_is_waiting=("carol",))
    plain_issue = _wi(3, ItemKind.ASSIGNED_ISSUE)
    ordered = queue.order_by_mode([plain_issue, waiting_pr, review], Mode.UNBLOCK_OTHERS)
    assert [i.number for i in ordered] == [1, 2]  # review first, then waiting PR


def test_order_quick_wins_surfaces_small_labels_first() -> None:
    big = _wi(1, ItemKind.ASSIGNED_ISSUE, last_activity="2026-06-01T00:00:00Z")
    small = _wi(2, ItemKind.ASSIGNED_ISSUE, labels=("good first issue",))
    ordered = queue.order_by_mode([big, small], Mode.QUICK_WINS)
    assert ordered[0].number == 2


def test_order_deep_work_filters_and_sorts_oldest_first() -> None:
    issue_old = _wi(1, ItemKind.ASSIGNED_ISSUE, last_activity="2026-05-01T00:00:00Z")
    pr_new = _wi(2, ItemKind.MY_PR_ACTIVITY, last_activity="2026-06-10T00:00:00Z")
    review = _wi(3, ItemKind.REVIEW_REQUEST)
    ordered = queue.order_by_mode([pr_new, review, issue_old], Mode.DEEP_WORK)
    assert [i.number for i in ordered] == [1, 2]  # review excluded; oldest first


async def test_enrich_maps_fields_and_upgrades_failing_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr = _wi(1, ItemKind.MY_PR_ACTIVITY, node_id="PR_1", is_pr=True)
    review = _wi(2, ItemKind.REVIEW_REQUEST, node_id="PR_2", is_pr=True, author="x")
    issue = _wi(3, ItemKind.ASSIGNED_ISSUE, node_id="I_1")
    nodes = [
        {
            "__typename": "PullRequest",
            "id": "PR_1",
            "isDraft": False,
            "reviewDecision": "CHANGES_REQUESTED",
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
            "reviewRequests": {"nodes": []},
            "comments": {"nodes": [{"bodyText": "please fix"}]},
            "labels": {"nodes": []},
        },
        {
            "__typename": "PullRequest",
            "id": "PR_2",
            "author": {"login": "alice"},
            "commits": {"nodes": []},
            "reviewRequests": {
                "nodes": [{"requestedReviewer": {"__typename": "User", "login": "me"}}]
            },
            "comments": {"nodes": []},
            "labels": {"nodes": []},
        },
        {
            "__typename": "Issue",
            "id": "I_1",
            "assignees": {"nodes": [{"login": "me"}]},
            "labels": {"nodes": [{"name": "bug"}]},
            "comments": {"nodes": []},
        },
    ]

    async def fake_graphql(token, query, variables=None, client=None):  # noqa: ANN001
        return {"nodes": nodes}

    monkeypatch.setattr(queue.github_client, "graphql", fake_graphql)
    await queue.enrich("tok", [pr, review, issue])

    assert pr.ci_status is CiStatus.FAILURE
    assert pr.kind is ItemKind.CI_FAILURE  # failing CI on your PR upgrades the kind
    assert pr.review_decision == "CHANGES_REQUESTED"
    assert pr.snippet == "please fix"
    assert review.who_is_waiting == ("alice",)  # author waits on your review
    assert review.reviewers == ("me",)
    assert issue.assignees == ("me",)
    assert issue._labels == ("bug",)


async def test_enrich_noop_without_node_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_graphql(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal called
        called = True
        return {"nodes": []}

    monkeypatch.setattr(queue.github_client, "graphql", fake_graphql)
    await queue.enrich("tok", [_wi(1, ItemKind.ASSIGNED_ISSUE)])  # no node_id
    assert called is False
