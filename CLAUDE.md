# Vaibify Architecture Guide

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific pipelines inside Docker containers. The backend is a FastAPI
server (Python); the frontend is plain JavaScript using IIFE modules
(no bundler, no npm, no ES modules).

## Style Guide

All code follows Hungarian notation with camelCase:

- Prefixes: b=bool, i=int, f=float, s=string, list=list/array, dict=dict/object
- Functions: fn=void, fs=string, fb=bool, fi=int, ff=float, flist=list, fdict=dict, fset=set
- JavaScript additionally: el=DOM element, ws=WebSocket
- Functions must be under 20 lines
- No abbreviations for words under 8 characters

## Python Backend (vaibify/gui/)

### Module Map

The backend has 39 Python modules organized into three layers:

**Application layer** (app factory, routing, context):
- `pipelineServer.py` (1,153) -- FastAPI app factory, Pydantic models, shared utilities, WebSocket dispatch. Creates the app via `fappCreateApplication()`. Routes are delegated to the `routes/` package.
- `routeContext.py` (107) -- Typed `RouteContext` wrapper for the `dictCtx` dict. Provides both attribute access (`dictCtx.docker`) and dict access (`dictCtx["docker"]`).

**Route modules** (vaibify/gui/routes/, 12 modules):
- `stepRoutes.py` -- Step CRUD (create, read, update, delete, reorder)
- `fileRoutes.py` -- File upload, download, pull, directory listing
- `syncRoutes.py` -- Overleaf, Zenodo, GitHub push/pull
- `testRoutes.py` -- Test generation, save-and-run, run categories
- `plotRoutes.py` -- Plot standardization, comparison, PNG conversion
- `pipelineRoutes.py` (630) -- Pipeline state, kill, clean, acknowledge, file-status polling, test markers
- `terminalRoutes.py` -- Terminal WebSocket session
- `workflowRoutes.py` -- Workflow search, create, connect
- `settingsRoutes.py` -- Settings get/put, log routes
- `figureRoutes.py` -- Figure HEAD/GET serving
- `scriptRoutes.py` -- Script detection, dependency scanning
- `systemRoutes.py` -- Monitor, runtime info, user info

Each route module exports `fnRegisterAll(app, dictCtx)`. All 12 are imported eagerly in `routes/__init__.py` for early error detection.

**Domain modules** (core logic, extracted from former monoliths):
- `pipelineRunner.py` (773) -- Pipeline step execution orchestrator. Public API: `fnRunAllSteps`, `fnRunFromStep`, `fnRunSelectedSteps`, `fnVerifyOnly`, `fnRunAllTests`.
- `pipelineUtils.py` (109) -- True leaf module with ZERO intra-package imports. Contains `fsShellQuote` and all `_fnEmit*` event helpers. Exists to break circular import cycles.
- `pipelineValidator.py` (113) -- Preflight validation (directory exists, scripts exist).
- `pipelineLogger.py` (159) -- Logging callbacks, log file writing, state updates during execution.
- `pipelineTestRunner.py` (258) -- Test execution within pipeline runs (per-category, legacy format).
- `interactiveSteps.py` (99) -- Interactive step pause/resume/complete protocol.
- `pipelineState.py` (134) -- Pipeline state persistence to `/workspace/.vaibify/pipeline_state.json`.
- `workflowManager.py` (749) -- Workflow CRUD, variable resolution, step references, dependency graph.
- `fileStatusManager.py` (484) -- File-status polling, mtime tracking, step invalidation, verification freshness. Contains the formal verification state machine documentation in its module docstring.
- `testStatusManager.py` (122) -- Test result recording, aggregate state computation, test file cleanup.
- `fileIntegrity.py` (129) -- SHA-256 script hashing, path normalization, change detection.
- `syncDispatcher.py` (688) -- Sync operations (Overleaf, GitHub, Zenodo), DAG visualization, test marker commands.

**Test generation modules** (extracted from former 3,652-line testGenerator.py):
- `testGenerator.py` (755) -- Orchestrator for test generation. Re-exports all symbols from the 5 modules below.
- `testParser.py` (83) -- Python syntax validation, import repair, code extraction. Zero intra-package imports.
- `dataPreview.py` (81) -- File preview generation (numpy, HDF5, text).
- `conftestManager.py` (120) -- Pytest conftest.py plugin template and marker writing.
- `llmInvoker.py` (400) -- Claude API calls, prompt building, CLAUDE.md management.
- `templateManager.py` (457) -- Template hashing, test code builders, template constants.
- `introspectionScript.py` (1,108) -- Builds a self-contained Python script (as an f-string) that runs inside Docker containers to introspect data files. This intentionally duplicates format-handling logic from `dataLoaders.py` because container scripts cannot import from the host.
- `dataLoaders.py` (1,059) -- Dispatch table mapping 50 file extensions to loader functions. Used both at runtime and embedded in generated test code via `fsReadLoaderSource()`.

