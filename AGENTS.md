# Vaibify — agent guide

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific pipelines inside Docker containers. Backend: FastAPI
(Python). Frontend: vanilla JavaScript using IIFE modules.

This file is the entry point for anyone (human or agent) working on this
repository. It states the rules; `docs/architecture.md` explains the
reasoning; `tests/testArchitecturalInvariants.py` enforces the
structural invariants; `tools/listModules.py` reports the current
structural state on demand. See `docs/vibeCoding.md` for the
methodology behind this structure.

## How to read this repo when starting a task

1. Read this file.
2. Read the files directly touched by the task.
3. If working inside a subtree, read the nearest `AGENTS.md`:
   - Backend Python work under `vaibify/gui/` → `vaibify/gui/AGENTS.md`
   - Frontend JS work under `vaibify/gui/static/` → `vaibify/gui/static/AGENTS.md`
4. If you need the current module layout or public-symbol list, run
   `python tools/listModules.py <subtree>`. Do not guess; do not rely
   on memory of a previous session.
5. For architectural "why" questions, read `docs/architecture.md`.
6. If the task touches an architectural contract (route registration,
   leaf modules, path-module choice, science-agnostic source),
   `tests/testArchitecturalInvariants.py` is the executable
   specification. Run it to see the current state.

## Style guide

The source code shall adhere to the following conventions: 

1. Functions should be short (less than 20 lines), orthogonal, and  single-purposed. If identical lines exist in the codebase, make a new function that contains those lines, i.e., don't repeat yourself.

2. Variable names should be camel-case and should have prefixes that 
correspond to the variable type or cast, i.e. Hungarian notation. Use the following guide:

- Boolean = "b"
- Integer = "i"
- Float = "f"
- Double = "d"
- Arrays should include an "a", e.g., an array of doubles starts with "da"
- Dictionary = "dict"
- List = "list"
- JSON = "json"
- Tuple = "t"

If a cast is not listed above, ask me.

3. Function names should begin with an "f" and should be followed by additional lowercase letter(s) that describe the return type, e.g. "fb" for a function that returns a Boolean, or "flist" for a function that returns a list. If a function does not return anything, use "fn" as the prefix.

4. Functions should never be more than 20 lines long. More than this amount, and it will be challenging for a developer to keep track of how the function is accomplishing its task. When a function is over 20 lines, identify the block(s) of code that are most likey to be of broader use and create (a) new function(s).

5. File names should be camelcase, but should not use Hungarian prefixes.

6. Don't abbreviate any word less than 8 characters long. Function names must have an action verb in them (except for main).

7. Use inline documentation sparingly. Clear, long variable and function names allow the developer to understand how the code is executing just by reading the source code.

8. Do not allow a developer's personal style preferences supersede these rules. 

## Required after edits

