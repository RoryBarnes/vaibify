# Agent Council — remediation plan (revision 2)

**What this file is.** A review-ready plan to turn the `feat/agent-council`
prototype into a mergeable feature. It enumerates every outstanding defect from
the two external reviews (2026-08-19), gives each a root cause, a fix, and the
test that proves the fix, then sequences the work and names the hard gates.

**What it is not.** Not a redesign. `design/agentCouncil.md` (revision 13)
remains the specification; this plan realizes it where the prototype only stubbed
it. Revision 2 of *this* plan folds in the second review: the controller becomes
a serialized authority, project identity becomes canonical, the Docker gateway
gains a handle-based contract with correct quarantine semantics, snapshot
coherence gets a real algorithm, credential enablement becomes machine-readable,
the verification lanes are separated, rebase moves first, and Codex moves to a
feasibility-first follow-up (pending the maintainer's confirmation — see R9).

**Status of the branch.** The components are real and independently tested; the
production controller that composes them does not exist (`CouncilEngine(...)` is
built only in `tests/`). Both reviews agree: a strong prototype, not a completed
feature.

**Decisions taken (2026-08-19).**
- **Docker access: Option B** — one typed gateway is the sole caller of the
  Docker SDK for the council.
- **Credentials: default-off, evidence-gated** — the runner backend for a
  provider is disabled until that provider's credential behavior is verified by
  a live check, recorded as machine-readable evidence (R10).
- **Controller: a serialized authority** — the controller is the sole writer of
  campaign state; routes submit bounded commands under a per-campaign
  serialization primitive (R1).
- **Identity: canonical, not the URL resource id** — campaigns are bound to the
  canonical container/lease principal plus the validated project-repo and
  snapshot identities (R2).

---

## 1. The outstanding issues

Each item: **defect → root cause → fix → proof** (a falsification test that fails
if the fix regresses).

### R1 — Blocker: no production controller, and it must be a *serialized authority*

- **Defect.** `POST …/start` mints a synthetic "turn in flight" and returns; it
  never builds the engine, captures a snapshot, invokes a provider, or creates a
  runner. Beyond that, even a naive background task would race `respond`, `stop`,
  `accept`, `delete`, and its own checkpoints against each other.
- **Root cause.** No controller, and no concurrency model for one.
- **Fix.** A **campaign controller** that is the **sole writer of campaign
  state**. Routes never mutate campaign state directly; they submit **bounded
  commands** (`respond`, `requestStop`, `acceptPlan`, `delete`) onto a
  **per-campaign serialization primitive** (a single-owner lock or a per-campaign
  command queue) that the controller drains in order. Specify, concretely:
  - **Admission/carrier.** The controller's container mutations execute under
    the council registry's admission (not the commit carrier — runner containers
    are council-created, §10.3), reserved write-ahead and settled on proven
    absence. Its durable checkpoints are the §7.5 app-data writes.
  - **Ownership + honest failure.** The controller task is owned by the registry;
    if it raises, the campaign is recorded `failed`/`interrupted` (never left
    "running"), all live runners are settled or quarantined, and the turn is
    retired.
  - **Project release.** Releasing the project during deliberation drains the
    controller (settle live work, suspend at the next boundary) before the lease
    is released — it never leaves paid work hidden.
  - **Restart classification.** A campaign whose controller disappeared mid-turn
    is discovered on restart and classified honestly: labelled-runner survivors
    are destroyed-with-proof or quarantined; a turn with no terminal record is
    `interrupted`, never resumed silently or called complete.
  On Start the controller captures the snapshot, builds `CouncilEngine` with the
  real per-participant connections, drives deliberation, checkpoints as each
  phase settles, settles all live work at a human gate, and retires the turn.
  The route returns immediately; the UI polls (§11).
- **Proof.** (a) An HTTP integration test drives Start with a fake provider in a
  **real runner** to `planReady` with no hand-patched state. (b) A concurrency
  test fires `respond`/`stop`/`accept`/`delete` while a turn is live and asserts
  the serialization primitive orders them and campaign state stays consistent.
  (c) A controller-crash test kills the controller mid-turn and asserts restart
  classifies it `interrupted` with runners settled/quarantined, never resumed.

