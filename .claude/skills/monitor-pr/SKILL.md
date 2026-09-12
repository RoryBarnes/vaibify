---
name: monitor-pr
description: Watch a vaibify pull request's GitHub Actions lanes and its mergeability, pull failures back to the local checkout, fix and re-check them locally, and push. Use when a PR has been opened or updated and its CI needs shepherding to green, or when a merge elsewhere may have left the branch conflicted.
---

# Monitoring a vaibify pull request to green

CI on this repository is slow and wide: a dozen lanes, some of them
tens of minutes. A PR is rarely green on the first push, and a merge
into a *different* branch can leave this one conflicted while its
checks are still running. This skill is the loop that closes both.

One pass has four phases. Run them in order; each one can end the pass.

## Phase 0 — Establish that CI is even running

Do this FIRST, every pass. Two repository-specific traps make an
un-run lane look exactly like a passing one.

```bash
gh pr view <PR> --json number,baseRefName,mergeable,mergeStateStatus,isDraft,headRefName
```

- **A PR whose base is not `main` runs no CI at all.** Every workflow is
  `on: pull_request: branches: [main]`, so a PR stacked on another open
  branch shows a clean, empty check list. Retargeting the base does
  **not** trigger the workflows either — `gh pr close <PR> && gh pr
  reopen <PR>` does.
- **An empty check list is never a pass.** If `gh pr checks` reports no
  checks, say so explicitly and stop; do not report the PR as green.

```bash
gh pr checks <PR> --json name,state,bucket,link
```

If the list is empty and the base is `main`, the run has not been
created yet — wait for the next pass rather than concluding anything.

## Phase 1 — Wait for the lanes, event-driven

Prefer waiting on the event over polling on a timer:

```bash
gh pr checks <PR> --watch --fail-fast
```

Run this with `run_in_background: true`. It blocks until the checks
finish and exits non-zero on the first failure, so the harness wakes
you at the moment something is known — no five-minute sampling of a
forty-minute lane. Exit code `8` means checks are still pending.

When a recurring pass is wanted instead, this skill is written to be
driven by `/loop`:

```
/loop 5m /monitor-pr <PR>
```

Under `/loop`, keep each pass short: check, act if there is something
to act on, and report `noop` when nothing moved.

## Phase 2 — Triage a failure before touching the code

Pull the failure back, and read it before forming a hypothesis:

```bash
gh run view <RUN_ID> --log-failed | tail -100
```

**Not every red lane is a defect in this branch.** Classify first:

| Signal | Meaning | Action |
|---|---|---|
| `fresh-image-build`, exit **1**, "pinned compiler toolchain is no longer available" | A real Ubuntu pin rotation | **Maintainer decision — do not fix.** Report it and stop. |
| `fresh-image-build`, exit **100**, `Hash Sum mismatch` | Transient Ubuntu mirror hiccup | `gh run rerun <RUN_ID> --failed` |
| Google Fonts / DNS error in a browser test | Known flake — `styleMain.css` `@import`s a font | Re-run the lane before diagnosing the frontend |
| The same lane fails twice on the same commit | Real | Fix it |
| A lane that passed on the previous commit | Likely real, caused by this push | Fix it |

Do not infer which failure you hit from run duration. Read the exit
code and grep the log for the diagnostic text.

## Phase 3 — Fix locally, verify locally, then push

Bring the branch down and reproduce the failure on this machine. The
whole point of pulling it back is that a CI round trip costs far more
than a local run.

```bash
git fetch origin && git checkout <headRefName> && git pull --ff-only
```

Reproduce the failing lane with its own selection, not the whole suite:

```bash
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
python -m pytest tests/testArchitecturalInvariants.py -v
python -m pytest tests/browser -m browser          # needs the browser extra
python tools/carrierIntentAudit.py
```

`tools/deriveLaneSelection.py <workflow file>` prints the exact pytest
arguments a lane runs, so a local reproduction can match CI's selection
instead of guessing at it.

Four rules govern the fix itself:

- **Never delete or silence a test to make a failure go away.** A
  failing test signals a bug in the code, a bug in the assertion, or a
  legitimate behavior change the test predates. Address the right one.
- **Batch the fixes.** A suite run is ~20 minutes locally and a CI round
  trip is longer. Finishing four small items in four pushes spends over
  an hour to learn what one push would have said, and it hides
  interactions between them.
- **Regenerate every ledger, not just the one that complained.** A local
  rename re-fingerprints rows in more than one generated file, and
  `--check` on one ledger reads exactly as clean as `--check` on all of
  them. After touching any function that composes container commands,
  regenerate and drift-check each generator under `tools/`.
- **Never force-push.** `git push --force` is hard-blocked by a harness
  hook; `--force-with-lease` is permitted but is almost never what a
  shared PR branch wants.

Then push and go back to Phase 1:

```bash
git push origin HEAD
```

## Phase 4 — Conflict watch

A merge into a *different* branch is what usually conflicts this one,
and it happens silently while the lanes run. `mergeStateStatus` from
Phase 0 carries it:

- `DIRTY` / `mergeable: CONFLICTING` — resolve now.
- `BEHIND` — the base moved; merge it forward so the lanes test what
  will actually land.
- `BLOCKED` — a required check has not passed, or a review is pending.
  Not a conflict.

Resolve by merging `main` **into** the branch, never by rebasing — the
branch is shared and rebasing it would need the force-push that is
blocked:

```bash
git fetch origin && git merge origin/main
```

**A generated ledger conflicts differently from source.** The ledgers
are one record per line precisely so a conflict is a reading task.
Keep every record from *both* sides, then rebuild — never take one side
wholesale, because regeneration carries a reviewer's judgment forward
from the file on disk and `git checkout --theirs` silently discards
every disposition this branch recorded:

```bash
$EDITOR tests/mutationInventory.json          # keep BOTH sides' records
python tools/generateMutationInventory.py --write
python tools/generateMutationInventory.py --check   # must print {}
```

The same applies to `tests/hostCapabilityInventory.json` and
`tests/styleInventory.json`. A ratchet constant that both sides moved
must be **re-measured**, never summed.

After resolving, run the local suite before pushing — a merge resolution
is a code change like any other.

## What to report

End every pass with a statement that separates what was verified from
what was not:

- Which lanes are green, which are red, and which have **not run**.
- For anything fixed: the check that was run locally to confirm it.
- For a lane that could not be exercised on this host (Docker, browser),
  say so by name. Silence about an unverified surface reads as
  verification.
