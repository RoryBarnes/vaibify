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
_dictSessionState   // session token, container id, per-tab lease, user name, dashboard mode
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
- **Per-container exclusivity is a server-minted lease (`sLeaseId`).**
  The claim response carries it; store it per-tab in `sessionStorage`
  (so a reload re-asserts the same ownership instead of locking the user
  out) and present it as the `sLeaseId` query param on the pipeline and
  terminal WebSocket URLs and on the claim / release / registry REST
  calls. The lease — not the shared session token — is what says *which*
  browser session owns a container; a 409 from claim surfaces an "In use
  in another browser session" toast and the tile renders locked from
  `bOwnedByOtherSession`. A `pagehide` handler `navigator.sendBeacon`s
  the release route with the lease only; it is best-effort acceleration,
  never load-bearing (the backend's disconnect-grace reaper is the real
  release). The intended single fetch choke point is `VaibifyApi`; a
  legacy `window.fetch` shim (`fnInstallAuthenticatedFetch`) still
  injects the shared token and is known debt — do not add a second
  token-injection path, and migrate callers toward `VaibifyApi`.

## Public API of `scriptApplication.js`

New modules register behavior through the public API that
`scriptApplication.js` exposes (`fnSetStepStatus`, `fnRenderStepList`,
and related). Do not manipulate the state dicts or the DOM directly
from feature modules; always go through the application's public
methods.

## Stale-output advisories

The poll response carries `listStaleOutputAdvisories` produced by
[vaibify/gui/staleOutputDetector.py](../staleOutputDetector.py). Each
advisory has `iConsumerStepIndex`, `iLikelyProducerStepIndex`,
`listOffendingFiles`, and `fAgeDeltaSeconds`. When rendering the
Step Viewer, treat each path in `listOffendingFiles` exactly like an
L1-blocker offending file — same failure-mode glyph, no separate icon
set — and surface the suggested undeclared upstream as an extra row
in the consumer step's dependency list with a "Declare as upstream"
affordance that writes the producer's token(s) into the consumer's
`saDependencies`. The only resolution paths are "Declare as upstream"
(workflow JSON update) and the existing `run-step`. Do not add an
acknowledge or dismiss action.

## Non-refactored island

`scriptFigureViewer.js` is a single large module that was not part of
the 2026-01 refactor. It handles PDF rendering, dual-viewer
comparison, and history. When working inside it, follow its existing
internal structure rather than imposing the newer IIFE + state-dict
pattern wholesale.

## L1 blocker surfacing

