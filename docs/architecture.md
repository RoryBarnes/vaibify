# Architecture

This document describes how Vaibify is organized internally: which modules
exist, how they depend on each other, how state flows, and where the
load-bearing invariants live. It is the "why" companion to the `AGENTS.md`
files, which state the rules; this file explains the reasoning behind them.

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific software and data analysis pipelines inside Docker containers. The backend is a FastAPI
server (Python); the frontend is plain JavaScript using IIFE modules
(no bundler, no npm, no ES modules).

For the human contributor workflow (how to run tests, submit PRs, follow
the style guide) see [developers.md](developers.md). For the methodology
behind the agent documentation, see [vibeCoding.md](vibeCoding.md). 

## Preface

For the full argument of why vaibify exists and what it believes about
AI-assisted scientific computing, see [philosophy.md](philosophy.md).
The short version: the tagline *"Vibe boldly. Verify everything."* is
the architecture specification. Bold vibing happens inside a Docker
container the agent cannot escape. Verification happens in a browser
dashboard that makes the researcher's "yes, I looked at this" a
first-class artifact alongside the code and the data. Every design
choice below — the containerization model, the verification state
machine, the polling cadence, the rule that the dashboard never lies —
falls out of taking both halves of the tagline seriously at the same
time.

## Mental model

A handful of concepts run through the whole codebase. Understanding
them in the abstract makes the module layout below much easier to read.

**Container.** A Docker sandbox, one per project. It holds the
researcher's scripts, their Python environment, and any ephemeral files
the agent produces. The agent launched inside the container sees only what is inside. The host sees
the container through a narrow, audited interface.

**Workflow.** A `workflow.json` file that declares an ordered sequence
of steps. Workflows are checked into git, travel with the project, and
reconstruct the same pipeline on a different machine. A workflow is a
portable unit of reproducibility.

**Step.** One unit of work in a workflow: typically a data command, a
plot command, or a test command. Steps declare their script and their
outputs, and they carry dependencies on the outputs of earlier steps.
Each step carries verification state.

**Verification state.** A structured record per step that answers three
questions. Did the unit tests pass the last time they ran? Has the
researcher looked at the output since it last changed? Has an upstream
step been modified without this step being rerun? Verification state
lives on the step, is persisted with the workflow, and degrades
automatically when the world underneath it changes. The full state
machine is defined in [fileStatusManager.py](../vaibify/gui/fileStatusManager.py).

**Dashboard as ground truth.** The browser GUI is the only place where
container status, workflow state, and verification state are surfaced
together. This is a rule, not an aesthetic. Nothing in vaibify may lie
to the dashboard: no optimistic status, no cached-past-lifetime state,
no quietly swallowed errors. If the truth is slow or ugly, the
dashboard shows it slow and ugly. The [AGENTS.md](../AGENTS.md) trap
list treats dashboard honesty as a hard invariant.

## The happy path

The most concrete way to understand how vaibify verifies a workflow is to watch what happens
when a researcher clicks **Run All** in the browser.

1. `PipeleyenPipelineRunner.fnRunAll()` fires in `scriptPipelineRunner.js`.
   The click was registered by the delegated handlers in
   `scriptEventBindings.js` and dispatched through `scriptApplication.js`.

2. The runner sends a single WebSocket message through `VaibifyWebSocket`,
   the singleton in `scriptWebSocket.js` that owns the connection to the
   backend. The payload is `{sAction: "runAll"}`.

3. On the backend, the WebSocket handler in `pipelineServer.py`
   dispatches actions to `pipelineRunner.fnRunAllSteps()`. The runner
   validates the workflow (via `pipelineValidator`), opens a log file
   (via `pipelineLogger`), and walks the step list.

4. For each step, the runner executes the step's command inside the
   container, streaming stdout and stderr back over the same WebSocket
   as `output` events. It emits `stepStarted` before the command runs,
   and `stepPass` or `stepFail` after it returns. Interactive steps
   pause and wait for the researcher via the protocol in
   `interactiveSteps.py`.

5. The frontend dispatches these events through `VaibifyWebSocket` to
   handlers registered by `scriptPipelineRunner.js`. Each handler
   updates the step's status via `PipeleyenApp.fnSetStepStatus()` and
   requests a render.

6. `fnRenderStepList()` is debounced with `requestAnimationFrame`, so
   a burst of events from a fast step coalesces into one DOM rebuild.
   `VaibifyStepRenderer.fsRenderStepItem()` produces the HTML for each
   step, including its verification badges.

7. When the run completes, the backend emits a terminal `runComplete`
   event. The next file-status poll (below) detects any new or modified
   output files and degrades stale verifications.

```
User clicks "Run All"
  -> PipeleyenPipelineRunner.fnRunAll()
  -> VaibifyWebSocket.fnSend({sAction: "runAll"})
  -> Backend: pipelineServer WebSocket handler
  -> pipelineRunner.fnRunAllSteps()
  -> For each step: backend emits stepStarted, output, stepPass or
     stepFail via WebSocket
  -> Frontend: VaibifyWebSocket dispatches to registered handlers
  -> PipeleyenPipelineRunner.fnHandlePipelineEvent()
  -> PipeleyenApp.fnSetStepStatus() + PipeleyenApp.fnRenderStepList()
  -> VaibifyStepRenderer.fsRenderStepItem() generates HTML
  -> DOM updated (debounced)
```

