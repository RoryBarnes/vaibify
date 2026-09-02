# The review council — design sketch for the council's second campaign kind

**Status: DESIGN ONLY, 2026-08-26. Nothing here is implemented, and
nothing here is a commitment.** This document exists because the
continuation plan (`agentCouncilContinuationPlan.md` §10.2) removed
"implementation council" from its own scope and owed the idea a home
that is not a section of an unrelated plan. It records what such a
feature must answer, which parts of the current specification it
contradicts, and three corrections established by the 2026-08-26
cross-reviews that any future designer must inherit — because each was
derived the hard way and each is easy to re-derive wrong.

## 1. What is being proposed, smallest honest version first

A **review-only council over an externally supplied implementation**:
the researcher (or an implementation agent outside the council) builds
the change; a council campaign is then convened whose participants
*review* the diff against the accepted plan — objections, security
review, verification-requirement checking — and whose deliverable is a
review verdict, not code. This is the smaller first extension, and it
should be designed and shipped before any council that *writes* code is
considered:

- It reuses the existing protocol shape (proposals become review
  findings; cross-review and veto keep their meaning; synthesis
  composes the verdict).
- It needs no runner write-access to anything: reviewers read a
  snapshot, exactly as planning participants do today.
- Its one genuinely new mechanism is getting the *implementation* into
  the reviewers' snapshot — a capture question, not an execution one.

A full implementation council — participants writing code inside
runners, artifacts flowing back — is a separate, later design. Its
blockers are enumerated in §4 so nobody mistakes the review council for
"most of the way there."

## 2. What the current specification forbids, by name

`design/agentCouncil.md` is a PLANNING-council specification:

- Planning-only scope: `agentCouncil.md` §2 (the product is a planning
  deliberation; the plan is handed to a fresh implementation agent).
- Explicit non-goal: §3 — the council does not implement, and no
  campaign kind other than planning exists.

So a review council is a **specification amendment first**: campaign
kinds must become a real concept (`sCampaignKind`, persisted,
validated, rendered), with the planning kind's behavior byte-identical
to today's. The amendment pattern follows the continuation plan's §1:
name the non-goal being retired, keep the guarantees that motivated it,
and land the amendment in the same change as the mechanism.

## 3. Concepts the review council must add

1. **Campaign kinds.** A `planning` campaign and a `review` campaign
   share identity, snapshot, charter and gates but differ in phase
   vocabulary and deliverable schema. The kind is immutable after
   convene.
2. **Artifact lineage.** A review campaign points at the planning
   campaign whose accepted plan it reviews (`sPlanCampaignId` +
   `sPlanSha256`), and at the implementation it reviews (a snapshot
   content identity, §5). A review of "the change" with no recorded
   ancestry is an unfalsifiable review.
3. **Modified-state reconstruction.** The reviewers reason about
   *base + change*. The server must be able to state, mechanically,
   what the change was — see the third correction in §5.
4. **Review gates.** The researcher's decision surface is different:
   accept-review / request-revision / override-objection, each a
   recorded researcher decision, none an engine-invented state.
5. **Resource bounds.** A review burns the same paid turns a planning
   round does; the same credential gate, egress scopes and turn
   budgets apply unchanged. No new spend lane.
6. **A security review of runner-to-host artifact retrieval** — even
   read-only review needs the implementation delivered INTO runners,
   and any future implementation kind needs artifacts delivered OUT.
   The out direction is the dangerous one and is NOT needed for the
   review council; deferring it is most of why the review council is
   the right first step.

## 4. Blockers for a code-writing implementation council (deferred)

Recorded so the review council is not mistaken for a stepping stone
that made these disappear: a turn-result schema for code artifacts; a
server-observed change manifest (§5, third correction); write-capable
runners inside the egress boundary; artifact retrieval from runner to
host and its authority model; a verification lane that runs the
council's own tests without trusting the council; and gates for
"the diff the researcher applies is the diff the council reviewed"
(sha-sealed, like the accepted plan). Each is a design section, and
several are security reviews.

## 5. Three corrections to inherit, not re-derive

These were established by the 2026-08-26 cross-reviews of the
continuation plan, against source. Each contradicts a plausible first
guess.

1. **The snapshot and credential lanes do NOT share a carve-out.**
   Snapshot capture uses `container.get_archive` — a Docker daemon API
   read (`agentCouncilContext.py`) — while credential extraction rides
   the single typed-read exemption `DockerConnection._ftRunTypedRead`.
   A review-council designer extending "the snapshot exemption" to new
   reads is extending something that does not exist. Adding a named
   operation to `_DICT_TYPED_READ_PROGRAMS` is *not* a second grant
   point — `tests/testMutationBoundary.py` pins the grant *method*,
   not the table — and `S_TYPED_READ_FILE_BASE64` may serve bounded
   patch retrieval under a stated size limit.
2. **Council runners are reached through the council gateway's handle
   authority.** Any retrieval of anything from a runner must stay
   keyed by the gateway handle (`agentCouncilDockerGateway`); driving
   an ordinary `DockerConnection` against a raw council container id
   would bypass the single-gateway authority that the egress and
   teardown proofs depend on. (Asserted by the cross-review; trace the
   gateway before designing retrieval.)
3. **A model-authored patch is not a change manifest.** The server
   must observe the runner's before/after state itself; a diff the
   model *says* it made is a claim, not an observation.
   `EvidenceDisciplineMixin._fnRecordModifiedStateClaim` already
   refuses confirmed claims lacking a reconstructable manifest —
   extend that discipline rather than building parallel machinery.

## 6. What would make this worth building

The planning council's value case was: expensive human decisions,
cheap parallel deliberation. The review council's case must be argued
on its own numbers before implementation: a review campaign costs a
full council round of paid turns, against the alternative of one
`/code-review`-style single-agent pass. The design should start with
the evidence from real usage — how many accepted plans produced
implementations whose defects a council-shaped review would have
caught — and a researcher ruling that the spend is wanted. That
evidence does not exist yet; collecting it is a prerequisite, not a
formality.
