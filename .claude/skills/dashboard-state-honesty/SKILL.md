---
name: dashboard-state-honesty
description: Rules that keep the dashboard from overstating what vaibify knows: a row renders a gate's verdict rather than re-deriving it, a level cell and its rows must fail on the same set, an uncheckable remote is never red, and a configured secret this host cannot resolve degrades loudly. Use when editing a requirement row, a level cell, a badge, or any poll payload the frontend renders.
---

# The dashboard tells the truth or says it does not know

The GUI is the researcher's ground truth. Each contract here exists
because a screen once contradicted the machinery behind it while every
component was internally consistent -- which is the failure mode that
no unit test catches.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

**A configured secret this host cannot resolve DEGRADES, and the
telling is the load-bearing half.** `flistMountSecrets` skips it and
the container starts (ruled 2026-09-05, making the Features page's
"the container will still work but git push will fail" true). All three
methods used to RAISE, before `docker run` was ever composed, so no
container existed to inspect — a researcher met that hours after a
wizard toggle with a `RuntimeError` naming neither the secret nor the
remedy. A silent degrade would be strictly worse than that refusal, so
three surfaces report it from one authority
(`vaibify/config/secretAvailability.py`): the CLI preflight, the hub
log at start, and the dashboard's readiness banner. The dashboard's copy is
RECOMPUTED per settled readiness answer, never remembered — a
researcher who runs `gh auth login` afterwards has fixed the thing it
complains about — and only on the SETTLED answer, because the frontend
polls readiness sixty times while a container boots and `gh auth token`
is a subprocess. Never materialize a secret to discover that it exists;
`fbSecretExists` is written not to.

**A requirement row renders the gate's VERDICT, never re-derives it.**
The Reproducibility-rules row computed "declared" in JavaScript as
"the `dictDeterminism` block is non-empty", while
`fbWorkflowDeclaresDeterminism` required a `true` waiver or a pinned
thread count. The declare form writes `{bAcceptBlasVariance: false}`
when submitted with nothing ticked — non-empty, and a declaration of
nothing — so the row went green, the L3 verify refused, and every
component was internally consistent while the screen contradicted the
machinery (researcher-reported, 2026-08-30). The poll now ships
`bDeterminismDeclared` and `listDeterminismIssues`; the row renders
them. **A mirrored predicate in JS is a second authority on a question
that has one** — the slug mirror in `scriptUtilities.js` is tolerated
only because it is display-only with the backend enforcing. Two
corollaries, both from the same report: a reason must distinguish the
shapes it describes ("no block" and "a block that pins nothing" read
identically, and one was false), and a refusal names its cause rather
than pointing at a tab. Guarded by
`tests/testDeterminismRowMatchesItsGate.py` and
`tests/browser/testDeterminismRowFollowsTheGate.py`.

**A level CELL and the rows beneath it must fail on the same set.**
The Project header's L2 cell counts criteria from a fixed tuple
(`_T_WORKFLOW_LEVEL2_BASE_CRITERIA`) and INTERSECTS the live blocker
list against it — so a criterion the gates emit but the tuple omits is
silently dropped and the cell over-reports. That shipped: the tuple
listed only `*-verify-stale` and not `not-in-*`, so a fresh verify that
proved published files DIFFERED painted a check above two orange
Published-copies rows (researcher-reported, 2026-08-30). The scalar
gate `_fbComputeLevel2` was correct throughout, which is what makes
this class nasty — the display disagreed with itself and only the
display was wrong. **When you add or rename an L2/L3 blocker criterion,
check whether the workflow-scope tuple should carry it**; a criterion
absent from the tuple is invisible to the header, not merely
uncounted. `tests/testProjectHeaderNeverOutranksItsRows.py` is the
kill-confirmed guard, and it also pins the opposite error: the two
halves of one remote's check are mutually exclusive by construction, so
neither may charge a service twice.

**A remote badge pulses while vaibify is asking, and a failed ask is
never red.** Opening a project re-checks every CONFIGURED remote
(`POST /api/workflow/{id}/remotes/refresh`), the poll REPORTS where
each check has got to (`dictRemoteChecks`), and the badge pulses until
its own answer arrives. Four things hold that honest, and each is a way
to turn it back into a lie:

- A check that could not complete settles UNCHECKABLE with a reason.
  Red means *diverged* — a claim about the remote nobody earned — and
  the cached record on disk stays untouched
  (`scheduledReverify.fdictAttemptOneVerify` writes only on success; do
  not add a write beside it).
- Which remotes pulse comes from
  `scheduledReverify.flistSelectConfiguredServices`, the predicate the
  scheduled loop skips on. A remote absent from `dictRemoteChecks`
  renders exactly as it did before any of this existed.
- A check in flight moves no colour. It has compared nothing.
- The CHECKING timeout is evaluated when the state is READ, never on a
  timer: the failure it covers is a worker that never returns, and such
  a worker cannot clear its own flag.

The refresh cannot move into the poll —
`_fdictBuildWorkflowEnvelopeDetail` is built with no extra container
execs and no network I/O. `docs/architecture.md` carries the model and
the one accepted residual (a Run Step in the first seconds after open
is refused by name while the checks hold the drain).
`tests/testRemoteBadgeRefresh.py` and
`tests/browser/testARunningRemoteCheckPulsesTheBadge.py` are the
kill-confirmed guards; the browser one reads `animationName` off a live
element, because asserting the CSS class alone passes against a
stylesheet with no rule in it.
