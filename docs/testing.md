# Testing

Vaibify's test suite has **three kinds of test**, distinguished not by
where they live (they are all `pytest` tests under `tests/`) but by the
question each one answers.

| Kind | Question it answers | Where |
|---|---|---|
| **Unit / behavior tests** | Does input *X* produce output *Y*? | `tests/` |
| **Architectural invariants** | Is the codebase wired together the way it must be? | `tests/testArchitecturalInvariants.py` |
| **Falsification tests** | If a safety guard broke, would any test *notice*? | marked `@pytest.mark.falsification` across `tests/` |

Counts are deliberately not written here. They were, and they went
stale: this table claimed ~146 falsification tests long after the real
number passed 290, which is the same prose-drifts-from-code failure the
falsification tests themselves exist to catch. The live numbers are on
the README badges, refreshed by `badges.yml` on every push to `main`.
To count them yourself:

```bash
python -m pytest tests/ -m "not docker and not docker_live" --collect-only -q | grep -c "::"
python -m pytest tests/testArchitecturalInvariants.py --collect-only -q | grep -c "::"
python -m pytest -m falsification --collect-only -q | grep -c "::"
```

The first two are conventional. The third is the one that needs
explaining.

## Why falsification tests exist

A passing test suite proves that the tests and the code **agree** — not
that the code is **correct**. When an AI agent writes the code, the
tests, *and* the review, a single blind spot can author all three, and a
green suite can hide a serious bug. (This is not hypothetical: a refactor
once passed the entire suite while carrying a defect that would have
broken every real session, because the fixtures used a degenerate input
and never drove the real path.)

A **falsification test** is the software equivalent of a laboratory
**negative control**. An ordinary test is a positive result: "given good
code, the answer is right." A falsification test additionally proves the
*negative control*: "given deliberately **broken** code, the test
**fails**." A test that stays green when its guard is sabotaged is an
assay with no working negative control — it would never catch the real
bug either.

Concretely, a falsification test is **kill-confirmed**: it has been
proven to *fail* when a specific one-line mutation is applied to the code
it defends, then to pass again once that mutation is reverted.

## How the falsification suite is built

Four pieces make "every falsification test still has teeth" an
enforceable, re-checkable guarantee:

1. **The marker.** Falsification tests carry `@pytest.mark.falsification`.
   Dedicated files (`tests/test*MutationCoverage.py` and the tier-1
   dedicated files) mark every test via a module-level
   `pytestmark = pytest.mark.falsification`; files that mix falsification
   tests with ordinary unit tests mark only the falsification ones with a
   per-test decorator. Run just this class with `pytest -m falsification`.

2. **The `Kills:` docstring line.** Every falsification test names, in its
   docstring, the exact mutation it is proven to catch.

3. **The registry** — `tests/falsificationRegistry.py` — records that
   mutation in a *machine-applicable* form: one
   `Falsification(nodeid, source, old, new)` entry per test, where `old`
   is the exact text to replace and `new` is the break.

   `old` must occur exactly `iExpectedOccurrences` times (default 1). A
   guard that is deliberately checked in more than one place — the
   ownership-transfer conditions run once before anything is minted and
   again at the commit point — needs **every** copy mutated: disabling
   one changes nothing a caller can observe, so the entry reports
   SURVIVED and reads as an undefended guard. Stating the count makes a
   copy appearing or vanishing an error rather than a quiet
   half-mutation.

4. **The re-kill harness** — `tools/reconfirmFalsification.py` — is the
   standing negative control. For every registry entry it requires the
   test to pass on clean code, applies the mutation, requires the test to
   then fail with a genuine assertion failure (a compile error or an
   unrelated failure does **not** count), and restores the source. It
   reports any marked test with no entry and exits nonzero on any gap. It
   mutates source, so it is deliberately **not** collected by
   `pytest tests/`; run it directly:

   ```bash
   python tools/reconfirmFalsification.py
   ```

   Entries whose test drives a real container (`docker_live`) cannot be
   judged on a host with no Docker daemon: the harness demands a daemon
   for every run it judges, precisely so a skip is never miscounted as a
   surviving mutant, and that same demand turns those entries into
   errors where no daemon can exist. They are reported by name as NOT
   EVALUATED and left out of the denominator instead. That is safe only
   because they *are* judged wherever a daemon exists, so the Linux CI
   legs set `VAIBIFY_REQUIRE_DOCKER_DAEMON`, which refuses the deferral
   outright — losing Docker there turns the lane red rather than
   silently shrinking what it reports against.

Three architectural invariants keep the class from silently decaying:
`testFalsificationFilesDeclareMarker`,
`testFalsificationTestsRecordTheKilledMutation`, and
`testFalsificationRegistryIsWellFormed`.

