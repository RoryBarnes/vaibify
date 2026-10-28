# Vaibify — agent guide

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific pipelines inside Docker containers. Backend: FastAPI
(Python). Frontend: vanilla JavaScript using IIFE modules.

This file states the rules that apply to **every** edit. Rules that
apply to one subsystem live in that subsystem's skill, listed below;
`docs/architecture.md` explains the reasoning;
`tests/testArchitecturalInvariants.py` enforces the structural
invariants; `tools/listModules.py` reports the current structural state
on demand. See `docs/forAgents.md` for the methodology behind this
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
- An asyncio event loop = "loop" (a domain prefix; see below)
- A DOM `Element`/`HTMLElement` = "element" (not any DOM `Node`; a domain prefix; see below)

If a cast is not listed above, ask me. Beyond these core casts, a
closed registry of domain prefixes (e.g. `set`, `path`, `config`,
`connection`, and the `loop` and `element` listed above) maps each to
its concrete type family; it lives in two
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
prefix/type *agreement* is checked only where an annotation or a
literal return states the type — an unannotated binding is checked for
carrying a vocabulary prefix (rule 2), not for holding what its prefix
claims — and the action-verb rule in rule 6 is not machine-enforced.

4. Prefer functions under ~20–30 lines, because a single-purpose function usually fits there and stays easy to navigate. This is a guideline, not a hard limit. When a long function contains a block that is of broader use or marks a real conceptual boundary, extract it. When the function is long but irreducibly one purpose — its only "helpers" would be single-call pass-throughs sharing threaded state — leave it whole; that is clearer than artificial fragmentation, which also costs an agent navigability by smearing one behavior across many call hops.

5. File names should be camelcase, but should not use Hungarian prefixes.

