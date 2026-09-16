# Promoting sandbox archives to permanent ones — design

Status: design settled, not yet implemented. Written 2026-09-14,
against `main` at `e3f4557b`.

**This file is the reasoning.** The sequenced work is in
[permanentArchivePromotionImplementation.md](permanentArchivePromotionImplementation.md);
start there and come back here when a decision looks arbitrary. The
Changelog at the end records what six review passes changed and which
earlier instructions were wrong — several of those are the natural
thing to do, which is why they are written down.

## The fact that shapes the whole feature

**Zenodo sandbox and Zenodo production are separate systems, and
there is no transfer between them.** `sandbox.zenodo.org` mints test
DOIs under the DataCite test prefix `10.5072/`, authenticates with its
own token (`zenodo_token_sandbox` versus `zenodo_token_production`,
both held in the *container* keyring), and — in Zenodo's own words —
may be cleaned at any time, under no preservation commitment.

So "Make Permanent" can only mean: **deposit again on zenodo.org under
a production token, and record the new version DOI.** The sandbox DOI
does not migrate and does not redirect; the sandbox record simply stays
where it is until Zenodo clears it, which vaibify neither causes nor
observes. Every part of this plan follows from
that, and the UI must never imply otherwise — a button labelled "Make
Permanent" that a researcher reads as "move my archive" would be the
same class of error as a row rendering a claim nobody earned.

## Two Zenodo vocabularies, and one site that confuses them

This trips up every reading of the code, so it is stated once here.

| Concept | Values | Where |
|---|---|---|
| **UI instance** | `"sandbox"` / `"production"` | the connect dialog's radios, and the keyring slot names (`zenodo_token_production`) |
| **Client service key** | `"sandbox"` / `"zenodo"` | `zenodoClient._SERVICES`, `sZenodoService` in the project definition and in the deposit record |

`syncDispatcher.fsZenodoInstanceToService` is the one translation
(`_DICT_ZENODO_INSTANCE_TO_SERVICE`). Anything comparing a *service
key* against `"production"` is wrong; anything comparing an *instance*
against `"production"` is right. See Part 6.

## What main already has (checked, not remembered)

Several pieces of this landed already, so the plan below is smaller
than it would have been a week ago:

- **The deposit record already carries its instance.**
  `imageArchive.fdictBuildArchiveRecord` takes `sZenodoService` and
  writes it into the record; both writers pass it —
  `imageDeposit` from `clientZenodo.sService`, and
  `environmentArchiveRoutes._fdictBuildReferencedArchiveRecord` from
  the instance the record was fetched from. Nothing to add here.
- **A DOI-prefix classifier already exists.**
  `zenodoClient.fsServiceForDoi` (exported), backed by
  `_S_SANDBOX_DOI_PREFIX = "10.5072/"`. It is two-state by design:
  anything not carrying the test prefix is production, because its
  caller must pick a host.
- **The recorded-wins-over-inferred precedent already exists.**
  `imageAcquisition._fsDepositService`: the record's own field is the
  authority, an unknown value REFUSES rather than being guessed
  around, and a record predating the field falls back to the prefix.
  The permanence classifier must match this shape rather than invent
  a second one.
- **`reproduce.sh` already reads the service** out of
  `environment.json` at run time
  (`reproduceScriptGenerator.fnLoadImageFromArchive`), so a promotion
  does **not** change `reproduce.sh` — but it does change
  `environment.json`. That matters; see Part 5.
- `_T_WORKFLOW_LEVEL3_CRITERIA` and `_fdictL3WorkflowChecks` are
  unchanged and still the four-places-at-once edit described in Part 4.
- A `clear-environment-archive-answer` action now exists beside the
  deposit one, with a `dictConfirm` block — the pattern the promotion
  buttons should follow.

What main does **not** have: any notion of *permanence* as distinct
from *which host*, any DOI on the Zenodo envelope row, and any
promotion path.

## Open items

**No decisions outstanding.** Ruling 7 was amended during review and
the amendment was accepted on 2026-09-14: a project may sit in a mixed
state — image archive permanent, project deposit still sandbox — with
no switch forcing them together, disclosed on the row rather than
eliminated by configuration. Ruling 7 below records the settled form.

One scope call was made rather than asked, and is easy to reverse:
**returning a promoted project to sandbox is supported**, through the
`start-new-concept` transition specified in Part 5. Without it a
promoted project is permanently locked to production and the
cross-instance refusal names a remedy that does not exist. If that is
more than this work should carry, the removal is bounded — delete the
route, its rung entry, its confirmation and its end-to-end test, and
make the refusal say plainly that returning to sandbox is unsupported
rather than pointing at a missing action.

The Zenodo policy question is closed: checked against Zenodo's
documentation on 2026-09-14 and written into Part 5. It turned up one
correction to shipped copy — the `deposit-environment-archive`
confirmation's "cannot be deleted" is false for the first 30 days —
which this work fixes rather than leaves.

## Rulings (converged 2026-09-14)

1. **Guided re-deposit.** Vaibify prompts for a production token when
   absent, re-runs the deposit against zenodo.org, and rewrites the
   record with the new version DOI.
2. **Sandbox is the only failing state.** Red/warning only when the
   deposit is *known* to be a test instance. The check fails open.
3. **Attestation red; the two L3 archive rows keep their computed
   state and gain an orange warning glyph.** A sandbox deposit really
   does hold matching bytes, so moving those rows' colour would be a
   false claim — the warning rides beside the state rather than
   replacing it. (Stated as "the rows go orange" when the ruling was
   taken; the detailed design below is what was meant, and the browser
   test asserts a green row carrying a warning.) Level 2 is untouched.
4. **Replace the record, keep a superseded note.**
5. **The L3 rerun still runs and still records.** Only the *credit* is
   withheld.
