---
name: add-route-module
description: Recipe for adding a new FastAPI route module to the vaibify backend under vaibify/gui/routes/. Use when the task is to expose a new group of HTTP or WebSocket endpoints.
---

# Adding a new route module to vaibify

Use this recipe when you need to add a new group of HTTP or WebSocket
endpoints to the vaibify FastAPI backend. Every route module follows
the same contract; follow these steps in order.

## Prerequisites

Read these first:

- `AGENTS.md` (repo root) — global rules and traps
- `vaibify/gui/AGENTS.md` — backend contracts
- the `container-mutations` skill — for any route whose path carries
  `{sContainerId}`: what the carrier-mode declaration in step 4 means,
  and the ledger a container exec changes
- the `host-mode-and-paths` skill — for any route that composes a path
- `vaibify/gui/routes/committedFileRoutes.py` — small, current reference
  for a container-scoped module (agent actions, carrier declarations, a
  read on the frozen read list); `vaibify/gui/routes/runtimeSetupRoutes.py`
  is the hub-only, control-plane counterpart. (`figureRoutes.py`, the old
  reference, is still recorded in the carrier-mode allow-list that may
  only shrink, so it shows nothing about step 4.)

## Steps

### 1. Create `vaibify/gui/routes/<name>Routes.py`

The filename is camelCase ending in `Routes.py` (e.g., `metricsRoutes.py`,
`userRoutes.py`). The file must declare `__all__`, provide a module
docstring, and export a module-level function `fnRegisterAll(app, dictCtx)`
(a module that confines paths to the workspace takes it as a third
argument, as `fileRoutes` does: `fnRegisterAll(app, dictCtx, sWorkspaceRoot)`).

Minimal scaffold:

```python
"""<One-sentence description of what this route group does>."""

__all__ = ["fnRegisterAll"]

from fastapi import HTTPException

from ..routeScope import S_CARRIER_TYPED_READ, ffnDeclareCarrierMode


def _fnRegisterExample(app, dictCtx):
    """Register GET /api/<name>/{sContainerId}/<resource>."""

    @app.get("/api/<name>/{sContainerId}/<resource>")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictReadExample(sContainerId: str):
        fnRequire = dictCtx["require"]
        fnRequire(sContainerId)
        # ... implementation ...
        return {"bOk": True}


def fnRegisterAll(app, dictCtx):
    """Register all <name> routes."""
    _fnRegisterExample(app, dictCtx)
```

Notes:

- A handler is named for what it returns, like every other function:
  `fdict…` for a JSON dict (most handlers), `fresponse…` for a
  `Response`, `fn…` only for one that returns nothing.
  `tests/testStyleInvariants.py` fails an `fn` function that returns a
  value, and that budget is zero.
- Functions are about 20 to 30 lines (a guideline: split for reuse or a
  genuine conceptual boundary, never to meet a line count). Factor helpers
  out (`_fn…`, `_fs…`, `_fdict…`) if a handler grows.
- The module-level `fnRegisterAll` should only call the per-endpoint
  `_fn…` helpers. Keep it thin so tests can import it without side
  effects.
- For container-path joins use `posixpath`, never `os.path`. Never use
  `WORKSPACE_ROOT` as the jail: resolve the project's root with
  `projectRoots.fsResolveProjectRoot(sContainerId, WORKSPACE_ROOT)` (the
  constant is only the container answer the resolver is handed) and
  confine a caller's path with
  `fsValidatePathWithinRoot(sAbsPath, sProjectRoot)`. A route that WRITES a caller's path also applies
  `fnRejectWriteDenylistedPath` and passes `fdictConfinedWriteKeywords`
  to the write. All three live in `vaibify.gui.pipelineServer`, beside
  the figure helpers `fsResolveFigurePath` and
  `fbaFetchFigureWithFallback`; the `host-mode-and-paths` skill explains
  the resolvers.
- For shell-quoting container command arguments, use
  `fsShellQuote` from `vaibify.gui.pipelineRunner`.
- If the route needs Docker access, call through
  `dictCtx["docker"]`; do not import Docker utilities directly.

### 2. Register the module in `vaibify/gui/routes/__init__.py`

Add the module name to both the `__all__` list and the
`from . import (...)` block. Preserve the existing order grouping
where it makes sense; otherwise append.

### 3. Mount the module

Importing a module registers nothing. Add an explicit call,
`routes.<name>Routes.fnRegisterAll(app, dictCtx)`, to
`pipelineServer._fnRegisterAllRoutes`, which both the viewer and the hub
build. A route that belongs only to the hub's control plane is imported
and called inside `appFactory._fnRegisterHubLifecycle` instead, as
`runtimeSetupRoutes` is. No test catches a module that is imported but
never mounted; its own endpoint tests (step 7) are what fail.

### 4. Declare each route's scope and carrier mode