6. Don't abbreviate any word less than 8 characters long. Function names must have an action verb in them (except for main). The one accepted exception is `dictCtx`, the per-hub context dictionary every route module receives, whose spelling is part of a registration signature (see `docs/knownDebt.md`); allow no other abbreviation, and do no mass renames.

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
mechanical split. It scans `vaibify/gui/` only; a module elsewhere (in
`vaibify/reproducibility/` or `vaibify/docker/`, say) is bounded by
nothing but this discipline.

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
- **A falsification test is kill-confirmed**: proven to FAIL when the
  guard it defends is broken, not merely to pass. It carries the
  `falsification` pytest marker (per test, or a module-level
  `pytestmark`), records the mutation it kills on a `Kills:` docstring
  line, and has exactly one entry in `tests/falsificationRegistry.py`
  stating that mutation as exact `old` → `new` text in a named source
  file. `testFalsificationMarkedTestsAreRegistered` and
  `testFalsificationRegistryIsWellFormed` enforce that shape;
  `tools/reconfirmFalsification.py` re-applies each mutation to prove
  the kill still holds (see "Required after edits"). A kill proves the
  test is sensitive, not that its expected value is right: derive that
  value independently of the code under test (the independent-oracle
  rule in the registry's docstring).
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
enforces this with a seed list for `*.py`, `*.html`, `*.js` and `*.css`
under `vaibify/`, skipping any `tests/`, `templates/` or `docs/`
directory; `testNoScienceSpecificIdentifiersInShippedTemplates` scans
every file of every template except the one shipped example workflow
(`vaibify/templates/workflow/`). Markdown outside the templates,
repo-root `docs/` and `tests/` are not scanned: there the rule holds by
review alone. Extend the seed list when new science-specific terms
need to be forbidden. A researcher's project
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
  listed. The existing helper `fsValidatePathWithinRoot(sResolvedPath,
  sAllowedRoot)` in `pipelineServer.py` does this; pass the root the
  path is meant for (the project root, a writable root, a drafts or
  logs directory), never a broader one such as the whole workspace.
  A route that writes caller-supplied content into the project repo
  must also apply `fnRejectWriteDenylistedPath` (refuses `.git/`,
  `.vaibify/` and `project.json`) and pass
  `**fdictConfinedWriteKeywords(<root>)` to the container write, which
  re-applies the boundary without following symlinks. Do not remove or
  weaken any of the three.
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
failing test is signaling one of three things: a bug in the code
under test, a bug in the test's assertion, or a legitimate behavior
change that the test predates. The fix is to investigate and address
the right one, not to remove the test. Deleting or disabling a test to
unblock a run is effectively unrecoverable: future regressions have no
guardrail.

**State the mechanism, not the tally.** A count of code facts written
into this file is wrong within weeks, and a reader who checks one and
finds it false stops trusting the ones they cannot check. Where a
number is genuinely wanted, give the command that computes it.

**A plan ships as ONE pull request.** However many defects or stages
a plan covers, it lands on one branch and one PR. This is a public
repository, and GitHub allocates its macOS runners to open-source
projects slowly, so every PR's macOS lanes sit queued long before
they run. Several PRs multiply that wait, and a fix every PR needs
(a CI timeout, a shared test) has to be pushed through each of them.
Separate the work into commits on the one branch, never into PRs.

**A plan asks for every approval up front, and building waits for a
go-ahead.** Gather every decision the work will need from the
maintainer (design choices, ask-first edits, ledger rises, CI changes)
into the plan itself, so the build then runs without interruption.
Answering the plan's questions is not approval to build it; wait for
an explicit go-ahead.

**A CI timeout catches a hang, it is not a performance budget.** Set
`timeout-minutes` well above what the step has ever taken, never just
above the last measured run: a suite that grows a little then times
out on every pull request at once.

## Contracts enforced by a test

Each of these is a real rule. Where a bullet names a test, a violation
of what that test checks fails CI; where a bullet says enforcement is
partial or absent, the rule holds all the same and review is the only
control. The test is the control where one exists; this line is the
signpost. The reasoning lives in `docs/architecture.md` or the
subsystem skill.

- **Step labels are per-type sequential, not positional.** `A09` is
  the 9th *automated* step. Use `fsLabelFromStepIndex` /
  `fiStepIndexFromLabel` from `vaibify/gui/pipelineUtils.py`; never
  inline the translation. Users speak labels; internal code paths keep
  0-based indices. Enforcement is partial:
  `testStepPayloadsCarrySLabel` checks that the step routes emit
  through the label decorators and
  `testGeneratedConftestTranscribesTheHostStepLabeller` that the
  container's copy agrees; no test catches an inline translation
  elsewhere.
- **A step's directory basename is a function of its name.** The single
  implementation is `fsSlugFromStepName` / `fsValidateStepName` /
  `fbStepDirectoryConforms` in `vaibify/gui/pipelineUtils.py` (with a
  display-only JS mirror in `scriptUtilities.js` — the backend is the
  authority). Never write a second derivation, and never let a name
  change bypass the rename cascade in `stepRename.py`. Enforcement is
  partial: `tests/testStepSlugContract.py` and
  `tests/testStepSlugMirrorEquivalence.py` pin the formula and the JS
  mirror, and `tests/testStepRenameCascadeReachesEverything.py` pins
  the cascade's reach; no test detects a new derivation written
  elsewhere or a code path that changes a name without the cascade.
- **`pipelineUtils.py` and `truthDerivation.py` are deliberate leaf
  modules.** No import from the vaibify package at all — no
  `vaibify.*` and no relative import, anywhere in the file — to break
  dependency cycles. `testLeafModuleHasNoIntraPackageImports`
  enforces it.
- **Every cross-step file reference must be a `{step:<sStepId>.<stem>}`
  token in the step's command**, not a hardcoded path. The workflow
  JSON, parsed mechanically, is the complete dependency graph; the
  parser cannot introspect script source, so a path hidden in a script
  literal means the edge does not exist and the workflow cannot
  honestly reach PROOF Level 1. `saDependencies` is the escape hatch.
  `testTemplateCommandsUseStepTokens` and
  `testTemplateCommandsUseSymbolicNotPositionalTokens` enforce it for
  the shipped templates under `vaibify/templates/` only;
  `vaibify/docs/scriptAuthoring.md` has the worked examples.
- **Every state-mutating route a researcher can invoke must be
  registered with the agent-action catalog** — `LIST_AGENT_ACTIONS`
  plus `@ffnAgentAction("name")`, or an explicit entry in
  `SET_INTENTIONALLY_EXCLUDED_PATHS` (both in
  `vaibify/gui/actionCatalog.py`). `bAgentSafe` is enforced
  server-side and fails closed, so getting it wrong is a security
  decision, not a documentation one. `testAgentActionRegistered`
  builds the workflow-viewer app (`fappCreateApplication`) and checks
  its POST, PUT and DELETE routes, including that each handler's
  decorator name matches its catalog entry;
  `tests/testSecurityBoundaryInvariants.py::testHubAppStateMutatingRoutesAreGoverned`
  applies the same membership rule to the hub app; and
  `tests/testAgentLaneEnforcement.py` drives the server-side refusals
  with an agent token. No test covers PATCH, which the server's own
  `SET_STATE_MUTATING_METHODS` counts as mutating. The
  `add-route-module` skill has the recipe. **And every mutating route
  must have a CLI equivalent**: a route outside `LIST_AGENT_ACTIONS`
  needs exactly one home among `LIST_RESEARCHER_ACTIONS`,
  `DICT_ROUTE_TO_HOST_COMMAND` (a host command), and
  `SET_GUI_ONLY_ROUTES` (with a reason), and a pipeline-socket action
  the page sends needs a `WS` entry in `LIST_AGENT_ACTIONS` or an entry
  in `DICT_WS_ACTION_TO_HOST_COMMAND`; every one of those homes but
  `LIST_AGENT_ACTIONS` lives in `vaibify/gui/researcherActions.py`.
  `testEveryGuiInvokedMutationHasCliEquivalent` enforces it over every
  POST, PUT and DELETE route of both apps, not only those the page
  calls, and fails a stale entry too, so Level 3 stays reachable from
  the command line alone.
- **`introspectionScript.py` is an f-string executed inside
  containers.** Editing it as ordinary Python loses escape sequences
  silently, and its duplication with `dataLoaders.py` is deliberate —
  container scripts cannot import from the host. No test enforces the
  duplication: nothing binds the script's `_DICT_FORMAT_MAP` to
  `dataLoaders.DICT_FORMAT_MAP` (the module docstring says so), and
  `tests/testDeterministicTestGenerator.py` checks only that the
  generated script parses and names its format handlers.
- **A coroutine is never built and discarded.** Turning a `def` into
  an `async def` is the one signature change Python does not report at
  the call site: an un-awaited coroutine is truthy, runs no body, and
  raises nothing, so a guard that stops being awaited stops guarding
  while the suite stays green. That shipped once, on the
  environment-delete route. `testEveryCoroutineCallIsConsumed`
  resolves calls by NAME, which is why
  `testCoroutineNamesDoNotCollideWithSyncNames` bounds the names
  defined both ways — each collision is a hole in the guard, not a
  blemish. Runtime detection is NOT a substitute: Python reports the
  un-awaited coroutine through the garbage collector's unraisable
  hook, where pytest's `filterwarnings` cannot turn it into a failure
  (measured — `error::RuntimeWarning` reports the suite green on a
  live instance of the bug).
- **JavaScript IIFE state objects share mutable collections by
  reference.** Reassigning a Set breaks rendering; use `.clear()` and
  mutate in place. See `vaibify/gui/static/AGENTS.md`. No test
  enforces this one; it is held by review.

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

- After any Python change, the suite CI's matrix runs (the `-m`
  expression is the one in `.github/workflows/tests-linux.yml`):
  `python -m pytest tests/ -q -m "not docker and not docker_live and not browser"`
