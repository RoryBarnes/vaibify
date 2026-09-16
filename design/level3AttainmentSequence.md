# Making the Level 3 path honest: implementation guide

**Audience: an agent picking this up fresh.** This is the sequenced
work. Read this file, then the files each stage names. Do not
re-derive the decisions — the "Rulings" and "Facts already verified"
sections below settle them, and re-opening one costs a round trip to
the researcher.

**Where the work happens.** Branch `feat/l3-path-and-divergence-merge`
in the worktree `/Users/rory/src/vaibify-divergence`, rebased onto
`main` at `3bd6aa74`. One commit, `0ce609f0`, sits on top. Run the app
with `PYTHONPATH=/Users/rory/src/vaibify-divergence vaibify` — it
resolves ahead of the editable install.

Written 2026-09-15, after three adversarial review rounds. Companion
to [permanentArchivePromotion.md](permanentArchivePromotion.md) and
[permanentArchivePromotionImplementation.md](permanentArchivePromotionImplementation.md),
which own the archive-permanence half of this ladder and **must be
edited alongside Stage 5**.

Before touching anything, load the skills `CLAUDE.md` routes you to:
`reproducibility-lane` for the gates, `container-mutations` for the
carrier, `dashboard-state-honesty` for every row and poll payload.

---

## What you are building, in one paragraph

A researcher walking vaibify's ladder to PROOF Level 3 follows a
sequence, and vaibify has always known that sequence without saying
it. A "Do this next" arrow now says it — but the arrow is wrong in
three ways and silent in a fourth. A lock-satisfaction check that
should light the Dependency-lock row raises an `ImportError` on every
call, so the route 500s and three surfaces render "unknown"; the check
execs inside an HTTP GET with no carrier admission; its answer is
measured against the running container and reported as a fact about
the pinned image; its cache is never invalidated; the ordering graph
has no node for the environment archive, whose deposit re-pins the
manifest; and the scalar level gate enumerates its conjuncts by hand
and forgot one, so the header can outrank its own rows. This work
fixes all of that, adds confirmations to two level-crossing writes,
and deletes the hand-maintained list that made the header defect
possible.

---

## The ladder, for reference

Level 3 = Level 2 + readiness + a current rebuild attestation + the
published-artifact set.

**Level 1 (Self-Consistent).** Per step: the workflow lives in a git
project repo; every step is user-approved; every step is timing-clean;
every step's defined test categories are green. Script-staleness also
blocks when the caller supplies `dictScriptStatus`.

**Level 2 (Publication).** L1 plus: every canonical file matches the
GitHub mirror at a recently-verified SHA; every Zenodo-published file
matches at a known DOI; an attested AI Declaration step; AI models
declared; Personal AI Configuration **answered** (disclosure never
required — only silence fails). A recorded arXiv ID is checked; none
leaves the conjunct trivially true.

**Level 3 readiness** (`fbL3ReadinessOK`, cheap, every recompute):
project repo, manifest complete, dependency lock hashed, environment
snapshot pinned, Dockerfile pinned, reproduce.sh present and pinned,
determinism declared, binaries declared-or-waived.

**Level 3 attestation** (`fbL3AttestationCurrent`): the shadow rerun —
export the project, build a throwaway container **from the pinned
image**, re-run the whole pipeline, re-hash every pinned file. The only
multi-hour criterion, and the only one with a pre-flight.

**Level 3 published artifacts**: envelope matches the GitHub mirror;
envelope matches the Zenodo archive; a deposit covers the pinned
image; no archive is a known sandbox deposit; reproduce.sh is current;
and the archive carries an attestation covering its own manifest. A
registry copy is deliberately **not** a conjunct (ruled 2026-09-05):
Docker Hub and GHCR carry no preservation commitment.

Because Zenodo versions are immutable, Level 3 is a **release-time**
property: any envelope change drops it until a new version is
published.

### The ordering graph

`T_LEVEL3_ORDERING_EDGES`. `(A, B)` means *doing B first is work you
will do again*. The arrow renders only when exactly one root is live;
otherwise nothing, which is information, not a gap.

