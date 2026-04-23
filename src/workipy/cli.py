#!/usr/bin/env python3
"""Command line interface for the Clockify API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.clockify.me/api/v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workipy",
        description="Small CLI for the Clockify API.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("CLOCKIFY_API_KEY"),
        help="Clockify API key. Defaults to CLOCKIFY_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CLOCKIFY_BASE_URL", DEFAULT_BASE_URL),
        help=f"API base URL. Defaults to {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw response bodies instead of pretty JSON.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("me", help="Show the authenticated user.")
    subparsers.add_parser("workspaces", help="List available workspaces.")

    projects = subparsers.add_parser("projects", help="List projects in a workspace.")
    projects.add_argument("--workspace-id", required=True, help="Clockify workspace ID.")

    request_cmd = subparsers.add_parser("request", help="Send an arbitrary API request.")
    request_cmd.add_argument("method", help="HTTP method, for example GET or POST.")
    request_cmd.add_argument("path", help="API path, for example /workspaces or /user.")
    request_cmd.add_argument(
        "--data",
        help="JSON request body as a string.",
    )

    return parser


def require_api_key(api_key: str | None) -> str:
    if api_key:
        return api_key
    raise SystemExit("Missing API key. Set CLOCKIFY_API_KEY or pass --api-key.")


def build_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def perform_request(
    *,
    api_key: str,
    base_url: str,
    method: str,
    path: str,
    data: str | None = None,
) -> tuple[int, str]:
    body = None
    headers = {
        "X-Api-Key": api_key,
        "Accept": "application/json",
    }

    if data is not None:
        try:
            json.loads(data)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON passed to --data: {exc}") from exc
        body = data.encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        build_url(base_url, path),
        data=body,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read().decode("utf-8")
            return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {payload}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc.reason}") from exc


def print_payload(payload: str, raw: bool) -> None:
    if raw:
        print(payload)
        return

    try:
        parsed: Any = json.loads(payload)
    except json.JSONDecodeError:
        print(payload)
        return

    print(json.dumps(parsed, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    api_key = require_api_key(args.api_key)

    if args.command == "me":
        _, payload = perform_request(
            api_key=api_key,
            base_url=args.base_url,
            method="GET",
            path="/user",
        )
        print_payload(payload, args.raw)
        return 0

    if args.command == "workspaces":
        _, payload = perform_request(
            api_key=api_key,
            base_url=args.base_url,
            method="GET",
            path="/workspaces",
        )
        print_payload(payload, args.raw)
        return 0

    if args.command == "projects":
        _, payload = perform_request(
            api_key=api_key,
            base_url=args.base_url,
            method="GET",
            path=f"/workspaces/{args.workspace_id}/projects",
        )
        print_payload(payload, args.raw)
        return 0

    if args.command == "request":
        _, payload = perform_request(
            api_key=api_key,
            base_url=args.base_url,
            method=args.method,
            path=args.path,
            data=args.data,
        )
        print_payload(payload, args.raw)
        return 0

    parser.print_help(sys.stderr)
    return 1
