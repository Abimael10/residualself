"""Phase 0 tests: device-flow polling loop + whoami, all mocked (no network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from residualself import auth, config, github_client


@pytest.fixture(autouse=True)
def _client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CLIENT_ID", "test-client-id")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(auth.asyncio, "sleep", _instant)


@respx.mock
async def test_request_device_code_parses_fields() -> None:
    respx.post(config.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dev123",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            },
        )
    )
    async with httpx.AsyncClient() as client:
        device = await auth.request_device_code(client)
    assert device.device_code == "dev123"
    assert device.user_code == "WXYZ-1234"
    assert device.interval == 5


@respx.mock
async def test_poll_for_token_pending_then_success() -> None:
    respx.post(config.ACCESS_TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"error": "authorization_pending"}),
            httpx.Response(200, json={"error": "slow_down", "interval": 5}),
            httpx.Response(200, json={"access_token": "gho_secret"}),
        ]
    )
    async with httpx.AsyncClient() as client:
        token = await auth.poll_for_token(client, "dev123", interval=1, expires_in=900)
    assert token == "gho_secret"


@respx.mock
async def test_poll_for_token_fatal_error_raises() -> None:
    respx.post(config.ACCESS_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"error": "access_denied"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(auth.AuthError):
            await auth.poll_for_token(client, "dev123", interval=1, expires_in=900)


@respx.mock
async def test_get_authenticated_user_returns_login() -> None:
    respx.get(f"{config.API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "octocat", "id": 1})
    )
    user = await github_client.get_authenticated_user("gho_secret")
    assert user["login"] == "octocat"


async def test_get_authenticated_user_requires_token() -> None:
    with pytest.raises(github_client.GitHubError):
        await github_client.get_authenticated_user("")


@respx.mock
async def test_fetch_notifications_polled_returns_items_and_headers() -> None:
    respx.get(url__startswith=f"{config.API_BASE}/notifications").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "t1"}],
            headers={
                "Last-Modified": "Wed, 10 Jun 2026 10:00:00 GMT",
                "X-Poll-Interval": "90",
            },
        )
    )
    result = await github_client.fetch_notifications_polled("tok")
    assert result.items == [{"id": "t1"}]
    assert result.last_modified == "Wed, 10 Jun 2026 10:00:00 GMT"
    assert result.poll_interval == 90


@respx.mock
async def test_fetch_notifications_polled_304_is_unchanged() -> None:
    respx.get(url__startswith=f"{config.API_BASE}/notifications").mock(
        return_value=httpx.Response(304, headers={"X-Poll-Interval": "75"})
    )
    result = await github_client.fetch_notifications_polled("tok", last_modified="prev")
    assert result.items is None  # unchanged -> reuse cache
    assert result.last_modified == "prev"
    assert result.poll_interval == 75


@respx.mock
async def test_graphql_returns_data() -> None:
    respx.post(f"{config.API_BASE}/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"nodes": []}})
    )
    data = await github_client.graphql("tok", "query{ viewer { login } }", {})
    assert data == {"nodes": []}


@respx.mock
async def test_graphql_raises_on_errors() -> None:
    respx.post(f"{config.API_BASE}/graphql").mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "bad"}]})
    )
    with pytest.raises(github_client.GitHubError):
        await github_client.graphql("tok", "query{}", {})
