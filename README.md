# workipy

`workipy` is a small Python CLI for the Clockify API.

It installs a real shell command named `workipy` via `project.scripts`, so after installation you can run it directly from your terminal.

## Features

- Zero runtime dependencies
- Reads the API key from `CLOCKIFY_API_KEY`
- Handy built-in commands for common Clockify lookups
- Generic request mode for any endpoint

## Install

```bash
python -m pip install .
```

For local development:

```bash
python -m pip install -e .
```

## Configure

Set your Clockify API key:

```bash
export CLOCKIFY_API_KEY="your-api-key"
```

## Usage

Show help:

```bash
workipy --help
```

Get the authenticated user:

```bash
workipy me
```

List workspaces:

```bash
workipy workspaces
```

List projects for a workspace:

```bash
workipy projects --workspace-id YOUR_WORKSPACE_ID
```

Call any Clockify endpoint:

```bash
workipy request GET /workspaces
workipy request GET /user
```

Send JSON data:

```bash
workipy request POST /workspaces --data '{"name":"Example Workspace"}'
```

## Development

Run the module directly:

```bash
python -m workipy --help
```
