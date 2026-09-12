# Vaibify — agent guide

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific pipelines inside Docker containers. Backend: FastAPI
(Python). Frontend: vanilla JavaScript using IIFE modules.

This file states the rules that apply to **every** edit. Rules that
apply to one subsystem live in that subsystem's skill, listed below;
`docs/architecture.md` explains the reasoning;
`tests/testArchitecturalInvariants.py` enforces the structural
invariants; `tools/listModules.py` reports the current structural state
on demand. See `docs/vibeCoding.md` for the methodology behind this
structure.

## How to read this repo when starting a task

1. Read this file.
2. **Load the skill that covers your subsystem** — it carries the
   contracts, the do-not-undo lists, and the tests that enforce them.
   Do not edit one of these areas without it:

   | Touching | Load |
   |---|---|
   | `vaibify/reproducibility/`, level gates, PROOF tab, attestations | `reproducibility-lane` |
   | a container-scoped route, an exec, a container write, a ledger drift | `container-mutations` |
   | `vaibify/containerImage/`, `resources.py`, packaged templates | `container-image-and-packaging` |
   | the terminal, containment, the quiescence claim | `terminal-sessions` |
   | any path a container or host project opens | `host-mode-and-paths` |
   | a requirement row, level cell, badge, or poll payload | `dashboard-state-honesty` |
   | a new FastAPI route module | `add-route-module` |
   | a new data file-format loader | `add-data-loader` |
   | shepherding a pull request to green | `monitor-pr` |

3. Read the files directly touched by the task.
4. If working inside a subtree, read the nearest `AGENTS.md`:
   - Backend Python work under `vaibify/gui/` → `vaibify/gui/AGENTS.md`
   - Frontend JS work under `vaibify/gui/static/` → `vaibify/gui/static/AGENTS.md`
5. If you need the current module layout or public-symbol list, run
   `python tools/listModules.py <subtree>`. Do not guess; do not rely
   on memory of a previous session.
6. For architectural "why" questions, read `docs/architecture.md`.
   For war stories, `docs/lessons.md`. For things that look like bugs
   and are not, `docs/knownDebt.md`.

## Style guide

The source code shall adhere to the following conventions: 

1. Functions should be orthogonal and single-purposed — which usually means 20–30 lines. A longer function is acceptable when splitting it would only create artificial seams: pass-through helpers called from exactly one place that thread the parent's variables onward to carry on the parent's single purpose. Split for reuse or a genuine conceptual boundary, never to satisfy a line count. If identical lines exist in the codebase, make a new function that contains those lines, i.e., don't repeat yourself — but only for *true* duplication; tolerate parallel structure that legitimately diverges (see "When to modularize").

2. Variable names should be camel-case and should have prefixes that 
correspond to the variable type or cast, i.e. Hungarian notation. Use the following guide:

- Boolean = "b"
- Integer = "i"
- Float = "f"
- Double = "d"
- String = "s"
- Arrays should include an "a", e.g., an array of doubles starts with "da". An array prefix declares its element type and is satisfied by `list[<element>]` (numeric arrays also by `numpy.ndarray`); a bare "list" prefix declares only "a list". Exception by live convention: "ba" means bytes/bytearray, never array-of-bool.
- Dictionary = "dict"
- List = "list"
- JSON = "json" — a *decoded* JSON value; encoded JSON text is a string and takes "s"
- Tuple = "t"
- Generator/iterator = "iter"
- A `@contextmanager`/`@asynccontextmanager` function = "context" (its return annotation, if any, describes the undecorated generator: `Iterator[T]`, never `ContextManager[T]`)

If a cast is not listed above, ask me. Beyond these core casts, a
closed registry of domain prefixes (e.g. `set`, `path`, `config`,
`connection`) maps each to its concrete type family; it lives in two
independently edited copies in `tools/generateStyleInventory.py` and
`tests/testStyleInvariants.py`, and growing either tier takes both
edits plus my approval. This rule governs EVERY binding site —
assignments, parameters, loop and `with` targets, `except ... as`
names — not only annotated ones: a binding whose name parses to no
vocabulary prefix fails `testVariableBindingsCarryCastPrefixes`
(legacy bindings are seeded; the budget only falls). A variable
holding a function composes: it carries "f" plus the held function's
own run (`fnStatusCallback` holds a procedure, `fbIsIdle` a
bool-returner). Conventional exemptions: `_`, `self`/`cls`,
`*args`/`**kwargs`, and ALL_CAPS constants.