- L1 blocker state lives at `_dictWorkflowState.dictBlockersByStep`
  (populated from each poll's `listBlockers`). A step's check icon
  renders only when that map has no entry for the step; not-verified
  steps get one banner glyph per `sCriterion` (`upstream-modified`,
  `script-stale`, `axis-not-green`, `attestation-stale`,
  `user-not-approved`), plus the existing failure-mode glyph on every
  offending file and dependency edge. Do not introduce an acknowledge
  affordance in any blocker context — the only path to clear a
  blocker is `run-step` (or `verify-step` for `user-not-approved`).

## L2 and L3 blocker surfacing

- L2 blocker state lives at `_dictWorkflowState.dictBlockersByStepLevel2`
  (populated from each poll's `listLevel2Blockers`). Criteria:
  `not-in-github-mirror`, `not-in-zenodo-deposit`, `figure-not-frozen`
  (per-step); `github-verify-stale`, `zenodo-verify-stale`,
  `missing-ai-declaration-step`, `arxiv-not-submitted`,
  `arxiv-mismatch`, `arxiv-version-stale` (workflow-scope,
  `iStepIndex=-1`). Workflow-scope blockers render as banner rows
  above the step list, not as per-step glyphs.
- L3 blocker state lives at `_dictWorkflowState.dictBlockersByStepLevel3`
  (populated from `listLevel3Blockers`). Per-step criteria:
  `missing-from-manifest`, `script-not-pinned`,
  `nondeterminism-undeclared`, `binary-not-declared`,
  `binary-not-captured`. Workflow-scope criteria mirror the existing
  `fbL3ReadinessOK` conjuncts: `dockerfile-not-pinned`,
  `dependency-lock-missing`, `environment-snapshot-missing`,
  `reproduce-script-missing`, `l3-attestation-stale`,
  `binaries-not-declared-or-waived`.
- Every blocker entry carries `iLevel`, `iStepIndex`, `sStepLabel`,
  `sScope`, `sCriterion`, `listOffendingFiles`,
  `listOffendingUpstreamSteps`, `sRemediationHint`. Glyph tooltips
  prefer `sRemediationHint` (server-supplied per-criterion language)
  over the static-dict `sLabel` fallback. This is enforced by the
  contract test `tests/testStepRendererBlockerGlyphs.py`.

## Climbing-the-ladder UX

- The AICS chip in the dashboard header renders a 4-state
  progression: L0 = "Self-Consistent (N steps blocking)" red, L1 =
  "Self-Consistent ✓ · Published (N blocking)" orange, L2 =
  "Published ✓ · Reproducible (env pending)" yellow, L3 =
  "Reproducible ✓" green. Each segment is clickable and scrolls the
  AICS tab to the corresponding readiness card. Logic lives in
  `scriptAicsTab.js::_fsFormatBlockerCountSuffix`.
- Per-step level cells (`scriptStepRenderer.js::_fsBuildStepLevelStrip`)
  render four columns per step row: a regression-warning column, then
  L1|L2|L3 (no text in the cells; one column-header row labels them).
  Each level is computed INDEPENDENTLY (no upward propagation) and
  arrives as a CELL dict (`sState`, `iSatisfied`, `iTotal`,
  `bRegression`). Visual vocabulary: grey filled circle =
  "not-started" (the step has no activity), red circle = "none"
  (activity, nothing satisfied), orange circle = "partial", the
  vaibify favicon image = "attained", hollow outlined grey circle =
  "unknown" (sync verify cache stale — a stale cache must NEVER
  render attained). First-attainment dates persist in
  `dictLevelHighWater` in state.json and are never erased.
- The regression column renders `dictStepLevelWarnings` VERBATIM —
  the backend gates the warning to the step's lowest non-attained
  level (a regression at a higher level is suppressed until lower
  levels pass); red ⚠ only when failed tests underlie it, orange ⚠
  for staleness/regression. Never derive warning logic client-side
  for steps.
- The Workflow row (`fsRenderWorkflowLevelHeader`) is an expandable
  step-like row: collapsed shows the same four columns at workflow
  scope; expanded renders `dictWorkflowEnvelopeDetail` (declared
  binaries with version-match and hash lights, envelope artifacts,
  determinism, remote sync summaries — a never-verified cache
  renders hollow grey, never green). The AI-declaration criterion is
  excluded from the header; its home is the AI Declaration
  interactive step (or the ghost row offering to add one).
- NO ✗/X status glyphs anywhere — failures and missing items use the
  red warning glyph ⚠; staleness uses orange ⚠ or the pencil ✎.
  X-shaped characters are permitted only as close/delete BUTTON
  chrome. `axis-not-green` carries `sSubState`
  (failed/outputs-missing/outputs-changed/untested) mapped through
  `_DICT_AXIS_SUBSTATE_GLYPHS` — failed/missing render red ⚠,
  outputs-changed renders the orange pencil ✎, and untested renders
  NO banner glyph (the orange status light carries "not yet done").
  Per-file marks read `dictOffendingFileMarks` ("stale" → orange ✎,
  "failed"/"missing" → red ⚠).
- The pencil banner glyph on the step card is suppressed when an L1
  blocker is active (the `⚠ script-stale` glyph carries the same
  fact). The per-script pencil badge in the verification panel is
  preserved — it identifies which script went stale.
- The `?` button next to the AICS chip opens
  `scriptLegendPanel.js`'s legend modal. It lists every glyph per
  level with live counts of active blockers. The only resolution
  paths listed are `run-step` and `verify-step` — there is no
  acknowledge affordance.
- File-list red text now distinguishes three states via modifier
  classes on `.file-necessary-red`: `.file-missing-state` (upright),
  `.file-stale-state` (dotted underline), `.file-unattested-state`
  (italic). Same color, different secondary affordance so the user
  can scan without expanding the verification panel. Logic in
  `scriptFileOperations.js::_fsRedModifierClass`.
- `_dictWorkflowState.iCachedAicsLevel` is a scalar (not a Set);
  mutate it via `PipeleyenApp.fnSetCachedAicsLevel(iLevel)`. The
  shared-Sets-by-reference trap does not apply.

## Discovery commands

- `ls vaibify/gui/static/*.js` — current JS modules
- `python tools/listModules.py vaibify/gui/static` — module name + first
  comment line per module
- `grep -l "var .* = (function" vaibify/gui/static/*.js` — confirm IIFE
  compliance

## Required after frontend edits

- Run the full backend test suite if the change could affect backend
  contracts (e.g., a new API call or WebSocket message shape):
  `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`.
- Exercise the feature in a running GUI against a real container.
  There is no automated browser testing — visual confirmation is
  required for UI changes.
