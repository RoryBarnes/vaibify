# CLI Reference

Vaibify provides two equivalent entry points: `vaibify` and the shorthand
`vaib`. All commands accept a `--config` flag to specify an alternate
`vaibify.yml` path.

## Global Options

| Flag                  | Description                              |
|-----------------------|------------------------------------------|
| `--config PATH`       | Path to `vaibify.yml` (default: `./vaibify.yml`) |
| `--version`           | Print the installed version and exit     |
| `--help`              | Show the help message and exit           |

When invoked with no subcommand, Vaibify starts in **hub mode** -- a
browser-based dashboard for managing multiple projects.

## Project Setup

### `vaibify init`

Create a new project from a template.

```bash
vaibify init [--template NAME] [--force]
```

| Option           | Description                                   |
|------------------|-----------------------------------------------|
| `--template`     | Template name: `general` or `reproducible-paper` |
| `--force`        | Overwrite existing configuration files         |

Creates `vaibify.yml`, `container.conf`, and `workflow.json` in the current
directory.

### `vaibify setup`

Launch the interactive setup wizard in a browser (port 8051). The wizard
walks through configuration fields and writes the result to `vaibify.yml`.

```bash
vaibify setup
```

### `vaibify config`

Edit, export, or import configuration.

```bash
vaibify config edit              # Open vaibify.yml in $EDITOR
vaibify config export <file>     # Write current config to a file
vaibify config import <file>     # Load config from a file
```

## Container Lifecycle

### `vaibify build`

Build the Docker image from `vaibify.yml` and `container.conf`.

```bash
vaibify build [--no-cache]
```

| Option         | Description                              |
|----------------|------------------------------------------|
| `--no-cache`   | Force a clean rebuild of all layers      |

### `vaibify start`

Start the container in the background.

```bash
vaibify start [--gui] [--jupyter]
```

| Option       | Description                                  |
|--------------|----------------------------------------------|
| `--gui`      | Launch the pipeline viewer after starting     |
| `--jupyter`  | Start JupyterLab inside the container         |

### `vaibify stop`

Stop the running container. The workspace volume persists.

```bash
vaibify stop
```

### `vaibify destroy`

Remove the container and optionally delete the workspace volume.

```bash
vaibify destroy [--volumes]
```

| Option       | Description                                  |
|--------------|----------------------------------------------|
| `--volumes`  | Also remove the workspace volume             |

### `vaibify status`

Report the state of the container, image, and workspace volume.

```bash
vaibify status
```

## Working with the Container

### `vaibify connect`

Open an interactive shell inside the running container.

```bash
vaibify connect
```

### `vaibify push`

Copy files from the host into the container workspace.

```bash
vaibify push <source> <destination>
```

### `vaibify pull`

Copy files from the container workspace to the host.

```bash
vaibify pull <source> <destination>
```

### `vaibify verify`

Run the isolation security audit inside the container. The audit checks
for Docker socket access, privilege escalation paths, exposed ports, and
mounted secrets.

```bash
vaibify verify
```

## GUI and Pipeline

### `vaibify gui`

Launch the pipeline viewer in a browser (port 8050). See [Workflow
Viewer](gui.md) for details.

```bash
vaibify gui
```

## Publishing

### `vaibify publish workflow`

Generate a GitHub Actions workflow from `workflow.json` and `vaibify.yml`.
The output is written to `.github/workflows/vaibify.yml`.

```bash
vaibify publish workflow
```

### `vaibify publish archive`

Package pipeline outputs and upload them to Zenodo (or the Zenodo sandbox).
Returns a DOI on success.

```bash
vaibify publish archive
```
