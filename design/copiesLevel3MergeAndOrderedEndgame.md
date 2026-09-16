# Copies/Envelope merge, the ordered endgame UI, and the readiness latency

Implementation plan agreed 2026-09-16, REVISED the same day after an
external review of v1 and of work item C's partial implementation.
Work lands on `feat/honest-level3-path` in the divergence worktree.
Nothing is committed or pushed without approval. Revised order:
**C-finish -> B -> A -> D**, with D as its own checkpoint.

## Rulings this plan encodes (researcher, 2026-09-16)

1. **One row per remote.** The "Published envelope" section duplicates
   the Published-copies rows by name. Its rows become the L3 cells OF
   the copies rows; the group banner aggregates both levels.
2. **`ai_provenance.json` is an L4 concern.** It gates NOTHING. Badge
   shown truthfully when stale; never a requirement.
3. **The attestation stays a Zenodo-L3 criterion only.** GitHub is
   not an archive; its badge there is informational.
4. **Ordering UI: grey with the circle-slash, never red.** Blocked
   rows still expand and still teach; every action control inside is
   inert, with the edge's reason visible.
5. **No blocking UI and no arrow until Level 2 is attained.** Below
   L2 a row's push/publish actions are the remedy and stay live.
6. **The arrow's readiness suppression is removed** (done). The L2
   half of the endgame gate survives; the readiness half fired
   exactly when ordering mattered most.
7. **Copies and envelope stay separate in code**; names say which
   level they speak for.

## Status: work item C backend is DONE

Landed and kill-confirmed (9/9 mutations, registry complete):

- `levelOrdering.fdictDescribeOrderedEndgame` returns the arrow AND
  the blocked map `{sRowKey: sReason}` from ONE judge pass; readiness
  gate removed; L2 gate empties both below Level 2.
- Payload: `dictBlockedRows` beside `dictNextOrderedStep`, assembled
  in `pipelineRoutes._fdictBuildWorkflowEnvelopeDetail` (v1 of this
  plan misnamed the module; the envelope payload lives there, not in
  reproducibilityRoutes).
- Frontend blocks from the map, not the arrow; expanded blocked rows
  show a muted "⊘ Do this later — <reason>" line.
- Tests: `testTheBlockedMapOutlivesTheArrow.py`,
  `test_readiness_never_silences_the_arrow` (readiness RAISES if
  consulted), registry re-anchors. Mutation-inventory reconciliation
  still pending.

## C-finish — structural blocking (the review's main finding)

`_fsRenderActionButton` honors `_sBlockedActionReason`, but the rows
the blocked map actually targets render their principal controls
inline: `wf-push-envelope` and `wf-verify-remote` in the envelope
rows' `fsDetail`, and more exist (regenerate variants, remote verify
on sync rows). Auditing renderers one by one is the approach that
already missed these, so the fix is STRUCTURAL — blocking is a
property of the ROW, enforced at one choke point per layer:

- `_fsRenderRequirementRow` adds `requirement-row-blocked` to the
  row container whenever `_fsReasonThisRowMustWait` answers.
- CSS: `.requirement-row-blocked .requirement-row-detail .btn` gets
  the disabled look, `cursor: not-allowed`, and a `::before` content
  "⊘ " glyph — the indicator survives on touch screens where the
  cursor does not exist. Per-button `disabled` via
  `_fsRenderActionButton` stays where it already works.
- The delegated click dispatcher refuses any button click inside
  `.requirement-row-blocked .requirement-row-detail` and surfaces
  the row's reason, so a control no renderer thought about is still
  inert. Expansion is untouched: the header is outside the detail.

Browser falsification test `tests/browser/testABlockedRowStillTeaches.py`
(ONE lease, every state in one test): render an envelope row under a
blocked map ->

- every `<button>` in its detail is inert (assert via click having
  no effect AND the structural class, not via a list of known
  button classes — the mutation to kill is "block only the
  `_fsRenderActionButton` path", which leaves `wf-verify-remote`
  live);
- the row still expands and its file badges and explanatory copy
  render;
- the reason line is visible;
- the same row WITHOUT the blocked entry has live buttons.

## Work item B — `ai_provenance.json` compared-not-required (backend + truthful UI)

Backend:

- Named constants in `publicationScope`:
  `S_ATTESTATION_REPO_PATH`, `S_AI_PROVENANCE_REPO_PATH`;
  `TUPLE_COMPARED_NOT_REQUIRED_PATHS` built from them. Replace BOTH
  `TUPLE_COMPARED_NOT_REQUIRED_PATHS[0]` usages
  (`levelGates.py:3084`, `pipelineRoutes.py:2529`) — positional
  indexing is what would break the day the tuple grows, which is
  this day.
- Bump `I_PUBLICATION_SCOPE_VERSION` to 6: a version-5 cache never
  compared the stamp, and absence must not read as agreement.

UI (the review is right that backend classification alone renders a
contradiction — a red per-file badge under a green L2 cell with no
explanation):

- Compared-not-required paths are EXCLUDED from the L2 and L3 badge
  lists in the copies rows' expansions and rendered in their own
  short "informational" block, stating that divergence here never
  lowers a PROOF level, with the stamp's remedy (it re-syncs on the
  next push / deposit version).
