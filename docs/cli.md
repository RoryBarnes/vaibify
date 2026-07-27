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

Create a new project, from a template or from nothing.

```bash
vaibify init [--template NAME] [--name NAME] [--minimal] [--force]
```

| Option           | Description                                   |
|------------------|-----------------------------------------------|
| `--template`     | Template name: `sandbox` or `workflow`            |
| `--name`         | Project name; scaffolds without a template when given alone |
| `--minimal`      | Smallest config that still builds: no optional features, no extra packages |
| `--force`        | Overwrite existing configuration files         |

With `--template`, this copies the template's files and creates
`vaibify.yml` in the current directory. With `--name` alone it writes
only `vaibify.yml`, which is what a script wants:

```bash
mkdir myProject && cd myProject
vaibify init --name myProject --minimal
```

Run with neither option, it lists the available templates and exits.

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

Start the container. By default it attaches a terminal and holds the
shell; `--detach` leaves it running in the background instead, which is
what a script or a CI job needs.

```bash
vaibify start [--detach/-d] [--gui] [--jupyter] [--project/-p NAME]
```

| Option             | Description                                  |
|--------------------|----------------------------------------------|
| `--detach`, `-d`   | Start in the background and return           |
| `--gui`            | Launch the pipeline viewer after starting     |
| `--jupyter`        | Start JupyterLab inside the container         |
| `--project`, `-p`  | Target project name (optional if only one exists) |

A detached container runs idle so you can `vaibify connect` into it; a
COMMAND argument is refused with `--detach` rather than silently
dropped.

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

Report the state of the container, image, and workspace volume — and,
on request, the project's AICS level and everything blocking the next
one.

```bash
vaibify status [--aics] [--json] [--project/-p NAME]
```

| Option             | Description                                  |
|--------------------|----------------------------------------------|
| `--aics`           | Also print the AICS level and its blockers   |
| `--json`           | Emit environment and AICS status as one JSON object |

The level and the blockers come from the same gates the dashboard
renders, read straight from the container, so no browser and no running
hub is needed:

```bash
vaibify status --json | python -c \
  "import json,sys; print(json.load(sys.stdin)['dictAics']['iAICSLevel'])"
```

One criterion is honestly absent. The dashboard polls file modification
times and therefore also evaluates `script-stale`; a single read of the
container has no mtime history, so that criterion is not evaluated here.
The payload says which criteria were skipped in
`listUnevaluatedCriteria` rather than letting the silence read as a
pass.

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

Print a summary of the current project, or details for a single step.
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
CLI counterpart to clicking your sign-off row in a step's Verification
section in the GUI: a researcher records their judgment that a step's
outputs look correct (or don't).

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
browser session at a time: the hub landing page greys out
containers already held by another session, a second tab that tries
to open a held container is refused *"In use in another browser
session"*, and a second `vaibify start -p X` on the same project
refuses to attach. The exclusivity mechanism — the per-claim lease,
the owner-of-record map, the one-live-connection invariant, and the
release triggers — is specified once in the
[architecture reference](architecture.md#single-browser-session-per-container);
that section is the normative source of truth, including the
holder-payload field table.
The **New vaibify window** button on the container hub, project
picker, and Admin menu spawns a detached hub on a free port and
opens it in a new browser tab.

## Session management

Vaibify hub and viewer servers run in the foreground of the terminal
that launched them. Closing a browser tab does not stop them, and
closing the terminal orphans the server, which keeps holding its
session slot and per-container locks until it is reaped. These
commands let you see and stop live sessions. They are the host-side
analog of `jupyter server list` and `jupyter server stop`, and like
those, they run **on the host only** -- they are not invokable from
inside a container.

### `vaibify sessions`

List every live hub and viewer session on the host, with its PID,
role, port, start time, and the container(s) it holds.

```bash
vaibify sessions
```

Each line reports `pid`, `role` (`hub` or `viewer`), `port`, `started`
(the ISO start time), and `containers` (the names locked on that
port). When nothing is running it prints `No live Vaibify sessions.`

### `vaibify sessions stop`

Gracefully stop a session by PID, or every session with `--all`.

```bash
vaibify sessions stop <PID>
vaibify sessions stop --all
```

| Option   | Description                                       |
|----------|---------------------------------------------------|
| `PID`    | The session PID to stop (from `vaibify sessions`) |
| `--all`  | Stop every live session except the current one    |

Stopping sends `SIGTERM`, which lets the server run its graceful
shutdown -- releasing its session slot and any container locks it
holds. `stop` **refuses any PID that is not a known live Vaibify
session**, so it can never signal an unrelated process, and `--all`
**excludes the current session** so a session never stops itself.

## Dashboard actions from the host: `vaibify do`

Everything a researcher can do from the dashboard has a name in the
agent-action catalog (`vaibify/gui/actionCatalog.py`), and every one of
those names is a `vaibify do` subcommand, generated from the catalog
itself. The CLI therefore cannot drift from the dashboard: a new
dashboard action is a new CLI command, and an action the CLI could not
dispatch fails CI
(`testArchitecturalInvariants.py::testEveryCatalogActionHasCliCommand`).

```bash
vaibify do                       # list every action, grouped by category
vaibify do <action> --help       # one action's arguments
vaibify do run-step A09          # run one step through the dashboard's own path
vaibify do push-to-github \
    listFilePaths='["Step/out.csv"]'
vaibify do check-l2-readiness --json
```

| Option             | Description                                  |
|--------------------|----------------------------------------------|
| `--project`, `-p`  | Target project name (optional if only one exists) |
| `--port`           | Port of the vaibify session to drive (required when several are live) |
| `--workflow`       | Container path of the `project.json` to connect |
| `--json`           | Emit one JSON object per line                |
| `--dry-run`        | Print the call that would be made; send nothing |
| `--timeout`        | Seconds to wait on the hub (0 disables the limit) |

Path parameters become positional arguments (`vaibify do
run-test-category A09 sCategory=integrity`); request-body fields are
`key=value` pairs, coerced to numbers, booleans, and JSON by shape. A
whole JSON object also works: `vaibify do declare-ai-model '{"sVendor":
"...", "sModelId": "..."}'`.

**A hub must be running.** These commands drive the same backend the
browser drives, so the dashboard sees every one of them; they are not a
second, parallel way to change a project. Start one with `vaibify` in
another terminal.

**One session per container still holds.** The CLI claims the container's
lease for the duration of one command and releases it afterwards, so a
container currently open in a dashboard tab answers with *"In use in
another browser session"* rather than being taken over.

**The CLI is the researcher lane.** It authenticates with the hub's
shared session token exactly as the browser does. The `bAgentSafe` flag
in the catalog governs what a compromised *in-container agent* may
invoke; it does not restrict the person at their own terminal, so
user-only actions like `clean-outputs` are available here and are marked
in `--help`.

## Publishing

The `vaibify publish` subcommands are **not implemented**. Both print
`Not yet implemented.` and exit.

There is no Settings → Publish pane. This section previously said the
publishing machinery was "already available" through one; it never
existed, and `vaibify/reproducibility/githubWorkflow.py` — the GitHub
Actions generator behind that claim — has no caller anywhere in the
product. Zenodo archiving is real and reachable, but through the AICS
Level 2 workflow in the dashboard, not a publish pane.

### `vaibify publish workflow` *(coming soon)*

Generate a GitHub Actions workflow from `project.json` and `vaibify.yml`.
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