6. **Re-save from the local image; fall back to the sandbox copy.**
7. **Per-block buttons, and no shared service switch** (amended during
   review, accepted 2026-09-14; originally "one shared service
   switch"). Promoting the environment archive changes no project
   configuration at all — its deposit record carries its own
   `sZenodoService`, `reproduce.sh` reads the service from
   `environment.json` at run time, and the promotion builds its
   production client explicitly. Promoting the project deposit
   advances `dictRemotes.zenodo.sService` as sidecar bookkeeping on a
   successful publish, and never top-level `sZenodoService`.

   A mixed state — image permanent, project deposit still sandbox — is
   therefore reachable and legitimate, and the intent behind the
   original ruling is served by **naming** it on the row rather than by
   mutating configuration shared with every declared record. The
   eager flip was both unnecessary (the environment archive is already
   service-independent) and unsafe (one service is applied to every
   declared record, so a retained sandbox record would 404 and abort
   the whole Zenodo verify).

Ruling 5 is what keeps the design non-circular. Zenodo versions are
not freely editable after publication, so the production deposit
should carry the envelope *and* the
attestation together — if permanence of the project deposit blocked
the rerun, the attestation that deposit is supposed to carry could
never be produced. The honest order is therefore:

> promote the environment archive → Verify Level 3 → commit the
> attestation → publish the production project deposit carrying the
> envelope and the attestation together.

That is the order the Zenodo envelope row already documents, with the
promotion prepended.

---

## Part 1 — Classifying permanence

### New leaf module: `vaibify/reproducibility/archivePermanence.py`

Three surfaces need the same answer (the image archive row, the
envelope row, the attestation gate), and the domain has started naming
something the code has no representation for. That is the
un-homed-concept case in CLAUDE.md's "When to modularize" — the one
case to extract on the first instance.

```
S_PERMANENCE_PERMANENT = "permanent"
S_PERMANENCE_SANDBOX   = "sandbox"
S_PERMANENCE_UNKNOWN   = "unknown"

fsClassifyDeposit(sService, sDoi) -> one of the three
```

Resolution, in order:

1. **A recorded service that vaibify knows wins.** `"sandbox"` →
   sandbox, `"zenodo"` → permanent. A value that is neither is
   `unknown` — the permanence question has no business refusing, unlike
   the fetch path in `imageAcquisition._fsDepositService`, but it must
   not guess either.
2. **Otherwise, a recognized sandbox prefix means sandbox.** Reuse
   `zenodoClient.fsServiceForDoi`; do **not** introduce a second
   `10.5072/` constant.
3. **Otherwise, a well-formed production Zenodo DOI means permanent** —
   and nothing weaker does.
4. **Everything else is `unknown`.**

**Step 3 is the correction a review caught, and the reasoning matters.**
`fsServiceForDoi` is TOTAL by design: its caller must choose a host, so
it answers `"zenodo"` for every value that is not the test prefix —
including an empty string, a malformed DOI, and a foreign one. An
earlier draft of this plan deferred to it wholesale, which would have
turned "this string is not recognizably a sandbox DOI" into the
positive claim "this deposit is permanently archived". Since `unknown`
already passes every gate (ruling 2), abstaining costs a researcher
nothing and a false `permanent` costs them the warning this feature
exists to show.

That is not two authorities on one question. `fsServiceForDoi` answers
*which host do I fetch from*, which must be decided; `fsClassifyDeposit`
answers *what may vaibify claim*, which may be declined. Believing its
sandbox answer while requiring independent positive evidence for the
permanent one keeps one prefix constant and one DOI vocabulary.

**Where the DOI-shape predicate lives.** The only Zenodo-DOI parser
today is `workflowManager.fsZenodoRecordIdFromDoi` — in the GUI
package, which `imageAcquisition` already imports across the boundary.
`archivePermanence` is consumed by `levelGates` and `pipelineServer`,
so importing the GUI from it invites a cycle. Put the shape predicate
beside the Zenodo DOI vocabulary it belongs to, in `zenodoClient`
(which already owns `fsServiceForDoi` and `_S_SANDBOX_DOI_PREFIX`, and
stays container-safe as a pure regex helper), and have
`workflowManager.fsZenodoRecordIdFromDoi` delegate to it. A fourth copy
of the DOI grammar is the thing to avoid.

**Three states, never a boolean**, for the same reason
`dictRerunFailure` distinguishes `null` from `{}`. `unknown` renders
exactly as today — no glyph, no button, no message — and passes every
gate.

### Superseded records (ruling 4)

On a successful promotion the previous record moves to
`dictContainer.dictSupersededImageArchive` before the new one is
written. It is a note, never an input: no gate reads it, and
`flistDescribeArchiveMismatch` never consults it.

**It must be added to the envelope's carry-forward contract, or it
evaporates.** `environmentSnapshot`'s carry-forward preserves
`dictImageArchive` alone across a regeneration, and the envelope is
regenerated whenever a workflow crosses Level 1 — so a superseded note
written today is gone at the next ordinary regeneration, silently.
Extend that function to carry both records under the same
digest-and-architecture condition it already applies (when the capture
disagrees, both are dropped together: neither describes this envelope
any more), and test the carry explicitly.

**The project deposit needs the same note**, which the first draft of
this plan did not specify. `_fnPersistZenodoPublishRecord` overwrites
`sZenodoLatestDoi`, `sZenodoConceptDoi`, `sZenodoLatestUrl` and
`dictRemotes.zenodo` outright; the superseded sandbox identifiers go
under `dictRemotes.zenodo.dictSuperseded`, written by the promotion
path only.

`flistDescribeArchiveMismatch` must **not** gain a reason for a record
that lacks `sZenodoService` — records predating the field are legal and
classify by prefix. Making absence a mismatch would turn currently-green
rows red on upgrade, which is precisely what
`testAdvancingALevelNeverLowersOne.py` exists to catch.

---

## Part 2 — The Environment Archive block

### Payload

`pipelineServer.fdictBuildImageArchiveDetail` (`pipelineServer.py:2387`)
adds one key:

```
"sPermanence": archivePermanence.fsClassifyDeposit(
    dictRecord.get("sZenodoService"), dictRecord.get("sVersionDoi"))
```

computed only when a record exists; no record → `"unknown"`.

This is the backend's verdict, shipped whole. **JavaScript must not
re-derive it from the DOI string** — that is the mirrored-predicate
mistake the Reproducibility-rules row already made once, and a DOI
prefix is exactly the kind of thing that invites a regex in the
frontend.

### Rendering (`scriptWorkflowRequirements.js:870`)

`sState` is **unchanged**. A sandbox deposit that matches the envelope
is still `attained`; the bytes really do match and the comparison
really did pass. What is added, when `sPermanence === "sandbox"`:

- an orange ⚠ glyph beside the row title, tooltip **"Not a permanent
  archive"**;
- in the expanded detail, above the deposited-DOI field
  (`_fsRenderDepositedDoiRow`, line 1035): that this deposit is on
  `sandbox.zenodo.org`, that sandbox records carry no preservation
  commitment and may be cleared at any time, and that **the sandbox
  DOI cannot be moved — making it permanent means depositing the same
  image again on zenodo.org, under a new DOI**;
- a **Make Permanent** button.

`"unknown"` and `"permanent"` render exactly as today.

---

## Part 3 — The Published Envelope block

### The missing DOI (the researcher's second observation)

`_fdictProjectSyncSummary` (`pipelineRoutes.py:2744`) already reads the
snapshotted `syncStatus.json` and projects two useful fields away. Add
to the Zenodo summary:

```
"sZenodoDoiVerified":  dictStatus.get("sZenodoDoi")
"sEndpointVerified":   dictStatus.get("sEndpointVerified")
```

**and separately, from the workflow rather than the cache:**

```
"sProjectArchivePermanence": archivePermanence.fsClassifyDeposit(
    dictRemotes["zenodo"].get("sService"),
    dictRemotes["zenodo"].get("sDoi"))
```

The two must not share a key or a source, and the field names say which
moment each describes. `_fdictProjectSyncSummary` is handed only
`dictStatus` today, so the permanence verdict cannot be computed there:
compute it in `_fdictBuildWorkflowEnvelopeDetail`, which already holds
`dictWorkflow`, and leave the summary a pure projection of the cache.
Threading the workflow into the summary would invite exactly the
conflation this separation exists to prevent.

No new I/O — `_fdictBuildWorkflowEnvelopeDetail` is built with no extra
container execs and no network calls, and this stays inside that rule.

The Zenodo arm of `_fdictEnvelopeRemoteRow`
(`scriptWorkflowRequirements.js:1165`) gains a read-only, selectable
**Archived DOI** field mirroring `_fsRenderDepositedDoiRow`'s shape, so
the two archive blocks look and behave alike.

**The DOI shown is the one the verify compared against**, from the
cache — not `dictRemotes.zenodo.sDoi`. The row is a statement about
that comparison, so it must show the DOI that comparison used; with no
verify on record it shows no DOI rather than the workflow's
aspirational one.

**But permanence and actionability come from the CURRENT primary
record, not from that cache.** The two are different moments and they
come apart in an ordinary sequence: the post-archive verify is
deliberately best-effort — "a failed verify warns and never fails the
archive" — so a successful production promotion can leave a sandbox
verify cache in place. Driving the warning, the gate and the Make
Permanent button off the cache would then offer to promote a project
that is *already* on production, and the route would correctly refuse
what the screen had just invited. So: the displayed comparison DOI is
cached, `sPermanence` is computed from `dictRemotes.zenodo`, and the
test drives a production primary alongside an older sandbox cache.

### The warning and the button

Identical treatment to Part 2, driven by the same verdict: orange ⚠,
an explanation, and a **Make Permanent** button. The row's own
green/orange/red state, which summarizes the per-file badges, is
untouched.

---

## Part 4 — The Attestation block

### New Level 3 criterion

```
"an-archive-is-a-sandbox-deposit": fbNoArchiveIsKnownSandbox(
    dictWorkflow, filesRepo)
```

Returns False iff **either** the image deposit record **or** the
**current primary** Zenodo project deposit (`dictRemotes.zenodo`, not
the verify cache — see Part 3) classifies as `S_PERMANENCE_SANDBOX`.

**The name is the policy, and the first draft got it wrong**
(finding 4). It called the gate `fbProjectArchivesArePermanent` and had
the row say "attestations only count if both archives are permanent" —
while `unknown` passes. Under a fail-open policy that sentence is
simply false: a project with two unclassifiable deposits passes a gate
claiming both are permanent. Passing must never become a positive
permanence claim. The gate asserts the weaker, true thing — *no archive
is known to be a sandbox deposit* — and the row says that.

The three-state verdict is **preserved through the payload**, not
flattened at the gate: ship `sImageArchivePermanence` and
`sProjectArchivePermanence` alongside `bNoArchiveKnownSandbox`, so the
row can distinguish "both are permanent" from "nothing here is known to
be a sandbox" and say the accurate one. `flistDescribePermanenceIssues`
names which archive is the sandbox and what to do, because a refusal
that names its cause is the difference between a researcher fixing it
and a researcher opening a tab.

**Five** places must be edited together; missing any one is a known
failure shape:

1. `levelGates._fdictL3WorkflowChecks` (line 2728) — the check.
2. `levelGates._T_WORKFLOW_LEVEL3_CRITERIA` (line 3599) — the tuple.
   The comment directly above it says it in the code's own words: a
   criterion the gates emit but the tuple omits is silently dropped
   from the header count, so the cell can paint a check above an
   orange row.
3. **`levelGates.fbAtLeastLevel3` (line 1252) — the scalar gate.**
   Caught by review; the first draft of this plan omitted it. That
   function does not consume `_fdictL3WorkflowChecks`; it enumerates
   its conjuncts by hand (`fbAtLeastLevel2`, `fbL3ReadinessOK`,
   `fbL3AttestationCurrent`, `fbEnvelopeMatchesGithubMirror`,
   `fbEnvelopeMatchesZenodoArchive`, `fbImageArchiveDeposited`). Adding
   the criterion to 1 and 2 alone would produce the mirror image of the
   2026-08-30 header bug: the rows and the Project cell would block
   while the overall PROOF chip still reported Level 3 attained. This
   is the same "a level cell and the rows beneath it must fail on the
   same set" rule, read in the other direction.
4. The remediation-text dict beside the other L3 criteria.
5. `scriptApplication.js::_DICT_BLOCKER_CRITERION_GLYPHS` (near line
   2899) — icon, label, class.

The paired test is explicit about both halves: a sandbox archive makes
`fbAtLeastLevel3` return False **and** leaves `/level3/verify`
runnable, which is ruling 5 expressed as an assertion rather than a
sentence.

### The row

`bL3AttestationCurrent` **stays honest**: it keeps meaning "a rebuild
reran and reproduced the outputs against the current manifest", which
remains true over a sandbox archive. The rebuild happened.

**Superseded 2026-09-15 — the permanence conditions moved OFF the
attestation row and onto the two archive rows.** The formula below
was `bL3AttestationCurrent && bNoArchiveKnownSandbox`, and it was
wrong in a way only an asymmetric pair exposes: `fbNoArchiveIsKnownSandbox`
classifies **two** deposits, so a sandbox *project* deposit reddened
the *Attestation* row — whose only button re-runs a verification that
was never the problem — while a sandbox *image* deposit left the
*Environment archive* row green. Neither Make Permanent button lives
on the attestation row. The rows now read:

| Row | Green requires |
|---|---|
| Environment archive | a matching image archive **and** `sImageArchivePermanence != sandbox` |
| Zenodo archive | a matching envelope **and** a covering archived attestation **and** `sProjectArchivePermanence != sandbox` |
| Rebuild attestation | `bL3AttestationCurrent` only |

Permanence is spelled `!= sandbox`, never `== permanent`: the gate
passes on `unknown` by design and the row may not make a claim the
gate refuses to make. The archived-attestation condition is
**tri-state** — green on `True`, red on `False`, **orange on `None`**,
because `None` means no verify has looked and red is a claim about the
archive nobody earned.

The poll still ships `bNoArchiveKnownSandbox`, `listPermanenceIssues`
and the two per-archive verdicts, plus `dictArchivedAttestation`. Both
halves are backend verdicts; the frontend composes them and derives
neither. Where a row states the positive — that an archive *is*
permanent — it reads that archive's own verdict, never the combined
gate. The same decomposition applies to the "Do this next" arrow:
`levelOrdering` gained an `environmentArchive` node judged from
`fbImageArchiveDeposited` plus the image verdict alone, and its
`envelopeArchive` node carries the three Zenodo conjuncts, which share
one remedy — publish a Zenodo version.

The expanded block also states the order, because this is the one place
on the ladder where getting it wrong costs a published DOI that cannot
be recalled.

`reproducibilityRoutes`'s `/level3/verify` is **not** touched (ruling 5).

---

## Part 5 — The promotion lane

### New module: `vaibify/reproducibility/archivePromotion.py`

Policy and sequencing. It needs two seams that **do not exist yet**,
and the plan must name them rather than leaving an implementer to
duplicate or weaken the controls around them (finding 6):

- **A verified-download seam.** `ZenodoClient.fnDownloadFile` is
  unbounded and checks no hash. The hardened path is private:
  `imageAcquisition._fsDownloadVerifiedTarball` requires a DOI, a
  tarball name and a `sha256:` from the ENVELOPE's record (never the
  record page), requires a recorded size, refuses without room on
  disk, resolves the file URL through the origin allowlist, streams
  with hash verification, and cleans up. Promotion must call **that**,
  not `fnDownloadFile`. Promote it to a shared entry point rather than
  adding a second private cross-module reach — `imageAcquisition`
  already reaches into `imageDeposit._fnRefuseWithoutRoomOnDisk`, and
  a third such reach is how a control ends up with two divergent
  copies.
