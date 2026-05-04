# The Dashboard

The dashboard is what you arrive at after the [QuickStart](quickStart.md):
the running container's control surface, in your browser. It is where
you run pipeline steps, inspect outputs, attest that you have looked at
them, push results to GitHub or Overleaf, and (optionally) let an AI
coding agent work alongside you.

This page is a tour of every panel.

![Dashboard overview](./images/dashboard-overview.png)

## Layout

The dashboard has a fixed layout:

- **Top toolbar** — container name, active workflow (if any), sync
  buttons, and the Admin menu.
- **Left panel** — the *Repos panel* for sandbox / toolkit projects, or the   *Pipeline panel* for workflow projects.
- **Top panels** — Two "Viewing Windows" to display plots and files.
- **Bottom panel(s)** - Terminal window(s)/tab(s) for work inside the container.

## Terminal

Click in a terminal section to access a shell session inside the
container. The terminal runs in your browser over WebSocket and behaves
like a standard terminal emulator. Multiple sessions can run
concurrently — open as many as you like.

![Terminal](./images/dashboard-terminal.png)

If Claude Code is enabled for the project, run `claude` from a
terminal session to start an in-container coding agent. The agent can
in turn ask the dashboard to run steps, generate tests, push to
GitHub, and so on — see [Agent actions](#agent-actions) below.

## Viewing Window

The Viewing Windows above the terminal(s) display plots and ASCII text files in the container. Supported formats include PDF, PNG, SVG, and JPG. In Workflow mode, the log is displayed in a window.

![Figure viewer](./images/dashboard-figures.png)

## Repos panel

The Repos panel replaces the Pipeline panel for sandbox and toolkit
projects (the templates without a `workflow.json`). It lists the git
repositories inside the container with their branch, dirty status, and
push controls.

![Repos panel](./images/dashboard-repos.png)

When you first open a container, repositories already present in the
workspace (cloned by the entrypoint from `vaibify.yml`) are tracked
automatically. If you clone additional repositories from the terminal,
vaibify detects them within a few seconds and prompts you to **Track**
or **Ignore** them.

**Dirty detection** reflects whether you have made source-level
changes. Build artifacts that package managers and compilers leave
behind (Python `__pycache__/`, C `*.o`, LaTeX `*.aux`,
`*.egg-info/`, and so on) are filtered out, so a freshly installed
repository shows as clean unless you have edited its source files.

**Push** commits and pushes whatever you have staged in the terminal —
`git add`, `git commit`, `git push` rolled into a single button. A
secondary **Push files…** option in the gear menu opens a file picker
for selecting specific files to commit.

## Pipeline panel

The pipeline panel lists the steps defined in your workflow. Each step
shows its name, working directory, and current status.

![Pipeline panel](./images/dashboard-pipeline.png)

From the top of the panel you can:

- **Run All** — execute every enabled step in order.
- **Run Selected** — execute only the checked steps.
- **Run From Step** — execute from a chosen step to the end.
- **Enable / Disable** — toggle individual steps without removing
  them.
- **Reorder** — drag steps to change the execution order.

### Adding a step

Click **Add Step** to open the step editor. Fill in the step name,
working directory, and the commands to run. The editor separates *data
commands* (heavy computation) from *plot commands* (figure generation),
so you can re-run just the plotting after tweaking a script without
re-running the simulation.

### Interactive steps

Mark a step as *interactive* and the pipeline pauses there, waiting
for you to confirm before continuing. Useful when you want to eyeball
an intermediate result, adjust a parameter, or hand control to an
agent for a specific stage.

## Step verification status

Vaibify's core job is to track **what has happened on disk** and flag
any drift between a step's current filesystem state and the last time
it was validated. Two visual indicators communicate this state: a
coloured **status dot** on the right of each step row and an optional
**pencil icon** (✏) next to the step name.

### Status dot

Each step row ends with one of three indicators:

- **Nothing** — the step has not yet produced any output, so there
  is nothing to verify.
- **Orange dot** — the step is *partially verified*. Some of user
  attestation, unit tests, and dependency analysis pass, or one or
  more on-disk changes have been detected since the last full pass.
- **Accent-coloured badge** (the vaibify favicon) — the step is
  *fully verified*: user attestation, unit tests, and dependencies
  all pass and no on-disk drift has been detected.

When every enabled step is fully verified, the workflow name and the
"Workflow" label in the top toolbar shift to the accent colour, giving
an at-a-glance sign that the whole pipeline is in a trusted state.

### Pencil icon

A pencil next to a step's name means **the step's on-disk state has
moved out of sync with its last validation**. The pencil is derived
entirely from filesystem timestamps, so it catches changes vaibify did
not directly observe — a `git pull`, a manual `pytest` at the
terminal, an edit from a Claude Code session.

The pencil lights up when any of the following are true:

| Drift | Meaning |
|---|---|
| A data script is newer than the test marker | Tests need to be rerun against the updated script. |
| A data file is newer than the test marker | Tests need to be rerun against the updated output. |
| A data script or data file is newer than the last user attestation | Re-inspect the outputs and re-attest. |
| A plot script or plot file is newer than the last user attestation | Re-inspect the figures and re-attest. |

Expanding the step shows a human-readable list of the specific files
causing each condition.

### Clearing a pencil

- **Re-run tests** for the step (from the dashboard or a terminal).
  Successful completion clears any "newer than the test marker" drift.
- **Re-attest** by clicking the verification badge after inspecting
  the outputs. This clears any "newer than your last attestation"
  drift.

### Expanded-step timestamps

Clicking a step expands its detail view. Four timestamps appear; three
are read directly from disk and one is your own attestation.

| Row | What it records |
|---|---|
| **Last tests** | When `pytest` for this step last finished, regardless of who ran it. |
| **Data files last modified** | When any of the step's data outputs was last written. |
| **Plot files last modified** | When any of the step's plot outputs was last written. |
| **Last verified** | When you last clicked the verification badge to attest the outputs. |

## Sync status

Symbols before files indicate the current status of a file's publication on a remote repository.

![Sync panel](./images/dashboard-sync.png)

- **Push to GitHub** — commit and push the project repository to its
  configured remote.
- **Push to Overleaf** — sync figures and any selected files to the
  configured Overleaf project.
- **Archive to Zenodo** — upload outputs and receive a DOI.
- **Generate LaTeX** — produce ready-to-paste `\includegraphics`
  commands for the current figures.

Credentials for these services are resolved from your host's keychain
at request time. They are never written into the container or into
`vaibify.yml`. See [Connecting external services](connecting-services.md)
for the per-service setup.

### Remote-sync panel

The sync panel surfaces one row per configured remote (GitHub,
Overleaf, Zenodo). Each row shows the same four pieces of
information:

| Field | Meaning |
|---|---|
| **Status pill** | Green / yellow / red, semantics below. |
| **Summary** | `<matching>/<total> files match SHA-256`, optionally listing the first diverged path. |
| **Last verified** | Age of the most recent authoritative SHA-256 verify (e.g. "12m ago"). Empty when the remote has never been authoritatively verified. |
| **Re-verify** | A button that runs an authoritative SHA-256 verify against the remote's current bytes (downloads the files, recomputes hashes, compares against `MANIFEST.sha256`). |

Pill semantics:

- **Green** — the most recent SHA-256 authoritative verify reported
  every file matching the manifest.
- **Yellow** — drift detected since the last authoritative verify
  (the remote's cheap-poll change-detection layer fired). The remote
  may or may not actually be out of sync; click **Re-verify** to find
  out.
- **Red** — an authoritative SHA-256 verify confirmed at least one
  file's hash does not match `MANIFEST.sha256`.

A scheduled background loop re-verifies every configured remote on a
configurable cadence (default 6 hours), so the panel reflects
recently-validated state even if the user never clicks Re-verify.

### Aggregate consistency banner

Above the per-remote rows, a single line summarises the union of all
remote states. The three forms produced by
[scriptSyncManager.js](../vaibify/gui/static/scriptSyncManager.js) are:

- `Remote consistency: not yet verified` — no remote has been verified
  yet (e.g. immediately after opening a workflow for the first time).
- `Remote consistency: ✓ all in sync` — every configured remote
  authoritatively matched on its last verify.
- `Remote consistency: ⚠ N files drifted across M remotes` — at least
  one remote reports drift; the count aggregates across remotes.

### Hash-aware step badges

Per-step status indicators distinguish *content drift* from *cosmetic
mtime drift*. After a fresh clone, file mtimes are reset to checkout
time, which historically caused every step badge to render as
"stale" even though the bytes had not changed. The dashboard now
consults the per-file SHA-256 recorded in the test marker before
declaring a step stale:

| Indicator | Meaning |
|---|---|
| **Filled badge** (the favicon glyph) | Fully verified: tests pass, attestation current, content matches. |
| **Orange dot** | Partially verified — at least one of tests, attestation, or dependency analysis is stale. |
| **Hollow circle** (◌) | Mtime drifted since the test marker, but the file's SHA-256 still matches the recorded hash. The step is treated as content-clean; the indicator just signals that a tool touched the file without changing its bytes. |
| **Pencil** (✏) | True content drift — the file's hash differs from the recorded hash. Re-run tests or re-attest. |

The hollow-circle case replaces a noisy false-positive class: a
post-clone or post-`touch` mtime bump no longer inflates verification
state, so the badges reflect what is actually on disk.

## Verification

Steps are verified three ways: 1) unit tests, 2) dependnency checks (if applicable), and 3) user attestation. These three controls are displayed in each Step's expanded view.