After Stage 5 the graph is:

```
dependencyLock ────┬──► rebuildAttestation
                   ├──► envelopeMirror
                   └──► envelopeArchive

environmentArchive ┬──► rebuildAttestation
                   ├──► envelopeMirror
                   └──► envelopeArchive

reproduceScript ─────► manifest        (the one non-undo edge)

manifest ──────────┬──► rebuildAttestation
                   ├──► envelopeMirror
                   └──► envelopeArchive

rebuildAttestation ──► envelopeArchive
```

Read as a walk, the closing sequence is:

1. Regenerate the envelope while the lock disagrees with the image —
   rewrites `requirements.lock` *and* the manifest.
2. Generate `reproduce.sh` — re-pins `MANIFEST.sha256` in the same
   action, so the script first settles both.
3. Deposit or promote the environment image archive — **re-pins the
   manifest**, so doing it after the rerun wastes the rerun.
4. Run the Level 3 verification.
5. Push the envelope + `.vaibify/l3_attestation.json`, then Verify now
   on the GitHub mirror row.
6. Publish a Zenodo version carrying the envelope **and** the
   attestation, then Verify now on the Zenodo row.

Steps 5 and 6 are independent — which is exactly when the arrow
disappears, and that is correct.

---

## Rulings — settled, do not re-litigate

1. ~~**The Dependency-lock row keeps its state on a mismatch.**~~
   **REVERSED 2026-09-15, on sight, by the researcher who made the
   original ruling.** It said the warning lives in an amber note and
   not in the row's color, because the row's criterion is about the
   *repository's envelope* (the lock is present and hashed, which is
   true) while a lock the container does not satisfy is a fact about
   the *container* — with `dictImageCurrency` as precedent.

   Rendered on a live project the combination read as nonsense: every
   applicable level showing a check, the "Do this next" arrow pointing
   at that row, and a note underneath saying a rerun would refuse
   before it starts. *"A row nothing can be done about is not green."*

   The row now carries the same conjunct the arrow does and resolves
   to **partial** — the artifact vocabulary already had that state,
   and it is the honest one, because the file IS present and IS
   hashed and what disagrees is the image. It is the POLICY conjunct,
   never the raw measurement, so `unknown` and a mismatch against a
   container nobody has shown to be the pin both keep the row green.
2. ~~**That row is a named single-row exception.**~~ **GONE with
   ruling 1.** The arrow and the rows now agree with no carve-out, and
   `test_no_row_diverges_from_the_arrow_at_all` states the invariant
   in that form.

   Worth keeping for the next reader: the original pair was reasoned
   from a real precedent and was internally consistent, and it still
   took one look at the rendered page to reject. A ruling about what a
   screen should say is not settled until somebody has seen the
   screen.
3. **Host projects are denied Level 3 by construction**, in the scalar
   gate — not incidentally by failing the published-artifact
   conjuncts.
4. **Both seeded level-crossings get a confirmation.**
   `declare-binary` and `declare-determinism` warn before rewriting
   `project.json`; `_I_UNWARNED_CROSSING_BUDGET` falls to 0.
5. **The lock check keeps asking the RUNNING container**, not the
   pinned image. Asking the pinned image means launching it, which is
   the cost this check exists to avoid, and the two are the same on
   any project whose snapshot is current.

   **Ruling 5 is what makes the running-vs-pinned wording a defect
   rather than a preference.** Keeping the cheap measurement means
   every claim built on it must shrink to fit: the flag is renamed,
   the wording says "the container you are working in", and the
   pre-flight blocks only when image currency proves the running
   container is the pinned one. The choice was never "measure the
   cheap thing and describe it as the expensive one".

---

## Facts already verified — do not re-research, do not contradict

Each was executed or read from the authority during three review
rounds, and each one killed a plausible-looking approach.

