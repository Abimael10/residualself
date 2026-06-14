"""Configuration: client id, scopes, defaults, poll settings.

The OAuth App Client ID must be filled in by the human (see SPEC.md
"Prerequisites"). Device Flow needs only the client id — no client secret.
"""

from __future__ import annotations

import os

from . import __version__

# --- OAuth App ---------------------------------------------------------------
# Replace with your OAuth App's Client ID (Settings -> Developer settings ->
# OAuth Apps -> New, with "Device Flow" enabled). Can also be overridden via the
# RESIDUALSELF_CLIENT_ID environment variable for local testing.
CLIENT_ID: str = os.environ.get("RESIDUALSELF_CLIENT_ID", "YOUR_CLIENT_ID_HERE")
CLIENT_ID_PLACEHOLDER: str = "YOUR_CLIENT_ID_HERE"

# Classic OAuth scopes. Public-only users may swap "repo" for "public_repo".
SCOPES: str = "notifications read:user repo"

# --- HTTP --------------------------------------------------------------------
USER_AGENT: str = f"residualself/{__version__} (+https://github.com/Abimael10/residualself)"
API_BASE: str = "https://api.github.com"
GITHUB_API_VERSION: str = "2022-11-28"
HTTP_TIMEOUT: float = 30.0

# Device Flow endpoints (github.com, not api.github.com).
DEVICE_CODE_URL: str = "https://github.com/login/device/code"
ACCESS_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
GRANT_TYPE_DEVICE: str = "urn:ietf:params:oauth:grant-type:device_code"

# --- Token storage (keyring) -------------------------------------------------
# The token lives in the OS keychain. Never written to a file, never logged.
KEYRING_SERVICE: str = "residualself"
KEYRING_USERNAME: str = "github-oauth-token"

# --- Search ------------------------------------------------------------------
# Results per search query. Search has a separate, lower rate limit (~30/min),
# so we keep a single page per bucket and never spam it.
SEARCH_PER_PAGE: int = 50

# --- GraphQL enrichment ------------------------------------------------------
GRAPHQL_PATH: str = "/graphql"
# Max node ids per GraphQL request (keeps the points budget modest).
GRAPHQL_BATCH: int = 50

# --- Notifications polling ---------------------------------------------------
# Never poll notifications faster than this, regardless of X-Poll-Interval.
MIN_POLL_INTERVAL: int = 60

# --- Polling -----------------------------------------------------------------
# Fallback poll interval (seconds) used until GitHub tells us otherwise.
DEFAULT_POLL_INTERVAL: int = 5
# Rule 2 (Power of Ten): an absolute ceiling on device-flow poll iterations,
# independent of any value GitHub returns, so the loop is provably bounded.
MAX_POLL_ATTEMPTS: int = 600