The **Unit Tests** text is expandable to show detailed information about the step's unit tests, including generating and running them. Three categories of
unit tests exist:

1. **Integrity tests** (`test_integrity.py`) — output files exist, are
   non-empty, load in their expected format, have the correct shape,
   and contain no NaN or infinity values.
2. **Qualitative tests** (`test_qualitative.py`) — column names, JSON
   keys, parameter names, and other categorical content match
   expectations.
3. **Quantitative tests** (`test_quantitative.py` plus
   `quantitative_standards.json`) — numerical output values match
   stored benchmarks at full double precision, with configurable
   relative and absolute tolerances.

Test generation is **deterministic by default**: a Python introspection
script runs inside the container, reads each data file, and writes the
tests mechanically. No language model is involved on the default path.
An LLM-based path is available as a fallback for formats the
introspection script cannot read.

See [Supported Data Formats](testFormats.md) for the full list of file
types the test generator can read.

`vaibify` monitors the steps for dependency violations, such as a dependent step 
not being fully verified or a dependent file being created *after* a subsequent step was marked verified.

Finally, a button for the (human) records that user's assessment.

## Agent actions

When an AI coding agent is running inside the container (typically
Claude Code, started by typing `claude` in the terminal), it can ask
the dashboard to perform named operations on the user's behalf. These
*agent actions* are the bridge between the agent's text-only world and
the dashboard's verified state. This scheme enforces deterministic behavior.