`fnValidateRouteScopesOrRaise` refuses to build the app while any
mutating route has no authorization scope, so every test that builds an
app fails, not one. A path carrying `{sContainerId}` is scoped by
convention: a mutating route is `container-owner`, a GET or HEAD is
`container-read`. Anything else is declared by you:

- **A route whose path carries `{sContainerId}`** declares what it does
  to the container with `@ffnDeclareCarrierMode(...)`, placed BELOW the
  `@app.<verb>(...)` line. An undeclared one fails
  `testEveryContainerScopedRouteEitherDeclaresOrIsRecordedAsAwaiting`,
  and `SET_ROUTES_AWAITING_CARRIER_MODE` may only shrink. The
  declaration authorizes nothing: the handler still opens a carrier
  around each mutation. Choose the mode with the `container-mutations`
  skill; `S_CARRIER_TYPED_READ` only for a handler that reaches no exec
  and no container write.
- **A GET whose path carries `{sContainerId}`** is lease-enforced, and
  must also be added, with a comment saying what it reads, to
  `routeScope.SET_CONTAINER_READ_ROUTES`
  (`testContainerReadScopeIsAFrozenRatchetedAllowlist`).
- **A mutating route with no `{sContainerId}`** is named in
  `routeScope.DICT_CONTROL_PLANE_SCOPES` with its scope and a comment
  saying why (`runtimeSetupRoutes` uses `S_SCOPE_BROWSER_HUB`), or
  stamped with `@ffnRouteScope(...)` below its `@app.<verb>(...)` line.

If a handler execs into or writes to the container,
`tests/mutationInventory.json` gains a row: regenerate it with
`python tools/generateMutationInventory.py --write` and classify the row
as the `container-mutations` skill describes, or
`tests/testMutationInventory.py` fails.

### 5. Register state-mutating endpoints with `@ffnAgentAction`

If any endpoint on the new module mutates state and a researcher
could reasonably invoke it from the UI (run, test, verify, commit,
create, edit, delete, push, publish, upload, write, etc.), it must
appear in the agent-action catalog so the in-container `vaibify-do`
CLI can translate researcher intent into the same call. Use POST, PUT
or DELETE for a mutation: `testAgentActionCatalogShape` refuses a PATCH
entry, and `testAgentActionRegistered` does not look at PATCH routes.

Two things go together:

1. **Decorate the handler.** Import the decorator and apply it
   directly above the FastAPI path-op decorator:
   ```python
   from ..actionCatalog import ffnAgentAction

   @ffnAgentAction("my-new-action")
   @app.post("/api/my-thing/{sContainerId}/do-it")
   @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
   async def fdictRunMyThing(sContainerId: str): ...
   ```

2. **Add a catalog entry** in
   `vaibify/gui/actionCatalog.py::LIST_AGENT_ACTIONS`. Copy the shape
   of existing entries: `sName`, `sCategory`, `sMethod`, `sPath`,
   `bAgentSafe`, `sDescription`. The `sName` here must match the
   decorator argument exactly. Add `saQueryFields` naming every handler
   parameter FastAPI reads from the query string (anything that is not
   a path placeholder, a body model or the `Request`);
   `testGeneratedActionsSendFieldsOnTheTransportTheRouteReads` fails
   otherwise, because `vaibify do` would send that field as a body the
   route never reads.

Pick `bAgentSafe`:

- **True** — execution, read-side work, test runs, agent authoring
  (writing files, creating/updating steps the agent is working on),
  committing canonical state, downloading inputs.
- **False** — destructive (delete, clean, kill, force), externally
  visible (push to Overleaf, publish to Zenodo), or requiring
  researcher judgment (accept plots as standard, set Zenodo metadata,
  reorder pipeline semantics). `push-to-github` is agent-safe by a
  recorded decision; read its catalog entry before citing it as
  precedent.

Every state-mutating route, whether or not the GUI invokes it, must
also have a CLI equivalent. Register it in exactly one place:

- `LIST_AGENT_ACTIONS` — callable by agents and by `vaibify do`
  (ships into the container);
- `LIST_RESEARCHER_ACTIONS` — `vaibify do` on the host only, never
  shipped into the container (credential-free researcher actions); the
  route must also be in `SET_INTENTIONALLY_EXCLUDED_PATHS`, and its
  entry needs `saQueryFields` on the same rule as above;
- `DICT_ROUTE_TO_HOST_COMMAND` — a hand-written `vaibify` subcommand
  (credentials, typed confirmations, interactive frames), which must
  already exist;
- `SET_GUI_ONLY_ROUTES` — genuine browser plumbing, with a one-line
  reason.

The last three live in `vaibify/gui/researcherActions.py`.
`SET_INTENTIONALLY_EXCLUDED_PATHS` (in `actionCatalog.py`, with a
comment) keeps a route off the agent lane, and is not a CLI home: an
excluded route still needs exactly one of the three, whether or not the
GUI calls it. The setup wizard's step route is both excluded and mapped
to `vaibify setup-docker` in `DICT_ROUTE_TO_HOST_COMMAND`.

