# Promoting sandbox archives: implementation plan

**Audience: an agent picking this up fresh.** This is the sequenced
work. The *reasoning* — why each decision went the way it did, what was
measured, and which plausible alternatives are wrong — lives in
[permanentArchivePromotion.md](permanentArchivePromotion.md), referred
to below as **the design doc**. Read that file's "Rulings", "Traps you
will walk into" (the Known wrong turns section here), and the part
relevant to whichever stage you are on. When something below looks
arbitrary, the design doc says why; do not re-derive it.

Written 2026-09-14 against `main` at `e3f4557b`.

---

## What you are building, in one paragraph

Vaibify projects can publish to Zenodo's **sandbox**, which mints test
DOIs and carries no preservation commitment. Nothing currently tells a
researcher their archives are sandbox deposits, and nothing helps them
move to production. This work adds: a three-state permanence
classifier; an orange warning and a **Make Permanent** button on the
Environment Archive and Published Envelope blocks; a DOI display on the
envelope block (which has none today); a new Level 3 criterion so a
rebuild attestation does not count while an archive is a sandbox
deposit; two promotion lanes that re-deposit to production; and a
crash-recovery lane, because promotion mints permanent DOIs and a lost
one cannot be recovered by guessing.

**Sandbox and production are separate systems. Nothing transfers.**
Promotion means depositing again on zenodo.org under a production
token and recording the new DOI. Every design decision follows from
that.

---

## Facts already verified — do not re-research, do not contradict

These were checked against the source or against Zenodo's
documentation during design. Each one killed a plausible-looking
approach.

| Fact | Where | Consequence |
|---|---|---|
| Zenodo deposition file checksums are **MD5** | Zenodo REST docs, verified 2026-09-14 | The pending record carries MD5 + size, not only SHA-256 |
| Deposition `state` is `inprogress` / `done` / `error` | same | Reconciliation needs an `errored` outcome that is never retried |
| An owner may **delete a published record within 30 days**; metadata stays editable; file changes need support; deletion leaves a tombstone | Zenodo *Manage records*, verified 2026-09-14 | The shipped "cannot be deleted" copy is false — Stage 10 fixes it |
| `fbAtLeastLevel3` enumerates its L3 conjuncts **by hand** and does not consume `_fdictL3WorkflowChecks` | `levelGates.py:1252` | A new criterion needs **five** edits, not four (Stage 4) |
| `fsServiceForDoi` is **total**: anything not `10.5072/` answers `"zenodo"` | `zenodoClient.py:418` | Believe its sandbox answer only; require positive evidence for `permanent` (Stage 1) |
| `syncBookkeeping` extracts and merges a **closed** key set | `syncBookkeeping.py:61,95,127` | An unnamed sidecar key is erased by the next save (Stage 6) |
| `fnSaveWorkflowToContainer` writes the sidecar **then** `project.json` | `workflowManager.py:1567` | Promotion must use `fnWriteSyncBookkeeping` alone (Stage 6) |
| `_fnPersistZenodoService` writes **only** top-level `sZenodoService` | `syncRoutes.py:2594` | The instance selector breaks if the produced field becomes the publish authority (Stage 2) |
| `remove-zenodo-record` **refuses the primary record** by design | `syncRoutes.py:1099` | "Clear the deposit record" is not an available remedy (Stage 8) |
| `fbDeclareZenodoRecord` counts the **primary** as already declared | `workflowManager.py:506` | It cannot recover an orphaned DOI (Stage 9) |
| The image scratch directory is `rmtree`'d in a `finally` | `environmentArchiveRoutes.py:580` | Resume has no bytes unless you keep them (Stage 9) |
| `_fbRepinManifestOrWarn` returns `False` rather than raising | `environmentArchiveRoutes.py:355` | Image adoption has an interior state (Stage 9) |
| `ZenodoClient.fnDownloadFile` is unbounded and checks no hash | `zenodoClient.py:188` | Use the hardened private path instead (Stage 5) |
| `_fdictFetchZenodoHashes` applies **one** service to every declared record, and one failure aborts the verify | `scheduledReverify.py:333,382` | Any additional declared record blocks project promotion (Stage 8) |
| The container archive script prints `ZENODO_RESULT=` only after publishing | `syncDispatcher.py:527` | The deposit id must be made durable at draft creation (Stage 9) |
| `_flistFindCrossings` skips `SET_CONTENT_GUARDED_ARTIFACTS` | `testAdvancingALevelNeverLowersOne.py:465` | Do not pre-add crossing entries (Stage 10) |