3. Function names should begin with an "f" and should be followed by additional lowercase letter(s) that describe the return type, e.g. "fb" for a function that returns a Boolean, or "flist" for a function that returns a list. If a function does not return anything, use "fn" as the prefix. Two special runs: "ffn" returns a *function* (decorators, callback factories; the inner return type is deliberately undeclared — decorators cannot know it), and "fgeneric" returns the *caller-determined* type (parametric executors; the future mypy lane pins it with TypeVar annotations). A `@property` is a computed variable and carries a VARIABLE cast prefix, not a function prefix. Every name conforms unless a FOREIGN contract owns it (dunders, framework overrides like `dispatch`/`emit`/`read` — the closed interface-method list in the style inventory); Click command functions conform too, with the user-facing verb pinned by an explicit `@click.command("verb")` string.

3a. This naming contract is ENFORCED: `tests/testStyleInvariants.py`
fails CI on any new nonconforming name, any `fn*` that returns or
yields a value, any literal return or annotation that contradicts its
prefix, and any drift between `tests/styleInventory.json` and the
source. Existing violations are grandfathered in a frozen seed with
exact per-class budgets that may only fall; fixing one lowers the
matching budget constant in the same commit
(`python tools/generateStyleInventory.py --write`). Honesty of scope:
prefix/type consistency is checked where prefixes and annotations
exist — unannotated, unprefixed names are not governed, and the
action-verb rule in rule 6 is not machine-enforced.

4. Prefer functions under ~20–30 lines, because a single-purpose function usually fits there and stays easy to navigate. This is a guideline, not a hard limit. When a long function contains a block that is of broader use or marks a real conceptual boundary, extract it. When the function is long but irreducibly one purpose — its only "helpers" would be single-call pass-throughs sharing threaded state — leave it whole; that is clearer than artificial fragmentation, which also costs an agent navigability by smearing one behavior across many call hops.

5. File names should be camelcase, but should not use Hungarian prefixes.

6. Don't abbreviate any word less than 8 characters long. Function names must have an action verb in them (except for main).

7. Use inline documentation sparingly. Clear, long variable and function names allow the developer to understand how the code is executing just by reading the source code.

8. Do not allow a developer's personal style preferences supersede these rules.

## When to modularize

Extracting an abstraction has a real cost: indirection, a new name to
learn, behavior moved away from where it is used. A human pays that cost
in time and feels it; an agent does not, so an agent's bias runs the
other way — toward premature abstraction. Premature abstraction is the
*worse* error, because **duplication is cheaper than the wrong
abstraction**: duplicated code is visible and deletable, whereas a wrong
abstraction couples distant code through a false commonality and is
painful to unwind. So default to leaving code alone, and extract only in
response to a force that has *already materialized*, in roughly this
order of strength:

1. **A divergence bug** — the same fix had to land in N places and one
   was missed, or two things that had to agree drifted apart. The cost is
   no longer hypothetical.
2. **The third instance** (the rule of three) — not the second. Two
   similar things may be coincidence, and you cannot yet tell which parts
   are essential versus accidental; three is a pattern with a direction.
3. **An un-homed concept** — the domain keeps naming something the code
   has no representation for (e.g. "one session per container" before the
   lease existed). This is the one case to extract on the *first*
   instance: the concept is already real, just homeless.
4. **Differing reasons to change** — one part of a module changes on a
   different cadence or for different reasons than the rest. That is a
   genuine fault line; split along it.