- **A deposit-an-existing-tarball seam.** `fdictDepositImageArchive`
  begins unconditionally with `ftSaveAndCompressImage`, so it cannot
  upload bytes that are already on disk. Split the save half from the
  upload/publish/record half so both the original deposit and the
  fallback path share the upload.

### Preflight, in this order, before any expensive work

**Policy before credentials, credentials before bytes.** The order
below is deliberate: every check that can refuse the request on
information vaibify already holds runs before the production token is
brought across the container boundary, and the token is validated
before anything expensive begins.

1. **Network access is permitted for this container.** Both promotion
   routes call `_fnRequireNetworkAccess`, the same guard every Zenodo
   publish route already runs. A network-isolated project is refused
   from data the hub already has.
2. **The envelope pins a digest and an architecture** — reuse
   `_fdictRequireEnvelopeContainerBlock` verbatim.
3. **The current record really is a sandbox deposit.** Promoting a
   `permanent` or `unknown` record is refused by name; the route must
   not rely on the button's absence.
4. **A production token exists and validates.** Last of the cheap
   checks and first of the ones that touch a secret: reading it means
   a typed read into the container keyring, so a request that was
   going to be refused anyway must never get this far. Still well
   before `docker save` — discovering a missing token after a
   multi-gigabyte save is the failure mode
   `_fdictRequireEnvelopeContainerBlock` exists to prevent. The
   refusal shape is designed, not assumed — see below.

### Byte source (ruling 6)

1. **`docker save` the image the envelope pins**, exactly as the
   original deposit did. This path is safe even if the record on file
   was mismatched, because it deposits *the envelope's* image and the
   resulting record is built from what was actually saved.
