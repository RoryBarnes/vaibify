---
name: running-steps
description: Run one or more pipeline steps so the dashboard reflects the run, and avoid making a run invisible to the researcher. Use whenever you are about to execute a step's commands, run several steps in sequence, or are tempted to launch a script directly in a shell.
---

# Running steps so the dashboard stays honest

The dashboard is the researcher's ground truth. It can only show a
step as *running* for runs it is told about — which means runs
dispatched through `vaibify-do`. A script you launch yourself in a
shell is invisible to the dashboard as a running step.

## The one rule

**Run steps through `vaibify-do`, never by executing a step's script
directly in a terminal.**

- `vaibify-do run-step A09` — run one step.
- `vaibify-do run-selected-steps A09 A10 A11` — run several. Prefer
  this to a loop of `run-step` calls or (worse) a loop of bare
  `python …` invocations: it is one dispatch, and the dashboard shows
  the run advancing through each step.
- `vaibify-do run-from-step A05` — rerun from a step onward.
- `vaibify-do run-all` — the whole pipeline.

A dispatched run lights the step's status marker orange while it runs
and records its result; the researcher sees it happen. That visibility
is the entire reason to prefer `vaibify-do` over running the script
yourself.

## Why a direct run is a problem

If you run `python dataFoo.py …` in a shell instead:

- The dashboard shows **no running step** — the researcher has no live
  signal that anything is happening, and may think the container is
  idle or stuck.
- The only thing they eventually see is dependent steps flipping
  **stale** on the next poll, once your new outputs land. That is a
  delayed, indirect signal — and it is *stale*, never *failed* (a
  failure requires a test to actually fail).
- That staleness flip only reaches dependents whose dependency is
  declared as a `{step:<id>.<stem>}` token. A hidden hardcoded path
  produces no edge and no flip, so the dashboard stays silently green.

**If you genuinely must run something directly** — a one-off probe, a
debug — tell the researcher in chat exactly what you ran and on which
step. The dashboard cannot show it for you.

## Editing the project, not just running it

Change the project only through `vaibify-do` actions
(`create-step`, `update-step`, `reorder-steps`, …). Never hand-edit an
existing `project.json` or `state.json` with `sed`/`Edit`/an editor:

- Direct edits bypass the host save path — step-label recomputation,
  positional→symbolic token normalization, and reload detection — and
  reintroduce drift (a bad reorder edit once left every step's label
  blank).
- `update-step` carries a compare-and-swap fingerprint, so a
  concurrent dashboard edit cannot be silently clobbered. Read the
  current fingerprint from `vaibify-do resolve-commands` (which also
  dry-runs the whole graph so you can verify a rewire before running
  anything), pass it as `sBaseFingerprint`, and retry on a 409.

The one sanctioned exception is *creating a brand-new* project in
toolkit mode, where no `create-project` action exists yet — writing a
fresh `.vaibify/projects/<slug>.json` is expected there.

## Honesty

Never leave the researcher guessing whether compute is happening.
Prefer the dispatched path so the dashboard tells the truth on its
own; when you cannot, say so in chat. Do not treat "the dashboard
didn't show it" as "it didn't matter."
