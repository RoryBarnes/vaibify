# The Dashboard

The dashboard is what you arrive at after the [QuickStart](quickStart.md):
the running container's control surface, in your browser. It is where
you run pipeline steps, inspect outputs, attest that you have looked at
them, climb the AICS reproducibility ladder, push results to GitHub or
Overleaf, and (optionally) let an AI coding agent work alongside you.

This page is a tour of every panel.

## Layout

The dashboard has a fixed layout:

- **Top toolbar** — container name, active project, the three AICS
  level badges, the **?** Help button, and the Run, Sync, View, and
  Admin menus. A pulsing **compute indicator** appears beside the
  container name whenever the container's CPU is busy: theme-tinted
  when a vaibify step owns the compute, amber when the compute is
  happening outside the dashboard (an in-container agent or a
  terminal session running simulations directly — no step blinks in
  that case, because no step is running). The indicator hides when
  no reading is available; it never claims the container is idle.
- **Left panel** — a tabbed panel. For projects with a `project.json`
  the tabs are **Main**, **AICS**, **Files**, and **Logs**; for sandbox
  and toolkit projects (no `project.json`) they are **Files**, **Repos**,
  and **Logs**.
- **Top panels** — Two "Viewing Windows" to display plots and files.
- **Bottom panel(s)** — Terminal window(s)/tab(s) for work inside the
  container.

Beside the project name, three copies of the vaibify badge mark AICS
Levels 1–3 (Self-Consistent, Published, Reproducible). Each lights up
when the project attains that level, and the whole dashboard theme
shifts colour with the highest level attained: pale blue before Level
1, purple at Level 1, green at Level 2, and pink at Level 3. The
badge, the logo, and every "attained" mark share the tint, so a glance
at any corner of the screen tells you where the project stands.

## Terminal

Click in a terminal section to access a shell session inside the
container. The terminal runs in your browser over WebSocket and behaves
like a standard terminal emulator. Multiple sessions can run
concurrently — open as many as you like.

If Claude Code is enabled for the project, run

```bash
claude --dangerously-skip-permissions
```

