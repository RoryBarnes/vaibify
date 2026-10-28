# vaibify/gui — backend agent guide

This subtree contains the FastAPI backend. Rules in this file apply to
any change under `vaibify/gui/` (and its subpackages `routes/`).

For the repo-wide rules, read `../../AGENTS.md` first. For the
reasoning behind the module layout, see `../../docs/architecture.md`.

## Contracts

### Route modules

Every file matching `vaibify/gui/routes/*Routes.py` must export a
module-level function `fnRegisterAll(app, dictCtx)` that registers its
endpoints on the FastAPI application (a module that confines paths to
the workspace takes the root as a third argument, `sWorkspaceRoot`, as
`fileRoutes` does). Every such module must also be imported in
`vaibify/gui/routes/__init__.py` so that import errors surface at
startup, and mounted by an explicit `fnRegisterAll` call in
`pipelineServer._fnRegisterAllRoutes` (viewer and hub) or, for a
hub-only control-plane route, `appFactory._fnRegisterHubLifecycle`.
Importing a module registers nothing.

Enforced by
`tests/testArchitecturalInvariants.py::testEveryRouteModuleExportsRegisterAll`
and `testAllRouteModulesRegisteredInInit`. No test checks the mounting
call; an unmounted module's own endpoint tests are what fail.

To add a new route, use the `.claude/skills/add-route-module/` recipe.

### Public API declaration

Every direct-child `.py` file under `vaibify/gui/` declares `__all__`
with the intended public symbols. New modules must follow this
convention. Exception: `__init__.py`.

Enforced by `testAllPackageModulesDefineDunderAll`.

### Leaf module discipline

`pipelineUtils.py` is a deliberate leaf module with zero intra-package
imports. It exists to break circular dependency cycles — several
modules depend on it, and nothing depends on the full `vaibify.gui`
package from within. Do not add `from vaibify.gui ...` or
`import vaibify.gui` lines to it.

Enforced by `testLeafModuleHasNoIntraPackageImports`.

### Path module separation

`workflowManager.py` uses `posixpath` because it operates on container
paths, which are POSIX on every host operating system. A module that
operates on the host filesystem must use `os.path`, whose separator is
the host's. Unifying the two would either mangle Windows host paths or
mangle container paths on any host, and the failure stays silent until
a cross-platform user hits it. A module in this subtree that handles
host paths takes `os.path` and shares only *pure* helpers with
`workflowManager`.

**Host mode is the deliberate exception, and it is not really one.**
A host project's pipeline runs on the researcher's machine, so
`workflowManager` composes host paths for it — with `posixpath`, still.
Host mode is macOS and Linux only, where the two modules are identical,
so one implementation serves both modes without a fork. Windows is the
boundary and is refused: there the step commands (`bash -c` text) and
the POSIX path guards weaken silently rather than failing. A host-path
fork of this module was tried (the withdrawn `director` module) and
abandoned; swap the connection object instead.

Enforced by `testWorkflowManagerUsesPosixPath`. (A second invariant,
`testDirectorUsesOsPath`, guarded the withdrawn host-side director
module and was removed with it — a contract whose named test cannot be
grepped reads as a contract that does not exist.)

### Container-access gate

All container-access decisions consult ONE authority: `containerOwnership`
over `app.state.dictContainerOwners` (keyed by container **name**), whose
`fbBrowserSessionOwnsLease` requires a valid per-browser credential AND
a lease bound to that session. The access principal is the per-claim
lease, not the shared session token, and no route may inline a
container-id membership check or read a process-global allow set. Each
lane reaches the authority through one entry point: the claim route
(`registryRoutes`) commits through `sessionLifecycle.ftClaimWithCardinality`
and so `containerOwnership.ftClaim`; the connect handler
(`workflowRoutes`) reads the `X-Vaibify-Lease` header into
`containerOwnership.fbBrowserSessionOwnsLease`; both WebSocket routes
(`routes/pipelineRoutes.py`, `routes/terminalRoutes.py`) resolve the
Docker container id to a name and call
`webSocketAuthorization.fiContainerSessionRejectionCode` (its boolean
wrapper `fbAuthorizeContainerSession` has no production caller); and
every container-scoped HTTP route is checked by
`routeScope.fiAuthorizeContainerHttp`, which `routeScope.ContainerAwareRoute`
runs before the handler. New backend modules from this layer:
`containerOwnership.py` (owner-of-record map + lease arbitration + the
per-container live-connection counter) and `webSocketAuthorization.py`
(the WebSocket guard). The normative model is the "Single browser
session per container" section of `../../docs/architecture.md`.
Enforced by `testWebSocketGatesUseSharedAuthorizationGuard`,
`testSetAllowedContainersRemoved`, and
`testContainerAwareRouteRunsAuthorityBeforeEndpoint`.

