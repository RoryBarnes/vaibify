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
- Arrays should include an "a", e.g., an array of doubles starts with "da"
- Dictionary = "dict"
- List = "list"
- JSON = "json"
- Tuple = "t"

If a cast is not listed above, ask me.

3. Function names should begin with an "f" and should be followed by additional lowercase letter(s) that describe the return type, e.g. "fb" for a function that returns a Boolean, or "flist" for a function that returns a list. If a function does not return anything, use "fn" as the prefix.

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
alike" — they may diverge later, like `director`/`workflowManager`),
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
  adjusting import graphs, touching `workflowManager.py` or
  `director.py`):
  `python -m pytest tests/testArchitecturalInvariants.py -v`
- After JS changes: see "Required after JS changes" below — the
  Python suite does not execute the frontend at all.
- After editing any `.claude/skills/*/SKILL.md`:
  `python -m pytest tests/testSkillIntegrity.py -v` (referential
  integrity), then run the trigger and outcome evaluation harnesses
  in `tools/` before merging skill changes — see
  [docs/skillTesting.md](docs/skillTesting.md).

### Required after JS changes

Type checking and string-presence contract tests do not validate UI
correctness. Neither does the ordinary Python suite: it does not
execute the frontend at all.

**CI now does.** The browser lane (`tests/browser/`, run by
`.github/workflows/browser.yml`) loads the real dashboard in real
Chromium against a real uvicorn hub, and fails on any console error,
uncaught promise rejection, or failed asset. Pushing a branch is
therefore a genuine verification path, which it never used to be.

Run it locally when you want the fast signal:

```bash
pip install -e '.[browser]' && python -m playwright install chromium
python -m pytest tests/browser -m browser
```

**What the browser lane does not cover.** It drives a fail-closed fake
Docker adapter, so it says nothing about container launch, file
ownership on write, the real transport, terminal content, figure
rendering, or the sync panel. Those belong to the container
acceptance lane, which runs **nightly** — meaning drift between the
fake and a real container is caught up to a day late. The browser lane
failing blocks merge; the container-acceptance lane failing blocks the next release, not
retroactively. Do not read a green browser lane as "the frontend is
verified".

The manual check below is still the right tool when you are working
on something the lane does not assert — layout, wording, a specific
interaction. It takes about a minute and needs no Docker:

```bash
python -m vaibify --port 8137     # scratch port, not your usual hub
```

Then, in the browser at `http://127.0.0.1:8137/`:

1. **Read the console.** Zero errors is the bar. A single
   `ReferenceError` means a module failed to evaluate and every
   feature below it in load order is dead.
2. **Enumerate the globals.** In the console:
   `Object.keys(window).filter(k => /^Vaibify/.test(k)).length`.
   Then check any global your change touched resolves *as a bare
   identifier*, not via `window.`: modules declared with `const`
   create a global lexical binding, so `window.VaibifyApp` is
   `undefined` while `VaibifyApp` works. Probing the wrong one
   produces a false alarm.
3. **Confirm any new cross-module call resolves**, e.g.
   `typeof VaibifyApp.fsGetLeaseId` → `"function"`.
4. **Look at the page.** It should render, and any unavailable
   dependency (Docker down, no containers) must be reported honestly
   on screen rather than hidden.

Kill the scratch hub when done.

**Container-dependent paths need a container.** Anything touching the
lease, the WebSockets, or the file-status poll is not verified by the
above. Start Docker, open a project, and exercise the specific path.

#### If you are a delegated agent and cannot do this

**Push the branch and open a pull request, then let the browser lane
run it.** The lane is `pull_request`-triggered, so a pushed branch with
no PR runs nothing — do not read a quiet Actions tab as a pass. That is
now the answer, and it is why the lane exists: the old rule asked
delegated agents to load a page they had no browser for, so five of
them once changed JavaScript in one session, none could follow the
rule, and the merged branch was green with the frontend entirely
unexecuted. A rule nobody can follow is not a control.

