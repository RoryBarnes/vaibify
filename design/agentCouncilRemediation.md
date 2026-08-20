# Agent Council — remediation plan

**What this file is.** A review-ready plan to turn the `feat/agent-council`
prototype into a mergeable feature. It enumerates every outstanding defect from
the external review (2026-08-19), gives each a root cause, a fix, and the test
that proves the fix, then sequences the work and names the two hard gates.

**What it is not.** It is not a redesign. `design/agentCouncil.md` (revision 13)
remains the specification; this plan changes none of its intent. Every fix below
is the spec being *realized* where the prototype only stubbed it.

**Status of the branch.** The components are real and independently tested — the
pure engine (with falsification tests that caught a real bug), the disposable
runner and egress and snapshot primitives (live-falsified against a daemon), the
registry, the transport seam, the routes, and the UI shell. What is missing is
the **controller that composes them**, plus cross-project authorization, the
accept-consensus gate, fail-closed runner cleanup, honest capability/credential
reporting, and the live credential gate. The review's verdict — "a strong
prototype, not a completed feature" — is accepted in full. The central proof:
`CouncilEngine(...)` is constructed only in `tests/`; no production path builds
it.

**Two decisions already taken (2026-08-19).**
- **Docker access: Option B.** Route every council Docker operation through one
  small typed gateway, rather than teaching the mutation scanner to resolve a
  passed-in client. This makes "the council only ever touches council-created
  containers" true by construction, collapses ~22 opaque scanner blind-spots
  into one gateway with a tested invariant, and is the natural home for the
  registry reservation/settlement the production path currently skips.
- **Credentials: a live paid-account gate.** Enabling the runner backend for a
  provider is gated on a live check of that provider's CLI credential behavior.
  Until it passes, the runner backend for that provider stays disabled and the
  UI says so truthfully.

---

## 1. The outstanding issues

Each item lists the **defect**, the **root cause**, the **fix**, and the
**proof** (a falsification-style test that fails if the fix regresses). Severity
follows the review.

### R1 — Blocker: starting a council does not run a council

- **Defect.** `POST …/start` mints a synthetic "turn in flight" record and
  returns. It never captures a snapshot, builds `CouncilEngine`, invokes a
  provider, creates a runner, or processes a result. A real campaign sits in
  `planning` forever with a live turn that never retires.
- **Root cause.** There is no controller. The engine (Phase 1), the runner
  adapter (Phase 2), and the runner/egress/snapshot primitives (Phase 0) were
  each tested in isolation with fakes; the seam that composes them in production
  was never written. `councilRoutes.py` does not even import `agentCouncil`.
- **Fix.** Add a **campaign controller** — a background task the start route
  launches (and the respond route resumes) that:
  1. captures the immutable snapshot via `agentCouncilContext`;
  2. constructs `CouncilEngine` with real per-participant
     `CouncilProviderConnection`s (the Claude and Codex runner adapters),
     wired to the snapshot, the egress proxy, the council Docker gateway (R4),
     and the registry;
  3. drives the deliberation, persisting proposals, critiques, candidate plans,
     evidence-ledger entries, events, and human gates to the store **as each
     phase settles** (the §7.5 checkpoint discipline);
  4. on a human gate (`needsHuman`), settles all live work and suspends —
     leaving no live runner or turn — per §5.4;
  5. on a terminal state, records it (`planReady` / `failed` / `interrupted`)
     and retires the turn.
  The controller runs under the council registry's admission and the idle-veto,
  and is drained on shutdown (all already built). It is NOT an HTTP request that
  blocks; the route returns immediately and the UI polls (§11).
- **Proof.** A new HTTP-level integration test drives `start` with a **fake
  provider that runs inside a real runner** and asserts the campaign actually
  progresses through the phases to `planReady`, that events accrue, and that the
  turn retires — with no state hand-patched into the store. This test replaces
  the current browser journey's fabricated-state patching.

### R2 — Blocker: campaigns are not project-scoped (cross-project authorization)

- **Defect.** The campaign record carries no container/project identity, the
  store is global, `list` returns every campaign, and the mutating/reading
  routes verify the caller owns the container in the URL but never that the
  campaign belongs to that container. A browser owning project B can list, read,
  accept, stop, or delete project A's councils.
- **Root cause.** Campaign creation (`fdictCreateCampaign`) never recorded the
  originating resource, and the routes authorize the *container* path segment
  without cross-checking the *campaign* path segment against it.
- **Fix.**
  1. Add `sResourceId` (the container id / host registry name the council was
     started for) to the campaign record, set at creation from the start
     route's `sContainerId`.
  2. Every route that takes `{sCampaignId}` resolves the campaign and **refuses
     (404, not 403 — do not leak existence across projects) unless
     `dictCampaign["sResourceId"]` matches the authorized resource**.
  3. `list` filters to the authorized resource only.