### R2 — Blocker: campaigns must carry a *canonical* project identity

- **Defect.** Campaigns carry no project identity; the store is global; `list`
  returns everything; routes authorize the URL container but never that the
  campaign belongs to it — a cross-project read/accept/stop/delete hole.
- **Root cause.** No identity on the record, and — as the second review notes —
  the raw `sContainerId` from the URL is the *wrong* identity to store: a Docker
  id can change or be represented differently, ownership is name-keyed, and one
  container can host multiple workflows/project repositories.
- **Fix.** Bind each campaign to a **canonical identity triple**: the canonical
  **container name / lease principal** (the name-keyed owner, per the repo's
  ownership authority), the **validated project-repository identity** (the
  project repo the council was started for — a container can host several), and
  the **snapshot identity** (the immutable snapshot hash). Every `{sCampaignId}`
  route resolves the campaign and **refuses 404** (not 403 — do not leak
  existence across projects) unless all three match the authorized principal and
  its active project repo. `list` filters to that principal + project repo.
- **Proof.** With two resources (name ≠ id each): a campaign under A is 404 from
  B on every route and absent from B's list. **And** with two project
  repositories hosted by the *same* container: a campaign under repo 1 is not
  reachable or listed under repo 2.

### R3 — Blocker: plan acceptance bypasses consensus

- **Defect.** `accept-plan` takes arbitrary caller text from any state and
  transitions straight to `planAccepted`, never checking `planReady` or using the
  engine's guarded acceptance. A test even accepts on a never-deliberated
  campaign.
- **Root cause.** The route re-implemented acceptance as a raw transition + file
  write instead of delegating to the engine.
- **Fix.** The route submits an `acceptPlan` command to the controller (R1),
  which calls the engine's guarded acceptance: refuses unless `planReady`;
  accepts the **council's own server-held candidate**, not caller text; only then
  writes `plan.md` locally and transitions.
- **Proof.** Accept on non-`planReady` is refused (409); accept on `planReady`
  persists the engine's candidate, not caller text; the current stop-then-accept
  test is inverted to assert the refusal.

### R4 — Blocker: Docker gateway contract, quarantine semantics, egress hardening

- **Defect.** The engine creates the runner then leaks it on any later exception
  (no `finally`); the production path never registers the runner in the registry;
  the baseline executor discards the destruction result, so a `quarantined`
  teardown still returns "confirmed" evidence.
- **Root cause.** No single owner of the runner lifecycle; the registry is a
  parallel structure the production path forgot; cleanup lives only on success.
- **Fix (Option B).** Introduce **`agentCouncilDockerGateway.py`** — the **only**
  module that calls the raw Docker SDK for the council. Its contract is stronger
  than a module boundary:
  - It **mints opaque reservation handles** and accepts **only those handles**,
    never arbitrary container ids. Before any destructive operation it
    **verifies the registry identity and the council label** on the target — so
    it cannot act on a container it did not create, and cannot be handed the
    active project container's id.
  - Every operation **reserves** (write-ahead) before create and is wrapped so
    the runner is **destroyed on every exit path** (success, exception, cancel)
    via a structured owner (async context manager / explicit `finally`).
  - **Quarantine semantics (corrected).** Destruction is attempted, but when
    Docker **cannot prove absence** the result is NOT settled-clean: the
    reservation stays **visibly quarantined**, admission stays **consumed**, the
    campaign/UI says **the runner may still exist**, and **no evidence becomes
    `confirmed`**. Proven absence settles clean; an indeterminate answer
    quarantines. "Destroyed every time" was wrong.
  - **Egress hardening.** The proxy container gets the same posture as the
    runner: a **pinned** proxy image (digest-pinned, not floating
    `python:3.10-slim`), non-root, all capabilities dropped, no-new-privileges,
    and resource limits. The proxy is council infrastructure and must not be a
    softer target than the runner it fronts.
  Runner/egress keep their pure helpers (argv, tar, env, DNS, absence-probe
  logic) but call the gateway for every SDK operation.
