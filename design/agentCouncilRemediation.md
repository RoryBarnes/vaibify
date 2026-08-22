# Agent Council — implementation plan for the remediation

**Who this is for.** The agent implementing the remediation of the
`feat/agent-council` prototype. It is self-contained: you should not need the
conversation that produced it. Read it top to bottom before writing code.

**The authority above this file.** `design/agentCouncil.md` (revision 13) is the
specification — protocol, charter, containment, and security requirements. This
plan realizes it where the prototype only stubbed it; when in doubt, the spec
wins. `design/agentCouncilPhase0Findings.md` records the Claude CLI empirics you
depend on.

**The core truth to internalize.** The prototype's *components* are real and
well-tested in isolation, but the *production feature is disconnected from them*:
`CouncilEngine(...)` is constructed only under `tests/`. Two external reviews
agree it is a strong prototype, not a feature. Your job is the missing
integration and the correctness/containment/authorization defects below — not a
rewrite. **Do not trust a green suite as proof of the feature**: this repo has
shipped fatal bugs under fully green suites because stubs agreed with each other
(see `CLAUDE.md` → "Epistemics"). Every guarantee that crosses an
HTTP/WebSocket/container boundary must be proven with a real connection and with
container **name ≠ id**.

---

## 0. Environment and working rules

- **Worktree / branch.** Work in
  `/Users/rory/src/vaibify/.claude/worktrees/agent-council`, branch
  `feat/agent-council`. Do not `cd` to the main checkout or other worktrees.
  PR #81 is **closed** (so branch pushes spend no CI); do not reopen it — open a
  fresh PR only when the merge gate (§4) is met.
- **Docker for the Python SDK.** The daemon is Colima; the `docker` CLI finds it
  by context but the **Python SDK does not**. Before running any `docker_live`
  test or SDK code, `export DOCKER_HOST="unix:///Users/rory/.colima/default/docker.sock"`.
- **Live tests** carry `pytest.mark.docker_live` (see `tests/conftest.py` — they
  are not auto-skipped; they run against the real daemon). Run them explicitly,
  e.g. `python -m pytest tests/testAgentCouncilProvidersLive.py -v`.
- **Required verification commands** (run the ones your change touches):
  - Python suite: `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`
  - Architectural/style: `python -m pytest tests/testArchitecturalInvariants.py tests/testStyleInvariants.py -q`
  - Mutation inventory: `python tools/generateMutationInventory.py --check`
    (regenerate with `--write`); dispositions in `tests/testBlindSpotDispositions.py`
  - Carrier coverage: `PYTHONPATH=. python tools/carrierIntentAudit.py`
  - Host-mode refusal (both directions): `python -m pytest tests/testHostModeContainerOnlyRefusals.py -v`
  - Browser lane (the ONLY real frontend check):
    `pip install -e '.[browser]' && python -m playwright install chromium`, then
    `python -m pytest tests/browser -m browser`.
- **Style is machine-enforced** (`tests/testStyleInvariants.py`): Hungarian
  variable prefixes on every binding, `f`+return-type function prefixes (`fn`
  procedure, `ffn` returns-a-function, `fdict`/`flist`/`fb`/`fs`/`fi`/`ft`),
  camelCase filenames, no abbreviations under 8 characters, `__all__` per module.
  Never edit `tests/styleInventory.json` seeds/budgets to pass.
- **Do not** import `vaibify.gui` into `pipelineUtils.py`; do not construct a
  `TerminalSession` or touch `/ws/terminal`; reproducibility terminology is
  `PROOF`/`iProofLevel`; no science-specific identifiers anywhere.

## 1. Starting conditions

**Commits on the branch (prototype + this plan):** `68979e4f` → `16e92880`.
Rebase first (§3, step 1).

**Modules that already exist and are tested in isolation — read them before
changing:**

