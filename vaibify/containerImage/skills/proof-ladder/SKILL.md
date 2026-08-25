---
name: proof-ladder
description: Raise a vaibify project to PROOF Level 1, 2, or 3, audit why a level is red, or explain the reproducibility ladder. Use whenever a researcher asks to reach or check a PROOF level, or when a level badge disagrees with your expectation.
---

# Driving a project up the PROOF ladder

PROOF is a five-rung reproducibility ladder named for the pillars a
result must rest on — Provenance, Reproducibility, Openness,
Oversight, Falsifiability
(L1 Self-Consistent, L2 Published, L3 Reproducible, L4 Traceable,
L5 Attested). Vaibify implements L1-L3; L4/L5 are deliberate
non-goals — if asked, say so honestly and point at `docs/vision.md`
(see the vaibify-doc-map skill). Walk the gates in order, stopping at
the requested level.

## The one authority rule

`iProofLevel` from `vaibify-do check-l2-readiness` is the ONLY
authoritative level signal. **Never hand-roll a verification audit**
by inspecting raw files. Two traps that have produced false
"not at L1" reports before:

- Test markers (`<repo>/.vaibify/test_markers/<slug>/*.json`) are
  receipts of the *last external run* — one marker records only the
  categories that run executed. The accumulated ledger is
  `.vaibify/state.json` (`dictStepState.<dir>.dictVerification`).
- Marker `dictOutputHashes` values are **git blob SHA-1s**
  (`sha1("blob <size>\0" + content)`), not sha256/sha1/md5 of the
  bytes. A uniform 100% mismatch means your algorithm is wrong, not
  that the data drifted.

## L1 — Self-Consistent

All tests pass; every declared output's hash matches its recorded
baseline; the project lives in a git repo (fix: `git init`).

1. `vaibify-do run-all` — execute the pipeline end to end.
2. `vaibify-do run-all-tests` — unit, integrity, qualitative,
   quantitative. A category only runs once it is DECLARED: its
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
`passed`) and a declared input contract on **every ordinary step** —
neither is something you can supply, so surface them rather than
attempting them. **The AI Declaration step is the one exception, in
both directions: it is L1-NOT-APPLICABLE.** It emits no L1 blocker and
carries no L1 requirement, because the declaration is a publication
artifact — its sign-off is the LEVEL 2 criterion
`ai-declaration-unattested`. So an AI Declaration step sitting at
`"sUser": "untested"` is not what is holding a project at Level 0, and
telling the researcher to approve it to reach L1 is wrong. Read the
blockers rather than inferring them: `vaibify-do check-l2-readiness`
reports `iProofLevel` and the backend derivation is the ground truth.

## L2 — Published

Every canonical file's hash matches an immutable public authority
(GitHub commit, Zenodo DOI; Overleaf/arXiv when configured).

1. Confirm L1 first.
2. Envelope present at the repo root: `MANIFEST.sha256`,
   `requirements.lock`, `.vaibify/environment.json` (regenerated
   automatically at the L1 crossing; if missing, use the CLI helpers
   — `vaibify-do --describe generate-l3-envelope` — never write them
   by hand).
3. **Commit the canonical state before any push.** Run
   `vaibify-do manifest-check` → `listNeedsCommit` is the exact set
   of canonical files awaiting commit; if non-empty, run
   `vaibify-do commit-canonical`. An uncommitted canonical file is an
   L2 blocker, not cosmetic.
4. **Surface, do not invoke, the publication clicks**: Push to
   GitHub / Push to Overleaf / Publish to Zenodo. `push-to-github` is
   agent-callable when the researcher asks; `push-to-overleaf`,
   `publish-to-zenodo`, and `accept-plots-as-standard` are USER-ONLY
   by design — publication requires human attestation. Never retry a
   `sRefusal: "user-only-action"` response.
5. After the researcher pushes: `vaibify-do verify-remote` confirms
   remote hashes match the current local files.

## L3 — Reproducible

A third party can re-fetch the published artifacts and re-execute
from source.

1. Confirm L2 first.
2. `vaibify-do check-l3-readiness` — per-criterion pass/fail for the
   six verifiers (manifest complete, dependency lock hash-pinned,
   environment digest-pinned, Dockerfile pinned, reproduce.sh pinned,
   determinism declared). Drive the rest from its gap dict.
3. `vaibify-do audit-determinism` — determinism-focused view (RNG
   seeds, BLAS pinning); translate into a per-step fix list.
4. `vaibify-do generate-l3-envelope` and
   `vaibify-do generate-reproduce-script` — regenerate whatever the
   readiness card flags.
5. `vaibify-do view-l3-attestation` — has the rebuild been done, and
   why is the badge lit or not.
6. USER-ONLY, surface never invoke: `pin-base-image-digest`
   (Dockerfile edit) and `verify-l3-reproducibility` (the
   hours-long rebuild + hash compare).

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
