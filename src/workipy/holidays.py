"""Public holiday API client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from workipy.constants import NAGER_DATE_BASE_URL
from workipy.http import perform_public_json_request
from workipy.summary import PublicHoliday


@dataclass(frozen=True)
class HolidayClient:
    base_url: str = NAGER_DATE_BASE_URL

    def request_json(self, url: str) -> Any:
        return perform_public_json_request(url)

    def fetch_public_holidays(self, start_date: date, end_date: date) -> list[PublicHoliday]:
        holidays_by_date: dict[date, PublicHoliday] = {}
        for year in range(start_date.year, end_date.year + 1):
            payload = self.request_json(f"{self.base_url}/publicholidays/{year}/AT")
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
                if not item.get("global"): # bank, not school holiday
                    continue
                holidays_by_date[current_date] = PublicHoliday(
                    current_date=current_date,
                    local_name=item.get("localName", ""),
                    name=item.get("name", ""),
                )
        return [holidays_by_date[current] for current in sorted(holidays_by_date)]


def fetch_public_holidays(start_date: date, end_date: date) -> list[PublicHoliday]:
    return HolidayClient().fetch_public_holidays(start_date, end_date)