**Other modules:**
- `commandUtilities.py` (106) -- Script path extraction from commands.
- `dependencyScanner.py` (485) -- Code dependency analysis for scripts.
- `director.py` (576) -- Standalone CLI runner (has intentionally divergent `fbValidateWorkflow` and `fdictBuildGlobalVariables` because it operates on the host filesystem, not container paths).
- `registryRoutes.py` (643) -- Project registry API.
- `terminalSession.py` (90) -- PTY bridge for terminal WebSocket.
- `resourceMonitor.py` (92) -- Container CPU/memory stats.
- `figureServer.py` (23) -- MIME type lookup.
- `setupServer.py` (154) -- Setup wizard host-side server.

### Dependency Graph

```
pipelineUtils (leaf -- zero imports)
commandUtilities (leaf)
pipelineState (leaf)
figureServer (leaf)
testParser (leaf)

workflowManager <-- most modules depend on this
fileIntegrity <-- pipelineRunner, fileStatusManager, syncDispatcher
pipelineValidator <-- pipelineRunner (re-export)
pipelineLogger <-- pipelineRunner (re-export)
pipelineTestRunner <-- pipelineRunner (re-export, 1 deferred import back)
interactiveSteps <-- pipelineRunner (re-export)

pipelineRunner <-- pipelineServer, route modules
fileStatusManager <-- pipelineServer (re-export)
testStatusManager <-- pipelineServer (re-export)
syncDispatcher <-- route modules

pipelineServer <-- app entry point, imports everything
routes/* <-- imported by pipelineServer via routes/__init__.py
```

All imports are acyclic at module load time. One deferred import remains: `pipelineTestRunner` defers importing `_ftRunCommandList` from `pipelineRunner` to avoid a cycle (pipelineRunner eagerly re-exports pipelineTestRunner).

### Re-export Pattern

Several orchestrator modules re-export symbols from extracted child modules for backward compatibility:
- `pipelineRunner.py` re-exports 34 symbols from 6 modules
- `pipelineServer.py` re-exports 28 symbols from fileStatusManager and testStatusManager, plus 49 lazily via `__getattr__` from route modules
- `testGenerator.py` re-exports ~49 symbols from 5 modules
- `syncDispatcher.py` re-exports 7 symbols from fileIntegrity

All modules define `__all__` to declare their public API.

### Verification State Machine

Each workflow step carries a `dictVerification` dict. The formal state machine is documented in `fileStatusManager.py`'s module docstring. Key fields:

- `sUnitTest`: untested | passed | failed (set by test runner)
- `sUser`: untested | passed | failed (set by researcher clicking UI badge)
- `sIntegrity`, `sQualitative`, `sQuantitative`: per-category test results
- `bUpstreamModified`: True when an upstream step's outputs changed
- `listModifiedFiles`: list of changed output paths (set by polling)

State transitions:
- Step executes -> `sUser` resets to "untested"
- Data file changes -> `sUnitTest` resets to "untested"
- Plot file newer than `sLastUserUpdate` -> `sUser` resets to "untested"
- Upstream changes -> `bUpstreamModified = True`, `sUnitTest` -> "untested"

## JavaScript Frontend (vaibify/gui/static/)

### Module Map (24 IIFE modules)

The frontend uses the IIFE pattern: `var ModuleName = (function() { ... return { publicApi }; })();`. No build tools, no npm, no ES modules.

**Foundation (loaded first):**
- `scriptUtilities.js` (100) -- `VaibifyUtilities`: pure functions (fnEscapeHtml, fsSanitizeErrorForUser, fsFormatUtcTimestamp, fsResolveTemplate, fsTestCategoryLabel)
- `scriptApiClient.js` (111) -- `VaibifyApi`: centralized fetch wrapper (fdictGet, fdictPost, fdictPut, fnDelete, fbHead). All HTTP calls go through this module.
- `scriptWebSocket.js` (138) -- `VaibifyWebSocket`: pipeline WebSocket connection, event dispatch via `fnOnEvent(sType, fnHandler)`, pending action queue.
- `scriptPolling.js` (93) -- `VaibifyPolling`: unified polling manager for file-status (5s) and pipeline-state (10s) intervals.

**Rendering:**
- `scriptStepRenderer.js` (675) -- `VaibifyStepRenderer`: HTML generation for step list items, verification blocks, detail items, run stats.
- `scriptStepEditor.js` (286) -- `PipeleyenStepEditor`: step editing form.

