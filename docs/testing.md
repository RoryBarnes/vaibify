# Testing

Vaibify's test suite has **three kinds of test**, distinguished not by
where they live (they are all `pytest` tests under `tests/`) but by the
question each one answers.

| Kind | Question it answers | Where | Count |
|---|---|---|---|
| **Unit / behavior tests** | Does input *X* produce output *Y*? | `tests/` | ~6,500 |
| **Architectural invariants** | Is the codebase wired together the way it must be? | `tests/testArchitecturalInvariants.py` | ~52 |
| **Falsification tests** | If a safety guard broke, would any test *notice*? | marked `@pytest.mark.falsification` across `tests/` | ~146 |

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
   is the exact text that occurs once in the source file and `new` is the
   break.

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
| What it mutates | the guards our *existing* falsification tests already defend | the *newly changed* code in a pull request |
| What it answers | "do our existing guard-tests still catch their known breaks?" | "did this PR add a guard with **no** defending test?" |
| Direction | backward-looking — maintains the committed suite | forward-looking — discovers new gaps |
| On failure | **fails the job** (a guard lost its test) | **warns only** — never fails the build |

**Why warn-only, and how you are alerted.** The mutation gate never puts
a red ✗ on a pull request. Mutation testing inevitably produces
*equivalent mutants* — code changes with no observable effect (e.g.
reordering a commutative comparison) — that *no* test could ever catch,
so failing the build on every survivor would cry wolf and train everyone
to ignore it. Instead the gate reports each surviving mutant three ways, on
the pull request itself:

- a **sticky PR comment** — a single comment the gate updates in place on
  every run (never a new one each time), listing every surviving mutant;
  it is the hardest signal to miss and reflects the latest run, including
  a clean "all killed" state,
- an inline **`::warning::` annotation** on the exact changed line, shown
  in the PR's *Files changed* tab, and
- a **job-summary table** on the workflow run (module, line, operator,
  function).

So the signal is "here are the newly changed lines a test would not have
caught if they were wrong" — a review prompt, not a pass/fail verdict.
Read it on any PR that changes Python under `vaibify/`; a clean run says
so explicitly ("All N mutant(s) on the changed lines were killed"). The
comment is best-effort: a pull request from a fork gets a read-only token
and cannot be commented on, in which case the annotations and job summary
still appear.

**Do the two gates overlap?** Barely, and by design. They mutate
*different* sets of lines: falsification re-checks only lines that already
carry a committed falsification test, while the mutation gate touches only
lines a pull request *changed*. The two intersect just when a PR edits an
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

| Workflow | Runs | Matrix |
|---|---|---|
| `tests-linux.yml` / `tests-macos.yml` | the full `pytest` suite (incl. invariants and falsification tests) | Ubuntu 22/24 + macOS 15/26 × Python 3.9–3.14 |
| `falsification.yml` | the invariants, the falsification tests, and the re-kill harness | a representative subset (Ubuntu + macOS × Python 3.9 & 3.14) |
| `mutation.yml` | the cosmic-ray gate on a PR's changed lines (warn-only) | on pull requests |
| `badges.yml` | recomputes the live test / falsification / invariant counts | on push to `main` |

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
