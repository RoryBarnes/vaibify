---
name: proof-ladder
description: Raise a vaibify project to PROOF Level 1, 2, or 3, audit why a level is red, or explain the reproducibility ladder. Use whenever a researcher asks to reach or check a PROOF level, or when a level badge disagrees with your expectation.
---

# Driving a project up the PROOF ladder

PROOF is a six-rung reproducibility ladder named for the pillars a
result must rest on — Provenance, Reproducibility, Openness,
Oversight, Falsifiability
(L1 Self-Consistent, L2 Published, L3 Reproducible, L4 Traceable,
L5 Regenerated, L6 Attested). Vaibify implements L1-L3; L4-L6 are
deliberate non-goals — if asked, say so honestly and point at
`docs/proofLadder.md` (see the vaibify-doc-map skill). Walk the gates in
order, stopping at the requested level.

What the three out-of-scope rungs mean, so you can answer without
inventing:

**L4 Traceable** adds a public changelog for every artifact — git
history *and* the AI prompt transcript — plus traceability back
through calibration to the raw observations. It cannot be reached
retroactively, because the transcript has to exist before L1.

**L5 Regenerated** retraces the work with a *different* AI model and
gets the same science. Four things a researcher may ask you about,
each easy to get wrong:

- The regeneration starts from the **publication**, not the
  transcript. Replaying the transcript reproduces the first model's
  path instead of deriving anything independently. The transcript
  stays public for audit; it is not the input.
- The equivalence **tolerance is declared in the publication**, per
  quantity and with a justification, and must cover every quantity
  the paper's claims rest on. Publishing it beforehand is what makes
  it pre-registered rather than a rationalization.
- L5 then returns a **verdict** — did every compared quantity fall
  inside that tolerance? Whether the tolerance was *appropriate* is
  the reader's call, not the ladder's.
- Byte-identity across the two runs is NOT expected. The second
  analysis must itself reach Level 4; the two are not compared byte
  for byte.

"A different model" means a different model identifier — a floor, not
a guarantee, since same-vendor siblings likely share a pretraining
corpus. L5 claims model-independence conditional on the same
researcher; it cannot claim more, because one person directs both
runs.

**L6 Attested** adds independent, publicly verifiable attestations by
third parties who retraced the work themselves and confirmed the L3
reproducibility of BOTH artifacts plus the equivalence verdict. They
do not report cross-run byte-identity. Because those attestors never
saw the original process, L6 is where independence from the
researcher arrives.

**Level 3 requires a containerized project.** A host-mode project is
refused with the `host-mode` criterion — the level is defined by a
pinned image digest and an in-container rerun, and a host project has
neither. Do not walk a host project up to L3; say it reaches L2.

## The one authority rule

`iProofLevel` from `vaibify-do check-l2-readiness` is the ONLY
authoritative level signal. **Never hand-roll a verification audit**
by inspecting raw files. Two traps that have produced false
"not at L1" reports before:

- Test markers (`<repo>/.vaibify/test_markers/<slug>/*.json`) are
  receipts of the *last external run* — one marker records only the
  categories that run executed. The accumulated ledger is
  `.vaibify/state.json`, namespaced per project since schema v3:
  `dictWorkflowState.<repo-relative project.json path>.dictStepState`.
  A top-level `dictStepState` is quarantined legacy data, not the
  ledger.
- Marker `dictOutputHashes` values are **git blob SHA-1s**
  (`sha1("blob <size>\0" + content)`), not sha256/sha1/md5 of the
  bytes. A uniform 100% mismatch means your algorithm is wrong, not
  that the data drifted.

## Before the first run, when Level 3 is the goal: Determinism

The Determinism block asks ONE question: "How much speed should
vaibify trade for repeatable files?" The answers are "Prioritize
speed" and "Repeatable across computers". Either passes; only an
unanswered question fails. The answer picks the execution settings
EVERY run uses, so ask for it before the first run, not after Level 2:

- Until it is answered, runs use the speed settings.
- Changing the answer later means rerunning every step once,
  regenerating the envelope (the rerun replays the settings the
  envelope records), and verifying Level 3 again; it retires the
  current attestation.

The answer is the researcher's — `declare-determinism` is USER-ONLY.
Relay the question with both choices; never pick one for them, and do
not hunt for a seed to "fix" the row: it asks what they choose, not
what the scripts do.

## L1 — Self-Consistent