- **Proof.** A test with two resources (name ≠ id for each) starts a campaign
  under resource A, then drives every campaign route under resource B's lease
  with A's campaign id and asserts 404 on read/accept/stop/delete and absence
  from B's list. This is the name-vs-id discipline the repo already mandates,
  applied across the project boundary.

### R3 — Blocker: plan acceptance bypasses consensus

- **Defect.** `POST …/accept-plan` takes arbitrary caller text from any campaign
  state and transitions straight to `planAccepted`, never checking `planReady`
  and never using the engine's guarded acceptance. A test accepts a plan on a
  campaign that never deliberated.
- **Root cause.** The route re-implemented acceptance as a raw state transition
  plus a local file write, instead of delegating to the engine's `fnAcceptPlan`
  (which requires `planReady` — `agentCouncil.py`).
- **Fix.** The route calls the engine's guarded acceptance path, which:
  1. refuses unless the campaign is `planReady`;
  2. accepts **the council's own candidate plan** (server-held), not
     caller-supplied plan text — the researcher accepts or rejects, they do not
     author the plan body;
  3. only then writes `plan.md` locally and transitions to `planAccepted`.
- **Proof.** Tests assert: accept on a non-`planReady` campaign is refused
  (409); accept on a `planReady` campaign persists the *engine's* candidate, not
  any caller text; and the existing stop-then-accept test is rewritten to assert
  the refusal (it currently encodes the bug — it is deleted/inverted, not kept).

### R4 — Blocker: runner containment is not fail-closed; production path skips the registry

- **Defect.** The engine's `_fdictDriveConnection` creates the runner in
  `fdictPrepareImmutableContext`, then on any later exception returns
  `{"raised"}` with no cleanup and no `finally`; the runner leaks. The
  production connection never reserves or settles the runner in
  `agentCouncilRegistry`, so the registry cannot reliably drain or quarantine
  it. The baseline-evidence executor discards the destruction result, so a
  `quarantined` teardown still returns apparently-successful evidence.
- **Root cause.** No single owner of the runner lifecycle: creation and
  destruction are scattered across the connection's methods, the registry is a
  parallel bookkeeping structure the production path forgot to call, and cleanup
  lives only on the success path.
- **Fix (this is where Option B lands).** Introduce
  **`agentCouncilDockerGateway.py`** — one small module exposing a *typed*
  vocabulary for council Docker operations (`createCouncilRunner`,
  `destroyCouncilRunnerAndProveAbsence`, `createEgressNetwork`,
  `launchAllowlistProxy`, `removeEgressResources`, `runBaselineSandbox`). It is
  the **only** module that calls the raw Docker SDK for the council. Every
  operation:
  1. **reserves** in the registry (write-ahead) before creating, and **settles**
     (compare-and-settle) only on proven absence — so a leak is impossible
     without a registry record to drain;
  2. is wrapped so the runner is **always** destroyed on every exit path
     (success, exception, cancellation) via a structured owner (an async
     context manager or explicit `finally`), never left to the success path;
  3. **propagates** the destruction outcome — a `quarantined` teardown makes the
     turn's evidence `quarantined`/`asserted`, never `confirmed`.
  The runner and egress modules keep their pure helpers (argv, tar, env, DNS
  wiring, absence-probe logic) but no longer call the SDK directly; they call the
  gateway. The gateway physically cannot name the active project container.
- **Proof.**
  - A falsification test injects an exception at each fallible step *after*
    runner creation (snapshot copy, credential delivery, CLI start, streaming,
    parse) and asserts the runner is destroyed and its registry reservation
    settled every time (name ≠ id, real daemon).
  - A test asserts a forced-`quarantined` teardown yields evidence that is not
    `confirmed`.
  - A test asserts every council Docker call in the codebase originates in
    `agentCouncilDockerGateway` (grep-style architectural invariant), mirroring
    the single-authority tests already in the repo.

### R5 — High: snapshot coherence can miss a torn capture

- **Defect.** The pre/post coherence check hashes only git status codes and
  paths. An already-dirty file whose *contents* change during archive streaming
  leaves the status map unchanged, so the torn snapshot is accepted.
- **Root cause.** The coherence digest is over status metadata, not content.
- **Fix.** Fold a content signal into the coherence check for tracked-dirty and
  untracked files that are in the snapshot — a per-file content hash (bounded,
  from the same archive stream), compared pre/post — so a mid-stream content
  change is detected and the capture is refused and cleaned up.
- **Proof.** A live test mutates the *contents* of an already-dirty file during
  streaming and asserts the capture is refused and fully cleaned up (the current
  test only changes commit identity).