### The independent-oracle rule (important)

Kill-confirmation proves a test is **sensitive** to change; it does
**not** prove the test's asserted value is **correct**. If a test is
written against code that is itself buggy, its oracle freezes the bug —
and the test will still catch a deliberate break, so it passes
kill-confirmation while certifying the wrong answer. A falsification test
is therefore trustworthy only when its expected value is derived
**independently of the code** (a specification, an analytic result, a
conservation law, a published benchmark) **and** it is kill-confirmed.
Neither condition alone is enough. This rule lives in the
`falsificationRegistry.py` docstring; do not weaken it.

## Falsification testing vs. the mutation gate — two different jobs

Both use mutation testing, but they point at different things, and the CI
runs them as two separate workflows:

| | **Falsification** (`falsification.yml`) | **Mutation gate** (`mutation.yml`, cosmic-ray) |
|---|---|---|
| What it mutates | the guards our *existing* falsification tests already defend | the code a branch changed against a chosen base |
| What it answers | "do our existing guard-tests still catch their known breaks?" | "did this branch add a guard with **no** defending test?" |
| Direction | backward-looking — maintains the committed suite | forward-looking — discovers new gaps |
| When it runs | automatically, on every pull request | **manually only** — see below |
| On failure | **fails the job** (a guard lost its test) | **warns only** — never fails the build |

**The mutation gate is manual, and that is a real gap.** It triggers on
`workflow_dispatch` only. It used to run per-PR, but mutation-testing a
large feature-branch diff exceeded the 60-minute ceiling and was
cancelled before it could post any signal — an advisory gate that dies
on the PRs that matter most is pure friction. So it was made on-demand
(commit `94abe35`), which means **Python can merge with no mutation
feedback at all**. Falsification and the architectural invariants are
still graded for real on every PR; the mutation gate is not.

Run it deliberately from the Actions tab or with
`gh workflow run mutation.yml`, choosing `base_ref` (default `main`) and
`max_mutants` (default 300, `0` = uncapped). Any mutants dropped by the
cap are reported, never silently discarded.

**Why warn-only.** Mutation testing inevitably produces *equivalent
mutants* — code changes with no observable effect (e.g. reordering a
commutative comparison) — that *no* test could ever catch, so failing
the build on every survivor would cry wolf and train everyone to ignore
it. Surviving mutants are surfaced as `::warning::` annotations on the
changed lines and as a job-summary table (module, line, operator,
function). The sticky-PR-comment step is still in the workflow but is
inert while the trigger is manual: it is guarded on
`event_name == pull_request`, kept only so re-enabling the per-PR mode
is a one-line change.

**Do the two gates overlap?** Barely, and by design. They mutate
*different* sets of lines: falsification re-checks only lines that already
carry a committed falsification test, while the mutation gate touches only
lines a branch *changed*. The two intersect just when a branch edits an
already-guarded line — where the double coverage is harmless. Otherwise
they are complementary: falsification stops old guarantees from decaying,
the mutation gate flags new code that arrived without a guarantee.

## Running the suites locally

```bash
pip install -e ".[dev]"

# everything (unit + invariants + falsification tests):
pytest tests/ -m "not docker and not docker_live"

# just the falsification tests:
pytest -m falsification

# just the architectural invariants:
pytest tests/testArchitecturalInvariants.py

# the standing negative control (re-break each guard, confirm it's caught):
python tools/reconfirmFalsification.py

# the mutation gate on a module, for the curious (heavier; separate extra):
pip install -e ".[mutation]"
cosmic-ray init cosmic-ray.toml session.sqlite && cosmic-ray exec cosmic-ray.toml session.sqlite && cr-rate session.sqlite
```

## Continuous integration

Every workflow runs **either** before a merge or after it, never both.
The test suites gate the merge; documentation, badges and distributions
are built from `main` once the merge has happened. Until 2026-07-28 six
workflows did both, so the whole suite ran a second time on the merge
commit, where its answer could no longer change anything.

Branch protection is what makes the pre-merge half sufficient. It is the
reason the test workflows no longer need a `push: [main]` trigger, and
it is why `main` is not left unverified by their absence. That claim
holds only while the ruleset also requires branches to be **up to date**
before merging (enabled 2026-07-29): without it, two individually-green
pull requests that conflict semantically can both merge on stale checks,
and the broken merge commit runs no CI at all — the exact safety net the
removed `push: [main]` duplication used to provide.
`tools/syncRequiredChecks.py --apply` re-asserts the up-to-date
requirement on every run, so a UI change cannot silently reopen the
window.

**Before a merge — these decide whether a change may land:**