All tests pass; every declared output's hash matches its recorded
baseline; the project lives in a git repo. A missing repository is
created with `init-project-repo` (USER-ONLY — surface it) or, for a
directory that is not yet a project, `adopt-directory-as-project`.
Never run `git init` in a shell: that makes a repository the
dashboard never hears about.

1. `vaibify-do run-all` — execute the pipeline end to end. Each
   enabled step runs its data commands, then its plot commands, then
   its tests, so a successful run-all has already run every declared
   test; do NOT follow it with `run-all-tests`. A step whose plot
   commands fail skips its tests (the log says so), and a failing test
   never fails the step.
2. `vaibify-do run-all-tests` only when the tests changed and the
   outputs did not — it runs every step's tests without the data or
   plot commands. A category only runs once it is DECLARED: its
   commands live at `dictTests.dictQualitative` (and `.dictIntegrity`,
   `.dictQuantitative`). The best way to get them is
   `vaibify-do generate-tests-deterministic <step>
   sCategory=qualitative` — it introspects the step's declared outputs,
   writes the test and its standards from what is actually in them, and
   declares the category itself. Hand-declaring via `vaibify-do
   update-step <step> '{"dictTests": {...}}'` is the fallback, and
   there you must send every category you mean to keep in the same
   call: `dictTests` is replaced wholesale, not merged. Writing and
   running tests is agent-safe; only `generate-tests` — which can fall
   back to an LLM — is user-only.
3. `vaibify-do verify-only` — outputs exist and hashes match.
4. Confirm `iProofLevel >= 1` via `vaibify-do check-l2-readiness`. If
   it stays 0 after the prior steps succeeded, surface the
   discrepancy — the backend derivation is the ground truth.

Committing (`commit-canonical`) and the manifest are L2 preparation,
not L1 requirements: git-dirty-but-consistent files block L2, never
L1. A test category with no commands counts green ("N/A") — never
fabricate trivial tests to satisfy the dashboard.

All three tiers read a step's declared `saOutputDataFiles`, never its
figures: integrity says the artifact loads, qualitative says its
columns/keys/array names are still there, quantitative says the
numbers match within tolerance. Accepting plots as standard is a
separate figure-comparison mechanism and does NOT create or enable a
qualitative test — a step whose only outputs are figures has none, and
that is N/A, not a gap. The full statement of what each tier does and
does not prove is in the repo-root agent guide.

L1 also requires the researcher's per-step attestation (`sUser` is
`passed`), which only they can give, and a declared input contract on
**every ordinary step**, which you may declare with
`add-input-data-file` or `declare-no-input-data` once the researcher
has confirmed what the step reads. Two more things hold the level at 0
and are easy to misread: an unresolved remote-data pull marker forces
level 0 with no step blocker (the next successful run of that step
clears it), and a dependency scan that found an unreconciled input
(`rescan-dependencies` is agent-safe; dismissing a finding is
USER-ONLY). **The AI Declaration step is the one exception, in
both directions: it is L1-NOT-APPLICABLE.** It emits no L1 blocker and
carries no L1 requirement, because the declaration is a publication
artifact — its sign-off is the LEVEL 2 criterion
`ai-declaration-unattested`. So an AI Declaration step sitting at
`"sUser": "untested"` is not what is holding a project at Level 0, and
telling the researcher to approve it to reach L1 is wrong. Read the
blockers rather than inferring them: `vaibify-do check-l2-readiness`
reports `iProofLevel` and the backend derivation is the ground truth.

## L2 — Published

Every published file's hash matches an immutable public authority
(GitHub commit, Zenodo DOI; Overleaf/arXiv when configured), AND the
researcher has answered the publication questions. Read
`dictLevel2Gaps` from `vaibify-do check-l2-readiness`: every `false`
key is one remaining criterion. They are:

- **Published copies** match: GitHub (`bGithubFullySynced`) and
  Zenodo (`bZenodoFullySynced`); Overleaf and arXiv only when bound
  or recorded. No two compared files may share a basename.
- **AI Declaration** signed and still current
  (`bAiDeclarationAttested`). A sign-off the files have moved past
  reads as stale.
- **AI models** declared (`bAiModelsDeclared`): `declare-ai-model` is
  agent-safe, but declare only facts the researcher confirmed.
- **Personal AI Configuration** answered (`bPersonalLayerDeclared`):
  `declare-personal-layer` is USER-ONLY. Any answer passes, including
  "declared-private"; never ask them to produce a file.
- **Environment archive** question answered (`bImageArchiveAnswered`):
  `answer-environment-archive` is USER-ONLY.

