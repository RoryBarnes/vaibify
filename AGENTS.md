# Vaibify — agent guide

Vaibify is a GUI tool for building, running, and verifying reproducible
scientific pipelines inside Docker containers. Backend: FastAPI
(Python). Frontend: vanilla JavaScript using IIFE modules.

This file is the entry point for anyone (human or agent) working on this
repository. It states the rules; `docs/architecture.md` explains the
reasoning; `tests/testArchitecturalInvariants.py` enforces the
structural invariants; `tools/listModules.py` reports the current
structural state on demand. See `docs/vibeCoding.md` for the
methodology behind this structure.

## How to read this repo when starting a task

1. Read this file.
2. Read the files directly touched by the task.
3. If working inside a subtree, read the nearest `AGENTS.md`:
   - Backend Python work under `vaibify/gui/` → `vaibify/gui/AGENTS.md`
   - Frontend JS work under `vaibify/gui/static/` → `vaibify/gui/static/AGENTS.md`
4. If you need the current module layout or public-symbol list, run
   `python tools/listModules.py <subtree>`. Do not guess; do not rely
   on memory of a previous session.
5. For architectural "why" questions, read `docs/architecture.md`.
6. If the task touches an architectural contract (route registration,
   leaf modules, path-module choice, science-agnostic source),
   `tests/testArchitecturalInvariants.py` is the executable
   specification. Run it to see the current state.

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

## Required after edits