A reader who absorbs this path has the working model of vaibify:
browser event, WebSocket, orchestrator, extracted executor, event
stream back, debounced render.

### File-status polling

Running the pipeline is only half the story. The other half is keeping
the dashboard honest while nothing is running — the researcher is
editing a script in the container terminal, or the agent just finished
a long analysis off-dashboard. Every five seconds the frontend polls
the backend for the current state of every file the workflow cares
about.

```
Every 5 seconds (VaibifyPolling):
  -> VaibifyApi.fdictGet("/api/pipeline/{id}/file-status")
  -> Backend: pipelineRoutes._fnRegisterFileStatus handler
  -> fileStatusManager: compute mtimes, detect changes, check stale
     verifications
  -> Response: {dictModTimes, dictInvalidatedSteps, dictTestMarkers, ...}
  -> Frontend: PipeleyenApp.fnProcessFileStatusResponse()
  -> Updates caches, applies invalidations, applies test markers
  -> PipeleyenApp.fnRenderStepList() (debounced, cascading updates
     coalesce)
```

When a file changes, the affected step's unit-test state resets to
`untested`. When a plot changes, the user-verification state resets.
When an upstream step is modified, downstream steps are flagged as
upstream-modified. The researcher sees verification badges dim
automatically; no one has to remember to invalidate anything by hand.

Each poll's path-mtime collection is one `docker exec` total, fed by
a path list written to `/tmp/vaibifyPoll.list` via
`connectionDocker.fnWriteFileViaTar` and consumed by
`xargs -d '\n' -a … stat -c '%n %Y'`. The motivation is that each
`docker exec` on Colima costs roughly 300–800 ms of API round-trip
overhead, independent of how much work runs inside the container.
Coalescing N batches into one is the dominant lever for poll
latency, and it is the reason the polling endpoint scales past a few
hundred tracked paths without saturating the daemon.

An earlier design cached parent-directory mtimes per container and
skipped child stats whenever the parent's mtime had not moved. POSIX
only bumps a directory's mtime on add/remove/rename of children, not
on an in-place rewrite of an existing child, so the optimization
silently fed stale mtimes back to the reload detector and the
"step source modified" invalidation pass whenever an out-of-band
editor (the in-container agent's `Edit` tool, `vim :w`, `sed -i` on
some platforms) modified `workflow.json` or a step script in place.
The cache layer has been removed: every poll stats the polled paths
directly. This still costs exactly one `docker exec` per poll
(the dominant wire cost) and trades a small per-poll byte increase
on the path-list for the dashboard's honesty contract — the same
contract the [AGENTS.md](../AGENTS.md) "do not suppress or
misrepresent state" trap enforces for every other surface.

Four module-level booleans in `scriptPolling.js` — one each for
pipeline, file-status, repos, and discovery — short-circuit a poll
tick when the previous tick is still pending. These are
duplicate-request suppressors, not state caches. They do not cache
server responses, do not extend mtime values, and do not affect what
the next successful poll sees. Do not extend them into result
caching: that would re-introduce the stale-dashboard failure mode
the [AGENTS.md](../AGENTS.md) "do not suppress or misrepresent
state" trap warns about.

## Architectural decisions with tradeoffs

Each choice below has a reasonable-looking alternative. The paragraphs
explain what that alternative would cost.

**Vanilla JavaScript IIFE frontend, not React or Vue.** The frontend
uses the pattern `var ModuleName = (function () { ... })();` with
script tags loaded in a fixed order. There is no build step, no
`package.json`, no node_modules tree. This gives up ergonomic
components, reactive state, and the broader ecosystem of a framework.
In exchange, a new contributor who knows plain JavaScript can read any
file top-to-bottom and understand it without learning a framework's
conventions; the repository has no build pipeline to break on CI; and
the frontend has zero transitive npm dependencies to audit, update, or
worry about at install time. For a research tool with a long expected
lifetime and a small contributor pool, the tradeoff favors legibility
over ergonomics.

**FastAPI backend running on the host, not inside the container.** The
backend orchestrates containers, so it cannot live inside one of the
containers it orchestrates. It needs the Docker socket, it needs to
read and write the workspace volume from the host side, and it needs
to serve the GUI over localhost. This is what makes features like pull
files to host, browse host directories, and sync to GitHub possible at
all. The cost is that path traversal is a live concern: any path that
originates from an HTTP request body, a `workflow.json` field, or a
config file must be validated against its intended root before the
backend opens it. `fnValidatePathWithinRoot(sAbsPath, WORKSPACE_ROOT)`
in `pipelineServer.py` is the canonical guard; the trap list in
[AGENTS.md](../AGENTS.md) flags this explicitly.