Every state-changing operation in the dashboard — running a step,
generating tests, pushing to GitHub, archiving to Zenodo — is
registered in a single catalog. Each action carries a stable name, the
arguments it accepts, and the verification it triggers when it
finishes. The agent never invents an action; it picks one from the
catalog or it falls back to plain shell commands.

From inside a container terminal, you can list the available actions:

```bash
vaibify-do --list
```

Or describe a single action's arguments:

```bash
vaibify-do --describe run-step
```

Or invoke one directly (the agent does this for you):

```bash
vaibify-do run-step A03
```

Step labels (`A03`, `I01`) come from the pipeline panel — labels are
*per-type sequential*, so `A03` is the third *automated* step and
`I01` is the first *interactive* step. The dashboard updates as the
action runs; if it produces new files, the affected step's pencil and
status dot react automatically.

### Why this matters

Without the catalog, an agent that wants to "run unit tests on step
A03" would have to guess at HTTP endpoints or shell out blindly, and
the dashboard would silently drift out of sync with what the agent
actually did. The catalog makes the agent's intentions explicit and
verifiable: every action it takes is one the user could have taken
through the dashboard, and every action triggers the same verification
state machine.

## Hub mode

The hub is a separate page from the dashboard, but you visit it every
time you launch vaibify with no subcommand:

```bash
vaibify
```

The hub lists every container vaibify knows about on the host, with
quick-launch buttons to start, stop, or open the dashboard for any of
them. From here you can also create a new container (the setup wizard
from the [QuickStart](quickStart.md)) or add an existing project that
already has a `vaibify.yml`.

### One session per container

Each container managed by vaibify can be open in only one dashboard at
a time. This prevents two browsers from issuing conflicting commands
to the same container. When a session is already attached, the
container appears greyed out on other hubs.

The **New vaibify window** icon (⧉) in the hub, the workflow picker,
and the dashboard's Admin menu launches a new vaibify session in a new browser tab — useful for working on two projects side by side.