| Fact | Where | Consequence |
|---|---|---|
| `fdictParsePinnedVersions` lives in `declaredPackages.py`, **not** `dependencyPinning.py`; the import raises | `reproducibilityRoutes.py:208` | The readiness route 500s on every request (Stage 1) |
| The handler has no outer `try`; the import sits outside every `try` | same | "Never raises" in the docstring is false |
| `GET .../level3/readiness` is **`(awaiting)`** in the carrier audit; its `level3/envelope` neighbors are `mode-b-lock-held` | `tools/carrierIntentAudit.py` | A declaration is missing, not merely a ledger row (Stage 1) |
| The route execs **twice**: the lock probe, and `_fsRecordKindOrUndetermined` via `asyncio.to_thread` | `reproducibilityRoutes.py:352,386` | One carrier must cover both (Stage 1) |
| `MutationNotAdmittedError` derives from `ControlPlaneRefusalError(Exception)` | `mutationAdmission.py:94,126` | A bare `except Exception` converts a refusal into a scientific verdict (Stage 1) |
| `_flistNamePendingReadiness` treats a key as satisfied only when it is literally `true` | `scriptApplication.js:5053` | A tri-state cannot join `_DICT_L3_READINESS_LABELS` (Stage 1) |
| `fnForgetLockSatisfaction` has **zero callers** | `lockSatisfaction.py:120` | The cache has no invalidation at all (Stage 4) |
| `fdictJudgeOrderedRequirements` receives only `dictLockSatisfaction`; any mismatch marks it unsatisfied | `levelOrdering.py:158` | The arrow ignores image currency (Stage 2) |
| `fdictStampArchiveRecord` re-pins `MANIFEST.sha256` in the same breath as writing `environment.json` | `imageDeposit.py` docstring | Depositing after the rerun stales the attestation (Stage 5) |
| `flistDescribeSandboxArchives` classifies **two** records — the image archive and the primary Zenodo deposit | `levelGates.py:2874` | `fbNoArchiveIsKnownSandbox` cannot belong to one node (Stage 5) |
| `fdictArchivePermanenceState` already ships `sImageArchivePermanence` and `sProjectArchivePermanence` | `levelGates.py:2900` | The per-archive decomposition needs no new derivation (Stage 5) |
| The Environment archive row uses the image archive only; sandbox is a `sWarning` | `scriptWorkflowRequirements.js:941` | Row state must move with the node (Stage 5) |
| The Zenodo archive row goes green on envelope agreement alone (`if (bMatched)`) | `scriptWorkflowRequirements.js:1275` | Same (Stage 5) |
| `fdictAttestationPublicationState` is tri-state and its docstring forbids reading `None` as either | `levelGates.py:2967` | Never-compared must render orange, never red (Stage 5) |
| `fdictJudgeOrderedRequirements`' verdicts **must agree** with what the rows render | `levelOrdering.py:143` | Any new node changes a row too (Stage 5) |
| The agreement test covers only `manifest` and `reproduceScript` | `testTheNextStepIsNamedOnlyWhenOrderMatters.py:216` | Nothing would catch a new divergence (Stage 5) |
| `fbAttestationIsPubliclyArchived` is in the checks dict and the header tuple but **not** in `fbAtLeastLevel3` | `levelGates.py:1254,2831` | The header outranks its own row (Stage 7) |
| `fiProofLevel` calls `fbAtLeastLevel3` with **no** host-mode argument; `flistLevel3Blockers` early-returns `host-mode` | `levelGates.py:304,331,2710` | Host mode is denied only incidentally (Stage 7) |
| `_fdictL3WorkflowChecks` omits project-repo, manifest-complete and determinism (they are per-step) | `levelGates.py:2811` | `fbL3ReadinessOK` must stay, and stay first (Stage 7) |
| `_I_UNWARNED_CROSSING_BUDGET` is asserted for **equality**, not as a bound | `testAdvancingALevelNeverLowersOne.py:715` | A stale budget fails loudly (Stage 6) |
| `test_a_crossing_recorded_as_warned_really_warns` parses JS **source** for `bConfirm` | same file:665 | Source presence is not the guarantee (Stage 6) |
| `rerunVerification.py:118` says "pinned image" **correctly** — that is the shadow | `rerunVerification.py:118` | Do not "fix" that sentence (Stage 3) |

