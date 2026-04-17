# Vibe coding with verification

Vaibify's tagline is *Vibe boldly. Verify everything.* This document
is about the second half. It describes a methodology for writing
agent-facing documentation so that an AI coding agent can contribute
safely to a scientific software repository, and so that the
documentation itself never drifts away from the code it describes.

The principles are repository-agnostic. Vaibify is used as a worked
example because it is a mid-sized Python and JavaScript codebase with
more than two thousand unit tests, frequent refactors, and several
collaborators of mixed software backgrounds. The same patterns apply
to `alabi`, `vplot`, `vspace`, `multiplanet`, `bigplanet`, `vconverge`,
and `vplanet_inference`, and to any scientific codebase with a similar
shape.

This is a methodology guide, not a quick reference. Expect to read it
in one sitting, then come back to individual sections when you are
setting up a new repository.

## 1. Motivation: why agent docs drift

AI coding agents are stochastic. The same prompt, run twice, can
produce different code, different file names, and different imports.
This is not a defect of any particular model; it is a property of how
these systems sample from a distribution of plausible completions.

Scientific software tolerates stochasticity poorly. A simulation that
gives a slightly different answer each time it runs is not a
simulation, it is a hazard. The same is true for a codebase: if an
agent silently rewrites a helper function the wrong way on Tuesday
because Monday's prompt happened to produce a different result, a
human reviewer will not catch it until a test fails months later. The
most dangerous mistakes are the ones that are plausible.

The natural response is to write more documentation. Tell the agent
where everything lives, what the modules are called, how many files
are in each package, what the imports look like. This works for one
afternoon. Then somebody refactors a module, or adds a new route, or
splits a file in two, and the documentation quietly becomes wrong.

Vaibify's previous `CLAUDE.md` hard-coded line counts for more than
thirty-five Python modules. Within a few weeks the counts were off by
ten to forty percent, and a newly added `scriptReposPanel.js` module
was missing entirely from the map. An agent reading that file had two
bad choices: trust the stale data and generate confidently wrong code,
or ignore the map and re-derive the architecture from scratch every
session, producing inconsistent results. Neither is acceptable in a
scientific setting where reproducibility is a primary value.

The root cause is that the documentation was trying to do two jobs at
once: it was stating rules that cannot be tested, and it was reciting
facts that should never have been typed by hand. Untangling those two
jobs is the core of the methodology.

## 2. Deterministic versus stochastic documentation

There are two fundamentally different kinds of content in any
architecture document.

**Deterministic signals** are facts that the code unambiguously is:
the list of modules in a package, the symbols each module exports,
the type of an argument, the presence or absence of a test, the
result of running a linter. Machines extract these reliably. They
cannot drift relative to the code because they are derived from the
code. If you write them down by hand, you are creating a second
source of truth that is guaranteed to diverge.

**Stochastic signals** are rules, contracts, intents, hazards, and
invariants that span multiple files. They cannot be extracted from
any single file or from any mechanical scan. They have to be written
by humans who know the system. They are load-bearing *precisely
because* an agent cannot infer them from reading the code.

Examples of stochastic signals in vaibify:

- "Container paths use `posixpath`; host paths use `os.path`."
- "Never reassign `setExpandedSteps`; mutate it in place."
- "`director.py` intentionally duplicates two functions from
  `workflowManager.py` because it operates on host filesystem paths."

None of those rules are visible by reading any one file. All three
have caused real bugs when an agent or a new developer ignored them.

A documentation system that mixes these two categories in one
hand-written file gets the worst of both worlds. The deterministic
parts drift, training the reader to distrust the file. The stochastic
parts, which are the whole point, get buried under rotting module
maps and stale line counts.

The analogy to scientific computing is almost too on-the-nose.
Deterministic components of a physical model (conservation laws,
boundary conditions, unit conversions) are handled by code and
checked by tests. Stochastic components (priors, parameter ranges,
stopping criteria) are handled by the researcher. You do not ask
your simulation to invent its own priors and you should not ask your
documentation to invent its own module map.

## 3. The four-layer framework

Vaibify's agent documentation is organized into four layers. Each
layer has a different trigger (when the agent sees it), a different
source of truth, and a different failure mode. Keeping them separate
is what makes the whole system stable.

### Layer 1: always-on, semantic

Short prose files stating rules that cannot be tested. These live
at `AGENTS.md` in the repository root, with nested `AGENTS.md` files
in subtrees that have their own conventions (for example,
`vaibify/gui/AGENTS.md` for the FastAPI backend and
`vaibify/gui/static/AGENTS.md` for the JavaScript frontend).

These files are loaded every turn of an agent session. They should
contain only content that is load-bearing and cannot be expressed as
a test: style contracts, the handful of cross-cutting rules a
newcomer would miss, the traps listed in section 5.

**Failure mode:** silent. When a Layer 1 file is wrong, the agent
trusts it and produces subtly wrong code. This is why Layer 1 should
stay small.

### Layer 2: enforced, deterministic

