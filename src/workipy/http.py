"""HTTP helpers for Clockify and public JSON APIs."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from workipy.constants import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MAX_ERROR_PAYLOAD_CHARS,
    MAX_RESPONSE_PAYLOAD_CHARS,
)


def truncate_error_payload(payload: str, *, limit: int = MAX_ERROR_PAYLOAD_CHARS) -> str:
    if len(payload) <= limit:
        return payload
    return f"{payload[:limit]}...[truncated]"


def read_response_payload(response: Any) -> str:
    payload = response.read(MAX_RESPONSE_PAYLOAD_CHARS + 1).decode("utf-8", errors="replace")
    if len(payload) > MAX_RESPONSE_PAYLOAD_CHARS:
        raise SystemExit(
            f"Response payload exceeded the {MAX_RESPONSE_PAYLOAD_CHARS}-character limit."
        )
    return payload


def build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    base = base_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{base}{path}"
    if params:
        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None},
            doseq=True,
        )
        if query:
            url = f"{url}?{query}"
    return url


def perform_request(
    *,
    api_key: str,
    base_url: str,
    method: str,
    path: str,
    data: str | None = None,
    params: dict[str, Any] | None = None,
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
        build_url(base_url, path, params=params),
        data=body,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS) as response:
            payload = read_response_payload(response)
            return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {truncate_error_payload(payload)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc.reason}") from exc


def perform_json_request(
    *,
    api_key: str,
    base_url: str,
    method: str,
    path: str,
    data: str | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    _, payload = perform_request(
        api_key=api_key,
        base_url=base_url,
        method=method,
        path=path,
        data=data,
        params=params,
    )
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Expected JSON response for {path}, got: {truncate_error_payload(payload)}"
        ) from exc


def perform_public_json_request(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS) as response:
            payload = read_response_payload(response)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Holiday API HTTP {exc.code}: {truncate_error_payload(payload)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Holiday API request failed: {exc.reason}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Holiday API returned invalid JSON: {truncate_error_payload(payload)}"
        ) from exc