- After changes that touch structural invariants (adding a route,
  adjusting import graphs, or touching `workflowManager.py`):
  `python -m pytest tests/testArchitecturalInvariants.py -v`
- After editing documentation: `python tools/checkAmericanSpelling.py`
  (`--write` fixes). Its default scope is `docs/*.md` and `README.md`,
  and only that scope is enforced: `tests/testAmericanSpelling.py` is
  the enforcement; `.githooks/pre-commit` is the fast local copy,
  enabled with `git config core.hooksPath .githooks`. For an
  `AGENTS.md` or a `SKILL.md`, pass the path explicitly
  (`python tools/checkAmericanSpelling.py AGENTS.md`); nothing checks
  those files otherwise.
- After editing any `.claude/skills/*/SKILL.md`:
  `python -m pytest tests/testSkillIntegrity.py -v`, then the trigger
  and outcome harnesses in `tools/` — see `docs/skillTesting.md`.
- Before pushing a falsification test, run
  `python tools/reconfirmFalsification.py --only <substring>` (add
  `--include-local-diff` on a dirty checkout; without it the harness
  refuses, because it checks out HEAD). It applies each recorded
  mutation in a disposable worktree and reports KILLED or SURVIVED in
  about a minute, against ~20 minutes for a suite and far longer for a
  CI round trip. A narrowed run is not the standing negative control
  and does not judge registry completeness, so also run
  `python tools/reconfirmFalsification.py --completeness-only`: it
  collects every falsification-marked test pytest finds, the browser
  lane's included (the in-suite registry test scans only top-level
  `tests/test*.py`), and fails on any without a registry entry.

### Required after JS changes

**A green Python suite says nothing about the frontend** — it does not
execute it at all. Five agents once changed JavaScript in one session,
none could load a browser, and the merged branch was green with the
frontend entirely unexecuted.

