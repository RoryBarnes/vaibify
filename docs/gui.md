# Workflow Viewer

The workflow viewer is a browser-based GUI for managing and executing
pipelines inside a Vaibify container. Launch it with:

```bash
vaibify gui
```

The viewer opens at `http://127.0.0.1:8050` and communicates with the
running container over the Docker CLI.

## Pipeline Panel

The left panel displays the steps defined in `workflow.json`. Each step
shows its name, working directory, and current status (idle, running,
passed, or failed). From this panel you can:

- **Run All** -- execute every enabled step in order.
- **Run Selected** -- execute only the checked steps.
- **Run From Step** -- execute from a chosen step to the end.
- **Enable / Disable** -- toggle individual steps without removing them.
- **Reorder** -- drag steps to change the execution order.

### Adding a Step

Click **Add Step** to open the step editor. Fill in the step name,
working directory, and commands. The editor has separate fields for data
commands (heavy computation) and plot commands (figure generation), making
it straightforward to re-run just the visualization after adjusting a
plotting script.

### Interactive Steps

Steps can be marked as **interactive** (`bInteractive: true`). When the
pipeline reaches an interactive step it pauses and waits for the user to
confirm before continuing. This is useful for manual inspection or
parameter adjustment between automated stages.

## Step Verification Status

Vaibify's core job is to track **what has happened on disk** and flag any
drift between a step's current filesystem state and the last time it was
validated. Two visual indicators communicate this state: a coloured
**status dot** on the right of each step row, and an optional **pencil
icon** (✏) next to the step name.

### Status Indicator

Each step row ends with one of three indicators:

- **Nothing** -- the step has not yet produced any output, so there is
  nothing to verify.
- **Orange dot** -- the step is *partially verified*. Some of user
  verification, unit tests, and dependency analysis pass, or one or more
  on-disk changes have been detected since the last full pass.
- **Accent-colored badge** (the vaibify favicon; accent colour is
  configurable and defaults to purple) -- the step is *fully verified*:
  user, unit tests, and dependencies all pass and no on-disk drift has
  been detected.

When every enabled step is fully verified, the workflow name and the
"Workflow" label in the top toolbar also shift to the accent colour,
giving an at-a-glance sign that the whole pipeline is in a trusted state.

### Pencil Icon

A pencil next to a step's name means **the step's on-disk state has moved
out of sync with its last validation event**. The pencil is derived
entirely from filesystem modification times compared against two
validation anchors:

- **The test-marker file** (`/workspace/.vaibify/test_markers/<step>.json`),
  written by vaibify's pytest plugin whenever `pytest` finishes. This
  captures any test invocation, whether from the GUI, a terminal, Claude
  Code, or CI.
- **`sLastUserUpdate`**, set when the researcher clicks the verification
  badge in the GUI to attest that the outputs have been inspected.

The pencil lights up when any of the following are true:

| Drift | Meaning |
|---|---|
| A data script is newer than the test marker | Tests need to be rerun against the updated script. |
| A data file is newer than the test marker | Tests need to be rerun against the updated output. |
| A data script or data file is newer than `sLastUserUpdate` | Re-inspect the outputs and re-attest. |
| A plot script or plot file is newer than `sLastUserUpdate` | Re-inspect the figures and re-attest. |

Expanding the step shows a human-readable list of the specific files
causing each condition. Because every condition is disk-derived, edits
made outside the GUI are caught automatically -- a terminal edit, a
`git pull`, or a Claude Code session that touches a script will advance
the mtime and surface the pencil on the next polling cycle, without any
UI action.

### Clearing a Pencil

- **Re-run tests** for the step (from the GUI or a terminal). Successful
  completion advances the test-marker file's mtime and clears any
  "... newer than the test marker" drift.
- **Re-verify** by clicking the verification badge in the GUI after
  inspecting the outputs. This advances `sLastUserUpdate` and clears any
  "... newer than `sLastUserUpdate`" drift.

### Expanded-Step Timestamps

Clicking a step expands its detail view, which includes four UTC
timestamps. Three are derived directly from the container's filesystem;
the fourth is the one field stored in the workflow as a user attestation.

| Row | Source | What it records |
|---|---|---|
| **Last tests** | mtime of `/workspace/.vaibify/test_markers/<step>.json` | When `pytest` for this step last finished, regardless of who ran it. |
| **Data files last modified** | max mtime over the step's `saDataFiles` | When any of the step's data outputs was last written to disk. |
| **Plot files last modified** | max mtime over the step's `saPlotFiles` | When any of the step's plot outputs was last written. |
| **Last verified** | `dictVerification.sLastUserUpdate` | When the researcher last clicked the verification badge. The only timestamp stored as a workflow variable rather than read from disk. |

