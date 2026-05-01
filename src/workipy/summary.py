"""Work-time summary domain logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from workipy.constants import (
    HOLIDAY_TASK_NAME,
    SICK_LEAVE_TASK_NAME,
    SPECIAL_LEAVE_TASK_NAME,
    VACATION_TASK_NAME,
)


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


def day_bounds(current_date: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(current_date, time.min, timezone)
    end_dt = start_dt + timedelta(days=1)
    return start_dt, end_dt


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