- **Proof.**
  - Inject an exception at each fallible step after creation and assert the
    reservation is settled **or quarantined** every time, and never leaks
    unrecorded (real daemon, name ≠ id).
  - Force an indeterminate teardown and assert: reservation quarantined,
    admission still consumed, evidence not `confirmed`, UI says may-exist.
  - A single-authority architectural test: every council Docker SDK call
    originates in `agentCouncilDockerGateway` (grep-style), and the gateway
    refuses a handle whose registry identity/label does not verify.
  - The proxy container is asserted digest-pinned, non-root, cap-dropped,
    resource-limited.

### R5 — High: a real snapshot-coherence algorithm

- **Defect.** Coherence hashes only git status codes and paths, so an
  already-dirty file whose *contents* change mid-capture is accepted. And a
  content hash drawn from the *same* archive stream cannot be an independent
  second observation.
- **Root cause.** The digest is over status metadata, and there is no independent
  pre/post observation nor a capture lock.
- **Fix.** Specify the algorithm:
  - Take the **bounded project lock** the design requires (§9.2) for the capture
    window.
  - Obtain **two independent source identities** — a pre-capture and a
    post-capture observation of the project repo taken *outside* the archive
    stream (e.g. a commit + per-path content digest of the tracked-dirty and
    included-untracked set, gathered independently of the tar) — and match
    archive members to them by path.
  - Define behavior for **untracked additions/deletions, renames, symlink
    swaps, and content that changes then reverts** during capture: any mismatch
    between the two independent observations (including a changed-then-reverted
    file whose intermediate archive bytes differ from both endpoints) **refuses
    the capture and cleans up**. A refusal is honest; a torn snapshot is not.
- **Proof.** A live test mutates the *contents* of an already-dirty file during
  streaming and asserts refusal + cleanup; separate cases cover a rename, a
  symlink swap, and a change-then-revert.

### R6 — High: unify record shapes and transitions across engine/routes/frontend

- **Defect.** Seam mismatches, all downstream of R1: `sResponseText` vs `sText`;
  candidate at `dictCandidatePlan.dictResult` vs UI reading `sPlanText`/`sText`;
  exhausted-round choices sent as strings the backend never maps to the engine's
  three exit transitions; settings rendered but not sent; "queued at a boundary"
  copy vs a 409.
- **Fix.** The engine's shapes are the authority; routes and frontend conform.
  Responses use `sText`; the UI reads the candidate from its real path; the three
  exhausted-round controls map to the engine's exit transitions; the convene
  request sends the settings the form collects; the composer copy matches the
  real continuation behavior R1 provides (recorded, consumed at the next
  boundary). Build a proper **accepted-plan renderer** for the real candidate
  shape.
- **Proof.** A contract test asserts the fields the engine reads are the ones the
  routes write; a frontend contract test asserts settings are sent and the
  exhausted-round controls post the engine's transition names; the R1 test
  exercises a real `respond` continuation.

### R7 — High: honest capability reporting (Claude-only for this branch)

- **Defect.** `bAvailable: … or True` is unconditionally true; the endpoint
  advertises `codex` with no adapter.