---

## Known wrong turns

Each of these was written into a draft of the design doc and caught by
review. They are the shape of mistake this feature invites.

1. **Adding the L3 criterion to the checks dict and the tuple only.**
   The scalar gate would still report Level 3 attained while the rows
   blocked.
2. **Deferring permanence to `fsServiceForDoi`.** It turns a malformed
   or empty DOI into a positive "permanently archived" claim.
3. **Naming the gate `fbProjectArchivesArePermanent` while `unknown`
   passes.** Passing must never become a positive permanence claim.
4. **Making `dictRemotes.zenodo.sService` the publish target.** It
   already means "where the recorded deposit lives"; overloading it
   silently disables the instance selector.
5. **Flipping `sZenodoService` on promotion.** It lives in
   `project.json`, which Level 2 compares — you would stale the deposit
   you just minted. It is also shared with every declared record.
6. **Routing the missing-token refusal into the existing setup
   dialog.** Its success path persists the instance, reintroducing #5
   through the back door.
7. **Calling any unsettled promotion record an "orphaned DOI".** It may
   be an empty draft, a partial upload, an unpublished draft, or a
   published record — four different remedies.
8. **Adopting both lanes through one path.** The image record lives in
   `environment.json` and needs the manifest re-pinned; the project
   record lives in the sidecar.
9. **Upgrading provenance on promotion.** `original` and
   `verified-equivalent` are distinct scientific claims; a re-deposit
   establishes neither.
10. **Falling back to the archived tarball because `docker save`
    failed.** Fall back only when the image is *positively absent*.

---

## Stages

Stages 1, 2, 5 and 6 are independent and can be done in any order or in
parallel. Everything else has the dependencies listed.