### Re-export pattern

Four orchestrator modules re-export symbols from extracted child
modules for backward compatibility:

- `pipelineRunner` re-exports from `pipelineValidator`,
  `pipelineLogger`, `pipelineTestRunner`, `interactiveSteps`,
  `pipelineUtils`, and `pipelineState`.
- `pipelineServer` re-exports from `fileStatusManager` and
  `testStatusManager`, plus lazy access via `__getattr__` to route
  modules. Its app-construction internals were extracted into
  `appFactory.py` (the single `fappCreateApplication`),
  `serverMiddleware.py` (the middleware classes), `serverLifespan.py`
  (background-task registration), and `dockerStatus.py`; `pipelineServer`
  re-exports them for existing callers.
- `testGenerator` re-exports from `testParser`, `dataPreview`,
  `conftestManager`, `llmInvoker`, and `templateManager`.
- `syncDispatcher` re-exports from `fileIntegrity`.

When adding a new symbol to an extracted child module, decide
explicitly whether it should also appear in the orchestrator's
`__all__`. The re-export shim exists for callers that still import
from the pre-refactor names; new callers should import from the
canonical module.

## Traps

- `introspectionScript.py` is an f-string executed inside Docker
  containers. Editing it as ordinary Python loses escape sequences
  silently. The duplication with `dataLoaders.py` is deliberate —
  container scripts cannot import from the host environment.
- A host-path module and `workflowManager.py` will look similar and
  will trick you. See "Path module separation" above.
- `pipelineRunner` has a deferred import from `pipelineTestRunner` to
  avoid a load-time cycle. If you add new imports in either module,
  run the full test suite to confirm you haven't closed the cycle.
- The dashboard must reflect true state. When editing
  `fileStatusManager.py`, `pipelineRoutes.py`, or `pipelineState.py`,
  do not cache, suppress, or short-circuit in ways that hide the real
  condition of the workflow. See "The rules with no trigger" in
  `../../AGENTS.md`, and the `dashboard-state-honesty` skill.

## Verification state machine

Each workflow step carries a `dictVerification` dict. The formal
state machine is documented in `fileStatusManager.py`'s module
docstring; read it in full before modifying any transition.
Summary of the fields and invariants:

- `sUnitTest`, `sIntegrity`, `sQualitative`, `sQuantitative` — each is
  `untested | passed | failed | unnecessary`; `unnecessary` marks a
  category with no commands, counts as green, and survives
  invalidation.
- `sUser` — `untested | passed | failed | stale`.
- `sPlotStandards` — `passed | stale`.
- `bUpstreamModified` flags a step whose upstream outputs have
  changed.
- `listModifiedFiles` is populated by polling.

Invariants:

- A step runs data, then plots, then tests.
- Only the server decides whether `sUser` survives a run, whoever
  dispatched it; the browser never resets it. A sign-off records
  every declared plot's SHA-256 (`dictUserVerifiedHashes`); different
  bytes, or a missing plot, make it `stale` with the hashes kept.
- Change is judged by content: a byte-identical data or plot file with
  a newer mtime changes neither the tests nor the sign-off.
- Data- or plot-file change (by content) makes the tests not current.
- Upstream change sets `bUpstreamModified = True` and resets
  `sUnitTest` and every test category to `untested` (an `unnecessary`
  category stays). Only the upstream files a step names by `{step:…}`
  token count, and only when their bytes differ from the ones the step
  recorded consuming (`dictConsumedUpstreamDigests`).

Any change to these transitions is an ask-first operation — consult
the user before proceeding.

## State schema v3: level high-water marks

`state.json` (schema version 3, `stateManager.py`) is repo-scoped and
namespaces each project's state under
`dictWorkflowState[<repo-relative project file path>]`
(`stateManager.fsWorkflowKeyFromPath`), so saving one project never
rewrites another's. Each section persists two add-only ratchet fields
beside the verification state:

- Per step: `dictLevelHighWater` (`{"1": isoFirstAttained, "2": ...,
  "3": ...}`) under the section's `dictStepState[sDirectory]`.
- Workflow scope: `dictWorkflowLevelHighWater` (same shape) at the
  section's top level.

Both are stamped by `stateManager.fbRatchetLevelHighWater`, called
from the poll path (`pipelineRoutes.py`) after the per-step level
states are projected from the blocker lists
(`levelGates.fdictComputeStepLevelStates` /
`fdictComputeWorkflowScopeLevelStates`). Levels are INDEPENDENT
seven-state cells (`sState`:
not-started/unassessed/none/partial/attained/unknown/not-applicable
with `iSatisfied`/`iTotal`/`bRegression`); only "attained" stamps.
"not-started" vs "unassessed" splits on whether the step's declared
outputs exist on disk (the poll's `dictMaxMtimeByStep`): hollow
circle = nothing yet, gray filled circle = material present but
never assessed.
Per-step L3 counts only the criteria whose domain is non-empty on
the step; "not-applicable" (`iTotal` 0) marks a step with nothing to
reproduce and never stamps, never regresses, and never renders as
attained. The workflow header row is NOT an aggregate of the step
rows — its cells cover only workflow-attached requirements
(`levelGates.fdictComputeWorkflowScopeLevelStates`). L1: project repo
present. L2: the GitHub and Zenodo published-copy checks (verify
freshness and divergence), the AI-model and Personal AI Configuration
declarations and the environment-archive question
(`_T_WORKFLOW_LEVEL2_BASE_CRITERIA`), plus the arXiv pair while an
arXiv submission is recorded, the Overleaf pair while an Overleaf
project is bound, and a basename collision when one is reported. L3:
the workflow-scope envelope checks (`_T_WORKFLOW_LEVEL3_CRITERIA`). The
all-steps aggregate is the scalar `iProofLevel`
(`levelGates.fiProofLevel`) rendered by the PROOF chip, so a workflow
L1 check above red step rows is a consistent display.
The poll also emits `dictStepLevelWarnings` (regression/timestamp
warnings gated server-side to each step's lowest non-attained level;
the frontend's ⚠ column composes this entry with the client-known
staleness signals into one multi-line tooltip — the LEVEL gating
stays server-side, the composition is display-only)
and `dictWorkflowEnvelopeDetail` (declared-binary version/hash
match, envelope artifacts, determinism, remote sync summaries —
assembled with zero extra container execs). Invariants: stamps are
ADD-ONLY — regression never erases a stamp (the regression column
depends on the stamp surviving); "unknown" (stale sync cache) never
stamps and never renders attained; saves are issued only when the
ratchet reports a change. A pre-v3 document is migrated on load
(`stateManager.fdictMigrateStateDocument`): its state is attributed
when the repo holds exactly one project, and otherwise kept under
`listQuarantinedState`, owned by nobody, so those steps read unverified
until re-verified. Absent high-water keys mean "never attained on this
machine"; a fresh clone
honestly shows not-started/none cells until re-attained. Do not move
high-water into committed test markers. Archive scope: test files
and standards are manifest-pinned, verified, and offered for archive
by default (`bArchiveTests`, absent means true).

## Discovery commands

- `ls vaibify/gui/*.py` — top-level backend modules
- `ls vaibify/gui/routes/*Routes.py` — route modules
- `python tools/listModules.py vaibify/gui` — Python module map
- `grep -l "fnRegisterAll" vaibify/gui/routes/*.py` — which route
  modules define the contract
- `python -m pytest tests/testArchitecturalInvariants.py -v` —
  current enforced invariants

## Required after backend edits

- If you added a state-mutating route, register it in
  `actionCatalog.LIST_AGENT_ACTIONS` with a `@ffnAgentAction` decorator,
  OR add `(method, path)` to `SET_INTENTIONALLY_EXCLUDED_PATHS` with a
  comment explaining why.
  `tests/testArchitecturalInvariants.py::testAgentActionRegistered`
  will fail otherwise. A mutating route outside the agent catalog,
  excluded or not, also needs exactly one CLI home in
  `researcherActions.py` (`testEveryGuiInvokedMutationHasCliEquivalent`);
  the `add-route-module` skill has the full list.

```bash
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
python -m pytest tests/testArchitecturalInvariants.py -v
```

The second command is the contract check; run it after any change that
touches route registration, imports, `__all__`, or the path-module
choice described above.