Architectural invariants expressed as pytest assertions. A single
file like `tests/testArchitecturalInvariants.py` contains tests that
assert things like "no module under `vaibify/gui/routes/` imports
from `pipelineServer`" or "every route module defines
`fnRegisterAll`" or "no JavaScript file in `static/` exceeds two
thousand lines".

These cannot drift. When the rule changes, the test changes in the
same commit. The test name, docstring, and assertion together *are*
the rule, which means the rule is self-documenting and executable at
the same time.

**Failure mode:** loud. When a Layer 2 invariant breaks, CI turns
red. This is exactly what you want.

### Layer 3: on-demand, deterministic

Discovery scripts that extract structural facts from the current
code. Vaibify ships `tools/listModules.py`, which walks the package
with Python's `ast` module and prints the current module map — path,
public symbols (from `__all__`), and a one-line purpose from each
module's docstring. Line counts and import edges are deliberately
excluded as drift bait; if an agent needs either, it should run a
targeted tool at the moment of need rather than read a persisted
summary. An agent runs this script when it needs the current state of
the codebase.

Nothing is persisted. The output is regenerated on every invocation
from the live source tree, so it cannot be stale. Other examples
include a script that lists all pytest markers, a script that
prints the route graph, or a grep that enumerates TODOs.

**Failure mode:** rare. When a discovery script is wrong, it fails at
the moment of use and the agent notices immediately.

### Layer 4: conditional, semantic

Multi-step recipes for recurring tasks, loaded only when the task
matches. Anthropic Skills (`.claude/skills/*/SKILL.md`), Cursor
rules, and similar tool-specific formats belong here. A skill might
encode the steps for adding a new module, reviewing a pull request,
or running a security audit.

**Failure mode:** silent, like Layer 1, because skills are prose. Use
sparingly and only for genuinely recurring multi-step work. A skill
that fires on every task is just Layer 1 wearing a costume.

### Summary

| Layer | Trigger       | Source of truth      | Failure mode |
|-------|---------------|----------------------|--------------|
| 1     | Every turn    | Hand-written prose   | Silent       |
| 2     | Every commit  | The assertion itself | Loud (CI)    |
| 3     | On demand     | The live source tree | At point of use |
| 4     | Task match    | Hand-written prose   | Silent       |

The important property is that Layers 2 and 3, which cannot drift,
carry most of the deterministic content, while Layers 1 and 4,
which can drift, carry only the irreducibly stochastic content.

## 4. The scoping test

When you are tempted to add content to an agent-facing document, run
it through four questions in order.

1. **Would a new developer, reading the code alone for twenty
   minutes, miss this?** If the answer is no, do not write it at all.
   A document full of content that a careful reader could have
   inferred trains its readers (human and agent) to skim.

2. **Can the rule be expressed as an assertion on the code?** If yes,
   it belongs in Layer 2. Promote aggressively. Tests are the only
   documentation artifact that cannot lie.

3. **Is the fact extractable from the code?** If yes, it belongs in
   Layer 3. Write a script, do not persist the output. If you catch
   yourself typing a module list or a line count, stop.

4. **Otherwise, is it a single-step rule or a multi-step recipe?**
   Single-step rules go in Layer 1. Multi-step recurring recipes go
   in Layer 4.

The common failure is to skip straight to Layer 1 for everything,
because prose is easy to write and tests are hard. Resist this. The
cost of writing a test once is far less than the cost of a stale
paragraph misleading an agent every day for a year.

## 5. Traps over rules

The highest-value prose content in an `AGENTS.md` is almost never a
list of rules. It is a list of *traps*: places where the code does
the opposite of what a careful reader would expect, or where two
things look alike but behave differently.

Traps are what a new contributor cannot discover by reading
carefully. They have to be told. A good trap entry names the two
things that look alike, says which one does what, and gives the
consequence of getting it wrong.

Examples from vaibify:

- Container paths use `posixpath`; host paths use `os.path`.
  `workflowManager.py` and `director.py` contain similarly named
  functions because one operates on container paths and the other
  on host paths. Using the wrong one silently produces wrong file
  paths on Windows, or on any host where the separator differs.

- `director.py` looks like a CLI helper but is actually a parallel
  workflow runner with its own variable resolution. Do not unify it
  with `workflowManager.py` without understanding why the two
  resolve variables differently.

- `_dictUiState` contains several `Set` objects. These sets are
  captured by reference in the render closure. Reassigning a set
  (`_dictUiState.setExpandedSteps = new Set()`) silently breaks the
  render, which continues to read the old set. Always call
  `.clear()` instead.

- `introspectionScript.py` duplicates format-handling logic from
  `dataLoaders.py`. This is not a refactoring opportunity; the
  introspection script runs inside Docker containers that cannot
  import from the host environment.

The exercise, when starting an `AGENTS.md`, is to ask: what are the
five mistakes I would be most annoyed to see an agent make in this
repository next week? Write those. Everything else can wait.

## 6. The feedback principle

When an agent makes the same mistake twice, the documentation system
should absorb the lesson. There are three ways to do that, in order
of preference.