Each stage ends green: run
`python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
before moving on. Batch small fixes — a suite run is ~20 minutes and a
CI round trip is longer.

---

### Stage 1 — The permanence classifier

**Depends on:** nothing.

**Add** `vaibify/reproducibility/archivePermanence.py`, a leaf module
(no `vaibify.gui` imports):

- `S_PERMANENCE_PERMANENT` / `_SANDBOX` / `_UNKNOWN`
- `fsClassifyDeposit(sService, sDoi)` resolving in this order:
  1. a recorded service vaibify knows (`"sandbox"` / `"zenodo"`) wins;
     any other value is `unknown`
  2. otherwise a recognized sandbox prefix (via
     `zenodoClient.fsServiceForDoi`) is `sandbox`
  3. otherwise a **well-formed production Zenodo DOI** is `permanent`
  4. otherwise `unknown`

**Move** the Zenodo-DOI shape predicate beside the rest of the Zenodo
DOI vocabulary in `zenodoClient` (container-safe: pure regex, no
vaibify imports) and have `workflowManager.fsZenodoRecordIdFromDoi`
delegate to it. Do not add a fourth copy of the grammar.

**Tests** — `tests/testArchivePermanence.py`:
- recorded service beats DOI prefix, **in both directions** (a sandbox
  service with a production-looking DOI, and the converse)
- monkeypatch `fsServiceForDoi` and assert the verdict follows it —
  proves the fallback is delegated, not copied
- empty, malformed and foreign DOIs with no recorded service → `unknown`
- a record with no `sZenodoService` gains **no** mismatch reason from
  `imageArchive.flistDescribeArchiveMismatch` (the upgrade path;
  otherwise every existing green row turns red)

**Done when:** the classifier abstains everywhere it has not been given
positive evidence, and no existing project's row changes state.

---

### Stage 2 — Three service authorities

**Depends on:** nothing. Fixes latent bugs independently of promotion.

The design doc's authority table is normative:

| Question | Authority | Storage |
|---|---|---|
| Where does the recorded primary deposit live? | `dictRemotes.zenodo.sService` | sidecar (produced) |
| Where should the next project publish go? | top-level `sZenodoService` | `project.json` (declaration) |
| Where does the environment deposit live? | the record's own `sZenodoService` | `environment.json` |

**Repoint** each consumer at its own authority:
- `_fsZenodoBadge`'s `sCurrentEndpoint` → question 1 (today it reads
  the declaration, which would turn every badge `drifted` after a
  promotion)
- `_fsBuildZenodoDepositUrl` (`scriptSyncManager.js:1506`) → question 1,
  resolved `dictRemotes.zenodo.sService → sZenodoService → "sandbox"`.
  **Do not** merely swap the `"production"` literal for `"zenodo"`;
  that corrects the vocabulary against a stale authority. Leave
  line 1276 alone — it reads the connect dialog's instance radio, where
  `"production"` is correct.
- `_ftPerformZenodoArchive` and `_fbArchiveZenodoForAutoArchive` →
  question 2, unchanged. They must keep reading the declaration.
- `environmentArchiveRoutes` → question 3 for a re-deposit, question 2
  for a first one; never the project record's produced service.

**Add** the cross-instance refusal to both publish paths: when a parent
deposit id exists and the primary record's service disagrees with the
declared target, refuse by name rather than asking one instance for a
new version of a record on the other.

**Tests:** one fixture with the two keys made to **disagree**
(`dictRemotes.zenodo.sService == "zenodo"`, top-level `"sandbox"`) —
otherwise every assertion passes against the bug — driving: the link
and the verify follow the produced field; badges do not read `drifted`;
an ordinary publish still targets the declared sandbox; the instance
selector still works; and the cross-instance parent is refused on both
the manual and auto-archive paths.

---

### Stage 3 — Surfacing permanence

**Depends on:** Stage 1.

**Payload.** `pipelineServer.fdictBuildImageArchiveDetail` adds
`"sPermanence"`, classified from the **deposit record**. In
`_fdictBuildWorkflowEnvelopeDetail`, add `sProjectArchivePermanence`
classified from `dictRemotes.zenodo`. Separately,
`_fdictProjectSyncSummary` adds `sZenodoDoiVerified` and
`sEndpointVerified` from the cache.

**The split is the point.** The displayed DOI is the one the verify
compared against; permanence and the button come from the **current
primary record**. The post-archive verify is best-effort, so a
successful promotion can leave a sandbox cache in place — driving the
button off the cache would offer a promotion the route then refuses.
Do not thread `dictWorkflow` into `_fdictProjectSyncSummary`; it is
deliberately a pure projection of the cache.

**Frontend.** Both blocks keep their computed row state and gain, when
permanence is `sandbox`: an orange ⚠ with tooltip "Not a permanent
archive", an explanation in the expanded detail, and a **Make
Permanent** button. `permanent` and `unknown` render exactly as today.
The Published Envelope block also gains a read-only, selectable
**Archived DOI** field mirroring `_fsRenderDepositedDoiRow`.

**Never re-derive permanence in JavaScript from the DOI string.**

**Tests:** browser lane — the glyph and button appear for `sandbox` and
for neither other state; the Archived DOI renders and is selectable;
and a fixture with a **production primary plus an older sandbox verify
cache** shows no Make Permanent button.

---

### Stage 4 — The Level 3 criterion

**Depends on:** Stages 1, 3.

Add `"an-archive-is-a-sandbox-deposit": fbNoArchiveIsKnownSandbox(...)`,
returning False iff the image deposit record **or** the current primary
project deposit classifies as `sandbox`. `unknown` passes.

**Five edits, and missing any one is a known failure:**
1. `levelGates._fdictL3WorkflowChecks`
2. `levelGates._T_WORKFLOW_LEVEL3_CRITERIA`
3. **`levelGates.fbAtLeastLevel3`** — the scalar gate, which enumerates
   its conjuncts by hand
4. the L3 remediation-text dict
5. `scriptApplication.js::_DICT_BLOCKER_CRITERION_GLYPHS`

**The row.** `bL3AttestationCurrent` stays honest — the rebuild did
happen. Ship `bNoArchiveKnownSandbox`, `listPermanenceIssues` and the
two per-archive verdicts; the row renders
`bL3AttestationCurrent && bNoArchiveKnownSandbox` and says "This
attestation does not count while an archive is a sandbox deposit",
naming which. Where the row wants to state the positive, it reads the
per-archive verdicts — never inferring it from the gate.

`/level3/verify` is **not** touched: the rerun still runs and still
records.

**Tests:** a sandbox archive makes `fbAtLeastLevel3` return False
**and** leaves `/level3/verify` runnable — two assertions, because each
passes against a different wrong implementation. Extend
`testProjectHeaderNeverOutranksItsRows.py` for the new criterion.

---

### Stage 5 — Two seams

**Depends on:** nothing.

**Verified-download seam.** Promote
`imageAcquisition._fsDownloadVerifiedTarball` to a shared entry point.
It requires DOI, tarball name and `sha256:` from the envelope's record
(never the record page), requires a recorded size, refuses without disk
room, resolves the URL through the origin allowlist, streams with hash
verification, and cleans up. **Do not** reach for
`ZenodoClient.fnDownloadFile`, which is unbounded and unchecked.

**Deposit-existing-tarball seam.** `fdictDepositImageArchive` begins
unconditionally with `ftSaveAndCompressImage`. Split the save half from
the upload/publish/record half so both the original deposit and the
promotion fallback share the upload.

**Tests:** the existing deposit path is unchanged; the upload half can
be driven against a tarball already on disk.

---

### Stage 6 — The pending-promotion record

**Depends on:** nothing.

**Give it a home in the sidecar schema.** Add `listPendingPromotions`
to `T_BOOKKEEPING_TOP_KEYS` (or give it a sibling section in
`syncStatus.json` outside the workflow round-trip). A key the
extract/merge contract does not know is erased by the next ordinary
save.

**Record fields:** lane (`image` / `project`), target service, parent
id, the file set with **SHA-256, MD5 and size** plus basename mapping,
deposit id once a draft exists, phase, promotion id, and a **UTC ISO
timestamp** — not monotonic, which is meaningless across the restart
this record exists to survive.

**Add a narrow atomic update** that mutates one record under the
existing per-file lock without serializing the whole workflow, so a
settlement cannot be lost to a concurrent save and a save cannot revert
a settlement.

**Persistence seam:** `syncBookkeeping.fnWriteSyncBookkeeping` only.
Never `fnSaveWorkflowToContainer`.

**Tests:** a pending record survives an ordinary workflow save;
concurrent save and settlement do not lose each other.

---

### Stage 7 — Environment-archive promotion

**Depends on:** Stages 1, 5, 6.

**Preflight, in this order — policy before credentials, credentials
before bytes:**
1. `_fnRequireNetworkAccess`
2. `_fdictRequireEnvelopeContainerBlock` (digest **and** architecture)
3. the record really is a sandbox deposit
4. a production token exists and validates

**Byte source:**
1. `docker save` the image the envelope pins
2. otherwise — **and only when the image is positively absent from the
   daemon** — download the sandbox tarball through the Stage 5 seam.
   This path additionally requires
   `flistDescribeArchiveMismatch(dictEnvironment) == []`
3. otherwise refuse, naming both failures

**Provenance is carried forward verbatim**, never upgraded. Recompute
only if the record carries none.

**On success:** write through `_fdictStampArchiveRecord` (which re-pins
the manifest), move the old record to `dictSupersededImageArchive` in
the same read-modify-write, and **touch no project configuration at
all**. Extend `environmentSnapshot`'s carry-forward to preserve the
superseded note under the same digest-and-architecture condition it
already applies, or it evaporates at the next regeneration.

**Token recovery is credential-only.** Do not post to
`/api/sync/{id}/setup` — its success path persists the instance. Build
either a `bCredentialOnly` flag that skips `_fnPersistZenodoService` or
a dedicated route; add a public instance-preselecting opener that
resolves a promise the promotion awaits, instead of opening the push
modal.

**Tests:** the token check refuses before `docker save` is reached
(make the save raise if called); the fallback refuses a mismatched
record; the fallback does not fire when the image is present and the
save fails; provenance `verified-equivalent` survives promotion (the
natural test — promoting an `original` record — passes against the
bug); a successful production connection leaves `sZenodoService` and
`dictRemotes.zenodo` untouched; the carry-forward preserves the
superseded note and drops it with the primary when digest or
architecture disagrees.

---

### Stage 8 — Project-deposit promotion and `start-new-concept`

**Depends on:** Stages 2, 6.

**It is a new production release**, not a byte copy: it publishes the
current canonical publication union through the existing archive flow,
with its existence pre-flight and basename-collision refusal. Say so in
the confirmation — a researcher expecting a byte copy has been misled
about what the DOI names.

**Service and parent are overrides, not pre-writes.** Pass `"zenodo"`
and parent `0` into the existing upload flow; persist nothing until the
publish succeeds. Parent 0 because a promotion starts a new concept,
and a sandbox deposit id sent to the production `newversion` flow names
a record that does not exist there.

**Persist only produced fields**, through the Stage 6 seam: advance
`dictRemotes.zenodo.sService` and add `dictSuperseded` to
`DICT_REMOTE_PRODUCED_FIELDS["zenodo"]`. Never top-level
`sZenodoService`.

**Refuse on any additional declared record** while the configured
service is sandbox — *all* of them, not only those classifying as
sandbox. They carry no service of their own, so a DOI-less entry would
classify `unknown`, pass a permanence-based check, and break
verification. Name them and point at `remove-zenodo-record`.

**Add `POST /api/workflow/{id}/zenodo/start-new-concept`**, the remedy
the Stage 2 refusal names. Sidecar-only, confirmed, Level 2 rung: it
clears the primary record's deposit id, DOI, URL and service into the
superseded note so the next publish creates a fresh concept on the
declared instance.

**Tests:** `project.json` bytes are identical before and after a
successful promotion; the no-DOI declared record is refused; end-to-end
promote → declare sandbox → refuse → `start-new-concept` → publish
yields a first version on sandbox with the old identifiers in the
superseded note.

---

### Stage 9 — The recovery lane

**Depends on:** Stages 6, 7, 8. The largest stage; do it last.

**Two checkpoints.** Intent before anything starts, and the **deposit
id made durable at draft creation, before any upload**. The image lane
is a direct call and a callback suffices; the project lane creates its
draft inside the container script, so it needs a streaming event the
host persists and acknowledges before the script proceeds, or a split
between draft creation and upload/publish.

**Resume needs bytes.** Choose one and say which in the code:
- keep a promotion-scoped snapshot through settlement, with an orphan
  sweep over `~/.vaibify/imageArchive/` that deletes only directories
  belonging to no live pending record (age is not evidence — the
  `~/.vaibify/tmp` lesson); **or**
- re-create every source, hash it against the record, and **refuse
  Resume on drift**, naming the changed paths.

Resume must never mean "upload whatever is there now".

**Reconciliation asks Zenodo**, via `fdictGetDeposit`, comparing MD5
and size against the record and deposit keys against the recorded
basename mapping:

| Outcome | Zenodo shows | Offered |
|---|---|---|
| `resumable` | `inprogress`, files absent/incomplete | Resume, Discard |
| `publishable` | `inprogress`, all files matching | Resume (publish), Discard |
| `published` | `done`, contents matching | Adopt |
| `mismatched` | contents do not match | neither |
| `gone` | a positive 404 | Discard the record |
| `errored` | `state == "error"` | neither; surface Zenodo's guidance |
| `unknown` | unreadable (no token/network, 5xx) | nothing; **keep the record** |

`unknown` and `gone` are different: a 404 is an answer, a timeout is
not.

**Stamp the promotion id into the remote metadata** (the same way the
image deposit's fingerprint rides in its description) and verify it
before Resume or Discard — otherwise a writable local file is the only
thing binding a remote mutation to its intent.

**Two adoption paths.** Project lane → sidecar seam. **Image lane →
`_fdictStampArchiveRecord`**, and the pending record survives until
`bManifestRefreshed` is true, because that function writes
`environment.json` first and `_fbRepinManifestOrWarn` returns `False`
rather than raising. Adoption is idempotent across that boundary.

**Routes:** `GET .../promotions/pending`,
`POST .../{id}/reconcile`, `POST .../{id}/resume`,
`POST .../{id}/adopt`, `DELETE .../{id}`. `DELETE` refuses `published`
and `unknown`.

**Tests:** the deposit id is durable at draft creation, **for both
lanes** (drop the process after the draft exists, before publication
returns); each of the seven outcomes is reachable and offers only its
own actions; `unknown` keeps the record and refuses `DELETE`; adoption
matches on hashes, not filenames, and is idempotent; the image lane
writes no project fields and the project lane writes no
`environment.json`. Browser: the card appears on reload, offers
outcome-specific actions, confirms before every mutation, persists on
`unknown`, and disappears after settlement.

---

### Stage 10 — Registration, confirmations, wording

**Depends on:** Stages 7, 8, 9.

**Every new action needs a rung** in
`testAdvancingALevelNeverLowersOne.py`'s `DICT_LADDER_ACTION_RUNGS`:
the two promotions (3), the recovery actions (the rung of the promotion
they settle), `start-new-concept` (2). A missing rung fails
`test_the_rung_ledger_covers_every_ladder_action`.

**Do not pre-add anything to `DICT_ACCEPTED_CROSSINGS`** —
`test_..._stale` fails on an entry for an action that does not cross,
and none of these is expected to. Run the test and record only what it
finds, describing what the scanner saw rather than asserting that
published bytes diverge.

**Catalog:** all routes in `LIST_AGENT_ACTIONS` with
`bAgentSafe: False`, plus agent-lane rejection tests. Promotion spends
a production credential and mints a public DOI.

**Confirmations** — each says something different:
- environment promotion: a step on the way; the envelope must be pushed
  and the project deposit published afterwards
- project promotion: **is** the final publication; do not tell them to
  republish
- `start-new-concept`: gives up a version chain
- Resume / Discard: mutate a live draft; Adopt: writes a published DOI

**Wording, and this is correctness not copy.** Do not write "the
sandbox DOI dies" (it persists until Zenodo clears it), "wiped
periodically" (Zenodo says *at any time*), or that a production deposit
"cannot be deleted". **Fix the shipped copy**: the
`deposit-environment-archive` confirmation currently says "Zenodo
deposits are PERMANENT and cannot be deleted", which is false for the
first 30 days. Prefer "deleting it later requires contacting Zenodo,
and the DOI is tombstoned rather than removed" — stronger and true.

**Regenerate every ledger** if any function composing container
commands was touched, and diff the disposition count before and after.

---

### Stage 11 — Verification sweep

```bash
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
python -m pytest tests/testArchitecturalInvariants.py -v
pip install -e '.[browser]' && python -m playwright install chromium
python -m pytest tests/browser -m browser
PYTHONPATH=. python tools/carrierIntentAudit.py
python tools/generateMutationInventory.py --check    # must print {}
python tools/reconfirmFalsification.py --only <new entries>
```

**A green Python suite says nothing about the frontend.** If you cannot
load a browser, push the branch and open a pull request against `main`
so the browser lane runs — a pushed branch with no PR runs nothing, and
a PR based on anything but `main` runs nothing. If you can do neither,
say so explicitly and name the exact surface you did not verify.

Add falsification entries for: recorded-service-wins precedence, the
`unknown`-passes branch, parent-0 on promotion, the tarball-hash check
before upload, and the `unknown`-keeps-the-record branch. Verify each
kills before committing it.

---

## Suggested sequencing

```
Stage 1 ─┐
Stage 2 ─┼─→ Stage 3 ─→ Stage 4 ─┐
Stage 5 ─┤                       ├─→ Stage 10 ─→ Stage 11
Stage 6 ─┴─→ Stage 7 ─┬─→ Stage 9┘
                      └─→ Stage 8
```

Stages 1–4 are shippable on their own: they add the warning, the DOI
display and the gate without any promotion lane. That is a legitimate
first pull request, and it is the half a researcher benefits from
immediately — being *told* their archives are sandbox deposits.
Stages 5–9 are the promotion and recovery machinery.

---

## Before you start

- Read the design doc's **Rulings** and **Changelog**. The changelog
  records which instructions were wrong in earlier drafts and why;
  several are the natural thing to do.
- One scope call is reversible and recorded in the design doc's "Open
  items": returning a promoted project to sandbox is **supported**
  (Stage 8's `start-new-concept`). If the researcher drops it, delete
  the route, rung, confirmation and test, and make the Stage 2 refusal
  say plainly that returning to sandbox is unsupported.
- **Ask before** changing the verification state machine, Docker
  security posture, or anything under `vaibify/reproducibility/` beyond
  what is specified here. The `askSensitiveEdit.py` hook will stop you
  on some of it.
