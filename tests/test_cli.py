import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from zoneinfo import ZoneInfo

from workipy.cli import (
    DEFAULT_BASE_URL,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    PublicHoliday,
    WorkSchedule,
    build_parser,
    build_url,
    compute_work_summary,
    parse_european_date,
    perform_public_json_request,
    perform_request,
    require_api_key,
    validate_clockify_base_url,
)


class CliTests(unittest.TestCase):
    def test_build_url_normalizes_slashes(self) -> None:
        self.assertEqual(
            build_url("https://api.clockify.me/api/v1/", "workspaces"),
            "https://api.clockify.me/api/v1/workspaces",
        )

    def test_parser_accepts_summary_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "Max Testerman",
                "01-01-2026",
                "31-01-2026",
                "5",
                "5",
                "5",
                "5",
                "0",
            ]
        )
        self.assertEqual(args.user, "Max Testerman")
        self.assertFalse(args.allow_costum_base_url)
        self.assertIsNone(args.api_key_file)

    def test_require_api_key_reads_key_from_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            key_file = Path(tmpdir) / "clockify.key"
            key_file.write_text("secret-key\n", encoding="utf-8")
            self.assertEqual(require_api_key(str(key_file)), "secret-key")

    def test_require_api_key_rejects_missing_file(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            require_api_key("/tmp/does-not-exist-clockify-key")
        self.assertIn("Unable to read API key file", str(exc.exception))

    def test_require_api_key_rejects_empty_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            key_file = Path(tmpdir) / "clockify.key"
            key_file.write_text(" \n", encoding="utf-8")
            with self.assertRaises(SystemExit) as exc:
                require_api_key(str(key_file))
        self.assertIn("is empty", str(exc.exception))

    def test_validate_clockify_base_url_accepts_default_url(self) -> None:
        self.assertEqual(
            validate_clockify_base_url(DEFAULT_BASE_URL, allow_costum_base_url=False),
            DEFAULT_BASE_URL,
        )

    def test_validate_clockify_base_url_rejects_custom_url_without_flag(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            validate_clockify_base_url(
                "https://example.com/api",
                allow_costum_base_url=False,
            )
        self.assertEqual(
            str(exc.exception),
            "Custom Clockify base URLs are disabled. Use "
            "--allow-costum-base-url to override the default API endpoint.",
        )

    def test_validate_clockify_base_url_accepts_custom_https_url_with_flag(self) -> None:
        self.assertEqual(
            validate_clockify_base_url(
                "https://example.com/api/",
                allow_costum_base_url=True,
            ),
            "https://example.com/api",
        )

    def test_validate_clockify_base_url_rejects_non_https_url_even_with_flag(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            validate_clockify_base_url(
                "http://example.com/api",
                allow_costum_base_url=True,
            )
        self.assertEqual(str(exc.exception), "Clockify base URL must use HTTPS.")

    def test_parse_european_date(self) -> None:
        self.assertEqual(parse_european_date("31-01-2026"), date(2026, 1, 31))

    def test_perform_request_uses_timeout(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with mock.patch("workipy.cli.urllib.request.urlopen", return_value=response) as urlopen:
            status, payload = perform_request(
                api_key="secret",
                base_url=DEFAULT_BASE_URL,
                method="GET",
                path="/workspaces",
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload, '{"ok": true}')
        self.assertEqual(urlopen.call_args.kwargs["timeout"], DEFAULT_REQUEST_TIMEOUT_SECONDS)

    def test_perform_public_json_request_uses_timeout(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with mock.patch("workipy.cli.urllib.request.urlopen", return_value=response) as urlopen:
            payload = perform_public_json_request("https://example.com/holidays")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], DEFAULT_REQUEST_TIMEOUT_SECONDS)

    def test_summary_uses_passed_public_holidays_for_target_hours(self) -> None:
        schedule = WorkSchedule(5, 5, 5, 5, 0)
        timezone = ZoneInfo("Europe/Vienna")
        summary, warnings = compute_work_summary(
            entries=[],
            schedule=schedule,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 9),
            public_holidays=[
                PublicHoliday(date(2026, 1, 1), "Neujahr", "New Year's Day"),
                PublicHoliday(date(2026, 1, 6), "Heilige Drei Koenige", "Epiphany"),
            ],
            out_of_office_project_id="ooo",
            task_names={},
            timezone=timezone,
        )
        self.assertEqual(summary.scheduled_hours, 25.0)
        self.assertEqual(summary.public_holiday_credit, 10.0)
        self.assertEqual(summary.target_hours, 15.0)
        self.assertEqual(len(warnings), 2)

    def test_summary_counts_absence_categories_and_balance(self) -> None:
        schedule = WorkSchedule(5, 5, 5, 5, 0)
        timezone = ZoneInfo("Europe/Vienna")
        entries = [
            {
                "projectId": "client",
                "taskId": "dev",
                "timeInterval": {
                    "start": "2026-01-05T08:00:00Z",
                    "end": "2026-01-05T13:00:00Z",
                },
            },
            {
                "projectId": "ooo",
                "taskId": "holiday",
                "timeInterval": {
                    "start": "2026-01-06T08:00:00Z",
                    "end": "2026-01-06T13:00:00Z",
                },
            },
            {
                "projectId": "ooo",
                "taskId": "vac",
                "timeInterval": {
                    "start": "2026-01-07T08:00:00Z",
                    "end": "2026-01-07T13:00:00Z",
                },
            },
            {
                "projectId": "ooo",
                "taskId": "sick",
                "timeInterval": {
                    "start": "2026-01-08T08:00:00Z",
                    "end": "2026-01-08T13:00:00Z",
                },
            },
        ]
        summary, warnings = compute_work_summary(
            entries=entries,
            schedule=schedule,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 8),
            public_holidays=[
                PublicHoliday(date(2026, 1, 6), "Heilige Drei Koenige", "Epiphany"),
            ],
            out_of_office_project_id="ooo",
            task_names={
                "holiday": "Public Holiday",
                "vac": "Vacation",
                "sick": "Sick-Leave",
            },
            timezone=timezone,
        )
        self.assertEqual(summary.target_hours, 15.0)
        self.assertEqual(summary.worked_hours, 5.0)
        self.assertEqual(summary.vacation_hours, 5.0)
        self.assertEqual(summary.sick_leave_hours, 5.0)
        self.assertEqual(summary.special_leave_hours, 0.0)
        self.assertEqual(summary.public_holiday_logged_hours, 5.0)
        self.assertEqual(summary.credited_hours, 20.0)
        self.assertEqual(summary.balance_hours, 0.0)
        self.assertEqual(warnings, [])

    def test_summary_warns_when_public_holiday_booking_mismatches_nominal_hours(self) -> None:
        schedule = WorkSchedule(5, 5, 5, 5, 0)
        timezone = ZoneInfo("Europe/Vienna")
        entries = [
            {
                "projectId": "ooo",
                "taskId": "holiday",
                "timeInterval": {
                    "start": "2026-01-06T08:00:00Z",
                    "end": "2026-01-06T12:00:00Z",
                },
            },
        ]
        summary, warnings = compute_work_summary(
            entries=entries,
            schedule=schedule,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 6),
            public_holidays=[
                PublicHoliday(date(2026, 1, 6), "Heilige Drei Koenige", "Epiphany"),
            ],
            out_of_office_project_id="ooo",
            task_names={"holiday": "Public Holiday"},
            timezone=timezone,
        )
        self.assertEqual(summary.target_hours, 0.0)
        self.assertEqual(summary.public_holiday_logged_hours, 4.0)
        self.assertEqual(
            warnings,
            [
                "Warning: public holiday booking mismatch on 06-01-2026 (Epiphany). "
                "Expected 5 h, booked 4 h."
            ],
        )


if __name__ == "__main__":
    unittest.main()