---

## Known wrong turns

Each was written into a draft of this plan and caught by review. They
are the shape of mistake this work invites.

1. **Moving the bad import into a `try` so it stops raising.** A wrong
   import path is a programming error; route modules are meant to fail
   at startup. Hiding it makes the next one silent too.
2. **Regenerating the three ledgers to clear the red tests.** That
   stamps `UNCLASSIFIED` on the missing-carrier row and converts a red
   test into a silent gap. Fix the carrier first.
3. **Testing the readiness route by asserting on its source text.**
   That is what let the `ImportError` ship: `inspect.getsource(...)`
   cannot observe whether a function runs. The same reflex reappeared
   in a later draft as "parse the conjuncts out of `fbAtLeastLevel3`".
4. **Writing one carrier test that asserts the gate refuses.** A
   correctly carried route must *succeed*. Refusal is a second,
   separate test.
5. **Shipping the lock tri-state as the checklist key.** It would list
   the lock as pending on every project, clean ones included.
6. **Blocking the verification on any running-container mismatch.** It
   falsely blocks a shadow whose pinned image is fine, and a clean
   running container falsely clears one the shadow will reject.
7. **Fixing the pre-flight and leaving the arrow.** They are two
   surfaces answering one question; two derivations is the duplication
   `lockSatisfaction.py` was extracted to end.
8. **Hand-invalidating the lock cache.** It fails silently the first
   time a new write path forgets to call it — which is how the feature
   arrived with zero callers.
9. **One `environmentArchive` node judged from `fbNoArchiveIsKnownSandbox`.**
   That gate covers two archives, so a sandbox *project deposit* would
   light the *Environment* row, whose button fixes the wrong archive.
10. **Spelling permanence as `== permanent`.** `unknown` must keep
    passing; the gate fails open in the researcher's favor by design.
11. **Reddening a never-compared archived attestation.** `None` means
    nobody looked. Orange, never red.
12. **Changing an ordering verdict without changing its row.** The
    arrow would point at a row the researcher sees as green — which is
    the defect being fixed, reintroduced one row over.

---

## Baseline: what is red before you start

A full run of both lanes on this branch, 2026-09-15:

```
8 failed, 11747 passed, 33 skipped   Python   45:56
3 failed,   361 passed,  1 skipped   browser  18:54
```

**Group 1 — the `ImportError`, 5 tests.** Cleared by Stage 1 and by
nothing else:

- `testReproducibilityRoutes::test_l3_readiness_returns_gap_dict`
- `testReproducibilityRoutes::test_readiness_resolves_the_docker_id_before_the_package_lookup`
- `browser/testHostProjectJourney::testAHostProjectOpensItsWorkflowWithoutAFailedRequest`
- `browser/testHostProjectJourney::testRunningAStepWritesARealFileAndTheDashboardSeesIt`
- `browser/testVerifyRefusesBeforeItWarns::test_an_unready_project_is_told_what_is_missing`

**Group 2 — ledger drift, 3 tests.** `testHostCapabilityInventory`,
`testMutationInventory`, `testMutationAttribution`. They name four new
rows: the `fnEnsureVaibifyGitignore` write, two `containerGit` merge
execs, and the `fdictCheckLockSatisfiedByContainer` exec. **Do not
regenerate until the end of Stage 1** — see wrong turn 2.

Note what this baseline proves: the guard written specifically for the
broken function is green, while five neighbors are red. The browser
lane caught a 500 on project open, which is exactly what it is for.

---

## Stages

Stages 1, 5, 6 and 7 are independent and can be done in any order.
Stages 2, 3 and 4 depend on Stage 1. Stages 2, 3 and 6 all edit
`_DICT_PROJECT_ACTIONS` / `scriptApplication.js` — batch them.