| Module | Role | State |
|---|---|---|
| `agentCouncil.py` + `agentCouncilCampaign/Charter/Resolution/Evidence.py` | Pure engine, charter, state machine, veto/quorum, evidence ledger | Solid; 52 falsification tests (`tests/testAgentCouncilEngine/Charter/Ledger.py`) |
| `agentCouncilStore.py` | Event ring, evidence ledger, durable checkpoint, local accept | Solid unit-tested |
| `agentCouncilProviders.py` | Claude CLI runner adapter (`CouncilProviderConnection`), capability contract, credential lane, baseline executor | Adapter works live; **defects R4/R7/R10** |
| `agentCouncilRunner.py` | Runner/sandbox container lifecycle (SDK) | Live-falsified; **must move behind the gateway, R4** |
| `agentCouncilEgress.py` | Internal network + CONNECT proxy (SDK) | Live-falsified; **must move behind the gateway + harden, R4** |
| `agentCouncilContext.py` | Immutable snapshot (`get_archive`) + manifest | Works; **coherence weak (R5), agent-doc policy unresolved (R11)** |
| `agentCouncilRegistry.py` | Reservations, admission, idle-veto, drain | Solid; **production path never calls it (R4)** |
| `routes/councilRoutes.py` | 9 HTTP routes | **No controller, no scoping, accept bypass (R1/R2/R3)** |
| `static/scriptAgentCouncil.js` | Modal, workspace, polling | Shell; **shape/transition mismatches (R6), fabricated staleness (R12)** |

**The engine↔provider seam you will wire.** The engine drives a
`CouncilProviderConnection`: `fdictPrepareImmutableContext(dictTurnRequest)` →
`fnStartTurn(dictRequest)` → `fiterStreamNormalizedEvents()` →
`fdictCollectStructuredResult()` → `fsReportCompletion()`. **Read
`tests/agentCouncilHarness.py` and `tests/testAgentCouncilProvidersLive.py`** —
they construct `CouncilEngine` with real connections exactly as production must,
then assert a two-model campaign reaches `planReady` over real runners. Your
controller (R1) mirrors that construction in product code.

**Locked decisions (do not relitigate):**
- **Option B** — one typed Docker gateway is the sole SDK caller for the council.
- **Credentials default-off**, enabled only against a machine-readable evidence
  record (R10).
- **Controller is a serialized authority** — sole campaign-state writer (R1).
- **Canonical identity** — not the raw URL id (R2).
- **Codex is deferred** to a feasibility-first follow-up (R9); this branch ships
  Claude-only and reports Codex as unimplemented.

## 2. Work items

Each item: **defect → fix (with file targets) → proof**. A "proof" is a
falsification test that fails if the fix regresses.

### R1 — Build the controller as a serialized authority
- **Fix.** Add a **campaign controller** (new module, e.g.
  `agentCouncilController.py`) that is the **sole writer of campaign state**.
  `routes/councilRoutes.py` stops mutating campaign state; it submits **bounded
  commands** (`start`, `respond`, `requestStop`, `acceptPlan`, `delete`) onto a
  **per-campaign serialization primitive** (a single-owner async lock or
  per-campaign command queue held in `app.state`) that the controller drains in
  order. The controller:
  - runs container mutations under the **council registry admission**
    (write-ahead reserve, settle on proven absence — §10.3; NOT the commit
    carrier), and durable checkpoints via `agentCouncilStore` (§7.5);
  - on Start: captures the snapshot (`agentCouncilContext`), builds
    `CouncilEngine` with the real Claude connection(s), drives deliberation,
    checkpoints per phase, retires the turn;
  - on a human gate: settles all live work, suspends (no live runner/turn);
  - on raise: records `failed`/`interrupted` (never "running"), settles/
    quarantines runners;
  - on **project release**: drains before the lease releases;
  - on **restart**: discovers labelled-runner survivors (destroy-with-proof or
    quarantine) and classifies a turn with no terminal record as `interrupted`,
    never resumed.
  Register controller state in `appFactory.py` beside the registry; the
  idle-veto and shutdown drain already exist — extend them to the controller's
  live work.