**Docker containers, not Python-level sandboxing.** Vaibify does not
try to sandbox the agent with a virtualenv, a restricted subprocess
environment, or a library like `RestrictedPython`. Language-level
sandboxes are shallow: a determined agent can import `ctypes`, spawn a
child process, or exploit a parsing quirk and escape. Docker's
isolation is an industry-standard kernel-level boundary, and the
container ships with an unprivileged user plus `gosu` as a second
layer. The cost is that users need Docker installed and running, but
for a tool whose primary job is preventing an autonomous agent from
touching the host, a shallower boundary would defeat the point.

**Polling for file status, not push notifications.** The frontend polls
`/api/pipeline/{id}/file-status` every five seconds instead of
subscribing to file-change events over the WebSocket. Polling loses
sub-second responsiveness: a file that changes just after a poll will
show as stale for up to five seconds. What it gains is simplicity and
robustness. A push channel would have to survive container restarts,
reconnects, sleep and wake on the host, and the many edge cases where
file-watching APIs miss events on bind-mounted volumes. Polling just
works; it is cheap; and five seconds is faster than a human can notice
in practice. When the dashboard's single job is honesty, a boring
mechanism that cannot lie beats a clever one that occasionally does.

**Leaf modules and the re-export pattern.** The backend's orchestrator
modules (`pipelineRunner`, `pipelineServer`, `testGenerator`,
`syncDispatcher`) re-export symbols from extracted child modules. The
alternative would be to update every caller to import from the new
canonical locations directly. That migration is happening, but
gradually: the re-exports keep external and legacy callers working
while the internal structure is cleaned up. In parallel,
`pipelineUtils.py` and a handful of other files are deliberate leaf
modules with zero intra-package imports, which exist to break
circular-dependency cycles. Removing either pattern naively —
collapsing the leaves or deleting the re-exports — breaks real
callers. `tests/testArchitecturalInvariants.py` encodes both
invariants as executable rules.

**`posixpath` in `workflowManager.py`, `os.path` in `director.py`.**
These two modules contain similarly named functions and look like
natural candidates for deduplication. They are not. `workflowManager`
manipulates container paths, which are POSIX on every host operating
system. `director` manipulates host paths, which use the host's native
separator. Unifying them would either mangle Windows host paths or
mangle container paths on any host, and the failure would be silent
until a cross-platform user hit it. The divergence is load-bearing;
the [AGENTS.md](../AGENTS.md) trap list and
`tests/testArchitecturalInvariants.py` both guard it.

## Workflow = git repo

Every vaibify workflow lives inside a git repository — its "project
repo". The `workflow.json` file belongs to that repo, not to the
container, not to `/workspace`, and not to a shared vaibify-managed
location. This constraint is enforced at discovery time
(`flistFindWorkflowsInContainer` drops any candidate not inside a git
work tree) and at creation time (`_fsValidateRepoDirectory` rejects
target directories that are not git repos). It maps directly to L1 of
the reproducibility ladder in [vision.md](vision.md): a workflow that
cannot be committed cannot be reproduced.

`/workspace` itself is a Docker-managed named volume, not a repo. It
is the *discovery root* — the search origin for workflow.json files —
but not a git target. Inside a container, `/workspace` contains N
project-repo subdirectories (each a standalone git clone) plus some
shared configuration. A single container can therefore host multiple
workflows: GJ1132_XUV's paper pipeline today, XUVCatalog's
cross-system analysis tomorrow, both reusing the same heavy dependency
clones without needing a rebuild.

The **active workflow determines the badge scope**. At connect time,
`fdictHandleConnect` runs `git rev-parse --show-toplevel` inside the
container, starting from the directory that contains the loaded
`workflow.json`. The result is stamped on the workflow dict as
`dictWorkflow["sProjectRepoPath"]` and every subsequent git / badge /
manifest call threads it through `containerGit` as the authoritative
workspace. The helper lives in
`containerGit.fsDetectProjectRepoInContainer`; the routes read it from
the active workflow dict.

Per-step output paths (`saOutputFiles`, `saDataFiles`, `saPlotFiles`)
must be repo-relative and must stay inside the project repo. Absolute
paths and `..`-escaping paths are rejected by
`flistValidateOutputFilePaths` on save. Step directories (`sDirectory`
on each step) are held to the same rule by `flistValidateStepDirectories`
— a value like `/workspace/GJ1132_XUV/KeplerFfdCorner` is rejected; the
repo-relative form `KeplerFfdCorner` is required. Input references
inside `saCommands` / `saPlotCommands` / `saDataCommands` are
deliberately *not* validated — a step may legitimately read an
absolute `/workspace/GJ1132_XUV/Plot/foo.pdf` produced by a sibling
workflow. Badges are emitted only for the producing workflow; a
consumer workflow sees the file as a read path, not as a tracked
artifact.

Test markers — JSON files that record the outcome of the last pytest
session for each step, including `dictOutputHashes` for staleness
detection — live inside the project repo at
`<sProjectRepoPath>/.vaibify/test_markers/<slug>.json` where the slug
is derived from the step's (repo-relative) `sDirectory`. Marker
*writes* (by the conftest plugin deployed into each step's `tests/`
directory) and *reads* (by `fileStatusManager`, `gitRoutes`,
`syncDispatcher`) both resolve the directory through
`dictWorkflow["sProjectRepoPath"]` — no module hardcodes
`/workspace/.vaibify/test_markers`. Together with committing the
markers alongside the workflow, this makes test-verification state
survive a clone of the project repo.

