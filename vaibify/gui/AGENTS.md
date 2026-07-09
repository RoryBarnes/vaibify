# vaibify/gui — backend agent guide

This subtree contains the FastAPI backend. Rules in this file apply to
any change under `vaibify/gui/` (and its subpackages `routes/`).

For the repo-wide rules, read `../../AGENTS.md` first. For the
reasoning behind the module layout, see `../../docs/architecture.md`.

## Contracts

### Route modules

Every file matching `vaibify/gui/routes/*Routes.py` must export a
module-level function `fnRegisterAll(app, dictCtx)` that registers its
endpoints on the FastAPI application. Every such module must also be
imported in `vaibify/gui/routes/__init__.py` so that import errors
surface at startup.

Enforced by
`tests/testArchitecturalInvariants.py::testEveryRouteModuleExportsRegisterAll`
and `testAllRouteModulesRegisteredInInit`.

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
paths. `director.py` uses `os.path` because it operates on the host
filesystem. The two modules also expose intentionally divergent
implementations of `fbValidateWorkflow` and
`fdictBuildGlobalVariables`. This divergence is load-bearing — do not
unify them.

Enforced by `testWorkflowManagerUsesPosixPath` and `testDirectorUsesOsPath`.

### Container-access gate

All container-access decisions route through ONE guard. The claim route
(`registryRoutes`), the connect handler (`workflowRoutes`), and both
WebSocket routes (`routes/pipelineRoutes.py`,
`routes/terminalRoutes.py`) must authorize via
`webSocketAuthorization.fbAuthorizeContainerSession` /
`fiContainerSessionRejectionCode`; no route may inline a container-id
membership check or read a process-global allow set. The single
owner-of-record authority is `containerOwnership` over
`app.state.dictContainerOwners` (keyed by container **name**); the access
principal is the per-claim lease, not the shared session token. The
WebSocket routes resolve the Docker container id to a name before calling
the guard. New backend modules from this layer: `containerOwnership.py`
(owner-of-record map + lease arbitration + the per-container
live-connection counter) and `webSocketAuthorization.py` (the shared
guard). The normative model is the "Single browser session per
container" section of `../../docs/architecture.md`. Enforced by
`testWebSocketGatesUseSharedAuthorizationGuard` and
`testSetAllowedContainersRemoved`.

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
- `director.py` vs. `workflowManager.py` look similar and will trick
  you. See "Path module separation" above.
- `pipelineRunner` has a deferred import from `pipelineTestRunner` to
  avoid a load-time cycle. If you add new imports in either module,
  run the full test suite to confirm you haven't closed the cycle.
- The dashboard must reflect true state. When editing
  `fileStatusManager.py`, `pipelineRoutes.py`, or `pipelineState.py`,
  do not cache, suppress, or short-circuit in ways that hide the real
  condition of the workflow. See the traps section in `../../AGENTS.md`.

## Verification state machine

Each workflow step carries a `dictVerification` dict. The formal
state machine is documented in `fileStatusManager.py`'s module
docstring; read it in full before modifying any transition.
Summary of the fields and invariants:

- `sUnitTest`, `sUser`, `sIntegrity`, `sQualitative`, `sQuantitative`
  — each is `untested | passed | failed`.
- `bUpstreamModified` flags a step whose upstream outputs have
  changed.
- `listModifiedFiles` is populated by polling.

Invariants:

- Step execution resets `sUser` to `untested` (user must re-verify).
- Data-file change resets `sUnitTest` to `untested`.
- Plot file newer than `sLastUserUpdate` resets `sUser`.
- Upstream change sets `bUpstreamModified = True` and resets
  `sUnitTest`.

Any change to these transitions is an ask-first operation — consult
the user before proceeding.

## State schema v2: level high-water marks

`state.json` (schema version 2, `stateManager.py`) persists two
add-only ratchet fields beside the verification state:

- Per step: `dictLevelHighWater` (`{"1": isoFirstAttained, "2": ...,
  "3": ...}`) under `dictStepState[sDirectory]`.
- Workflow scope: `dictWorkflowLevelHighWater` (same shape) as a
  top-level key.

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
circle = nothing yet, grey filled circle = material present but
never assessed.
Per-step L3 counts only the criteria whose domain is non-empty on
the step; "not-applicable" (`iTotal` 0) marks a step with nothing to
reproduce and never stamps, never regresses, and never renders as
attained. The workflow header row is NOT an aggregate of the step
rows — its cells cover only workflow-attached requirements (L1:
project repo present; L2: sync freshness + arXiv; L3: envelope
artifacts); the all-steps aggregate is the scalar `fiAICSLevel`
rendered by the AICS chip, so a workflow L1 check above red step
rows is a consistent display.
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
ratchet reports a change. v1 state files load without migration —
absent keys mean "never attained on this machine"; a fresh clone
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
  `actionCatalog.LIST_AGENT_ACTIONS` with a `@fnAgentAction` decorator,
  OR add `(method, path)` to `SET_INTENTIONALLY_EXCLUDED_PATHS` with a
  comment explaining why.
  `tests/testArchitecturalInvariants.py::testAgentActionRegistered`
  will fail otherwise.

```bash
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
python -m pytest tests/testArchitecturalInvariants.py -v
```

The second command is the contract check; run it after any change that
touches route registration, imports, `__all__`, or the
`workflowManager` / `director` path-module choice.