- **Proof.** (a) HTTP integration: Start → fake provider in a **real runner** →
  `planReady`, no hand-patched state. (b) Concurrency: fire
  respond/stop/accept/delete during a live turn; assert ordering + consistent
  state. (c) Crash: kill the controller mid-turn; assert restart →
  `interrupted`, runners settled/quarantined.

### R2 — Canonical project identity + cross-project refusal
- **Fix.** Bind each campaign (`agentCouncilCampaign.fdictCreateCampaign`) to a
  **canonical identity triple**: the container **name / lease principal**
  (name-keyed owner authority, not the raw id), the **validated project-repo
  identity** (a container can host several — use the repo's project-repo
  resolver, e.g. `containerGit.fsDetectProjectRepoInContainer`), and the
  **snapshot identity**. Every `{sCampaignId}` route resolves the campaign and
  **refuses 404** (not 403 — do not leak existence) unless all three match the
  authorized principal and its active project repo. `list` filters to that
  principal + repo.
- **Proof.** Two resources (name ≠ id each): A's campaign is 404 from B on every
  route and absent from B's list. Two project repos in one container: repo-1's
  campaign is unreachable/unlisted under repo-2.

### R3 — Route acceptance through the engine's consensus gate
- **Fix.** `accept-plan` submits an `acceptPlan` command to the controller (R1),
  which calls the engine's guarded acceptance (`agentCouncil.py` — requires
  `planReady`), accepts the **council's own server-held candidate** (not caller
  text), then writes `plan.md` locally and transitions.
- **Proof.** Accept on non-`planReady` → 409; accept on `planReady` persists the
  engine's candidate; **invert** the current stop-then-accept test to assert the
  refusal.

### R4 — Typed Docker gateway, correct quarantine semantics, egress hardening
- **Fix.** New `agentCouncilDockerGateway.py` — the **only** council caller of
  the Docker SDK. `agentCouncilRunner.py` and `agentCouncilEgress.py` keep their
  pure helpers (argv, tar via `fbaBuildStampedFileTarball`, env, DNS wiring,
  absence-probe logic) but call the gateway for every SDK op. The gateway:
  - **mints opaque reservation handles** and accepts **only** those, never
    arbitrary container ids; **verifies registry identity + the council label**
    on the target before any destructive op (so it cannot touch the active
    project container);
  - **reserves before create, destroys on every exit path** (async context
    manager / `finally`);
  - **quarantine semantics (this is the crux):** proven absence → settle clean;
    an **indeterminate** daemon answer → reservation stays **visibly
    quarantined**, admission stays **consumed**, UI says **runner may exist**,
    and **no evidence becomes `confirmed`**. The baseline executor must
    **propagate** the destruction outcome (currently discards it).
  - **egress hardening:** the proxy container gets the runner's posture — a
    **digest-pinned** image (not floating `python:3.10-slim`), non-root, all caps
    dropped, no-new-privileges, resource limits.
- **Proof.** Inject an exception at each fallible step after create → reservation
  always settled-or-quarantined, never leaked unrecorded (real daemon, name ≠
  id). Force indeterminate teardown → quarantined + admission consumed + not
  `confirmed` + UI may-exist. Architectural test: every council SDK call
  originates in the gateway, and the gateway refuses a handle whose identity/
  label fails to verify. Proxy asserted pinned/non-root/cap-dropped/limited.

### R5 — Real snapshot-coherence algorithm
- **Fix (`agentCouncilContext.py`).** Take the **bounded project lock** (§9.2)
  for the capture window. Obtain **two independent pre/post source identities**
  outside the archive stream (commit + per-path content digest of the
  tracked-dirty and included-untracked set), and match archive members to them by
  path. Any mismatch — including **untracked add/delete, rename, symlink swap, or
  content changed-then-reverted** whose intermediate bytes differ — **refuses the
  capture and cleans up**.