Each stage ends green:
`python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
(~48 min). A CI round trip is longer; batch small fixes.

### Stage 1 — Make the readiness route real

**Depends on:** nothing. The largest stage; Stages 2–4 wait on its
verdict actually arriving.

1. **Fix the import at module level**, alongside the existing
   `declaredPackages` import. Not inside a runtime guard — wrong turn 1.
2. **Declare the carrier and open one admission covering BOTH exec
   paths.** Stamp `mode-b-lock-held` with
   `routeScope.ffnDeclareCarrierMode`, matching the `level3/envelope`
   and `level3/dockerfile` neighbors, and open **one** mode-B worker
   over the whole exec-bearing computation — the lock probe *and*
   `_fsRecordKindOrUndetermined`. The declaration authorizes nothing;
   a forgotten admission still raises, and that refusal is the proof.

   The `container-mutations` skill's `asyncio.to_thread` caveat
   applies: a primitive bound into a thread loses its inventory row,
   because inside the worker the frames above it are executor
   infrastructure. The carrier MODE survives. Folding the probe into
   the worker as a direct call can *recover* the row — check whether
   it does before accepting a `passed-callable` row.
3. **Re-raise control-plane refusals before degrading anything.**
   Narrow the `except Exception` so `ControlPlaneRefusalError`
   propagates. Only ordinary operational errors — the exec failed, the
   container is gone — may become `unknown`.
4. **Ship two fields, not one.** Delete `bLockSatisfiedByImage`: it
   asserts what ruling 5 forbids and collapses `unknown` into `clean`.
   Replace with:
   - **the measurement** — the `dictLockSatisfaction` block, three-state
     (`clean` / `mismatch` / `unknown`), honestly named for the
     **running container**. Stage 3's note renders from it.
   - **a policy boolean** for the checklist, e.g.
     `bLockDoesNotBlockVerification`, computed on the backend from the
     measurement *plus* image currency per Stage 2.

   The checklist takes the boolean or becomes predicate-based. Do not
   hand it the measurement — wrong turn 5.
5. **Two carrier tests, asserting opposite things:**
   - **Positive** — the finished route **succeeds** under enforced
     admission, owner map keyed by a name ≠ the container id, and
     exercises both exec paths.
   - **Negative** — with the admission removed or bypassed, the same
     request is **refused**. This is the `testDeclaringMintsNoAdmission`
     property: the decorator alone grants nothing.
6. **Replace the source-text test with a request.** Drive
   `GET /level3/readiness` through the app and assert the lock verdict
   is present. **Keep** the `S_SHADOW_PIP_ENUMERATE_COMMAND` source
   assertion — it guards a different property (both callers enumerate
   identically) and source text is the right tool for that one.
7. **Then** regenerate the three ledgers and disposition the new rows.
   Read the `removed` keys; resolve per record, never wholesale.
8. Record the mutation in `tests/falsificationRegistry.py` and confirm
   with `python tools/reconfirmFalsification.py --only <substring>`.
   The mutation is **restoring the bad import path** — a boolean flip
   would survive, because the bug is an exception, not a wrong answer.

### Stage 2 — Gate the pre-flight on evidence about the pin

**Depends on:** Stage 1.

The shadow runs the **pinned image**. A verdict about the **running
container** may block it only when the two are known to be the same.

1. Refuse when the lock verdict is `mismatch` **and**
   `dictImageCurrency.bPinnedImageIsLive === true` — the only state in
   which the running container is evidence about the pin.
2. Otherwise the pinned-image answer is **unknown**: do not block. The
   shadow is authoritative and already refuses with a good message.
3. Add a `_DICT_L3_READINESS_LABELS` entry naming the cheap remedy
   first — the lock is usually older than the image, and Regenerate
   now rewrites it from what the image has. Rebuild second, with its
   cost: it downgrades the image to match the lock.
4. Do **not** add it to `_LIST_L3_CONTAINER_FACT_KEYS`. The
   switch-to-pinned-image remedy fixes a *built-vs-pinned* image; a
   stale lock on a correctly-pinned image is a different cause.
5. **Thread the same answer into the ordering judgment.** Pass image
   currency (or the policy boolean) into
   `fdictJudgeOrderedRequirements` and use **one** truth table for
   both surfaces — wrong turn 7.
6. **Test all four cases against BOTH surfaces**, because three must
   NOT block: clean; mismatch with the running container proven to be
   the pin (**the only blocking case**); mismatch with a different or
   undetermined image; unknown.
7. Browser lane: `pytest tests/browser -m browser`. The fake must
   answer the nested readiness payload, not a flat one — the flat fake
   is how the 2026-08-30 pre-flight bug hid.
8. **One browser assertion for the whole intended combination:** green
   structural Dependency-lock cell, visible amber mismatch note, and
   the "Do this next" arrow — arrow present *only* when the running
   image is proven to be the pin.

### Stage 3 — Strengthen the note; the row keeps its state

**Depends on:** Stage 1. Ruling 1.

1. Keep the note gated on `sState === "mismatch"`; `unknown` paints
   nothing.
2. **Fix the wording.** The note says *the container you are working
   in*, never *the pinned image*. Same correction to the
   `levelOrdering.py` edge reason. **Leave `rerunVerification.py:118`
   alone** — that is the shadow, which does run the pinned image.
3. Lead with the cheap remedy; name rebuilding second with its cost.
   One wording, not two: the note and the shadow refusal must not give
   different instructions for one cause.
4. State the consequence the row cannot show — a rerun **refuses
   before it starts** — and per Stage 2, only when the running
   container is the pinned one.
5. Pin the shared property with a test (cheap remedy named first),
   not the sentence.
6. **The named exception** (ruling 2). Write it into the
   `fdictJudgeOrderedRequirements` docstring *beside the sentence it
   qualifies*, and add a test asserting `dependencyLock` is the
   **only** row permitted to diverge from its rendered state. Drive it
   from the same row payload the agreement test uses. Without this the
   carve-out is indistinguishable from the bug, and the next agent
   reverts the ruling to "fix" it.

**Do NOT** add a criterion, a row state, or a conjunct to
`fbL3ReadinessOK`.

### Stage 4 — Give the cached verdict a fingerprint

**Depends on:** Stage 1.

1. **The fingerprint is the authority.** Store the lock digest and the
   running image identity beside `sState`; the poll compares them and
   downgrades to `unknown` when either moved, **without an exec**.
2. `fnForgetLockSatisfaction` calls on paths that rewrite
   `requirements.lock` are **defense in depth only** — wrong turn 8.
3. Test **both** components **with the forget call removed**, so the
   tests prove the fingerprint rather than the invalidation: a
   rewritten lock downgrades the answer, **and** a changed running-image
   identity does too. The second is what a lock-only fingerprint
   misses, and it is the case ruling 5 makes load-bearing.

### Stage 5 — Decompose the archive into per-archive nodes and rows

**Depends on:** nothing. The most invasive stage — it changes two row
states and two companion documents.

`fbNoArchiveIsKnownSandbox` is a **combined** criterion over two
archives, so it cannot belong to one node (wrong turn 9). The
per-archive verdicts already exist in the payload.

1. **`environmentArchive` node** — judged from `fbImageArchiveDeposited`
   and `sImageArchivePermanence` only. Unsatisfied exactly when *Make
   Permanent* on the Environment archive row is the right button.
2. **Widen the `envelopeArchive` node.** It is satisfied by envelope
   agreement alone today, so when Zenodo holds the envelope but no
   covering attestation, **Level 3 fails with no unsatisfied
   publication node anywhere** — the arrow goes silent exactly when it
   is needed. Judge from `fbEnvelopeMatchesZenodoArchive` **and**
   `fbAttestationIsPubliclyArchived` **and**
   `sProjectArchivePermanence`. All three have one remedy: publish a
   Zenodo version.
3. **Edges**, with reasons naming what gets redone:
   `environmentArchive → rebuildAttestation`, `→ envelopeMirror`,
   `→ envelopeArchive`.
4. **Take the combined criterion off `rebuildAttestation`.** Its row
   carries neither remedy. This is a **row-state change** on a row
   researchers have been reading — call it out in the PR.
5. **Move the conditions into the ROWS, in the same change** — wrong
   turn 12, and the invariant at `levelOrdering.py:143`:

   | Row | Green requires |
   |---|---|
   | Environment archive | matching image archive **and** image permanence ≠ sandbox |
   | Zenodo archive | matching envelope **and** covering archived attestation **and** project permanence ≠ sandbox |
   | Rebuild attestation | a current attestation only |

   **Permanence is `!= sandbox`, never `== permanent`** (wrong turn
   10), and **the archived-attestation condition is tri-state**:
   green on `True`, red on `False`, **orange on `None` — never red**
   (wrong turn 11). Browser test all three states.
6. **Update BOTH companion documents in the same PR.**
   `permanentArchivePromotion.md:420` places the combined criterion on
   the Attestation row with a paired test, and
   `permanentArchivePromotionImplementation.md:240` repeats the
   formula as `bL3AttestationCurrent && bNoArchiveKnownSandbox`.
   Leaving them stale means three documents disagreeing about one row.
7. **Test both asymmetric cases** — they are what the combined gate
   hides:
   - permanent image + sandbox project deposit → the arrow names
     `envelopeArchive`, never `environmentArchive`;
   - sandbox image + permanent project deposit → the reverse.

   A test with both archives in the same state passes against the
   broken design and proves nothing.
8. Re-check the single-root property. New nodes with three out-edges
   change when the arrow appears; the tests assert "one root or
   nothing", so extend them with the new shape rather than pinning a
   row.
9. Extend the row/arrow agreement test
   (`testTheNextStepIsNamedOnlyWhenOrderMatters.py:216`) to the new
   rows — it covers only `manifest` and `reproduceScript` today.
10. Reconcile against `permanentArchivePromotion.md`'s stated order —
    promote, verify, commit the attestation, publish.

### Stage 6 — Warn on the two seeded crossings

**Depends on:** nothing. Batch with Stages 2–3. Ruling 4.

1. Add a `dictConfirm` to `declare-binary` and `declare-determinism`
   in `_DICT_PROJECT_ACTIONS`, following the `declare-no-binaries`
   precedent three lines below `declare-binary`. The message names the
   cost: this rewrites the project definition Level 2 compares against
   GitHub and Zenodo, so the project sits below Level 2 until it is
   pushed and a new Zenodo version is published. Zenodo versions are
   immutable — which is why this earns a modal and a rename does not.
2. Flip both `DICT_ACCEPTED_CROSSINGS` entries to
   `S_DISPOSITION_WARNED` and rewrite their reasons. A reason still
   saying "the decision is the researcher's" outlives its own decision.
3. Lower `_I_UNWARNED_CROSSING_BUDGET` from 2 to **0** in the same
   commit — asserted for equality, not as a bound.
4. Add a **browser** assertion that each action opens the modal
   *before* sending its request. The existing test parses JS source
   for `bConfirm`; presence in source is not the guarantee.

**One cost to watch.** `declare-binary` is per-package, so declaring
four binaries means four modals. That is the honest price of the
ruling. If it grates, make the declaration form submit several
packages at once — do not drop the confirmation.

### Stage 7 — Delete the duplicate authority, and deny host mode

**Depends on:** nothing.

Two defects of one shape on one gate: `fbAtLeastLevel3` enumerates its
conjuncts by hand, so it forgot `fbAttestationIsPubliclyArchived`, and
it takes no host-mode argument at all.

1. **Remove the duplication** rather than parsing the list (wrong turn
   3):

   ```python
   if not fbAtLeastLevel2(dictWorkflow, filesRepo):
       return False
   if not fbL3ReadinessOK(dictWorkflow, filesRepo):
       return False
   return all(_fdictL3WorkflowChecks(dictWorkflow, filesRepo).values())
   ```

   A workflow criterion then enters the scalar gate **by
   construction**, and the hand-maintained list stops existing.

   `fbL3ReadinessOK` must stay, and stay **first**: the checks dict
   carries neither project-repo, manifest-complete nor determinism —
   those are per-step. Dropping it would silently widen Level 3. The
   overlap (dockerfile, lock, snapshot, reproduce-script in both) is
   harmless.
2. **Deny host projects by construction** (ruling 3). Thread the
   host-mode fact into `fbAtLeastLevel3`. `flistLevel3Blockers` takes
   `bHostProject` with **no default**, deliberately; follow that
   precedent rather than inventing one.
3. Keep a **behavioral** test proving an absent archived attestation
   denies Level 3, and one proving a host project is denied with every
   other conjunct satisfied — the state that today would attain it.
4. Record the mutations and confirm with
   `python tools/reconfirmFalsification.py`.

### Stage 8 — Land it

1. The base is `main`, so open the PR normally. A stacked branch gets
   an **empty check list, which is not a pass**.
2. `docs/lessons.md` gets one entry: **a test that asserts on a
   function's source text cannot observe whether the function runs.**
   Second time this repo has shipped a fault the suite was
   structurally unable to see — and a later draft of this very plan
   proposed the same mistake again, which is worth recording beside it.
3. Shepherd to green with the `monitor-pr` skill.

---

## Before you open the PR

- `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
- `python -m pytest tests/testArchitecturalInvariants.py -v` — Stage 1
  adds a route declaration and Stage 7 touches import graphs.