First, promote the mistake into a Layer 2 test. If the mistake is
"an agent keeps importing from `pipelineServer` in a route module,"
add an architectural invariant that fails when any route module
imports from `pipelineServer`. The mistake becomes a permanent guard
rail; no future agent can repeat it without turning CI red.

Second, if the mistake cannot be tested, add it to the `Lessons`
section of the relevant `AGENTS.md`. One paragraph per lesson. State
the mistake, state the correct behavior, state the consequence of
getting it wrong.

Third, if the mistake is a multi-step task being done inconsistently,
add a Layer 4 skill.

Without this loop, documentation stagnates at initial quality. The
test-promotion path is the most valuable because it converts
stochastic prose (which can be ignored) into deterministic enforcement
(which cannot). Over time, a repository that uses the feedback loop
well will find that most of its Layer 1 content slowly migrates into
Layer 2 tests, leaving behind only the rules that genuinely resist
mechanization.

## 7. Tradeoffs and limits

Honest caveats, because nothing in this space is free.

Tests cover maybe forty percent of the rules in a typical research
codebase. The rest stays as prose. It is tempting to over-promise
determinism and claim that the right architecture can test
everything, but invariants about intent, taste, and scientific
meaning resist assertion. Do not pretend otherwise.

One-line module docstrings drift less than paragraph-length ones.
Keep docstrings short for routine modules; reserve longer docstrings
for the modules that encode emergent semantics a reader cannot infer
by reading the functions in order. `fileStatusManager.py` in vaibify
has a long module docstring because it documents a state machine that
spans five files; most modules have one-line docstrings because
their behavior is visible in their function signatures.

Context-window economics matter less at frontier-model scale than
they did a year ago. A two-hundred-line `AGENTS.md` that is correct
beats a sixty-line `AGENTS.md` that omits load-bearing context.
Optimize for correctness, not brevity. That said, every sentence you
add is a future drift liability, so only add content that is
load-bearing.

Skills are tool-specific. Claude Code's skill format differs from
Cursor's rules format, and both differ from whatever the next tool
will ship. If cross-tool portability matters, stick to `AGENTS.md`
plus tests plus scripts. Skills are a convenience on top, not a
foundation.

## 8. Practical playbook

Here is a checklist for applying this methodology to your own
repository. Do the items in order.

1. List the three to five traps you would be most annoyed to see an
   agent fall into. Write those first as the core of your
   `AGENTS.md`. Do not start with style rules or module maps.

2. List the architectural invariants you rely on. For each, ask
   "can this be a test?" Promote the ones that can. Create a file
   like `tests/testArchitecturalInvariants.py` even if it only
   contains three tests on day one.

3. Do not write a module map. Write a `tools/listModules.py` (or
   equivalent) that prints the current state on demand. If you
   cannot resist persisting a map somewhere, persist the *script's
   output* rather than a hand-written version, and regenerate it on
   every commit via a pre-commit hook.

4. Symlink `CLAUDE.md` to `AGENTS.md` so every agent tool reads the
   same file. If your tool of choice uses yet another filename,
   symlink that too. One source of truth per repository.

5. Set up a CI path check that greps your `AGENTS.md` files for
   file paths and fails on dangling references. This catches the
   most common silent drift: a module gets moved and the doc still
   points at the old location.

6. Iterate on `AGENTS.md` every time you catch yourself correcting
   an agent on the same thing twice. Apply the feedback principle:
   promote to a test if possible, otherwise add a Lessons entry.

7. Resist the urge to add everything. Each new sentence is a future
   drift liability. Read the scoping test in section 4 before
   adding content.

## 9. Worked examples: vaibify as reference

The patterns in this document are visible in vaibify's own repository.
Cross-references so you can see how they look in practice:

- [AGENTS.md](../AGENTS.md): the Layer 1 semantic doc at the
  repository root.
- [vaibify/gui/AGENTS.md](../vaibify/gui/AGENTS.md) and
  [vaibify/gui/static/AGENTS.md](../vaibify/gui/static/AGENTS.md):
  nested subtree docs that state conventions specific to the
  FastAPI backend and the JavaScript frontend.
- [tests/testArchitecturalInvariants.py](../tests/testArchitecturalInvariants.py):
  the Layer 2 file that encodes every testable invariant.
- [tools/listModules.py](../tools/listModules.py): the Layer 3
  discovery script. Run it when you need a module map; do not paste
  its output anywhere.
- [.claude/skills/](../.claude/skills/): Layer 4 recipes for
  recurring multi-step tasks.

Some of these files are being checked in alongside this document, so
a reference may briefly fail to resolve. Once the full set lands,
each link above points at the concrete artifact that corresponds to
the abstract layer.

## 10. A closing thought

This methodology is not really about agents. It is about making code
legible to any reader, human or machine, without sacrificing the
honesty that scientific software requires. In a scientific context,
documentation that drifts from code is a reproducibility hazard:
future readers (including future you) use the docs to understand what
the code did, and a docs-code gap quietly corrupts the record.
Agent-friendly docs and reproducibility-friendly docs turn out to be
the same thing. The discipline required to keep an `AGENTS.md`
correct is the same discipline required to keep a methods section
correct. Vibe boldly. Verify everything.