- **Proof.** Live: mutate a dirty file's **contents** during streaming → refusal
  + cleanup; separate cases for rename, symlink swap, change-then-revert.

### R6 — Unify record shapes and transitions
- **Fix.** The engine's shapes are authority. Fix: researcher responses use
  `sText` (routes currently write `sResponseText`); the UI reads the candidate
  from its real path (`dictCandidatePlan.dictResult`, not top-level
  `sPlanText`/`sText`); the three exhausted-round controls POST the engine's exit
  transitions; the convene request sends the form's council settings; the
  composer copy matches R1's real continuation. Build an accepted-plan renderer
  for the real candidate shape.
- **Proof.** Contract test: fields the engine reads == fields routes write.
  Frontend contract test: settings sent; exhausted-round controls post the exit
  transition names. R1 exercises a real `respond`.

### R7 — Honest capability reporting (Claude-only)
- **Fix.** `bAvailable` reflects the real probe (remove `... or True`).
  `SET_ALLOWED_PROVIDERS` (in `routes/councilRoutes.py`) advertises **Claude
  only**; no adapter-less provider is advertised.
- **Proof.** Unavailable provider → `bAvailable: False` + toolbar disables/
  explains; no adapter-less provider advertised.

### R8 — Honest mutation ratchets (falls out of R4)
- **Fix.** With every SDK call behind the gateway, `untraceable-docker-sdk-root`
  **falls** and its disposition ("governed by the registry") becomes *true*.
  Re-examine the unclassified-row rise: classify council use-site rows where
  possible; where a row is genuinely best-effort metadata, say so — never call it
  dispositioned. Regenerate the inventory; update
  `tests/testBlindSpotDispositions.py`.
- **Proof.** `--check` clean; single-authority gateway test; ratchet constants
  reflect post-gateway counts with accurate comments.

### R9 — Codex: deferred to a feasibility-first follow-up (confirmed)
- **This branch:** Codex is not built and not advertised (R7). **Follow-up
  (separate branch, after this merges):** Codex Phase 0 empirics **first**
  (headless launch; instruction channel separable from snapshot agent docs, §5.5;
  R10 credential feasibility), then the adapter. If feasibility fails, Codex ships
  on the API backend or not at all — decided with evidence, before code.

### R10 — Version-bound, machine-readable credential enablement
- **Fix.** The runner backend for a provider is **disabled by default**, enabled
  only against a **machine-readable evidence record** keyed to **provider+backend,
  CLI version, project-image/executable identity, credential schema/source, host
  platform, verification date**. Any mismatch → **disabled**. Even with a match,
  **login presence + usable models are probed live** at launch. The **residual
  token-exfiltration disclosure stays visible even after verification** (§2.7) —
  the UI never states the handling as "proven secure" (it currently overclaims;
  fix `scriptAgentCouncil.js`). The live check itself (one runner, copied
  access-token only, trivial headless turn; project login still valid after;
  token not rotated; staged files gone; across a failure and a crash-recovery) is
  a **maintainer action on a paid account** — you cannot run it; you build the
  gate that *reads* its result and defaults off without it.
- **Proof.** Fake-token structural tests stay (delivery, 1000-owned, cleanup on
  reachable paths). Enablement flag defaults off on any key mismatch (tested). No
  green test may imply the live properties hold.

### R11 — Decide the agent-instruction-file policy
- **Defect.** `agentCouncilContext.py` currently **excludes**
  `CLAUDE.md`/`AGENTS.md`/`GEMINI.md` and `.claude`/`.codex` at every depth, while
  the plan/spec also talk about "not shadowing" a snapshot `AGENTS.md` — two
  different policies.
