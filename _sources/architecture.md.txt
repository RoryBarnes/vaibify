# Architecture

This document describes how Vaibify is organized internally: which modules
exist, how they depend on each other, how state flows, and where the
load-bearing invariants live. It is the "why" companion to the `AGENTS.md`
files, which state the rules; this file explains the reasoning behind them.

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific pipelines inside Docker containers. The backend is a FastAPI
server (Python); the frontend is plain JavaScript using IIFE modules
(no bundler, no npm, no ES modules).

For the human contributor workflow (how to run tests, submit PRs, follow
the style guide) see [developers.md](developers.md). For the methodology
behind the agent documentation, see [vibeCoding.md](vibeCoding.md).

## Python Backend

The backend lives under `vaibify/gui/` and is organized into four layers
by responsibility.

### Application layer

- `pipelineServer.py` — FastAPI app factory, Pydantic models, shared
  utilities, WebSocket dispatch. Creates the app via
  `fappCreateApplication()`. Routes are delegated to the `routes/` package.
- `routeContext.py` — typed `RouteContext` wrapper for the `dictCtx`
  dict. Provides both attribute access (`dictCtx.docker`) and dict access
  (`dictCtx["docker"]`).

### Route modules

Route modules live under `vaibify/gui/routes/`. Each file matching
`*Routes.py` exports an `fnRegisterAll(app, dictCtx)` function that
registers its endpoints on the FastAPI application at startup. The
`routes/__init__.py` imports every route module eagerly so that import
errors surface at startup rather than on first request.

Current route modules:

- `stepRoutes.py` — step CRUD (create, read, update, delete, reorder)
- `fileRoutes.py` — file upload, download, pull, directory listing
- `syncRoutes.py` — Overleaf, Zenodo, GitHub push/pull
- `testRoutes.py` — test generation, save-and-run, run categories
- `plotRoutes.py` — plot standardization, comparison, PNG conversion
- `pipelineRoutes.py` — pipeline state, kill, clean, acknowledge,
  file-status polling, test markers
- `terminalRoutes.py` — terminal WebSocket session
- `workflowRoutes.py` — workflow search, create, connect
- `settingsRoutes.py` — settings get/put, log routes
- `figureRoutes.py` — figure HEAD/GET serving
- `scriptRoutes.py` — script detection, dependency scanning
- `systemRoutes.py` — monitor, runtime info, user info
- `repoRoutes.py` — repository panel API for multi-repo workflows

Run `python tools/listModules.py vaibify/gui/routes` to print the
current list and each module's `__all__`.

### Domain modules

These carry the core execution logic that used to live in monolithic
files and were extracted during the 2026-01 refactor:

- `pipelineRunner.py` — pipeline step execution orchestrator. Public API:
  `fnRunAllSteps`, `fnRunFromStep`, `fnRunSelectedSteps`, `fnVerifyOnly`,
  `fnRunAllTests`.
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
- `interactiveSteps.py` — interactive step pause/resume/complete protocol.
- `pipelineState.py` — pipeline state persistence to
  `/workspace/.vaibify/pipeline_state.json`.
- `workflowManager.py` — workflow CRUD, variable resolution, step
  references, dependency graph. Uses `posixpath` because it operates on
  container paths.
- `fileStatusManager.py` — file-status polling, mtime tracking, step
  invalidation, verification freshness. The formal verification state
  machine is documented in its module docstring.
- `testStatusManager.py` — test result recording, aggregate state
  computation, test file cleanup.
- `fileIntegrity.py` — SHA-256 script hashing, path normalization,
  change detection.
- `syncDispatcher.py` — sync operations (Overleaf, GitHub, Zenodo), DAG
  visualization, test marker commands.

### Test generation modules

These were extracted from the former 3,652-line `testGenerator.py`:

- `testGenerator.py` — orchestrator for test generation. Re-exports all
  symbols from the five modules below.
- `testParser.py` — Python syntax validation, import repair, code
  extraction. Zero intra-package imports.
- `dataPreview.py` — file preview generation (numpy, HDF5, text).
- `conftestManager.py` — pytest conftest.py plugin template and marker
  writing.
- `llmInvoker.py` — Claude API calls, prompt building, CLAUDE.md
  management.
- `templateManager.py` — template hashing, test code builders, template
  constants.
- `introspectionScript.py` — builds a self-contained Python script (as
  an f-string) that runs inside Docker containers to introspect data
  files. This intentionally duplicates format-handling logic from
  `dataLoaders.py` because container scripts cannot import from the host.
- `dataLoaders.py` — dispatch table mapping file extensions to loader
  functions. Used both at runtime and embedded in generated test code
  via `fsReadLoaderSource()`.

### Other modules