from a terminal session to start an in-container coding agent. The
option's name sounds alarming, but inside a vaibify container it is
the intended mode: the container is an isolated sandbox, the agent
runs as an unprivileged user with no sudo, and everything it edits is
tracked in git and hash-pinned in the project manifest. Your
protection comes from verifying results, not from approving each
command — see the **Using AI** section of the [Help panel](#the-help-panel)
and the [Security model](security.md). The agent can in turn ask the
dashboard to run steps, generate tests, push to GitHub, and so on —
see [Agent actions](#agent-actions) below.

## Viewing Window

The Viewing Windows above the terminal(s) display plots and ASCII text files in the container. Supported formats include PDF, PNG, SVG, and JPG. In Project mode, the log is displayed in a window.

## Repos panel

The Repos panel is the home tab for sandbox and toolkit projects (the
templates without a `project.json`). In a project the tab is
hidden, but the panel is one click away: every "Open the Repos panel"
link in the Main tab's Project block and on the AICS tab lands there.
It lists the git repositories inside the container with their branch,
dirty status, and push controls.

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

## The Main tab

The Main tab is the project's control surface. It contains two
top-level collapsible blocks, each with a banner you can click to
collapse or expand:

- **Steps** — the per-step work of the project. Level 1
  (Self-Consistent) is a per-step property, so this is where Level 1
  is earned.
- **Project** — requirements that apply to the project as a whole
  rather than to any single step. Levels 2 (Published) and 3
  (Reproducible) are project-wide, so this is where they are earned.

Both banners carry the same right-aligned strip of status cells as
the rows beneath them, so a collapsed block still reports its
aggregate state.

The panel header above the blocks holds three buttons: a gear for
project settings, a refresh arrow to re-poll remote status, and
**+** to create a new step.

### The Steps block

A one-time header row labels the step columns: **Run**, the warning
column (⚠), and **L1 | L2 | L3**. Each step row then shows, left to
right:

- **Run checkbox** — include this step in the next run.
- **Run light** — execution only: hollow grey means the step has not
  run this session, filled grey means queued, blinking orange means
  running now, blinking red means running *past its runtime limit*
  (see below), solid red means the last run failed, and a quiet
  **pale-blue dot** means the last run succeeded. The vaibify check
  is reserved for attained level cells; the success record (outcome,
  finish time, durations) lives in the expanded step's **Last run**
  line.
- **Step label and name** — labels are per-type sequential: `A03` is
  the third *automated* step, `I01` the first *interactive* step.
- **Warning column (⚠)** — every warning the step carries,
  consolidated into one glyph; hover it for a plain-English list of
  reasons and remedies. The colour encodes *severity*, not level:
  **red** means something is broken right now (a test failed),
  **orange** means pending work or staleness (a script or output
  changed since verification, an earlier step changed, or a level
  regressed).
- **L1 | L2 | L3 cells** — the step's own state at each ladder rung
  (vocabulary below). Every rung has per-step requirements — Level 2
  covers the published copies of this step's outputs, Level 3 its
  manifest pinning, determinism, and binaries — and a rung with none
  shows the muted dash. Clicking a cell opens the step's detail onto
  that level's section.

Steps run from the toolbar's **Run** menu: **Run Selected Steps**,
**Run All Steps**, **Force Run All (Clean)**, **Stop All Running
Tasks**, plus the verification sweeps **Verify Outputs**, **Run All
Unit Tests**, and **Verify Dependencies**. Steps can be reordered by
dragging, and an individual step's right-click menu offers **Run
Step**, **Edit Step**, **Rename…** (which previews and then cascades
the rename through the step's directory, verification marker,
manifest paths, and declared paths), **Set Runtime Limit…**, **Run
From Here**, insertion, and deletion.

#### Step names and directories

A step's directory is not a free choice: its final component is
derived from the step name by an obvious formula — remove the
spaces, uppercase each word's first letter, keep the rest as typed.
"Step Name" becomes `StepName`; "GJ 1132 XUV" becomes `GJ1132XUV`;
hyphens pass through ("TOI-540 XUV" → `TOI-540XUV`). Names may
contain only letters, digits, spaces, and hyphens, and two steps may
not map to the same directory (compared case-insensitively, because
macOS clones sit on case-insensitive filesystems). Parent folders
are free — `systems/GJ1132XUV` is fine; only the last component is
governed.

Creating a step derives the directory automatically; renaming one
moves it (with the full cascade); the generic edit path refuses name
changes so the two can never drift. A step from an older project
whose directory does not match shows a **red** warning in its ⚠
column, and an **Align directories** button appears on the Steps
banner: one click migrates every nonconforming step — each move is a
`git mv`, with markers, manifest entries, and declared paths
following — and reports any step it had to skip (a name containing
now-forbidden characters must be renamed first).

#### Runtime limit (wall-clock budget)

A running step reports a heartbeat that only proves the *runner* is
alive — a step stuck in an infinite loop keeps that heartbeat beating
and, on its own, looks identical to a legitimately long forward-model
run. A step's **runtime limit** closes that gap: it is a ceiling, in
seconds, on how long the step may run before the dashboard flags it.
Once the active step passes its limit the run light turns **blinking
red** and its tooltip reads "running longer than its wall-clock
budget — may be hung". This is advisory and never stops the run — an
over-limit step keeps executing, because exceeding a declared
expectation is not proof of a hang; the flag only tells you where to
look.

New projects default to a four-hour limit for every step; existing
projects keep whatever they had (no limit unless one was set). The
first run of a project shows a one-time notice naming the default and
where to change it. Adjust the project-wide default under
**Settings**, or right-click a step and choose **Set Runtime
Limit…** — the dialog prefills a suggestion of twice the step's last
*successful* runtime, converting "how long should this take?" into
"should it take twice as long as last time?". Zero or blank means the
step inherits the project default; a project default of zero disables
the feature entirely, so long runs you expect are never mislabelled.

#### Adding a step

Click **+** in the panel header to open the step editor. Fill in the
step name, working directory, and the commands to run. The editor
separates *data commands* (heavy computation) from *plot commands*
(figure generation), so you can re-run just the plotting after
tweaking a script without re-running the simulation.

#### Interactive steps

Mark a step as *interactive* and it runs in the terminal with X11
display forwarding, via the **Run in Terminal** button in its expanded
view. Useful when a step requires human judgment — eyeballing an
intermediate result, adjusting a parameter — or when you want to hand
control to an agent for a specific stage.

#### The expanded step view

Clicking a step row expands its detail, which is organised by the
reproducibility ladder. At the top sits an optional, expandable
**Description** block — a few sentences on what the step does,
written by you or an agent (click the text to edit; agents set the
same field through the ordinary step-edit action). There is no
separate directory display: renaming a step renames its directory,
so the step's name *is* its directory. Below come three expandable
sections mirroring the banner cells — **Level 1 — Self-Consistent**,
**Level 2 — Published**, **Level 3 — Reproducible** — each headed by
the same level cell the banner shows, a compact "6/7" count, and an
ⓘ that opens a modal listing every requirement of that rung with its
live mark and a parenthetical spelling out what the mark means.
On first open the step's *target rung* — the first level not yet
attained — is expanded and the others are collapsed, so the detail
opens onto the work the ladder asks for next; your own toggles are
remembered after that.

**Level 1 is the workbench**: the step's input data, scripts, data
analysis commands, output data, plot commands, plot files, test
standards, and the Verification section — the step's own artifacts
are exactly its self-consistency surface. It ends with the **Run
Step** button and, just below it, the **Last run** line (outcome,
finish time, wall-clock and CPU durations). File rows carry the
per-file marks and remote badges described under
[Status lights and colours](#status-lights-and-colours), and
clicking a file opens it in a Viewing Window.

**Levels 2 and 3 are requirement sections**, one row per applicable
criterion with a met mark, the offending files when unmet, and either
an in-place action (**Verify now** for the GitHub and Zenodo rows —
the same verify the Project block offers) or a pointer to the
Project-block section where the project-scoped remedy lives (manifest
refresh, environment capture, determinism rules). The rows render the
same requirement breakdown the banner cell counts — the two can never
disagree. A rung with no requirements for this step says so and its
header shows the muted dash.

The **Input Data** block, between Directory and Scripts, declares
the raw files the step consumes that no step produces — for example,
observational data committed in the repository. Paths are
repo-relative; the **+** button opens a file picker that browses the
container's project repository (or accepts a typed path). Vaibify
watches declared inputs on every poll: a modified input invalidates
the step and shows "Input data modified since last run" — the
Project is no longer self-consistent until the step re-runs. A step
with no raw inputs is declared explicitly with the **No input data
needed** checkbox; a step with neither files nor the checkbox is
*undeclared* and cannot reach Level 1. The Project block's
"Input data declared" row names undeclared steps and offers a
one-click bulk declaration for retrofitting an existing Project.

A step that pulls data from a remote source records per-file
provenance (`listRemoteData`: source URL, retrieval time, content
hash, refreshed after every successful pull). Re-running such a step
when the pulled files already exist asks before overwriting the
canonical committed copy — as a modal in the browser, and as an
actionable refusal for the in-container agent, which must relay the
question to you. Fresh pulls are never auto-committed; review and
commit them through the ordinary canonical flow.

The **Verification** section at the bottom of the expanded view shows
one row per verification axis, each with its state and a timestamp:

| Row | What it records |
|---|---|
| **Unit Tests** | The combined state of the step's generated tests; "Last run" is when they last finished, regardless of who ran them. |
| **Dependencies** | Whether the step's cross-step inputs are consistent; "Last checked" is the last dependency analysis. |
| **Your name** | Your own sign-off. Click the row to attest that you have inspected the outputs; "Last updated" is your last attestation. |

Above these rows, plain-English drift notices name exactly which files
went stale and why — for example "Tests older than data scripts" or
"User verification older than plot files" — so you always know what to
re-run or re-inspect. The last run's outcome and durations live in
the **Last run** line below the Run Step button; the modification
times of the step's data and plot files are shown beside their
sections.

The expanded quantitative-tests block additionally carries a
**Falsification** row with a **Check test teeth** button. It
mutation-tests the step's own Python code against its quantitative
tests and records the kill-rate: a statement about the tests'
*fault-detection sensitivity* — "these tests were shown to notice
deliberately injected faults" — never about the result's accuracy.
It is deliberately **non-gating** (equivalent mutants make a hard
pass/fail dishonest) and applies only to deterministic pure-Python
steps; a step that shells out to a compiled binary reads **not
applicable**, never green. The record is digest-keyed to the script
and its standards, so any edit invalidates it. Runs are on-demand
only — cost is roughly mutants × step runtime.

The **Unit Tests** row expands to the three test categories, with
buttons to generate and run them — see [Verification](#verification).

### The Project block

The Project block lists project-scope requirements, grouped into seven
collapsible sections:

| Section | What it covers |
|---|---|
| **Repository** | The Level 1 project-scope requirement: the project lives inside a git repository (its *repository*). |
| **Software** | Standalone scientific binaries the project runs, each declared with an expected version and a captured version + SHA-256. |
| **Artifacts** | The reproducibility envelope files: `MANIFEST.sha256`, `requirements.lock`, the environment snapshot, the `Dockerfile`, and `reproduce.sh`. |
| **Determinism** | Your declared repeatability rules — how exactly a rerun must match your numbers (random seeding, numeric-library variance). |
| **Published copies** | The GitHub mirror, Zenodo deposit, Overleaf manuscript, and arXiv submission, with per-file sync state. |
| **AI** | The Replay-axis provenance rows: the AI model declaration (which models did the work — Level 2) with the two standing prompt files, and the AI Declaration (the researcher's signed statement — Level 2). |
| **Attestation** | The rebuild attestation (Level 3). |

The AI section is where the process provenance lives. Every AI model
used on the project is declared with its vendor, exact model ID, and
dates of use; a closed-weights model passes by declaration, while an
open-weights model additionally declares its weights source and
revision hash. Undeclared is the only failing state. The backend
captures a machine-written stamp (`.vaibify/ai_provenance.json`) —
the declared models, the SHA-256 of both standing prompt files, the
container's live network-isolation state, and an explicit trust-base
statement — and folds it into the Level 3 attestation record, so the
archived attestation carries the claim "these models, under these
instructions" alongside the rebuild hashes. The stamp is rewritten on
the next status poll whenever it drifts from the declaration; hand
edits do not survive.

Every section banner and every requirement row inside it carries a
status light and an **L1 | L2 | L3** level strip: the levels the
requirement gates show its state, and the others show a dash. A
researcher hunting for Level 2 blockers scans one column.

Expanding a requirement row reveals its file rows (with remote
badges), a plain-English status line, one "how to" line, and — where
an action exists — a button that performs it in place:

- **Capture version + SHA** and **Remove package…** on each declared
  binary, plus **Add package…** at the bottom of the Software section.
- **Regenerate now** on the manifest, dependency lock, and environment
  snapshot; **Check files against manifest** and **Check
  dependencies** for on-demand verification.
- **Generate reproduce.sh** to write the one-command reproduction
  script and pin it in the manifest.
- **Declare rules** / **Delete rules…** for the determinism
  declaration (stored directly in `project.json`; there is no
  separate rules file).
- **Configure arXiv…** to record the arXiv submission that must match
  the frozen Overleaf figures (optional — an untracked submission
  reads "not tracked" and never blocks Level 2).
- **Declare model…** and per-model **Remove** in the AI section's
  AI Model / Prompts row (removal is user-only — deleting a
  declaration erases provenance and can drop the project below
  Level 2).
- **Start from template**, **Import from this computer…**, and
  **Adopt repo-root context file** when no project context file
  exists yet. The context file (`.vaibify/AGENTS.md`) is the
  standing instructions the in-container agent reads; the entrypoint
  symlinks it to both root names (`CLAUDE.md` and `AGENTS.md`) so
  any agent tooling finds it. Once it exists, click its file row to
  view and edit it in place (saves go through a dedicated,
  path-fixed route; the generic editor still refuses `.vaibify/`
  writes). Import is researcher-only — the in-container agent can
  read and update the file via its own actions but can never pull
  files from your computer. Edits take effect for the agent's next
  session, not a running one.
- **Prompt Record…** opens the opt-in transcript recorder (the
  Replay axis's *Recorded* state, shown on the row's
  Untracked → Declared → Recorded → Supervised ladder). When
  enabled, the in-container agent's session transcripts are captured
  into `.vaibify/promptRecord/` every ~30 seconds as **redacted
  transcripts**: each capture is scanned (exact vaibify session
  secrets, the detect-secrets pattern catalog, vendor token
  prefixes, and a conservative high-entropy check) and every hit
  becomes a visible `[REDACTED: category]` marker with a per-category
  count. You review a sample of the first capture before the record
  counts (the agent cannot approve its own transcript). Captures are
  hash-chained — editing or removing one breaks the chain loudly —
  and coverage intervals are listed so time the hub was down reads
  as an explicit gap, never implied continuity. The record is
  tamper-evident, not proven complete; enabling it requires
  `pip install vaibify[replay]` on the host. Off reads "Not tracked
  — optional" and never blocks a level.

  The same dialog holds **Supervised mode** (the rung above
  Recorded; requires the record enabled and reviewed). When on,
  every repository change must attribute to a recorded action
  channel — a pipeline dispatch, an editor save, a context write, or
  an open terminal session — within a 60-second window; changes with
  no recorded cause become **permanent, hash-chained flags**
  (`unattributed-modification`), and a repo that changed while the
  hub was not watching flags an `unsupervised-gap` on reconnect.
  Flags render as a red chip on the AI row and are never cleared by
  the tool; editing the flag file breaks its chain loudly.
  Attribution granularity is honest but coarse: the window and the
  channel, not the file path — and a terminal session attributes as
  an open channel without its keystrokes being captured (content
  capture is a planned extension, stated in the dialog).
- **Add AI declaration step** if the project has none, and **Verify
  Level 3 reproducibility** to launch the full rebuild-and-compare.

The Dockerfile row is guidance-only: the Dockerfile is yours to edit,
and pinning its base image to an exact digest (`FROM
<image>@sha256:…`) is something you — or the in-container agent — do
by hand.

## Status lights and colours

The same small vocabulary repeats across step rows, both block
banners, and every requirement row. The **?** Help panel carries the
authoritative legend; this is the summary.

### Level cells

The L1 | L2 | L3 cells (and the single L1 cell on step rows) use
seven states. The circle fills in as reality does — hollow (nothing
exists), grey (material exists), coloured (assessed), badge
(attained):

| Cell | Meaning |
|---|---|
| Hollow grey circle | Not started — no outputs on disk and no activity at this level yet. |
| Grey filled circle | Unassessed — outputs exist on disk, but no tests, checks, or sign-off have been recorded yet. |
| Red circle | No requirements met. |
| Orange circle | Partially met. |
| Vaibify badge (the favicon, theme-tinted) | Attained — every requirement at this level is met. |
| Question mark (?) | Unknown — GitHub/Zenodo have not been checked recently; refresh remote status to find out. |
| Dash (—) | Not applicable — no requirements at this level for this row. |

The grey states are honest by design: "unassessed" asserts only that
the step's declared outputs exist — hours of compute performed
outside the dashboard stay visible as progress — but it never claims
verification, and a remote that has never been checked is never
shown as passing.

### Warning glyphs

Warning glyphs (⚠) are coloured by **severity**, never by level:

- **Red** — broken or failing *now*: a test failed, a declared file is
  missing, a requirement check failed.
- **Orange** — pending work or staleness: something changed since the
  last verification, or a check has gone stale and needs refreshing.

Blue is reserved for purely informational marks, such as the
not-tracked-by-git badge. The pencil mark (✎) on a file row means the
file changed since its last verified run — re-run the step to refresh
it.

### File-name styles

Inside expanded rows, a file name rendered in red is itself a
diagnosis: upright red means the declared file is missing; red with a
dotted underline means it changed since its last test run; red italic
means it exists but you have never verified it.

### Per-file remote badges

Each file row carries one badge per configured remote (GitHub,
Overleaf, Zenodo, arXiv), tinted by that remote's state:

| Badge | Meaning |
|---|---|
| Pale blue | In sync with the remote. |
| Amber | Local file differs from the last push. |
| Red | Uncommitted local changes. |
| Blue | Not tracked by git (informational). |
| Solid muted grey | Git-ignored — a deliberate `.gitignore` exclusion, distinct from "never published". |
| Faded grey | Not synced to this remote. |

Only figure formats travel to a manuscript, so the Overleaf and arXiv
rows list figure files only.

## The AICS tab

The AICS tab is the requirements ledger for the reproducibility
ladder. A header card names the project's current level (for
example, "Level 1: Self-Consistent") with a clickable progression
strip, followed by three expandable sections — **Level 1 —
Self-Consistent**, **Level 2 — Published**, **Level 3 — Reproducible**
— each summarising how many of its requirements are met.

Every requirement row shows a status light, the requirement, what it
means, and how to meet it, with a deep link to the surface where the
work happens (the Main tab's blocks or the Repos panel). The tab owns
the requirement *text*; the buttons that do the work live in the Main
tab's Project block. The requirements are:

- **Level 1**: Repository; Every step self-consistent; Project
  context file (optional — a versioned `.vaibify/AGENTS.md` recording
  the agent's standing instructions; shown with a neutral dash and
  excluded from the met count when absent, since it never blocks a
  level).
- **Level 2**: GitHub mirror; Zenodo deposit; arXiv manuscript
  (opt-in — checked only when an arXiv submission is recorded, since
  posting happens outside vaibify on its own timeline); AI
  Declaration attested; AI model declared (every model used, with
  vendor, ID, and dates — closed-weights models pass by declaration).

The AI rows' deep links land on the specific requirement row in the
Project block's AI section — the link switches tabs, expands the
group and row, and scrolls it into view.
- **Level 3**: Manifest complete; Dependency lock; Environment
  snapshot; Dockerfile pinned; Reproduce script; Determinism declared;
  Software declared; Rebuild attestation.

The Level 3 section ends with the verification machinery: the
**Verify Level 3 Reproducibility** button (enabled only when the
readiness checks pass; the rebuild runs in the container and can take
hours), the current **Level 3 Attestation** card (timestamp, manifest
digest, image digest, hashes matched, duration — with a staleness
notice if the manifest has changed since), and the **Reproduction
History** table of every attempt.

See [Reproducibility](reproducibility.md) for what each envelope
artifact contains and how third parties verify it without vaibify.

## The Help panel

The **?** button beside the project name opens the Help panel. It
contains:

- A link to the full online documentation.
- **Using AI** — how to start the in-container coding agent
  (`claude --dangerously-skip-permissions`) and why skipping
  per-command permission prompts is the intended, safe mode inside the
  sandbox: the container isolates the agent from your host, every
  edit is tracked in git and hash-pinned, and a full rebuild
  ultimately checks the analysis — the AICS Level 3 posture.
- The **Legend** — the symbol key, in four divisions matching the
  dashboard's surfaces: **Steps** (run checkbox, run light, warning
  column, per-file marks), **Project** (requirement-row marks and the
  Level 2/Level 3 warning catalog), **Level status lights** (the
  L1 | L2 | L3 cell vocabulary), and **Files and remotes** (the
  per-file badges and red file-name styles).

The legend is generated from the same catalog the dashboard renders
from, so it cannot drift from the glyphs you actually see. Status
itself is deliberately *not* in the panel — status lives on the
banners and the AICS tab.

## Verification

Steps are verified three ways: 1) unit tests, 2) dependency checks (if
applicable), and 3) user attestation. These three controls are
displayed in each step's expanded view.

The **Unit Tests** row is expandable to show detailed information
about the step's unit tests, including generating and running them.
Three categories of unit tests exist:

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

Finally, clicking the row that carries your name records your own
assessment — the human attestation that no test can substitute for.

## Publishing and remote sync

The toolbar's **Sync** menu holds the publication actions:

- **Push to GitHub** — commit and push the repository to its
  configured remote.
- **Push to Overleaf** — sync figures and any selected files to the
  configured Overleaf project.
- **Archive to Zenodo** — upload outputs and receive a DOI.
- **Configure arXiv…** — record the arXiv submission that must match
  the frozen figures.
- **Verify Reproducibility** — open the remote-verification panel
  described below.

Credentials for these services are resolved from your host's keychain
at request time. They are never written into the container or into
`vaibify.yml`. See [External services](externalServices.md) for the
per-service integration architecture.

Per-file sync state is always visible as the
[remote badges](#per-file-remote-badges) on file rows, and each remote
has a requirement row under **Published copies** in the Project block.
Every one of those rows carries a **Verify now** button that runs the
authoritative remote comparison in place, and a successful Overleaf
push re-verifies its row automatically — the row reports the last
verification, so the action that refreshes it is always one click
away.

### The Verify Reproducibility panel

**Sync → Verify Reproducibility** opens a panel with one row per
configured remote (GitHub, Overleaf, Zenodo). Each row shows the same
four pieces of information:

| Field | Meaning |
|---|---|
| **Status pill** | Green / yellow / red, semantics below. |
| **Summary** | `<matching>/<total> files match SHA-256`, optionally listing the first diverged path. |
| **Last verified** | Age of the most recent authoritative SHA-256 verify (e.g. "12m ago"). Empty when the remote has never been authoritatively verified. |
| **Re-verify** | A button that runs an authoritative SHA-256 verify against the remote's current bytes (downloads the files, recomputes hashes, and compares them against the declared project files as they exist on disk right now — never against `MANIFEST.sha256`, which is the Level 3 envelope artifact). |

Pill semantics:

- **Green** — the most recent SHA-256 authoritative verify reported
  every file matching the manifest.
- **Yellow** — never verified, or drift suspected since the last
  authoritative verify (the remote's cheap-poll change-detection layer
  fired). The remote may or may not actually be out of sync; click
  **Re-verify** to find out.
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
  yet (e.g. immediately after opening a project for the first time).
- `Remote consistency: ✓ all <K> configured remote(s) in sync` — every
  configured remote authoritatively matched on its last verify; `<K>`
  is the count of remotes that reported a verified status. The trailing
  noun is singularised to `remote` when `<K>` is 1, otherwise
  `remotes` (e.g. `Remote consistency: ✓ all 1 configured remote in
  sync` vs. `Remote consistency: ✓ all 3 configured remotes in sync`).
- `Remote consistency: ⚠ <N> file(s) drifted across <M> of <K>
  remote(s)` — at least one remote reports drift; `<N>` is the total
  diverged-file count across all remotes, `<M>` is the number of
  remotes with at least one diverged file, and `<K>` is the count of
  configured-and-verified remotes. Both the file noun and the remote
  noun singularise independently when their count is 1 (e.g. `Remote
  consistency: ⚠ 1 file drifted across 1 of 1 remote` vs. `Remote
  consistency: ⚠ 5 files drifted across 2 of 3 remotes`).

### Hash-aware staleness

Status marks distinguish *content drift* from *cosmetic mtime drift*.
After a fresh clone, file mtimes are reset to checkout time, which
historically caused every step to render as stale even though the
bytes had not changed. The dashboard consults the per-file SHA-256
recorded in the test marker before declaring a file stale: a
post-clone or post-`touch` mtime bump with matching hashes is treated
as content-clean, so the warning column and the ✎ file marks reflect
what is actually on disk, not what a tool merely touched.

## Agent actions

When an AI coding agent is running inside the container (typically
Claude Code, started by typing `claude --dangerously-skip-permissions`
in the terminal), it can ask the dashboard to perform named operations
on the user's behalf. These *agent actions* are the bridge between the
agent's text-only world and the dashboard's verified state. This
scheme enforces deterministic behavior.

Every state-changing operation in the dashboard — running a step,
generating tests, pushing to GitHub, archiving to Zenodo — is
registered in a single catalog. Each action carries a stable name, the
arguments it accepts, and the verification it triggers when it
finishes. The agent never invents an action; it picks one from the
catalog or it falls back to plain shell commands.

### Shipped agent skills

The container also ships ready-made *skills* — task recipes the agent
loads on demand — installed into the agent's skills directory at
container start (edit or delete your container's copies freely; an
image rebuild refreshes them):

- **session-budget** — keeps long autonomous runs alive across Claude
  session-usage limits: commit-per-work-unit checkpointing with a
  running resume note as the primary defense, a conservative usage
  reading (`claude-monitor`, documented as an account-wide lower
  bound) as the secondary one, and a pause-until-reset mechanic for
  the 5-hour window. Default pause threshold is 95%; override it by
  saying so in the task prompt.
- **read-arxiv** — token-efficient paper reading: fetch the arXiv
  e-print TeX source instead of the PDF (far fewer tokens, and figure
  captions arrive as searchable text), read selectively, record the
  version read, and fall back to the PDF only when no source exists.
- **aics-ladder** — the ordered L1→L2→L3 walkthrough for raising or
  auditing a project's reproducibility level, with the known audit
  traps codified (`iAICSLevel` is the only authoritative signal;
  marker hashes are git blob SHA-1s; publication is user-only).
- **create-pipeline-step** — the five-phase protocol for authoring a
  fully wired step, centred on the `{StepNN.varname}` cross-step
  token contract.
- **vaibify-doc-map** — a question→(doc, section) table so the agent
  reads the right 30 lines of vaibify's own docs (staged in-container
  at `/usr/share/vaibify/docs`) instead of a whole file.
- **diagnose-failed-run** — a triage tree over the read-only
  `get-pipeline-state` and `get-host-log-tail` actions for a dead or
  stuck run.
- **read-manuscript** — pull the project's own Overleaf manuscript
  (via the `pull-manuscript` action) into a git-ignored scratch copy
  and read it, rather than answering from memory.

Moving the ladder and step-authoring walkthroughs into on-demand
skills also slims the always-loaded container `CLAUDE.md` from ~470 to
~170 lines — a direct per-session token saving, with the
safety-critical rules (authoritative level signal, user-only
publication, the token contract) kept inline.

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

Step labels (`A03`, `I01`) come from the Steps block — labels are
*per-type sequential*, so `A03` is the third *automated* step and
`I01` is the first *interactive* step. The dashboard updates as the
action runs; if it produces new files, the affected step's warning
column and level cell react automatically.

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

### One browser session per container

Each container managed by vaibify can be open in only one **browser
session** at a time. When you claim a container, the server mints a
private lease for that tab; a different tab — even another tab of the
same browser on the same hub — that tries to open the same container is
refused with *"In use in another browser session"* and its tile renders
greyed out. This holds across two tabs of one browser, two browsers,
and two hubs alike; it is the single owner-of-record model described in
the [architecture reference](architecture.md#single-browser-session-per-container).
It is an operational guarantee for honest use behind vaibify's loopback
trust boundary, not a defense against a hostile in-page script.

Reloading the owning tab is safe: its lease lives in `sessionStorage`,
so the refreshed tab re-asserts the same ownership and is never locked
out of its own container.

An abandoned session does not hold a container forever. A hub or
viewer left with no connected tab and nothing running self-retires
after an idle timeout (see
[Configuration](configuration.md#vaibify_hub_idle_timeout_seconds)),
freeing its container. Ownership is also released the moment the owning
tab closes (a `pagehide` signal) or a brief disconnect's grace window
expires with no reconnect — never while a pipeline is still running.
The hub re-polls availability every few seconds, so a freed container
un-greys on its own without a page reload. You can also list and stop
live sessions from the host with `vaibify sessions` (see the
[CLI Reference](cli.md#session-management)).

The **New vaibify window** icon (⧉) in the hub, the project picker,
and the dashboard's Admin menu opens a fresh vaibify session in a new
browser tab — useful for working on **two different projects** side by
side. Each window claims its own containers; it is not a way to open
the *same* container twice.
