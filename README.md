# workipy

`workipy` is a small Python CLI for Clockify with a work time summary tailored to Austrian part-time schedules.

It installs a shell command named `workipy` via `project.scripts`, so after installation you can run it directly from your terminal.

## Features

- Zero runtime dependencies
- Reads the API key from a file configured via `CLOCKIFY_API_KEY_FILE`
- Calculates target hours, worked hours, public holiday bookings, and absences for a date range
- Fetches Austrian public holidays from `nager.date`
- Warns when `Public Holiday` bookings do not match nominal hours on a holiday
- Supports direct invocation like `workipy "Max Testerman" 01-01-2026 31-01-2026 5 5 5 5 0`

## Install

```bash
python -m pip install .
```

## Configure

Set your Clockify API key file:

```bash
umask 077
printf '%s\n' 'your-api-key' > ~/.clockify-api-key
export CLOCKIFY_API_KEY_FILE="$HOME/.clockify-api-key"
```

## Clockify Mapping

The project `Out of office` is treated as absence tracking with these tasks:

- `Public Holiday` = public holiday
- `Sick-Leave` = sick leave
- `Special Leave` = paid special leave
- `Vacation` = vacation

All other projects count as actual worked time.

## Austrian Rules Used

The summary uses these practical assumptions:

- the weekly schedule is given explicitly as Monday to Friday hours
- the target time is the scheduled time in the period minus Austrian public holidays from `nager.date` that fall on planned workdays
- `Vacation`, `Sick-Leave` and `Special Leave` count as credited hours
- `Public Holiday` is shown separately, but public holidays reduce the target time instead of being added a second time as worked hours
- each public holiday on a planned workday is checked against the `Public Holiday` booking in Clockify for the same date
- extra hours or missing hours are calculated as credited time minus target time

This is a pragmatic implementation for reporting, not legal advice for every collective agreement or edge case.

## Usage

Direct mode:

```bash
workipy "Max Testerman" 01-01-2026 31-01-2026 5 5 5 5 0
```

The five trailing numbers are the nominal daily hours for Monday through Friday.

If the user exists in multiple workspaces, pass one of:

```bash
workipy "Max Testerman" 01-01-2026 31-01-2026 5 5 5 5 0 --workspace-id YOUR_WORKSPACE_ID
workipy "Max Testerman" 01-01-2026 31-01-2026 5 5 5 5 0 --workspace-name "Your Workspace"
```

Example output:

```text
User: Max Testerman
Workspace: Example Workspace
Period: 01-01-2026 to 31-01-2026
Target hours: 86 h
Worked hours: 80 h
Vacation: 10 h
Sick leave: 0 h
Special leave: 2 h
Public holidays deducted: 10 h
Public holidays logged:   4 h
Credited hours: 92 h
Extra hours: 6 h
Warnings:
Warning: public holiday booking mismatch on 06-01-2026 (Epiphany). Expected 5 h, booked 4 h.
```

Show help:

```bash
workipy --help
```

## Development

Run tests:

```bash
make test
```

Run locally without installation:

```bash
make run RUN_ARGS='"Max Testerman" 01-01-2026 31-01-2026 5 5 5 5 0'
```
