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

## Lane 4 — paid-account credential check (in-app after consent; manual fallback)

Not runnable by any agent or CI, because it spends a real
subscription. Since the 2026-09-29 ruling the researcher runs it from
vaibify itself: clicking the council button on a project whose image
has no passed test opens a consent modal, and after consent vaibify
runs the credential test (`vaibify/gui/agentCouncilCredentialTest.py`)
on the real project image by its sha256 id — one runner, the copied
access token only, a trivial headless turn, the project login present
and unchanged afterwards, the token not rotated, the staged files gone,
across a failure and a runner killed mid-turn. The consent and the
outcome are recorded separately in the host document at
`~/.vaibify/agentCouncils/credentialEvidence.json`
(`agentCouncilCredentialStore`, schema v3), and the runner backend is
enabled only while that consent is active and its latest outcome is a
pass under the current consent generation. The manual procedure — the
maintainer runs the same checks by hand and writes a record carrying
every key in `agentCouncilCredentialGate.LIST_EVIDENCE_REQUIRED_KEYS`
— is the fallback, and records written that way (v1/v2) are still
read, until the researcher withdraws consent for their key. No green
test in lanes 1–3 implies these properties hold for a real
subscription token. The
per-adapter empiric of R11 (a hostile agent doc does not steer a REAL
model over the charter) belongs to this lane too.