- After any Python change:
  `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
- After changes that touch structural invariants (adding a route,
  adjusting import graphs, or touching `workflowManager.py`):
  `python -m pytest tests/testArchitecturalInvariants.py -v`
- After JS changes: see "Required after JS changes" below — the
  Python suite does not execute the frontend at all.
- After editing any `.claude/skills/*/SKILL.md`:
  `python -m pytest tests/testSkillIntegrity.py -v` (referential
  integrity), then run the trigger and outcome evaluation harnesses
  in `tools/` before merging skill changes — see
  [docs/skillTesting.md](docs/skillTesting.md).

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
container launch, file ownership on write, the real transport, terminal
content, figure rendering, or the sync panel. The manual walkthrough,
the three lanes and what each proves are in
[docs/developers.md](docs/developers.md) — "Verifying a change reaches
the screen".

**If you are a delegated agent and cannot load a browser: push the
branch and open a pull request**, then let the browser lane run it. The
lane is `pull_request`-triggered, so a pushed branch with no PR runs
nothing — do not read a quiet Actions tab as a pass. If you also cannot
push, **say so explicitly and name the exact surface you did not
verify** — "the three JS call sites in `scriptWorkflowManager.js` were
not executed; no JS runtime and no push on this host." Silence about an
unverified surface reads as verification. A rule nobody can follow is
not a control, which is why this one has a fallback.

Two properties hold the lanes together and **must not be weakened**:

- **The browser lane's fake is fail-closed and declared.** Every command
  it answers is listed in `LIST_MODELLED_COMMANDS` with the container
  assertion that confirms it; anything else raises. Never give it a
  catch-all return — this suite already carries ~20 permissive Docker
  mocks, and `testDockerConnectionLive.py` records where that habit led.
  `tests/testBrowserLaneContract.py` enforces both halves.
- **No lane may skip itself green.** `VAIBIFY_REQUIRE_DOCKER_DAEMON` and
  `VAIBIFY_REQUIRE_BROWSER` turn each lane's convenience skip into a
  failure in CI. The `docker info || exit 0` guard this replaced
  reported success for having run nothing;
  `tests/testDockerLiveDaemonRequirement.py` forbids its return.

## Traps

These are the mistakes most likely to cause real harm in this
repository. Read them before you start editing.

**Step labels are per-type sequential, not positional.** `A09` is the
9th *automated* step, `I01` is the 1st *interactive* step. `A09`
does not mean `listSteps[9]` in general — it lands at
`listSteps[9 + (number of interactive steps preceding the 9th
automated one)]`. Use `fsLabelFromStepIndex` and
`fiStepIndexFromLabel` from
[vaibify/gui/pipelineUtils.py](vaibify/gui/pipelineUtils.py) — never
inline the translation. `sLabel` is persisted in `project.json` and
recomputed on every load/save by `fnAttachStepLabels`, so insertions,
deletions, and reorderings produce correct labels on the next save
automatically. Error messages, logs, toasts, and agent-facing
commands use labels (users speak labels); internal code paths keep
0-based indices.

**A step's directory basename is a function of its name.** The slug
contract (2026-07-18): split the name on whitespace, uppercase each
word's first letter, preserve the rest, concatenate ("MCMC 512 Chains"
→ `MCMC512Chains`; hyphens pass through). Names allow only letters, digits,
spaces, and hyphens; slugs are unique per project case-insensitively;
parent path components are free. The single implementation is
`fsSlugFromStepName` / `fsValidateStepName` /
`fbStepDirectoryConforms` in
[vaibify/gui/pipelineUtils.py](vaibify/gui/pipelineUtils.py) (with a
display-only JS mirror in `scriptUtilities.js` — the backend is the
authority). Never write a second derivation, and never let a name
change bypass the rename cascade (`stepRename.py`): the generic
update-step path 400s renames precisely so the directory, marker,
and manifest can never drift from the name. Legacy mismatches are a
red ⚠ error with the `align-step-directories` action as the
migration path.

**Never hard-code science-specific examples.** Vaibify is for the
general problem of containerized scientific workflows. Specific
datasets, specific experimental setups, specific user projects, and
specific target systems must not appear in vaibify source, templates,
tests-of-record, or docs. When a specific example helps during
development, keep it in a scratch branch or a user-owned workflow
repo, never in vaibify itself.
`tests/testArchitecturalInvariants.py::testNoScienceSpecificIdentifiersInSource`
enforces this with a seed list; extend the list when new science-specific terms
need to be forbidden.

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

If a change expands the attack surface, call it out explicitly in the plan
before implementing.

**Never suppress or misrepresent the container or workflow state in the
dashboard.** The GUI is the user's ground truth. Step status, file
staleness, verification state, test results, and container health must
always reflect reality. Do not cache state beyond its natural lifetime;
do not short-circuit polling to "look responsive"; do not hide errors;
do not optimistically mark steps as passed. If the truth is slow or
ugly, show it. This applies to `fileStatusManager.py`,
`pipelineRoutes.py`, `pipelineState.py`, and every frontend render
path.

**Determinism is THREE questions, and answering is the criterion.**
The L3 determinism gate was an OR until 2026-08-30 — any one of a BLAS
waiver, a pinned thread count or an MKL mode satisfied it — so a
project could attest at Level 3 having answered a third of the
question. The researcher's ruling made them three requirements with
three markers. A DECLINING answer passes ("I do not accept last-digit
differences", "the thread count is not fixed", "this project does not
use Intel MKL"); only silence fails, exactly as for Personal AI
Configuration. Two things not to undo: answers live in their own keys
because a VALUE cannot express consideration (`bAcceptBlasVariance:
false` is what the old form wrote when nothing was ticked, so it means
"unanswered" and "declined" at once), and an answer naming a pinned
value must carry it. The schema-v13 migration promotes only
unambiguous legacy values — never the `false` waiver — and spells the
key names as literals because this module may import only leaf
modules, so `testTheMigrationSpellsTheSameKeysTheGateReads` pins them
to the gate's constants. Researcher-facing wording lives in
`LIST_DETERMINISM_QUESTIONS` beside the gate and must never carry a
schema key.

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

**A batched container probe must be split to fit ONE exec argument.**
Both badge probes render their whole path list into a single argument —
one embeds it in the typed-read program, one appends it as a
here-string, and a here-string is part of the command string too.
Linux caps a single argument at 128 KB, so unbatched they stopped
working: `flistContainerPathsExist` RAISED at ~1,845 paths (inside a
carrier worker, which poisons the journal record and **quarantines the
container**), and `fdictComputeBlobShasInContainer` answered `{}`
SILENTLY at ~2,562, so every badge was computed from an empty hash map
and shown as fact. Both measured against a real daemon, 2026-08-30.
`vaibify/docker/execArgumentBudget.py` owns the split; never re-derive a budget
beside it. Two things not to "simplify": the split preserves ORDER
(the existence probe zips answers back onto paths), and a failed batch
collapses the whole blob-sha answer rather than returning the batches
that worked (a partial map reads as a claim about the files it omits).
Any NEW batched probe must go through the same splitter —
`tests/testExecArgumentBudget.py` is the kill-confirmed guard.

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

**Container paths are `posixpath`, host paths are `os.path`.**
`workflowManager.py` handles container paths, which are POSIX on every
host operating system. Any module handling host paths must use
`os.path`, whose separator is the host's. A helper shared between the
two lanes must be *pure* (e.g. `flistValidateOutputFilePaths`);
unifying the path handling itself would silently mangle one lane or
the other, and the failure would not surface until a cross-platform
user hit it.

**Host mode does not repeal that rule; it survives it by staying
POSIX.** A host project's pipeline runs on the researcher's own
machine, so `workflowManager` now composes paths that are host paths —
and it still uses `posixpath`, deliberately. Host mode is macOS and
Linux only, where `posixpath` and `os.path` are the same module, so one
implementation serves both modes exactly. The boundary is Windows: the
step commands are composed `bash -c` text and the POSIX path guards
weaken silently there, so Windows is refused rather than accommodated,
and this paragraph is the reason a reader will not find a host-path
fork of the workflow manager. One was tried (the withdrawn
`director` module) and abandoned — swap the connection object, never
fork the path handling.
Modules that are host-only (`vaibify/host/`) still use `os.path`,
because they say what they mean.

**Never write `/workspace` — or `/tmp` — as a constant.** Both name a
root, and a host project's roots are different ones. Ask
`projectRoots.fsResolveProjectRoot(sResourceId, sContainerRoot)` for
the root a project's FILES live under, and
`projectRoots.fsResolveScratchDirectory(sResourceId, sOperationName,
sContainerScratchRoot)` for the one an EPHEMERAL file may be written
to. The container answer is passed in at every call site, so that
module never becomes a second authority on what those roots are; only
a host resource overrides it, and its scratch answer is a private
0700 directory under the host-diagnostics subtree, which is the only
ephemeral root the host path guard admits. A `/tmp` literal is not a
style problem there — it is a refusal, and the whole introspection
lane answered 500 for a host project until this existed.
`tests/testHostModeProjectRoots.py` carries a symmetric falsification
pair for each resolver, and the container direction is the one with
the wider blast radius: a container handed the host answer gets a
path that does not exist inside it.

**Do not revert to `/workspace`-as-repo.** Every vaibify workflow
must live inside a git repository — its "project repo" —
auto-detected from the project.json's parent via
`containerGit.fsDetectProjectRepoInContainer`. `/workspace` is a
Docker-managed named volume, not a repo; it is only the discovery
root. Routes in `vaibify/gui/routes/gitRoutes.py` must thread
`dictWorkflow["sProjectRepoPath"]` into every `containerGit.*` call
(`testGitRoutesAlwaysPassProjectRepoToContainerGit` enforces this),
and no module may hardcode `/workspace/.vaibify/test_markers`
(`testNoWorkspaceRootedMarkerHardcodeInSource` enforces this — test
markers live at `<sProjectRepoPath>/.vaibify/test_markers/`). Step
`sDirectory` values and all `saOutputDataFiles` /
`saPlotFiles` paths are repo-relative; absolute or `..`-escaping
values are rejected at load time. A silent fallback to the
`/workspace` default reintroduces the all-grey-badges bug and
desynchronizes marker writes from marker reads. See
[docs/architecture.md](docs/architecture.md) — the "Workflow = git
repo" section — for the full rationale. A container may host
multiple workflows in different project-repo subdirectories; the
active workflow determines the badge scope.

**`introspectionScript.py` is an f-string executed inside containers.**
Editing it as ordinary Python loses escape sequences and string
delimiters silently. The format-handling duplication with
`dataLoaders.py` is also deliberate — container scripts cannot import
from the host environment.

**`pipelineUtils.py` is a deliberate leaf module.** It has zero
intra-package imports, used to break circular dependency cycles. Do
not add `from vaibify.gui` or `import vaibify.gui` lines to it.
`testLeafModuleHasNoIntraPackageImports` enforces this.

**JavaScript IIFE state objects share mutable collections by
reference.** `_dictWorkflowState` contains Sets that are held by the
render context. Reassigning a Set (`setExpandedSteps = new Set()`)
breaks rendering; use `.clear()` and mutate in place. The
`_fnResetWorkflowState()` factory pattern is how state is cleared
across workflow switches.

**Do not delete or silence a test to make a failure go away.** A
failing test is signalling one of three things: a bug in the code
under test, a bug in the test's assertion, or a legitimate behavior
change that the test predates. The fix is to investigate and address
the right one, not to remove the test. Deleting or disabling a test to
unblock a run is effectively unrecoverable: future regressions have no
guardrail.

**Host→container file writes default to the unprivileged container
user.** Every backend write of a file inside the workspace volume
funnels through `fnWriteFile` / `fnWriteFileViaTar` in
[vaibify/docker/dockerConnection.py](vaibify/docker/dockerConnection.py),
which calls `container.put_archive(tarball)`. The tarball entry's
uid/gid IS the file's owner inside the container, and `tarfile.TarInfo`
natively defaults uid/gid to 0. If that default ever leaks through, the
file lands root-owned and the in-container agent cannot edit it (sudo
is absent by design — commit 426f6b7). The symptom is a researcher's
`git push` failing on `.git/objects/<prefix>` or the agent unable to
modify `project.json` after a backend save. The dispatcher's
`_finfoBuildTarEntry` defaults the stamps to
`_I_CONTAINER_DEFAULT_UID`/`_GID` (1000:1000, locked to the Dockerfile
by `testContainerUserUidIsOneThousand`); any new host→container write
path must preserve that default.
`tests/testArchitecturalInvariants.py::testFnWriteFileDefaultsToContainerUserOwnership`
enforces it.

**Container access has exactly one authority: the lease plus
`dictContainerOwners`.** A container is owned by a per-claim,
server-minted lease (`containerOwnership.fsMintLease`), recorded in the
in-process owner-of-record map `app.state.dictContainerOwners`, keyed by
container **name** (the host flock and the caffeinate keep-alive are both
name-keyed, so the owner map must be too; the WebSocket routes resolve
the Docker container id to a name before the lease lookup). Claim
(`registryRoutes`), the connect handler, and the pipeline WebSocket must
all authorize through the single shared guard
`webSocketAuthorization.fbAuthorizeContainerSession` /
`fiContainerSessionRejectionCode` — never an inlined container-id
membership check. Never reintroduce `setAllowedContainers` (the old
append-only, process-global allow set that leaked authorization for the
whole process lifetime) or treat the shared session token as the browser
*principal* — the shared token is only the trust/CSRF boundary, not the
thing that says *which* browser session owns a container. The
in-container agent authenticates with a **per-container** token
(`OwnerRecord.sAgentToken`, written into that container's
`/tmp/vaibify-session.env`), validated per-container by both
`webSocketAuthorization.fbCheckAgentToken` and the REST
`SessionTokenMiddleware`, so a compromised agent in one container cannot
reach another. Never collapse the agent lane back onto the hub-wide
token. The lease is the exclusivity principal; the
holder payload carries `sStartedIso` for recycle-proof staleness; exactly
one live *pipeline* WebSocket per container is enforced by the
per-container `iLivePipelineConnectionCount` (a duplicate tab that copied
the lease is closed 4409 — after `accept`, via `fnCloseWithCode`, so a
real browser sees the code instead of an unreachable-looking 1006).
Non-pipeline sockets are counted in `iLiveConnectionCount` for liveness
but never budgeted: one session legitimately holds several sockets at
once — budgeting all sockets shipped the Run-Step-always-refused bug
(the terminal, opened on workflow entry, held the only slot; every Run
Step was 4409'd and mislabeled "cannot reach server"). The terminal is
the unbudgeted lane's production caller, so that budget must never be
extended to it. Run exclusivity is additionally
enforced at dispatch for every lane, including the budget-exempt agent
lane: a run arriving while another pipeline action is live in that
container is answered with a `runRefused` event, never started
(`_fbRefuseWhilePipelineTaskLive`). The idle busy-veto reads
`dictContainerOwners.keys()` so the watchdog can never self-SIGTERM a
hub mid-run. The full normative model is the "Single browser session per
container" section of [docs/architecture.md](docs/architecture.md).
Enforced by `testClaimRejectsForeignLease`, `testReleaseRejectsNonOwner`,
`testWebSocketGatesUseSharedAuthorizationGuard`,
`testSetAllowedContainersRemoved`, and
`test_terminal_plus_pipeline_ws_coexist_in_one_session`.

## A human step's outputs are GIVEN, not reproduced

**The rerun carries an interactive step's outputs instead of refusing
the workflow (2026-08-31 ruling).** Before this, any interactive step
refused the whole tier-5 rerun. That made Level 3 **unreachable for
every project vaibify builds**: vaibify creates an interactive AI
Declaration step, Level 2 blocks on `missing-ai-declaration-step`, so
reaching L2 the product's own way guaranteed L3 could never pass. The
researcher found it by clicking Verify; no test combined the two,
because the refusal tests used generic interactive steps.

The model is that a human step's outputs are **input** to the rerun,
like the repository's source files — data a person produced, which the
steps below consume. Nothing new carries them: the shadow's coherent
export already copies everything git can enumerate, so they arrive
verbatim and the executable steps run against the bytes the original
run used.

**The carve-out is exclusion, and both directions of it are load-bearing.**
`flistCarriedOutputRepoPaths` drops those paths from the comparison,
`fdictBuildAttestation` records them in `listCarriedPaths`, and both
lanes report them. Three ways to break it, each already pinned by a
kill-confirmed test:

- **An exclusion set that matches nothing.** It must resolve paths
  through the SAME `fdictWorkflowTemplateValues` +
  `flistStepOutputRepoPaths` helpers the manifest writer uses. Resolved
  any other way the paths simply fail to match, nothing is excluded,
  nothing says so, and every given file is graded as reproduced.
- **An exclusion that leaves nothing.** A workflow whose every pinned
  entry is human-made has nothing for a rerun to reproduce, so it fails
  closed on `S_DIVERGENCE_EVERY_ENTRY_GIVEN` rather than passing 0 of 0.
- **A silent count.** `iOutputHashesTotal` now covers only what
  executed. Displaying that ratio without `listCarriedPaths` beside it
  turns a narrow true statement into a broad false one — the same shape
  as the "Outputs match the GitHub mirror" projection bug.

**DISABLED steps still refuse, and the asymmetry is deliberate.** Being
interactive is a declared property of the workflow; being disabled is a
switch. Carrying a disabled step's outputs would let anyone silence a
step and still attest around it. The AI Declaration needs no case of
its own — it is an interactive step, and the general rule covers it.

**A rerun that reached no verdict is NEVER written as an attestation.**
It used to be: a refusal became `sStatus: "failed"` plus a history
entry, i.e. a scientific claim keyed to a manifest digest saying the
project does not reproduce, on the strength of a precondition the run
could not meet. It also destroyed any earlier passing attestation the
unchanged manifest still entitled the project to. Both lanes now branch
on `bRerunAttempted` — `reproducibilityRoutes._fnRecordOutcome` and
`commandReproduce._fbWriteAttestationFromRun` — and they **must agree,
because they write the same file**. The hub remembers the reason
in-process (`_DICT_LAST_NO_VERDICT`) and the PROOF tab renders it; that
lifetime is the honest one, since nothing was established.

**A verification reports progress, or it reads as a hang.** The tab
polls only while the server says a verification is live, and disarms
when it settles. Until this existed the "started" toast was the last
thing a researcher saw — a 2.5 second refusal and a two-hour rerun were
indistinguishable from the chair. Do not make this a standing cadence,
and do not let the card pulse over a finished run.

**The shadow is destroyed, so the FAILURE RECORD is the only evidence.**
The rerun's status callback used to be `_fnDiscardStatusEvent`: every
step result and every line of output went to the floor, so a failed
rerun produced one sentence — "pipeline rerun exited non-zero" — about
a container that no longer existed. The researcher could not re-run the
shadow, could not read its logs, and could not tell a missing
dependency from a real divergence (researcher-reported, 2026-09-01).
`rerunDiagnostics.ftBuildRerunDiagnosticsCollector` keeps the first
failing step's label, name, exit code and a bounded output tail, and
both lanes persist it as `dictRerunFailure`. Three properties are
load-bearing: the FIRST failure (later steps fail because the first
did), a BOUNDED tail (this record is committed and published to Zenodo
— an unbounded log would put a researcher's whole console into a public
artefact), and unconditional FORWARDING (the CLI prints the same
stream; an observer that swallowed events would break the caller it was
added beside).

**Three states, not a boolean — twice over.** The row said "No current
rebuild attestation. Run this once every other check passes" over a
rerun that HAD run and reported a failing step, because the poll shipped
only `bRebuildAttestationCurrent`. "Never run", "ran and failed" and
"passed but stale" are different things and a researcher acts on them
differently; the poll now ships `dictRebuildAttestation` so the row can
tell them apart. Likewise `null` and `{}` in `dictRerunFailure` are
"this record predates capture" and "no step reported a failure" — the
migrators write `None` for exactly that reason.

**A shared summarizer does not know your new state.**
`fsSummarizeLevelStates` is shared with the Steps banner, so it counted
a `running` row as *nothing assessed* and painted a pulsing `?` over a
rerun plainly under way. `_fdictGroupStateByLevel` maps `running` to
`partial` before summarizing. When adding a level-cell state, check
every aggregation it flows into — the row and its banner are computed
by different code.

## The L3 rerun happens in a shadow container, not the researcher's

**Tier 5 no longer re-runs the workflow in the live project
container.** `shadowRerun.fdictRerunAndVerifyThroughShadow` is the one
entry point both attestation lanes use — the dashboard's
`/level3/verify` route and `vaibify reproduce --rerun`. It creates a
fresh container from the image digest the envelope's environment
snapshot pins,
copies the repository in, drives the shared `rerunVerification`
comparison against **the shadow's** filesystem, and destroys the shadow
with proof.

Two reasons, and the second is the one worth remembering. The rerun
used to overwrite the researcher's real outputs. And it exercised
whatever the project container had *become* — packages from a debugging
session, files from an interactive step — rather than the image
`reproduce.sh` would pull, so it could pass where a stranger's
reproduction would fail. `docs/architecture.md` carries the full model.

**`filesRepoLive` is the source of the image PIN, never the comparison
root.** The parameter is spelled that way on purpose: the older
function beside it takes a `filesRepo` that must be rooted on the
filesystem the rerun writes to, and passing the live adapter into the
comparison is the substitution that makes a verification grade a tree
the rerun never touched — every entry clean, every attestation passing.
`testTheComparisonIsRootedOnTheShadowNeverOnTheLiveRepository` drives
the lane with the two roots made distinct and is kill-confirmed against
exactly that swap.

**The shadow needs its own mutation admission, and forgetting it breaks
every rerun.** The rerun drives the ordinary `DockerConnection`, whose
execs ask the gate about the container id they name. On the dashboard
lane that runs inside a mode-(c) durable carrier opened for the
**project** container, so the shadow's execs are refused —
`MutationNotAdmittedError`, from inside a background task, reported to
the researcher as an unexplained attestation failure. That is not
hypothetical: the lane was written without it.
`commitCarrier.ftOpenDisposableContainerAdmission` is the seam, and it
is narrow in both directions —
`tests/testShadowContainerAdmission.py` asserts that the shadow's
admission reaches nothing but the shadow, and the project's reaches
nothing of the shadow.

**Do not read a green route-test as evidence this lane works.** The
route tests patch the shadow entry point out, precisely so they do not
touch a daemon. What exercises the lane is
`tests/testRerunVerifiesWhatItRan.py`, which builds the host clone, the
live container repo and the shadow copy as three genuinely distinct
directories and runs real commands against them, and
`tests/testDisposableContainerLive.py`, which drives a real daemon.

**The archive's parent directories must be stamped, not left to the
daemon.** A tarball may name `repo/data/file` without naming
`repo/data`, and both `put_archive` and `tar` then create the gap
ROOT-owned — so the container user owns its files and cannot create a
sibling beside them. Verified live: without the synthesized parents the
first write into the copied repository is refused. `disposableSpecification`
emits every parent as its own 1000:1000 member, and
`testFnWriteFileDefaultsToContainerUserOwnership` pins it as the second
tar-building write path.

**The shadow's execs run as the image's declared `USER`.**
`DockerConnection` resolves every exec's user from the image's
`Config.User`, falling back to `researcher`. The create specification
sets the container user numerically (`1000:1000`), which is always
valid, but the per-exec override is by NAME — so a shadow built from an
image that declares no `USER` and has no `researcher` account is refused
by the daemon with "unable to find user", on every step. An L3 envelope
pins the image vaibify built for the project, which carries the
directive, so this is a constraint on what a shadow can be built FROM
rather than a live defect. It was found by pointing the live test at a
stock base image; do not "fix" it by making the exec numeric without
working out what that does to the project-container lane, which relies
on the name.

**`/shadow` is not `/workspace`, and that is deliberate.** The shadow
carries no volumes at all, so borrowing the workspace name would invite
a reader to assume a mount that is not there. Ask
`shadowRerun.ftResolveShadowPaths` rather than composing the path.

**The repository export is coherence-pinned, and BOTH halves of the
check are load-bearing.** `coherentExport.fbaExportRepositoryCoherently`
observes every path git can enumerate immediately before and
immediately after the archive stream, and refuses unless (a) the two
observations are exactly equal AND (b) every archive member matches the
before-observation by an identity recomputed host-side over the
archived bytes. Neither implies the other, which is the thing most
likely to be "simplified" away:

- a file changed after the walk passed it leaves the archive perfectly
  consistent with the before-observation — only (a) sees it;
- a file changed and changed back leaves both observations identical —
  only (b) sees it.

Two registry entries exist precisely to prove that, one per half:
delete either check and exactly one of them survives. Do not collapse
them.

**The exemption is `.git/` only, and its narrowness is a real bug
class.** Git enumerates the working tree, not its own internals, so
`.git/` is exempt from the member check. Writing that test as
`startswith(".git")` instead of `startswith(".git/")` silently exempts
`.gitignore`, `.gitattributes` and `.gitmodules` — real,
manifest-relevant files — and
`testTheGitInternalsExemptionIsNarrow` is the kill-confirmed guard.
Any OTHER unobserved member is refused, naming a checked-out submodule
as the likely cause, because a submodule's files are listed by no
superproject git command.

**A researcher is warned before the copy, and the warning has one
home.** `VaibifyApp.fnConfirmLevel3Verification` is the single opener;
both entry points (the Project block's `verify-l3` action and the PROOF
tab's own Verify button) call it, because a safety warning maintained
in two places is one that drifts. The CLI prints the equivalent notice
rather than prompting — a prompt would break every unattended
`vaibify reproduce --rerun`. Neither is the safety mechanism: the
export refuses a torn copy either way. They exist so a researcher who
meets that refusal was already told what causes it, which is the whole
premise of vaibify — never a baffling debugging session.
`tests/browser/testVerifyWarnsBeforeCopyingTheProject.py` asserts the
ORDER (no POST before the confirm), not the wording.

**Two container facts this lane depends on, both found the hard way.**
`get_archive` cannot read out of a **tmpfs** mount — it answers 404 for
a directory an exec in the same container lists happily. It reads out
of a named **volume** fine, which is what real project repos live in
(verified live). And `DockerConnection` execs `/bin/bash`, so an image
without bash fails with a non-zero exit and empty stderr, which reads
like a broken program rather than a missing shell.

**The disposable lifecycle is SHARED with the Agent Council.**
`vaibify/docker/disposableContainer.py` (the SDK authority),
`disposableSpecification.py` (the pure half) and `daemonCapacity.py`
were extracted from the council's gateway so both lanes have one
container lifecycle. Changes here reach both. The ledger in the gateway
records reservations and outcomes and NOTHING else — admission quotas,
per-provider accounting and the idle-watchdog veto belong to whoever is
spending the resource, and the council wraps this rather than replacing
it. Do not grow those policies into this module.

**Bound a container from the DAEMON's memory, never the host's.** On
Linux they agree and the bug is invisible; on macOS the daemon is a VM
with its own allocation (measured: 16 GB host over an 8.3 GB daemon), so
a container sized from host RAM is over-provisioned and the kill arrives
mid-workflow. `daemonCapacity` keeps the two figures apart by name —
`iHostMemoryBytes` bounds what the hub materialises in its own address
space, `iDaemonMemoryBytes` bounds the container — and
`tests/testDaemonCapacity.py` drives them 16x apart so a test cannot
pass while reading the wrong one.

## Container mutations go through the commit-guard carrier

**Arbitrary command execution is always treated as mutating**, because
the primitive cannot know whether the text it was handed reads a file
or deletes a workspace. Inside an enforced lane — an HTTP request
served by `routeScope.ContainerAwareRoute`, or a carrier-launched
durable task — an exec or a container write without a live carrier
admission raises `MutationNotAdmittedError`. Outside one (background
threads, the CLI, direct library use) the gate is a no-op: that
remainder is deliberate and named, never a silent claim of coverage.

**A typed read is exempt only inside its adapter.** Reading a file
means running a program in the container, so guarding the exec would
refuse reads too. Exactly one private method,
`DockerConnection._ftRunTypedRead`, grants the exemption. It takes
an operation NAME from a fixed table, plus a path **or a flat sequence
of paths**, and builds the command itself; it never accepts one. The
sequence form was added on 2026-08-05 for the batched file-existence
probe: the alternative was up to 1000 container round-trips on a
debounced UI path. It widens what the adapter may be *given*, never
what it may be *told to run* — `repr()` of a validated list of strings
is as inert as `repr()` of one. An adapter that forwarded a caller's string would
turn the read carve-out into a general bypass —
`tests/testMutationBoundary.py` fails the build on one that does, and
on a second grant point anywhere, pinning the name through
`S_EXEMPTION_METHOD`.

(This paragraph named `_texecRunAuditedRead` until 2026-08-04, a symbol
that exists nowhere in the repository. The enforcement was always
correct — the test reads the real name — but a security contract whose
stated grant point cannot be grepped is one an agent will conclude does
not exist, and two separate tracks reported it before it was fixed.)

**`tests/mutationInventory.json` carries three records, and only one of
them is completeness-critical.** Regenerate with `python
tools/generateMutationInventory.py --write`; drift-check with
`--check`.

- **Acquisitions** — every import or attribute-load of a member in a
  closed dangerous vocabulary: `subprocess.*` launchers, `os.system` /
  `exec*` / `spawn*` / `popen`, `asyncio.create_subprocess_*`,
  `pty.spawn`, multiprocessing and process pools, Docker client
  constructors and low-level `APIClient` methods, direct Unix-socket
  access, process signalling (`os.kill` / `os.killpg`), and reflection
  (`eval`, `exec`, `sys.modules[...]`, `importlib`, `__import__`,
  dynamic `getattr`). **This is the completeness boundary and it fails
  closed.** Importing `os` is not acquisition; `from os import system`
  is — 33 GUI modules import `os`, so a module-level reading would be
  useless.

  Signalling joined the vocabulary on 2026-08-10 and is worth a
  sentence, because it is the one member that is not command
  authority: a signal cannot make a process do anything new, only stop
  one. It went unrecorded for as long as every signal vaibify sent went
  to a process vaibify had created and was tracking. Host mode changed
  that — `hostCancellation` signals a process group named by a number
  read back out of a journal file, on the researcher's own machine.
  The scope is the namespaced `os` surface only: a bare
  `processChild.kill()` on a Popen handle is not matched, because
  `.kill()` and `.terminate()` are ordinary method names shared with
  threads and test doubles, and matching them by spelling is the defect
  this scanner exists to avoid. The launch that produced such a handle
  is already an acquisition.
- **Use sites** — decoded calls and commands. **Metadata,
  best-effort.** A launch whose argv the scan cannot read becomes a row
  with an UNKNOWN command, never a site that disappears.
- **Dispositions** — the reviewed judgement per module or named
  function: forbidden, guarded, or separately authorized.

Completeness rests on the ACQUISITION, not on decoding the command,
because decoding depends on reading an expression somebody else writes.
The withdrawn host-side director module is the demonstration: its
`subprocess.Popen(sCommand, shell=True)` was the most permissive command
authority under `vaibify/gui/` and produced **zero rows** under the old
design — one blind-spot entry, nothing more.

The drift check fails on an added, removed, duplicated, edited, or
hand-altered row, on acquisition drift, and on blind-spot drift. Three
ratchets may only fall: `I_UNCLASSIFIED_ROW_BUDGET`,
`I_UNDISPOSED_ACQUISITION_BUDGET`, and `DICT_UNRESOLVED_BUDGET`. **The
scanner never decides reachability** — a human judgement recorded
against a fingerprint is honest about being a judgement, where a
scanner's reachability verdict would pretend to be a proof. And a
fingerprint is an identity, never a warrant: for an opaque site the
expression is `subprocess.run(listCommand)` both before and after the
builder filling it is swapped from git to `docker rm`, so a manual
disposition must name the supporting symbols its review relied on.

**A generated ledger is never hand-edited, and never hand-merged.**
`tests/mutationInventory.json`, `tests/hostCapabilityInventory.json`
and `tests/styleInventory.json` are all built by a tool. Their on-disk
layout is load-bearing, not cosmetic — one record per line, no field
holding the length of a list beside it — because a ledger is
regenerated by every branch that touches the source it scans, and a
multi-line record puts git's three lines of context inside a single
record. `tools/ledgerFormat.py` is the single renderer and explains
each property; `tests/testLedgerFormatIsCanonical.py` fails when a
checked-in file is not byte-exactly what its generator writes. That
test exists because every other check parses JSON first and so cannot
see formatting at all: one ledger was rewritten by hand with
`indent=1` against a generator writing `indent=2`, every drift check
stayed green, and the next `--write` reformatted ten thousand lines.

**When a ledger conflicts on merge: resolve per record, then
regenerate.** Each side of a conflict is now a set of whole records,
one per line, so choosing between them is a reading task rather than a
JSON-repair task. Keep every record either side recorded a judgement
on, then rebuild:

```bash
$EDITOR tests/mutationInventory.json          # keep BOTH sides' records
python tools/generateMutationInventory.py --write
python tools/generateMutationInventory.py --check   # must print {}
```

**Do not resolve by taking one side wholesale.** Regeneration carries a
reviewer's judgement forward from *the file on disk* — verified by
stamping a disposition, regenerating, and finding it intact — so
`git checkout --theirs` silently discards every disposition your branch
recorded, and the drift check cannot tell you, because a row reverted
to `UNCLASSIFIED` is a legal row. The ratchets in
`tests/testMutationInventory.py` are the only thing that would notice,
and only if the count crosses a budget.

Regeneration then normalizes ordering and drops anything the scan no
longer finds, so `--check` printing `{}` means the rebuilt file agrees
with the source. Read its `removed` keys before assuming a clean
rebuild: a row your branch deleted takes its recorded review with it. A
row whose enclosing scope moved keeps its review but gets a new
`sScopeFingerprint` — that is the mechanism asking you to re-read the
judgement, not to re-stamp it.

**A route declares its carrier mode, and the declaration authorizes
NOTHING.** `routeScope.ffnDeclareCarrierMode` stamps one or more of
`typed-read`, `mode-a-synchronous`, `mode-b-lock-held`,
`mode-c-durable`, `lifecycle-transaction`, `separate-authority` onto a
handler. A declared route takes a branch with NO admission, so its
handler must open one through a carrier around each logical mutation;
forget one and the primitive raises `MutationNotAdmittedError`. **That
refusal is the proof** — a decorator that pre-admitted the handler
would delete it, which is the `bAgentSafe` mistake one level up.
`testDeclaringMintsNoAdmission` drives a declared route over real HTTP
with the owner map keyed by a name != the container id and asserts the
real gate refuses. Why each mode exists, and what the migration found,
is in [docs/architecture.md](docs/architecture.md) — "Container
mutations announce themselves".

**Two rules that are easy to undo by accident.** A refusal is not an
I/O error: `MutationNotAdmittedError` and `CommitRefusedError` derive
from `ControlPlaneRefusalError(Exception)`, never `PermissionError` —
they used to, and every `except OSError` in the package swallowed them,
which is how a refusal came to silently downgrade a reproducibility
badge. And a carrier worker must not raise an expected 4xx/502: that
poisons its journal record and quarantines the container. Carry it back
through `routeContext.fdictCarryARefusalBackInsteadOfRaising` and
re-raise outside, after the record settles. A genuinely half-finished
write still poisons, correctly.

**For the current coverage, run the command — do not trust a number
written here.**

```bash
PYTHONPATH=. python tools/carrierIntentAudit.py
```

It prints every container-scoped route with its declaration, or
`(awaiting)`. The counts used to be prose in this file and went stale
four times in one session, because they change on every batch while the
sentence does not. Two routes are `APIWebSocketRoute`s that
`app.router.route_class` never governs, so the resolved population and
the governed one differ by two; the command reports the governed one.
The migration was scoped to mutating routes, so the awaiting list
bottoms out at the read-only routes rather than empty.

**There is no production observation point**: nothing
under `vaibify/` records a carrier observation, so
`tools/carrierIntentAudit.py` compares only what the suite drove, and
an empty violation list is not compliance —
`flistSelectDeclarationsNeverObserved` keeps that visible. An
observation records what its entry point DECLARED, never *which* entry
point it was, so a violation cannot be narrowed between two routes
sharing a declaration; migrating one route at a time is what bounds the
diagnosis. And some mutation-capable rows are **structurally**
unattributable: a primitive bound into `asyncio.to_thread` loses its
row, because inside the worker the frames above it are executor
infrastructure rather than the expression the row records. Its carrier
MODE survives, so the event is still routed correctly — the row is what
is lost, and those must be traced by hand and will never be observed.
Migrating a route can *recover* one, by turning the passed callable into
a direct call the scanner can read; the current set is every
`passed-callable` row in `tests/mutationInventory.json`. The semantic
classification of the inventory is unfinished
and ratcheted: the count may only go down, and it is the input to that
migration, not a substitute for it.

**A busy container refuses a hand-over at once, and names what is busy.**
The lock HOLDER registers its operation kind and target, because an
`asyncio.Lock` knows only that it is held. A transfer never waits for a
drain — waiting spends the capability's window on an operation of
unknown length — and there is no DRAINING phase: a transfer refuses over
any terminal execution nobody has proven dead.

## The terminal serves both modes, and costs the quiescence claim

**`/ws/terminal` serves container projects AND host projects
(2026-08-15 ruling).** A host project's shell is a real PTY on the
researcher's own machine, launched by the host gateway's suspended-gate
primitive and journaled with the `terminal` kind before its first
instruction.

Containment of a terminal is **not proven and cannot be assumed**. A
shell can `setsid` out of the session the containment record tracks,
so "the terminal stopped" is not provable. Vaibify therefore does not
claim it: **a project in which a terminal has run reports quiescence
UNPROVEN and routes to `vaibify reconcile`, never quiet.** Do not
weaken that back — a release that reports quiet after a terminal is a
false statement, and the feature is only defensible because the
statement is true. The cost is real and intended, in both modes, and
the host lane adds a second honesty device: every host session's first
output is a banner saying the shell runs on the researcher's own
machine and that processes can outlive the tab (the host-mode modal is
the standing consent; the banner is the per-session reminder).

`terminalContainment` and the `terminal` journal kind are what make the
weaker claim honest — the record is the difference between a detached
process being *unproven* and being *invisible*. Deleting either removes
the honesty, not the risk.

**Ordering in the handler is the contract**: gate, then branch on the
host mode, then `require` the daemon, then build the session. The gate
is the shared `fiContainerSessionRejectionCode` guard the pipeline lane
uses — never an inlined membership check, or the two lanes drift about
who owns a container. A session built before the gate would put a
quarantine-bearing operation on a project for a caller with no
standing in it; `require` before the host branch would answer "install
Docker" about a project that never wanted one; and the branch decides
WHICH session class carries the record — `HostTerminalSession`, never
the Docker class, for a host project.
`testTheTerminalRouteGatesBeforeItBuildsAnything` pins it.

**Host containment is SESSION-wide, on both halves.** A shell's job
control moves children to new process groups within its session
(verified live: a backgrounded `disown`ed job wears its own pgid), so
the probe enumerates by session id, and the drain delivers per-member
(`hostCancellation.fnSignalSessionMembers`) — a `killpg`-only probe or
delivery would miss exactly the stray this machinery exists to find.
The reconcile-time prover for a crashed hub's host terminal record
does the same sweep (`_fdictProbeHostTerminalOperation`); killpg-empty
is treated as necessary, never sufficient.
`I_REJECT_TERMINAL_NOT_ON_HOST` is RESERVED, no longer emitted: hubs
between 2026-08-11 and 2026-08-15 refused host terminals with it.

**The handler resolves the container name before the gate**, so a
caller that can reach the socket can distinguish a real id from a
fabricated one. That is a property of the WebSocket gates in general —
`/ws/pipeline` has the identical ordering — so treat it as one boundary
to fix in both lanes or neither, never as a terminal-specific hole.

Four controls keep the feature contained. A no-callers invariant over
`terminalContainment` **cannot** pass, because the module keeps
production callers for drain, reap and shutdown, so they are narrower:
only `vaibify/gui/routes/terminalRoutes.py` constructs a `TerminalSession`
(`testOnlyTheGatedRouteConstructsATerminalSession`), so every shell is
one the gate admitted; only one handler answers the path
(`testOnlyOneHandlerServesTheTerminalWebSocket`); only the seam names
the record-creation calls
(`testOnlyTheSeamPreparesATerminalExecutionRecord`), so no shell runs
without the record the quiescence claim depends on; and every other
containment caller is cleanup
(`testRemainingContainmentCallsAreCleanupOnly`).

**A terminal journal record is never swept** — not by an upgrade, not
by a later session. It stays on disk and keeps its container
QUARANTINED until the container is positively stopped or its process
group proven empty, i.e. through `vaibify reconcile`. Opening a
terminal settles nothing about an existing record, because it has
proven nothing (`tests/testWithdrawnTerminalLegacyRecords.py`).

**`tests/testTerminalContainment.py` keeps the process-group prover as
a standing demonstration that it cannot see a `setsid` descendant.** It
is not a gate to be satisfied; it is the evidence for the limit stated
above. A green run there is not containment.

## Cross-step references via tokens

**Every cross-step file reference in a vaibify workflow script must be
passed as a CLI argument, named in the step's command via a
`{step:<sStepId>.<stem>}` token.** Hardcoded paths in Python (or any
other language) that cross step boundaries are forbidden. The contract
is that the workflow JSON, parsed mechanically, declares the complete
dependency graph.

The canonical form is symbolic — `{step:generate-samples.samples}` —
keyed on the target step's `sStepId`. The older positional
`{StepNN.varname}` form still parses but is **deprecated**: the number
renumbers on any insert or reorder, which is the reorder-drops-a-step
hazard. Shipped templates must use the symbolic form
(`testTemplateCommandsUseSymbolicNotPositionalTokens` enforces it), and
new workflows should too. The single pattern is
`S_STEP_SYMBOLIC_PATTERN` in
[workflowManager.py](vaibify/gui/workflowManager.py).

The dependency parser scans only those tokens in command strings. It
cannot introspect arbitrary script source. A cross-step reference hidden
inside a script literal (e.g. `path = "../OtherStep/output.json"`) is
invisible to the parser, so the dependency edge does not exist in the
graph, `bUpstreamModified` cannot fire correctly, and the workflow
cannot honestly reach PROOF Level 1.

A step's script *may* read its own step-directory files via hardcoded
relative paths. The boundary is the step. Anything from another step
must be tokenized.

The `saDependencies` field is the escape hatch when the data flow
doesn't naturally express the dependency (e.g., a plot script
implicitly relies on a sibling step's output): list one or more
`{step:<sStepId>.*}` tokens there and the parser will pick the edge up.

Full developer documentation, including the naming convention,
worked examples, and how to handle colliding basenames, is in
[vaibify/docs/scriptAuthoring.md](vaibify/docs/scriptAuthoring.md). The architectural
invariant `testTemplateCommandsUseStepTokens` in
`tests/testArchitecturalInvariants.py` enforces this rule on every
vaibify-shipped template at CI time.

## Adding a UI action

Every new state-mutating HTTP or WebSocket route that a researcher can
invoke from the UI must also be registered with the agent-action
catalog — either by an entry in `LIST_AGENT_ACTIONS` plus a
`@ffnAgentAction("action-name")` decorator on the handler, or by an
explicit entry in `SET_INTENTIONALLY_EXCLUDED_PATHS` (with a short
rationale on the same line or in the preceding comment block) if the
route is genuinely not agent-invokable.

Unregistered state-mutating routes are invisible to the in-container
agent: when a researcher says "Claude, run unit tests on step A09",
the agent has no way to translate that request into a backend call,
so the dashboard silently drifts out of sync as the agent improvises.

**`bAgentSafe` is enforced server-side (2026-07-26).** It used to be
advertisement: `ffnAgentAction` changes no behaviour, and the flag was
consumed only by `vaibify/containerImage/vaibifyDo.py` *inside* the container, which
an agent bypasses with `curl`. `SessionTokenMiddleware` now resolves
each request to its route template and refuses the agent lane for any
route whose catalog entries are all `bAgentSafe: False`, for anything
in `SET_INTENTIONALLY_EXCLUDED_PATHS`, and — **failing closed** — for
any state-mutating route with no catalog entry at all.

Two consequences follow. Getting `bAgentSafe` wrong is now a security
decision, not a documentation one: marking a destructive route safe
hands it to a compromised agent. And forgetting to register a route
denies the agent rather than silently admitting it, so a new action
that "does nothing when the agent calls it" is usually a missing
catalog entry.

The gate is HTTP-only — `BaseHTTPMiddleware` never sees a `websocket`
scope — so WebSocket actions rely on
`testEveryWebSocketActionIsAgentSafe` as a tripwire instead. A route
that reads host filesystem state needs its own
`_fnRejectAgentTokenLane` call at the handler; the catalog cannot
express that capability on its own.

`tests/testArchitecturalInvariants.py::testAgentActionRegistered`
fails CI if the registration is missing, and
`tests/testAgentLaneEnforcement.py` drives every non-agent-safe route
with an agent token and asserts a 403.

The catalog authority is
[vaibify/gui/actionCatalog.py](vaibify/gui/actionCatalog.py); the
agent-facing CLI that consumes it is
[vaibify/containerImage/vaibifyDo.py](vaibify/containerImage/vaibifyDo.py); documentation for
in-container usage lives in the embedded CLAUDE.md generated by
[vaibify/containerImage/entrypoint.sh](vaibify/containerImage/entrypoint.sh).

## Personal AI Configuration is a QUESTION, not an artifact

The Level 2 row named **Personal AI Configuration** asks the
researcher one thing: did your own private, host-side agent setup —
global instruction file, personal skills, memory, hooks — govern this
work, and are you disclosing it? It is instruction-stack layer 4 (the
other three: the harness system prompt, the vaibify-generated
container context, and the project's own context file). **Answering
is the criterion. Disclosure is never required**: `none`,
`declared-private` and `included` all pass, and `declared-private`
with nothing further is a complete answer. Only *unanswered* fails.

Two things follow, and both have already gone wrong. It is not a
"system prompt" — skills, memory and hooks are not prompts, and the
harness system prompt is a DIFFERENT layer of the same stack, so
using that phrase here names the one thing it excludes. And it is not
a file the researcher must produce, so never tell them to write or
find one; a researcher with no personal layer answers `none` and is
done.

The researcher-facing label is the only thing that was renamed
(2026-08-24, from "Personal instruction layer"). The identifiers,
route path and persisted key remain `personalLayer` /
`dictPersonalLayer` — renaming a stored schema key would strand every
project that has already answered. `docs/architecture.md` carries the
full model; `vaibify/gui/personalLayerManager.py` and
`vaibify/gui/static/scriptPersonalLayer.js` own the behaviour. The
hash-commitment route is browser-only twice over (excluded from the
catalog AND rejected at the route for agent tokens), because only the
researcher can answer truthfully for their own machine.

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

## A container may host Claude, Codex, or Gemini

Container agents are overlays selected by feature flags
(`vaibify/containerImage/Dockerfile.claude`, `.codex`, `.gemini`). Each installs **as
the unprivileged container user**, not root, so the provider's own
updater can replace its binary without sudo — do not "fix" that by
installing as root, and do not add sudo to the image.

**Cline is the exception to the flat-name rule.** Six of the seven
agents read a repo-root markdown file, so a symlink serves them. Cline
reads a `.clinerules/` **directory**, so `fnLinkClineRules` creates it
with a symlink inside pointing back at the canonical file — a flat
`.clinerules` symlink would be a file where a directory is expected.
It is gated on Cline being installed, because a stray directory in the
researcher's repository is the sort of thing that gets committed by
accident. `tests/testEntrypointAgentDocLinks.py` fails if an agent is
added to the skills loop without a path to the guidance, which is
exactly how Cline came to ship with skills and no instructions.

**Agent-facing docs have one source and several names.** Inside the
container the canonical file is
`/workspace/<repo>/.vaibify/AGENTS.md`; `entrypoint.sh`'s
`fnLinkRepoClaudeMd` symlinks `/workspace/<repo>/CLAUDE.md`,
`/workspace/<repo>/AGENTS.md` and `/workspace/<repo>/GEMINI.md` to it,
and migrates a legacy CLAUDE.md in that directory into place. So write
in-container agent guidance once, to the canonical file. Never author
a provider-specific one — a second *real* file at one of those names
shadows the symlink for that provider only, and the three agents
silently start reading different instructions.

(These are container paths, deliberately absolute:
`tools/checkAgentDocsPaths.py` resolves repo-relative references and
would flag them as broken, because they exist only inside a running
container.)

**All three agents share one login store and one user.** Each agent's
config directory is persisted into the workspace volume and symlinked
back into the home directory (`fnPersistAgentConfig`), so logins
survive container recreation. This pattern predates multi-agent
support; what changed is the blast radius. Every agent runs as the
same container user, so file permissions isolate nothing between
them: whichever agent is compromised can read all three providers'
credentials, and they are reachable through the dashboard's file
routes like any other workspace path. Treat "an agent was
compromised" as "every configured provider's session was exposed"
when reasoning about a threat, and do not add a fourth provider
without revisiting that.

## Runtime resources live inside the package

`vaibify/templates/` and `vaibify/containerImage/` ship in the wheel.
They used to be reached with `parents[N]` from the repository root,
which resolves to `site-packages` in an install — so no wheel ever
contained them and `vaibify init` printed "No templates found" and
exited 0. Four rules follow; the full account is in
[docs/architecture.md](docs/architecture.md) — "Packaging: why runtime
resources live inside the package".

**Locate them only through `vaibify/resources.py`.** It is the single
place that names the trees, and `importlib.resources` resolves them
identically from a checkout, an editable install, and a wheel. **Never
reintroduce a `parents[N]` walk to reach package data** — and after
fixing any resolution bug, grep for every other way the codebase reaches
outside the package, not just the spelling that bit you.

**Treat them as read-only, and give every build its own copy.**
`commandBuild.fsStageBuildContext` mkdtemps a private context under
`~/.vaibify/build/`, discarded on success and kept on failure with its
path printed. It is per *build*, not per project — two dashboard clicks
race, and refreshing a shared directory starts with `rmtree`.
`tests/testPackagedResources.py` fails if this regresses.

**Anything the image needs must live under `vaibify/`.** The curated
agent docs at `vaibify/docs/` are **symlinks onto the Sphinx sources** —
never replace one with a real file, which is the shadowing trap;
`testCuratedDocsRemainSymlinksOntoTheSphinxSources` fails if you do.
When adding one: add the symlink, extend `T_STAGED_DOCS`, extend the
doc-map skill's table, and add the *Sphinx source* path to
`freshImageBuild.yml`'s triggers.

**Prove the distribution, not the import.** `pip-install.yml` runs
`tools/checkInstalledDistribution.py` against an installed sdist and
wheel. It is **release-only**, so a packaging regression can sit on
`main` until a version is cut — after touching `vaibify/resources.py`,
the packaged trees, or the Dockerfile `COPY` set, run it by hand
(`workflow_dispatch`). Never make it a required status check: it cannot
report on a pull request, so every PR would wait on it forever.

## Known technical debt

These are known, deliberate, and load-bearing — do not "fix" them
without discussion:

- `introspectionScript.py` duplicates format-handling logic from
  `dataLoaders.py`. Container scripts cannot import from the host.
- `scriptFigureViewer.js` was not part of the 2026-01 frontend
  refactor. Kept as a single cohesive module.
- Re-export blocks exist across `pipelineRunner`, `pipelineServer`,
  `testGenerator`, and `syncDispatcher` for backward compatibility.
  Callers should migrate toward canonical imports over time; do not
  delete the re-exports until external callers are updated.
- `vaibify/reproducibility/githubWorkflow.py` is implemented, tested,
  and **unreachable** — no product code imports it. Kept rather than
  deleted because wiring it expands remote-execution surface, which is
  a product decision. `tests/testOrphanedPublishMachinery.py` fails if
  the docs re-advertise it or if it gains a caller while still marked
  unreachable.
- `condaPackages` is refused at validation rather than installed. The
  Dockerfile installs Miniforge for a non-pip package manager but has
  no `conda install` step and no build argument carries the list, so
  accepting the field produced a container without the requested
  packages and said nothing. Wiring it is the honest fix; refusing is
  the honest interim.
- **`POST /api/zenodo/{id}/download` cannot work, and its two tests
  pass anyway.** It calls `syncDispatcher.ftResultDownloadDataset`,
  which exists nowhere — verified at runtime, `hasattr` is `False`, so
  every real call raises `AttributeError` and answers 500. The tests in
  `testSyncRoutesCoverage.py` patch the name into existence with
  `create=True`, which is why the suite has been exercising a function
  the product does not have. It is advertised to the in-container agent
  as `download-zenodo-dataset` with `bAgentSafe: True`, so an agent
  asked to fetch a dataset calls it and fails. **Do not "fix" this by
  deleting or loosening the tests** — the missing function is the
  defect. It is also the one mutating route left undeclared by the
  carrier migration, deliberately: inside a carrier that
  `AttributeError` would poison the journal and quarantine a working
  container over a broken button. Writing the function is a feature
  decision.
- `terminalContainment.py` and the `terminal` journal kind also
  reconcile records written by EARLIER hub versions — release, the
  safe reaper and shutdown all settle terminal records through
  `fdictTerminateAndProveRecord` — and
  `tests/testTerminalContainmentLive.py` keeps the container
  process-group prover as the standing demonstration that it cannot
  see a `setsid` descendant (`tests/testHostTerminal.py` carries the
  host leg's twin demonstration). This bullet used to say the
  in-memory registry was permanently empty; the terminal came back
  for containers on 2026-08-11 and for host projects on 2026-08-15,
  so live sessions register in production again.
- `commitCarrier.fdictRequestDurableTaskCancel` has no caller and
  refuses everything. Kept deliberately: Python cannot interrupt a
  worker in `asyncio.to_thread`, so there is no honest generic cancel,
  and a reader who finds no function at all re-derives that from
  scratch — or writes one. The refusal is the answer, in the place the
  question is asked.
- The poison axis is NOT subsumed by the journal quarantine. A
  quarantine record survives a crash; it does not fence a socket that
  is open right now. Poison does both — the pipeline lane is refused at
  the gate and revalidated per frame — so the two are complementary,
  not redundant.

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

## Lessons

This section records specific mistakes made in past sessions that are
worth remembering. It is empty at initial commit. Add entries as they
come up — one line each, pointing at the offending pattern and the
correct approach.

- The pre-refactor claim route short-circuited to `bClaimed: True`
  whenever a container was already locked, silently admitting a second
  same-hub browser tab. A claim must ARBITRATE (unowned grant /
  same-lease idempotent / foreign 409), never unconditionally succeed;
  `testClaimRejectsForeignLease` guards this.
- A green test suite is not proof of a working guarantee: the
  one-session refactor's owner map was keyed by container *name* on the
  write path but looked up by container *id* at the WebSocket gate, so
  every real hub session would have failed closed — yet the suite stayed
  green because its fixtures used name == id and never drove the live
  WebSocket/viewer paths. When a behavior crosses the
  HTTP/WebSocket/container boundary, assert it with name != id and an
  actual connection, not a unit stub.
- Five parallel agents changed JavaScript in one session and none could
  load a browser; the merged branch was green and the frontend entirely
  unexecuted. A green Python suite says nothing about the frontend.
  Load the page — see "Required after JS changes".
- `VaibifyApp` and friends are declared with `const`, which creates a
  global *lexical* binding, not a `window` property. A probe using
  `window.VaibifyApp` reports `undefined` for a module that is working
  perfectly. Use the bare identifier.
- A startup sweep deleted every "stale" credential file in
  `~/.vaibify/tmp`, including one an existing container had
  bind-mounted for months, leaving it unstartable (Docker fails the
  mount and creates a directory stub where the file was). Age is not
  evidence that a host file is garbage — reachability is. Before
  deleting anything under `~/.vaibify`, ask the daemon what it still
  mounts.
- A guarantee stated only in prose is not enforced, and mutation
  testing cannot find it: there is no mutant for a guard that was
  never written. `bAgentSafe` was metadata, the force-push hook missed
  every ordinary invocation order, and the science-name scan could not
  match the identifiers it existed to catch — all three passed CI for
  months. When adding a rule here, name the test that fails when it is
  broken.
- An import is not a smoke test. The release workflow validated every
  distribution with `import vaibify; print(__version__)`, which passes
  for a wheel containing no templates and no Docker build context —
  exactly what every wheel contained. The failure was not subtle
  (`vaibify init` printed "No templates found") but it was invisible
  to CI, and `init` exited 0, so even a human running it saw success.
  When a check exercises a component, ask what a *user* does with it.
- A template nobody executes is not a template. The shipped `workflow`
  template put `GenerateSamples` in a directory named `Sampler` —
  which vaibify's own slug contract forbids — and invoked two scripts
  that existed nowhere in the repository. Every new project from it
  opened red and could not run. Three template tests existed; all
  three checked token syntax, none loaded or ran the thing.
- Fixing the instance is not fixing the class. The packaging fix
  repointed the templates and the build context, added a locator, and
  shipped a distribution check — and missed the shell completions,
  which had the identical bug two directories away
  (`dirname(dirname(__file__)) + "completions"`, resolving to a path
  present in no install *and no checkout*). Completion had never
  worked for anyone, and first-run setup wrote a permanent marker
  saying it had. After fixing a resolution bug, grep for every other
  way the codebase reaches outside the package, not just the spelling
  that bit you.
- A marker that records "setup done" must not be written after a step
  that silently did nothing. It is checked forever, so one bad write
  makes the defect permanent for that machine.
- A CI step that reports success for having run nothing is worse than
  no step. The live-Docker job was guarded by `docker info || exit 0`,
  so an unreachable daemon turned it green; `pytest -m docker` was
  selected by no job at all. Both looked like coverage on the workflow
  list. When a check can be skipped, ask what the skip reports.
- A source-mutating tool must not share a working tree. Running
  `reconfirmFalsification.py` beside any other pytest run made that
  run read half-mutated source, producing failures in tests nobody had
  touched — twice diagnosed as flakiness or test-order leakage, which
  is the natural wrong answer. It now mutates only inside a disposable
  git worktree, and refuses a dirty checkout unless
  `--include-local-diff` is passed. Note the diagnostic: with no
  randomisation plugin installed, pytest's collection order is fixed,
  so two identical-order runs failing in *different* tests cannot be
  an ordering problem — that pattern means something outside pytest is
  editing the sources.
- **A count of code facts written into this file is wrong within
  weeks.** The carrier migration's route totals were re-typed and wrong
  four times in a single session (14/116, 31/99, 53/77, 60/70), each
  corrected only because somebody happened to notice; auditing for that
  found two more already stale — a "27 test files" that was 28, and a
  "20 unattributable rows" that migrations had reduced to 10. None was
  wrong in substance and all were wrong in fact, which is worse, because
  a reader who checks one and finds it false stops trusting the ones
  they cannot check. **State the mechanism, not the tally**, and where a
  number is genuinely wanted give the command that computes it —
  `PYTHONPATH=. python tools/carrierIntentAudit.py` for carrier
  coverage, `python tools/listModules.py` for structure. This is the
  deterministic-versus-stochastic split from
  [docs/vibeCoding.md](docs/vibeCoding.md) applied to this file: a fact
  that changes when the code changes does not belong in prose that
  does not.
- The carrier migration's only proof was unobservable in the tests that
  would have to observe it. "Forget a carrier and the primitive raises
  loudly" is true of the real `DockerConnection` and false of nearly
  every route test: **the route-test doubles answer a write by storing
  bytes and never consult the admission gate at all.** (Confirm with
  `grep -l 'def fnWriteFile' tests/test*.py | xargs grep -L
  mutationAdmission` — at the time of writing, all but one.) A migrated
  route with its carrier call deleted outright still passed its whole
  route-test file. The fix
  is `tests/testCarrierMigratedRoutes.py` — a double calling the same
  gates, under the same primitive names, at the same points the real
  connection calls them, recording the live admission MODE at each. Assert
  the mode, never merely that nothing raised: "no exception" is equally
  true of a route riding the ambient mint. Every future migration group
  needs an entry there; one verified against the ordinary route tests is
  not verified.
- An external review is evidence, not a verdict. A 2026-07-26 review
  correctly identified the browser/container execution hole and the
  doc drift, and was wrong about the falsification suite being
  order-unstable. A parallel audit over-reported the fixture-collapse
  sweep: of three flagged key pairs only one was real, because
  `sName`/`sDirectory` are *required* to agree by the slug contract
  and no code ever compares `sVersion` to `sExpectedVersion`. Check
  each claim against the source before acting on it; manufacturing a
  change to match a confident report is worse than ignoring it.
- **A falsification mutation detected through a SIDE EFFECT goes inert
  the day that mechanism is deleted, and nothing fails.** Three
  ownership-transfer guards are checked twice on purpose — once before
  anything is minted, once at the commit point — so disabling one copy
  changes nothing a caller can observe. They were killable anyway via
  the DRAINING phase: a doomed transfer must not drain the sitting
  owner's terminals. Deleting that phase (wave 2.4) silently made the
  discriminator vacuous, and for a week the harness reported three
  SURVIVED entries that read as three undefended ownership guards.
  Registry entries now carry `iExpectedOccurrences` so a guard with
  several copies has every one mutated. Two practices follow: when
  deleting a mechanism, grep the falsification docstrings that lean on
  it; and when an entry survives, first ask whether the mutation is
  *observable*, because "the guard is undefended" and "the mutation
  changes nothing" look identical in the report.
- A test that asserts an outcome plus a shared word does not identify a
  cause. The poison-transfer test asserted `S_TRANSFER_REFUSED` and
  `"reconcile" in sMessage` — both equally true of the live-terminal
  guard sitting below it, so the test was satisfied by a container
  carrying no poison at all. Where two guards can produce the same
  refusal, assert the text that names *this* cause.
- A check that can be skipped must say what the skip reported, and a
  check that is *timing out* is saying nothing at all. The macOS
  falsification legs had exceeded their 25-minute ceiling for weeks;
  raising it produced the first one that ever finished and immediately
  exposed seven entries that no daemon-less host can evaluate. A red
  lane nobody can read is indistinguishable from a lane that never ran.
- **A pull request whose base is not `main` runs no CI at all.** Every
  workflow is `on: pull_request: branches: [main]`, so a PR stacked on
  another open branch shows a clean, empty check list — the same shape
  as the older "a pushed branch with no PR runs no browser lane" trap,
  and just as easy to read as a pass. Retargeting the base does not
  trigger the workflows either; `gh pr close` then `gh pr reopen`
  does.
- **A git fixture inherits the machine's `init.defaultBranch`.** A
  host-mode test that pushed `HEAD:refs/heads/main` with `-u` passed
  on a laptop defaulting to `main` and failed all four falsification
  legs on runners defaulting to `master`, because the production
  `git push` was then handed a local branch and an upstream with
  different names. Pin it with `git symbolic-ref HEAD
  refs/heads/<name>` (works on every git version, unlike `init -b`),
  and reproduce a divergence like this locally with
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.defaultBranch
  GIT_CONFIG_VALUE_0=master` rather than pushing a guess at CI.
- **A browser-lane test can drive an event shape no browser sends.** A
  Shift+scroll handler was added so a researcher could reach the
  terminal scrollback while an agent held the mouse, and its
  browser-lane test passed in Chromium and Firefox — while the feature
  scrolled nothing at all for every real user. Browsers remap a shifted
  wheel onto the HORIZONTAL axis (`deltaY` 0, magnitude on `deltaX`);
  Playwright's `mouse.wheel` writes `deltaY` whatever the modifier
  state, because it injects at the DOM layer and the remap happens
  below it. The handler read `deltaY`, measured zero lines, and then
  still claimed the event — so the "fix" SUPPRESSED the scroll the user
  would otherwise have had. Driving a real gesture through a real
  browser is not the same as driving the event that gesture produces:
  where an input-layer transform sits between the two, construct and
  dispatch the event yourself. The same caution applies to touch, IME
  composition, and any modifier that changes an axis.
- **Agent-facing docs that specify procedure without semantics produce
  confident invention, not questions.** Four wrong answers reached a
  researcher in one session — qualitative tests are "user-only", an
  unattested AI Declaration blocks L1, making figures standards enables
  qualitative tests, and `iProofLevel` recited at a scientist — and
  every one came from guidance that said how to DO things and never
  what they ARE. The container docs named the three test tiers as a
  list of three words and never defined them; the L1 rule was stated
  without its carve-out; the blocker list existed only on the
  dashboard's poll path, so an agent asked "why am I not at Level 1"
  had raw JSON and nothing else. An agent will not stop at a gap, it
  will fill one. When documenting an action for an agent, state what
  the thing IS and what it does NOT establish, and give it a call that
  returns a verdict rather than fields to infer one from.
- **A local rename re-fingerprints ledger rows, in more than one
  ledger.** Renaming `sFullPath` to `sAbsolutePath` inside one function
  invalidated its row in `mutationInventory.json` (keyed on the
  enclosing scope) AND in `hostCapabilityInventory.json` (keyed on the
  command expression itself). Regenerating the first left the second
  drifted, and `--check` on one ledger reads exactly as clean as
  `--check` on all three. Worse, the first regeneration silently
  dropped that row's recorded security review — it came back
  `UNCLASSIFIED`, drift printed `{}`, and only
  `testTheUnclassifiedBudgetOnlyEverShrinks` noticed. After touching
  any function that composes container commands, regenerate and
  drift-check EVERY generated ledger, and diff the disposition count
  before and after.
- **A guard can be credited with a property the surrounding code
  already supplies, and then it is not a guard.** `_fsLevelCellState`
  checked `bUnknown` ahead of the counts, documented and tested as
  what keeps a stale verify cache out of `attained`. It never did:
  `iSatisfied` counts only `bMet is True`, so an unknown requirement
  already forces `iSatisfied < iTotal` and attainment is impossible by
  the arithmetic alone. All the short-circuit did was erase a
  researcher's positively-verified GitHub mirror behind a "?" because
  Zenodo had never been checked. Three tests defended it and none
  would have failed if the property were gone, because they asserted
  the MARK ("unknown") rather than the property (never `attained`).
  Before preserving a guard, find the line that actually supplies the
  thing it is credited with; and assert properties, not the symptom
  you expect a property to produce.
- **A constant that must equal a derivation has to be pinned to it,
  not re-typed.** `fdictBuildAiDeclarationStep` defaulted `sName` to
  "AI Declaration" and `sDirectory` to the independently-typed
  `"aiDeclaration"`, while the slug contract derives `"AIDeclaration"`
  from that name. Every declaration step vaibify built was therefore
  born violating vaibify's own contract, and the dashboard painted it
  a red error telling the researcher to rename a step the product had
  just created for them. Nothing caught it because creation validated
  the name and the directory independently — the guard existed only on
  the rename path. Two rules: pin the relationship
  (`assert CONSTANT == fsDerive(OTHER)`) rather than the spelling, and
  when a contract is enforced on edit, check whether creation enforces
  it too.
- **A projection that narrows its input set turns a proved failure
  into a green check.** The L2 reverify compared every canonical path
  against GitHub, recorded the AI declaration file as diverged in the
  project's cached sync status — and the per-step projection then intersected
  that divergence list with `_flistStepOutputFiles` alone, so no
  blocker was emitted and the row rendered "Outputs match the GitHub
  mirror". The gate whose purpose is not to overstate publication was
  asserting a match about a file it had just proved did not match.
  Two things to carry forward. When a comparison and its display are
  separated by a projection, the projection's set must be a SUPERSET
  of the comparison's — check that relationship explicitly, because
  both stages look correct in isolation. And a *narrow but accurate*
  label ("Outputs") reads as a broad claim once it sits in a
  requirement row, so it hides the gap rather than disclosing it.
- **A threaded parameter can be accepted and dropped, and every call
  site still reads correctly.** Wiring the GitHub verify cache into
  the badges meant adding one argument to four functions in a chain.
  The third link took it into its signature with a default and then
  called the fourth without it — so every badge silently read
  `unknown`, the honest-but-wrong answer, while a reader checking any
  individual function found nothing amiss. Signatures agreed; the wire
  was cut. Nothing about a green import or a green existing suite could
  see it, because the parameter's absence is indistinguishable from its
  default. Two habits: when threading a value through more than two
  hops, assert it arrives with a value the DEFAULT cannot produce; and
  distrust a change whose new tests all fail the same way, because one
  broken link and one wrong branch look identical from the assertion.
- **`git checkout <file>` to undo a kill-confirm mutation discards the
  fix with it.** Restoring from HEAD reverts every edit in that file,
  not the mutation — and the suite goes green afterwards, because the
  pre-fix code is what the pre-fix tests were written against. Copy
  the file aside and copy it back, and re-grep for a marker string
  from the change before believing the restore.
- **A fake that answers every input cannot exercise an adapter that
  refuses most of them.** `SnapshotRepoFiles` — the adapter the
  file-status poll passes — answers `fbIsFile` only for the paths one
  container exec sampled and raises `KeyError` for the rest, by
  design, because guessing would make a gate silently wrong. Every
  test of the new Level 3 envelope gate drove a hand-written fake
  whose `fbIsFile` answers anything, so all of them passed while the
  shipped gate raised on a dependency-declaration path, 500'd the poll, and
  blanked every badge and level cell on the researcher's dashboard.
  The tests and the code were both self-consistent and neither was
  the product. When a module has a permissive test double and a
  fail-closed production adapter, at least one test must drive the
  real one — and where a set in module A must be a subset of a set in
  module B, pin the relationship, because each edit looks complete on
  its own.
- **Run the mutations locally before pushing; the tool exists.**
  `tools/reconfirmFalsification.py --only <substring>` applies each
  recorded mutation in a disposable git worktree, runs the named test,
  and reports KILLED or SURVIVED — about a minute for a handful of
  entries, against ~20 minutes for a suite and far longer for a CI
  round trip. `--include-local-diff` replays uncommitted work; it
  otherwise refuses a dirty tree, because checking out HEAD would
  report on code you do not have. Three defects reached CI in one
  session that a single narrowed run would have caught, and the agent
  responsible had asserted that no local runner existed — the tool's
  own `--help` said otherwise. `testFalsificationRegistryIsWellFormed`
  is NOT this check: it verifies the mutation text still appears in
  the source, never that a test would notice the mutation.
- **Batch the small fixes; every one costs a full verification cycle.**
  A suite run is ~20 minutes locally and a CI round trip is longer, so
  finishing four small items in four passes spends over an hour to
  learn what one pass would have said. It also hides interactions: the
  blocker fix in one item is what turned another item's "no
  materialized force yet" into three red tests, and a batched run
  surfaced that immediately instead of a cycle later. "Deliberately
  scoped out" is a decision worth stating, but re-check it whenever a
  neighbouring change lands — the force that was absent is often
  created by the very next commit.
- **Retargeting a falsification entry is authoring a new mutation, not
  bookkeeping.** When a refactor moves the code an entry points at,
  the natural reflex is to repoint `old`/`new` and move on — it feels
  like updating a reference. It is not: the replacement text has to
  parse AND still kill. One retarget dropped a closing quote
  (`f"(git diff --cached ` for `f"(git diff --cached --quiet || "`),
  producing an unterminated f-string; the static registry check passed,
  because it only asks whether `old` appears in the source, and CI
  reported `ERROR: mutation does not compile` an hour later. Three
  entries were retargeted in that change, two were kill-confirmed, and
  the one treated as clerical was the broken one. Verify every
  retarget the same way as a new guard: apply it, `ast.parse` the
  result, run the named test, see it fail, restore.
- **A new early return converts every downstream guard's test into a
  tautology, and only the marked ones are noticed.** Adding the
  scope-version check to `_fbCachedSyncStatusFullMatch` made it refuse
  fixtures before the SHA, freshness and divergence guards below it —
  and those tests kept passing, because `False` is what they already
  asserted. CI's mutation run flagged the three that were
  `@pytest.mark.falsification`; a fourth was an ordinary unit test and
  was invisible to every lane (verified: deleting the divergence guard
  outright left it green). When adding an early return to a gate, list
  what sits below it and check that each one's test still fails when
  its guard is removed. Shared fixture builders are a control here, not
  a tidiness exercise: a hand-typed fixture gains a newly-required
  field in five files and silently not the other fourteen.
- **A kill-confirmation that edits by non-unique text mutates the
  wrong site and reports a false negative.** `syncDispatcher.py` has
  three byte-identical `(git diff --cached --quiet || git commit …)`
  guards in three functions, so a `str.replace(old, new, 1)` aimed at
  the third silently hit the first. The suite went green, which reads
  exactly like "this guard is not load-bearing" — the conclusion that
  gets a guard deleted. Before believing a mutation survived, `grep -c`
  the target text: more than one occurrence means the edit landed
  somewhere unknown. Mutate by line number, or by text that includes
  the enclosing function's unique context, and print the mutated region
  before running.
- **The obvious assertion often cannot tell two shell chains apart.**
  For `a && b || c && d`, the natural test — "a failed `add` returns
  non-zero" — passes against BOTH the grouped and ungrouped forms,
  because a failed add with a clean index stops either way. The forms
  diverge only when the index is DIRTY: ungrouped, the failure falls
  through to the commit, which succeeds on unrelated staged content and
  pushes it. A docstring claiming the simple case kills the mutant was
  written and was false. When pinning operator grouping, find the input
  where the two parses actually differ, and verify the kill rather than
  reasoning about it.
- **A researcher's project STATE leaks into comments more easily than
  their science does.** The "never hard-code science-specific
  examples" rule is easy to obey for dataset and target names and easy
  to break for everything else: a comment justifying a fix was written
  as `the row reads "19 of 19 files matching"` — a count copied
  straight off the researcher's dashboard while debugging with them.
  It names no science, so it reads as harmless, and
  `testNoScienceSpecificIdentifiersInSource` cannot catch it (the seed
  list matches identifiers, not arbitrary numbers). It is still their
  project pinned into vaibify's source, where every future agent will
  read it as if it were a fact about vaibify. Write the general shape
  — "all files matching" — never the observed instance. The tell is a
  comment containing a number, path, or timestamp you learned from the
  session rather than from the code.
- **Making a gate scope-aware does not make the row beside it
  scope-aware.** The L2/L3 split taught the gates that a drifted
  `reproduce.sh` says nothing about whether the researcher's DATA is
  published — and the Published-copies row kept reporting the verify's
  aggregate counts and listing `reproduce.sh` among the files, so the
  screen went on making exactly the statement the gate had just been
  corrected out of. The researcher saw it immediately; the suite could
  not, because every test asserted the gate. After changing what a
  computation MEANS, grep for the other consumers of the same cached
  record — a summary, a count, a file list — and check each one
  against the new meaning.
- **A cached comparison is a claim about verify-TIME bytes, and read
  time is a different moment.** The envelope gate quoted a Zenodo
  verify's divergence list after the local `environment.json` had been
  regenerated — so the per-file badge (live hashes) showed red while
  the Level 3 cell (this cache) stayed green on the same screen
  (researcher-reported, 2026-09-01). A cached verdict about a file is
  only usable while the file still IS the bytes that were graded:
  record the local hash each path was compared AS
  (`dictComparedHashes` in `syncStatus.json`) and re-check it at read
  time, treating a mismatch or a pre-field cache as UNPROVEN.
  `test_an_envelope_regenerated_after_the_verify_no_longer_passes` is
  the kill-confirmed guard; `_fsEnvelopeStateFingerprint` keeps the
  blocker cache from masking the transition. The same session added
  the twin alert for the other direction of staleness — the envelope
  pinning an image the container no longer runs
  (`fdictAssessEnvelopeImageCurrency`, captured once at connect) —
  because both were found the same way: a rebuild landed, nothing on
  the screen moved.

- **Generated bookkeeping must never be able to overturn a
  scientific verdict.** The conftest vaibify generates writes its test
  marker in `pytest_sessionfinish`, unguarded, so a marker directory
  it could not create ended the session non-zero — over a run that had
  just printed `1 passed`. Every test tier of every step reported
  `exit 1` and the researcher read it as their science failing
  (2026-09-04). Two separate causes had to line up and each is its own
  lesson. The generated file had a container path written into it as a
  literal, which is the `/workspace`-as-constant trap arriving in
  GENERATED output where no source scan looks for it — a generated
  artefact needs the same review as source, and it must locate its
  root rather than be stamped with one. And the refresh that would
  have replaced it is memoised per hub PROCESS and runs only at
  connect, so a `git pull` reinstated the stale copy and reopening the
  project re-probed nothing: **a cache keyed on identity, over a file
  something outside the process can rewrite, is a correctness bug
  wearing a performance optimisation's clothes.** Re-probe at the
  moment the content matters. Guarded by
  `tests/testConftestRefreshBeforeRun.py`.
- **A `monkeypatch` guarantee stops at the process boundary, and the
  docstring will not say so.** `tests/conftest.py` promised that "no
  test can read, overwrite, or delete the researcher's real stored
  credentials" while patching only this process; over a hundred test
  files spawn subprocesses, each of which imported the real `keyring`
  and reached the real keychain. It surfaced as four macOS approval
  dialogs during one suite run (researcher-reported, 2026-09-04) —
  the reads being the visible half, while a child reaching
  `_fnDeleteKeyringEntry` would have destroyed a working credential
  silently. When an isolation fixture protects host state, ask what a
  CHILD process sees, and enforce it through the environment children
  inherit. Guarded by
  `tests/testKeychainIsolationCrossesProcesses.py`, which asserts the
  backend the child itself reports rather than the variable the
  parent exported.

## Pointers

- [docs/architecture.md](docs/architecture.md) — the "why" behind the
  module layout
- [docs/vibeCoding.md](docs/vibeCoding.md) — the methodology behind
  this documentation structure
- [docs/developers.md](docs/developers.md) — human contributor guide
- [vaibify/gui/AGENTS.md](vaibify/gui/AGENTS.md) — backend subtree
  rules
- [vaibify/gui/static/AGENTS.md](vaibify/gui/static/AGENTS.md) —
  frontend subtree rules
- [.claude/skills/](.claude/skills/) — conditional recipes for
  recurring extension tasks. The two currently defined:
  - [.claude/skills/add-route-module/](.claude/skills/add-route-module/)
    — recipe for adding a new FastAPI route module.
  - [.claude/skills/add-data-loader/](.claude/skills/add-data-loader/)
    — recipe for adding a new data file-format loader.
- [docs/skillTesting.md](docs/skillTesting.md) — how skills are
  tested: referential integrity (`tests/testSkillIntegrity.py`, CI),
  trigger evaluation (`tools/evaluateSkillTriggers.py`), and A/B
  outcome evaluation (`tools/evaluateSkillOutcomes.py`).