What is **not** sufficient: a line count, surface similarity ("these look
alike" — they may diverge later),
speculative reuse ("might be needed someday"), or "it would be cleaner."
When tempted to split for one of those, don't — note it as a candidate
and wait for a real force.

Module cohesion is the same discipline one level up: a module should own
one cohesive responsibility. A large *cohesive* module is fine; a module
that has accreted a second major concern should be split along that seam.
`testArchitecturalInvariants.py::testModuleSizeIsBounded` is a
*smell-to-justify*, not a mandate: it ratchets current module sizes so a
new god module cannot appear and an existing large one cannot grow, but
it forces a conversation (split, or justify in the allow-list), never a
mechanical split.

## Epistemics for an AI-written codebase

When an agent writes the code, writes the tests, and reviews the diff,
the usual guardrail is gone: a green suite means "the stubs agree with
each other," not "this is correct." This repo has already shipped a fatal
bug (an owner map keyed by name but read by id) under a fully green
suite, because the fixtures used name == id and never drove a live
connection. Treat correctness as un-demonstrated until reality is
exercised. Concretely:

- **Verify by trying to falsify, not confirm.** The way to surface a bug
  you cannot enumerate is to task a check with *breaking* a claim, not
  agreeing with it. Adversarial review — not more confirmatory tests — is
  what caught the name-vs-id bug. For any guarantee that crosses an
  HTTP / WebSocket / container boundary, assert it with the keys made
  distinct (name ≠ id) and a real connection, never a unit stub.
- **Separate "verified" from "asserted."** Say "I confirmed X by running
  Y" or "I believe X but have not checked" — never let the second masquerade
  as the first. A confident, unverified claim about a diff is the same
  reflex that produces a confident, unverified claim about whether a
  benchmark passed; in scientific software that reflex is a contamination
  risk, not a convenience risk.
- **Do not let agreement substitute for evidence.** An agent's pull toward
  pleasing the reader will bend it toward the reader's hypotheses and
  expected results. Resist convergence until the premise has been
  defended on its own terms.

## The rules with no trigger

Everything in this section applies to every edit, and nothing will
remind you of it. The subsystem skills carry the rest.

**Never hard-code science-specific examples.** Vaibify is for the
general problem of containerized scientific workflows. Specific
datasets, specific experimental setups, specific user projects, and
specific target systems must not appear in vaibify source, templates,
tests-of-record, or docs. When a specific example helps during
development, keep it in a scratch branch or a user-owned workflow
repo, never in vaibify itself.
`tests/testArchitecturalInvariants.py::testNoScienceSpecificIdentifiersInSource`
enforces this with a seed list; extend the list when new
science-specific terms need to be forbidden. A researcher's project
*state* leaks more easily than their science does: a comment
containing a number, path, or timestamp you learned from the session
rather than from the code is the tell. Write the general shape.

**Never introduce security vulnerabilities.** Review every plan for
exploits before implementing. Threat model: AI agents running inside
containers, acting on user-owned host data, with credentials for
Overleaf, GitHub, and Zenodo. Failure modes to audit against:

- Command injection through user-provided workflow fields
- Path traversal via `sPath` parameters. Vaibify's backend and CLI
  run on the host, not inside the container, and they handle host
  paths in file pulls, directory browsing, sync, and workspace
  mounts. Any path that originated from a user-facing source (HTTP
  request body, project.json, config file) must be validated
  against its intended root before being opened, read, written, or
  listed. The existing helper `fsValidatePathWithinRoot(sAbsPath,
  WORKSPACE_ROOT)` in `pipelineServer.py` does this — do not remove
  or weaken it.
- Credential leakage through logs, error messages, or generated test
  code
- Mounting host paths outside the workspace volume
- Bypass of the unprivileged-user + `gosu` protection in the container
- Network egress where the container is meant to be isolated
- Embedding secrets in source, commit messages, or CI output

If a change expands the attack surface, call it out explicitly in the
plan before implementing.

**Never suppress or misrepresent the container or workflow state in the
dashboard.** The GUI is the user's ground truth. Step status, file
staleness, verification state, test results, and container health must
always reflect reality. Do not cache state beyond its natural lifetime;
do not short-circuit polling to "look responsive"; do not hide errors;
do not optimistically mark steps as passed. If the truth is slow or
ugly, show it. This applies to `fileStatusManager.py`,
`pipelineRoutes.py`, `pipelineState.py`, and every frontend render
path. The detail is in the `dashboard-state-honesty` skill.

**Do not delete or silence a test to make a failure go away.** A
failing test is signalling one of three things: a bug in the code
under test, a bug in the test's assertion, or a legitimate behavior
change that the test predates. The fix is to investigate and address
the right one, not to remove the test. Deleting or disabling a test to
unblock a run is effectively unrecoverable: future regressions have no
guardrail.

**State the mechanism, not the tally.** A count of code facts written
into this file is wrong within weeks, and a reader who checks one and
finds it false stops trusting the ones they cannot check. Where a
number is genuinely wanted, give the command that computes it.

## Contracts enforced by a test

Each of these is a real rule whose violation fails CI. The test is the
control; this line is the signpost. The reasoning lives in
`docs/architecture.md` or the subsystem skill.

- **Step labels are per-type sequential, not positional.** `A09` is
  the 9th *automated* step. Use `fsLabelFromStepIndex` /
  `fiStepIndexFromLabel` from `vaibify/gui/pipelineUtils.py`; never
  inline the translation. Users speak labels; internal code paths keep
  0-based indices.
- **A step's directory basename is a function of its name.** The single
  implementation is `fsSlugFromStepName` / `fsValidateStepName` /
  `fbStepDirectoryConforms` in `vaibify/gui/pipelineUtils.py` (with a
  display-only JS mirror in `scriptUtilities.js` — the backend is the
  authority). Never write a second derivation, and never let a name
  change bypass the rename cascade in `stepRename.py`.
- **`pipelineUtils.py` is a deliberate leaf module.** Zero
  intra-package imports, to break dependency cycles. Do not add
  `from vaibify.gui` or `import vaibify.gui` to it.
  `testLeafModuleHasNoIntraPackageImports` enforces it.
- **Every cross-step file reference must be a `{step:<sStepId>.<stem>}`
  token in the step's command**, not a hardcoded path. The workflow
  JSON, parsed mechanically, is the complete dependency graph; the
  parser cannot introspect script source, so a path hidden in a script
  literal means the edge does not exist and the workflow cannot
  honestly reach PROOF Level 1. `saDependencies` is the escape hatch.
  `testTemplateCommandsUseStepTokens` and
  `testTemplateCommandsUseSymbolicNotPositionalTokens` enforce it;
  `vaibify/docs/scriptAuthoring.md` has the worked examples.
- **Every state-mutating route a researcher can invoke must be
  registered with the agent-action catalog** — `LIST_AGENT_ACTIONS`
  plus `@ffnAgentAction("name")`, or an explicit entry in
  `SET_INTENTIONALLY_EXCLUDED_PATHS`. `bAgentSafe` is enforced
  server-side and fails closed, so getting it wrong is a security
  decision, not a documentation one. `testAgentActionRegistered` and
  `tests/testAgentLaneEnforcement.py` enforce it; the
  `add-route-module` skill has the recipe.
- **`introspectionScript.py` is an f-string executed inside
  containers.** Editing it as ordinary Python loses escape sequences
  silently, and its duplication with `dataLoaders.py` is deliberate —
  container scripts cannot import from the host.
- **JavaScript IIFE state objects share mutable collections by
  reference.** Reassigning a Set breaks rendering; use `.clear()` and
  mutate in place. See `vaibify/gui/static/AGENTS.md`.

## Personal AI Configuration is a QUESTION, not an artifact

The Level 2 row asks the researcher one thing: did your own private,
host-side agent setup — global instruction file, personal skills,
memory, hooks — govern this work, and are you disclosing it?
**Answering is the criterion. Disclosure is never required**: `none`,
`declared-private` and `included` all pass, and `declared-private`
with nothing further is a complete answer. Only *unanswered* fails.

It is not a "system prompt" — skills, memory and hooks are not
prompts, and the harness system prompt is a different layer of the
same stack. And it is not a file the researcher must produce, so never
tell them to write or find one. The identifiers, route path and
persisted key remain `personalLayer` / `dictPersonalLayer`; renaming a
stored schema key would strand every project that has already
answered.

## Required after edits

- After any Python change:
  `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
- After changes that touch structural invariants (adding a route,
  adjusting import graphs, or touching `workflowManager.py`):
  `python -m pytest tests/testArchitecturalInvariants.py -v`
- After editing documentation: `python tools/checkAmericanSpelling.py`
  (`--write` fixes). `tests/testAmericanSpelling.py` is the
  enforcement; `.githooks/pre-commit` is the fast local copy, enabled
  with `git config core.hooksPath .githooks`.
- After editing any `.claude/skills/*/SKILL.md`:
  `python -m pytest tests/testSkillIntegrity.py -v`, then the trigger
  and outcome harnesses in `tools/` — see `docs/skillTesting.md`.
- Before pushing a falsification test, run
  `python tools/reconfirmFalsification.py --only <substring>`. It
  applies each recorded mutation in a disposable worktree and reports
  KILLED or SURVIVED in about a minute, against ~20 minutes for a
  suite and far longer for a CI round trip.

### Required after JS changes

**A green Python suite says nothing about the frontend** — it does not
execute it at all. Five agents once changed JavaScript in one session,
none could load a browser, and the merged branch was green with the
frontend entirely unexecuted.

```bash
pip install -e '.[browser]' && python -m playwright install chromium
python -m pytest tests/browser -m browser
```

**Do not read a green browser lane as "the frontend is verified."** It
drives a fail-closed fake Docker adapter, so it says nothing about
container launch, file ownership on write, the real transport,
terminal content, figure rendering, or the sync panel. The manual
walkthrough and what each lane proves are in `docs/developers.md`.

**If you are a delegated agent and cannot load a browser: push the
branch and open a pull request**, then let the browser lane run it.
The lane is `pull_request`-triggered, so a pushed branch with no PR
runs nothing — do not read a quiet Actions tab as a pass. If you also
cannot push, **say so explicitly and name the exact surface you did
not verify**. Silence about an unverified surface reads as
verification.

Two properties hold the lanes together and **must not be weakened**:
the browser lane's fake is fail-closed and declared
(`tests/testBrowserLaneContract.py`), and no lane may skip itself
green — `VAIBIFY_REQUIRE_DOCKER_DAEMON` and `VAIBIFY_REQUIRE_BROWSER`
turn each lane's convenience skip into a CI failure.

## Ask first

The following actions have outsized blast radius and require explicit
user confirmation before execution:

- Changing the verification state machine semantics (`fileStatusManager.py`).
- Modifying Docker security capabilities, user namespace, or network
  isolation.
- Touching the reproducibility pipeline (`vaibify/reproducibility/`,
  Zenodo, Overleaf, LaTeX integration).
- Force-pushing, rewriting shared git history, or changing CI
  workflows beyond the documentation path-check added alongside this
  guide.

### Enforced by harness hooks

Some of the above are enforced by Claude Code PreToolUse hooks
configured in `.claude/settings.json`:

- **`askSensitiveEdit.py`** pauses `Edit`, `Write`, and `NotebookEdit`
  on: `vaibify/containerImage/*`, `vaibify/docker/containerManager.py`,
  `vaibify/config/secretManager.py`, any `AGENTS.md`, and any
  `.claude/skills/*/SKILL.md`. The hook returns an "ask" decision so
  the user sees a confirmation prompt.
- **`blockDestructiveGit.py`** denies `Bash` commands matching
  `git push --force` (except `--force-with-lease`) and
  `git rebase -i`. These are hard-blocked; run manually if genuinely
  needed.

If a hook fires during your work, read the reason and either confirm
with the user (for "ask") or escalate the need (for "deny"). Do not
edit the hook scripts or `.claude/settings.json` to bypass a block —
that itself is an edit to a sensitive file and an ask-first action.
Temporary bypass is available via `--disable-hooks` at the CLI level
if a human is driving.

## Discovery commands

Rather than memorizing structural facts, run these when you need them:

- `ls vaibify/gui/routes/*Routes.py` — current route modules
- `grep -rh "^__all__" vaibify/gui/ | sort -u` — public symbol exports
- `python tools/listModules.py vaibify/gui` — Python module map with
  docstring purposes
- `python tools/listModules.py vaibify/gui/static --format json` — JS
  IIFE modules, machine-readable
- `find . -name AGENTS.md -not -path './.git/*'` — all agent docs
- `python -m pytest tests/testArchitecturalInvariants.py -v` —
  current enforced invariants (tests are documentation)

## Pointers

- [docs/architecture.md](docs/architecture.md) — the "why" behind the
  module layout
- [docs/lessons.md](docs/lessons.md) — mistakes from past sessions,
  where the wrong answer was the natural one
- [docs/knownDebt.md](docs/knownDebt.md) — things that look like bugs
  and are deliberate
- [docs/vibeCoding.md](docs/vibeCoding.md) — the methodology behind
  this documentation structure
- [docs/developers.md](docs/developers.md) — human contributor guide
- [docs/skillTesting.md](docs/skillTesting.md) — how skills are tested
- [vaibify/gui/AGENTS.md](vaibify/gui/AGENTS.md) — backend subtree
  rules
- [vaibify/gui/static/AGENTS.md](vaibify/gui/static/AGENTS.md) —
  frontend subtree rules
- [.claude/skills/](.claude/skills/) — the subsystem skills routed in
  "How to read this repo" above