If you also cannot push, say so explicitly and name the exact surface
you did not verify — "the three JS call sites in
`scriptWorkflowManager.js` were not executed; no JS runtime and no
push on this host." Silence about an unverified surface reads as
verification.

### The three execution lanes

| Lane | What is real | When | What it proves |
|---|---|---|---|
| browser (`browser.yml`) | Chromium + uvicorn + real HTTP/WebSockets; Docker is a fail-closed fake | every PR | JS loads and evaluates; API and refusal behaviour reach the screen honestly |
| container acceptance (`containerAcceptance.yml`) | a real container, image keyed by build-input hash | nightly / manual | a real container answers the commands The browser lane's fake models |
| fresh image (`freshImageBuild.yml`) | full build from scratch | weekly / on `vaibify/containerImage/**` PRs | the image still builds; the container user is unprivileged |

Two properties hold these together and must not be weakened:

- **The browser lane's fake is fail-closed and declared.** Every command
  it answers is listed in `LIST_MODELLED_COMMANDS` with the container
  assertion that confirms it; anything else raises. Never give it a
  catch-all return — this suite already carries ~20 permissive Docker
  mocks, and `testDockerConnectionLive.py` records where that habit
  led. `tests/testBrowserLaneContract.py` enforces both halves,
  including that each named container-acceptance assertion actually exists.
- **No lane may skip itself green.** `VAIBIFY_REQUIRE_DOCKER_DAEMON`
  and `VAIBIFY_REQUIRE_BROWSER` turn each lane's convenience skip into
  a failure in CI. The `docker info || exit 0` guard this replaced
  reported success for having run nothing;
  `tests/testDockerLiveDaemonRequirement.py` forbids its return.

