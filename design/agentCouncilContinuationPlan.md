# Agent Council — continuation and deliverable: implementation plan

**Revision 3, 2026-08-26. This is the working document for the
implementing agent.** It synthesizes two independent cross-reviews of
revision 2 (one reviewer working from the document, one working from
the source) with a fresh verification pass over every claim either
review made. Where the reviews converged, this plan adopts the
convergent design; where either was wrong about the code, the
correction is recorded inline so it is not re-inherited.

**Epistemic convention.** Every load-bearing claim is **verified** (the
citing session ran the command or read the cited lines) or **asserted**
(believed, unchecked). Implementer: re-verify anything marked verified
that your work touches — line numbers drift, and two prior review
rounds each contained confident false claims about this same code.

**Researcher rulings (all dated 2026-08-26, all settled — do not
re-litigate):**

1. Continue from the last settled phase; re-run the phase that failed.
   Not a fresh round. The failed attempt is retired into the record,
   never erased.
2. A resumed council is the same campaign for provenance: same sealed
   snapshot, same charter, same participants, same identity. Never
   re-capture; never re-mint the id.
3. A resume that would change the execution image is **refused** — no
   override flag, no recorded-decision escape hatch. The remedy is a
   fresh council.
4. Repository export of the plan is **dropped** (see §10.1).

---

## 0. Evidence base and current state

### 0.1 The session that motivated this work

Five councils convened against a real multi-repository project on
2026-08-25/26; none reached an accepted plan. Established facts
(**verified** at the time from records and logs):

- A council takes 10–25 minutes to reach its first human gate. Four of
  five died after that investment: two turn failures, one external
  SIGKILL, one hub restart.
- A `needsHuman` campaign survives on disk and cannot be continued:
  `fiClassifyInterruptedCampaignsOnStartup` rewrites only `planning`
  campaigns, and `_fdictRequireContinuationRuntime`
  (`agentCouncilController.py:676`) refuses because the in-process
  runtime died with the hub. The panel shows a gate and an answer box,
  then refuses the answer.
- One campaign reached a 13-question gate with a candidate plan present
  and every turn `completed`; all of it was unreachable after the hub
  restart, and the plan was salvaged by hand outside the product.
- The listing carried no name, no timestamp, no directory: nine
  repositories, identical rows, `sorted(os.listdir())` order.

### 0.2 The branch is red

**Verified** by running `python -m pytest tests/testStyleInvariants.py
tests/testArchitecturalInvariants.py -q` → **4 failed, 101 passed**:

- Two `fFindLastWrittenEpoch` functions return a float and need the
  `ff` prefix (`agentCouncilStore.py`, both checkpoint classes).
- The style-inventory drift those two names cause
  (`python tools/generateStyleInventory.py --write` after renaming).
- `routes/councilRoutes.py: 1530 lines (allowed 1505)` — the module
  ratchet, forcing the chat/lifecycle split (§11 step 2).

### 0.3 Seven files carry uncommitted work, with three defects in it

The working diff already implements naming, the summary fields, the
stopping-point descriptor, and the listing UI (parts of §6). Three
defects in that diff, each **verified**, must be fixed before it lands:

1. **Every accepted campaign reads as resumable.**
   `fdictAcceptCampaignPlan` transitions `planAccepted` then
   immediately `awaitingImplementation` (`agentCouncil.py:872-873`), so
   the persisted state is never `planAccepted` — and
   `SET_TERMINAL_BY_CHOICE = {"planAccepted", "archived"}`
   (`agentCouncilResolution.py:476`) never matches an accepted
   campaign. Add `awaitingImplementation` to the set.
2. **`_fsFindFailedPhase` blames the wrong phase.** It scans phases in
   fixed order and returns the first holding any failed turn
   (`agentCouncilResolution.py:557`). A participant failing during
   proposals is *tolerated* (marked `bFailed`, dropped from
   `_flistActiveParticipants`, council continues); if synthesis later
   kills the campaign, the descriptor blames proposals. §2's attempt
   record supersedes this function entirely — the retry target is the
   attempt whose outcome is the terminating failure, never a
   phase-order inference. Until §2 lands, the descriptor must not be
   used to select anything.
