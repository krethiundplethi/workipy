#!/usr/bin/env python3
"""Command line interface for Clockify-based work time summaries."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import urllib.parse
from datetime import date, datetime

from workipy.clockify import (
    ClockifyClient,
    fetch_paginated_list,
    fetch_tasks_for_project,
    fetch_time_entries,
    fetch_workspace_details,
    find_project_by_name,
    find_user,
    find_workspace,
    iso_utc,
)
from workipy.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_OUT_OF_OFFICE_PROJECT,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    HOLIDAY_TASK_NAME,
    MAX_API_KEY_FILE_BYTES,
    MAX_ERROR_PAYLOAD_CHARS,
    MAX_PROJECTS,
    MAX_RESPONSE_PAYLOAD_CHARS,
    MAX_TASKS,
    MAX_TIME_ENTRIES,
    MAX_USERS,
    MAX_WORKSPACES,
    NAGER_DATE_BASE_URL,
    SICK_LEAVE_TASK_NAME,
    SPECIAL_LEAVE_TASK_NAME,
    VACATION_TASK_NAME,
)
from workipy.holidays import HolidayClient, fetch_public_holidays
from workipy.http import (
    build_url,
    perform_json_request,
    perform_public_json_request,
    perform_request,
    read_response_payload,
    truncate_error_payload,
)
from workipy.summary import (
    PublicHoliday,
    WorkSchedule,
    WorkSummary,
    check_public_holiday_bookings,
    compute_work_summary,
    day_bounds,
    format_hours,
    get_timezone,
    iter_dates,
    parse_clockify_datetime,
    round_hours,
    split_entry_hours_by_day,
    summarize_entries,
)


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
    clockify = ClockifyClient(api_key=api_key, base_url=base_url)
    holidays = HolidayClient()

    workspace = clockify.find_workspace(
        workspace_id=args.workspace_id,
        workspace_name=args.workspace_name,
        user_name=args.user,
    )
    workspace_id = workspace["id"]

    user = clockify.find_user(
        workspace_id=workspace_id,
        user_name=args.user,
    )

    workspace_details = clockify.fetch_workspace_details(workspace_id=workspace_id)
    timezone = get_timezone(
        workspace_details.get("timeZone")
        or workspace_details.get("workspaceSettings", {}).get("timeZone")
    )

    out_of_office_project = clockify.find_project_by_name(
        workspace_id=workspace_id,
        project_name=args.out_of_office_project,
    )

    task_names: dict[str, str] = {}
    if out_of_office_project is not None:
        task_names = clockify.fetch_tasks_for_project(
            workspace_id=workspace_id,
            project_id=out_of_office_project["id"],
        )

    entries = clockify.fetch_time_entries(
        workspace_id=workspace_id,
        user_id=user["id"],
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
    )

    public_holidays = holidays.fetch_public_holidays(start_date, end_date)

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


__all__ = [
    "ClockifyClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_OUT_OF_OFFICE_PROJECT",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "HOLIDAY_TASK_NAME",
    "HolidayClient",
    "MAX_API_KEY_FILE_BYTES",
    "MAX_ERROR_PAYLOAD_CHARS",
    "MAX_PROJECTS",
    "MAX_RESPONSE_PAYLOAD_CHARS",
    "MAX_TASKS",
    "MAX_TIME_ENTRIES",
    "MAX_USERS",
    "MAX_WORKSPACES",
    "NAGER_DATE_BASE_URL",
    "PublicHoliday",
    "SICK_LEAVE_TASK_NAME",
    "SPECIAL_LEAVE_TASK_NAME",
    "VACATION_TASK_NAME",
    "WorkSchedule",
    "WorkSummary",
    "build_parser",
    "build_url",
    "check_public_holiday_bookings",
    "compute_work_summary",
    "day_bounds",
    "fetch_paginated_list",
    "fetch_public_holidays",
    "fetch_tasks_for_project",
    "fetch_time_entries",
    "fetch_workspace_details",
    "find_project_by_name",
    "find_user",
    "find_workspace",
    "format_hours",
    "get_timezone",
    "handle_balance_command",
    "iso_utc",
    "iter_dates",
    "main",
    "normalize_argv",
    "parse_clockify_datetime",
    "parse_european_date",
    "perform_json_request",
    "perform_public_json_request",
    "perform_request",
    "print_work_summary",
    "read_response_payload",
    "require_api_key",
    "round_hours",
    "split_entry_hours_by_day",
    "summarize_entries",
    "truncate_error_payload",
    "validate_clockify_base_url",
]
