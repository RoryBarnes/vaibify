# Agent Council — the four verification lanes (R12)

Each lane proves a NAMED slice and nothing else. A green run in one
lane must never be read as covering another — that conflation is how
the prototype's over-claim happened (components verified in isolation,
the feature reported complete).

## Lane 1 — browser journey (fail-closed fake Docker)

`tests/browser/testCouncilPlanningJourney.py`, run with
`python -m pytest tests/browser -m browser` in real Chromium.

Proves: the researcher-visible journey — convene form, real engine
deliberation over scripted fake provider connections, the needsHuman
gate card, acceptance through the planReady gate, reload/reopen, and
the stale-baseline banner rendering the backend's verdict.

Does NOT prove: anything about real runners, the real snapshot
capture (the fixture writes a synthetic sealed snapshot), the
credential gate (patched enabled), or the staleness computation (the
route-level producer is patched here; its computation is lane 2's).

## Lane 2 — HTTP/controller integration (deterministic fake provider)

`tests/testCouncilRoutes.py`, `tests/testCouncilControllerIntegration.py`,
`tests/testCouncilCampaignIdentity.py`, `tests/testCouncilCredentialGate.py`,
run in the ordinary suite.

Proves: the REAL controller, routes, store, serialization, identity
binding, gate/exit transitions, restart classification, acceptance
gate, credential-gate default-off, and the real stale-baseline
computation (manifest vs a modelled live repository) — over real HTTP
with container name != id, no hand-patched campaign state.

Does NOT prove: any Docker behaviour (the provider seam and the
snapshot capture are deterministic fakes), or that a paid provider
turn works.

## Lane 3 — live-Docker containment (real daemon)

`tests/testCouncilGatewayLive.py`, `tests/testAgentCouncilRunnerLive.py`,
`tests/testAgentCouncilEgressLive.py`,
`tests/testAgentCouncilProvidersLive.py`,
`tests/testAgentCouncilContextLive.py` — `pytest.mark.docker_live`;
export `DOCKER_HOST` for the Colima socket first.

Proves: gateway reserve-before-create and settle-on-every-exit,
label-verified destruction, forced-indeterminate quarantine holding
budget, the baseline executor's raise on unproven destruction, the
hardened proxy posture, egress refusal falsifications, resource-limit
falsifications, the real snapshot capture's coherence refusals under
live mid-stream mutation, and a full fake-provider campaign to
planReady over real disposable runners.

Does NOT prove: a real Claude CLI turn (the in-runner provider is a
scripted fake), or anything about a real subscription credential.

## Lane 4 — paid-account credential check (MAINTAINER, manual)

Not runnable by any agent or CI. The maintainer personally runs, on a
paid account and the real project image: one runner, the copied
access-token only, a trivial headless turn, the project login still
valid afterwards, the token not rotated, the staged files gone —
across a failure and a crash-recovery. The result is recorded as the
machine-readable evidence file at
`~/.vaibify/agentCouncils/credentialEvidence.json` carrying every key
in `agentCouncilCredentialGate.LIST_EVIDENCE_REQUIRED_KEYS`; the
runner backend stays DISABLED until that record exists and matches,
and no green test in lanes 1–3 implies these properties hold. The
per-adapter empiric of R11 (a hostile agent doc does not steer a REAL
model over the charter) belongs to this lane too.