- **Fix.** `bAvailable` reflects the real probe (SDK/login/usable models present).
  For **this branch**, advertise **only Claude** (Codex moves to R9's follow-up);
  a provider with no adapter is never advertised as available.
- **Proof.** An unavailable provider reports `bAvailable: False` and the toolbar
  disables/explains; no adapter-less provider is advertised.

### R8 — Governance: honest mutation ratchets

- **Fix (falls out of R4).** With every council Docker call behind the gateway,
  the ~22 opaque SDK sites collapse to the gateway's small surface, so
  `untraceable-docker-sdk-root` **falls** and its disposition ("governed by the
  registry") becomes *true* (the gateway performs reservation/settlement).
  Re-examine the unclassified-row rise honestly: classify the council use-site
  rows where possible; where a row is genuinely best-effort metadata, say so —
  never call it dispositioned. Any residual change is a reviewed maintainer
  decision with the real reason.
- **Proof.** `--check` clean; the single-authority gateway test; ratchet
  constants reflect post-gateway counts with accurate comments.

### R9 — Codex: a feasibility-first follow-up, **out of this merge gate** *(pending maintainer confirmation)*

- **Context.** Two distinct Claude models already satisfy the MVP floor. The
  second review recommends **not** gating this remediation on Codex: it expands
  the credential, instruction-channel, parser, model-discovery, and
  live-verification surface on an already-oversized branch, and its feasibility
  checks must **precede** implementation, not follow. The maintainer previously
  asked that Codex be added; this item reconciles the two by **keeping Codex in
  the roadmap but sequencing it correctly** — the maintainer should confirm or
  override.
- **Plan.** For this branch: R7 makes capabilities report Codex as unimplemented
  (honest), and Codex is **not** in the merge gate. As a **separate follow-up**,
  in order:
  1. **Codex Phase 0 empirics FIRST** — headless launch; a highest-priority
     instruction channel **separable from the snapshot's agent docs** (§5.5); and
     the credential feasibility of R10 (access-token-only headless auth,
     non-interference). If any fails, Codex ships on the API backend or not at
     all — decided with evidence, before code.
  2. Only then the Codex `CouncilProviderConnection` (argv discipline, charter
     via the separable channel, normalized events, model identity, live model
     discovery), parallel to Claude's.
- **Proof.** Codex's own §15.2 adapter tests and its own R10 credential record —
  in the follow-up, not here.

### R10 — Gate: version-bound, machine-readable credential enablement

- **Defect/status.** The runner reuses the subscription login (access token, not
  refresh; materialized into the runner; no writeback). Three properties are
  empirical and unverified per provider: (1) access-token-only headless auth;
  (2) non-interference with the project login; (3) cleanup on success, failure,
  cancel, crash-recovery. The launch UI states the token "is copied" as if
  proven — a truth-in-UI violation.
- **Fix.**
  - The runner backend for a provider is **disabled by default** and enabled only
    against a **machine-readable evidence record** keyed to **provider+backend,
    CLI version, project-image/executable identity, credential schema/source,
    host platform, and verification date**. Any mismatch **defaults to
    disabled**. Even with a matching record, **login presence and usable models
    are probed live** at launch.
  - The **residual token-exfiltration disclosure stays visible even after
    verification** — verification establishes the credential *mechanics*, not
    that reuse is risk-free (§2.7). The UI never states the handling as "proven
    secure."
  - The live check (Claude first; Codex in R9's follow-up) runs on a real paid
    account: one runner, copied token only, trivial headless turn completes;
    project login still works afterward; token did not rotate; staged files gone;
    repeated across an injected failure and a simulated crash-recovery.
  - If a provider fails (1) or (2), it does not ship on the runner backend; the
    API backend (separate keys, no reuse) is **not currently built** — building
    it is then a scoped decision taken with the failure evidence.
- **Proof.** The structural lane keeps its fake-token tests (delivery, 1000-owned
  ownership, cleanup on reachable paths). The enablement flag reads the evidence
  record and defaults off on any key mismatch (tested). No green test may *imply*
  the live properties hold.

### R11 — Agent-instruction-file policy (resolve the contradiction I introduced)

- **Defect.** R9 (and §5.6) say the charter delivery must not shadow the
  snapshot's `AGENTS.md`; but the context module now **excludes**
  `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` and the `.claude`/`.codex` trees at every
  depth. "Deliver without shadowing" and "exclude entirely" are different
  policies, and the plan must pick one explicitly.
- **Fix.** Decide, per adapter, and record it: are project instruction files
  **evidence content** (the council reviews them as part of the project) or
  **exclusions** (kept out so they cannot steer the participant)? At **which
  paths** (repo-root only, or every depth)? And **how does each CLI** prevent a
  snapshot instruction file from overriding the server-owned charter (the
  `--append-system-prompt`-style channel, verified to win)? Default position to
  test against: exclude the *agent-instruction* files (they are meta-instructions,
  not source under review) **and** verify the charter channel out-ranks any that
  remain — belt and suspenders — but this must be a *decision with per-adapter
  empirical tests*, not an incidental exclusion-list edit.
- **Proof.** A per-adapter test that a snapshot containing a hostile
  `CLAUDE.md`/`AGENTS.md` does not override the charter (the charter's
  instructions win), plus a test pinning whichever exclusion policy is chosen.

### R12 — Separate the verification lanes; add a stale-baseline producer

- **Defect.** The plan blended four lanes that prove different things, and the
  stale-baseline UI state is currently *fabricated* by the browser test with no
  real producer.
- **Fix.** Name and keep four **distinct** lanes, stating what each does and does
  not prove:
  1. **Browser + fail-closed fake Docker adapter** — UI/DOM and journey wiring
     only; proves nothing about real runner behavior.
  2. **HTTP/controller integration + deterministic fake provider** — the real
     controller, routes, store, serialization, and recovery; no real Docker.
  3. **Live-Docker containment** — real runners/gateway: leak/quarantine,
     resource + network falsification, absence proofs.
  4. **Paid-account credential** — R10, manual, per provider.
  Add a **real stale-baseline producer**: compute the current project identity
  (commit + dirty digest of the active project repo) and compare it to the
  recorded snapshot identity; the UI shows "baseline stale" from that comparison,
  not from fabricated state.
- **Proof.** Each lane named in CI/docs with its scope; a test drives the real
  staleness computation (change the project after capture → stale shown).

---

## 2. Build order (per the second review)

1. **Rebase** `feat/agent-council` on current `main`; resolve the `index.html`
   conflict; regenerate the generated ledgers. (Do this *before* implementation,
   and again at merge.)
2. **R2 canonical identity** + **R1 per-campaign serialization primitive**
   (the ownership/identity substrate).
3. **R4 typed Docker gateway**, quarantine semantics, **egress hardening**;
   **R8** honest ratchets fall out here.
4. **R5 snapshot coherence** algorithm.
5. **R1 controller** and lifecycle/recovery integration (crash, project-release,
   restart classification).
6. **R3 accept gate** + **R6 contract unification** + accepted-plan renderer.
7. **R7 honest Claude-only capability reporting**; **R11 agent-doc policy**.
8. **R10 live Claude credential gate** (maintainer action).
9. **R12** the four named verification lanes + stale-baseline producer; then
   **rebase + full gates** at merge.
10. **R9 Codex** as a feasibility-first follow-up, **unless the maintainer
    directs it into this branch** — in which case its Phase 0 empirics run at
    step 2.5, before any Codex code.

## 3. Definition of done (merge gate)

- A researcher runs a real campaign to `planReady` over real disposable runners
  with no fabricated state (R1 HTTP integration proof).
- Concurrent `respond`/`stop`/`accept`/`delete` are serialized; a controller
  crash classifies the campaign honestly on restart (R1).
- A campaign is 404 and unlisted from any other project **and** from another
  project repo in the same container (R2).
- Acceptance requires `planReady` and accepts the council's own candidate (R3).
- **No possible runner leak can become unrecorded or be reported as clean**; an
  indeterminate teardown is visibly quarantined with admission still consumed and
  no confirmed evidence; the gateway is the sole SDK authority and verifies
  handle identity/label before destroying; the proxy is digest-pinned and
  hardened (R4).
- Snapshot capture refuses and cleans up on any torn/independent-observation
  mismatch (R5).
- Capability and credential UI state only what is true; the runner backend is
  enabled for a provider only against a matching machine-readable evidence
  record, with login/models probed live and the residual risk still disclosed
  (R7, R10).
- The agent-instruction-file policy is decided and its charter-precedence proven
  per adapter (R11).
- The four verification lanes are named and green (browser real-flow, controller
  integration, live-Docker, plus the recorded manual credential lane); staleness
  has a real producer (R12).
- Full suite, browser lane, mutation `--check`, carrier audit all green on a
  branch rebased on `main`.

## 4. Out of scope (unchanged)

Review councils, the Deep protocol, tracked/manifest artifacts, and the
API-backend adapters stay deferred — except that the API backend becomes required
*for a specific provider* iff that provider fails its R10 gate. **Codex** is in
the roadmap but out of this branch's merge gate (R9), pending maintainer
confirmation.
