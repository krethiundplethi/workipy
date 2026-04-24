#!/usr/bin/env python3
"""Command line interface for Clockify-based work time summaries."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_BASE_URL = "https://api.clockify.me/api/v1"
NAGER_DATE_BASE_URL = "https://date.nager.at/api/v3"
DEFAULT_OUT_OF_OFFICE_PROJECT = "Out of office"
HOLIDAY_TASK_NAME = "Public Holiday"
SICK_LEAVE_TASK_NAME = "Sick-Leave"
SPECIAL_LEAVE_TASK_NAME = "Special Leave"
VACATION_TASK_NAME = "Vacation"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
MAX_ERROR_PAYLOAD_CHARS = 4096
MAX_API_KEY_FILE_BYTES = 4096
MAX_RESPONSE_PAYLOAD_CHARS = 1_048_576
MAX_WORKSPACES = 200
MAX_USERS = 5_000
MAX_PROJECTS = 5_000
MAX_TASKS = 10_000
MAX_TIME_ENTRIES = 100_000


@dataclass(frozen=True)
class WorkSchedule:
    monday: float
    tuesday: float
    wednesday: float
    thursday: float
    friday: float

    def hours_for_date(self, current_date: date) -> float:
        weekday = current_date.weekday()
        if weekday == 0:
            return self.monday
        if weekday == 1:
            return self.tuesday
        if weekday == 2:
            return self.wednesday
        if weekday == 3:
            return self.thursday
        if weekday == 4:
            return self.friday
        return 0.0


@dataclass(frozen=True)
class WorkSummary:
    scheduled_hours: float
    public_holiday_credit: float
    target_hours: float
    worked_hours: float
    vacation_hours: float
    sick_leave_hours: float
    special_leave_hours: float
    public_holiday_logged_hours: float
    credited_hours: float
    balance_hours: float


@dataclass(frozen=True)
class PublicHoliday:
    current_date: date
    local_name: str
    name: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workipy",
        description="Clockify work time summary with Austrian part-time rules.",
    )
    parser.add_argument(
        "--api-key-file",
        default=os.getenv("CLOCKIFY_API_KEY_FILE"),
        help="Path to a file containing the Clockify API key. Defaults to CLOCKIFY_API_KEY_FILE.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CLOCKIFY_BASE_URL", DEFAULT_BASE_URL),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-custom-base-url",
        action="store_true",
        help="Allow a custom Clockify base URL. Only HTTPS URLs are accepted.",
    )
    parser.add_argument("user", help='Clockify user display name, for example "Max Testerman".')
    parser.add_argument("start", help="Start date in DD-MM-YYYY format.")
    parser.add_argument("end", help="End date in DD-MM-YYYY format.")
    parser.add_argument("monday", type=float, help="Scheduled Monday hours.")
    parser.add_argument("tuesday", type=float, help="Scheduled Tuesday hours.")
    parser.add_argument("wednesday", type=float, help="Scheduled Wednesday hours.")
    parser.add_argument("thursday", type=float, help="Scheduled Thursday hours.")
    parser.add_argument("friday", type=float, help="Scheduled Friday hours.")
    parser.add_argument(
        "--workspace-id",
        help="Clockify workspace ID. Optional, but recommended when names are ambiguous.",
    )
    parser.add_argument(
        "--workspace-name",
        help="Clockify workspace name. Used if no --workspace-id is provided.",
    )
    parser.add_argument(
        "--out-of-office-project",
        default=DEFAULT_OUT_OF_OFFICE_PROJECT,
        help=f'Project name for absences. Defaults to "{DEFAULT_OUT_OF_OFFICE_PROJECT}".',
    )

    return parser


def normalize_argv(argv: list[str] | None) -> list[str]:
    return argv if argv is not None else sys.argv[1:]


def require_api_key(api_key_file: str | None) -> str:
    if not api_key_file:
        raise SystemExit("Missing API key file. Set CLOCKIFY_API_KEY_FILE or pass --api-key-file.")

    try:
        file_stat = os.stat(api_key_file)
    except OSError as exc:
        raise SystemExit(f"Unable to read API key file '{api_key_file}': {exc.strerror}.") from exc

    if not stat.S_ISREG(file_stat.st_mode):
        raise SystemExit(f"API key file '{api_key_file}' must be a regular file.")
    if file_stat.st_mode & 0o077:
        raise SystemExit(
            f"API key file '{api_key_file}' must not be accessible by group or others."
        )
    if file_stat.st_size > MAX_API_KEY_FILE_BYTES:
        raise SystemExit(
            f"API key file '{api_key_file}' exceeds the {MAX_API_KEY_FILE_BYTES}-byte limit."
        )

    try:
        with open(api_key_file, encoding="utf-8") as handle:
            api_key = handle.read(MAX_API_KEY_FILE_BYTES + 1).strip()
    except OSError as exc:
        raise SystemExit(f"Unable to read API key file '{api_key_file}': {exc.strerror}.") from exc

    if len(api_key) > MAX_API_KEY_FILE_BYTES:
        raise SystemExit(
            f"API key file '{api_key_file}' exceeds the {MAX_API_KEY_FILE_BYTES}-byte limit."
        )
    if not api_key:
        raise SystemExit(f"API key file '{api_key_file}' is empty.")

    return api_key


def validate_clockify_base_url(base_url: str, *, allow_custom_base_url: bool) -> str:
    normalized_base_url = base_url.rstrip("/")
    parsed = urllib.parse.urlparse(normalized_base_url)

    if parsed.scheme != "https":
        raise SystemExit("Clockify base URL must use HTTPS.")
    if not parsed.netloc:
        raise SystemExit("Clockify base URL must be an absolute HTTPS URL.")
    if not allow_custom_base_url and normalized_base_url != DEFAULT_BASE_URL:
        raise SystemExit(
            "Custom Clockify base URLs are disabled. Use "
            "--allow-custom-base-url to override the default API endpoint."
        )

    return normalized_base_url


def parse_european_date(raw_value: str) -> date:
    try:
        return datetime.strptime(raw_value, "%d-%m-%Y").date()
    except ValueError as exc:
        raise SystemExit(f"Invalid date '{raw_value}'. Expected DD-MM-YYYY.") from exc


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
        payload = perform_json_request(
            api_key=api_key,
            base_url=base_url,
            method="GET",
            path=path,
            params=query,
        )
        if not isinstance(payload, list):
            raise SystemExit(f"Expected list response for {path}, got {type(payload).__name__}.")
        items.extend(payload)
        if len(payload) < page_size:
            return items
        if max_items is not None and len(items) >= max_items:
            raise SystemExit(f"Exceeded max_items limit of {max_items} while fetching {path}.")
        if max_pages is not None and page >= max_pages:
            raise SystemExit(f"Exceeded max_pages limit of {max_pages} while fetching {path}.")
        page += 1


def get_timezone(tz_name: str | None) -> ZoneInfo:
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            pass
    return ZoneInfo("Europe/Vienna")


def parse_clockify_datetime(raw_value: str) -> datetime:
    if raw_value.endswith("Z"):
        raw_value = raw_value[:-1] + "+00:00"
    return datetime.fromisoformat(raw_value)


def iter_dates(start_date: date, end_date: date) -> list[date]:
    total_days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(total_days + 1)]

def fetch_public_holidays(start_date: date, end_date: date) -> list[PublicHoliday]:
    holidays_by_date: dict[date, PublicHoliday] = {}
    for year in range(start_date.year, end_date.year + 1):
        payload = perform_public_json_request(
            f"{NAGER_DATE_BASE_URL}/publicholidays/{year}/AT"
        )
        if not isinstance(payload, list):
            raise SystemExit(f"Holiday API returned {type(payload).__name__}, expected a list.")

        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_date = item.get("date")
            if not raw_date:
                continue
            current_date = date.fromisoformat(raw_date)
            if not (start_date <= current_date <= end_date):
                continue
            holidays_by_date[current_date] = PublicHoliday(
                current_date=current_date,
                local_name=item.get("localName", ""),
                name=item.get("name", ""),
            )
    return [holidays_by_date[current] for current in sorted(holidays_by_date)]


def day_bounds(current_date: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(current_date, time.min, timezone)
    end_dt = start_dt + timedelta(days=1)
    return start_dt, end_dt


def iso_utc(dt_value: datetime) -> str:
    return dt_value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def round_hours(value: float) -> float:
    return round(value + 1e-9, 2)


def format_hours(value: float) -> str:
    value = round_hours(value)
    if value.is_integer():
        return f"{int(value)} h"
    return f"{value:.2f} h"


def split_entry_hours_by_day(
    entry: dict[str, Any],
    timezone: ZoneInfo,
    start_date: date,
    end_date: date,
) -> dict[date, float]:
    time_interval = entry.get("timeInterval", {})
    start_raw = time_interval.get("start")
    end_raw = time_interval.get("end")
    if not start_raw or not end_raw:
        return {}

    start_dt = parse_clockify_datetime(start_raw).astimezone(timezone)
    end_dt = parse_clockify_datetime(end_raw).astimezone(timezone)
    if end_dt <= start_dt:
        return {}

    range_start, _ = day_bounds(start_date, timezone)
    _, range_end = day_bounds(end_date, timezone)
    clipped_start = max(start_dt, range_start)
    clipped_end = min(end_dt, range_end)
    if clipped_end <= clipped_start:
        return {}

    hours_by_day: dict[date, float] = {}
    cursor = clipped_start
    while cursor < clipped_end:
        next_midnight, _ = day_bounds(cursor.date(), timezone)
        day_end = next_midnight + timedelta(days=1)
        segment_end = min(day_end, clipped_end)
        duration_hours = (segment_end - cursor).total_seconds() / 3600
        hours_by_day[cursor.date()] = hours_by_day.get(cursor.date(), 0.0) + duration_hours
        cursor = segment_end
    return hours_by_day


def find_workspace(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str | None,
    workspace_name: str | None,
    user_name: str,
) -> dict[str, Any]:
    workspaces = perform_json_request(
        api_key=api_key,
        base_url=base_url,
        method="GET",
        path="/workspaces",
    )
    if not isinstance(workspaces, list):
        raise SystemExit("Expected /workspaces to return a list.")
    if len(workspaces) > MAX_WORKSPACES:
        raise SystemExit(f"Exceeded max_items limit of {MAX_WORKSPACES} while fetching /workspaces.")

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
        users = fetch_paginated_list(
            api_key=api_key,
            base_url=base_url,
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


def find_user(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    user_name: str,
) -> dict[str, Any]:
    users = fetch_paginated_list(
        api_key=api_key,
        base_url=base_url,
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
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    project_name: str,
) -> dict[str, Any] | None:
    projects = fetch_paginated_list(
        api_key=api_key,
        base_url=base_url,
        path=f"/workspaces/{workspace_id}/projects",
        max_pages=25,
        max_items=MAX_PROJECTS,
    )
    matches = [project for project in projects if project.get("name") == project_name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(f"Project name '{project_name}' is ambiguous in workspace '{workspace_id}'.")
    return None


def fetch_tasks_for_project(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
    project_id: str,
) -> dict[str, str]:
    tasks = fetch_paginated_list(
        api_key=api_key,
        base_url=base_url,
        path=f"/workspaces/{workspace_id}/projects/{project_id}/tasks",
        max_pages=50,
        max_items=MAX_TASKS,
    )
    return {task["id"]: task.get("name", "") for task in tasks if "id" in task}


def fetch_workspace_details(
    *,
    api_key: str,
    base_url: str,
    workspace_id: str,
) -> dict[str, Any]:
    payload = perform_json_request(
        api_key=api_key,
        base_url=base_url,
        method="GET",
        path=f"/workspaces/{workspace_id}",
    )
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected workspace details for '{workspace_id}'.")
    return payload


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
    range_start, _ = day_bounds(start_date, timezone)
    _, range_end = day_bounds(end_date, timezone)
    return fetch_paginated_list(
        api_key=api_key,
        base_url=base_url,
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


def summarize_entries(
    *,
    entries: list[dict[str, Any]],
    out_of_office_project_id: str | None,
    task_names: dict[str, str],
    timezone: ZoneInfo,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, float], dict[date, float]]:
    totals = {
        "worked_hours": 0.0,
        "vacation_hours": 0.0,
        "sick_leave_hours": 0.0,
        "special_leave_hours": 0.0,
        "public_holiday_logged_hours": 0.0,
    }
    public_holiday_hours_by_day: dict[date, float] = {}
    for entry in entries:
        hours_by_day = split_entry_hours_by_day(entry, timezone, start_date, end_date)
        hours = sum(hours_by_day.values())
        if hours <= 0:
            continue

        project_id = entry.get("projectId")
        task_name = task_names.get(entry.get("taskId", ""), "")
        if project_id == out_of_office_project_id:
            if task_name == VACATION_TASK_NAME:
                totals["vacation_hours"] += hours
            elif task_name == SICK_LEAVE_TASK_NAME:
                totals["sick_leave_hours"] += hours
            elif task_name == SPECIAL_LEAVE_TASK_NAME:
                totals["special_leave_hours"] += hours
            elif task_name == HOLIDAY_TASK_NAME:
                totals["public_holiday_logged_hours"] += hours
                for current_date, day_hours in hours_by_day.items():
                    public_holiday_hours_by_day[current_date] = (
                        public_holiday_hours_by_day.get(current_date, 0.0) + day_hours
                    )
            continue

        totals["worked_hours"] += hours

    return (
        {key: round_hours(value) for key, value in totals.items()},
        {current: round_hours(hours) for current, hours in public_holiday_hours_by_day.items()},
    )


def check_public_holiday_bookings(
    public_holidays: list[PublicHoliday],
    schedule: WorkSchedule,
    public_holiday_hours_by_day: dict[date, float],
) -> list[str]:
    warnings: list[str] = []
    for holiday in public_holidays:
        nominal_hours = round_hours(schedule.hours_for_date(holiday.current_date))
        if nominal_hours <= 0:
            continue
        booked_hours = round_hours(public_holiday_hours_by_day.get(holiday.current_date, 0.0))
        if booked_hours != nominal_hours:
            holiday_name = holiday.name or holiday.local_name or "Public holiday"
            warnings.append(
                f"Warning: public holiday booking mismatch on "
                f"{holiday.current_date.strftime('%d-%m-%Y')} ({holiday_name}). "
                f"Expected {format_hours(nominal_hours)}, booked {format_hours(booked_hours)}."
            )
    return warnings


def compute_work_summary(
    *,
    entries: list[dict[str, Any]],
    schedule: WorkSchedule,
    start_date: date,
    end_date: date,
    public_holidays: list[PublicHoliday],
    out_of_office_project_id: str | None,
    task_names: dict[str, str],
    timezone: ZoneInfo,
) -> tuple[WorkSummary, list[str]]:
    scheduled_hours = 0.0
    public_holiday_credit = 0.0
    holiday_dates = {holiday.current_date for holiday in public_holidays}
    for current_date in iter_dates(start_date, end_date):
        hours = schedule.hours_for_date(current_date)
        scheduled_hours += hours
        if current_date in holiday_dates:
            public_holiday_credit += hours
    target_hours = scheduled_hours - public_holiday_credit
    entry_totals, public_holiday_hours_by_day = summarize_entries(
        entries=entries,
        out_of_office_project_id=out_of_office_project_id,
        task_names=task_names,
        timezone=timezone,
        start_date=start_date,
        end_date=end_date,
    )
    credited_hours = (
        entry_totals["worked_hours"]
        + entry_totals["vacation_hours"]
        + entry_totals["sick_leave_hours"]
        + entry_totals["special_leave_hours"]
        + public_holiday_credit
    )
    balance_hours = credited_hours - scheduled_hours
    summary = WorkSummary(
        scheduled_hours=round_hours(scheduled_hours),
        public_holiday_credit=round_hours(public_holiday_credit),
        target_hours=round_hours(target_hours),
        worked_hours=entry_totals["worked_hours"],
        vacation_hours=entry_totals["vacation_hours"],
        sick_leave_hours=entry_totals["sick_leave_hours"],
        special_leave_hours=entry_totals["special_leave_hours"],
        public_holiday_logged_hours=entry_totals["public_holiday_logged_hours"],
        credited_hours=round_hours(credited_hours),
        balance_hours=round_hours(balance_hours),
    )
    warnings = check_public_holiday_bookings(
        public_holidays,
        schedule,
        public_holiday_hours_by_day,
    )
    return summary, warnings


def print_work_summary(
    summary: WorkSummary,
    warnings: list[str],
    *,
    user_name: str,
    start_date: date,
    end_date: date,
    workspace_name: str,
) -> None:
    print(f"User: {user_name}")
    print(f"Workspace: {workspace_name}")
    print(f"Period: {start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}")
    print(f"Target hours: {format_hours(summary.target_hours)}")
    print(f"Worked hours: {format_hours(summary.worked_hours)}")
    print(f"Vacation: {format_hours(summary.vacation_hours)}")
    print(f"Sick leave: {format_hours(summary.sick_leave_hours)}")
    print(f"Special leave: {format_hours(summary.special_leave_hours)}")
    print(f"Public holidays deducted: {format_hours(summary.public_holiday_credit)}")
    if summary.public_holiday_logged_hours:
        print(f"Public holidays logged:   {format_hours(summary.public_holiday_logged_hours)}")
    print(f"Credited hours: {format_hours(summary.credited_hours)}")
    if summary.balance_hours >= 0:
        print(f"Extra hours: {format_hours(summary.balance_hours)}")
    else:
        print(f"Missing hours: {format_hours(abs(summary.balance_hours))}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(warning)


def handle_balance_command(args: argparse.Namespace) -> int:
    api_key = require_api_key(args.api_key_file)
    base_url = validate_clockify_base_url(
        args.base_url,
        allow_custom_base_url=args.allow_custom_base_url,
    )
    start_date = parse_european_date(args.start)
    end_date = parse_european_date(args.end)
    if end_date < start_date:
        raise SystemExit("End date must not be before start date.")

    schedule = WorkSchedule(
        monday=args.monday,
        tuesday=args.tuesday,
        wednesday=args.wednesday,
        thursday=args.thursday,
        friday=args.friday,
    )

    workspace = find_workspace(
        api_key=api_key,
        base_url=base_url,
        workspace_id=args.workspace_id,
        workspace_name=args.workspace_name,
        user_name=args.user,
    )
    workspace_id = workspace["id"]

    user = find_user(
        api_key=api_key,
        base_url=base_url,
        workspace_id=workspace_id,
        user_name=args.user,
    )

    workspace_details = fetch_workspace_details(
        api_key=api_key,
        base_url=base_url,
        workspace_id=workspace_id,
    )

    timezone = get_timezone(workspace_details.get("timeZone") or workspace_details.get("workspaceSettings", {}).get("timeZone"))

    out_of_office_project = find_project_by_name(
        api_key=api_key,
        base_url=base_url,
        workspace_id=workspace_id,
        project_name=args.out_of_office_project,
    )

    task_names: dict[str, str] = {}
    if out_of_office_project is not None:
        task_names = fetch_tasks_for_project(
            api_key=api_key,
            base_url=base_url,
            workspace_id=workspace_id,
            project_id=out_of_office_project["id"],
        )

    entries = fetch_time_entries(
        api_key=api_key,
        base_url=base_url,
        workspace_id=workspace_id,
        user_id=user["id"],
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
    )

    public_holidays = fetch_public_holidays(start_date, end_date)

    summary, warnings = compute_work_summary(
        entries=entries,
        schedule=schedule,
        start_date=start_date,
        end_date=end_date,
        public_holidays=public_holidays,
        out_of_office_project_id=out_of_office_project["id"] if out_of_office_project else None,
        task_names=task_names,
        timezone=timezone,
    )

    print_work_summary(
        summary,
        warnings,
        user_name=args.user,
        start_date=start_date,
        end_date=end_date,
        workspace_name=workspace.get("name", workspace_id),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    return handle_balance_command(args)