**Feature modules (loaded after scriptApplication.js):**
- `scriptPipelineRunner.js` (939) -- `PipeleyenPipelineRunner`: pipeline execution, WebSocket event handling, interactive steps, state recovery, run/kill/verify actions.
- `scriptTestManager.js` (831) -- `PipeleyenTestManager`: test generation, running, markers, verification state updates.
- `scriptContainerManager.js` (475) -- `PipeleyenContainerManager`: container landing page, build/start/stop/remove.
- `scriptWorkflowManager.js` (587) -- `VaibifyWorkflowManager`: workflow selection, creation wizard, dropdown switcher.
- `scriptSyncManager.js` (277) -- `VaibifySyncManager`: push modal, connection setup, sync error display.
- `scriptDependencyScanner.js` (460) -- `PipeleyenDependencyScanner`: dependency detection modal.
- `scriptPlotStandards.js` (209) -- `PipeleyenPlotStandards`: plot standardization and comparison.
- `scriptEventBindings.js` (806) -- `PipeleyenEventBindings`: all delegated event handlers (click registry pattern), toolbar/menu/context/resize bindings.
- `scriptFileOperations.js` (441) -- `PipeleyenFileOps`: file existence checking, status coloring, change detection, clipboard.
- `scriptModals.js` (139) -- `PipeleyenModals`: confirm, input, error modals.
- `scriptFiles.js` (190) -- `PipeleyenFiles`: in-container file browser panel.
- `scriptDirectoryBrowser.js` (184) -- `PipeleyenDirectoryBrowser`: host directory browser modal.
- `scriptFilePull.js` (153) -- `PipeleyenFilePull`: pull-to-host modal.

**Pre-existing modules (not refactored):**
- `scriptFigureViewer.js` (1,019) -- `PipeleyenFigureViewer`: PDF/image rendering, dual viewer, history.
- `scriptTerminal.js` (619) -- `PipeleyenTerminal`: xterm.js terminal pane management.
- `scriptResourceMonitor.js` (257) -- `VaibifyMonitor`: container resource monitoring panel.
- `scriptSetupWizard.js` (394) -- `VaibifySetup`: initial setup wizard.

**Core application:**
- `scriptApplication.js` (1,998) -- `PipeleyenApp`: application state, initialization, rendering orchestration. Exposes ~60 public API methods that other modules call.

### State Management

`scriptApplication.js` manages all state in 3 objects:

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
    setExpandedQualitative/Quantitative/Integrity,
    bShowTimestamps, iContextStepIndex, sContextFilePath
}
```

`_fnResetWorkflowState()` uses a factory function to reset all fields atomically, preventing state leaks across workflow switches. Sets use `.clear()` (not reassignment) to preserve references held by the render context.

### Rendering

`fnRenderStepList()` is debounced via `requestAnimationFrame` — multiple rapid calls (from WebSocket events, polling, user clicks) coalesce into a single DOM rebuild. `fnRenderStepListSync()` is available for the rare case where the DOM must be read immediately after rendering.

Every render calls `fnUpdateHighlightState()` to synchronize the toolbar verification indicator (checkmark + color shift) with the current workflow state.

### Data Flow: Pipeline Run

```
User clicks "Run All"
  -> PipeleyenPipelineRunner.fnRunAll()
  -> VaibifyWebSocket.fnSend({sAction: "runAll"})
  -> Backend: pipelineServer WebSocket handler
  -> pipelineRunner.fnRunAllSteps()
  -> For each step:
       Backend emits stepStarted, output, stepPass/stepFail via WebSocket
  -> Frontend: VaibifyWebSocket dispatches to registered handlers
  -> PipeleyenPipelineRunner.fnHandlePipelineEvent()
  -> PipeleyenApp.fnSetStepStatus() + PipeleyenApp.fnRenderStepList()
  -> VaibifyStepRenderer.fsRenderStepItem() generates HTML
  -> DOM updated (debounced)
```

### Data Flow: File-Status Polling

```
Every 5 seconds (VaibifyPolling):
  -> VaibifyApi.fdictGet("/api/pipeline/{id}/file-status")
  -> Backend: pipelineRoutes._fnRegisterFileStatus handler
  -> fileStatusManager: compute mtimes, detect changes, check stale verifications
  -> Response: {dictModTimes, dictInvalidatedSteps, dictTestMarkers, ...}
  -> Frontend: PipeleyenApp.fnProcessFileStatusResponse()
  -> Updates caches, applies invalidations, applies test markers
  -> PipeleyenApp.fnRenderStepList() (debounced -- cascading updates coalesce)
```

## Testing

2,200+ unit tests in `tests/`. Run with:
```bash
python -m pytest tests/ -q --ignore=tests/testGJ1132Build.py
```

The `testGJ1132Build.py` tests require a running Docker container and are excluded from routine runs.

Test files are organized by module: `testPipelineRunnerCoverage.py`, `testPipelineServerCoverage.py`, `testSyncDispatcherCoverage.py`, `testRefactoringCoverage.py`, `testExhaustiveCoverage.py`, etc. Every non-Docker pure function in the backend has unit test coverage.

## Known Technical Debt

1. `introspectionScript.py` (1,108 lines) duplicates format-handling logic from `dataLoaders.py`. This is inherent: the introspection script runs inside Docker containers that cannot import from the host Python environment.

2. `director.py` has its own `fbValidateWorkflow` and `fdictBuildGlobalVariables` that diverge from `workflowManager.py`. This is intentional: director.py operates on the host filesystem with `os.path` and `os.makedirs`, while workflowManager uses `posixpath` for container paths.

3. `scriptFigureViewer.js` (1,019 lines) was not part of the refactoring effort. It handles PDF rendering, dual-viewer comparison, and history management as a single cohesive module.

4. Re-export blocks (126 symbols across 4 orchestrator modules) exist for backward compatibility. Callers should eventually migrate to importing from canonical modules directly.
