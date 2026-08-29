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

**Project.** A `project.json` file that declares an ordered sequence
of steps. Projects are checked into git, travel with the repository, and
reconstruct the same pipeline on a different machine. A project is a
portable unit of reproducibility.

**Step.** One unit of work in a project: typically a data command, a
plot command, or a test command. Steps declare their script and their
outputs, and they carry dependencies on the outputs of earlier steps.
Each step carries verification state.

**Verification state.** A structured record per step that answers three
questions. Did the unit tests pass the last time they ran? Has the
researcher looked at the output since it last changed? Has an upstream
step been modified without this step being rerun? Verification state
lives on the step, is persisted with the project, and degrades
automatically when the world underneath it changes. The full state
machine is defined in [fileStatusManager.py](../vaibify/gui/fileStatusManager.py).

**Dashboard as ground truth.** The browser GUI is the only place where
container status, project state, and verification state are surfaced
together. This is a rule, not an aesthetic. Nothing in vaibify may lie
to the dashboard: no optimistic status, no cached-past-lifetime state,
no quietly swallowed errors. If the truth is slow or ugly, the
dashboard shows it slow and ugly. The [AGENTS.md](https://github.com/RoryBarnes/Vaibify/blob/main/AGENTS.md) trap
list treats dashboard honesty as a hard invariant.

**Every action reachable from the command line.** The authority is the
backend action contract: every state-mutating action a researcher can
invoke is registered in one catalog,
[actionCatalog.py](../vaibify/gui/actionCatalog.py). The dashboard and
both command lines are three clients of that one contract — they call
the same routes. What sets the command lines apart is that they are
*generated* from the catalog rather than hand-written against it:
`vaibify do` on the host
([actionCommands.py](../vaibify/cli/actionCommands.py)) and
`vaibify-do` inside the container
([vaibifyDo.py](../vaibify/containerImage/vaibifyDo.py)). An action
added to the catalog appears in both without anyone maintaining a
parallel list, so neither CLI can drift from the contract.

Command-line reachability is the *default*, and departing from it
requires a recorded decision. This is philosophical, not convenient. A
data scientist works at the command line, and a project that can only
be driven by clicking is not reproducible: Level 3 means someone else
re-runs the work headlessly — on CI, on a cluster, in a batch job,
with no browser present. An accidentally GUI-only action would be an
action that cannot appear in a reproduction script, so it would be a
hole in the ladder rather than a missing convenience. A deliberately
GUI-only one is a considered trade-off, and the difference between the
two is the whole point of what follows.

The default is therefore enforced by accounting, not by coverage.
Where a
route is deliberately not CLI-invokable it is a written exception in
`SET_INTENTIONALLY_EXCLUDED_PATHS` with its rationale recorded beside
it, and `testAgentActionRegistered` fails CI — failing *closed* — for
any state-mutating route with no decision recorded either way. The
guarantee is therefore not that every route is on the command line; it
is that no route can quietly fail to be.

Read this together with the entry above, since both concern where
truth lives and they mean different things. The dashboard is where
truth is *displayed*, and it may never lie about what it displays. The
action contract is where capability is *defined*, and the command line
is its most direct expression.

## The happy path

The most concrete way to understand how vaibify verifies a project is to watch what happens
when a researcher clicks **Run All** in the browser.

1. `VaibifyPipelineRunner.fnRunAll()` fires in `scriptPipelineRunner.js`.
   The click was registered by the delegated handlers in
   `scriptEventBindings.js` and dispatched through `scriptApplication.js`.

2. The runner sends a single WebSocket message through `VaibifyWebSocket`,
   the singleton in `scriptWebSocket.js` that owns the connection to the
   backend. The payload is `{sAction: "runAll"}`.

3. On the backend, the WebSocket handler in `pipelineServer.py`
   dispatches actions to `pipelineRunner.fiRunAllSteps()`. The runner
   validates the project (via `pipelineValidator`), opens a log file
   (via `pipelineLogger`), and walks the step list.

4. For each step, the runner executes the step's command inside the
   container, streaming stdout and stderr back over the same WebSocket
   as `output` events. It emits `stepStarted` before the command runs,
   and `stepPass` or `stepFail` after it returns. Interactive steps
   pause and wait for the researcher via the protocol in
   `interactiveSteps.py`.

5. The frontend dispatches these events through `VaibifyWebSocket` to
   handlers registered by `scriptPipelineRunner.js`. Each handler
   updates the step's status via `VaibifyApp.fnSetStepStatus()` and
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
  -> VaibifyPipelineRunner.fnRunAll()
  -> VaibifyWebSocket.fnSend({sAction: "runAll"})
  -> Backend: pipelineServer WebSocket handler
  -> pipelineRunner.fiRunAllSteps()
  -> For each step: backend emits stepStarted, output, stepPass or
     stepFail via WebSocket
  -> Frontend: VaibifyWebSocket dispatches to registered handlers
  -> VaibifyPipelineRunner.fnHandlePipelineEvent()
  -> VaibifyApp.fnSetStepStatus() + VaibifyApp.fnRenderStepList()
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
the backend for the current state of every file the project cares
about.

```
Every 5 seconds (VaibifyPolling):
  -> VaibifyApi.fdictGet("/api/pipeline/{id}/file-status")
  -> Backend: pipelineRoutes._fnRegisterFileStatus handler
  -> fileStatusManager: compute mtimes, detect changes, check stale
     verifications
  -> Response: {dictModTimes, dictInvalidatedSteps, dictTestMarkers, ...}
  -> Frontend: VaibifyApp.fnProcessFileStatusResponse()
  -> Updates caches, applies invalidations, applies test markers
  -> VaibifyApp.fnRenderStepList() (debounced, cascading updates
     coalesce)
```

When a file changes, the affected step's unit-test state resets to
`untested`. When a plot changes, the user-verification state resets.
When an upstream step is modified, downstream steps are flagged as
upstream-modified. The researcher sees verification badges dim
automatically; no one has to remember to invalidate anything by hand.

Declared input data (`saInputDataFiles` — raw files a step consumes
that no step produces) rides the same poll: input paths join the
stat batch, an mtime delta on a declared input invalidates every
step that declares it (matched by full resolved path against the
repository root, never by basename), and the marker-hash pass reads
`dictInputHashes` alongside `dictOutputHashes` so content drift with
a preserved mtime is caught while a fresh clone with identical
content stays green. The staleness rows label the input lane
distinctly ("Input data modified since last run").

Run dispatch carries one more gate beside the busy-refusal: a run
covering a step whose `listRemoteData` files already exist on disk
is answered with `runRefused` `sReason=remoteDataOverwrite` unless
the request confirms the overwrite. The gate lives at the single
WebSocket dispatch choke point so the browser and the in-container
agent meet the identical rule; the interactive Run-in-Terminal
buttons never reach dispatch (they compose a shell command
client-side), so that one lane carries the same check in the
frontend — a documented exception, not an enforcement path.

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
some platforms) modified `project.json` or a step script in place.
The cache layer has been removed: every poll stats the polled paths
directly. This still costs exactly one `docker exec` per poll
(the dominant wire cost) and trades a small per-poll byte increase
on the path-list for the dashboard's honesty contract — the same
contract the [AGENTS.md](https://github.com/RoryBarnes/Vaibify/blob/main/AGENTS.md) "do not suppress or
misrepresent state" trap enforces for every other surface.

Four module-level booleans in `scriptPolling.js` — one each for
pipeline, file-status, repos, and discovery — short-circuit a poll
tick when the previous tick is still pending. These are
duplicate-request suppressors, not state caches. They do not cache
server responses, do not extend mtime values, and do not affect what
the next successful poll sees. Do not extend them into result
caching: that would re-introduce the stale-dashboard failure mode
the [AGENTS.md](https://github.com/RoryBarnes/Vaibify/blob/main/AGENTS.md) "do not suppress or misrepresent
state" trap warns about.

### The poll's freshness stamp, and why it ships `no-store`

The file-status response carries an `ETag` so a client that echoes it
in `If-None-Match` gets a `304` instead of a payload that can reach
500 KB on a large project. That optimization has one failure mode,
and the repository shipped it: the stamp was assembled from a
hand-maintained list of signals, so every field added to the payload
afterwards fell outside it. Two responses differing only in
`dictRunState` — an agent-dispatched run starting, say — hashed
identically, and a client holding the stale body could not tell.

Two changes make the stamp honest, and both are load-bearing:

- The stamp is derived from the **whole serialized payload** minus an
  explicit volatile-key set, so a field added tomorrow is covered the
  moment it exists. A hand-maintained signal list is exactly the
  artifact that drifts.
- The response carries `Cache-Control: no-store`
  (`S_FILE_STATUS_CACHE_CONTROL`). Without it, a `200` bearing an
  `ETag` and no freshness directive is heuristically cacheable: the
  browser may revalidate on its own and hand the JavaScript a cached
  body it never re-downloaded. The frontend manages no ETags itself,
  so that revalidation would be invisible to it.

The stamp is the correctness fix; `no-store` is the belt-and-braces
guarantee that no private cache can serve a stale dashboard behind a
revalidation the application cannot observe.

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
originates from an HTTP request body, a `project.json` field, or a
config file must be validated against its intended root before the
backend opens it. `fsValidatePathWithinRoot(sAbsPath, WORKSPACE_ROOT)`
in `pipelineServer.py` is the canonical guard; the trap list in
[AGENTS.md](https://github.com/RoryBarnes/Vaibify/blob/main/AGENTS.md) flags this explicitly.

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

**`posixpath` everywhere a container path is handled.**
`workflowManager` manipulates container paths, which are POSIX on
every host operating system, so it uses `posixpath` rather than
`os.path`. A host-side module handling host paths must use `os.path`,
because those carry the host's native separator. Unifying the two
would either mangle Windows host paths or mangle container paths on
any host, and the failure would be silent until a cross-platform user
hit it. The repository formerly carried a host-side `director.py`
whose deliberate divergence from `workflowManager` illustrated this;
it was withdrawn in favour of `vaibify reproduce --rerun`, which
re-runs a project through the container and therefore reproduces the
environment as well as the steps.

## Project = git repo

Every vaibify project lives inside a git repository — its
"repository". The `project.json` file belongs to that repo, not to the
container, not to `/workspace`, and not to a shared vaibify-managed
location. This constraint is enforced at discovery time
(`flistFindWorkflowsInContainer` drops any candidate not inside a git
work tree) and at creation time (`_fsValidateRepoDirectory` rejects
target directories that are not git repos). It maps directly to L1 of
the reproducibility ladder in [vision.md](vision.md): a project that
cannot be committed cannot be reproduced.

`/workspace` itself is a Docker-managed named volume, not a repo. It
is the *discovery root* — the search origin for project.json files —
but not a git target. Inside a container, `/workspace` contains N
repository subdirectories (each a standalone git clone) plus some
shared configuration. A single container can therefore host multiple
projects: ParameterSweep's paper pipeline today, SurveyCatalog's
cross-system analysis tomorrow, both reusing the same heavy dependency
clones without needing a rebuild.

The **active project determines the badge scope**. At connect time,
`fdictHandleConnect` runs `git rev-parse --show-toplevel` inside the
container, starting from the directory that contains the loaded
`project.json`. The result is stamped on the workflow dict as
`dictWorkflow["sProjectRepoPath"]` and every subsequent git / badge /
manifest call threads it through `containerGit` as the authoritative
workspace. The helper lives in
`containerGit.fsDetectProjectRepoInContainer`; the routes read it from
the active workflow dict.

Per-step output paths (`saOutputDataFiles`, `saPlotFiles`)
must be repo-relative and must stay inside the repository. Absolute
paths and `..`-escaping paths are rejected by
`flistValidateOutputFilePaths` on save. Step directories (`sDirectory`
on each step) are held to the same rule by `flistValidateStepDirectories`
— a value like `/workspace/ParameterSweep/PosteriorCorner` is rejected; the
repo-relative form `PosteriorCorner` is required. Input references
inside `saCommands` / `saPlotCommands` / `saDataCommands` are
deliberately *not* validated — a step may legitimately read an
absolute `/workspace/SurveyCatalog/Plot/foo.pdf` produced by a sibling
project. Badges are emitted only for the producing project; a
consumer project sees the file as a read path, not as a tracked
artifact.

Test markers — JSON files that record the outcome of the last pytest
session for each step, including `dictOutputHashes` for staleness
detection — live inside the repository at
`<sProjectRepoPath>/.vaibify/test_markers/<slug>.json` where the slug
is derived from the step's (repo-relative) `sDirectory`. Marker
*writes* (by the conftest plugin deployed into each step's `tests/`
directory) and *reads* (by `fileStatusManager`, `gitRoutes`,
`syncDispatcher`) both resolve the directory through
`dictWorkflow["sProjectRepoPath"]` — no module hardcodes
`/workspace/.vaibify/test_markers`. Together with committing the
markers alongside the project, this makes test-verification state
survive a clone of the repository.

This choice has two architectural consequences worth naming:

- **No workspace-root projects.** A `project.json` at `/workspace`
  (outside any enclosing git repo) cannot be reproduced and is not
  allowed. The `pipelineServer` surfaces this by stamping an empty
  `sProjectRepoPath`, at which point the four `/api/git/*` endpoints
  return the explicit "Workflow is not in a git repository" payload
  rather than silently reporting `bIsRepo: false` against `/workspace`.
- **Forward-compatible multi-project model.** The workflow-dict field
  is the anchor for a future project-selector UI: when the user
  switches active projects in a container, the cache key widens to
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
resolved from the active project's `sProjectRepoPath`.

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
from loopback, and each now carries its own per-bootstrap credential
(the shared session token was retired in the sweep-A rewrite). The
exclusivity principal that tells two tabs apart is the **lease**: a
per-claim, server-minted `secrets.token_urlsafe(32)` value
(`containerOwnership.fsMintLease`), bound to the browser session that
claimed it.

`POST /api/registry/{name}/claim` mints the lease and returns it to the
claiming tab, which stores it in its own `sessionStorage` (per-tab, and
surviving a reload). Every subsequent access — the connect handler and
the pipeline WebSocket — presents the lease
in the `X-Vaibify-Lease` header (a header, not a query parameter, so it
cannot land in a log). The per-session credential and the
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

#### Which control-plane routes are lease-enforced, and which are a deliberate residual

Not every state-mutating route is gated on the owning lease, and that is
intentional. The `container-owner` HTTP routes (the `{sContainerId}`
viewer routes) and the two routes that touch a live session's integrity —
`POST /api/connect/{sContainerId}` and `POST /api/registry/{name}/release`
— require the **session-bound** lease
(`containerOwnership.fbBrowserSessionOwnsLease`): a second browser session
replaying a *copied lease value* is refused, because connect would take
over the workflow and the container's agent session, and release would
drop the owner record. Connect enforces this in
`workflowRoutes._fnRequireOwningLeaseForConnect`; release enforces it in
`containerOwnership.fbReleaseOwnership`.

The name-keyed container-lifecycle routes — `start`, `stop`, `build`,
`settings`, and the ownership-*establishing* `claim` — are classified
`browser-hub` in `routeScope.DICT_CONTROL_PLANE_SCOPES` and are **not**
lease-enforced. This is a considered residual, not an oversight. The hub
is single-user, so the lease is live-session *coordination*, not an
authorization boundary against a hostile peer; the container picker
operates on these routes *before and across* claims, when no lease exists
yet; and safe owner-gating of `stop`/`settings` depended on the
ORPHANED_SESSION takeover lifecycle (a crashed owner's container must stay
stoppable). **That lifecycle has since landed**, and with it the
takeover path (`vaibify open`) that makes gating safe, so `stop` and
`settings` are now lease-enforced under the `container-lifecycle` scope:
the lease is required when the container is owned and the operation is
permitted when it is not, which keeps a crashed owner's container
stoppable without leaving it open to any tab. `build` and `claim`
remain `browser-hub` by decision — `build` is an image operation with
no owner, and `claim` is what *establishes* ownership, so neither can
require a lease it does not yet have.

`OwnerRecord` fields (in-process, dies with the hub process):

| Field                          | Meaning                                              |
|--------------------------------|------------------------------------------------------|
| `sLeaseId`                     | The lease that owns this container                   |
| `fileHandleLock`               | The held host flock from `containerLock`             |
| `iLiveConnectionCount`         | Every live WebSocket for this container (liveness for the reaper/watchdog) |
| `iLivePipelineConnectionCount` | Live *pipeline* WebSockets only (the one-live-pipeline budget) |
| `fLastSeenMonotonic`           | When the last live connection dropped; starts grace  |

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

`containerOwnership.ftClaim` replaces the old short-circuit (the
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

### Starting a container is a server-owned reservation

Starting a container is not a request-scoped action. A pull can outlast
any HTTP timeout, the response can be lost, the button can be clicked
twice, and `docker run` does not name the container it is creating until
it returns — so a start that has to be killed leaves one the hub can only
guess at. `POST /api/containers/{sName}/start` therefore *reserves*:

- It arbitrates ownership through the same claim primitive a browser
  claim uses (host flock, journal quarantine, cross-hub refusal, and the
  one-container-per-session reverse index all in one place), then attaches
  a **`StartReservation`** to the owner record and answers `202` with a
  status-poll location — **never a lease**, because nothing is running yet
  for a lease to authorize.
- The reservation is an **orthogonal axis**, not a state: a record can be
  `ACTIVE` and starting, or `ORPHANED_SESSION` and starting. It holds only
  live execution state (stable id, the launch process handle, the journal
  record id, a heartbeat) — no session, lease, or generation copies, and
  no outcome.
- While it is live: a repeated start by the initiating session returns the
  same reservation (the idempotent recovery, never a second launch);
  another session is refused; `stop` and `settings` answer `409` "still
  starting"; a connect by the initiator gets a truthful pending refusal;
  and the record is never reapable, so the idle watchdog cannot free the
  flock under a running `docker create`.
- The Docker work is a **create-then-start pair under `Popen`**. The
  container carries `--label vaibify.reservation=<id>` and its id is
  written to the write-ahead journal *before* it is started, so cleanup
  removes exactly that incarnation and no other. Cancelling escalates
  TERM → bounded wait → KILL and waits for the real exit; only then is the
  labelled container removed, the reservation compare-and-deleted, and the
  flock freed. If the daemon's answer is uncertain the container is
  **quarantined, never made claimable** — killing the CLI does not prove
  the daemon abandoned the request.
- Cancellation is a **distinct explicit operation**. A host transfer
  *adopts* a running start (retagging it as a mode-(c) durable task) and
  never doubles as a cancel.

The outcome lives in a bounded in-memory ledger that outlives the
reservation, with **two delivery paths**, because success and failure
authorize differently. **SUCCEEDED** is bound to the live owner record and
hands back a **freshly derived** lease, so a `vaibify open` successor can
collect a start its predecessor requested and a revoked session cannot.
**FAILED** has no owner left to authorize it — that is the case the ledger
exists for — so it is a bounded, session-bound retrieval entitlement,
rebound by a transfer, that yields the safe error and **no container
authority of any kind**. A new start after a failure must name the
reservation id it read, so a stale failure can never silently relaunch.

### The one-live-pipeline-connection invariant

Two tabs of one browser cannot both own a container: only the first
claim mints a lease and a foreign claim is refused. A *duplicate* tab
that copied the lease out of `sessionStorage` passes the idempotent
claim, so exclusivity for that case is enforced at the WebSocket gate —
but scoped to the **pipeline lane**. One legitimate session may hold
several sockets at once. Budgeting *all* sockets shipped the
Run-Step-always-refused bug: the terminal, which opened its socket on
project entry, held the single slot, every pipeline connection was
closed 4409, and the browser reported a healthy server as unreachable.

So the budget is: at most one live **pipeline** WebSocket per container
(`iLivePipelineConnectionCount`); sockets on any other lane are counted
in `iLiveConnectionCount` for liveness (the reaper and the idle watchdog
read it) but are never refused. The terminal is that lane's production
caller, which is exactly why the budget must never be extended back
over it. `fnIncrementLiveConnection` /
`fnDecrementLiveConnection` keep both counts, and a second concurrent
pipeline connection presenting the same lease is refused with 4409.

Every deliberate refusal (4003/4401/4403/4409) is sent **after** the
handshake is accepted (`fnCloseWithCode`): closing before `accept`
downgrades the refusal to an opaque HTTP 403, which a real browser can
only observe as close code 1006 — indistinguishable from a dead server.
The client treats 4xxx closes as final (no reconnect ladder) and
reports the true reason.

Run exclusivity itself does not ride on socket accounting: the message
loop refuses a dispatch while another pipeline action for the same
container is still live (`_fbRefuseWhilePipelineTaskLive`, answered
with a `runRefused` event). That guard holds for every lane — a
duplicated tab, a reconnected socket after a mid-run detach, and the
in-container `vaibify-do` agent (which is exempt from the connection
budget) — so two runs can never race inside one container.

### The shared authorization guard

`webSocketAuthorization.fbAuthorizeContainerSession` (and its
status-code form `fiContainerSessionRejectionCode`) is the one gate,
consumed verbatim by the pipeline WebSocket and the connect handler.
The terminal route consults it not at all: it is disabled and refuses
as its first statement, precisely so an unauthenticated dial-in cannot
reach a gate whose side effect is refreshing the owner's liveness. A loopback browser must clear, in order,
loopback origin (`4003` on failure), shared token (`4401`), and owning
lease (`4403`). A non-loopback connection is never a browser; it is
admitted only through the lease-exempt **agent lane**
(`fbCheckAgentToken`): the in-container `vaibify-do` machine credential
is a **per-container agent token** minted on the container's owner
record (`OwnerRecord.sAgentToken`) and written into that container's
`/tmp/vaibify-session.env` at connect. It authorizes only the container
whose owner minted it — never the hub-wide session token and never
another container's token — so an agent compromised in one container of
a multi-container hub cannot authenticate against another. The REST
`SessionTokenMiddleware` enforces the same per-container scoping by
matching the presented token against the owner of the container id named
in the request path; a request that names no container fails closed.

### `bAgentSafe` is enforced, not advertised

Authorizing the agent lane answers *which container* an agent may act
on. It does not answer *what it may do there*, and for a long time
nothing did. `ffnAgentAction` attaches a name to a handler and changes
no behaviour; `bAgentSafe` was consumed only by `vaibify/containerImage/vaibifyDo.py`
**inside** the container, which an agent bypasses with `curl`. Every
route the catalog marked researcher-only — `clean-outputs`,
`delete-step`, `declare-determinism`, `supervision/configure`,
`publish-to-zenodo` — was reachable by a compromised agent on its own
container. The exclusion set's own rationale, that "the supervised
party must never switch its own supervision on or off", was false.

`SessionTokenMiddleware` now resolves each request to its **route
template** (via the router's own matcher, so it cannot disagree with
dispatch) and refuses the agent lane for any route whose catalog
entries are all `bAgentSafe: False`, for anything in
`SET_INTENTIONALLY_EXCLUDED_PATHS`, and — **failing closed** — for any
state-mutating route carrying no catalog entry at all. Adding a route
and forgetting to register it now denies the agent rather than
silently admitting it.

Two limits are worth stating rather than discovering. The gate is
HTTP-only: `BaseHTTPMiddleware` never sees a `websocket` scope, so
WebSocket actions are outside it — every WS catalog entry is
agent-safe today and `testEveryWebSocketActionIsAgentSafe` fails CI if
a user-only one appears, but that is a tripwire, not enforcement. And
routes that read host state need their own refusal at the handler
(`routeContext.fnRejectAgentTokenLane`), because a host read is a
capability question the catalog alone cannot express. That includes
routes that read no file at all: `has-credential` asks the host
keyring whether a service token exists, which is one bit about the
researcher's own machine, and a GET is never state-mutating so the
catalog gate never sees it.

### The four release triggers

Ownership tracks the *live session*, never the process lifetime (the
old `setAllowedContainers` was append-only and leaked authorization for
the whole process life). A container is released by exactly four paths:

1. **Explicit release** — `POST /api/registry/{name}/release` with the
   matching lease, from the dashboard's close affordance. There is no
   unload beacon: `pagehide` fires on reload and navigation, not only
   on a real close, so treating it as release intent would drop a
   running container on a mere refresh. The handler stops polling and
   nothing else. `sessionLifecycle.ftReleaseExplicit` arbitrates —
   refusing with 409 while a run or a live agent holds the container —
   then frees the flock, drops the record, and stops the keep-alive.
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
contract. Correctness rests entirely on triggers 2–4: no unload signal
is load-bearing, because none is sent. `pagehide` would in any case
never fire on a hard crash, which is why abandonment is decided by the
socket closing without a reconnect rather than by anything the
departing page claims about itself.

### Idle self-shutdown

Modeled on JupyterHub's `ServerApp.shutdown_no_activity_timeout`, both
the hub and the viewer run a watchdog (`_fnIdleShutdownWatchdogLoop`)
that self-`SIGTERM`s after a sustained idle period. SIGTERM -- not a
direct teardown -- is deliberate: it lets uvicorn run the existing
graceful-shutdown hooks that release the locks and the session slot,
so the path that frees a container is the same whether the user quits
manually or the watchdog fires.

The timeout is not a fixed constant. It is resolved at startup
(`_ffResolveIdleTimeoutSeconds`) across three precedence tiers — the
`VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS` env override, then the stored
host-global Settings preference, then the launch default — and
published on `app.state.fIdleTimeoutSeconds`. The watchdog re-reads
that attribute every tick (`_ffCurrentIdleTimeout`), so the gear
menu's **Idle shutdown** control applies **live**: a change updates
`app.state` and the loop honours it on its next pass, no relaunch. The
**launch default is never** (`math.inf`, disabled) for a browser
launch and `1800` seconds only for a headless/remote launch (browser
suppressed via `VAIBIFY_SUPPRESS_BROWSER`) — a researcher at the
dashboard is never reaped, but an abandoned headless server still
retires. "Never" has no finite sentinel: `0` keeps its historical
"retire as soon as idle" meaning, and disabled is carried as
`math.inf`, which `_fbHubShouldSelfExit` treats as never-exit because
no finite idle span reaches it.

"Idle" is defined conservatively so a running pipeline is never
interrupted (the dashboard's honesty contract). The watchdog vetoes
shutdown when **any browser tab is connected** -- tracked by a live
WebSocket presence counter (`fnIncrementWebSocketCount` /
`fnDecrementWebSocketCount`) incremented right after a pipeline socket
is accepted and decremented in a `finally` -- or when
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

### Session lifetime: two windows, three tiers, one honest notice

A browser session is bounded by two windows, and they relate to a live
socket differently on purpose.

**Sliding idle** is refreshed by every request and **vetoed by a live
WebSocket**: a dashboard that only streams events is doing something,
and the socket layer never refreshes the credential's last-seen stamp,
so without the veto a streaming dashboard would be revoked under the
researcher.

**The absolute cap** is measured from the session's creation and fires
**regardless of socket liveness**. That asymmetry is the point, and it
is worth stating outside a docstring because it looks like an
oversight: the case the cap exists to bound is a forgotten-open tab,
which holds a live socket *by definition*, so a veto generalized to
both triggers would make the cap unreachable in exactly its target
case.

Both windows resolve across the same three tiers — the environment
override (`sessionLifecycle.S_ABSOLUTE_SESSION_CAP_ENV`,
`S_SLIDING_IDLE_ENV`), then the host-global Settings preference in
`~/.vaibify/preferences.json`, then the built-in default that
`F_ABSOLUTE_SESSION_CAP_SECONDS` and `F_SLIDING_IDLE_SECONDS` carry.
The environment tier wins because it is what the test lanes drive.
Resolution happens **at every evaluation**, not once at import: a
change needs no hub restart, and — the property that made it worth
doing — *raising* the cap rescues a session that has not expired yet,
which is what a researcher wants at the moment they notice the
warning. "Never" is its own named choice
(`preferencesStore.SET_NEVER_TOKENS`, carried as `math.inf`) rather
than a very large number, because a 30-day cap outlives every hub
process, so it would never fire while the dashboard still claimed a
bound existed.

The designed mitigation for the cap is the pre-expiry dashboard
warning (`fdictSessionExpiryView`, lead
`F_EXPIRY_WARNING_LEAD_SECONDS`). **It assumes an audience it
structurally may not have**: a cap started in the afternoon expires in
the small hours. So the hub also answers afterwards. Revocation
records the sentence and the wall-clock time on the session record
(`BrowserSessionRecord.sEndedMessage`), and the middleware's 401
carries it, so a returning researcher is told what ended their session
and what became of the container instead of meeting a bare
"Unauthorized" — which, before this, the dashboard rendered as "the
server has been restarted", a guess that is false in exactly the case
that produces most 401s. The notice is keyed on the credential the
caller already presents, so it discloses nothing.

### Sleep prevention follows the work, not the tab

The macOS `caffeinate` keep-alive used to have one lifetime: the
ownership record's. `containerOwnership._fnForceReleaseOwnership`
stops it, so the machine became sleepable a reconnect window plus a
reap grace after the browser went away. A dashboard-launched pipeline
survived that only because the reaper is vetoed while vaibify's own
`bRunning` flag is set — and that flag is vaibify's own bookkeeping,
not a process scan. Work vaibify did not launch (a job backgrounded in
a terminal, an exec an in-container agent started, or **any** exec at
all once the hub that launched it has been restarted) had no veto, so
the record was reaped, the keep-alive died, and the laptop slept with
the job still running. Under colima the VM suspends rather than dies,
so the run is *frozen*, not killed, and looks healthy until somebody
reads the timestamps.

`sleepPrevention` gives the keep-alive a second lane whose lifetime is
the work's. The session lane is unchanged and keyed by container name;
the **work lane** is keyed by `fsWorkLaneKeepAliveName` — a registry
name containing a character Docker forbids in a container name, so the
two lanes can never stop each other's process. The work lane is
asserted and withdrawn from observed evidence on every hub-watchdog
pass, immediately after the reaper, so a record dropped on one tick is
re-examined as *work* on the same tick.

The evidence is `DockerConnection.flistRunningExecIdentifiers`: does
the daemon report any exec session in this container still running?
**It is evidence of work, never proof of work's absence.** A `setsid`
descendant whose parent exec has exited is invisible to it, exactly as
it is invisible to `terminalContainment`'s process-group prover.
Vaibify cannot prove what runs inside a container and does not claim
to. What it does claim is bounded and true: while it sees a running
exec it keeps the machine awake, and when it sees none it stops paying
for a keep-alive it has no reason to hold. An unreadable daemon is
read as evidence *present* — the two errors are not symmetric, since
withdrawing a keep-alive under a multi-day job costs the job while
holding one nothing needs costs some battery.

Because the lane is derived from observation rather than from an
in-process record, a hub that crashed and restarted **re-establishes**
the keep-alive for work its predecessor launched. The corollary is
that a work-lane keep-alive can outlive its hub; the next hub's first
sweep is what withdraws it.

### What survives what (measured, 2026-08-29)

Run against a live daemon (colima) rather than reasoned about, because
neither reading the code nor reasoning settles it:

- **An in-container exec survives the death of the client holding its
  stream.** SIGKILL the process that called
  `exec_start(stream=True)`; the exec keeps running, reparented to the
  container's init, and keeps writing its output inside the container.
- **Its outcome remains recoverable.** `exec_inspect` on the exec id
  answers `Running` while it runs and settles with the real
  `ExitCode` afterwards, to a *different* client than the one that
  started it. This is why the durable-task launch journals the exec id
  **before** `exec_start`: the journaled id is a probeable handle, and
  the experiment is what makes that worth relying on.
- **What is lost is the stream, not the work.** Re-attaching with
  `exec_start` on an already-started exec yields no output. A hub that
  died mid-run can learn *that* and *how* its step finished; it cannot
  recover the lines it was not there to read.
- **A terminal-backgrounded job survives too** — both a plain `&` job
  and a `setsid` one — and so does the interactive shell itself. The
  `setsid` job reparents to the container's init and carries its own
  session id, which is precisely the descendant no process-group
  prover can see.
- **The daemon prunes finished execs** from a container's `ExecIDs`,
  so the list is a live set rather than an accumulating log. Each id
  is still confirmed through `exec_inspect`, because the pruning is
  observed behaviour of one daemon while `Running` is a stated one.

The practical reading: **a hub restart does not stop a run.** It stops
vaibify *watching* the run. Anything that must survive a restart has
to be recoverable from the journal and the filesystem, never from the
hub's memory.

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

## Host mode: the same hub, a different substrate

A project is either **containerized** or **host**. A host project has
no image, no container and no volume: its pipeline runs directly on the
researcher's machine, in the directory they registered. It exists
because the image build ends most first encounters with vaibify before
they begin (see [philosophy.md](philosophy.md) for the stance, which is
that the container remains the default and the destination).

Almost nothing above changes, and that is the design. The ownership
model — flock, lease, two-tab arbitration, orphan and expiry, transfer
— was already Docker-free and name-keyed, so it is reused whole. The
mutation boundary is reused whole. The journal is reused whole, with
one new record kind. What is swapped is the *substrate*, at exactly one
seam.

**The seam is the connection object.** `dictCtx["docker"]` holds a
`ConnectionRouter` that dispatches per call on the resource id every
call site already passes: a Docker container id routes to
`DockerConnection`, a registry name that names a host project routes to
`HostConnection` (`vaibify/host/hostConnection.py`), which implements
the same duck-typed surface against `subprocess` and `os.*`. The
router's twelve delegations are explicit rather than a dynamic
`__getattr__`, so the capability inventory can read them. A host-path
*fork* of the workflow manager was tried once (the withdrawn
`director` module) and abandoned: swap the connection, never fork the
path handling.

**Every host subprocess is gated and journaled, with no exceptions.**
The child is spawned suspended behind a stdin gate in its own session;
a `host-exec` journal record carrying its recycle-proof identity (PID,
process group, in-flight stamp) is persisted and identity-gated; only
then is the gate released. A crash at any point leaves an *identified*
record rather than a process nobody can name. This is the host
analogue of Docker's `exec_create → journal → exec_start` split, and it
is what makes the quiescence claim — "every process vaibify started has
exited" — sayable at all. The record carries a bounded operation label
(`pipeline-step:A03`, `git-status`), never command text, because the
journal's schema allowlist admits no commands.

**What that claim is NOT.** A command can `setsid` out of its process
group, and nothing in the journal can see it. Host mode therefore never
says "nothing is running"; it says what it can prove, in the quarantine
copy, in the Cancel confirmation, and in the CLI. This is the same
boundary the interactive terminal was withdrawn over.

**Cancel signals a recorded group, never a matched name.** The
container lane greps its own process table, which is safe there
because the whole table belongs to vaibify. On the host that same
sweep matches the researcher's editor. So the host lane signals only
process groups it journaled, and only while the recorded identity is
still *provable* — a PID that vanished may have been handed to
something else. An unprovable record is reported and routed to
reconciliation, never guessed at (`vaibify/host/hostCancellation.py`).

**Two quarantine exits, and they are not the same act.** A container's
break-glass stops the container first, so clearing the marker
afterwards rests on something proven; it refuses a host project by
name. A host project instead has `--terminate-recorded`, which signals
the journaled groups and re-runs the proof, and — for a marker too
damaged to parse — `--abandon-host-journal`, which proves nothing and
says so. Abandonment writes an attributable audit entry (project name
*and* canonical directory, marker sha256, UTC timestamp, host uid and
session) beside the journal, appended and fsynced **before** the marker
is unlinked and idempotent by marker hash, so "a marker abandoned with
no record of who abandoned it" is unreachable rather than unlikely.

**Four capabilities are given up by name.** PROOF Level 3 is defined
by a pinned image; Supervised attribution is only honest when vaibify
mediates every path to the files; the agent lane does not exist,
because on the host the agent *is* the user and `bAgentSafe` has no
discriminator left; and the Agent Council is refused, because it
grounds its claims by building a disposable container and proving it
gone, which a host project has none to create. Each is refused at its
own door with a message naming the mode, rather than degrading into a
misleading cascade.

**Which root, asked per resource.** `/workspace` was written as a
constant wherever code needed "the root this project's files live
under", because until host mode there was only one answer.
`vaibify/gui/projectRoots.py` asks the question instead, and answers
it twice over: `fsResolveProjectRoot` for a project's own files, and
`fsResolveScratchDirectory` for the ephemeral ones — the throwaway
program an introspection runs, the DOT source a diagram is rendered
from, the file a credential passes through. A container's scratch is
`/tmp`, disposable by construction; a host project's is a private
0700 directory under the diagnostics subtree, which is the only
ephemeral root its path guard admits. The container answer is passed
in at every call site rather than known here, so this module never
becomes a second authority on what those roots are.

**Which keyring, likewise.** A container project's service tokens live
in the container's keyring, reachable only from inside it and thrown
away with it. A host project's live in the researcher's own OS
keyring, which is where Overleaf's token already went in both modes
because the Overleaf push has always run on the host. The dispatchers
in `syncDispatcher` pick the store; the `InContainer` primitives
beneath them are unchanged, because that is still exactly what they
do.

**Paths.** Every direct path argument and working directory is
validated against exactly two roots — the project directory and the
project's `~/.vaibify/tmp/host-diagnostics/<digest>/` scratch subtree —
with symlinks resolved before containment is checked. It defends
against hostile wire input; it deliberately cannot see paths embedded
inside opaque workflow shell text, and the warning modal owns that
disclosure. Windows is refused outright: there the `bash -c` command
composition and the POSIX guards weaken silently rather than failing.

## Container mutations announce themselves

The section above says a container is owned by one session at a time
and that ownership can be handed over. That is only half a guarantee.
The other half is that a hand-over must not commit while the previous
owner's work is still running -- and until the 2026-08 migration,
nothing enforced it.

The concrete failure: "clean outputs" started a `rm` on a worker thread
that nothing tracked, and answered immediately. A hand-over arriving a
second later asked "is anything running in this container?", saw an
idle container because the delete was invisible, and committed. The new
owner then held a container quietly deleting the previous owner's
files, and neither session was told. On a single desktop this is not
two researchers fighting over a server; it is the in-container AI agent
and the dashboard acting at once, or a researcher reclaiming a
container after a reload.

**The carrier is the thing that makes work visible.** A route that
mutates a container opens an admission through
`vaibify/gui/commitCarrier.py` around each logical mutation, in one of
three shapes:

| Mode | Shape | Used when |
|---|---|---|
| (a) synchronous | linearized commit plus journal transition, inside the request | one bounded write, e.g. saving `project.json` |
| (b) lock-held | holds the container mutation lock for the worker's whole lifetime, and registers what it is doing | work that crosses a thread boundary or runs long -- a delete, a push, a test run |
| (c) durable | registers the work before the response returns | a background job the request does not wait for |

Mode (b) registers an operation kind and target, because an
`asyncio.Lock` knows only that it is held: a refusal that can only say
"busy" tells a researcher nothing. A run arriving while a mode-(b)
worker holds the drain is refused at dispatch and told which operation
holds it, rather than queued behind it -- and that refusal deliberately
does not offer the Kill button, because Kill stops a pipeline action
and does nothing to a carrier worker.

**A declaration authorizes nothing.** `routeScope.ffnDeclareCarrierMode`
stamps intent from a closed set (`typed-read`, `mode-a-synchronous`,
`mode-b-lock-held`, `mode-c-durable`, `lifecycle-transaction`,
`separate-authority`); a route may carry several, because a handler
that writes synchronously and then starts durable work is a real shape.
The stamp routes the request to a branch with **no** admission, so the
handler must open one per mutation. Forget one and the primitive raises
`MutationNotAdmittedError`. **That refusal is the proof** -- a
decorator that pre-admitted the handler would delete it, which is the
`bAgentSafe` mistake one level up.

Three rules follow from what the migration found, and each exists
because the obvious alternative was demonstrated wrong.

**A refusal is not an I/O error.** `MutationNotAdmittedError` and
`CommitRefusedError` derive from `ControlPlaneRefusalError(Exception)`,
not `PermissionError`. They used to subclass `PermissionError`, which
reads well and is an `OSError` -- so all 85 `except OSError` /
`except PermissionError` clauses in the package swallowed them,
including a dozen written to answer conservatively when a file cannot
be read. That is how a carrier refusal came to silently DOWNGRADE a
workflow's reproducibility badge.

**A carrier worker must not raise an expected refusal.** A worker that
raises poisons its journal record and quarantines the container until
`vaibify reconcile`. An expected 4xx or 502 -- a duplicate project
name, an unreachable git remote, a bad step index -- is carried back as
a value through `routeContext.fdictCarryARefusalBackInsteadOfRaising`
and re-raised outside, after the record settles. A genuinely
half-finished write still poisons, correctly: nobody knows what state
it left behind. Deciding which is which is done by reading the
failure paths, never by inferring from the shape.

**A typed read is exempt only inside its adapter.**
`DockerConnection._ftRunTypedRead` is the single grant point. It
takes an operation name from a fixed table plus a path or a flat
sequence of paths, and BUILDS the command; it never accepts one. That
distinction is what keeps the carve-out from becoming a general bypass.

**Scope, stated so the record is not read as more than it is.** The
migration was scoped to the routes that mutate. 83 of 130
container-scoped routes are declared; the 46 read-only ones stay on the
legacy ambient admission by decision (2026-08-05), so
`SET_ROUTES_AWAITING_CARRIER_MODE` bottoms out at 46 rather than empty.
Read-only routes cannot cause the hand-over failure; declaring them
would have caught a *future* mistake where somebody adds a write to a
shared helper, which is worth having and was not worth the remaining
cost. `POST /api/zenodo/{id}/download` is the one mutating route left
undeclared, deliberately: it calls a function that does not exist, so
migrating it would quarantine a working container over a broken button.

**Nothing here is verified by the ordinary route tests.** 27 test files
define a `fnWriteFile` mock and none of them consults the admission
gate, so "forget a carrier and the primitive raises loudly" is true of
the real `DockerConnection` and false of every route test -- a migrated
route with its carrier call deleted outright passed its whole test
file. `tests/testCarrierMigratedRoutes.py` is the verification path: a
double that calls the same gates, under the same primitive names, at
the same points the real connection calls them, recording the live
admission MODE at each. It asserts the mode, never merely that nothing
raised, because "no exception" is equally true of a route riding the
ambient mint.

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
  API: `fiRunAllSteps`, `fiRunFromStep`, `fiRunSelectedSteps`,
  `fiVerifyOnly`, `fiRunAllTests`.
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
- `workflowManager.py` — project CRUD, variable resolution, step
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
- `registryRoutes.py` — project registry API.
- `terminalSession.py` — PTY bridge for the terminal WebSocket.
  Constructed only by `routes/terminalRoutes.py`, after the ownership
  gate (see `AGENTS.md`, "The terminal serves containers, and costs
  the quiescence claim").
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

Each project step carries a `dictVerification` dict. The formal state
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
guarantee: the GUI must always reflect the true state of the project.
See the relevant trap in [../AGENTS.md](https://github.com/RoryBarnes/Vaibify/blob/main/AGENTS.md).

## Two PROOF-level truth systems

The backend computes the reproducibility ladder (PROOF L1–L3) in two
deliberately different shapes, and misreading one as the other is the
most likely way to misjudge the dashboard:

1. **The scalar aggregate** — `levelGates.fiProofLevel` /
   `fbAtLeastLevelN`. Strictly additive over the whole project: L1
   requires every step's L1 blockers clear, L2 requires L1, L3
   requires L2. This is "what level is this project at," and it is
   what the PROOF chip in the dashboard header renders. (Historical
   note: an early boolean `bVaibified` predated the ladder and meant
   what `fiProofLevel >= 1` means now; the v4 project migration drops
   the key on load, which is the excision mechanism — do not remove
   the migration.)

2. **The independent cell projections** —
   `fdictComputeStepLevelStates` (per step) and
   `fdictComputeWorkflowScopeLevelStates` (the Project header row).
   Each cell answers "which requirements *at this scope and level*
   are satisfied," with no propagation between levels or scopes. A
   step can honestly read L1 partial + L3 attained; that is a
   feature (the researcher sees exactly which rung needs what), not
   a contradiction.

The corollary that trips readers: **the Project row is not a summary
row.** Its cells cover only the requirements that attach to no single
step — L1: the repository exists; L2: sync-verify freshness plus
the arXiv criteria (only when an arXiv submission is recorded — the
arXiv claim is opt-in); L3: the envelope artifacts (pinned Dockerfile,
dependency lock, environment snapshot, reproduce script, attestation,
binary declarations). A Project-row L1 check above red step rows is
therefore a consistent display: the project-scope L1 requirement is
met while per-step L1 work remains, and the chip — the aggregate —
still says Level 0. The cell tooltips state this scoping.

Honesty floors inside the cell projection: a stale sync cache never
renders attained; a step to which no L3 criterion applies
(no declared paths, scripts, binary invocations, or randomness flag)
renders "not-applicable", never a vacuous attained; and per-step L3
counts every applicable criterion — the dominant-glyph design of the
blocker list does not flatten five failures into a 4-of-5 partial,
because the dominant entry carries `listFailingCriteria`.

"unknown" ranks BELOW "partial" (2026-08-25). It used to short-circuit
ahead of the counts, so one unknowable requirement erased every
requirement that was positively satisfied — a researcher whose GitHub
mirror had verified and whose Zenodo deposit never had was shown "?"
and read it as a lost result. Nothing was lost by moving it: the
never-attained floor above comes from the arithmetic, not from that
short-circuit, because `iSatisfied` counts only `bMet is True` and so
an unknown requirement already forces `iSatisfied < iTotal`. What the
short-circuit uniquely did was suppress known credit. "unknown" now
means what it says — nothing at this level is known to be satisfied
and something is unknowable — and the ⓘ breakdown distinguishes the
three marks per requirement (check / ⚠ / hollow circle = not
verifiable right now), so an orange cell never hides which of its
requirements is merely unchecked.

A step with no recorded activity splits on material evidence: when
none of its declared outputs exist on disk it renders "not-started"
(hollow circle — nothing yet); when at least one declared output is
on disk it renders "unassessed" (grey filled circle — material
present, assessment not begun). The discriminator is the poll's
`dictMaxMtimeByStep`, which has an entry only for steps whose
declared outputs were found in the container, so hours of compute
performed outside the dashboard stay visible as progress. The
"unassessed" state asserts only existence, never quality — it sits
below "none" on the ladder and never stamps a high-water mark.

## The Replay axis (AI provenance)

The PROOF ladder measures the state of the artifact; the Replay axis
measures the provenance of the process — which AI models did the
work, under what standing instructions, and whether the development
dialogue is preserved. States, each requiring the ones below it:
**untracked → declared** (every model used is declared; vendor +
model ID + date range; open-weights models add weights source and
revision hash; undeclared is the criterion's only failing state and
gates L2) **→ recorded** (the opt-in Prompt Record is enabled and
its first capture reviewed) **→ supervised** (the attribution
watchdog is on: every detected change to a **declared** path during a
watched interval — the outputs, scripts, markers, test sources,
inputs and binaries the poll already stats, not the whole repository
— must attribute to a recorded action channel: pipeline dispatch,
editor save, context write, or an open terminal session, the last
treated as an interval rather than an instant so ordinary work
mid-session does not read as unattributed. Attribution is judged
against the change's own mtime, within a 60-second window bounded at
both ends, so a future-dated event cannot vouch for everything that
follows it. Unattributed changes and manifest drift across hub
downtime become permanent, hash-chained flags that
`gui/attributionLog.py` never removes. Granularity is the window and
the channel, not the file path, and terminal *content* is not yet
captured — both limits are stated in the UI). The verdicts live in
`reproducibility/replayGate.py`; the machine-captured stamp
(`.vaibify/ai_provenance.json` — declared models, SHA-256 of both
standing prompt files, live network-isolation probe, an explicit
trust-base statement) is built by `aiProvenanceStamp.py` +
`gui/aiProvenanceCapture.py`, kept current by a poll side-effect,
and folded into the L3 attestation record (schema v2).

**The instruction stack and the personal layer.** (The dashboard
calls layer 4 **Personal AI Configuration**; `personalLayer` /
`dictPersonalLayer` remain its identifiers, wire path and persisted
key, because renaming a stored schema key would strand every existing
project.) The instructions
governing an AI agent stack in four layers: (1) the harness system
prompt (proprietary — declared via the model ID, unarchivable), (2)
the vaibify-generated container context, (3) the project's own
context file (captured by the project-context feature above), and
(4) the researcher's *personal layer* — private host-side agent
configuration (global instruction file, personal skills, memory,
hooks). Layer 4 is accounted for by a declaration in
`dictAiProvenance.dictPersonalLayer`: one of three statuses —
`none`, `declared-private`, `included` — where *answering the
question* is the L2 criterion (`fbWorkflowDeclaresPersonalLayer`,
gating exactly like the model declaration) and disclosure is never
required. `declared-private` may carry optional **hash
commitments** (`{sLabel, sSha256, iByteCount, sDeclaredIso}`):
the backend hashes a host file and persists only those four fields —
the host path is never stored, logged, or echoed (a missing-file
error names the basename at most). A commitment reveals nothing
about content, but prevents retroactive sanitization: a later
release of the files can be checked against the recorded digests.
The hash route is browser-only twice over — excluded from the
agent-action catalog *and* rejected at the route for requests
presenting the per-container agent token — because an
agent-invokable variant would be a hash oracle over host files.
The declaration route is user-only in the catalog, like the other
L2 consent moments.

**Epistemic contract.** The whole layer is *declared +
tamper-evident*, never proven complete — the same trust model as the
other L2 declarations. Tamper evidence: capture records are
hash-chained and pin their session files' content hashes; the poll
rewrites a hand-edited stamp. Completeness is not provable (no
mechanism can show that no prompt happened off the record), so
coverage intervals make the monitored windows explicit and the UI
renders gaps as gaps. The attestation's trust-base statement names
what is assumed rather than recorded: the host kernel, the Docker
daemon, and the hub, with no host-root bypass.

**Prompt Record threat model.** Captured transcripts land inside a
public (or to-be-public) repository, so the landing zone is the
threat: sanitization happens at capture, never at publish.
`gui/transcriptSanitizer.py` layers exact-value redaction of every
vaibify session secret, detect-secrets' pattern catalog (its two
entropy plugins are excluded — via `scan_line` they carry no usable
threshold and flag ordinary words; verified empirically), a
vendor-token-prefix rule, and a guarded Shannon-entropy supplement
(32+ characters, letters and digits, ≥ 4.5 bits/char) that leaves
code identifiers and git hashes intact. Redactions are explicit
`[REDACTED: category]` markers with per-category counts; a human
review gate (catalog-excluded — the agent must never approve its own
transcript) sits before the first capture counts; and the scanner
cannot catch prose the researcher considers private, which is what
the review gate is for.

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

- `scriptApplication.js` — `VaibifyApp`: application state,
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
atomically, preventing state leaks across project switches. Sets use
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
current project state.

## Packaging: why runtime resources live inside the package

`vaibify/templates/` and `vaibify/containerImage/` are data trees that
ship in the wheel. They used to sit at the repository root and be
reached with `Path(__file__).resolve().parents[2]` — which is the
repository root only in a checkout. From an installed wheel it is
`site-packages`, so **no wheel ever contained them**: `vaibify init`
printed "No templates found" and exited 0, and the Docker-context lookup
landed on `site-packages/docker`, the Docker SDK's own source directory,
which exists, so an `is_dir()` check passed.

Two resources were reached from the repository root the same way and
were therefore missing from every distribution: the curated agent docs
staged into `/usr/share/vaibify/docs`, and the shell completions. The
docs case was the worse one, because the bundled `vaibify-doc-map` skill
told the in-container agent all six documents were present — so a
wheel-built image did not merely lack docs, it *misdirected the agent*,
and differed materially from a checkout-built image. Those docs now live
at `vaibify/docs/` as symlinks onto the Sphinx sources, so there is one
file to edit and both builders dereference them into real files.

The build context is staged per *build*, not per project, because the
GUI starts builds in worker threads with no serialization: two dashboard
clicks race, and refreshing a shared directory begins with `rmtree`,
which would delete a context out from under a running `docker build`.

**Checking a shipped file is not the same as checking the artifact built
from it.** The release workflow once validated every distribution with
`import vaibify`, which passes for a wheel containing no templates —
exactly what every wheel contained. Its replacement,
`tools/checkInstalledDistribution.py`, resolves every tree, runs
`vaibify init`, executes the shipped example workflow to a figure, and
*assembles a real build context* to check that no curated doc and no
Dockerfile `COPY` source is missing. The first version of that script
spot-checked three files, which is why it passed a distribution whose
assembled context was missing five of six agent documents.

That job is **release-only** by decision (2026-07-28), matching `vspace`,
`bigplanet` and `multi-planet`: a release runs the full support matrix, a
manual run the corners. So a packaging regression can sit on `main` until
the next version is cut. `upload_pypi` needs `build` and `test`, so it is
caught while cutting the release and nothing broken is published — but
the diagnosis arrives during a release rather than beside the change that
caused it. It cannot be a required status check, because it cannot report
on a pull request and every PR would wait on it forever.


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
2. `scriptFigureViewer.js` was not part of the 2026-01 frontend
   refactor. It handles PDF rendering, dual-viewer comparison, and
   history management as a single cohesive module.
3. Re-export blocks across four orchestrator modules
   (`pipelineRunner`, `pipelineServer`, `testGenerator`,
   `syncDispatcher`) exist for backward compatibility. Callers should
   eventually migrate to importing from canonical modules directly.

Each debt item is load-bearing in a specific way: fixing it naively
breaks a working contract. The narrative here exists so a future
contributor can recognize these as deliberate rather than accidental.
