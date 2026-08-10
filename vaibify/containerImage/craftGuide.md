
---

# Scientific Code Craft

Everything below governs the code you write for the researcher —
analysis scripts, pipeline steps, tests, and figures. The sections
above are operational rules for driving vaibify; these are the rules
for the code itself. The researcher's project context
(`<repository>/.vaibify/AGENTS.md`) may extend or override any of
them for a specific project; where it is silent, these apply.

## Readability

Code is read far more often than it is run, by humans and agents who
were not present when it was written. Write source that a reader can
follow without comments:

1. Variable names are camelCase with a Hungarian-notation prefix for
   the type: `b` Boolean, `i` integer, `f` float, `d` double, `s`
   string, `a` array (an array of doubles starts with `da`), `dict`
   dictionary, `list` list, `json` JSON, `t` tuple. If a type has no
   prefix here, ask the researcher rather than inventing one.
2. Function names begin with `f` followed by lowercase letters naming
   the return type — `fb` Boolean, `fi` integer, `fs` string,
   `flist`, `fdict` — or `fn` for a function that returns nothing.
   Every function name contains an action verb (except `main`).
3. Do not abbreviate any word shorter than eight letters. A name that
   must be decoded is a name that will be misread.
4. File names are camelCase without Hungarian prefixes.
5. Functions should be orthogonal and single-purposed, which usually
   means 20–30 lines. That is a guideline, not a hard limit: split a
   function for reuse or at a genuine conceptual boundary, never to
   satisfy a line count. A long function that is irreducibly one
   purpose is clearer than the same behavior smeared across
   single-call pass-through helpers.
6. Use inline documentation sparingly. Clear, complete names should
   let a developer understand the algorithm from the source alone; a
   comment earns its place by stating a constraint the code cannot
   show.

Duplication is cheaper than the wrong abstraction. Extract a shared
function in response to a force that has already materialized — the
same fix had to land in several places, or a third instance of a
pattern has appeared, or the problem domain keeps naming a concept
the code has no home for — not because two things merely look alike
today.

## Observability

Write code that fails loudly and locally, not silently and plausibly. The
most dangerous bug in scientific software produces a finite, well-formatted,
wrong number: it passes type checks, runs to completion, survives a green
test suite, and surfaces only as a disagreement with a benchmark that cannot
tell you which line caused it. Prefer a failure that names its cause over a
result that hides it.

**Generic health checks — always add these.** These invariants hold for every
model, so enforce them unconditionally and without asking:

- Outputs must be finite. Trap floating-point exceptions — division by zero,
  overflow, underflow — so a NaN or Inf raises at its origin instead of
  propagating into the results.
- Where a computation is meant to be deterministic, verify it: identical
  inputs must produce identical outputs. A diff between two runs surfaces
  uninitialized memory, race conditions, and unintended nondeterminism, and
  points at the run that broke.

**Domain invariants — identify and propose, never silently assert.** Every
physical or mathematical model conserves or bounds something: a conserved
quantity (energy, mass, charge, a probability integrating to one), a bound (a
density stays non-negative, an eccentricity stays in [0, 1)), a monotonic
trend, a symmetry, or a known limiting case (the answer in the small-angle or
non-relativistic limit). Identifying these is real modeling work and it is
yours to do: when you touch a model, propose the invariants you believe it
must satisfy and CI tests that check them to a stated tolerance.

Treat these as proposals for the researcher to approve, not facts to assert on
your own authority. A wrong invariant is worse than none — a mis-stated law or
a too-tight tolerance turns every correct run red and sends the next agent
chasing a bug that is not there, while a too-loose one grants false
confidence. State the invariant, state the tolerance and why, and let the
researcher confirm it before it enters CI. When an invariant's own correctness
is uncertain, raise it as a question rather than resolving it silently.

**Assertions and diagnostics.** Put assertions at boundaries and on
invariants, not on every line: an assertion earns its place by catching a
violation that would otherwise be silent. Guard the expensive ones so they run
in test and debug builds and compile out of hot loops, matching the existing
memory- and floating-point-checking regime. When you log, emit the few
intermediate quantities that would let a reader localize a fault — not every
variable. A diagnostic firehose hides the signal as effectively as silence
does.

Graceful exits with helpful messages remain required: on an error condition,
exit cleanly with a message saying what went wrong and where — never a bare
code or a silent continuation.

## Localizability

A correct change should require reading a bounded, findable neighborhood of
the code — not a tour of the whole project. When the context needed to safely
edit one thing is scattered across many files and held together by conventions
that live in no interface, an agent (or a human) makes a locally-plausible
change that violates a distant assumption. Keep the knowledge a change
requires close to the change.

**No action at a distance.** The worst enemy of a bounded change is hidden
coupling: global mutable state, an execution-order dependency that no
signature expresses, a shared structure several modules quietly read and
write. A change here that breaks something over there, with no local signal
that the two were connected, is the failure to design against. Prefer explicit
inputs and outputs over reaching into shared state; pass what a function needs
as arguments; encode an ordering requirement in the interface — a type, a
required argument, an explicit step — rather than leaving it as folklore.

**Make cross-module dependencies explicit and greppable.** If one part of the
code depends on another, that dependency should be discoverable by search, not
inferred by reading everything. A reference that crosses a module or step
boundary should name its target in a form a tool can parse — the way a
cross-step data reference is declared as a token rather than buried in a path
literal — so the dependency graph can be recovered mechanically. A dependency
the parser cannot see is a dependency the next agent will miss.

**Keep modules cohesive and small.** A module should own one responsibility; a
function should fit on a screen and do one thing. When you must understand
three hundred lines to change three, or read a second file to know what the
first will do, the code is telling you a boundary is missing — extract it. A
bounded neighborhood is not a nicety here; it is the difference between a
change an agent can make correctly from local context and one it cannot.

## Error Handling

Error handling is not optional scaffolding; it is how a pipeline tells
the researcher the truth. When a script hits an error condition, catch
it, print a message that names what went wrong and where — the file
that was missing, the parameter that was out of range, the step that
produced the bad input — and exit with a nonzero status. Never a bare
exit code, never a swallowed exception, never a silent continuation
with a default value the researcher did not choose. Validate inputs
where they enter: a script that checks its arguments and files before
computing fails in seconds with a clear message instead of failing in
hours with a traceback deep inside a library.

## Testing and Invariants

A green test suite proves that the stubs agree with each other, not
that the code is correct — especially when the same agent wrote the
code, wrote the tests, and reviewed the diff. Treat correctness as
un-demonstrated until reality is exercised:

- Every pipeline step declares at least a basic sanity test in
  `saTestCommands`. Prefer a test that checks a property of the real
  output file — shape, finiteness, a physical bound, a conserved
  quantity — over one that re-runs the same arithmetic and compares
  it to itself.
- Verify by trying to falsify a claim, not confirm it. An adversarial
  check — degenerate inputs made distinct, a boundary actually
  driven, a real file on disk — finds the bugs that confirmatory
  tests cannot.
- Never adjust a benchmark or reference value just to make a test
  pass. A disagreement with a benchmark may be a bug in the model,
  and papering over it contaminates every result downstream. Surface
  the disagreement to the researcher.
- Separate "I verified X by running Y" from "I believe X but have not
  checked," and never let the second pose as the first. In scientific
  software a confident unverified claim is a contamination risk, not
  a convenience risk.