| Workflow | Runs | Matrix |
|---|---|---|
| `tests-linux.yml` / `tests-macos.yml` | the full `pytest` suite (incl. invariants and falsification tests) | Ubuntu 22/24 + macOS 15/26 × Python 3.9–3.14 |
| `falsification.yml` | the invariants, the falsification tests, and the re-kill harness | a representative subset (Ubuntu + macOS × Python 3.9 & 3.14) |
| `browser.yml` | the dashboard in real Chromium against a real uvicorn hub | on pull requests (one Linux/Python/Chromium cell) |
| `agentDocsPathCheck.yml` | that every path referenced in an `AGENTS.md` resolves | one Linux cell |

**After a merge — these publish what `main` now is:**

| Workflow | Runs | Matrix |
|---|---|---|
| `docs.yml` | the Sphinx build (`-W`), published to `gh-pages` | one Linux cell |
| `badges.yml` | recomputes the live test / falsification / invariant counts | one Linux cell |

**When a version is cut:**

| Workflow | Runs | Matrix |
|---|---|---|
| `pip-install.yml` | builds the sdist and wheel, runs `tools/checkInstalledDistribution.py` against each, then uploads to PyPI | the full support matrix on a release; the corners on a manual run |

This matches `vspace`, `bigplanet` and `multi-planet`, whose
`pip-install.yml` is likewise `release`-only.

The cost is that a packaging regression can sit on `main` until the
next version is cut. What makes that acceptable is that `upload_pypi`
needs `build` and `test`, so the break is caught while cutting the
release and blocks the upload — nothing broken is published, but the
diagnosis lands during a release rather than beside the change that
caused it. After touching packaging, `vaibify/resources.py`, the
template tree or the Dockerfile `COPY` set, run `pip-install` by hand
(`workflow_dispatch`) rather than waiting for release day.

```{warning}
Never add `pip-install` to the required status checks for `main`. It
does not run on pull requests, so a required check by that name can
never report and every PR waits on it forever. This happened the day
the split landed: two `pip-install` job names
(`Test py3.9 on macos-26`, `Test py3.14 on macos-26`) were left in the
branch ruleset and blocked an otherwise fully green pull request.
```

### Check names

A ruleset matches checks by **job name**, and the required-checks picker
searches those names — not workflow names. So the name is not cosmetic:
an unfindable name is an unprotected lane.

The scheme is `<test-type>:<os>:python-<version>` for anything that
varies across the matrix, and a bare noun for anything that does not:

| Check | Lane |
|---|---|
| `unit:ubuntu-22.04:python-3.9` … `unit:macos-26:python-3.14` | `tests-linux`, `tests-macos` |
| `falsification:ubuntu-24.04:python-3.9` … | `falsification` |
| `results:<os>:python-<version>` | the test-results report published by `tests-linux` |
| `browser` | `browser` |
| `invariants` | `tests-linux` |
| `docker-smoke` | `tests-linux` |
| `agent-docs` | `agentDocsPathCheck` |

Two failures forced this, both invisible in the workflow file. `browser`'s
job was called `frontend (chromium)`, so searching "browser" returned
nothing and the entire lane sat unprotected while appearing to gate
every pull request. And `falsification` reused the tests matrix
template, so `ubuntu-24.04:python-3.14` was emitted by two workflows —
requiring it is satisfied by whichever reports, gating neither.
`testNoTwoMergeGateLanesProduceTheSameCheckName` now fails if any name
has two owners.

`invariants` exists purely to give the architectural invariants a name.
They already run inside every matrix leg; the separate job adds about
ten seconds and a check a reviewer can actually see.

### Required status checks

**Requiring only some checks lets a pull request merge while the rest
are still running** — GitHub blocks on required checks alone. Derive the
full set from the workflows rather than picking names by hand:

```bash
python tools/syncRequiredChecks.py           # print, change nothing
python tools/syncRequiredChecks.py --apply   # write the ruleset
```

```{warning}
Renaming a job invalidates the ruleset entry that named it: the old name
stops being reported and every pull request waits forever on a check
that no longer exists. Run `syncRequiredChecks.py --apply` **before**
merging a rename, not after — otherwise the renaming pull request is
itself blocked by the names it is replacing.
```

`results:*` is deliberately excluded from the required set: it reports
the same run the matching `unit:` job already gates, so requiring both
doubles the wait for no extra signal.

**On their own schedule — neither gate nor publisher:**

| Workflow | Runs | Matrix |
|---|---|---|
| `mutation.yml` | the cosmic-ray gate on a branch's changed lines (warn-only) | manual (`workflow_dispatch`) |
| `containerAcceptance.yml` | the modelled container commands, against a real container | nightly + manual |
| `freshImageBuild.yml` | a full image build from scratch, then acceptance | weekly, manual, and on `vaibify/containerImage/**` pull requests |