A remote verify compares one set of files, but they gate different
levels: the project's own files (declared outputs, scripts,
`project.json`) gate Level 2; the reproducibility envelope
(`MANIFEST.sha256`, `requirements.lock`, `reproduce.sh`,
`.vaibify/environment.json`, `Dockerfile`, `.vaibify/rerunAssessment.py`,
and any `requirements.txt` / `environment.yml` / `pyproject.toml`)
gates Level 3. `.vaibify/l3_attestation.json` and
`.vaibify/ai_provenance.json` are compared and shown, but gate nothing
in this comparison.

1. Confirm L1 first.
2. **Commit the canonical state before any push**: run
   `vaibify-do commit-canonical`, which commits exactly the canonical
   files awaiting commit (`listNeedsCommit`). An uncommitted canonical
   file is an L2 blocker, not cosmetic.
3. **Surface, do not invoke, the publication clicks**: the Sync
   menu's Push to GitHub, Push to Overleaf and Archive to Zenodo.
   `push-to-github` is agent-callable when the researcher asks;
   `push-to-overleaf`, `publish-to-zenodo` and
   `accept-plots-as-standard` are USER-ONLY by design — publication
   requires human attestation. Never retry a
   `sRefusal: "user-only-action"` response.
4. A push through vaibify re-verifies that remote on its own. Report
   its counts only when the response carries no
   `sPostPushVerifyWarning`; with one, say the comparison did not run
   and that **Verify now** on the remote's row in **Published copies**
   runs it. `vaibify-do verify-remote <service>` is the same check.
5. **After a push that did NOT go through vaibify** — a `git push`
   you or the researcher typed in a terminal, or one a script made —
   run `vaibify-do reconcile-remote-state` (GitHub only). The L2 cells
   read a cached comparison; terminal git rewrites nothing, so until a
   verify runs (this call, Verify now, or the scheduled re-verify every
   six hours) the dashboard shows the pre-push state, and a comparison
   older than 24 hours reads as unknown. Report `iMatching`,
   `iTotalFiles` and `listDiverged` from the response's
   `dictVerifyStatus` — if the cell was already green the researcher
   sees no change and cannot otherwise tell the reconcile from a no-op.

   **Check this before concluding a green L2 cell is a bug.** A
   requirement row reads what the last verify recorded, not what
   github.com holds right now.

## L3 — Reproducible

A third party can obtain the pinned environment, re-run the workflow
from the published files, and get results the published comparisons
accept.

Read `vaibify-do check-l3-readiness`. Readiness has eight parts, each
its own row: the manifest is complete; the manifest matches the files;
the dependency lock is hash-pinned; the environment snapshot exists;
the Dockerfile is pinned; `reproduce.sh` exists; Determinism is
answered; standalone binaries are declared or waived
(`declare-standalone-binaries`, USER-ONLY). Level 3 itself also needs
a current rebuild attestation, the envelope on GitHub, the envelope
AND the attestation in the Zenodo archive, the environment image
archived, no archive left on the Zenodo sandbox, and a current
`reproduce.sh`. The readiness answer also carries `bImageWasBuilt` and
`bPinnedImageObtainable`: facts about how the image came to exist,
which choose the remedy a refusal names on someone else's
containerized clone. They are not criteria; never report a `false`
one as a gap.

**The last steps have an order**, because some rewrite files others
have already fingerprinted, and a published Zenodo version cannot be
changed. When order matters, the dashboard puts a "Do this next" arrow
on the section that holds the row to fix; with no arrow, the remaining
work can be done in any order. The order:

1. **Determinism answered before the run that made the outputs** (see
   above).
2. `vaibify-do regenerate-envelope` — the WRITER. It rewrites
   `MANIFEST.sha256`, `requirements.lock` and
   `.vaibify/environment.json` (including the execution settings the
   Determinism answer chose), and returns `dictTierResults`; read it,
   because a tier that could not run reports `bWritten: false` with an
   `sSkipReason` and the readiness gap alone cannot tell that apart
   from "regeneration is not what fixes this gate".
   `generate-l3-envelope` is READ-ONLY despite its name — a diagnostic
   view, not a regeneration.
   On a CLONE, check first: when "Manifest matches the files" fails
   and `vaibify-do list-committed-file-differences` reports
   `sManifestOwnership: "foreign"`, the manifest is the author's claim.
   `regenerate-envelope` then answers 409 unless the researcher agrees
   to replace it (`bReplaceForeignManifest`); relay that choice, never
   supply it. The usual remedy is the committed versions of the files
   it lists — `restore-committed-files`, USER-ONLY because it discards
   the container's changes to them. An envelope from an author's
   OBTAINED image is refused with `switch-to-building`; say so.