Because the first three are disk-derived, they remain accurate even for
changes vaibify did not directly observe: a `git pull` that updates a
data file, a manual `pytest` at a terminal, or an edit from Claude Code
will all be reflected in the displayed timestamps on the next polling
cycle.

## Figure Viewer

The right panel displays figures produced by the pipeline. Figures are
loaded from the container's plot directory and refresh automatically after
each step completes. Supported formats include PDF, PNG, SVG, and JPG.

The figure viewer provides:

- **Automatic refresh** after each step completes.
- **Full-size preview** by clicking a thumbnail.
- **Side-by-side comparison** of before/after versions of a figure.

## DAG Visualization

The workflow viewer renders the pipeline as a directed acyclic graph (DAG)
showing the dependency structure between steps. The DAG updates in real time
as steps complete. A zoom toolbar allows panning and scaling.

## Integrated Terminal

Click **Terminal** to open a shell session inside the container. The
terminal runs in the browser via WebSocket and behaves like a standard
terminal emulator. Multiple sessions can run concurrently.

## Resource Monitor

The status bar at the bottom displays real-time CPU, memory, and disk
usage of the container. These metrics update continuously via WebSocket.

## Test Generation

The **Tests** panel generates unit tests for pipeline steps. Select a step
and click **Generate Tests** to produce three categories of verification:

1. **Integrity tests** (`test_integrity.py`) -- validate that output files
   exist, are non-empty, load in their expected format, have the correct
   shape, and contain no NaN or Inf values.

2. **Qualitative tests** (`test_qualitative.py`) -- verify that column
   names, JSON keys, parameter names, and other categorical content match
   expectations.

3. **Quantitative tests** (`test_quantitative.py` +
   `quantitative_standards.json`) -- compare numerical output values
   against stored benchmarks at full double precision with configurable
   relative and absolute tolerances.

Test generation is **deterministic by default**: a Python introspection
script runs inside the container, reads each data file, and produces tests
mechanically. No LLM is required. The LLM-based path is available as a
fallback by setting `bDeterministic: false` in the API request.

See [Supported Data Formats](testFormats.md) for the full list of file
types the test generator can read.

## Sync Controls

The toolbar includes buttons for:

- **Push to Overleaf** -- sync figures to the configured Overleaf project.
- **Archive to Zenodo** -- upload outputs and receive a DOI.
- **Generate LaTeX** -- create `\includegraphics` commands for all figures.

These actions are also available via the CLI (`vaibify publish`).

## Repos Panel

The Repos panel appears in the left sidebar when a container is opened
without a workflow (sandbox or toolkit mode). It lists all tracked git
repositories in `/workspace` with branch, dirty status, and per-repo
push controls.

**Tracking**: when you first open a container, any git repositories
already in `/workspace` (cloned by the entrypoint from `vaibify.yml`)
are automatically tracked. If you clone additional repos from the
terminal, vaibify detects them within a few seconds and prompts you
to **Track** or **Ignore** them.

**Dirty detection** reflects whether the user has made source-level
changes to a repository. Build artifacts produced by package managers
and compilers (e.g. `*.egg-info/`, `*.o`, `*.aux`, `__pycache__/`)
are filtered out automatically. A freshly cloned and installed repo
will show as clean unless you have edited its source files. The
complete list of filtered patterns is defined in
`trackedReposManager.FROZENSET_ARTIFACT_PATTERNS`.

**Push** commits and pushes changes to a tracked repository's remote.
The default Push button commits whatever you have staged in the
terminal (`git add` + `git commit` + `git push`). A secondary
"Push files..." option in the gear menu opens a file picker for
selecting specific files to commit.

## Hub Mode

When Vaibify is invoked with no subcommand, it starts in **hub mode** -- a
dashboard that lists all Vaibify projects on the host. Hub mode provides:

- A container registry browser for discovering published images.
- A directory browser for locating project configuration files.
- Quick-launch buttons to start, stop, or open the GUI for any project.

Hub mode binds to port 8050 by default and is intended for users who
manage multiple Vaibify environments. A second `vaibify` invocation
on the same host auto-shifts to the next free port; pass `--port N`
to pin a specific port.

### One session per container

Each Docker container managed by vaibify may be accessed by only one
vaibify session at a time. This prevents two browsers from issuing
conflicting commands to the same container. When a session is
already attached to a container, that container appears greyed out
on other hubs' landing pages with the tooltip *"Already accessed by
another vaibify session."*. The lock is released when the holding
session navigates back to the landing, or when that vaibify process
exits (the kernel releases the `flock` automatically).

The **New vaibify window** icon (⧉) in the container hub, workflow
picker, and dashboard Admin menu spawns a detached child hub on a
free port and opens it in a new browser tab.
