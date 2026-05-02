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

## Project Targeting

> **Note:** When you are in a directory containing `vaibify.yml`, the
> `--project` flag defaults to that project. When only one project is
> registered globally, the flag can be omitted entirely. When multiple
> projects exist, `--project` is required unless you are in a project
> directory. Projects are registered automatically when you run
> `vaibify init`.

## Project Setup

### `vaibify init`

Create a new project from a template.

```bash
vaibify init [--template NAME] [--force]
```

| Option           | Description                                   |
|------------------|-----------------------------------------------|
| `--template`     | Template name: `sandbox` or `workflow`            |
| `--force`        | Overwrite existing configuration files         |

Creates `vaibify.yml`, `container.conf`, and `workflow.json` in the current
directory.

### `vaibify register`

Register an existing project directory in the global registry so it
can be targeted with `--project/-p` from any directory. Unlike
`vaibify init`, this does not create or overwrite any files.

```bash
vaibify register [DIRECTORY]
```

| Argument      | Description                                       |
|---------------|---------------------------------------------------|
| `DIRECTORY`   | Path to the project directory (default: `.`)      |

The directory must contain a `vaibify.yml` file.

### `vaibify setup`

Launch the interactive setup wizard in a browser (port 8051). The wizard
walks through configuration fields and writes the result to `vaibify.yml`.

```bash
vaibify setup
```

### `vaibify config`

Edit, export, or import configuration.

```bash
vaibify config edit                         # Open vaibify.yml in $EDITOR
vaibify config export [-p NAME] <file>      # Write current config to a file
vaibify config import <file>                # Load config from a file
```

## Container Lifecycle

### `vaibify build`

Build the Docker image from `vaibify.yml` and `container.conf`.

```bash
vaibify build [--no-cache] [--project/-p NAME]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--no-cache`       | Force a clean rebuild of all layers      |
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify start`

Start the container in the background.

```bash
vaibify start [--gui] [--jupyter] [--project/-p NAME]
```

| Option             | Description                                  |
|--------------------|----------------------------------------------|
| `--gui`            | Launch the pipeline viewer after starting     |
| `--jupyter`        | Start JupyterLab inside the container         |
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify stop`

Stop the running container. The workspace volume persists.

```bash
vaibify stop [--project/-p NAME]
```

### `vaibify destroy`

Remove the container and optionally delete the workspace volume.

```bash
vaibify destroy [--volumes] [--project/-p NAME]
```

| Option             | Description                                  |
|--------------------|----------------------------------------------|
| `--volumes`        | Also remove the workspace volume             |
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify status`

Report the state of the container, image, and workspace volume.

```bash
vaibify status [--project/-p NAME]
```

## Working with the Container

These commands work from any directory on the host. If you have multiple
projects, specify which one with `--project/-p`. If only one project is
registered, the flag can be omitted.

### `vaibify connect`

Open an interactive shell inside the running container.

```bash
vaibify connect [--project/-p NAME]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify push`

Copy files from the host into the container workspace.

```bash
vaibify push [--project/-p NAME] <source> <destination>
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify pull`

Copy files from the container workspace to the host.

```bash
vaibify pull [--project/-p NAME] <source> <destination>
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify verify`

Run the isolation security audit inside the container. The audit checks
for Docker socket access, privilege escalation paths, exposed ports, and
mounted secrets.

```bash
vaibify verify [--project/-p NAME]
```

### `vaibify ls`

List files in the container workspace. The path defaults to
`/workspace`; relative paths are resolved against `/workspace/`.

```bash
vaibify ls [--project/-p NAME] [--json] [PATH]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--project`, `-p`  | Target project name (optional if only one exists) |
| `--json`           | Emit a JSON array instead of one filename per line |
| `PATH`             | Directory to list (default: `/workspace`) |

### `vaibify cat`

Print the contents of a file inside the container. Relative paths are
resolved against `/workspace/`.

```bash
vaibify cat [--project/-p NAME] PATH
```

### `vaibify run`

Execute pipeline steps inside the container. Without options, runs every
step from the beginning. Use `--step` to run one step in isolation, or
`--from` to resume from a specific step. The two are mutually exclusive.

```bash
vaibify run [--project/-p NAME] [--step N | --from N]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--step N`         | Run only step N (1-based)                |
| `--from N`         | Run step N and every step after it       |
| `--project`, `-p`  | Target project name (optional if only one exists) |

Step progress, per-step pass/fail, and pipeline outcome are printed to
stdout as the run progresses.

### `vaibify workflow`

Print a summary of the current workflow, or details for a single step.
Without `--step`, emits a table of all steps with their last verification
status and run timestamp. With `--step N`, emits the step's name,
directory, run flags, and verification block.

```bash
vaibify workflow [--project/-p NAME] [--step N] [--json]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--step N`         | Show details for step N (1-based) only   |
| `--json`           | Emit JSON instead of a human-readable table |
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify test`

Run the test commands attached to one or all pipeline steps. Without
`--step`, every step's tests run in order. The exit code is non-zero if
any step's tests fail.

```bash
vaibify test [--project/-p NAME] [--step N] [--json]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--step N`         | Run tests for step N only (1-based)      |
| `--json`           | Emit JSON results instead of a summary table |
| `--project`, `-p`  | Target project name (optional if only one exists) |

### `vaibify verify-step`

Set the user-verification status for a single pipeline step. This is the
CLI counterpart to clicking a verification badge in the GUI: a researcher
records their judgment that a step's outputs look correct (or don't).

```bash
vaibify verify-step --step N --status STATUS [--project/-p NAME]
```

| Option             | Description                              |
|--------------------|------------------------------------------|
| `--step N`         | Step number (1-based, required)          |
| `--status STATUS`  | One of `passed`, `failed`, `untested` (required) |
| `--project`, `-p`  | Target project name (optional if only one exists) |

## GUI and Pipeline

### `vaibify gui`

Launch the pipeline viewer in a browser (port 8050 by default). When
run without a project, the landing page opens and displays all
registered containers. Use the **+** button to add an existing
project or create a new one. See [The Dashboard](dashboard.md) for
details.

```bash
vaibify gui [--project/-p NAME] [--port N]
```

### Multiple sessions

Several vaibify instances can run on the same host. Typing
`vaibify` twice in two terminals does not collide — the second
invocation auto-shifts to the next free port (8051, 8052, …) and
announces the fallback on stderr. Pass `--port N` to pin an
explicit port. Any given container may be accessed by only one
vaibify session at a time: the hub landing page greys out
containers already held by another session, and a second
`vaibify start -p X` on the same project refuses to attach.
The **New vaibify window** button on the container hub, workflow
picker, and Admin menu spawns a detached hub on a free port and
opens it in a new browser tab.

## Publishing

The `vaibify publish` subcommands are **coming soon**. The publishing
machinery (Zenodo archive, GitHub Actions workflow generation) is
already available through the GUI's Settings → Publish pane; the
CLI counterparts will land in a future release. Until then, both
subcommands print `Not yet implemented.` and exit.

### `vaibify publish workflow` *(coming soon)*

Generate a GitHub Actions workflow from `workflow.json` and `vaibify.yml`.
The output will be written to `.github/workflows/vaibify.yml`.

```bash
vaibify publish workflow
```

### `vaibify publish archive` *(coming soon)*

Package pipeline outputs and upload them to Zenodo (or the Zenodo sandbox).
Will return a DOI on success.

```bash
vaibify publish archive
```
