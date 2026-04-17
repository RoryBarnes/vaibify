# vaibify/gui/static — frontend agent guide

This subtree contains the vanilla-JavaScript frontend. Rules here apply
to any change under `vaibify/gui/static/`.

For repo-wide rules, read `../../../AGENTS.md` first. For the reasoning
behind the module layout and state design, see
`../../../docs/architecture.md`.

## IIFE pattern

Every JS module follows the immediately-invoked function expression
(IIFE) pattern:

```javascript
var ModuleName = (function () {
    // private state
    function fnPrivateHelper() { ... }
    return {
        fnPublicApi: ...,
    };
})();
```

- No build tools, no npm, no ES modules, no bundler.
- Scripts load via `<script>` tags in a specific order set by the HTML
  template; new modules must be added in the correct load position.
- Module names start with a capital letter (`VaibifyUtilities`,
  `PipeleyenApp`); file names use camelCase with a `script` prefix
  (`scriptUtilities.js`).

## State management

`scriptApplication.js` holds all application state in three top-level
dicts:

```javascript
_dictSessionState   // session token, container id, user name, dashboard mode
_dictWorkflowState  // workflow, step status, file caches, undo stack
_dictUiState        // selected step, expansion sets, timestamp toggle
```

**Sets are shared by reference.** The render context holds references
to Sets inside `_dictUiState` (e.g., `setExpandedSteps`). Reassigning
(`setExpandedSteps = new Set()`) breaks rendering. Use `.clear()` and
mutate in place.

When resetting state across workflow switches, use
`_fnResetWorkflowState()`. This factory resets all fields atomically
and preserves reference identity where needed.

## Rendering

`fnRenderStepList()` is debounced via `requestAnimationFrame`. Multiple
rapid calls (WebSocket events, polling ticks, user clicks) coalesce
into a single DOM rebuild. Do not bypass the debounce unless you
specifically need the synchronous variant (`fnRenderStepListSync()`),
and document why.

Every render calls `fnUpdateHighlightState()` to sync the toolbar
verification indicator. If you add a new render path, call it.

## Traps

- **Sets are shared by reference.** See above. Reassigning breaks
  rendering.
- **The dashboard is ground truth.** Do not cache display state beyond
  its natural lifetime; do not short-circuit polling to appear
  responsive; do not optimistically mark steps as passed. If the
  backend is slow or a step is failing, show it. See the traps section
  in `../../../AGENTS.md`.
- **HTTP goes through `VaibifyApi`.** Do not call `fetch()` directly
  from feature modules; route through `scriptApiClient.js`. This
  centralizes error handling, auth, and response sanitization.
- **WebSocket events dispatch through `VaibifyWebSocket`.** Do not add
  raw `socket.onmessage` handlers in feature modules; register via
  `fnOnEvent(sType, fnHandler)`.
- **Polling cadences are invariants.** File-status polls every 5
  seconds; pipeline-state polls every 10 seconds. Do not change these
  without discussion — they affect the server's computed-state load.

## Public API of `scriptApplication.js`

New modules register behavior through the public API that
`scriptApplication.js` exposes (`fnSetStepStatus`, `fnRenderStepList`,
and related). Do not manipulate the state dicts or the DOM directly
from feature modules; always go through the application's public
methods.

## Non-refactored island

`scriptFigureViewer.js` is a single large module that was not part of
the 2026-01 refactor. It handles PDF rendering, dual-viewer
comparison, and history. When working inside it, follow its existing
internal structure rather than imposing the newer IIFE + state-dict
pattern wholesale.

## Discovery commands

- `ls vaibify/gui/static/*.js` — current JS modules
- `python tools/listModules.py vaibify/gui/static` — module name + first
  comment line per module
- `grep -l "var .* = (function" vaibify/gui/static/*.js` — confirm IIFE
  compliance

## Required after frontend edits

- Run the full backend test suite if the change could affect backend
  contracts (e.g., a new API call or WebSocket message shape):
  `python -m pytest tests/ -q --ignore=tests/testGJ1132Build.py`.
- Exercise the feature in a running GUI against a real container.
  There is no automated browser testing — visual confirmation is
  required for UI changes.