- `pip install -e '.[browser]' && python -m playwright install chromium`
  then `python -m pytest tests/browser -m browser`. **A green Python
  suite says nothing about the frontend**, and Stages 2, 3, 5 and 6 all
  change it. If you cannot load a browser, push and open the PR so the
  lane runs it — and if you cannot push, **say so and name the surface
  you did not verify**.
- `python tools/checkAmericanSpelling.py` — Stage 5 edits two docs.
- `python tools/reconfirmFalsification.py --completeness-only` before
  pushing marked tests.
- `PYTHONPATH=. python tools/carrierIntentAudit.py` — the readiness
  route must no longer read `(awaiting)`.
- `python tools/generateMutationInventory.py --check` must print `{}`.

## Still open — not blocking

Whether `.vaibify/l3_attestation.json` reaching GitHub deserves a
nudge on the Attestation row. No criterion gates it, deliberately,
because GitHub is not an archive. Reader-experience, not a ladder
question.

The `level2/readiness` and `level3/attestation` GETs are also
`(awaiting)` in the carrier audit. Neither execs today, so both are
out of scope here — but they are the remaining gap on this prefix.

---

## Landed after the seven stages (2026-09-15)

Two follow-ups, both found by running the finished ladder against a
real project rather than by reading it.

**The image inventory no longer inherits pip's blind spot.** `pip
list` and `pip freeze` hardcode a refusal to report three installed
distributions whose names shadow stdlib modules -- `argparse`,
`python`, `wsgiref`. The lock-satisfaction check enumerated an image
with `pip list --format=freeze`, so a project whose dependencies pull
the `argparse` backport was told its image failed its own lock: a
FALSE mismatch that no button could clear, blocking Level 3
permanently from both the readiness gate and the shadow's refusal.
The shared constant is now `S_ENUMERATE_PACKAGES_COMMAND` and
enumerates distributions through `importlib.metadata`. Same grammar,
same parser, same two callers, one constant. It needs Python 3.8 in
the image and refuses below that rather than falling back to the
filtered answer.

**The Project block holds its first paint until its verdict is in.**
The verdict costs a container exec, the poll may make none, so it is
measured by the open-time readiness GET, recorded server-side, and
carried back by a LATER poll -- and everything rendered in between is
derived from an answer nobody has. Opening a project painted green
with the arrow on Rebuild attestation, then corrected itself to amber
with the arrow on Artifacts. The block now renders a "this may take a
moment" notice, without its level strip, until the readiness answer
AND an ordered single poll (`VaibifyPolling.fnPollFileStatusOnce`)
have both landed. The open-time warm-up race listed as a third item is
closed by this and needs no work of its own: no other surface reads
the verdict off the poll.
