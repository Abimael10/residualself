"""GitHub REST + GraphQL client.

Phase 0 implements only ``get_authenticated_user`` (for ``residualself whoami``).
Search, notifications, enrichment, and mark-done arrive in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from . import config


class GitHubError(Exception):
    """Raised on a missing token or an unexpected API response."""


@dataclass(frozen=True)
class NotificationsPoll:
    """Result of a conditional notifications poll."""

    items: list[dict] | None  # None means 304 Not Modified (unchanged)
    last_modified: str | None
    poll_interval: int


def default_headers(token: str) -> dict[str, str]:
    """Common headers required on every authenticated request."""
    if not token:
        raise GitHubError("no token; run `residualself auth` first")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": config.GITHUB_API_VERSION,
        "User-Agent": config.USER_AGENT,
    }


SEARCH_PATH = "/search/issues"


async def search(
    token: str,
    query: str,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """GET /search/issues for one query; returns the raw ``items`` list.

    Uses advanced search (the GitHub default since 2025-09-04). Pass a shared
    ``client`` to run the four queue queries over one connection.
    """
    if not query:
        raise GitHubError("empty search query")
    headers = default_headers(token)
    params = {
        "q": query,
        "per_page": config.SEARCH_PER_PAGE,
        "advanced_search": "true",
    }
    owns_client = client is None
    if owns_client:
        timeout = httpx.Timeout(config.HTTP_TIMEOUT)
        client = httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout)
    try:
        resp = await client.get(SEARCH_PATH, params=params, headers=headers)
        resp.raise_for_status()  # Rule 7: check the return.
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    items = data.get("items")
    if items is None:
        raise GitHubError("search response missing 'items'")
    return items


NOTIFICATIONS_PATH = "/notifications"


async def list_notifications(
    token: str,
    *,
    show_all: bool = False,
    participating: bool = False,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """GET /notifications — returns the raw notification objects.

    Phase 2 uses this plainly (to attach thread ids). Phase 4 makes it polite
    (If-Modified-Since + X-Poll-Interval + background refresh).
    """
    headers = default_headers(token)
    params = {
        "all": "true" if show_all else "false",
        "participating": "true" if participating else "false",
    }
    owns_client = client is None
    if owns_client:
        timeout = httpx.Timeout(config.HTTP_TIMEOUT)
        client = httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout)
    try:
        resp = await client.get(NOTIFICATIONS_PATH, params=params, headers=headers)
        resp.raise_for_status()  # Rule 7: check the return.
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(data, list):
        raise GitHubError("notifications response was not a list")
    return data


async def fetch_notifications_polled(
    token: str,
    *,
    last_modified: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> NotificationsPoll:
    """Politely poll notifications: send If-Modified-Since, read X-Poll-Interval.

    A 304 (not modified) does not count against the rate limit; in that case
    ``items`` is None and the caller reuses its cache.
    """
    headers = default_headers(token)
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    params = {"all": "false", "participating": "false"}
    owns_client = client is None
    if owns_client:
        timeout = httpx.Timeout(config.HTTP_TIMEOUT)
        client = httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout)
    try:
        resp = await client.get(NOTIFICATIONS_PATH, params=params, headers=headers)
    finally:
        if owns_client:
            await client.aclose()
    interval = int(resp.headers.get("X-Poll-Interval") or config.MIN_POLL_INTERVAL)
    if resp.status_code == 304:
        return NotificationsPoll(None, last_modified, interval)
    resp.raise_for_status()  # Rule 7: check the return.
    data = resp.json()
    if not isinstance(data, list):
        raise GitHubError("notifications response was not a list")
    new_last_modified = resp.headers.get("Last-Modified") or last_modified
    return NotificationsPoll(data, new_last_modified, interval)


async def graphql(
    token: str,
    query: str,
    variables: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """POST /graphql and return the ``data`` object (raising on GraphQL errors)."""
    if not query:
        raise GitHubError("empty GraphQL query")
    headers = default_headers(token)
    payload = {"query": query, "variables": variables or {}}
    owns_client = client is None
    if owns_client:
        timeout = httpx.Timeout(config.HTTP_TIMEOUT)
        client = httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout)
    try:
        resp = await client.post(config.GRAPHQL_PATH, json=payload, headers=headers)
        resp.raise_for_status()  # Rule 7: check the return.
        body = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    if body.get("errors"):
        raise GitHubError(f"GraphQL errors: {body['errors']}")
    return body.get("data") or {}


async def mark_thread_read(
    token: str,
    thread_id: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """PATCH /notifications/threads/{id} — mark a thread read (HTTP 205)."""
    if not thread_id:
        raise GitHubError("missing thread_id")
    headers = default_headers(token)
    path = f"{NOTIFICATIONS_PATH}/threads/{thread_id}"
    owns_client = client is None
    if owns_client:
        timeout = httpx.Timeout(config.HTTP_TIMEOUT)
        client = httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout)
    try:
        resp = await client.patch(path, headers=headers)
        resp.raise_for_status()  # Rule 7: 205 Reset Content is success.
    finally:
        if owns_client:
            await client.aclose()


async def get_authenticated_user(token: str) -> dict:
    """GET /user — returns the authenticated user's profile JSON."""
    headers = default_headers(token)
    timeout = httpx.Timeout(config.HTTP_TIMEOUT)
    async with httpx.AsyncClient(base_url=config.API_BASE, timeout=timeout) as client:
        resp = await client.get("/user", headers=headers)
        resp.raise_for_status()  # Rule 7: check the return.
        data = resp.json()
    if "login" not in data:
        raise GitHubError("unexpected /user response: no 'login' field")
    return data