3. `vaibify-do generate-reproduce-script` — writes `reproduce.sh` and
   re-pins the manifest in the same action.
4. **Settle the manifest.** The attestation records the manifest's
   fingerprint, so any later change makes it stale.
5. **Archive the environment** — `deposit-environment-archive`
   (USER-ONLY). It writes the archive record into
   `.vaibify/environment.json` and re-pins the manifest, so it comes
   before the verify. A sandbox deposit must be promoted to permanent
   Zenodo (`promote-environment-archive`, USER-ONLY) before Level 3.
6. **Verify Level 3** — `verify-l3-reproducibility`, USER-ONLY: the
   Run menu's "Verify Level 3 Reproducibility". It re-runs every step
   in a throwaway container built from the pinned image and grades each
   output: byte-identical, comparisons passed, not reproduced, or not
   enough evidence. Byte identity is never required, but an output
   whose bytes changed passes ONLY through a published quantitative
   comparison: integrity and qualitative checks alone grade it "not
   enough evidence", which fails. A step whose quantitative tests are
   N/A therefore passes Level 1 and can fail Level 3 on another
   computer; say so before the researcher relies on it. Before
   running, the verify also refuses when the image does not match the
   declared packages, the Dockerfile does not describe the pinned
   image, or the image does not satisfy `requirements.lock`; each
   refusal names its remedy, so relay it. Then commit the attestation
   (`vaibify-do commit-canonical`).
7. **Push to GitHub, then publish a Zenodo version** that holds the
   envelope and the attestation (Sync menu → Archive to Zenodo, which
   is the researcher's). Publishing before step 6 costs a second
   Zenodo version. When the project's Zenodo record is on a different
   site from the one it publishes to, the Zenodo row says so and offers
   the choice; relay it, it is the researcher's.

`vaibify-do view-l3-attestation` shows the last rebuild record. A
"passed" record does not count when it is historical (made by an older
verifier or comparison policy) or ran under different execution
settings from the current Determinism answer: decide from
`bL3AttestationCurrent` in the readiness answer, never from `sStatus`.

The Dockerfile row's remedy is `copy-image-dockerfile` (USER-ONLY): it
writes the image's own build chain into the repository and re-pins the
manifest. `pin-base-image-digest` is only a read-only view of that
gap.

## Reporting honesty

Report levels only from `iProofLevel`. When your own reading of files
disagrees with the backend, say "the backend derives N; my file
inspection suggested otherwise" and treat the backend as correct
until proven buggy — do not report your inspection as the level.

## Reporting register — write for the researcher, not the schema

The researcher is a scientist using a dashboard, not a reader of this
project's source. They did not name these fields and have no reason to
know them. So report in the words the dashboard uses: step labels
(`A01`, `I01`), the names on the badges (Tests, Verified, N/A), and
the button they would click.

Quote a dashboard label ONLY as the dashboard spells it, and give the
whole path to it (section, row, then control), e.g. Project block →
Published copies → GitHub mirror → Verify now; the dashboard-map
skill lists every label and its path. An action name is never
a label: `publish-where-the-zenodo-record-is` is not a button anyone
can find. When you do not know a control's label, say so and describe
where to look, rather than inventing a name.

`vaibify-do check-l2-readiness` returns `listLevel1Blockers`, and every
entry carries `sRemediationHint` — the exact sentence the dashboard
shows, e.g. "Step has never been verified — click verify when
satisfied". **Relay that sentence.** Do not translate it back into
`sUser`, `dictQualitative`, or `axis-not-green`; those are the names of
the machinery, and reciting them tells the researcher what you read
rather than what to do.

Identifiers are for two situations only: the researcher asked for one,
or you are writing code or a command they will run. "Step A02 has no
qualitative tests defined, which counts as N/A — nothing is blocking
you there" is a report. "`sQualitative` is `unnecessary` because
`dictQualitative.saCommands` is empty" is the same fact addressed to
the wrong reader.

One caveat to state rather than hide: `bScriptStalenessEvaluated` comes
back `false`, because that one criterion needs a scan only the
dashboard runs. An empty blocker list from this call therefore means
"nothing found here", not "nothing is wrong" — say the former.
