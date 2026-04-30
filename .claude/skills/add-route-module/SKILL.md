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
- `vaibify/gui/routes/figureRoutes.py` — small, clean reference example

## Steps

### 1. Create `vaibify/gui/routes/<name>Routes.py`

The filename is camelCase ending in `Routes.py` (e.g., `metricsRoutes.py`,
`userRoutes.py`). The file must declare `__all__`, provide a module
docstring, and export a module-level function `fnRegisterAll(app, dictCtx)`.

Minimal scaffold:

```python
"""<One-sentence description of what this route group does>."""

__all__ = ["fnRegisterAll"]

from fastapi import HTTPException


def _fnRegisterExample(app, dictCtx):
    """Register GET /api/<name>/<resource>."""

    @app.get("/api/<name>/<resource>")
    async def fnHandleExample(sArg: str):
        dictCtx["require"]()
        # ... implementation ...
        return {"bOk": True}


def fnRegisterAll(app, dictCtx):
    """Register all <name> routes."""
    _fnRegisterExample(app, dictCtx)
```

Notes:

- Functions remain under 20 lines. Factor helpers out (`_fn…`,
  `_fs…`, `_fdict…`) if a handler grows.
- The module-level `fnRegisterAll` should only call the per-endpoint
  `_fn…` helpers. Keep it thin so tests can import it without side
  effects.
- For container-path joins use `posixpath`, never `os.path`. The
  workspace root and path-validation helpers live in
  `vaibify.gui.pipelineServer` (`WORKSPACE_ROOT`,
  `fnValidatePathWithinRoot`, `fsResolveFigurePath`,
  `fbaFetchFigureWithFallback`).
- For shell-quoting container command arguments, use
  `fsShellQuote` from `vaibify.gui.pipelineRunner`.
- If the route needs Docker access, call through
  `dictCtx["docker"]`; do not import Docker utilities directly.

### 2. Register the module in `vaibify/gui/routes/__init__.py`

Add the module name to both the `__all__` list and the
`from . import (...)` block. Preserve the existing order grouping
where it makes sense; otherwise append.

### 3. Register state-mutating endpoints with `@fnAgentAction`

If any endpoint on the new module mutates state and a researcher
could reasonably invoke it from the UI (run, test, verify, commit,
create, edit, delete, push, publish, upload, write, etc.), it must
appear in the agent-action catalog so the in-container `vaibify-do`
CLI can translate researcher intent into the same call.

Two things go together:

1. **Decorate the handler.** Import the decorator and apply it
   directly above the FastAPI path-op decorator:
   ```python
   from ..actionCatalog import fnAgentAction

   @fnAgentAction("my-new-action")
   @app.post("/api/my-thing/{sContainerId}/do-it")
   async def fnDoIt(sContainerId: str): ...
   ```

2. **Add a catalog entry** in
   `vaibify/gui/actionCatalog.py::LIST_AGENT_ACTIONS`. Copy the shape
   of existing entries: `sName`, `sCategory`, `sMethod`, `sPath`,
   `bAgentSafe`, `sDescription`. The `sName` here must match the
   decorator argument exactly.

Pick `bAgentSafe`:

- **True** — execution, read-side work, test runs, agent authoring
  (writing files, creating/updating steps the agent is working on),
  committing canonical state, downloading inputs.
- **False** — destructive (delete, clean, kill, force), externally
  visible (push to GitHub/Overleaf, publish to Zenodo), or requiring
  researcher judgment (accept plots as standard, set Zenodo metadata,
  reorder pipeline semantics).

If the new route is state-mutating but should NOT be invokable by
the agent (control-plane, credential setup, poll-driven scan, etc.),
add its `(sMethod, sPath)` tuple to
`SET_INTENTIONALLY_EXCLUDED_PATHS` in the same file with a comment
explaining why. This keeps the invariant test silent on intentional
omissions.

Confirm registration:

```bash
python -m pytest tests/testArchitecturalInvariants.py::testAgentActionRegistered -v
```

### 4. Add tests

Create `tests/test<Name>Routes.py`. At minimum:

- A test that imports the module and confirms `fnRegisterAll` is
  callable.
- One test per new endpoint, using the FastAPI `TestClient`. Look at
  existing `tests/testPipelineRoutes*.py` and `tests/testSyncRoutes*.py`
  for mocking patterns for the Docker context.

Do not add Docker-dependent tests here — those go under the
`testContainerBuildIntegration.py`-style exclusions.

### 5. Run the contract tests

```bash
python -m pytest tests/testArchitecturalInvariants.py -v
```

Expect these to pass without modification:

- `testEveryRouteModuleExportsRegisterAll`
- `testAllRouteModulesRegisteredInInit`
- `testAllPackageModulesDefineDunderAll` (if your file lands outside
  `routes/`, make sure it declares `__all__`)

### 6. Run the new route's tests and the full suite

```bash
python -m pytest tests/test<Name>Routes.py -v
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
```

### 7. Exercise the endpoint end-to-end if possible

If the route affects the dashboard or reports container state, start a
real container and confirm the GUI reflects the new data correctly.
Remember: the dashboard must always reflect the true state of the
workflow and the container. Do not cache, short-circuit, or hide
errors.

## Common failure modes

- **`testAllRouteModulesRegisteredInInit` fails.** You forgot to add
  the module to both the `__all__` and the `from . import (...)`
  block in `vaibify/gui/routes/__init__.py`. Both are required.
- **Import error at app startup.** The route module imports a symbol
  that doesn't exist, or there's a circular import. Route modules can
  import from `..pipelineServer`, `..pipelineRunner`, and domain
  modules, but not from other route modules — those are siblings.
- **A 500 error on the new endpoint when the container is stopped.**
  Handlers should check container state via `dictCtx["require"]()`
  early and return a clean 4xx error when the container is not
  running. Do not mask the error — surface it.
- **Module not picked up at startup.** Both the `__all__` entry and
  the `from . import (...)` block in `vaibify/gui/routes/__init__.py`
  must include the new module name.

## Do not

- Do not bypass `VaibifyApi` on the frontend side if you're also
  adding a JS caller for this endpoint — route through
  `scriptApiClient.js`.
- Do not embed science-specific example names (planet names, system
  names) in route paths, sample data, or test fixtures.
- Do not add a new polling loop on the frontend without discussion —
  see the invariants in `vaibify/gui/static/AGENTS.md`.