This choice has two architectural consequences worth naming:

- **No workspace-root workflows.** A `workflow.json` at `/workspace`
  (outside any enclosing git repo) cannot be reproduced and is not
  allowed. The `pipelineServer` surfaces this by stamping an empty
  `sProjectRepoPath`, at which point the four `/api/git/*` endpoints
  return the explicit "Workflow is not in a git repository" payload
  rather than silently reporting `bIsRepo: false` against `/workspace`.
- **Forward-compatible multi-workflow model.** The workflow-dict field
  is the anchor for a future workflow-selector UI: when the user
  switches active workflows in a container, the cache key widens to
  `(sContainerId, sWorkflowPath)` and the badge scope re-scopes
  automatically — no changes to the git, badge, or manifest code.

The invariant `testGitRoutesAlwaysPassProjectRepoToContainerGit` in
`tests/testArchitecturalInvariants.py` guards the threading: every
`containerGit.*` call in `gitRoutes.py` must pass `sWorkspace`
explicitly. A silent fallback to the `/workspace` default would
reintroduce the all-grey-badges bug that motivated this design. A
companion invariant `testNoWorkspaceRootedMarkerHardcodeInSource`
bans the literal `/workspace/.vaibify/test_markers` in any module
under `vaibify/gui/` — enforcing that marker paths are always
resolved from the active workflow's `sProjectRepoPath`.

## Single browser session per container

This section is normative: it is the single source of truth for the
container-access model. `docs/dashboard.md` and `docs/cli.md` describe
the user-facing surface and point here for the mechanism.

Vaibify's concurrency model is borrowed from JupyterHub, which solves
the same problem of long-lived servers that outlive the browser that
launched them. There are three tiers:

- **The hub** (`vaibify` with no subcommand) is the multi-container
  landing page. It is the analog of the JupyterHub *Hub*.
- **The single-container viewer** (`vaibify start --gui`, or directly
  via `vaibify gui`) is the per-project dashboard. Both the hub and the
  viewer are uvicorn servers built by `appFactory.fappCreateHubApplication`
  / `fappCreateApplication`. Only the `start --gui` viewer registers a
  `role=viewer` session slot, so those are the viewer rows that appear
  in `vaibify sessions`.
- **The per-container host flock** (`~/.vaibify/locks/<name>.lock`) is
  the cross-process layer that keeps two *different* hub or viewer
  processes from opening the same container; it is the analog of a
  *kernel*, reaped when its holder dies.

A hub or viewer runs in the foreground of its launching terminal.
Closing the browser tab does nothing, and closing the terminal
*orphans* the server (reparented to `launchd`/`init`, `PPID 1`), which
keeps holding its session slot (`~/.vaibify/sessions/<pid>.slot`) and
its container flocks. The mechanisms below keep that from greying a
container out forever.

### The lease is the access principal

The host flock excludes a *second process*, but it cannot distinguish
two browser tabs talking to the *same* hub process — both originate
from loopback and both carry the same shared session token. The
exclusivity principal that tells two tabs apart is the **lease**: a
per-claim, server-minted `secrets.token_urlsafe(32)` value
(`containerOwnership.fsMintLease`).