- The block's wording never says "not checked" when it WAS compared
  — "differs from the published copy; not a requirement" is the
  honest form.

Tests:

- Extend the `testAttainingALevelDoesNotLowerAnother.py` pattern: a
  diverged stamp leaves `_fbCachedSyncStatusFullMatch` True, the
  blocker list empty, and every L3 readiness flag unchanged. Kills:
  the complement rule sweeping the stamp into L2; adding it to the
  envelope tuple.
- Scope-version falsification, mirroring the registry's existing
  `5 -> 4` entry: a `6 -> 5` mutation must be killed by a test that
  a version-5 cache does not satisfy the gates once the scope
  includes the stamp. Kills: forgetting the bump.

## Work item A — merge the envelope rows into the Copies rows

Frontend:

- The Published-copies GitHub/Zenodo rows gain an L3 cell; the
  `publishedEnvelope` section is removed and its copy (including the
  order-matters paragraph) moves into the L3 half of the merged
  rows' expansions.
- **The L3 cell keeps the tri-state vocabulary** the envelope rows
  have today: met / partial ("MIXED", some files unproven) /
  unchecked-orange / diverged-red map onto the level-strip states.
  Flattening to a boolean would repaint "no verify has compared yet"
  as red, which is the unchecked-is-never-red violation.
- **One explicit alias map, defined beside the row builder**: the
  merged row answers to its sync identity AND to the ordering keys
  `envelopeMirror` / `envelopeArchive` — arrow target resolution,
  blocked-map lookup (`_fsReasonThisRowMustWait` checks
  `dictRow.sKey` today and must consult the aliases), DOM anchor,
  and expansion state all read the same map. Zenodo's L3 cell reads
  the same three conjuncts as the `envelopeArchive` ordering node.

Contracts that enforce the OLD structure are rewritten in the same
change, not discovered by the full suite:
`tests/browser/testTheEnvelopeMirrorRowRenders.py`,
`testAPartlyDivergedEnvelopeReadsPartial.py`,
`testUnprovenEnvelopeIsNotAnAlarm.py`,
`testTheArchiveRowsCarryTheirOwnConditions.py`,
`testASandboxDepositIsNamedOnTheRow.py`,
`tests/testDashboardColumnsFrontendContract.py`, plus any doc that
names the Published-envelope section (grep `publishedEnvelope` and
"Published envelope" over docs/ and skills).

Tests (strengthened per review):

- Browser falsification: seed a diverged envelope file -> the GitHub
  row's **L2 cell is still attained** AND its **L3 cell is unmet**,
  and the group banner is not green. Both assertions, because
  "banner not green" alone passes an implementation that wrongly
  lowers L2.
- The arrow's target for `envelopeArchive` resolves to the merged
  Zenodo row's DOM node through the alias map.

## Work item D — readiness latency and the paused probe (own checkpoint/commit)

Security contract first (review's point, adopted): typed reads are
FIXED programs keyed by name in `dockerConnection`'s table with one
argument slot — the new `S_TYPED_READ_REPO_SNAPSHOT` follows that
contract exactly. The connection NEVER accepts snapshot command text
from a caller; `repoFiles` asks for the named operation with
arguments (root, content paths, hash paths). The general-exec
snapshot path survives only for legacy test doubles.

- `ffilesSnapshotForWorkflow` builds `SnapshotRepoFiles` from the
  typed read; the readiness route snapshots BEFORE and outside
  `fdictProbeUnderOneAdmission` (the current placement is why the
  fix never ran); the poll rides the same read.
- Argument budget: the payload equals today's one-exec snapshot
  payload, but the badge-probe argv wall (~1,850 paths) is the
  standing lesson — measure the built command length in the parity
  test and refuse loudly past the documented bound rather than
  failing silently.
- Open-time ordering: the remote-refresh endpoint returns before its
  background work finishes, so "await remote refresh, then
  readiness" would NOT serialize the carrier. Order becomes: badge
  refresh -> readiness (carries the automatic lock-satisfaction
  read) -> THEN start remote refresh. Asserted by a browser
  request-order test, not by manual latency observation alone.
- Instrumentation lands first: the readiness timing line states
  whether the probe RAN or PAUSED and who held the carrier, so the
  "0.01s probe" inference becomes a measurement.

Tests: parity (typed result byte-equivalent to the exec snapshot
shape over a fixture repo), injection (filenames with quotes and
newlines), malformed/nonzero typed-read responses, paused-probe
fixture still gets snapshot-backed gates, plus the existing
`testThePollDoesNotRewriteWhatItReads.py` staying green.

## Verification discipline

Targeted tests only while the stream is open: each item's own files,
`reconfirmFalsification.py --include-local-diff --only <substring>`
for new marked tests, single browser files for JS. The full Python
suite and browser lane run ONCE at the end of the stream, before any
commit. Live walkthrough on the real project after C-finish+A and
again after D.

## Out of scope, deliberately

- Server-side enforcement of ordering (blocking is advisory UI).
- Any L4 machinery for `ai_provenance.json` beyond the truthful badge.
- Renaming persisted schema keys.