`tests/testWorkflowMergeGateSplit.py` fails if any workflow drifts back
into running on both sides of the merge.

### What the README badges mean

Two different mechanisms, which fail in different ways:

**Count badges** (`unit tests`, `falsification tests`, `architectural
invariants`, `browser tests`) are computed by `badges.yml` after every
merge, by *collecting* the suite rather than running it. They say how
much test there is, never whether it passed. A count that collected
zero fails the workflow instead of publishing, because zero is never a
true answer here — it means a marker was renamed or a directory moved,
and the badge would otherwise state that absence as fact.

**Merge-gate status badges** (`tests-linux`, `tests-macos`,
`falsification`, `browser`, `agent-docs-path-check`) report the checks
that gated **the last merge into `main`**. `badges.yml` resolves them
after each merge: the merge commit names the pull request it came from,
that pull request's head commit carries the runs that decided the merge
was allowed, and their conclusions become the badges. Nothing is
re-run — the merge gate already ran them, and re-running post-merge is
the duplication this split removed.

GitHub's own workflow badges are deliberately *not* used for these
lanes. They show the newest run of a workflow on **any** branch, so a
contributor's failing pull request would redden the README while `main`
is perfectly healthy. (That is genuinely how they behave, and it is not
what the docs imply: on a workflow with only pull-request runs,
`?branch=main` renders *no status* while the unqualified badge renders
the latest PR run.)

A lane with no run against the merged pull request renders **did not
run** in grey, never green. That is the case worth having: it is what a
bypassed merge, a skipped lane, or a workflow that silently stopped
triggering looks like.

On a direct push to `main` — no pull request to resolve — the status
badges are left exactly as they were, because an absence of information
is not a change of state. They continue to describe the last real
merge.

The two **scheduled** lanes keep GitHub's own badges, because they
really do run on `main` on a timer, so "the latest run" is `main`'s
state. They are on the README because nobody watches a nightly or
weekly run: for `containerAcceptance` and `freshImageBuild` the badge
is realistically the only place a failure becomes visible.

## The three execution lanes

Most of this suite runs in one process with the Docker daemon and the
browser both absent. Three lanes exist because that leaves two real
boundaries unexercised, and both have shipped bugs a green suite could
not see.

**The browser lane (`browser.yml`)** loads the real dashboard in real Chromium
against a real uvicorn hub and fails on any console error, uncaught
promise rejection, or failed asset. It runs on one cell — a browser
journey does not become more trustworthy by running 24 times across
the OS/Python matrix. Its Docker adapter is a **fail-closed fake**:
every command it answers is declared in `LIST_MODELLED_COMMANDS`, and
anything else raises rather than returning a default. That rule exists
because this suite already carries ~20 permissive Docker mocks, one of
which answers success to any command it does not recognise.

**The container-acceptance lane (`containerAcceptance.yml`)** puts each of those modelled
commands to a real container, so a fake that drifts from the daemon is
caught rather than believed. Every entry in the fake's contract names
an assertion in `tests/testContainerAcceptance.py`, and
`testEveryNamedLaneTwoAssertionExists` fails if one of those names is
fiction. It runs nightly, which means **drift is caught up to a day
late**: the browser lane failing blocks merge, container acceptance blocks the next
release, not retroactively.

**The fresh-image lane (`freshImageBuild.yml`)** builds the image from scratch. Lane
2 reuses a cached image keyed by `tools/computeBuildInputHash.py` —
which hashes every build input, including the entrypoint, the agent
CLI, the overlays, the skills, the staged-doc *sources*, and the
generator itself — so it says nothing about whether the image still
builds.

None of the three may skip itself green. `VAIBIFY_REQUIRE_DOCKER_DAEMON`
and `VAIBIFY_REQUIRE_BROWSER` turn each lane's convenience skip into a
failure in CI, because the guard they replaced (`docker info || exit 0`)
reported success for having run nothing.

The harness runs on a *subset* because whether a test catches its
mutation is deterministic and OS/Python-independent; the full-matrix
coverage of the tests themselves already comes from the unit-test
workflows. The count badges in the README are refreshed by `badges.yml`,
which writes shields.io endpoint JSON to an orphan `badges` branch.

## Background

The falsification methodology, its limits, and the literature it draws on
(mutation testing since DeMillo, Lipton & Sayward 1978; the LLM-era work
on test-suite adequacy; metamorphic testing for oracle-free scientific
code) are written up for reference outside the repository. In short:
mutation-style falsification fits vaibify's *plumbing*, where the correct
behavior is definitional; the science code (vplanet), where there is no
known answer to assert against, is better tested with **metamorphic
relations** (e.g. "halve the timestep and the conserved energy must not
change") — a future direction, not yet part of this suite.