`POST /api/registry/{name}/claim` mints the lease and returns it to the
claiming tab, which stores it in its own `sessionStorage` (per-tab, and
surviving a reload). Every subsequent access — the connect handler,
the pipeline WebSocket, and the terminal WebSocket — presents the lease
as the `sLeaseId` query parameter. The shared session token and the
loopback-origin check remain the *trust boundary* (CSRF / "a browser is
talking to this hub"); the lease is the *exclusivity* layer above it
("which browser session"). The lease is operational exclusivity for
honest researchers behind the loopback + shared-token boundary, not a
hard guarantee against a hostile in-page script.

### The owner-of-record map is the sole authority

A running hub keeps exactly one in-process authority,
`app.state.dictContainerOwners`, a map from container name to one
`OwnerRecord`. It replaces the two unreconciled gates of the old model
(a name-keyed flock plus the process-global `setAllowedContainers`
set). Claim, connect, and both WebSocket gates all consult this map and
nothing else.

`OwnerRecord` fields (in-process, dies with the hub process):

| Field                  | Meaning                                              |
|------------------------|------------------------------------------------------|
| `sLeaseId`             | The lease that owns this container                   |
| `fileHandleLock`       | The held host flock from `containerLock`             |
| `iLiveConnectionCount` | Live WebSockets for this container (the one-live invariant) |
| `fLastSeenMonotonic`   | When the last live connection dropped; starts grace  |

The host flock holder payload (the persisted, cross-process artifact at
`~/.vaibify/locks/<name>.lock`) is the **normative holder-payload
table**:

| Field          | Meaning                                                    |
|----------------|------------------------------------------------------------|
| `iPid`         | PID of the holding hub/viewer process                      |
| `iPort`        | Port that process serves on                                |
| `sStartedIso`  | Holder's start time; the recycled-PID staleness anchor     |
| `sProjectName` | Container name the lock guards                             |

`sStartedIso` is load-bearing and must appear on every holder payload —
see "PID-reuse-proof staleness" below;
`testLockPayloadCarriesStartedIso` enforces it.

### Claim arbitration

`containerOwnership.ftdictClaim` replaces the old short-circuit (the
pre-refactor claim returned `bClaimed: True` whenever the container was
already locked, silently admitting a second same-hub tab). The arbiter
now has three outcomes:

1. **Unowned** → acquire the host flock, mint a lease, record the
   owner, return `200 {bClaimed: True, sLeaseId}`.
2. **Owned, same lease presented** → idempotent success, return the
   same lease. This is the reload path: a refreshed tab re-presents its
   `sessionStorage` lease and re-asserts ownership with no new mint and
   no self-lockout.
3. **Owned, no lease or a different lease** → `409
   {bClaimed: False, sMessage: "In use in another browser session",
   sStartedIso}`, *unless* the current owner is reapable
   (`iLiveConnectionCount == 0`, past the grace window, and no pipeline
   running), in which case the dead owner is released and the claim is
   granted fresh. The 409 never echoes the other owner's lease.

### The one-live-connection invariant

Two tabs of one browser cannot both own a container: only the first
claim mints a lease and a foreign claim is refused. A *duplicate* tab
that copied the lease out of `sessionStorage` passes the idempotent
claim, so exclusivity for that case is enforced at the WebSocket gate
by `iLiveConnectionCount` — at most one live connection per container.
`fnIncrementLiveConnection` / `fnDecrementLiveConnection` keep the
count, and a second concurrent connection presenting the same lease is
refused.

### The shared authorization guard

`webSocketAuthorization.fbAuthorizeContainerSession` (and its
status-code form `fiContainerSessionRejectionCode`) is the one gate,
consumed verbatim by the pipeline WebSocket, the terminal WebSocket,
and the connect handler. A loopback browser must clear, in order,
loopback origin (`4003` on failure), shared token (`4401`), and owning
lease (`4403`). A non-loopback connection is never a browser; it is
admitted only through the lease-exempt **agent lane**
(`fbCheckAgentToken`): the in-container `vaibify-do` machine credential
authorizes the shared token alone, but only for a container that
already has a live owner, which scopes the lane to a session the
researcher has already claimed.

### The four release triggers

Ownership tracks the *live session*, never the process lifetime (the
old `setAllowedContainers` was append-only and leaked authorization for
the whole process life). A container is released by exactly four paths:

1. **Explicit release** — `POST /api/registry/{name}/release` with the
   matching lease (the dashboard's close affordance and the `pagehide`
   `navigator.sendBeacon`, which carries only the lease as its own
   proof). `fnReleaseOwnership` verifies the lease, frees the flock,
   drops the record, and stops the keep-alive.
2. **WebSocket-disconnect grace** — when the last live connection
   drops, `iLiveConnectionCount` falls to 0 and a bounded grace window
   opens. If no reconnect with the matching lease arrives, the idle
   sweep (`flistReapIdleOwnerships`) releases the owner and flock. The
   record is *retained* during grace, so a competing claim still gets
   409 — a brief network blip never evicts the owner.
3. **Claimed-but-never-connected reaper** — a crash before any
   WebSocket opened (count never rose above 0) is covered by the same
   sweep keyed on `iLiveConnectionCount == 0` past grace.
4. **Process teardown** — idle self-shutdown (below) or a manual quit
   sends SIGTERM, and uvicorn's graceful hooks release the flock and
   session slot.

The reaper is **never** allowed to release a container whose pipeline
is still running (`flistReapIdleOwnerships` takes a `fbPipelineRunning`
veto), so an in-flight run is never torn down — the dashboard's honesty
contract. The `pagehide` beacon only *accelerates* trigger 1; it never
fires on a hard crash and is never load-bearing for correctness, which
rests on triggers 2–4.

### Idle self-shutdown

Modeled on JupyterHub's `ServerApp.shutdown_no_activity_timeout`, both
the hub and the viewer run a watchdog (`_fnIdleShutdownWatchdogLoop`)
that self-`SIGTERM`s after a sustained idle period (30 minutes by
default, see [Configuration](configuration.md)). SIGTERM -- not a
direct teardown -- is deliberate: it lets uvicorn run the existing
graceful-shutdown hooks that release the locks and the session slot,
so the path that frees a container is the same whether the user quits
manually or the watchdog fires.

"Idle" is defined conservatively so a running pipeline is never
interrupted (the dashboard's honesty contract). The watchdog vetoes
shutdown when **any browser tab is connected** -- tracked by a live
WebSocket presence counter (`fnIncrementWebSocketCount` /
`fnDecrementWebSocketCount`) incremented right after a terminal or
pipeline socket is accepted and decremented in a `finally` -- or when
**any owned container is busy** (a pipeline is mid-run, per
`fileStatusManager._fbPipelineIsRunning`). The set of owned containers
is read from `dictContainerOwners.keys()`, the same owner-of-record
authority described above, so the busy veto can never lose track of a
held container and self-SIGTERM a hub mid-run. The busy check is rechecked
every tick, so a run that *starts between ticks* still blocks the next
decision. If Docker is unreachable when the busy check runs, the
container is treated as busy (fail-safe: keep the server alive rather
than risk killing a hub whose container is briefly unreachable). The
idle timeout is set well above the dashboard's poll and WebSocket-ping
intervals, so a single dropped signal never triggers a shutdown; only
sustained absence does -- the same guidance JupyterHub gives for its
cull timeouts.

### PID-reuse-proof staleness

When a server dies uncleanly, its slot and lock files survive. The
slot and lock registries share one reaper
(`pidFileRegistry.fnReapStaleFilesIn`, with `containerLock` and
`sessionRegistry` supplying the per-schema staleness predicate) that
decides whether a leftover file belongs to a dead holder. A bare
`os.kill(pid, 0)` existence
check is **not** sufficient: after the holder exits, the kernel can
hand its PID to an unrelated process, and the existence check then
reports the stale claim as live forever. In the incident that
motivated this design, a recycled PID defeated both reapers, so a dead
hub's container lock was never cleared and the container read "in use"
indefinitely.

`processLiveness.fbIsProcessAliveSince(iPid, sClaimIso)` closes the
gap. Every slot and lock payload records its holder's start time
(`sStartedIso`). The check reads the live process's start time from
`ps -o lstart=` (run with `LC_ALL=C` so month and day names parse
under any locale on macOS and Linux), normalizes both timestamps to
local-naive datetimes, and treats a process that started *after* the
recorded claim (beyond a small tolerance) as a recycled PID -- hence
dead and reapable. The probe degrades safely: an unreadable start
time, an absent claim, or a legacy payload without `sStartedIso` all
fall back to the bare PID-existence check, so a live genuine holder is
never reaped. No new dependency is introduced; the probe shells out to
`ps`, which is present on both platforms.

The `vaibify sessions` CLI (see [CLI Reference](cli.md)) is the
host-side enumerator over these same files -- the analog of
`jupyter server list` / `jupyter server stop`.

## Python backend

The backend lives under `vaibify/gui/` and is organized into four
layers by responsibility. Run `python tools/listModules.py vaibify/gui`
for the current module list with `__all__` exports and docstring
summaries.

### Application layer

- `pipelineServer.py` — FastAPI app factory, Pydantic models, shared
  utilities, WebSocket dispatch. Creates the app via
  `fappCreateApplication()`. Routes are delegated to the `routes/`
  package.
- `routeContext.py` — typed `RouteContext` wrapper for the `dictCtx`
  dict. Provides both attribute access (`dictCtx.docker`) and dict
  access (`dictCtx["docker"]`).

### Route modules

Route modules live under `vaibify/gui/routes/`. Each file matching
`*Routes.py` exports an `fnRegisterAll(app, dictCtx)` function that
registers its endpoints on the FastAPI application at startup.
`routes/__init__.py` imports every route module eagerly so that import
errors surface at startup rather than on first request.

Two route modules deserve a mention because their names do not fully
give them away:

- `pipelineRoutes.py` — pipeline state, kill, clean, acknowledge,
  file-status polling, test markers. This is where the polling
  endpoint lives.
- `syncRoutes.py` — Overleaf, Zenodo, and GitHub push and pull; the
  thin HTTP layer over `syncDispatcher`.

Run `python tools/listModules.py vaibify/gui/routes` for the current
list and each module's public API.

### Domain modules

These carry the core execution logic:

- `pipelineRunner.py` — pipeline step execution orchestrator. Public
  API: `fnRunAllSteps`, `fnRunFromStep`, `fnRunSelectedSteps`,
  `fnVerifyOnly`, `fnRunAllTests`.
- `pipelineUtils.py` — deliberate leaf module with zero intra-package
  imports. Contains `fsShellQuote` and all `_fnEmit*` event helpers.
  Exists to break circular import cycles. Do not add imports from
  `vaibify.gui` to this file.
- `pipelineValidator.py` — preflight validation (directory exists,
  scripts exist).
- `pipelineLogger.py` — logging callbacks, log file writing, state
  updates during execution.
- `pipelineTestRunner.py` — test execution within pipeline runs
  (per-category, legacy format).
- `interactiveSteps.py` — interactive step pause/resume/complete
  protocol.
- `pipelineState.py` — pipeline state persistence to
  `/workspace/.vaibify/pipeline_state.json`.
- `workflowManager.py` — workflow CRUD, variable resolution, step
  references, dependency graph. Uses `posixpath` because it operates
  on container paths.
- `fileStatusManager.py` — file-status polling, mtime tracking, step
  invalidation, verification freshness. The formal verification state
  machine is documented in its module docstring.
- `testStatusManager.py` — test result recording, aggregate state
  computation, test file cleanup.
- `fileIntegrity.py` — SHA-256 script hashing, path normalization,
  change detection.
- `syncDispatcher.py` — sync operations (Overleaf, GitHub, Zenodo),
  DAG visualization, test marker commands.

### Test generation modules

Vaibify attempts to generate tests deterministically from data. The
following files control test generation:

- `testGenerator.py` — orchestrator for test generation. Re-exports
  all symbols from the five modules below.
- `testParser.py` — Python syntax validation, import repair, code
  extraction. Zero intra-package imports.
- `dataPreview.py` — file preview generation (numpy, HDF5, text).
- `conftestManager.py` — pytest `conftest.py` plugin template and
  marker writing.
- `llmInvoker.py` — Claude API calls, prompt building, `CLAUDE.md`
  management.
- `templateManager.py` — template hashing, test code builders,
  template constants.
- `introspectionScript.py` — builds a self-contained Python script
  (as an f-string) that runs inside Docker containers to introspect
  data files. Intentionally duplicates format-handling logic from
  `dataLoaders.py` because container scripts cannot import from the
  host.
- `dataLoaders.py` — dispatch table mapping file extensions to loader
  functions. Used both at runtime and embedded in generated test code
  via `fsReadLoaderSource()`.

### Other modules

- `commandUtilities.py` — script path extraction from commands.
- `dependencyScanner.py` — code dependency analysis for scripts.
- `director.py` — standalone CLI runner. Has intentionally divergent
  `fbValidateWorkflow` and `fdictBuildGlobalVariables` from
  `workflowManager` because it operates on the host filesystem. See
  the tradeoff note above and the `AGENTS.md` trap list.
- `registryRoutes.py` — project registry API.
- `terminalSession.py` — PTY bridge for terminal WebSocket.
- `resourceMonitor.py` — container CPU and memory stats.
- `figureServer.py` — small utility; see source.
- `setupServer.py` — setup wizard host-side server.

## Dependency graph

```
pipelineUtils (leaf — zero intra-package imports)
commandUtilities (leaf)
pipelineState (leaf)
figureServer (leaf)
testParser (leaf)

workflowManager          <-- most modules depend on this
fileIntegrity            <-- pipelineRunner, fileStatusManager, syncDispatcher
pipelineValidator        <-- pipelineRunner (re-export)
pipelineLogger           <-- pipelineRunner (re-export)
pipelineTestRunner       <-- pipelineRunner (re-export, 1 deferred import back)
interactiveSteps         <-- pipelineRunner (re-export)

pipelineRunner           <-- pipelineServer, route modules
fileStatusManager        <-- pipelineServer (re-export)
testStatusManager        <-- pipelineServer (re-export)
syncDispatcher           <-- route modules

pipelineServer           <-- app entry point, imports everything
routes/*                 <-- imported by pipelineServer via routes/__init__.py
```

All imports are acyclic at module load time. One deferred import
remains: `pipelineTestRunner` defers importing `_ftRunCommandList`
from `pipelineRunner` to avoid a cycle (`pipelineRunner` eagerly
re-exports `pipelineTestRunner`).

## Re-export pattern

Several orchestrator modules re-export symbols from their extracted
child modules for backward compatibility:

- `pipelineRunner` re-exports symbols from `pipelineValidator`,
  `pipelineLogger`, `pipelineTestRunner`, `interactiveSteps`, and
  `pipelineUtils`. (`pipelineState` is imported as a namespace
  module, not re-exported symbol-by-symbol.)
- `pipelineServer` re-exports from `fileStatusManager` and
  `testStatusManager`, plus lazily via `__getattr__` from route modules.
- `testGenerator` re-exports from `testParser`, `dataPreview`,
  `conftestManager`, `llmInvoker`, and `templateManager`.
- `syncDispatcher` re-exports from `fileIntegrity`.

All modules declare `__all__` to make the public API explicit. Callers
should migrate toward importing from canonical modules directly; the
re-export shim exists for backward compatibility with the pre-refactor
layout.

## Verification state machine

Each workflow step carries a `dictVerification` dict. The formal state
machine is documented in `fileStatusManager.py`'s module docstring.
Key fields:

- `sUnitTest` — `untested | passed | failed`, set by the test runner.
- `sUser` — `untested | passed | failed`, set by the researcher
  clicking the UI badge.
- `sIntegrity`, `sQualitative`, `sQuantitative` — per-category test
  results.
- `bUpstreamModified` — `True` when an upstream step's outputs changed.
- `listModifiedFiles` — list of changed output paths, set by polling.

State transitions:

- Step executes → `sUser` resets to `untested`.
- Data file changes → `sUnitTest` resets to `untested`.
- Plot file newer than `sLastUserUpdate` → `sUser` resets to `untested`.
- Upstream changes → `bUpstreamModified = True`, `sUnitTest` →
  `untested`.

This state machine is load-bearing for the dashboard's honesty
guarantee: the GUI must always reflect the true state of the workflow.
See the relevant trap in [../AGENTS.md](../AGENTS.md).

## JavaScript frontend

The frontend lives under `vaibify/gui/static/` and uses the IIFE
pattern:

```javascript
var ModuleName = (function () {
    // private state
    return { publicApi };
})();
```

There are no build tools, no npm, no ES modules. Modules are loaded
via script tags in the HTML in a specific order. Run
`python tools/listModules.py vaibify/gui/static --format json` for the
current module list with public exports.

### Foundation modules (loaded first)

- `scriptUtilities.js` — `VaibifyUtilities`: pure functions
  (`fnEscapeHtml`, `fsSanitizeErrorForUser`, `fsFormatUtcTimestamp`,
  `fsResolveTemplate`, `fsTestCategoryLabel`).
- `scriptApiClient.js` — `VaibifyApi`: centralized fetch wrapper
  (`fdictGet`, `fdictPost`, `fdictPut`, `fnDelete`, `fbHead`). All HTTP
  calls go through this module.
- `scriptWebSocket.js` — `VaibifyWebSocket`: pipeline WebSocket
  connection, event dispatch via `fnOnEvent(sType, fnHandler)`, pending
  action queue.
- `scriptPolling.js` — `VaibifyPolling`: unified polling manager for
  file-status (5 s) and pipeline-state (10 s) intervals.

### Rendering, feature, and pre-existing modules

The rest of the frontend splits into rendering modules
(`scriptStepRenderer.js`, `scriptStepEditor.js`), feature modules (one
per panel or workflow: pipeline runner, test manager, container
manager, workflow manager, sync manager, dependency scanner, plot
standards, event bindings, file operations, modals, file browser,
directory browser, file pull, repos panel), and pre-existing modules
that predate the 2026-01 refactor (`scriptFigureViewer.js`,
`scriptTerminal.js`, `scriptResourceMonitor.js`,
`scriptSetupWizard.js`). `scriptFigureViewer.js` in particular is kept
as a single cohesive module; see the technical-debt list below.

### Core application

- `scriptApplication.js` — `PipeleyenApp`: application state,
  initialization, rendering orchestration. Exposes the public API that
  other modules call.

### State management

`scriptApplication.js` manages all state in three top-level objects:

```javascript
_dictSessionState = {
    sSessionToken, sContainerId, sUserName, dictDashboardMode
}

_dictWorkflowState = _fdictDefaultWorkflowState()
// Contains: dictWorkflow, sWorkflowPath, dictStepStatus,
// dictScriptModified, dictDiscoveredOutputs, dictUserVerifiedAt,
// all file caches, file check timers, undo stack

_dictUiState = {
    iSelectedStepIndex, setExpandedSteps, setExpandedDeps,
    setExpandedQualitative / Quantitative / Integrity,
    bShowTimestamps, iContextStepIndex, sContextFilePath
}
```

`_fnResetWorkflowState()` uses a factory function to reset all fields
atomically, preventing state leaks across workflow switches. Sets use
`.clear()` rather than reassignment so that references held by the
render context stay valid.

### Rendering

`fnRenderStepList()` is debounced via `requestAnimationFrame`:
multiple rapid calls (from WebSocket events, polling, user clicks)
coalesce into a single DOM rebuild. `fnRenderStepListSync()` is
available for the rare case where the DOM must be read immediately
after rendering.

Every render calls `fnUpdateHighlightState()` to synchronize the
toolbar verification indicator (checkmark and color shift) with the
current workflow state.

## Testing

The test suite lives in `tests/`. Run all non-Docker tests with:

```bash
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
```

The `testContainerBuildIntegration.py` tests require a running Docker
container and a configuration passed via the
`VAIBIFY_INTEGRATION_CONFIG` environment variable; they are excluded
from routine runs.

Architectural invariants are encoded as tests in
`tests/testArchitecturalInvariants.py`. That file is the authoritative
source for structural rules about the codebase (leaf modules, route
contracts, path-module conventions, science-agnostic source). When a
rule there changes, the test changes. When the code violates a rule,
the test fails. This is the deterministic half of the documentation
system — see [vibeCoding.md](vibeCoding.md) for the broader methodology.

## Known technical debt

1. `introspectionScript.py` duplicates format-handling logic from
   `dataLoaders.py`. This is inherent: the introspection script runs
   inside Docker containers that cannot import from the host Python
   environment. The duplication is a feature, not a bug.
2. `director.py` has its own `fbValidateWorkflow` and
   `fdictBuildGlobalVariables` that diverge from `workflowManager.py`.
   This is intentional: `director.py` operates on the host filesystem
   with `os.path` and `os.makedirs`, while `workflowManager` uses
   `posixpath` for container paths.
3. `scriptFigureViewer.js` was not part of the 2026-01 frontend
   refactor. It handles PDF rendering, dual-viewer comparison, and
   history management as a single cohesive module.
4. Re-export blocks across four orchestrator modules
   (`pipelineRunner`, `pipelineServer`, `testGenerator`,
   `syncDispatcher`) exist for backward compatibility. Callers should
   eventually migrate to importing from canonical modules directly.

Each debt item is load-bearing in a specific way: fixing it naively
breaks a working contract. The narrative here exists so a future
contributor can recognize these as deliberate rather than accidental.