- **Fix.** Decide explicitly and record it: are project instruction files
  **evidence** or **exclusions**, at **which paths**, and **how does the CLI
  charter channel out-rank** any that remain? Default to test against: exclude the
  agent-instruction files (meta-instructions, not source under review) **and**
  verify the `--append-system-prompt` charter out-ranks any hostile file — belt
  and suspenders — but make it a decision with a per-adapter empirical test, not
  an incidental exclusion edit.
- **Proof.** Per-adapter test: a snapshot with a hostile `CLAUDE.md`/`AGENTS.md`
  does not override the charter; plus a test pinning the chosen exclusion policy.

### R12 — Separate the four verification lanes; add a stale-baseline producer
- **Fix.** Name and keep four **distinct** lanes, stating what each does/does not
  prove: (1) browser + fail-closed fake Docker — UI/journey only, nothing about
  real runners; (2) HTTP/controller integration + deterministic fake provider —
  the real controller/routes/store/serialization/recovery, no real Docker; (3)
  live-Docker containment — real gateway/runners: leak/quarantine, resource +
  network falsification, absence proofs; (4) paid-account credential (R10,
  manual). Add a **real stale-baseline producer**: compute current project
  identity (commit + dirty digest of the active repo) vs the recorded snapshot
  identity; the UI shows "baseline stale" from that comparison, not fabricated
  state.
- **Proof.** Each lane named with its scope; a test drives the real staleness
  computation (change the project after capture → stale shown).

## 3. Build order

1. **Rebase** on current `main`; resolve the `index.html` conflict; regenerate
   the generated ledgers (`generateMutationInventory.py --write`,
   `tools/generateStyleInventory.py` only if the style suite demands it). Rebase
   again at merge time.
2. **R2 canonical identity** + **R1 serialization primitive** (the substrate).
3. **R4 gateway** + quarantine semantics + egress hardening; **R8** falls out.
4. **R5 snapshot coherence.**
5. **R1 controller** + lifecycle/recovery (crash, release, restart).
6. **R3 accept gate** + **R6 contract unification** + accepted-plan renderer.
7. **R7 honest capability reporting**; **R11 agent-doc policy.**
8. **R10 credential gate** (you build the gate; the live check is the
   maintainer's).
9. **R12 four named lanes** + stale-baseline producer; then rebase + full gates.
10. **R9 Codex** — separate follow-up branch, not here.

Commit at each verified step (the branch is private; commit freely, push is
CI-free while PR #81 stays closed). Use focused subagents per work item if you
orchestrate, but verify each claim yourself by running the proof — do not accept
a subagent's "green" without re-running it.

## 4. Definition of done (open a fresh PR only when all hold)

- Real campaign → `planReady` over real runners, no fabricated state (R1).
- Concurrent commands serialized; controller crash → honest restart (R1).
- Campaign 404/unlisted from another project **and** another repo in the same
  container (R2).
- Acceptance requires `planReady` and accepts the council's own candidate (R3).
- **No possible runner leak can become unrecorded or reported as clean**;
  indeterminate teardown → quarantined + admission consumed + no `confirmed`
  evidence; gateway is sole SDK authority and verifies handle identity/label;
  proxy pinned + hardened (R4).
- Snapshot refuses + cleans up on any torn/independent-observation mismatch (R5).
- Capability + credential UI state only what is true; runner backend enabled only
  against a matching machine-readable record with live probing and the residual
  risk still disclosed (R7, R10).
- Agent-doc policy decided; charter precedence proven per adapter (R11).
- Four verification lanes named + green (browser real-flow, controller
  integration, live-Docker, recorded manual credential); staleness has a real
  producer (R12).
- Full suite, browser lane, mutation `--check`, carrier audit all green on a
  branch rebased on `main`.

## 5. Out of scope

Review councils, the Deep protocol, tracked/manifest artifacts, and the
API-backend adapters stay deferred — except the API backend becomes required
*for a provider* iff it fails its R10 gate. Codex is roadmap, not this branch
(R9).