### R6 — High: engine, routes, and frontend disagree on record shapes and transitions

- **Defect.** Multiple seam mismatches, all consequences of R1 (the pieces were
  never run against each other):
  - routes store researcher responses as `sResponseText`; the engine/charter
    read `sText`;
  - the engine stores a candidate under `dictCandidatePlan.dictResult`; the UI
    reads top-level `sPlanText`/`sText`;
  - exhausted-round choices are sent as ordinary strings (`[exit] …`); the
    backend never invokes the engine's three defined exit transitions;
  - the form renders council settings but the convene request omits them;
  - the UI says messages queue at a protocol boundary, while the backend returns
    409 whenever the synthetic turn is live.
- **Root cause.** No shared contract test across the three layers.
- **Fix.** Define the record/field contract once (the engine's shapes are the
  authority), and make routes and frontend conform: responses use `sText`; the
  UI reads the candidate from its real path; the three exhausted-round controls
  map to the engine's exit transitions (bounded-resolution-round /
  resolve-or-override-then-final-veto / reject-archive); the convene request
  sends the settings the form collects; the composer's "queued at boundary"
  copy matches the real continuation behavior (which R1 makes true — messages
  are recorded and consumed at the next phase boundary, not 409'd).
- **Proof.** A contract test asserts the field names the engine reads are the
  ones the routes write; a frontend contract test asserts the settings are sent
  and the exhausted-round controls post the engine's transition names; the R1
  integration test exercises a real `respond` continuation.

### R7 — High: capability reporting is fictional

- **Defect.** `bAvailable: … or True` is unconditionally true; the endpoint
  advertises both `claude` and `codex` though only a Claude adapter exists.
- **Root cause.** A placeholder that was never made real, plus `codex` in the
  allowed-providers set ahead of its adapter.
- **Fix.** `bAvailable` reflects the real probe (SDK present, login present).
  The provider set reflects only providers with a real adapter (see R9 — Codex
  becomes real, so both stay, but each reports its true availability).
- **Proof.** A test asserts an unavailable provider (no login / no SDK) reports
  `bAvailable: False` and the toolbar disables/explains accordingly; a test
  asserts no provider without an adapter is ever advertised.

### R8 — Governance: mutation ratchets stated honestly

- **Defect.** The PR claimed "every rising site is dispositioned." False for the
  `285 → 287` unclassified-row rise — those rows are `UNCLASSIFIED` by
  definition. The `12 → 34` Docker-SDK rise is dispositioned but its rationale
  claims registry governance the production path does not perform.
- **Fix (mostly falls out of R4/Option B).** With every council Docker call
  behind the gateway, the ~22 opaque SDK sites collapse to the gateway's small
  surface, so `untraceable-docker-sdk-root` **falls** rather than triples, and
  its disposition ("governed by the registry") becomes *true* because the
  gateway performs the reservation/settlement. Re-examine the unclassified-row
  rise honestly: classify the council's use-site rows where possible, and where
  a row genuinely stays best-effort metadata, say so — never call it
  dispositioned. Any residual ratchet change is presented to the maintainer as a
  reviewed decision with the real reason, not a papered-over number.
- **Proof.** `--check` clean; the single-authority gateway test (R4); the
  ratchet constants reflect the post-gateway counts with accurate comments.

### R9 — Feature: implement the Codex runner adapter

- **Context.** The MVP floor is "two distinct models." Two Claude models via one
  adapter clears it, but the design prefers at least one participant from a
  *different provider* (§6.3) because same-family models share blind spots, and
  the current code already advertises Codex. Rather than de-advertise, build it.
- **Fix.** A Codex `CouncilProviderConnection` parallel to Claude's:
  1. headless launch contract (Codex CLI's non-interactive/JSON mode), with
     researcher/plan/peer text kept out of argv (on stdin as labelled untrusted
     material, exactly as Claude's adapter does);
  2. the charter delivered through Codex's own highest-priority instruction
     channel, **separable from the project's agent docs** in the snapshot — this
     is a per-adapter empirical finding (§5.5); if Codex has no separable
     channel, the adapter discloses that in its capability card or routes to the
     API backend, and never shadows the snapshot's `AGENTS.md`;
  3. normalized event parsing, structured-result extraction, resolved model
     identity, usage, and failure classification;
  4. live model discovery through Codex's own mechanism (§8.2), never a
     hardcoded table;
  5. the same extraction-only credential lane (R10) against Codex's own login
     store, with **its own** credential empirics.
- **Proof.** The §15.2 CLI-adapter tests for Codex (fixed argv, headless launch,
  streaming parse across chunk boundaries, model-id extraction, non-zero exit
  reported honestly); a live runner turn with a fake Codex-shaped provider; live
  model discovery. The credential empirics are R10's live gate, per provider.

### R10 — Gate: live credential verification per provider

- **Defect / status.** The runner reuses the researcher's subscription login
  (access token, not refresh; materialized into the runner; no writeback). Three
  properties are empirical and unverified for **each** provider:
  1. an access-token-only copy authenticates the CLI headless;
  2. using the copy does not rotate/invalidate the project container's login
     (non-interference);
  3. staged host files and the in-runner copy are cleaned on success, failure,
     cancellation, and crash-recovery.
  The launch UI currently states the narrowest token "is copied" as if proven —
  a truth-in-UI violation.
- **Fix.**
  1. Make the runner backend for a provider **disabled by default** and gated on
     a recorded, dated result of the live check for that provider.
  2. Correct the launch UI to state the credential handling as the accepted,
     *displayed* risk it is (§2.7) — never as proven security — until the check
     passes.
  3. Run the manual live check on a real paid account (Claude first, Codex when
     R9 lands): one runner, copied token only, confirm a trivial headless turn
     completes; confirm the project container's login still works afterward;
     confirm the token value did not rotate; confirm all staged files are gone;
     repeat across an injected failure and a simulated crash-recovery.
  4. If a provider fails 1 or 2, it does **not** ship on the runner backend —
     the design's fallback is the API backend (separate keys, no subscription
     reuse), which is **not currently built**; building it is a scoped decision,
     not an assumption.
- **Proof.** The structural lane keeps its fake-token tests (delivery,
  ownership, cleanup on the paths a test can reach). The three live properties
  are recorded as a dated maintainer verification in the Phase 0 findings, and
  the runner backend's enablement flag reads that record. No green test is
  allowed to *imply* the live properties hold.

### R11 — Hygiene: rebase and the real end-to-end proofs

- Rebase `feat/agent-council` on current `main` and resolve the `index.html`
  conflict (main's upload-source-label work and the council toolbar both touch
  it).
- Rebuild the browser journey to drive the **real** controller flow (R1), not
  patched store state, against the fail-closed fake provider.
- Re-run the full suite, the browser lane, the mutation `--check`, and the
  carrier audit as the merge gate.

---

## 2. Build order

The controller is the spine; most fixes attach to it, and two prerequisites
must land first so the controller is built on solid ground.

1. **R2 (project scoping)** and **R4/Option B (the Docker gateway + registry
   wiring + fail-closed cleanup)** — prerequisites. The controller must create
   scoped campaigns and drive runners through the gateway, so these come first.
   R8 (honest ratchets) falls out of R4 and is finished alongside it.
2. **R1 (the controller)** — the spine, built on R2 + R4.
3. **R3 (accept gate)** and **R6 (record-shape/transition unification)** — the
   protocol correctness the controller now makes reachable.
4. **R5 (snapshot coherence)** and **R7 (honest capability reporting)** —
   correctness fixes that can proceed in parallel with 3.
5. **R9 (Codex adapter)** — parallelizable with 3–4 once the controller (R1) and
   the connection interface are stable.
6. **R10 (credential live gate)** — the manual, maintainer-run gate, per
   provider; blocks *enabling* the runner backend but not the rest of the build.
7. **R11 (rebase + real end-to-end proofs + full gates)** — last.

Items 1–2 are sequential; 3–5 fan out; 6 is a maintainer action; 7 closes.

## 3. Definition of done (merge gate)

- A researcher clicks Agent Council on a **containerized** project, and a real
  campaign runs to `planReady` over real disposable runners with no fabricated
  state — proven by an HTTP integration test, not a patched one.
- A campaign is invisible and un-actionable from any other project (R2 test).
- Acceptance requires `planReady` and accepts the council's own candidate (R3).
- No runner can leak on any exit path; every runner is registry-tracked and
  destroyed with proven absence; a quarantined teardown never reports confirmed
  evidence (R4).
- Every council Docker call goes through the gateway; the mutation `--check` is
  clean and the Docker-SDK ratchet has *fallen*, its disposition now true (R4,
  R8).
- Capability and credential UI state only what is true; the runner backend is
  enabled for a provider only against a recorded live-credential result (R7,
  R10).
- Claude and Codex both have real, tested runner adapters with live model
  discovery (R9); each has its own recorded credential verification (R10).
- Full Python suite, browser lane (driving the real flow), mutation `--check`,
  and carrier audit all green on a branch rebased on `main` (R11).

## 4. Explicitly out of scope (unchanged from the spec)

Review councils, the Deep protocol, tracked/manifest-integrated artifacts, and
the API-backend adapters remain deferred — **except** that the API backend
becomes required *for a specific provider* if and only if that provider fails
its R10 runner-credential gate. Building it is then a scoped decision, taken
with the failure evidence in hand.
