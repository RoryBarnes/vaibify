---
name: aics-ladder
description: Raise a vaibify workflow to AICS Level 1, 2, or 3, audit why a level is red, or explain the reproducibility ladder. Use whenever a researcher asks to reach or check an AICS level, or when a level badge disagrees with your expectation.
---

# Driving a workflow up the AICS ladder

The AI Containment Scale is a five-rung reproducibility ladder
(L1 Self-Consistent, L2 Published, L3 Reproducible, L4 Archived,
L5 Attested). Vaibify implements L1-L3; L4/L5 are deliberate
non-goals — if asked, say so honestly and point at `docs/vision.md`
(see the vaibify-doc-map skill). Walk the gates in order, stopping at
the requested level.

## The one authority rule

`iAICSLevel` from `vaibify-do check-l2-readiness` is the ONLY
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
baseline; the workflow lives in a git repo (fix: `git init`).

1. `vaibify-do run-all` — execute the pipeline end to end.
2. `vaibify-do run-all-tests` — unit, integrity, qualitative,
   quantitative.
3. `vaibify-do verify-only` — outputs exist and hashes match.
4. Confirm `iAICSLevel >= 1` via `vaibify-do check-l2-readiness`. If
   it stays 0 after the prior steps succeeded, surface the
   discrepancy — the backend derivation is the ground truth.

Committing (`commit-canonical`) and the manifest are L2 preparation,
not L1 requirements: git-dirty-but-consistent files block L2, never
L1. A test category with no commands counts green ("N/A") — never
fabricate trivial tests to satisfy the dashboard.

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

Report levels only from `iAICSLevel`. When your own reading of files
disagrees with the backend, say "the backend derives N; my file
inspection suggested otherwise" and treat the backend as correct
until proven buggy — do not report your inspection as the level.
