"""Clockify API client and lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from workipy.constants import (
    MAX_PROJECTS,
    MAX_TASKS,
    MAX_TIME_ENTRIES,
    MAX_USERS,
    MAX_WORKSPACES,
)
from workipy.http import perform_json_request
from workipy.summary import day_bounds


def iso_utc(dt_value: datetime) -> str:
    return dt_value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ClockifyClient:
    api_key: str
    base_url: str

    def request_json(
        self,
        *,
        method: str,
        path: str,
        data: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return perform_json_request(
            api_key=self.api_key,
            base_url=self.base_url,
            method=method,
            path=path,
            data=data,
            params=params,
        )

    def fetch_paginated_list(
        self,
        *,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = 200,
        max_pages: int | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        if max_pages is not None and max_pages < 1:
            raise SystemExit("max_pages must be at least 1.")
        if max_items is not None and max_items < 1:
            raise SystemExit("max_items must be at least 1.")

        page = 1
        items: list[dict[str, Any]] = []
        while True:
            query = dict(params or {})
            query["page"] = page
            query["page-size"] = page_size
            payload = self.request_json(
                method="GET",
                path=path,
                params=query,
            )
            if not isinstance(payload, list):
                raise SystemExit(
                    f"Expected list response for {path}, got {type(payload).__name__}."
                )
            items.extend(payload)
            if len(payload) < page_size:
                return items
            if max_items is not None and len(items) >= max_items:
                raise SystemExit(f"Exceeded max_items limit of {max_items} while fetching {path}.")
            if max_pages is not None and page >= max_pages:
                raise SystemExit(f"Exceeded max_pages limit of {max_pages} while fetching {path}.")
            page += 1

    def find_workspace(
        self,
        *,
        workspace_id: str | None,
        workspace_name: str | None,
        user_name: str,
    ) -> dict[str, Any]:
        workspaces = self.request_json(method="GET", path="/workspaces")
        if not isinstance(workspaces, list):
            raise SystemExit("Expected /workspaces to return a list.")
        if len(workspaces) > MAX_WORKSPACES:
            raise SystemExit(
                f"Exceeded max_items limit of {MAX_WORKSPACES} while fetching /workspaces."
            )

        if workspace_id:
            for workspace in workspaces:
                if workspace.get("id") == workspace_id:
                    return workspace
            raise SystemExit(f"Workspace with id '{workspace_id}' not found.")

        if workspace_name:
            matches = [workspace for workspace in workspaces if workspace.get("name") == workspace_name]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise SystemExit(f"Workspace named '{workspace_name}' not found.")
            raise SystemExit(f"Workspace name '{workspace_name}' is ambiguous. Use --workspace-id.")

        matching_workspaces: list[dict[str, Any]] = []
        for workspace in workspaces:
            users = self.fetch_paginated_list(
                path=f"/workspaces/{workspace['id']}/users",
                max_pages=25,
                max_items=MAX_USERS,
            )
            if any(user.get("name") == user_name for user in users):
                matching_workspaces.append(workspace)

        if len(matching_workspaces) == 1:
            return matching_workspaces[0]
        if not matching_workspaces:
            raise SystemExit(
                f"User '{user_name}' was not found in your accessible workspaces. "
                "Use --workspace-id if needed."
            )
        raise SystemExit(
            f"User '{user_name}' exists in multiple workspaces. Use --workspace-id to disambiguate."
        )

    def find_user(self, *, workspace_id: str, user_name: str) -> dict[str, Any]:
        users = self.fetch_paginated_list(
            path=f"/workspaces/{workspace_id}/users",
            max_pages=25,
            max_items=MAX_USERS,
        )
        matches = [user for user in users if user.get("name") == user_name]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SystemExit(f"User '{user_name}' was not found in workspace '{workspace_id}'.")
        raise SystemExit(f"User name '{user_name}' is ambiguous in workspace '{workspace_id}'.")

    def find_project_by_name(
        self,
        *,
        workspace_id: str,
        project_name: str,
    ) -> dict[str, Any] | None:
        projects = self.fetch_paginated_list(
            path=f"/workspaces/{workspace_id}/projects",
            max_pages=25,
            max_items=MAX_PROJECTS,
        )
        matches = [project for project in projects if project.get("name") == project_name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SystemExit(
                f"Project name '{project_name}' is ambiguous in workspace '{workspace_id}'."
            )
        return None

    def fetch_tasks_for_project(
        self,
        *,
        workspace_id: str,
        project_id: str,
    ) -> dict[str, str]:
        tasks = self.fetch_paginated_list(
            path=f"/workspaces/{workspace_id}/projects/{project_id}/tasks",
            max_pages=50,
            max_items=MAX_TASKS,
        )
        return {task["id"]: task.get("name", "") for task in tasks if "id" in task}

    def fetch_workspace_details(self, *, workspace_id: str) -> dict[str, Any]:
        payload = self.request_json(method="GET", path=f"/workspaces/{workspace_id}")
        if not isinstance(payload, dict):
            raise SystemExit(f"Expected workspace details for '{workspace_id}'.")
        return payload

    def fetch_time_entries(
        self,
        *,
        workspace_id: str,
        user_id: str,
        start_date: date,
        end_date: date,
        timezone: ZoneInfo,
    ) -> list[dict[str, Any]]:
        range_start, _ = day_bounds(start_date, timezone)
        _, range_end = day_bounds(end_date, timezone)
        return self.fetch_paginated_list(
            path=f"/workspaces/{workspace_id}/user/{user_id}/time-entries",
            params={
                "start": iso_utc(range_start),
                "end": iso_utc(range_end),
                "hydrated": "false",
            },
            page_size=500,
            max_pages=200,
            max_items=MAX_TIME_ENTRIES,
        )


def fetch_paginated_list(
    *,
    api_key: str,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    page_size: int = 200,
    max_pages: int | None = None,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    return ClockifyClient(api_key=api_key, base_url=base_url).fetch_paginated_list(
        path=path,
        params=params,
        page_size=page_size,
        max_pages=max_pages,
        max_items=max_items,
    )


def find_workspace(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str | None,
    workspace_name: str | None,
    user_name: str,
) -> dict[str, Any]:
    return ClockifyClient(api_key=api_key, base_url=base_url).find_workspace(
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        user_name=user_name,
    )


def find_user(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    user_name: str,
) -> dict[str, Any]:
    return ClockifyClient(api_key=api_key, base_url=base_url).find_user(
        workspace_id=workspace_id,
        user_name=user_name,
    )


def find_project_by_name(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    project_name: str,
) -> dict[str, Any] | None:
    return ClockifyClient(api_key=api_key, base_url=base_url).find_project_by_name(
        workspace_id=workspace_id,
        project_name=project_name,
    )


def fetch_tasks_for_project(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    project_id: str,
) -> dict[str, str]:
    return ClockifyClient(api_key=api_key, base_url=base_url).fetch_tasks_for_project(
        workspace_id=workspace_id,
        project_id=project_id,
    )


def fetch_workspace_details(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
) -> dict[str, Any]:
    return ClockifyClient(api_key=api_key, base_url=base_url).fetch_workspace_details(
        workspace_id=workspace_id,
    )


def fetch_time_entries(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    user_id: str,
    start_date: date,
    end_date: date,
    timezone: ZoneInfo,
) -> list[dict[str, Any]]:
    return ClockifyClient(api_key=api_key, base_url=base_url).fetch_time_entries(
        workspace_id=workspace_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
    )
