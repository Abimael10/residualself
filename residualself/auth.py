"""OAuth Device Flow login and keyring-backed token storage.

The token lives only in the OS keychain via ``keyring`` — never written to a
file, never logged (SPEC: token hygiene).
"""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import keyring

from . import config


class AuthError(Exception):
    """Raised when the device flow cannot complete."""


@dataclass(frozen=True)
class DeviceCode:
    """The first-step response that the user acts on in their browser."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


def _require(data: dict, key: str) -> str:
    """Return ``data[key]`` or fail loudly (Rule 7: validate the response)."""
    value = data.get(key)
    if not value:
        raise AuthError(f"GitHub device-flow response missing '{key}'")
    return str(value)


def _assert_client_id_configured() -> None:
    """Refuse to run with a placeholder client id (Rule 5: precondition)."""
    if not config.CLIENT_ID or config.CLIENT_ID == config.CLIENT_ID_PLACEHOLDER:
        raise AuthError(
            "CLIENT_ID is not set. Add your OAuth App client id to "
            "residualself/config.py or set the RESIDUALSELF_CLIENT_ID env var."
        )


def _form_headers() -> dict[str, str]:
    return {"Accept": "application/json", "User-Agent": config.USER_AGENT}


async def request_device_code(client: httpx.AsyncClient) -> DeviceCode:
    """Step 1: ask GitHub for a device + user code."""
    _assert_client_id_configured()
    resp = await client.post(
        config.DEVICE_CODE_URL,
        data={"client_id": config.CLIENT_ID, "scope": config.SCOPES},
        headers=_form_headers(),
    )
    resp.raise_for_status()  # Rule 7: check the return.
    data = resp.json()
    interval = int(data.get("interval") or config.DEFAULT_POLL_INTERVAL)
    expires_in = int(data.get("expires_in") or 900)
    return DeviceCode(
        device_code=_require(data, "device_code"),
        user_code=_require(data, "user_code"),
        verification_uri=_require(data, "verification_uri"),
        interval=max(interval, 1),
        expires_in=max(expires_in, 1),
    )


def _max_poll_attempts(expires_in: int, interval: int) -> int:
    """Bound the poll loop (Rule 2) using GitHub's hints, capped hard."""
    assert interval > 0, "interval must be positive"
    assert expires_in > 0, "expires_in must be positive"
    attempts = expires_in // interval + 1
    return min(attempts, config.MAX_POLL_ATTEMPTS)


async def _request_access_token(client: httpx.AsyncClient, device_code: str) -> dict:
    resp = await client.post(
        config.ACCESS_TOKEN_URL,
        data={
            "client_id": config.CLIENT_ID,
            "device_code": device_code,
            "grant_type": config.GRANT_TYPE_DEVICE,
        },
        headers=_form_headers(),
    )
    resp.raise_for_status()  # Rule 7: check the return.
    return resp.json()


_FATAL_ERRORS = {
    "expired_token": "the device code expired; run `residualself auth` again",
    "access_denied": "authorization was denied in the browser",
    "incorrect_client_credentials": "the configured CLIENT_ID is wrong",
}


async def poll_for_token(
    client: httpx.AsyncClient,
    device_code: str,
    interval: int,
    expires_in: int,
) -> str:
    """Step 3: poll the token endpoint within a provably bounded loop."""
    if not device_code:
        raise AuthError("missing device_code")
    interval = max(interval, 1)
    max_attempts = _max_poll_attempts(expires_in, interval)
    for _attempt in range(max_attempts):  # Rule 2: fixed upper bound.
        await asyncio.sleep(interval)
        data = await _request_access_token(client, device_code)
        token = data.get("access_token")
        if token:
            return str(token)
        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += int(data.get("interval") or 5)
            continue
        raise AuthError(_FATAL_ERRORS.get(error, f"device flow failed: {error!r}"))
    raise AuthError("device code expired before authorization completed")


def _show_instructions(device: DeviceCode, prompt: Callable[[str], None]) -> None:
    prompt("\nTo authorize ResidualSelf:")
    prompt(f"  1. Open: {device.verification_uri}")
    prompt(f"  2. Enter code: {device.user_code}\n")
    prompt("Waiting for authorization...")
    try:
        webbrowser.open(device.verification_uri)
    except Exception:  # noqa: BLE001 - opening a browser is best-effort only.
        pass  # Rule 7 exception: a failed browser launch is non-fatal; codes shown above.


async def device_flow_login(prompt: Callable[[str], None] = print) -> str:
    """Run the full device flow and persist the resulting token."""
    timeout = httpx.Timeout(config.HTTP_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        device = await request_device_code(client)
        _show_instructions(device, prompt)
        token = await poll_for_token(
            client, device.device_code, device.interval, device.expires_in
        )
    store_token(token)
    return token


def store_token(token: str) -> None:
    """Persist the token in the OS keychain (never to disk/logs)."""
    if not token:
        raise AuthError("refusing to store an empty token")
    keyring.set_password(config.KEYRING_SERVICE, config.KEYRING_USERNAME, token)


def get_token() -> str | None:
    """Return the stored token, or ``None`` if not logged in."""
    return keyring.get_password(config.KEYRING_SERVICE, config.KEYRING_USERNAME)


def delete_token() -> None:
    """Remove the stored token if present; a missing token is not an error."""
    try:
        keyring.delete_password(config.KEYRING_SERVICE, config.KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # Rule 7 exception: deleting an absent token is intentionally a no-op.