```bash
pip install -e '.[browser]' && python -m playwright install chromium
VAIBIFY_REQUIRE_BROWSER=1 python -m pytest tests/browser -m browser -n 6 --dist loadfile
```

`VAIBIFY_REQUIRE_BROWSER=1` turns a missing Playwright into a failure
instead of a green skip. Pick a worker count below your core count;
`docs/developers.md` explains `--dist loadfile` and gives the serial
command to fall back on when diagnosing.

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
  isolation. These are set in more than `containerManager.py`: the
  disposable-container specification and the Agent Council runner,
  proxy and egress network set them too, and
  `grep -rlE "cap_drop|cap-drop|security_opt|network_mode|internal=True" vaibify/`
  lists every file that does.
- Touching the reproducibility pipeline (`vaibify/reproducibility/`,
  Zenodo, Overleaf, LaTeX integration).
- Force-pushing, rewriting shared git history, or changing CI
  workflows beyond the documentation path-check added alongside this
  guide.

### Enforced by harness hooks

Claude Code PreToolUse hooks configured in `.claude/settings.json`
back a few of the rules above, and also gate some files the list does
not name. They are a convenience, not the control: a rule binds
whether or not a hook fires.

- **`askSensitiveEdit.py`** pauses `Edit`, `Write`, and `NotebookEdit`
  when the path the tool was given matches: anything under
  `vaibify/containerImage/`, `vaibify/docker/containerManager.py`,
  `vaibify/config/secretManager.py`, any file named `AGENTS.md`, or a
  `SKILL.md` directly inside `.claude/skills/<name>/`. The hook
  returns an "ask" decision so the user sees a confirmation prompt.
  It matches the path string as given and resolves no symlinks, so an
  edit addressed to `CLAUDE.md` (the symlink to the root `AGENTS.md`)
  is not intercepted, and it never sees an edit made with `Bash`
  (`sed -i`, a heredoc, a script). Nothing gates `fileStatusManager.py`,
  `vaibify/reproducibility/`, the CI workflows, the other files that
  set Docker security options, or the hook scripts and
  `.claude/settings.json` themselves.
- **`blockDestructiveGit.py`** denies `Bash` commands matching
  `git push --force` (except `--force-with-lease`) and
  `git rebase -i`. These are hard-blocked; run manually if genuinely
  needed.

If a hook fires during your work, read the reason and either confirm
with the user (for "ask") or escalate the need (for "deny"). Do not
edit the hook scripts or `.claude/settings.json` to bypass a block —
no hook prompts for those files, but editing them is still an
ask-first action by this rule. Likewise, reaching a gated file through
the `CLAUDE.md` path or through `Bash` does not lift its ask. Claude
Code has no `--disable-hooks` flag; a human who genuinely needs the
hooks off can disable them in their own settings, or start a session
with `claude --bare`, which per `claude --help` skips settings-defined
hooks — and CLAUDE.md auto-discovery with them, so this guide is not
loaded.

## Discovery commands

Rather than memorizing structural facts, run these when you need them:

- `ls vaibify/gui/routes/*Routes.py` — current route modules
- `python tools/listModules.py vaibify/gui` — Python module map: each
  module's public symbols (its `__all__`, parsed, so a multi-line list
  counts — a `grep "^__all__"` does not see one) and docstring purpose
- `python tools/listModules.py vaibify/gui/static --format json` — JS
  IIFE modules, machine-readable
- `git ls-files '*AGENTS.md'` — all agent docs (a `find` also lists the
  copies inside `.claude/worktrees/`)
- `python -m pytest tests/testArchitecturalInvariants.py -v` —
  current enforced invariants (tests are documentation)

## Pointers

- [docs/architecture.md](docs/architecture.md) — the "why" behind the
  module layout
- [docs/lessons.md](docs/lessons.md) — mistakes from past sessions,
  where the wrong answer was the natural one
- [docs/knownDebt.md](docs/knownDebt.md) — things that look like bugs
  and are deliberate
- [docs/forAgents.md](docs/forAgents.md) — the methodology behind
  this documentation structure
- [docs/developers.md](docs/developers.md) — human contributor guide
- [docs/skillTesting.md](docs/skillTesting.md) — how skills are tested
- [vaibify/gui/AGENTS.md](vaibify/gui/AGENTS.md) — backend subtree
  rules
- [vaibify/gui/static/AGENTS.md](vaibify/gui/static/AGENTS.md) —
  frontend subtree rules
- [.claude/skills/](.claude/skills/) — the subsystem skills routed in
  "How to read this repo" above