2. **Otherwise — and only when the image is positively absent from the
   daemon** — download the sandbox tarball through the
   verified-download seam and upload those bytes.

   The condition is an explicit image-presence probe, **not "the save
   failed"** (finding 6). A `docker save` can fail for a stopped
   daemon, a full disk, or a compression error, and falling back on
   any of those silently substitutes archived bytes for a local
   problem the researcher should be told about — and on a full disk
   the fallback needs the same space anyway.

   **This path additionally requires
   `flistDescribeArchiveMismatch(dictEnvironment) == []`** (finding 3).
   The fallback copies bytes whose only claim to identity is the record
   itself, so promoting a record that does not cover this envelope
   would spend a permanent DOI on a deposit that can never satisfy the
   gate. The local-image path needs no such check; the fallback cannot
   do without one.
3. **Otherwise refuse, naming both failures** — not on this daemon
   *and* the sandbox record could not be fetched or did not verify.
   Never a partial or silent degrade.

### Provenance is preserved, never upgraded

The first draft said provenance "stays `original`" on both paths. That
is wrong (finding 3). `fsJudgeDepositProvenance` deliberately
distinguishes `original` ("the archived environment IS the one that
produced these results") from `verified-equivalent` ("it reproduces
them"), and derives the difference from a *passing* attestation naming
a different image. A promotion re-deposits the same image, so it
establishes nothing new about that question: **carry the existing
record's `sProvenance` forward verbatim.** Recompute only when the
record carries none, and never let a promotion turn
`verified-equivalent` into `original` — that would be a scientific
claim manufactured by a bookkeeping operation.

### Token recovery is credential-only, and must not reuse the setup route

A missing production token currently becomes a generic
"Action failed:" toast, and `fnShowConnectionSetup` is module-private
and takes only a service.

**The obvious fix is the wrong one, and the first revision of this plan
walked into it.** Routing the refusal into the existing connection
dialog with `production` preselected would post to
`POST /api/sync/{id}/setup`, whose success path calls
`_fnPersistZenodoService` — which records the chosen instance into
`sZenodoService`. That is precisely the premature service flip that
Part 5's "On success" section rejects, arriving through the back door.
`_fnHandleSetupSave` then calls `fnOpenPushModal(sService)`
unconditionally, so it also would not resume the promotion; and
`dictRetryOnRefusal` can only re-issue the *same* action with extra
request fields, which a connection flow is not.

Three pieces, therefore:

- **A credential-only mode.** Either a `bCredentialOnly` flag on the
  setup route that skips `_fnPersistZenodoService`, or a dedicated
  route that stores and validates a named token slot and nothing else.
  Validation still runs — an unvalidated token is how a promotion
  fails halfway through an upload — but no project field moves.
- **A resume path.** The dialog resolves a promise (or invokes a
  success/cancel callback) that the promotion flow awaits, rather than
  opening the push modal. The public opener takes the instance to
  preselect and the continuation to run.
- **A test that both `sZenodoService` and `dictRemotes.zenodo` are
  unchanged after a successful production connection**, which is the
  assertion that would have caught this.

### On success

- Write the new record through the existing
  `_fdictStampArchiveRecord` path, which **re-pins the manifest in the
  same breath** (finding 6). `.vaibify/environment.json` is itself
  pinned in `MANIFEST.sha256`, so a writer that skips the re-pin makes
  promoting an image drop the project out of Level 3 — the exact
  failure that function's docstring was written to prevent. Do not
  compose a second writer beside it.
- Move the old record to `dictSupersededImageArchive` in the same
  read-modify-write.
- **The environment-archive promotion does NOT touch
  `sZenodoService`.** This is ruling 7 in its settled form, and the
  mechanism is why. `_fdictFetchZenodoHashes` reads ONE `sService` off
  `dictRemotes.zenodo` and applies it to the primary record *and every
  entry in `listRecords`*, and a fetch failure on any declared record
  aborts the whole verify. Flipping the project to production while
  sandbox records remain declared would therefore send those records
  to zenodo.org, 404 them, and break Zenodo verification entirely —
  for a promotion that had nothing to do with them.

  Nothing needs the flip: the deposit record carries its own
  `sZenodoService`, `reproduce.sh` reads the service out of
  `environment.json` at run time, and the promotion constructs its
  production client explicitly. The image archive is already
  service-independent.
- The row still **names the mixed state** when the project deposit is
  still on sandbox — that was the point of ruling 7, and it is served
  by saying so rather than by mutating shared configuration.

### The project deposit's promotion

**It is a new production release, not a copy of the sandbox archive.**
The two are different operations whenever a local file has changed
since the sandbox deposit, and the first draft left it ambiguous by
borrowing the introduction's "same bytes" (finding 3). Only the
*environment archive* copies bytes; the project deposit publishes the
**current canonical publication union** — the same set an ordinary
archive publishes, collected the same way, through the same
existence pre-flight and basename-collision refusal. The confirmation
must say so, because a researcher who expects a byte-copy and receives
a release of their current tree has been misled about what the DOI
names.

**All promotion-produced state must be bookkeeping** (finding 2). The
first draft said five keys "move together or not at all"; that
misreads the persistence architecture. `syncBookkeeping` exists exactly
to kill the treadmill this would recreate: `project.json` holds only
the researcher's DEFINITION, and every field a push, archive or verify
stamps lives in the uncompared `.vaibify/syncStatus.json` sidecar, so
the uploaded `project.json` can byte-match its archive forever.

- `dictRemotes.zenodo.sService` **is already** a produced field
  (`DICT_REMOTE_PRODUCED_FIELDS["zenodo"]`), so writing it is free of
  the treadmill. The four `sZenodo*` top-level keys are likewise
  bookkeeping.
- **Top-level `sZenodoService` is NOT**, and neither would
  `dictRemotes.zenodo.dictSuperseded` be. Writing either after the old
  bytes were uploaded makes the freshly minted production deposit —
  and the GitHub commit before it — immediately stale.

So the promotion writes **only** produced fields: it never touches
top-level `sZenodoService`, and `dictSuperseded` must be added to
`DICT_REMOTE_PRODUCED_FIELDS["zenodo"]` so it lands in the sidecar.

### Three service authorities, and they are different questions

The previous revision said to give the publish paths the same
produced-field-first precedence the verify uses. **That was wrong**, and
the reason is worth stating because it is the same mistake in a new
place: it overloads `dictRemotes.zenodo.sService`, which answers *where
the recorded deposit lives*, with *where the next publish should go*.
Those diverge the moment a researcher promotes and then wants to go
back to sandbox — and because `_fnPersistZenodoService` writes only the
top-level field, their instance selection would be silently ignored.

Three questions, three authorities, and no consumer may read the wrong
one:

| Question | Authority | Storage | Written by |
|---|---|---|---|
| Where does the recorded primary deposit live? | `dictRemotes.zenodo.sService` | sidecar (produced) | a successful publish, including promotion |
| Where should the next project publish go? | top-level `sZenodoService` | `project.json` (declaration) | the researcher, via the instance selector |
| Where does the environment deposit live? | the record's own `sZenodoService` | `environment.json` | the deposit that made it |

**Promotion changes only the first**, by publishing. It passes its
target as an explicit override and never writes a declaration on the
researcher's behalf; that is ruling 7 applied to one more field.

**The consumer inventory, which must be worked through rather than
assumed:**

- `_fbZenodoEndpointMatches` (verify) — asks question 1. Already reads
  produced-first. Correct as it stands.
- `_fsZenodoBadge`'s `sCurrentEndpoint` — asks question 1: "does the
  pushed copy match the archive on record". It is currently threaded
  from the top-level field, so after a promotion every Zenodo badge
  would read `drifted` against a project whose files were just
  published to production. Repoint it at the primary record's service.
- `_fsBuildZenodoDepositUrl` (Part 6) — asks question 1; produced-first
  is right there.
- `_ftPerformZenodoArchive` and `_fbArchiveZenodoForAutoArchive` — ask
  question 2. They must keep reading the top-level declaration, *not*
  the produced field, or the instance selector stops working.
- `environmentArchiveRoutes` — asks question 3 for a re-deposit and
  question 2 for a first one. It must not consult the project record's
  produced service at all; doing so would couple the two archives that
  ruling 7 deliberately decoupled.

**A guard the split makes necessary.** `_fbArchiveZenodoForAutoArchive`
reads the parent from `sZenodoDepositionId` (bookkeeping, and after a
promotion a *production* id) and the service from the top-level
declaration (still sandbox), so it would ask sandbox for a new version
of a record that does not exist there. Both publish paths must refuse
when a parent id exists and the primary record's service disagrees with
the declared target, naming the conflict: the recorded deposit is on
one instance, the project is set to publish to the other, and a new
version cannot cross. Guessing either way is how a researcher loses a
version.

**The remedy has to be built, because today there is none.** "Change
the target back" leaves the production parent id in place and hits the
same refusal; and "clear the deposit record" is not available —
`remove-zenodo-record` refuses the primary by design ("The primary
record is managed by the archive flow and cannot be removed here"), and
that refusal is correct: the primary is bookkeeping, not a declaration,
so a researcher must not be able to hand-edit it away.

So the refusal offers a **start a new concept on the selected
instance** transition. It is part of this work, not a follow-up: without
it a promoted project is permanently locked to production, and a
refusal pointing at a remedy that does not exist is the "a refusal
names its cause" rule failing in its worst direction — a researcher
follows the instruction, meets a 404, and concludes the product is
broken.

The transition is a confirmed, researcher-only action that clears the
primary record's deposit id, DOI, URL and service **in the sidecar** —
moving them into the superseded note rather than deleting them — so the
next publish creates a fresh concept on the declared instance. It is a
Level 2 ladder action, because it changes what the published-copies
rows compare against. Its confirmation names what is given up: the next
deposit will not be a version of the old one, and the old DOI keeps
resolving to exactly what it already holds. Its route is in the table
below, and its end-to-end test drives promote → declare sandbox →
refuse → start-new-concept → publish, asserting the new deposit is a
first version on sandbox and the old identifiers survive in the
superseded note.

### The level-ladder ledger: register the rungs, do not presume the crossings

`testAdvancingALevelNeverLowersOne.py` reads the dashboard's own ladder
action table, so every action this work adds **must** appear in
`DICT_LADDER_ACTION_RUNGS` or
`test_the_rung_ledger_covers_every_ladder_action` fails. That part is
unconditional, and it covers more than the two promotions: the recovery
actions (`reconcile`, `resume`, `adopt`, `discard`) and
`start-new-concept` are read from the same table. The promotions are
rung 3; the recovery actions inherit the rung of the promotion they
settle; `start-new-concept` is rung 2, because it changes what the
published-copies rows compare against.

**Do not pre-add any of them to `DICT_ACCEPTED_CROSSINGS`**, which an
earlier draft of this plan instructed. That dict is for actions the
crossing detector actually finds, and `test_..._stale` asserts
`set(DICT_ACCEPTED_CROSSINGS) - setCrossing == []` — so a recorded
judgement for an action that does not cross is itself a failure. On the
settled design none of them is expected to cross:

- **Environment promotion** writes `.vaibify/environment.json`, which
  is in `SET_CONTENT_GUARDED_ARTIFACTS` and is therefore skipped by
  `_flistFindCrossings` outright. That is why the existing
  `deposit-environment-archive` appears in the rung ledger and *not* in
  the crossings dict, and promotion is the same shape.
- **Project promotion, the recovery actions and `start-new-concept`**
  write only the uncompared `.vaibify/syncStatus.json` sidecar, which
  `_fiComparingLevelOf` scores 0.

The honest procedure is therefore: add the rung entries, run the test,
and let it tell you. None of these routes uses the general workflow
saver, so none is expected to cross; if the test reports one anyway,
record a judgement whose reason **describes what the scanner actually
saw** rather than asserting that published bytes diverge. A reason that
overstates is worse than none, because it reads as a review of a lane
nobody reviewed.

Every mutating action here also needs a real `dictConfirm` block in
`scriptApplication.js`'s action table — a product requirement
independent of the ledger, since each either spends a production
credential, mutates a remote deposit, or retires a published deposit's
place in the project. Follow the `deposit-environment-archive` entry's
shape.

**The confirmations say different things, and sharing one would be
wrong.** Environment promotion is a step on the way: it changes
`environment.json`, so the envelope must be pushed and the project
deposit published afterwards, and its confirmation should say so.
Project promotion **is** that final publication — telling the
researcher to push and republish after it would ask them to spend a
second production version for nothing. `start-new-concept` gives up a
version chain rather than creating one. Recovery's Resume and Discard
mutate a draft on a live archive; Adopt writes a published DOI into the
project.

**Wording is a correctness question here, not a copy question.**
Checked against Zenodo's own documentation on 2026-09-14 (*Manage
records*), so these are facts rather than caution:

- A published record **can be deleted by its owner within 30 days**;
  after that, deletion is restricted to copyright or personal-data
  cases. Deletion leaves a **tombstone page** carrying the citation.
- **Metadata stays editable at any time**, and editing does not affect
  the DOI. **File changes after publication require contacting
  support.**
- The sandbox may be cleaned **at any time**, under no preservation
  commitment.

Three consequences:

- Do not write "the sandbox DOI dies". The sandbox record persists
  until Zenodo clears it, and vaibify neither causes that nor observes
  it.
- Do not write that a production deposit "cannot be deleted". **That
  phrasing already ships**, in the `deposit-environment-archive`
  confirmation ("Zenodo deposits are PERMANENT and cannot be deleted"),
  and it is false for the first 30 days. Correcting it is part of this
  work, not a separate cleanup: a researcher who reads it and later
  needs to withdraw a mistaken deposit has been told they cannot.
- The defensible formulation describes what vaibify relies on:
  production Zenodo carries a preservation commitment and a persistent,
  tombstoned DOI; the sandbox carries neither. Where the confirmation
  needs to convey weight, "deleting it later requires contacting
  Zenodo, and the DOI is tombstoned rather than removed" is both
  stronger and true.

### Routes

| Route | Mode | `bAgentSafe` |
|---|---|---|
| `POST /api/workflow/{id}/environment-archive/promote` | `mode-b-lock-held`, `mode-c-durable` | `False` |
| `POST /api/workflow/{id}/zenodo/promote` | `mode-a-synchronous`, `mode-b-lock-held` | `False` |
| `GET /api/workflow/{id}/promotions/pending` | `typed-read` | `False` |
| `POST /api/workflow/{id}/promotions/{sPromotionId}/reconcile` | `mode-a-synchronous` | `False` |
| `POST /api/workflow/{id}/promotions/{sPromotionId}/resume` | `mode-b-lock-held`, `mode-c-durable` | `False` |
| `POST /api/workflow/{id}/promotions/{sPromotionId}/adopt` | `mode-a-synchronous`, `mode-b-lock-held` | `False` |
| `DELETE /api/workflow/{id}/promotions/{sPromotionId}` | `mode-a-synchronous` | `False` |
| `POST /api/workflow/{id}/zenodo/start-new-concept` | `mode-a-synchronous`, `mode-b-lock-held` | `False` |

`start-new-concept` is the remedy the cross-instance refusal names (see
Part 5). Sidecar-only, confirmed in the browser, Level 2 rung, and
researcher-only: it retires a published deposit's place in the project,
which is not a judgement an agent should make.

The recovery lane. `GET` lists pending records with their last observed
phase — the dashboard surfaces one on load, rather than relying on a
researcher having kept a failed request's toast. `reconcile` runs the
table above and returns the outcome; `resume` continues from wherever
the draft actually is; `adopt` restores a published record (project
lane through the sidecar seam, image lane through
`_fdictStampArchiveRecord` with the manifest re-pinned); `DELETE`
discards a draft the researcher has decided against, and refuses on a
`published` or `unknown` record — discarding either would throw away a
real DOI or a question nobody has answered.

Adoption validates on the promotion id **and the stored hashes**, never
filenames, and is idempotent: adopting a settled record reports the
same DOI and writes nothing.

**All of them** register in `LIST_AGENT_ACTIONS` with
`bAgentSafe: False`, recovery routes included. This is
a security decision, not documentation: promotion spends the
researcher's production credential and mints a DOI in a public
archive. An agent must not be able to do that, and "forgot to
register" now denies rather than silently admits.

---

## Part 6 — A pre-existing bug, and the fix is not the obvious one

`scriptSyncManager.js:1506` (`_fsBuildZenodoDepositUrl`) tests
`dictWorkflow.sZenodoService === "production"` — a **service key**
compared against the **instance** vocabulary. A production project is
therefore linked to `sandbox.zenodo.org/deposit/<id>`. It has never
mattered because no project has been on production; every project
promoted by this feature hits it immediately.

**Swapping the literal to `"zenodo"` is not enough**, and the first
draft said it was. Under this plan the promotion deliberately leaves
top-level `sZenodoService` alone and advances
`dictRemotes.zenodo.sService`, so a builder reading the top-level field
still resolves to sandbox after a successful promotion — a corrected
comparison against a stale authority.

The link asks **question 1** of Part 5's authority table — *where does
the recorded deposit live* — so it takes the same produced-first
resolution the verify uses:

```
dictRemotes.zenodo.sService  →  sZenodoService  →  "sandbox"
```

The frontend already receives `dictRemotes` in the merged workflow
dict, so this needs no new payload. Note what it must **not** copy: the
publish paths ask question 2 and keep reading the top-level
declaration. Verify, badge and link share one spelling; publish has its
own, deliberately.

Line 1276 in the same file is **correct** and must not be "fixed": it
reads the connect dialog's instance radio, where `"production"` is the
right vocabulary.

## Part 7 — Verification

The frontend carries real behaviour here, so a green Python suite says
nothing about it.

**Python**

- `tests/testArchivePermanence.py` — the classifier, with a
  kill-confirmed pair in each direction: a record whose *recorded
  service* says sandbox but whose DOI prefix looks like production
  classifies as sandbox, and the converse. Recorded evidence wins over
  inference, and a test that only drives agreeing inputs cannot see
  which one the code read.
- The fallback is `fsServiceForDoi` and not a private copy: assert by
  monkeypatching that function and watching the verdict follow it.
- `unknown` never warns and never blocks.
- A record with no `sZenodoService` gains no mismatch reason — the
  level-lowering guard for the upgrade path.
- `tests/testProjectHeaderNeverOutranksItsRows.py` extended for
  `an-archive-is-a-sandbox-deposit`, so the tuple omission is caught by the
  existing guard rather than by a researcher.
- **The scalar gate and the rows agree.** A sandbox archive makes
  `fbAtLeastLevel3` return False; the same fixture leaves
  `/level3/verify` runnable and still writing its attestation. Two
  assertions, because each one passes against a different wrong
  implementation.
- Provenance is preserved: promote a record whose `sProvenance` is
  `verified-equivalent` and assert the new record still says so. The
  natural test — promoting an `original` record — passes against the
  bug, since the upgraded value and the preserved value agree.
- The fallback refuses a mismatched record: drive it with the local
  image absent and an envelope the record does not cover, and assert
  nothing was uploaded.
- `environmentSnapshot`'s carry-forward preserves
  `dictSupersededImageArchive` across a regeneration — and drops it
  with the primary record when the digest or architecture disagrees.
- The project-deposit promotion refuses when **any** additional
  `listRecords` entry exists while the configured service is sandbox —
  including **an entry carrying no DOI**, which is the case a
  permanence-based refusal would let through.
- **Promotion writes no compared bytes it should not.** After a
  successful promotion, top-level `sZenodoService` is unchanged in
  `project.json`, and `dictRemotes.zenodo.sService` and
  `dictSuperseded` land in `.vaibify/syncStatus.json`. Assert on the
  serialized files, not the in-memory merged dict — the merge is what
  makes this bug invisible from a handler.
- **A production connection changes no project state.** Drive the
  credential-only path to success and assert `sZenodoService` and
  `dictRemotes.zenodo` are untouched.
- **Promotion writes no declaration.** After a successful promotion,
  top-level `sZenodoService` still holds whatever the researcher
  declared.
- **The fallback fires on absence, not on failure.** With the image
  present and `docker save` made to fail, assert nothing is
  downloaded and the error reaches the researcher.
- **Promotion leaves the manifest current**: `.vaibify/environment.json`
  is re-pinned, so the project does not drop a level by archiving.
- **Injected persistence failure.** Publish succeeds, then the sidecar
  write fails: assert the response carries the DOI and deposit id and
  names the adopt operation.
- **The deposit id is durable at draft creation.** Drop the process
  after the draft exists but before publication returns, reload, and
  assert the pending promotion is discoverable *with its deposit id* —
  not merely with its intent. Run it for both lanes; the container lane
  is the one where the id has to cross a process boundary to become
  durable.
- **Each reconciliation outcome is reachable and distinct.** Five
  fixtures — an empty draft, a partial upload, a complete unpublished
  draft, a published record, and a deposit read that fails — produce
  `resumable`, `resumable`, `publishable`, `published` and `unknown`,
  and each offers only the actions its row allows. A test asserting
  merely that reconciliation "succeeds" cannot tell four of these
  apart.
- **`unknown` keeps the record.** A failed deposit read must not
  discard or settle anything, and `DELETE` must refuse it.
- **Adoption matches on hashes, not filenames.** Offer a published
  record with the right basenames and different bytes and assert it is
  refused; adopting the right one twice is a no-op reporting the same
  DOI.
- **The image lane adopts into `environment.json`, with the manifest
  re-pinned** — and writes no project fields. The mirror assertion for
  the project lane: it writes no `environment.json`. One adoption path
  serving both would pass a test that only checked "the DOI is
  recorded somewhere".
- **Promotion does not touch `project.json`.** Assert its bytes are
  identical before and after a successful promotion — the sidecar-only
  claim, stated as an observation rather than an intention.
- **Adopt restores the PRIMARY record**, not a `listRecords` entry —
  including the case `fbDeclareZenodoRecord` refuses, where the
  in-memory primary already names the deposit.
- **Each consumer reads its own authority.** One fixture with
  `dictRemotes.zenodo.sService == "zenodo"` and top-level
  `sZenodoService == "sandbox"` — made to DISAGREE, or every assertion
  passes against the bug — drives all of them: the Repos-panel URL and
  the verify follow the produced field; the badges do not read
  `drifted`; an ordinary publish still targets the researcher's
  declared sandbox; and the instance selector, set back to sandbox
  after a promotion, is honoured.
- **The cross-instance parent is refused.** With a production deposit
  id on record and sandbox declared, both the manual and the
  auto-archive publish paths refuse by name rather than asking sandbox
  for a new version of a record it does not have.
- **The rung ledger covers both actions**, and whatever the crossing
  detector reports is what `DICT_ACCEPTED_CROSSINGS` holds — no
  pre-emptive entries.
- Persisted state is untouched by a failed or cancelled project-deposit
  promotion: drive a publish that fails and assert
  `sZenodoDepositionId`, the three DOI/URL keys, `dictRemotes.zenodo`
  and `sZenodoService` are all unchanged.
- `testAdvancingALevelNeverLowersOne.py` picks the new actions up
  automatically; the work is writing their ledger entries and the
  confirmations they claim.
- Preflight order: the token check refuses *before* `docker save` is
  reached — make the save raise if called.
- Byte-source fallback with the local image absent and a sandbox
  tarball whose sha256 *disagrees* with the record: must refuse, not
  upload.

**Browser lane** (`tests/browser/`)

- A sandbox record renders the ⚠ and the Make Permanent button on both
  blocks; `permanent` and `unknown` render neither.
- The attestation row is red while both archive rows stay green — the
  precise combination ruling 3 chose, and the one a mechanical
  "sandbox means red everywhere" implementation gets wrong.
- The Archived DOI field appears on the Zenodo envelope row and is
  selectable.
- No promote request leaves the page before the confirmation is
  accepted — assert the *order*, not the wording.
- With no production token stored, no deposit begins: the refusal
  surfaces the connection opener with `production` preselected, and the
  save starts only after the connection is made.
- **A pending promotion appears on reload.** With an unsettled record
  on disk, opening the project surfaces the recovery card without the
  researcher having kept a toast.
- **The card offers only the actions its outcome allows** — Resume and
  Discard on `resumable`, Adopt alone on `published`, neither on
  `mismatched` or `errored`, and no mutating action at all on
  `unknown`. Asserting the card merely *renders* cannot tell these
  apart.
- **Every mutating recovery action confirms first**, and no request
  leaves the page before the confirmation is accepted — the order, not
  the wording. Resume and Discard mutate a remote deposit; Adopt writes
  a published DOI into the project.
- **An `unknown` outcome leaves the card in place** across a reload,
  and its Discard is refused.
- **A settled promotion's card disappears** and does not return on the
  next poll.

**Falsification**

Entries for: recorded-service-wins precedence, the `unknown`-passes
branch, the parent-deposit-id clearing, and the tarball-hash check
before upload. Run `tools/reconfirmFalsification.py --only <substring>`
locally before pushing rather than learning it from CI.

**Ledgers**

`archivePromotion` should acquire `docker save` through `imageDeposit`'s
existing subprocess site rather than a new one. If that changes,
regenerate **every** generated ledger and diff the disposition count
before and after — a local rename re-fingerprints rows in more than one
of them.

---

## Open items deliberately left out of scope

- **Warning at first publish.** Nothing tells a researcher that the
  deposit they are about to make is a sandbox one. Adding a notice to
  the publish modal would prevent the situation this feature
  remediates, but it changes a flow every project uses and deserves its
  own decision.
- **Non-Zenodo permanent archives.** Ruling 2 fails open, so a Dryad or
  OSF DOI passes. Actually supporting one is a separate feature; the
  `referenced` path is Zenodo-only today.
- **Retracting a sandbox deposit** after promotion. The sandbox may be
  cleared at any time in any case, and deleting a published record is a
  destructive act vaibify should not perform on the researcher's
  behalf.

---

## Changelog

**2026-09-14, first draft.** Written against `feat/doctor-diagnostics`.

**2026-09-14, rebased onto `main` at `e3f4557b`.** Main had already
landed `sZenodoService` in the deposit record and both its writers, the
`fsServiceForDoi` prefix classifier, and the recorded-wins precedent in
`imageAcquisition._fsDepositService`; that work was removed from the
plan. Also corrected the `"production"` bug's scope — there are two
legitimate Zenodo vocabularies and only one site confuses them.

**2026-09-14, after external review.** Nine findings, all checked
against the source before acting. Seven changed the plan:

1. *(critical)* `fbAtLeastLevel3` enumerates its L3 conjuncts by hand
   and does not consume `_fdictL3WorkflowChecks`. Added as a fifth
   mandatory edit point; without it the rows would block while the
   PROOF chip still reported Level 3.
2. The classifier now **abstains** instead of deferring wholesale to
   `fsServiceForDoi`, which is total by design and would have turned a
   malformed or foreign DOI into a positive permanence claim.
3. *(critical)* The download fallback now requires the record to match
   the envelope, and provenance is **preserved, never upgraded** —
   `original` and `verified-equivalent` are distinct claims and a
   promotion establishes neither.
4. The project-deposit promotion passes service and parent as
   overrides and persists nothing until the publish succeeds; the
   persisted parent is `sZenodoDepositionId`. (The "five keys move
   together" formulation was itself corrected in the third pass —
   see finding 2 below.)
5. The environment-archive promotion no longer flips `sZenodoService`
   at all — it is shared with every declared record, and a flip would
   break Zenodo verification for retained sandbox records. Ruling 7
   amended accordingly (accepted 2026-09-14).
6. Named the two seams that do not exist yet: a verified-download seam
   (the safe path is private in `imageAcquisition`; the public
   `fnDownloadFile` is unbounded and unchecked) and a
   deposit-an-existing-tarball seam.
7. The superseded note must be added to the envelope carry-forward
   contract or it evaporates at the next regeneration; the project
   deposit needs an equivalent note, which was unspecified.
8. The token-recovery path is designed rather than asserted: a 409
   naming its follow-up through the existing refusal mechanism, a
   public instance-preselecting opener, and a browser test.
9. Confirmation wording softened, with the unverified Zenodo deletion
   policy flagged rather than propagated.

**2026-09-14, after second review.** Six findings, all checked against
the source; all six changed the plan.

1. *(critical)* The token-recovery path would have reintroduced the
   service flip through the back door: `POST /api/sync/{id}/setup`
   persists the chosen instance via `_fnPersistZenodoService`, and its
   success handler opens the push modal rather than resuming. Replaced
   with a credential-only mode plus an explicit resume, and a test that
   a successful connection moves no project state.
2. *(critical)* Promotion-produced state must be **bookkeeping**.
   `dictRemotes.zenodo.sService` already is; top-level `sZenodoService`
   and the proposed `dictSuperseded` are not, so writing them would
   rebuild the `project.json` treadmill `syncBookkeeping` exists to
   kill — staling the deposit that was just minted. The promotion now
   writes only produced fields, `dictSuperseded` joins
   `DICT_REMOTE_PRODUCED_FIELDS`, and the publish paths gain the
   produced-field-first precedence the verify already uses. Added the
   non-atomic-save recovery story for "published, but not recorded".
3. The project deposit publishes the **current canonical publication
   union** — a new production release — not a byte copy of the sandbox
   record. Only the environment archive copies bytes.
4. The gate is renamed to the policy it actually implements
   (`fbNoArchiveIsKnownSandbox`): with `unknown` passing, "both
   archives are permanent" was a false claim for a passing project. The
   three-state verdict is preserved through the payload instead of
   being flattened.
5. The extra-record refusal triggers on **any** additional declared
   record while the service is sandbox — they carry no service of their
   own, so a DOI-less entry would classify `unknown`, pass, and break
   verification.
6. Two contracts made explicit: reuse `_fdictStampArchiveRecord` so the
   manifest is re-pinned in the same breath, and call
   `_fnRequireNetworkAccess`. The download fallback now requires the
   image to be **positively absent**, not merely a failed `docker
   save`.

Also repaired a self-contradiction the review caught: the opening still
said "wiped periodically", "does not survive" and "immutable" after the
wording section had rejected exactly those formulations.

**Zenodo policy: unverified at this point in the plan's history**; see
the final entry, where it is checked and closed.

**2026-09-14, after third review.** Six findings, all checked; all six
changed the plan.

1. *(blocking)* `declare-zenodo-record` cannot recover an orphaned DOI:
   it appends to `listRecords`, which shares the one configured
   service, and `fbDeclareZenodoRecord` returns `False` without saving
   in precisely the failure being recovered from — the in-memory
   primary already names the deposit. Replaced with a dedicated **adopt
   a completed promotion** operation that restores the primary record,
   plus a **write-ahead record** so a hub that dies between Zenodo
   minting the DOI and the response returning does not lose it
   entirely.
2. Part 6's fix was a corrected comparison against a stale authority.
   Since promotion deliberately leaves top-level `sZenodoService`
   alone, the deposit-URL builder must use the same
   produced-field-first resolution as verify and publish — three
   readers of one question, spelled the same way.
3. The level-crossing section described the discarded design and told
   the implementer to pre-register both actions in
   `DICT_ACCEPTED_CROSSINGS` — which `test_..._stale` fails for an
   action that does not cross. Neither is expected to:
   `environment.json` is content-guarded and skipped outright, and the
   sidecar is uncompared. Now: register the rungs, run the test, record
   only what it finds, and describe the reason accurately. Also split
   the shared confirmation — project promotion *is* the final
   publication, so telling the researcher to republish afterwards would
   cost them a second production version.
4. Preflight reordered to policy → credentials → bytes, so a request
   that will be refused anyway never brings the production token across
   the container boundary.
5. Ruling 3's wording brought into line with the design it produced:
   the archive rows keep their computed state and gain a warning glyph;
   they do not turn orange.
6. Two remaining absolutes softened ("not editable after publication",
   "sandbox records are wiped anyway").

The Zenodo policy claim was still unverified at this point.

**2026-09-14, ruling 7 settled.** The mixed state is accepted: no
shared service switch, the environment archive touches no project
configuration, and the row names the mixed state rather than the
product preventing it. The plan carries no open decisions.

**2026-09-14, after fourth review.** Four findings, all checked; all
four changed the plan. The Zenodo policy question is closed.

1. *(blocking)* The write-ahead record could not survive the crash it
   existed for: the container archive script holds the draft id in a
   local and prints `ZENODO_RESULT=` only after publishing, and
   `fdictDepositImageArchive` returns its id the same way — so a kill
   after publication still lost it. Now two checkpoints, with the
   deposit id made durable **at draft creation**, and the pending
   record carrying exact hashes and the basename mapping. The adoption
   lane also gained its route-table entries, validation rule
   (promotion id plus hashes, never filenames) and idempotency.
2. *(blocking)* The previous revision overloaded
   `dictRemotes.zenodo.sService` with "where the next publish goes",
   which would have made the instance selector a silent no-op —
   `_fnPersistZenodoService` writes only the top-level field. Replaced
   with three named authorities, a consumer inventory (the badge
   endpoint and the auto-archive path were both reading the wrong one),
   and a refusal for the cross-instance parent that the split makes
   reachable.
3. Permanence and the Make Permanent button now derive from the current
   primary record, not the verify cache. The post-archive verify is
   best-effort, so a successful promotion can leave a sandbox cache in
   place and the screen would have offered a promotion the route
   refuses. The displayed comparison DOI still comes from the cache,
   which is the honest source for that one string.
4. Promotion persists through `syncBookkeeping.fnWriteSyncBookkeeping`
   alone rather than the general saver, which always writes both files.
   This also fixes the failure semantics: a succeeded sidecar write
   means the publication is recorded, so reporting failure there would
   invite an unnecessary second DOI.

**Zenodo policy, verified 2026-09-14** against Zenodo's *Manage
records* documentation: an owner may delete a published record within
30 days (afterwards only for copyright or personal-data reasons, with a
tombstone carrying the citation); metadata stays editable at any time
without affecting the DOI; file changes after publication require
contacting support. The plan's qualified wording stands, and one piece
of **already-shipped copy is wrong** — the `deposit-environment-archive`
confirmation's "Zenodo deposits are PERMANENT and cannot be deleted" —
which this work corrects.

**2026-09-14, after fifth review.** One blocker and three
contradictions, all checked; all four changed the plan.

1. *(blocking)* A persisted deposit id is not a publication. The
   previous revision called any unsettled record "exactly the orphan
   case", but a crash can leave an empty draft, a partial upload, a
   complete unpublished draft, or a published record — four situations
   wanting four different actions. Recovery is now an explicit state
   machine over a durable pending record, reconciled against Zenodo's
   authenticated deposit endpoint (`fdictGetDeposit`), with
   `resumable` / `publishable` / `published` / `mismatched` / `unknown`
   outcomes, `unknown` as a first-class keep-it state, and the record
   schema, settlement order, retention and idempotency specified. The
   **image lane adopts into `environment.json` with the manifest
   re-pinned**, never through the project sidecar — a gap the single
   shared adopt path had.
2. The Part 3 payload still derived permanence from the verify cache
   while the ruling below it said the current primary record. Split
   into `sZenodoDoiVerified` / `sEndpointVerified` (cache) and
   `sProjectArchivePermanence` (workflow), computed in
   `_fdictBuildWorkflowEnvelopeDetail` rather than threading the
   workflow into a summary that is deliberately a pure cache
   projection. Part 4's wording corrected to match.
3. "Clear the deposit record" is not an available remedy —
   `remove-zenodo-record` refuses the primary by design, correctly. The
   refusal now either offers a confirmed "start a new concept on the
   selected instance" transition (specified: sidecar-only, Level 2
   rung, moves the old identifiers to the superseded note) or states
   that returning to sandbox is unsupported. A refusal naming a remedy
   that does not exist is worse than one naming none.