Confirm registration:

```bash
python -m pytest tests/testArchitecturalInvariants.py::testAgentActionRegistered -v
python -m pytest tests/testArchitecturalInvariants.py::testEveryGuiInvokedMutationHasCliEquivalent -v
python -m pytest tests/testArchitecturalInvariants.py::testEveryCatalogActionHasCliCommand -v
python -m pytest tests/testArchitecturalInvariants.py::testGeneratedActionsSendFieldsOnTheTransportTheRouteReads -v
python -m pytest tests/testHubAppGovernance.py tests/testResearcherActions.py -v
```

`testAgentActionRegistered` builds only the viewer app;
`testHubControlPlaneRoutesAreGoverned` applies the same rule to the hub.

### 6. Raise the module-size caps you cross

`testModuleSizeIsBounded` holds every module to `I_MODULE_LINE_CAP`, or
to its entry in `tests/testArchitecturalInvariants.py::DICT_GRANDFATHERED_MODULE_LINES`,
which records the module's size when it was last raised. The modules a
new route registers in (`actionCatalog.py`, `routeScope.py`,
`appFactory.py`, `pipelineServer.py`) sit at or near their caps, so a
registration line usually trips one. Raise that entry by exactly what
the edit needs, with a one-line dated reason above it, as the setup
wizard did:

```python
    # 1544 -> 1548 (2026-10-24): the setup wizard's step route is excluded from the agent catalog, with why.
    "actionCatalog.py": 1548,
```

A module still at the default cap takes its first entry the same way.
Never delete blank lines or docstrings to fit under a cap.

### 7. Add tests

Create `tests/test<Name>Routes.py`. At minimum:

- A test that imports the module and confirms `fnRegisterAll` is
  callable.
- One test per new endpoint, using the FastAPI `TestClient`. Look at
  existing `tests/testPipelineRoutes*.py` and `tests/testSyncRoutes*.py`
  for mocking patterns for the Docker context.

Do not add Docker-dependent tests here — those go under the
`testContainerBuildIntegration.py`-style exclusions.

### 8. Run the contract tests

```bash
python -m pytest tests/testArchitecturalInvariants.py -v
python -m pytest tests/testCarrierModeDeclaration.py tests/testSecurityBoundaryInvariants.py tests/testStyleInvariants.py -v
```

Expect these to pass without modification:

- `testEveryRouteModuleExportsRegisterAll`
- `testAllRouteModulesRegisteredInInit`
- `testAllPackageModulesDefineDunderAll` (if your file lands outside
  `routes/`, make sure it declares `__all__`)
- `testModuleSizeIsBounded`, once step 6 is done
- `testEveryContainerScopedRouteEitherDeclaresOrIsRecordedAsAwaiting`
  and `testContainerReadScopeIsAFrozenRatchetedAllowlist`

### 9. Run the new route's tests and the full suite

```bash
python -m pytest tests/test<Name>Routes.py -v
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
```

### 10. Exercise the endpoint end-to-end if possible

If the route affects the dashboard or reports container state, start a
real container and confirm the GUI reflects the new data correctly.
Remember: the dashboard must always reflect the true state of the
workflow and the container. Do not cache, short-circuit, or hide
errors.

## Common failure modes

- **`testAllRouteModulesRegisteredInInit` fails.** You forgot to add
  the module to the `from . import (...)` block in
  `vaibify/gui/routes/__init__.py`, which is what the test reads. Add
  it to `__all__` as well; the test does not check that list.
- **Every app-building test fails with "Mutating routes with no declared
  authorization scope".** Step 4: a mutating route outside the
  `{sContainerId}` convention needs a `DICT_CONTROL_PLANE_SCOPES` entry
  or an `@ffnRouteScope` stamp.
- **Import error at app startup.** The route module imports a symbol
  that doesn't exist, or there's a circular import. Route modules can
  import from `..pipelineServer`, `..pipelineRunner`, and domain
  modules, but not from other route modules — those are siblings.
- **A 500 error on the new endpoint when the container is stopped.**
  Handlers should check container state via `dictCtx["require"]` (called with the container id)
  early and return a clean 4xx error when the container is not
  running. Do not mask the error — surface it.
- **The endpoint answers 404 though every contract test passes.** The
  module is imported but not mounted: step 3.

## Do not

- Do not bypass `VaibifyApi` on the frontend side if you're also
  adding a JS caller for this endpoint — route through
  `scriptApiClient.js`.
- Do not embed science-specific example names (planet names, system
  names) in route paths, sample data, or test fixtures.
- Do not add a new polling loop on the frontend without discussion —
  see the invariants in `vaibify/gui/static/AGENTS.md`.
