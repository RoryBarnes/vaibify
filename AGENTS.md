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

**Container paths are `posixpath`, host paths are `os.path`.**
`workflowManager.py` handles container paths, which are POSIX on every
host operating system. Any module handling host paths must use
`os.path`, whose separator is the host's. A helper shared between the
two lanes must be *pure* (e.g. `flistValidateOutputFilePaths`);
unifying the path handling itself would silently mangle one lane or
the other, and the failure would not surface until a cross-platform
user hit it.

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
disabled (see "The interactive terminal is disabled" below), so the
unbudgeted lane has no production caller; the budget is still enforced
and is driven through the real wrapper by a test-owned socket on that
lane. Run exclusivity is additionally
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
  access, and reflection (`eval`, `exec`, `sys.modules[...]`,
  `importlib`, `__import__`, dynamic `getattr`). **This is the
  completeness boundary and it fails closed.** Importing `os` is not
  acquisition; `from os import system` is — 33 GUI modules import `os`,
  so a module-level reading would be useless.
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

## The interactive terminal is disabled

**`/ws/terminal` refuses every caller, and no production path creates a
terminal execution.** A shell can `setsid` out of the process group the
containment record tracks, so "the terminal stopped" is not provable,
and release, hand-over, and shutdown cannot honestly report a container
quiet while a terminal has run in it. An unprovable containment
boundary is not shipped.

The refusal is the **first statement** in the handler: it accepts, then
closes with `I_REJECT_TERMINAL_DISABLED`. That ordering is the
contract, not a detail. Before it, the handler resolved the Docker id
(an existence oracle open to any caller that could reach the socket),
ran the ownership gate (which *refreshes* the owner's liveness stamp),
and entered the connection counters — so a refused dial-in had already
learned what existed and disturbed a session it had no standing in. The
close code is deliberately distinct from every authorization code: a
client that cannot tell a disabled feature from a rejected credential
tells the researcher to re-claim a container that is already theirs.
The frontend does not open the socket at all, because a socket left to
be refused reports a deliberate refusal as a connection failure.
Interactive *steps* need a shell, so they refuse honestly instead of
polling forever for a sentinel no shell will print.

Four parking controls hold it there. A no-callers invariant over
`terminalContainment` **cannot** pass — the module keeps production
callers for drain, reap, and shutdown — so the controls are narrower:
nothing in `vaibify/` constructs a `TerminalSession`
(`testNoProductionPathConstructsATerminalSession`); only the refusal
handler answers the path
(`testOnlyTheWithdrawnHandlerServesTheTerminalWebSocket`); only the
parked seam names the record-creation calls
(`testNoProductionPathPreparesATerminalExecutionRecord`); and every
surviving containment caller is cleanup
(`testRemainingContainmentCallsAreCleanupOnly`). The handler's own
ordering is pinned by `testWithdrawnTerminalRouteTouchesNothing`.

**Legacy records are never swept.** A terminal journal record written
by an earlier version stays on disk and keeps its container QUARANTINED
until the container is positively stopped or its process group proven
empty — that is, through `vaibify reconcile`. Disabling the route
settles nothing, because it has proven nothing
(`tests/testWithdrawnTerminalLegacyRecords.py`). Upgrading with a live
terminal therefore leaves that container quarantined, which is a
migration cost to state in release notes, not a bug to code around.

**Do not re-enable it behind a flag.** A runtime switch is a bypass
path by construction, and the boundary that made the terminal unsafe
has not moved. Re-enabling means proving containment against a `setsid`
descendant first; `tests/testTerminalContainment.py` keeps the old
prover as the standing demonstration of why the boundary was invalid,
and that is the gate any future terminal work must pass.

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
- `terminalContainment.py` and the `terminal` journal kind survive the
  terminal being disabled, and must. They are what reconciles a record
  written by an earlier version — release, the safe reaper and shutdown
  all settle terminal records through
  `fdictTerminateAndProveRecord` — and
  `tests/testTerminalContainmentLive.py` keeps the process-group prover
  as the standing demonstration that it cannot see a `setsid`
  descendant. Deleting the module would delete the reason the feature
  is off. Its in-memory registry is, in production, permanently empty:
  only `terminalSession` registers, and nothing constructs one.
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
