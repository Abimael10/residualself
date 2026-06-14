"""Command-line entrypoint: residualself [auth | whoami | list | run]."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from . import __version__, auth, github_client, queue, store
from .models import WorkItem


def _configure_logging() -> str:
    """Log INFO+ to a file in the data dir; return its path for the user."""
    log_path = store.default_db_path().parent / "residualself.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger("residualself")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
    return str(log_path)


def cmd_auth(_args: argparse.Namespace) -> int:
    """Walk the OAuth Device Flow and store the token."""
    try:
        token = asyncio.run(auth.device_flow_login())
        user = asyncio.run(github_client.get_authenticated_user(token))
    except auth.AuthError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"Network error during auth: {exc}", file=sys.stderr)
        return 1
    print(f"Logged in as {user['login']}.")
    return 0


def cmd_whoami(_args: argparse.Namespace) -> int:
    """Print the authenticated GitHub login."""
    token = auth.get_token()
    if not token:
        print("Not logged in. Run: residualself auth", file=sys.stderr)
        return 1
    try:
        user = asyncio.run(github_client.get_authenticated_user(token))
    except (github_client.GitHubError, httpx.HTTPError) as exc:
        print(f"Could not fetch user: {exc}", file=sys.stderr)
        return 1
    print(user["login"])
    return 0


def _format_item(item: WorkItem) -> str:
    head = f"[{item.kind.value}] {item.repo}#{item.number}  {item.title}"
    return f"{head}\n    {item.url}  (reason: {item.reason})"


def cmd_list(args: argparse.Namespace) -> int:
    """Fetch and print the actionable queue (debug/inspection view)."""
    token = auth.get_token()
    if not token:
        print("Not logged in. Run: residualself auth", file=sys.stderr)
        return 1
    try:
        items = asyncio.run(queue.fetch(token, include_mentions=args.mentions))
    except (github_client.GitHubError, httpx.HTTPError) as exc:
        print(f"Could not fetch queue: {exc}", file=sys.stderr)
        return 1
    if not items:
        print("Nothing actionable in your queue right now.")
        return 0
    for item in items:
        print(_format_item(item))
    print(f"\n{len(items)} item(s).")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Launch the single-focus TUI."""
    token = auth.get_token()
    if not token:
        print("Not logged in. Run: residualself auth", file=sys.stderr)
        return 1
    from .ui.app import ResidualSelfApp  # imported lazily so non-TUI commands stay light

    log_path = _configure_logging()
    print(f"Polling activity logged to {log_path}")
    app = ResidualSelfApp(token, include_mentions=args.mentions)
    app.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="residualself", description=__doc__)
    parser.add_argument("--version", action="version", version=f"residualself {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="log in via OAuth Device Flow").set_defaults(func=cmd_auth)
    sub.add_parser("whoami", help="print the authenticated GitHub login").set_defaults(
        func=cmd_whoami
    )
    list_parser = sub.add_parser("list", help="list your actionable queue")
    list_parser.add_argument(
        "--mentions", action="store_true", help="also include @-mentions (noisier)"
    )
    list_parser.set_defaults(func=cmd_list)
    run_parser = sub.add_parser("run", help="launch the single-focus TUI")
    run_parser.add_argument(
        "--mentions", action="store_true", help="also include @-mentions (noisier)"
    )
    run_parser.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