3. **Name uniqueness and validation are misdescribed** (§6.2).

---

## 1. The scope contradiction in the specification

**Verified, and neither prior review had the whole picture.**
`design/agentCouncil.md` is internally contradictory about this exact
feature:

- §3 "Explicit non-goals" (`agentCouncil.md:306`): the first production
  version does not "promise automatic continuation across a hub crash."
- §7.5 (`agentCouncil.md:1204-1207`): "On restart the campaign is
  discoverable and **resumes from the last settled phase** into a
  recovered state the UI shows honestly."
- Acceptance question 41 (`agentCouncil.md:2876`) repeats §7.5's
  requirement.

So this plan is not a scope change needing permission — it implements
what §7.5 already requires and §3 disclaims. The amendment (§8) must
reconcile all three, and the reconciliation that preserves everyone's
intent is:

- **No unattended automatic relaunch**: paid provider work never
  restarts merely because the hub booted. (§3's non-goal, kept,
  clarified.)
- **The researcher may explicitly resume from a proven coherent
  boundary.** (§7.5's requirement, now implementable.)
- **Ambiguous work is never treated as completed.** (Q41's clause;
  §2 is the mechanism.)

The amendment lands in the same PR as §11 step 6, not before — the
spec should not promise the mechanism until the mechanism exists.

---

## 2. Durable phase attempts — the design core

### 2.1 Why the record cannot currently prove a phase finished

**Verified.** The phase key in `dictTurnsByPhase` is written by the
*first turn to settle*, not at phase end: `_fdictExecuteTurn` does
`setdefault(sPhase, []).append(...)` then checkpoints
(`agentCouncil.py:524`), and `_fsNextPhaseForRound` treats key presence
as completion (`agentCouncil.py:353`). A hub killed after turn 1 of a
five-participant cross-review leaves a durable record in which every
recorded turn is terminal and the phase key exists. A resume predicate
built on turn statuses calls that resumable and runs synthesis over one
review of five — silent corruption of the deliberation presented as
clean continuation. The live path is safe (the loop never consults
`_fsNextPhaseForRound` mid-phase); the hazard is *created* by resume.

Three further settlement paths break any naive "settled" marker, found
by two independent reads of the engine (**each verified**):

- **Veto settles outside `_fnSettlePhaseOutcome`**, which early-returns
  for it (`agentCouncil.py:423-424`); the round's `sResolution` is
  written later by `_fnResolveRoundTermination`, called only when the
  phase walk is exhausted (`agentCouncil.py:301`). The crash window
  between them is survivable only because veto classification
  (`_fdictClassifyVeto`, `agentCouncil.py:710-719`) is a pure function
  of durable turn records — a property that is currently accidental and
  unpinned.
- **A failed synthesis never reaches ordinary settlement.**
  `_fnRunSynthesisPhase` transitions FAILED inside the phase run
  (`agentCouncil.py:654-656`) and `_fnSettlePhaseOutcome` then
  early-returns on the non-planning state (`agentCouncil.py:415-416`).
- **An indeterminate turn abandons settlement midway**: the INTERRUPTED
  transition fires before that phase's questions are collected or
  deferred (`agentCouncil.py:418-422`), and the indeterminacy lives in
  `sCompletion`, not `sStatus`, so no turn-status scan can see it.
- **Synthesis has no fixed expected set**: it tries authors
  sequentially and stops at the first success (`agentCouncil.py:640`),
  so "every expected participant has a terminal turn" can never be true
  of a successful synthesis.

### 2.2 The attempt record

Both reviews converged on the same shape: the record must carry the
attempt's *outcome*, not merely the fact of settlement. Extend the
already-checkpointed `dictPhaseInFlight` machinery
(`agentCouncil.py:379`) into a durable attempt record on the round:

```
dictPhaseAttempt = {
    "sPhase": ...,
    "iRoundNumber": ...,
    "iAttemptNumber": ...,            # 1-based; rises on retry
    "listEligibleParticipantIds": [], # ordered; fixed BEFORE first launch
    "sCompletionRule": ...,           # "allEligible" | "firstAuthorOrExhaustion"
    "sAttemptState": ...,             # "running" | "turnsSettled" | "outcomeSettled"
    "sOutcome": "",                   # "" until outcomeSettled, then one of:
                                      #   "advancedToNextPhase"
                                      #   "gateOpened"
                                      #   "transitioned:<state>"
                                      #   "roundResolved"
    "dictPrePhaseState": {},          # round-derived fields as they stood
                                      # before this attempt (for retirement)
}
```

- `running` is written, with the eligible set and completion rule,
  before the first turn launches.
- `turnsSettled` is written when the completion rule is met:
  `allEligible` (proposal, cross-review, veto) — every eligible
  participant has a terminal turn; `firstAuthorOrExhaustion`
  (synthesis) — one eligible author completed, or every eligible author
  failed. This is what the researcher's "all agents completed the step"
  means, made checkable.
- `outcomeSettled` + `sOutcome` are written by settlement — see 2.3 for
  the atomicity requirement, which is where the naive version dies.

`_fsNextPhaseForRound` keeps its current logic for the live path; the
attempt record is what *recovery* reads. Two authorities do not compete
because only one is ever consulted per code path — pin that with the
same mirror-test pattern `LIST_FIRST_ROUND_PHASES` already uses.

### 2.3 Settlement must be atomic with the transition it decides

**Verified**: `_fnTransition` checkpoints immediately
(`agentCouncil.py:265-269`), and `_fnOpenQuestionGate` mutates gate
state that checkpoints with it. If `outcomeSettled` were written
*after* the transition, a crash between the two checkpoints leaves a
new ambiguous durable state — the exact defect class this design
removes, reintroduced one level down.

The rule: **mutate the attempt fields first, then make the call that
checkpoints.** The transition's own checkpoint then carries the settled
attempt atomically. Concretely, `_fnSettlePhaseOutcome` (and the veto
resolution path, and the synthesis failure path) set
`sAttemptState = "outcomeSettled"` and `sOutcome` on the in-memory
record *before* calling `_fnTransition` / `_fnOpenQuestionGate`, and
never checkpoint separately for the attempt alone. A crash before the
combined checkpoint leaves `turnsSettled`, which is recoverable (2.4);
a crash after leaves a fully settled attempt. There is no third state.

### 2.4 `turnsSettled` is recoverable by settlement replay

Revision 2 refused everything short of `outcomeSettled`. The
cross-review argued, and this plan accepts, that `turnsSettled` should
be recoverable: settlement is deterministic — question collection,
veto classification, and round resolution are functions of durable turn
records — so a record at `turnsSettled` is recovered by **replaying
settlement and atomically checkpointing the result**, then proceeding
as if the crash had not happened. This also honors the researcher's
ruling: all agents *did* complete the step.

Two obligations come with it:

- **Determinism must be pinned, not assumed.** A falsification (§9)
  drives settlement twice over the same turn records and asserts
  identical outcomes; another mutates a turn record between replays and
  asserts the outcome changes. If settlement ever grows a
  non-record input (a clock, a live connection), the replay claim
  becomes false and the test must catch it.
- **Recovery states, exhaustively:** `outcomeSettled` → act on
  `sOutcome`; `turnsSettled` → replay settlement; `running` →
  **permanently unresumable** (launched runners nobody proved gone;
  remedy `vaibify reconcile`, then retry as a recorded record
  mutation); **no attempt record** (checkpointed by a pre-feature hub)
  → permanently unresumable, saying so — never assumed settled.

### 2.5 Recovery actions are keyed by `sOutcome`, not by campaign state

| Last attempt | Action shown | What it does |
|---|---|---|
| `sOutcome: gateOpened` | **Answer** | gate is live; runtime rebuilt only when the answer is submitted |
| `sOutcome: advancedToNextPhase` / `roundResolved` | **Resume** | rebuild runtime, engine continues the walk |
| `sOutcome: transitioned:failed` | **Retry <phase>** | retire this attempt (2.6), re-run the phase |
| `sOutcome: transitioned:interrupted` | **Reconcile, then Retry** | prove runners gone first; the indeterminate turn's phase is re-run as a retirement |
| `turnsSettled` | **Resume** | replay settlement first (2.4), then act on the resulting outcome |
| `planReady` (campaign state) | **Review** | accept/reject; no provider turn, no runtime |

The retry target is **the last attempt** — the one whose outcome is the
terminating failure. This supersedes `_fsFindFailedPhase` (§0.3 defect
2) outright.

An `interrupted` outcome's abandoned questions: the INTERRUPTED
transition fires before question collection (2.1), so retirement of
that attempt must not pretend questions were handled. Re-running the
phase regenerates them. (**Asserted**: commit `36365e99` "recover
questions held for a gate that never opened" may already cover part of
this — the implementer must read it before writing the retirement
path.)

### 2.6 Retiring an attempt is a transaction over derived state

A phase settles more than turns. Enumerated by two independent reads
(**verified** against the round shape at `agentCouncil.py:311-334` and
the synthesis/veto code): `bSynthesisSettled`, `sSynthesisAuthorId`,
`bChairbotSubstituted`, `listFrozenVoterIds`, `listDeferredQuestions`,
`dictVetoVerdicts`, `listUnresolvedObjections`, `dictCandidatePlan`,
each participant's `bFailed`/`sFailureReason`, and the evidence ledger
entries the turns produced. Retirement restores all of it as one
checkpoint, from `dictPrePhaseState` captured at attempt start:

- The retired attempt moves to `listRetiredAttempts` on the round with
  its turns, and a researcher decision is appended to
  `listResearcherDecisions` naming phase, attempt number, and time. The
  record is the provenance artifact: a plan that reached consensus
  after three silent re-rolls is not the same artifact as one that
  reached it first time, and a reader must be able to tell.
- **Evidence entries are never deleted**: bind each ledger entry to
  `(iAttemptNumber, sPhase)` at write time, and retirement marks them
  retired — excluded from the active result, preserved as history.
- **Not every failure is retryable.** Whitelist retryable failure
  reasons (provider rate limit, turn timeout, transient transport);
  refuse the rest (invalid credential, missing image, output budget
  exceeded) with the reason named — those fail identically on re-run
  and spend the researcher's subscription doing it.

### 2.7 Crash-point tests the design must survive

Each is a falsification pair (§9), driven by writing the durable
record, not by mocking the predicate — the point is that the record
alone is sufficient:

1. After the last turn checkpoint, before settlement (`turnsSettled`
   replay path).
2. During a gate-opening transition (atomicity, 2.3: either the gate
   and the settled attempt both exist, or neither does).
3. After an indeterminate outcome (`transitioned:interrupted` path,
   including the abandoned-questions behavior).
4. During synthesis, after the first author fails and before the
   fallback runs (`firstAuthorOrExhaustion` rule: the attempt is
   `running`, refused).
5. Between veto turns settling and round resolution (`turnsSettled`
   replay reproduces the resolution; the twin mutates a veto turn
   record and asserts the replayed resolution differs).
6. One wave of a multi-wave phase completed, later participants never
   launched (`running`, refused — the original §2.1 hazard).

---

## 3. Durable provenance the store currently loses

**Verified, found by the cross-review, confirmed against source.**
`fdictReloadDurableCampaigns` (`agentCouncilStore.py:714`) rebuilds
each entry with `_fdictBuildEntry` (`agentCouncilStore.py:502`), which
creates a **fresh, empty `CouncilEvidenceLedger`** and
**`iTurnsLaunched: 0`**. After any hub restart:

- Confirmed claims in the campaign record cite ledger entries that no
  longer exist; the next entry re-mints `evidence-1`; the ledger byte
  budget resets. This contradicts the specification directly:
  §7.5 lists the evidence ledger as part of the durable campaign record
  (`agentCouncil.md:1195-1196`, **verified**).
- The turn counter's only-rises contract is broken on reload.

This is a defect *today* — it corrupts evidence provenance for any
campaign whose record is read after a restart, resume or no resume —
and it is a hard prerequisite for resume, which would otherwise mint
colliding evidence ids into a half-amnesiac store. Persist both beside
the campaign record (same directory, same write discipline), reload
them in `_fdictBuildEntry`'s reload path, and pin with falsifications:
a claim recorded before a store reload is readable after it; a turn id
minted after reload does not collide with one minted before.

---

## 4. Resume admission

### 4.1 Two layers, honestly separated

Revision 2 promised the listing and the route "can never disagree."
Too strong, and the cross-review's split is adopted:

- **`dictStoppingPoint`** (in the summary, computed from the durable
  record alone): campaign state, attempt state, outcome, retry
  classification, recorded image identity, and the **record-derived
  action** of 2.5. What it claims is exactly what the record supports.
- **Route-level admission** (checked at the click, authoritative):
  current image vs recorded, registry reservations, live-runtime and
  peer-hub activity, snapshot integrity, egress cleanup. These are
  dynamic; the listing may annotate them where cheap but never
  guarantees them.

A late dynamic refusal is legitimate. A listing that offers an action
the durable record cannot support is the defect to prevent — that is
the answer-box-over-a-dead-runtime bug generalized.

### 4.2 The route's refusals

Each refusal names its remedy; each is a falsification pair in §9.

1. **Any unsettled reservation for the campaign** — quarantined, live,
   pending, or peer-owned; not only quarantined. Remedy:
   `vaibify reconcile`.
2. **Image identity changed** (ruling 3). **Verified**:
   `sImageReference` is a route parameter
   (`agentCouncilController.py:555`) recorded nowhere;
   `LIST_PROJECT_IDENTITY_KEYS` has no image field. Pin the image
   identity into the identity triple at launch; compare at resume;
   refuse on difference, naming both identities.
3. **Snapshot archive fails validation.** **Verified**:
   `fbaReadSealedSnapshotArchive` (`agentCouncilContext.py:436`)
   returns bytes unchecked — and `sSnapshotSha256` is a *content*
   identity over sorted manifest rows (`agentCouncilContext.py:969`),
   **not** a tar-byte digest, so "hash the tar and compare" (revision
   2's wording) validates nothing. Either record an additional
   archive-byte digest at capture (cheap, preferred) or reparse the
   archive and recompute the content identity (slow, exact). Decide at
   implementation; record which in the manifest schema version.
4. **Attempt state refuses** per 2.4: `running`, or no attempt record.
5. **`bStopRequested` still set.** **Verified** against the engine
   loop: `fdictRunUntilBlocked` archives immediately on a set flag
   (`agentCouncil.py:292-294`), so resuming a record that kept the
   flag from a pre-crash stop request instantly archives the campaign
   the researcher just asked to continue. A researcher-requested stop
   was a decision about *that run*, not about the campaign forever:
   resume clears the flag as an explicit, recorded researcher decision
   — or refuses if the stop should stand. Surface the choice; never
   silently clear.

### 4.3 A failed resume must not transition the record

`fdictLaunchCampaignDeliberation` is deliberately transactional the
*other* way: a build fault moves `planning → failed` and checkpoints
before re-raising (`agentCouncilController.py:631-641`, **verified**).
Resume needs the opposite: if the runtime rebuild fails while resuming
a `needsHuman` campaign, the record is still `needsHuman` with its gate
intact when the error lands. Reusing the launch path's error handling
would destroy the exact 13-question gate this plan exists to rescue. A
resume either succeeds and transitions, or fails and leaves the record
byte-identical. Falsification: fail the rebuild, assert the record's
bytes.

### 4.4 Staleness is stated, not hidden

A council resumed days later reasons about a snapshot from days ago.
The record stays honest (identity is the hash), but the researcher will
read the plan as advice about their current tree.
`_fdictComputeBaselineStaleness` already exists on the get route; the
resumed campaign's panel and the rendered plan (§5) must both carry its
statement.

---

## 5. The deliverable

`fsComposePlanMarkdown` (`agentCouncilController.py:889`) renders the
real artifact — and the researcher has never seen it, because it runs
only on acceptance and no council reached acceptance. The frontend
carries a second composer (`_fsComposePlanBriefText`,
`scriptAgentCouncil.js:2543`) behind Copy and Download — a divergence
waiting to happen.

1. **Always available.** `GET
   /api/agent-councils/{id}/{campaign}/plan.md` renders the current
   candidate for any campaign that has one. A council that dies at a
   gate still yields its plan. No candidate → 404, never an empty
   document.
2. **One composer.** Delete the frontend brief; Copy and Download serve
   the server's bytes.
3. **Watermark drafts.** An unaccepted candidate says so in the
   document's own text — the file outlives the page it was downloaded
   from. Include the baseline-staleness statement (§4.4) when it
   applies.
4. **Route registration**: read-only, but it must still join the
   action catalog decision — council routes are human-only
   (`SET_INTENTIONALLY_EXCLUDED_PATHS`, `actionCatalog.py:847`) and
   this one should follow, with the agent-token lane rejected at the
   handler like its siblings.

Falsifications: candidate present → plan; absent → 404; route bytes ==
accepted `plan.md` bytes (one composer proven by identity); unaccepted
candidate's text carries the watermark.

---

## 6. Naming and listing

### 6.1 What the in-progress diff already does (keep)

`sCampaignName` (researcher-supplied, derived from the question when
blank), `fLastActivityEpoch` from the durable checkpoint's mtime,
`sProjectRepoPath` in the summary, `dictStoppingPoint`, the split
listing UI, and the surfaced listing refusal (a swallowed refusal used
to render as "(0)" — indistinguishable from a project that never
convened).

### 6.2 What must change before it lands (all **verified**)

- **Stop promising uniqueness the code does not enforce.** The start
  route reads existing names then creates; two concurrent starts both
  pass. Either enforce at the store's write (the honest place — the
  route-level scan cannot arbitrate) or describe the suffixing as
  best-effort disambiguation. `sCampaignId` remains the only identity
  either way. Decide; do not ship the current wording over the current
  behavior.
- **Stop claiming "same terms as a step name."** `fsValidateStepName`
  (`pipelineUtils.py:51`): 100 chars, strip-only, any allowed
  character first, at-least-one-alphanumeric. The campaign validator:
  80 chars, whitespace-collapse, must *start* alphanumeric. Three
  differences. Either reuse the step validator or declare the campaign
  contract as deliberately its own — the current text claims a sharing
  that does not exist, which is exactly the drift AGENTS.md's
  divergence-bug rule is about.
- **"Editable afterwards" is dropped** until a rename endpoint is
  specified (it would need a `listResearcherDecisions` entry, since
  the name appears in the rendered plan). Add it when a researcher
  asks.

### 6.3 The fan-out stays

The list route is directory-scoped (`councilRoutes.py:845`,
**verified**), so the client must name a directory before it can read a
summary; `sProjectRepoPath` in the summary removes the client-side
*map*, not the fan-out. Resource-scoping the listing would remove the
fan-out at the cost of crossing the repository scoping
`fbCampaignMatchesPrincipal` enforces. Keep the fan-out; stop
describing it as deletable machinery.

---

## 7. Responsiveness

Convene is already handled — `_ffnEnterConveningState` disables,
relabels, runs a clock, restores on failure, and documents why it does
not narrate server stages it cannot observe (**verified**;
`scriptAgentCouncil.js:1029`). The snapshot-progress banner proposed in
revision 1 stays withdrawn: a timer-driven claim of server progress is
a fabricated progress statement.

What is left: chat open (~10–30 s), the capabilities poll, accept.

- **State-driven, not DOM-driven.** The cross-review is right that a
  helper holding a DOM element fails when polling re-renders the button
  mid-request. Keep pending-action state in the module state object and
  derive `disabled` in the render path — this module re-renders
  constantly, so a captured element is a stale element
  (**asserted** for the specific re-render race; the render
  architecture makes it the safe default regardless).
- **Bind at the action layer** (`_fnPostAction`, `_fnPostChatAction`)
  so new buttons inherit the behavior.
- **Name the helper for what it returns.** If it executes the action
  and returns its result, it is `fgeneric...`, not `_ffn...` — `ffn`
  claims it returns a function.
- **Verify in the browser lane**: a real double-click suppressed; a
  refused action's button restored. A source-shape contract test
  proves neither.

---

## 8. The specification amendment

Lands with §11 step 6, reconciling §3, §7.5, and Q41 per §1. While
editing, sweep `agentCouncil.md` for any guarantee that was written
*because* campaigns were assumed unresumable — such a guarantee is now
false and will not announce itself. (**Asserted**: none found yet;
nobody has swept.)

---

## 9. The falsification program

**Zero council entries exist in `tests/falsificationRegistry.py`**
(**verified**: the single grep hit is an unrelated comment). Some forty
kill-confirmed council mutations exist only in session-local scripts.

**Materialize them first** (§11 step 1) — concrete node ids, exact
source anchors, `iExpectedOccurrences` — because entries pin exact
source text and every later step moves source. Ordering within the
work: entries land before the route split; the split then runs
`reconfirmFalsification.py`, and any entry whose anchor the split moved
is re-derived *in the split commit*, which is exactly the forcing
function working. (Revision 2 had this rationale but scheduled the
registration after two source-moving steps — a contradiction the
cross-review caught.)

Session-local scripts are not durable: if any of the forty cannot be
reconstructed from what is on disk, it is re-derived against current
source and marked as such — and every entry gets the registry's
independent-oracle audit either way; transcription does not exempt an
entry from the oracle rule.

New falsification pairs this plan adds (beyond the existing forty):
2.7's six crash points, 2.4's determinism pair, 3's ledger-survival
pair, 4.2's five refusal pairs (each with its resuming twin), 4.3's
byte-identity check, and §5's four. Every pair is symmetric — the
refusal and its twin — because a guard that only ever refuses is
indistinguishable from one that refuses everything.

---

## 10. Removed scope, recorded so it is not re-proposed

### 10.1 Repository export (researcher ruling, 2026-08-26)

Dropped, four reasons, first two anticipated by §7.3 of the design doc:
it breaks remote-safety (`agentCouncil.md:1093` — council artifacts are
reached only over HTTP precisely so the store's machine does not
matter; an export writes a file on a machine the researcher may not be
sitting at); it creates the unaccounted middle state §7.3 forbids
(tracked paths change the manifest/reproducibility envelope contracts,
which must move together); it would be the council's first repository
write while every council route is deliberately human-only; and the
HTTP read (§5) already delivers the entire researcher-facing value. If
wanted later: its own short design answering those four, plus path
validation, host-vs-container write semantics, overwrite/collision
behavior.

The `plan.json` sidecar goes with it — its only justification was the
implementation council's parser (§10.2), and building it now is
speculative reuse.

### 10.2 The implementation / review council

Its own design document, not a section here. It contradicts the
current spec (planning-only: `agentCouncil.md:113-115`; non-goal:
`agentCouncil.md:307`), needs campaign kinds, artifact lineage,
modified-state reconstruction, its own gates, resource bounds, and a
security review of runner-to-host artifact retrieval — the
turn-result schema is one blocker of six, not "the" blocker. A
**review-only** council over an externally supplied implementation is
the smaller first extension.

Three corrections to carry into that design so they are not
re-derived wrong:

- The snapshot and credential lanes do **not** share a carve-out:
  capture uses `container.get_archive` (a daemon API read,
  `agentCouncilContext.py:19`); credentials use the single
  `_ftRunTypedRead` exemption. Adding a named operation to
  `_DICT_TYPED_READ_PROGRAMS` is not a second grant point — the
  invariant pins the grant *method* — and `S_TYPED_READ_FILE_BASE64`
  may serve patch retrieval under a stated size bound.
- **But** council runners are reached through the council gateway's
  handle authority: retrieval must stay gateway-handle keyed, and
  driving an ordinary `DockerConnection` against a raw council
  container id would bypass the single-gateway authority
  (**asserted** — from the cross-review; trace
  `agentCouncilDockerGateway` before designing retrieval).
- A model-authored patch is not a change manifest: the server must
  observe the runner's before/after filesystem itself.
  `EvidenceDisciplineMixin._fnRecordModifiedStateClaim` already
  refuses confirmed claims lacking a reconstructable manifest — extend
  that discipline rather than building parallel machinery.

---

## 11. Implementation order

Each step names its done-criterion. Steps 1–4 were approved by both
reviews; 5–7 embody this document's design and need no further
rulings; nothing below step 1 merges while the branch is red.

1. **Materialize the falsification registry entries** (§9).
   *Done:* the council mutations are registry entries with node ids
   and anchors; `reconfirmFalsification.py` passes.
2. **Green the branch**: the two `ff` renames + inventory
   regeneration; split `councilRoutes.py` along the chat seam (the
   chat half shares only the guard helpers — a real fault line, and
   the ratchet forces it). Re-run the reconfirm harness; re-derive any
   entry the split moved, in the same commit.
   *Done:* style + architectural invariants green; reconfirm green.
3. **Fix the in-diff defects** (§0.3): the terminal set, the
   failed-phase misattribution (or its removal in favor of §2's
   attempt reading), the naming honesty items (§6.2). Land the diff.
   *Done:* full suite green; the three §0.3 falsifications kill.
4. **The deliverable** (§5). Smallest step, largest immediate
   researcher value, no rulings needed.
   *Done:* §5's falsifications kill; browser lane drives the buttons.
5. **Durable provenance restoration** (§3). Prerequisite for 6–7.
   *Done:* ledger and turn counter survive a store reload, pinned.
6. **Durable phase attempts** (§2) + the spec amendment (§8).
   *Done:* 2.7's six crash points and 2.4's determinism pair kill;
   the amendment reconciles §3/§7.5/Q41.
7. **Resume** (§4): recovery actions, two-layer eligibility, the five
   refusals, non-destructive failure, staleness statement.
   *Done:* §4's pairs kill; a live hub-restart-and-resume is driven
   end-to-end in the lane that can (E-gap: no test drives a hub
   restart across a live campaign today — this step's tests are that
   gap's fix).
8. **Responsiveness** (§7), browser-lane verified.
9. **Review-council design document** (§10.2) — design only.

---

## 12. Obligations on the implementing agent

- **Re-verify before relying.** Line numbers in this document were
  correct on 2026-08-26 against the uncommitted worktree; steps 2–3
  move them. The claims marked **asserted** (2.5's question-recovery
  overlap with `36365e99`, 7's re-render race, 8's sweep, 10.2's
  gateway-authority trace) are yours to settle before building on
  them.
- **Two prior reviews were each wrong somewhere** — one about the
  principal-resolution seam (already unified at
  `councilRoutes.py:241`), one about a spec line citation, revision 1
  about four code behaviors, revision 2 about the snapshot hash and
  the `turnsSettled` boundary. Check claims against source; do not
  inherit either this document's confidence or its predecessors'.
- **Falsify, don't confirm**: every guard ships with its symmetric
  pair, driven through the durable record or a real connection, keys
  distinct, per AGENTS.md epistemics. A green suite on stubs is not
  the claim.
- **Say what you did not verify.** If you cannot drive the browser
  lane or a live restart, name the unexecuted surface in the PR —
  silence reads as verification.