- After any Python change:
  `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
- After changes that touch structural invariants (adding a route,
  adjusting import graphs, touching `workflowManager.py` or
  `director.py`):
  `python -m pytest tests/testArchitecturalInvariants.py -v`
- After JS changes: exercise the feature in the running GUI. Type
  checking does not validate UI correctness.
- Docker-dependent tests (`tests/testContainerBuildIntegration.py`)
  are excluded from routine runs and are the only tests that require
  a live container. They are parametrized via the
  `VAIBIFY_INTEGRATION_CONFIG` environment variable and skip when it
  is unset.

## Traps

These are the mistakes most likely to cause real harm in this
repository. Read them before you start editing.

**Step labels are per-type sequential, not positional.** `A09` is the
9th *automated* step, `I01` is the 1st *interactive* step. `A09`
does not mean `listSteps[9]` in general — it lands at
`listSteps[9 + (number of interactive steps preceding the 9th
automated one)]`. Use `fsLabelFromStepIndex` and
`fiStepIndexFromLabel` from
[vaibify/gui/pipelineUtils.py](vaibify/gui/pipelineUtils.py) — never
inline the translation. `sLabel` is persisted in `workflow.json` and
recomputed on every load/save by `fnAttachStepLabels`, so insertions,
deletions, and reorderings produce correct labels on the next save
automatically. Error messages, logs, toasts, and agent-facing
commands use labels (users speak labels); internal code paths keep
0-based indices.

**Never hard-code science-specific examples.** Vaibify is for the
general problem of containerized scientific workflows. Specific
datasets, specific experimental setups, specific user projects, and
specific target systems must not appear in vaibify source, templates,
tests-of-record, or docs. When a specific example helps during
development, keep it in a scratch branch or a user-owned workflow
repo, never in vaibify itself.
`tests/testArchitecturalInvariants.py::testNoScienceSpecificIdentifiersInSource`
enforces this with a seed list; extend the list when new science-specific terms
need to be forbidden.

**Never introduce security vulnerabilities.** Review every plan for
exploits before implementing. Threat model: AI agents running inside
containers, acting on user-owned host data, with credentials for
Overleaf, GitHub, and Zenodo. Failure modes to audit against:

- Command injection through user-provided workflow fields
- Path traversal via `sPath` parameters. Vaibify's backend and CLI
  run on the host, not inside the container, and they handle host
  paths in file pulls, directory browsing, sync, and workspace
  mounts. Any path that originated from a user-facing source (HTTP
  request body, workflow.json, config file) must be validated
  against its intended root before being opened, read, written, or
  listed. The existing helper `fnValidatePathWithinRoot(sAbsPath,
  WORKSPACE_ROOT)` in `pipelineServer.py` does this — do not remove
  or weaken it.
- Credential leakage through logs, error messages, or generated test
  code
- Mounting host paths outside the workspace volume
- Bypass of the unprivileged-user + `gosu` protection in the container
- Network egress where the container is meant to be isolated
- Embedding secrets in source, commit messages, or CI output

If a change expands the attack surface, call it out explicitly in the plan
before implementing.

**Never suppress or misrepresent the container or workflow state in the
dashboard.** The GUI is the user's ground truth. Step status, file
staleness, verification state, test results, and container health must
always reflect reality. Do not cache state beyond its natural lifetime;
do not short-circuit polling to "look responsive"; do not hide errors;
do not optimistically mark steps as passed. If the truth is slow or
ugly, show it. This applies to `fileStatusManager.py`,
`pipelineRoutes.py`, `pipelineState.py`, and every frontend render
path.

**`director.py` and `workflowManager.py` are different things.**
`director.py` is a parallel workflow runner that operates on the host
filesystem using `os.path`. `workflowManager.py` operates on container
paths using `posixpath`. Similarly named functions
(`fbValidateWorkflow`, `fdictBuildGlobalVariables`) exist in both and
are intentionally divergent. Do not "fix" the divergence — it's
load-bearing. They may share *pure* helpers (e.g.
`flistValidateOutputFilePaths`) without violating this rule; the
calling conventions remain divergent.

**Do not revert to `/workspace`-as-repo.** Every vaibify workflow
must live inside a git repository — its "project repo" —
auto-detected from the workflow.json's parent via
`containerGit.fsDetectProjectRepoInContainer`. `/workspace` is a
Docker-managed named volume, not a repo; it is only the discovery
root. Routes in `vaibify/gui/routes/gitRoutes.py` must thread
`dictWorkflow["sProjectRepoPath"]` into every `containerGit.*` call
(`testGitRoutesAlwaysPassProjectRepoToContainerGit` enforces this),
and no module may hardcode `/workspace/.vaibify/test_markers`
(`testNoWorkspaceRootedMarkerHardcodeInSource` enforces this — test
markers live at `<sProjectRepoPath>/.vaibify/test_markers/`). Step
`sDirectory` values and all `saOutputFiles` / `saDataFiles` /
`saPlotFiles` paths are repo-relative; absolute or `..`-escaping
values are rejected at load time. A silent fallback to the
`/workspace` default reintroduces the all-grey-badges bug and
desynchronizes marker writes from marker reads. See
[docs/architecture.md](docs/architecture.md) — the "Workflow = git
repo" section — for the full rationale. A container may host
multiple workflows in different project-repo subdirectories; the
active workflow determines the badge scope.

**`introspectionScript.py` is an f-string executed inside containers.**
Editing it as ordinary Python loses escape sequences and string
delimiters silently. The format-handling duplication with
`dataLoaders.py` is also deliberate — container scripts cannot import
from the host environment.

**`pipelineUtils.py` is a deliberate leaf module.** It has zero
intra-package imports, used to break circular dependency cycles. Do
not add `from vaibify.gui` or `import vaibify.gui` lines to it.
`testLeafModuleHasNoIntraPackageImports` enforces this.

**JavaScript IIFE state objects share mutable collections by
reference.** `_dictWorkflowState` contains Sets that are held by the
render context. Reassigning a Set (`setExpandedSteps = new Set()`)
breaks rendering; use `.clear()` and mutate in place. The
`_fnResetWorkflowState()` factory pattern is how state is cleared
across workflow switches.

**Do not delete or silence a test to make a failure go away.** A
failing test is signalling one of three things: a bug in the code
under test, a bug in the test's assertion, or a legitimate behavior
change that the test predates. The fix is to investigate and address
the right one, not to remove the test. Deleting or disabling a test to
unblock a run is effectively unrecoverable: future regressions have no
guardrail.

**Host→container file writes default to the unprivileged container
user.** Every backend write of a file inside the workspace volume
funnels through `fnWriteFile` / `fnWriteFileViaTar` in
[vaibify/docker/dockerConnection.py](vaibify/docker/dockerConnection.py),
which calls `container.put_archive(tarball)`. The tarball entry's
uid/gid IS the file's owner inside the container, and `tarfile.TarInfo`
natively defaults uid/gid to 0. If that default ever leaks through, the
file lands root-owned and the in-container agent cannot edit it (sudo
is absent by design — commit 426f6b7). The symptom is a researcher's
`git push` failing on `.git/objects/<prefix>` or the agent unable to
modify `workflow.json` after a backend save. The dispatcher's
`_finfoBuildTarEntry` defaults the stamps to
`_I_CONTAINER_DEFAULT_UID`/`_GID` (1000:1000, locked to the Dockerfile
by `testContainerUserUidIsOneThousand`); any new host→container write
path must preserve that default.
`tests/testArchitecturalInvariants.py::testFnWriteFileDefaultsToContainerUserOwnership`
enforces it.

**Container access has exactly one authority: the lease plus
`dictContainerOwners`.** A container is owned by a per-claim,
server-minted lease (`containerOwnership.fsMintLease`), recorded in the
in-process owner-of-record map `app.state.dictContainerOwners`, keyed by
container **name** (the host flock and the caffeinate keep-alive are both
name-keyed, so the owner map must be too; the WebSocket routes resolve
the Docker container id to a name before the lease lookup). Claim
(`registryRoutes`), the connect handler, the pipeline WebSocket, and the
terminal WebSocket must all authorize through the single shared guard
`webSocketAuthorization.fbAuthorizeContainerSession` /
`fiContainerSessionRejectionCode` — never an inlined container-id
membership check. Never reintroduce `setAllowedContainers` (the old
append-only, process-global allow set that leaked authorization for the
whole process lifetime) or treat the shared session token as the browser
*principal* — the token is the trust/CSRF boundary and the in-container
agent's machine credential, not the thing that says *which* browser
session owns a container. The lease is the exclusivity principal; the
holder payload carries `sStartedIso` for recycle-proof staleness; exactly
one live WebSocket per container is enforced by the per-container
`iLiveConnectionCount` (a duplicate tab that copied the lease is closed
4409); and the idle busy-veto reads `dictContainerOwners.keys()` so the
watchdog can never self-SIGTERM a hub mid-run. The full normative model
is the "Single browser session per container" section of
[docs/architecture.md](docs/architecture.md). Enforced by
`testClaimRejectsForeignLease`, `testReleaseRejectsNonOwner`,
`testWebSocketGatesUseSharedAuthorizationGuard`, and
`testSetAllowedContainersRemoved`.

## Cross-step references via tokens

**Every cross-step file reference in a vaibify workflow script must be
passed as a CLI argument, named in the step's command via a
`{StepNN.varname}` token.** Hardcoded paths in Python (or any other
language) that cross step boundaries are forbidden. The contract is
that the workflow JSON, parsed mechanically, declares the complete
dependency graph.

The dependency parser at
[workflowManager.py::fdictBuildDirectDependencies:1471-1487](vaibify/gui/workflowManager.py#L1471-L1487)
scans only `{StepNN.varname}` tokens in command strings. It cannot
introspect arbitrary script source. A cross-step reference hidden
inside a script literal (e.g. `path = "../OtherStep/output.json"`) is
invisible to the parser, so the dependency edge does not exist in the
graph, `bUpstreamModified` cannot fire correctly, and the workflow
cannot honestly reach AICS Level 1.

A step's script *may* read its own step-directory files via hardcoded
relative paths. The boundary is the step. Anything from another step
must be tokenized.

The `saDependencies` field is the escape hatch when the data flow
doesn't naturally express the dependency (e.g., a plot script
implicitly relies on a sibling step's output): list one or more
`{StepNN.*}` tokens there and the parser will pick the edge up.

Full developer documentation, including the naming convention,
worked examples, and how to handle colliding basenames, is in
[vaibify/docs/scriptAuthoring.md](vaibify/docs/scriptAuthoring.md). The architectural
invariant `testTemplateCommandsUseStepTokens` in
`tests/testArchitecturalInvariants.py` enforces this rule on every
vaibify-shipped template at CI time.

## Adding a UI action

Every new state-mutating HTTP or WebSocket route that a researcher can
invoke from the UI must also be registered with the agent-action
catalog — either by an entry in `LIST_AGENT_ACTIONS` plus a
`@fnAgentAction("action-name")` decorator on the handler, or by an
explicit entry in `SET_INTENTIONALLY_EXCLUDED_PATHS` (with a short
rationale on the same line or in the preceding comment block) if the
route is genuinely not agent-invokable.

Unregistered state-mutating routes are invisible to the in-container
agent: when a researcher says "Claude, run unit tests on step A09",
the agent has no way to translate that request into a backend call,
so the dashboard silently drifts out of sync as the agent improvises.

`tests/testArchitecturalInvariants.py::testAgentActionRegistered`
fails CI if the registration is missing.

The catalog authority is
[vaibify/gui/actionCatalog.py](vaibify/gui/actionCatalog.py); the
agent-facing CLI that consumes it is
[docker/vaibifyDo.py](docker/vaibifyDo.py); documentation for
in-container usage lives in the embedded CLAUDE.md generated by
[docker/entrypoint.sh](docker/entrypoint.sh).

## Ask first

The following actions have outsized blast radius and require explicit
user confirmation before execution:

- Changing the verification state machine semantics (`fileStatusManager.py`).
- Modifying Docker security capabilities, user namespace, or network
  isolation.
- Touching the reproducibility pipeline (`vaibify/reproducibility/`,
  Zenodo, Overleaf, LaTeX integration).
- Force-pushing, rewriting shared git history, or changing CI
  workflows beyond the documentation path-check added alongside this
  guide.

### Enforced by harness hooks

Some of the above are enforced by Claude Code PreToolUse hooks
configured in `.claude/settings.json`:

- **`askSensitiveEdit.py`** pauses `Edit`, `Write`, and `NotebookEdit`
  on: `docker/*`, `vaibify/docker/containerManager.py`,
  `vaibify/config/secretManager.py`, any `AGENTS.md`, and any
  `.claude/skills/*/SKILL.md`. The hook returns an "ask" decision so
  the user sees a confirmation prompt.
- **`blockDestructiveGit.py`** denies `Bash` commands matching
  `git push --force` (except `--force-with-lease`) and
  `git rebase -i`. These are hard-blocked; run manually if genuinely
  needed.

If a hook fires during your work, read the reason and either confirm
with the user (for "ask") or escalate the need (for "deny"). Do not
edit the hook scripts or `.claude/settings.json` to bypass a block —
that itself is an edit to a sensitive file and an ask-first action.
Temporary bypass is available via `--disable-hooks` at the CLI level
if a human is driving.

## Known technical debt

These are known, deliberate, and load-bearing — do not "fix" them
without discussion:

- `introspectionScript.py` duplicates format-handling logic from
  `dataLoaders.py`. Container scripts cannot import from the host.
- `director.py` has its own `fbValidateWorkflow` and
  `fdictBuildGlobalVariables` that diverge from `workflowManager.py`.
  Host path vs. container path.
- `scriptFigureViewer.js` was not part of the 2026-01 frontend
  refactor. Kept as a single cohesive module.
- Re-export blocks exist across `pipelineRunner`, `pipelineServer`,
  `testGenerator`, and `syncDispatcher` for backward compatibility.
  Callers should migrate toward canonical imports over time; do not
  delete the re-exports until external callers are updated.
- The in-container agent lane (`webSocketAuthorization.fbCheckAgentToken`)
  authorizes with the **hub-wide** shared session token, not a
  per-container credential. On a hub that owns more than one container, a
  compromised/prompt-injected agent in container A could therefore reach
  container B's session. This is pre-existing (the retired
  `setAllowedContainers` lane had the same hub-wide scope) and is
  deferred, not endorsed: the robust fix provisions a per-container
  `sAgentToken` (on the owner record, via `agentSessionBridge`, presented
  by `docker/vaibifyDo.py`, accepted by `SessionTokenMiddleware`). Until
  then, treat a single hub as one trust domain. Do not widen the agent
  lane further.

## Discovery commands

Rather than memorizing structural facts, run these when you need them:

- `ls vaibify/gui/routes/*Routes.py` — current route modules
- `grep -rh "^__all__" vaibify/gui/ | sort -u` — public symbol exports
- `python tools/listModules.py vaibify/gui` — Python module map with
  docstring purposes
- `python tools/listModules.py vaibify/gui/static --format json` — JS
  IIFE modules, machine-readable
- `find . -name AGENTS.md -not -path './.git/*'` — all agent docs
- `python -m pytest tests/testArchitecturalInvariants.py -v` —
  current enforced invariants (tests are documentation)

## Lessons

This section records specific mistakes made in past sessions that are
worth remembering. It is empty at initial commit. Add entries as they
come up — one line each, pointing at the offending pattern and the
correct approach.

- The pre-refactor claim route short-circuited to `bClaimed: True`
  whenever a container was already locked, silently admitting a second
  same-hub browser tab. A claim must ARBITRATE (unowned grant /
  same-lease idempotent / foreign 409), never unconditionally succeed;
  `testClaimRejectsForeignLease` guards this.
- A green test suite is not proof of a working guarantee: the
  one-session refactor's owner map was keyed by container *name* on the
  write path but looked up by container *id* at the WebSocket gate, so
  every real hub session would have failed closed — yet the suite stayed
  green because its fixtures used name == id and never drove the live
  WebSocket/viewer paths. When a behavior crosses the
  HTTP/WebSocket/container boundary, assert it with name != id and an
  actual connection, not a unit stub.

## Pointers

- [docs/architecture.md](docs/architecture.md) — the "why" behind the
  module layout
- [docs/vibeCoding.md](docs/vibeCoding.md) — the methodology behind
  this documentation structure
- [docs/developers.md](docs/developers.md) — human contributor guide
- [vaibify/gui/AGENTS.md](vaibify/gui/AGENTS.md) — backend subtree
  rules
- [vaibify/gui/static/AGENTS.md](vaibify/gui/static/AGENTS.md) —
  frontend subtree rules
- [.claude/skills/](.claude/skills/) — conditional recipes for
  recurring extension tasks. The two currently defined:
  - [.claude/skills/add-route-module/](.claude/skills/add-route-module/)
    — recipe for adding a new FastAPI route module.
  - [.claude/skills/add-data-loader/](.claude/skills/add-data-loader/)
    — recipe for adding a new data file-format loader.