- Docker-dependent tests (`tests/testContainerBuildIntegration.py`)
  are excluded from routine runs and are the only tests that require
  a live container. They are parametrized via the
  `VAIBIFY_INTEGRATION_CONFIG` environment variable and skip when it
  is unset.

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
  listed. The existing helper `fnValidatePathWithinRoot(sAbsPath,
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

**`director.py` and `workflowManager.py` are different things.**
`director.py` is a parallel workflow runner that operates on the host
filesystem using `os.path`. `workflowManager.py` operates on container
paths using `posixpath`. Similarly named functions
(`fbValidateWorkflow`, `fdictBuildGlobalVariables`) exist in both and
are intentionally divergent. Do not "fix" the divergence — it's
load-bearing. They may share *pure* helpers (e.g.
`flistValidateOutputFilePaths`) without violating this rule; the
calling conventions remain divergent.

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
(`registryRoutes`), the connect handler, the pipeline WebSocket, and the
terminal WebSocket must all authorize through the single shared guard
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
Terminal sockets are counted in `iLiveConnectionCount` for liveness but
never budgeted: one session legitimately holds the terminal strip, extra
terminal tabs, AND the pipeline socket at once — budgeting all sockets
shipped the Run-Step-always-refused bug (the terminal, opened on
workflow entry, held the only slot; every Run Step was 4409'd and
mislabeled "cannot reach server"). Run exclusivity is additionally
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
cannot honestly reach AICS Level 1.

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
`@fnAgentAction("action-name")` decorator on the handler, or by an
explicit entry in `SET_INTENTIONALLY_EXCLUDED_PATHS` (with a short
rationale on the same line or in the preceding comment block) if the
route is genuinely not agent-invokable.

Unregistered state-mutating routes are invisible to the in-container
agent: when a researcher says "Claude, run unit tests on step A09",
the agent has no way to translate that request into a backend call,
so the dashboard silently drifts out of sync as the agent improvises.

**`bAgentSafe` is enforced server-side (2026-07-26).** It used to be
advertisement: `fnAgentAction` changes no behaviour, and the flag was
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

`vaibify/templates/` and `vaibify/containerImage/` are data trees that
ship in the wheel. They used to sit at the repository root and be
reached with `Path(__file__).resolve().parents[2]`, which is the
repository root only in a checkout — from an installed wheel it is
`site-packages`. So no wheel ever contained them: `vaibify init`
printed "No templates found" and exited 0, and the Docker-context
lookup landed on `site-packages/docker`, the Docker SDK's own source
directory, which exists, so an `is_dir()` check passed.

Three rules follow.

**Locate them only through `vaibify/resources.py`.** It is the single
place that names the trees, and `importlib.resources` resolves them
identically from a checkout, an editable install, and a wheel. Never
reintroduce a `parents[N]` walk to reach package data.

**Treat them as read-only, and give every build its own copy.** A
wheel may be installed where the user cannot write, and `site-packages`
is shared by every project on the machine, so `vaibify build` never
writes into the packaged tree: `commandBuild.fsStageBuildContext`
mkdtemps a private context under `~/.vaibify/build/`, and it is
discarded on success and kept on failure with its path printed. The
staging directory is per *build*, not per project — the GUI starts
builds in worker threads with no serialization, so two dashboard
clicks race, and refreshing a shared directory begins with `rmtree`,
which would delete a context out from under a running `docker build`.
Note also that generated context files sat untracked *and* unignored
in the old `docker/` directory for months, then rode a `git mv` into a
wheel. `tests/testPackagedResources.py` fails if any of this
regresses.

**Anything the image needs must live under `vaibify/`.** Two
resources were reached from the repository root and therefore missing
from every distribution: the five curated agent docs staged into
`/usr/share/vaibify/docs`, and the shell completions. The docs case
was the worse one, because the bundled `vaibify-doc-map` skill told
the in-container agent all six documents were present — a wheel-built
image did not merely lack docs, it misdirected the agent, and it
differed materially from a checkout-built image. Those five now live
at `vaibify/docs/` as **symlinks onto the Sphinx sources**, so there
is exactly one file to edit and both builders dereference them into
real files in the distribution. Never replace one with a real file;
that is the shadowing trap, and
`testCuratedDocsRemainSymlinksOntoTheSphinxSources` fails if you do.
When adding a curated doc, add the symlink, extend `T_STAGED_DOCS`,
extend the doc-map skill's table, and add the *Sphinx source* path to
`freshImageBuild.yml`'s triggers — an edit lands on the target, never
on the symlink blob.

**Prove the distribution, not the import.** `pip-install.yml` runs
`tools/checkInstalledDistribution.py` against an installed sdist and
an installed wheel: it resolves every tree, runs `vaibify init`,
executes the shipped example workflow to a figure, *assembles a real
build context* and checks that no curated doc and no Dockerfile `COPY`
source is missing. The release workflow previously tested a
distribution with `import vaibify`, which is why a wheel missing every
template shipped without comment — and the first version of this
script spot-checked three files, which is why it passed a
distribution whose assembled context was missing five of six agent
documents. Checking a shipped file is not the same as checking the
artifact built from it. The job runs **after** a merge, at the corners
of the support matrix; a release runs the full matrix. It is a
post-merge lane by decision (2026-07-28), so a packaging regression
lands on `main` and is caught on the push rather than held out by the
merge gate — held out of a *release*, not out of the branch. Treat a
red `pip-install` on `main` as "main currently builds a broken
distribution", never as flaky CI.

## Known technical debt

These are known, deliberate, and load-bearing — do not "fix" them
without discussion:

- `introspectionScript.py` duplicates format-handling logic from
  `dataLoaders.py`. Container scripts cannot import from the host.
- `director.py` has its own `fbValidateWorkflow` and
  `fdictBuildGlobalVariables` that diverge from `workflowManager.py`.
  Host path vs. container path.
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
- An external review is evidence, not a verdict. A 2026-07-26 review
  correctly identified the browser/container execution hole and the
  doc drift, and was wrong about the falsification suite being
  order-unstable. A parallel audit over-reported the fixture-collapse
  sweep: of three flagged key pairs only one was real, because
  `sName`/`sDirectory` are *required* to agree by the slug contract
  and no code ever compares `sVersion` to `sExpectedVersion`. Check
  each claim against the source before acting on it; manufacturing a
  change to match a confident report is worse than ignoring it.

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
