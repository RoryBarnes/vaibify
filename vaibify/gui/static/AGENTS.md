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
  (populated from each poll's `listBlockers`). Collapsed
  rows render NO inline blocker glyphs: every warning a step carries
  (the backend level warning, the dominant L1 blocker's remediation
  hint, script/output/upstream staleness, unseeded randomness) is
  consolidated by `fdictRegressionWarning` →
  `_flistStepWarningReasons` into the single ⚠ column of the level
  strip, one plain-English tooltip line per reason, deduplicated
  against the dominant blocker. Red is reserved for genuine failures
  (`_fbStepWarningIsRed`: backend red, or the red axis glyph meta).
  The per-file failure-mode glyphs on offending files and dependency
  edges in the expanded detail are unchanged. The criterion glyph
  dicts (`_DICT_BLOCKER_CRITERION_GLYPHS`,
  `_DICT_AXIS_SUBSTATE_GLYPHS`) survive as the tooltip-language +
  severity source and feed the legend catalog. Do not introduce an
  acknowledge affordance in any blocker context — the only path to
  clear a blocker is `run-step` (or `verify-step` for
  `user-not-approved`).

## L2 and L3 blocker surfacing

- L2 blocker state lives at `_dictWorkflowState.dictBlockersByStepLevel2`
  (populated from each poll's `listLevel2Blockers`). Criteria:
  `not-in-github-mirror`, `not-in-zenodo-deposit`, `figure-not-frozen`,
  `ai-declaration-unattested` (per-step); `github-verify-stale`,
  `zenodo-verify-stale`, `missing-ai-declaration-step`,
  `arxiv-mismatch`, `arxiv-version-stale` (workflow-scope,
  `iStepIndex=-1`; the arXiv pair fires only when an arXiv
  submission is recorded — the claim is opt-in, so an untracked
  submission emits no blocker). Workflow-scope blockers render
  as banner rows above the step list, not as per-step glyphs. The AI
  declaration's sign-off is a LEVEL 2 requirement (ruling
  2026-07-02: the declaration only has meaning at publication) —
  ai-declaration steps emit no L1 blockers, their L1 cell reads
  not-applicable (dash), and their attestation counts on their L2
  cell.
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
  `listOffendingUpstreamSteps`, `sRemediationHint`. Per-step L3
  entries additionally carry `listFailingCriteria` — the complete
  failing set behind the single dominant glyph, which the level-cell
  projection counts so a step failing every criterion reads "none",
  not a near-complete partial. Glyph tooltips
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
- Each step row has two clusters. LEFT: the execution cluster —
  the run checkbox (intent: include this step in the next run) and
  the run light (`_fsBuildStepStatusCell`, FACT and execution-only:
  queued / running / failed / over-budget; it must never fold in
  verification signals — that was the pre-2026-07 design and it
  made the light read as a shadow L1. A successful run renders a
  QUIET EMPTY cell: the vaibify check is reserved for attained
  level cells, because a success check beside an unverified step
  read as a false Level 1 claim — 2026-07-17 ruling; success
  detail lives in the expanded step's Last run line). RIGHT: the
  verification strip (`_fsBuildStepLevelStrip`) — the ⚠ warning
  column then L1|L2|L3 (no text in the level cells). One column-header row
  labels both clusters ("Run" on the left; ⚠/L1/L2/L3 on the
  right), every header with an explanatory title. Step rows carry
  no hover edit affordance — hand-editing steps is deliberately
  de-emphasized (right-click context menu remains the one manual
  entry point; agents edit via the action catalog).
  Each level is computed INDEPENDENTLY (no upward propagation) and
  arrives as a CELL dict (`sState`, `iSatisfied`, `iTotal`,
  `bRegression`). Visual vocabulary: grey filled circle =
  "not-started" (the step has no activity), red circle = "none"
  (activity, nothing satisfied), orange circle = "partial", the
  vaibify favicon image = "attained", hollow outlined grey circle =
  "unknown" (sync verify cache stale — a stale cache must NEVER
  render attained), muted dash = "not-applicable" (per-step L3 only:
  no criterion has a domain on the step — nothing to reproduce must
  NEVER render as a vacuous attained). First-attainment dates persist
  in `dictLevelHighWater` in state.json and are never erased.
- The ⚠ column is the SINGLE consolidated warnings surface for a
  step (2026-07-02 redesign — see "L1 blocker surfacing"). The
  backend still gates the LEVEL warning in `dictStepLevelWarnings`
  to the step's lowest non-attained level (a regression at a higher
  level is suppressed until lower levels pass) — never re-derive
  that gating client-side. The frontend composes that entry with the
  client-known staleness signals into the cell's multi-line tooltip;
  red ⚠ only when a genuine failure underlies it, orange ⚠ for
  staleness/regression.
- The Project row (`fsRenderProjectHeader`, labeled "Project"
  precisely so it does not read as a summary of the steps) is an
  expandable step-like row. Its cells are NOT an aggregate or summary of the
  step rows: they cover only the requirements that attach to the
  workflow as a whole (L1: project repo present; L2: sync-verify
  freshness + arXiv; L3: the envelope artifacts). The all-steps
  aggregate is the scalar `iAICSLevel` rendered by the AICS chip, so
  a Workflow-row L1 check above red step rows is a consistent
  display, and the cell tooltips say so. Collapsed the row shows the
  same columns at workflow scope; expanded it renders
  `dictWorkflowEnvelopeDetail` as four independently expandable
  sections — Software, Artifacts, Determinism, Syncs — each with a
  summary mark on its header. Passing items render the theme-tinted
  vaibify check (`envelope-check`, colored by `--highlight-color` so
  it climbs the ladder); failures render warning glyphs; a
  never-verified cache renders the hollow grey circle, never a
  passing mark. Repository status has ONE home — the Repos panel —
  and the Software section links there rather than duplicating it.
  The AI-declaration criterion is excluded from the header; its home
  is the AI Declaration interactive step (or the ghost row offering
  to add one).
- NO ✗/X status glyphs anywhere — failures and missing items use the
  red warning glyph ⚠; staleness uses orange ⚠ or (per-file) the
  pencil ✎. X-shaped characters are permitted only as close/delete
  BUTTON chrome. `axis-not-green` carries `sSubState`
  (failed/outputs-missing/outputs-changed/untested) mapped through
  `_DICT_AXIS_SUBSTATE_GLYPHS` — failed/missing carry red severity,
  outputs-changed orange, and untested maps to null (no warning
  line; the orange L1 partial cell carries "not yet done"). These metas
  now drive the consolidated ⚠ column's severity and tooltip lines,
  not inline banner glyphs. Per-file marks read
  `dictOffendingFileMarks` ("stale" → orange ✎, "failed"/"missing" →
  red ⚠) and render only in the expanded detail.
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