4. Removed the instructions that contradicted the settled design: the
   test expecting an ordinary publish to follow the produced field, and
   the passages reasoning about `project.json` write failures during a
   promotion that writes no `project.json`.

**2026-09-14, after sixth review.** Five findings, all checked; all five
changed the plan. Two Zenodo facts were verified against the REST
documentation rather than assumed.

1. *(blocking)* The pending record had no home in the sidecar schema.
   Extraction and merge both walk a **closed** key set, so an ordinary
   workflow save would have rebuilt the section and erased the record
   the recovery lane depends on. It is now a named key in that contract
   (or a sibling section), with a narrow atomic update under the
   existing lock, and a UTC timestamp — a monotonic stamp is
   meaningless across the restart the record exists to survive.
2. *(blocking)* Resume had no guaranteed bytes: the image tarball's
   scratch directory is removed in a `finally` ("800 MB must not
   survive the operation that made it"), a hard kill leaves an orphan
   nothing sweeps, and project files can change. Now an explicit choice
   between a promotion-scoped snapshot with an orphan sweep, and
   re-creating every source and **refusing Resume on drift** — because
   resuming with today's bytes into yesterday's draft is a silent
   substitution inside a permanent archive.
3. The remote-verification contract is now exact. **Zenodo reports MD5
   for deposition files, not SHA-256** (verified), so the record
   carries MD5 and size alongside its SHA-256; basename mapping is part
   of the record because deposits are flat; and two outcomes were
   missing — `gone` (a positive 404, which is an answer) and `errored`
   (Zenodo's `state == "error"`, which must never be retried). The
   promotion id is stamped into the remote metadata and verified before
   Resume or Discard, so a writable local file is not the only thing
   binding a remote mutation to its intent.
4. "Re-pins the manifest in the same breath" overstated
   `_fdictStampArchiveRecord`: it writes `environment.json` first and
   `_fbRepinManifestOrWarn` returns `False` rather than raising. The
   image lane therefore has a real interior state, and the pending
   record now survives until `bManifestRefreshed` is true, with
   adoption idempotent across that boundary.
5. "Return to sandbox" is **decided: supported.** The plan previously
   both required the transition and allowed it to be dropped, while
   claiming no open decisions. `start-new-concept` is specified with
   its route, carrier mode, rung, confirmation and end-to-end test.
   Without it a promoted project is permanently locked to production.

Also added the recovery-UI browser tests, which the previous list did
not exercise at all, and fixed an editorial inversion ("Promotion
touches `project.json` at all" → "does not touch").