- `commandUtilities.py` — script path extraction from commands.
- `dependencyScanner.py` — code dependency analysis for scripts.
- `director.py` — standalone CLI runner. Has intentionally divergent
  `fbValidateWorkflow` and `fdictBuildGlobalVariables` from
  `workflowManager` because it operates on the host filesystem with
  `os.path` and `os.makedirs`, while `workflowManager` uses `posixpath`
  for container paths. This is a live trap; see the `AGENTS.md` traps
  section.
- `registryRoutes.py` — project registry API.
- `terminalSession.py` — PTY bridge for terminal WebSocket.
- `resourceMonitor.py` — container CPU/memory stats.
- `figureServer.py` — MIME type lookup.
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

All imports are acyclic at module load time. One deferred import remains:
`pipelineTestRunner` defers importing `_ftRunCommandList` from
`pipelineRunner` to avoid a cycle (`pipelineRunner` eagerly re-exports
`pipelineTestRunner`).

## Re-export pattern

Several orchestrator modules re-export symbols from their extracted child
modules for backward compatibility:

- `pipelineRunner` re-exports from `pipelineValidator`, `pipelineLogger`,
  `pipelineTestRunner`, `interactiveSteps`, `pipelineUtils`, and
  `pipelineState`.
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
machine is documented in `fileStatusManager.py`'s module docstring. Key
fields:

- `sUnitTest` — `untested | passed | failed`, set by the test runner.
- `sUser` — `untested | passed | failed`, set by the researcher clicking
  the UI badge.
- `sIntegrity`, `sQualitative`, `sQuantitative` — per-category test
  results.
- `bUpstreamModified` — `True` when an upstream step's outputs changed.
- `listModifiedFiles` — list of changed output paths, set by polling.

State transitions:

- Step executes → `sUser` resets to `untested`.
- Data file changes → `sUnitTest` resets to `untested`.
- Plot file newer than `sLastUserUpdate` → `sUser` resets to `untested`.
- Upstream changes → `bUpstreamModified = True`, `sUnitTest` → `untested`.

This state machine is load-bearing for the dashboard's honesty guarantee:
the GUI must always reflect the true state of the workflow. See the
relevant trap in [../AGENTS.md](../AGENTS.md).

## JavaScript Frontend

The frontend lives under `vaibify/gui/static/` and uses the IIFE pattern:

```javascript
var ModuleName = (function () {
    // private state
    return { publicApi };
})();
```

There are no build tools, no npm, no ES modules. Modules are loaded via
script tags in the HTML in a specific order.

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

### Rendering modules

- `scriptStepRenderer.js` — `VaibifyStepRenderer`: HTML generation for
  step list items, verification blocks, detail items, run stats.
- `scriptStepEditor.js` — `PipeleyenStepEditor`: step editing form.

### Feature modules

- `scriptPipelineRunner.js` — pipeline execution, WebSocket event
  handling, interactive steps, state recovery, run/kill/verify actions.
- `scriptTestManager.js` — test generation, running, markers,
  verification state updates.
- `scriptContainerManager.js` — container landing page, build, start,
  stop, remove.
- `scriptWorkflowManager.js` — workflow selection, creation wizard,
  dropdown switcher.
- `scriptSyncManager.js` — push modal, connection setup, sync error
  display.
- `scriptDependencyScanner.js` — dependency detection modal.
- `scriptPlotStandards.js` — plot standardization and comparison.
- `scriptEventBindings.js` — all delegated event handlers (click
  registry pattern), toolbar, menu, context, and resize bindings.
- `scriptFileOperations.js` — file existence checking, status coloring,
  change detection, clipboard.
- `scriptModals.js` — confirm, input, error modals.
- `scriptFiles.js` — in-container file browser panel.
- `scriptDirectoryBrowser.js` — host directory browser modal.
- `scriptFilePull.js` — pull-to-host modal.
- `scriptReposPanel.js` — multi-repo panel for toolkit-style workflows.

### Pre-existing modules (not refactored)

- `scriptFigureViewer.js` — PDF and image rendering, dual viewer,
  history. Kept as a single cohesive module rather than split.
- `scriptTerminal.js` — xterm.js terminal pane management.
- `scriptResourceMonitor.js` — `VaibifyMonitor`: container resource
  monitoring panel.
- `scriptSetupWizard.js` — `VaibifySetup`: initial setup wizard.

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

`fnRenderStepList()` is debounced via `requestAnimationFrame`: multiple
rapid calls (from WebSocket events, polling, user clicks) coalesce into
a single DOM rebuild. `fnRenderStepListSync()` is available for the rare
case where the DOM must be read immediately after rendering.

Every render calls `fnUpdateHighlightState()` to synchronize the toolbar
verification indicator (checkmark and color shift) with the current
workflow state.

## Data flows

### Pipeline run

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

### File-status polling

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

## Testing

The test suite lives in `tests/`. Run all non-Docker tests with:

```bash
python -m pytest tests/ -q --ignore=tests/testGJ1132Build.py
```

The `testGJ1132Build.py` tests require a running Docker container and
are excluded from routine runs.

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
