"""Serialized command authority for Agent Council campaigns (R1).

The controller is the SOLE writer of campaign state. Routes never
mutate a campaign directly: they submit one of the bounded commands
below onto the per-campaign serialization primitive, and the commands
drain strictly in submission order. Two commands for the same campaign
can never interleave; commands for different campaigns are independent.

This module owns the substrate — the command vocabulary, the
per-campaign locks, the observable command log — and the campaign
runtime registry the controller drives engines through. It is deliberately
free of route imports: the routes call down into it, never the reverse.

Why a lock and not a queue-with-worker: an ``asyncio.Lock`` wakes its
waiters first-in-first-out on one event loop, which IS a per-campaign
command queue, without a worker task whose crash/restart semantics
would need their own recovery story. The lock lives in ``app.state``
(via the controller state dict registered in ``appFactory``), so its
lifetime is the hub process — exactly the lifetime of the in-memory
campaign state it serializes.
"""

import asyncio

__all__ = [
    "CouncilCommandError",
    "LIST_CONTROLLER_COMMANDS",
    "S_COMMAND_ACCEPT_PLAN",
    "S_COMMAND_DELETE",
    "S_COMMAND_GRANT_RESOLUTION_ROUND",
    "S_COMMAND_REJECT_CANDIDATE",
    "S_COMMAND_RESOLVE_OBJECTIONS",
    "S_COMMAND_RESPOND",
    "S_COMMAND_REQUEST_STOP",
    "S_COMMAND_START",
    "S_COUNCIL_CONTROLLER_STATE_KEY",
    "fdictCreateCouncilControllerState",
    "fgenericSubmitCampaignCommand",
    "flistReadCampaignCommandLog",
]

# The single ``app.state`` attribute the routes reach the controller
# state through, beside the registry and the campaign store.
S_COUNCIL_CONTROLLER_STATE_KEY = "dictCouncilControllerState"

# The bounded command vocabulary (remediation R1). A route may submit
# exactly these; anything else is a programming error and refuses loudly.
S_COMMAND_START = "start"
S_COMMAND_RESPOND = "respond"
S_COMMAND_REQUEST_STOP = "requestStop"
S_COMMAND_ACCEPT_PLAN = "acceptPlan"
S_COMMAND_DELETE = "delete"
S_COMMAND_GRANT_RESOLUTION_ROUND = "grantResolutionRound"
S_COMMAND_RESOLVE_OBJECTIONS = "resolveObjectionsThenFinalVeto"
S_COMMAND_REJECT_CANDIDATE = "rejectCandidate"

LIST_CONTROLLER_COMMANDS = [
    S_COMMAND_START,
    S_COMMAND_RESPOND,
    S_COMMAND_REQUEST_STOP,
    S_COMMAND_ACCEPT_PLAN,
    S_COMMAND_DELETE,
    S_COMMAND_GRANT_RESOLUTION_ROUND,
    S_COMMAND_RESOLVE_OBJECTIONS,
    S_COMMAND_REJECT_CANDIDATE,
]

# The command log is an observability convenience for the serialization
# proofs and the UI, never an authority; bounded so a long-lived campaign
# cannot grow it without limit.
I_MAX_COMMAND_LOG_ENTRIES = 200


class CouncilCommandError(Exception):
    """A caller submitted a command outside the bounded vocabulary."""


def fdictCreateCouncilControllerState():
    """Create the empty app-owned controller state.

    A plain dict driven by module functions, the same shape the council
    registry and campaign store use: ``app.state`` owns one value and no
    class-instance identity threads through the protocol records.
    ``dictCampaignRuntime`` holds each campaign's live driving state
    (engine, connections, live-turn task) once the controller launches
    real deliberation; the substrate only reserves the slot.
    """
    return {
        "dictCampaignLocks": {},
        "dictCommandLogByCampaign": {},
        "dictCampaignRuntime": {},
    }


def _flockForCampaign(dictControllerState, sCampaignId):
    """Return (creating on first use) the campaign's serialization lock."""
    lockCampaign = dictControllerState["dictCampaignLocks"].get(sCampaignId)
    if lockCampaign is None:
        lockCampaign = asyncio.Lock()
        dictControllerState["dictCampaignLocks"][sCampaignId] = lockCampaign
    return lockCampaign


def _fnRecordCommandEvent(dictControllerState, sCampaignId, sCommandKind,
                          sStage):
    """Append one bounded command-log row for the serialization proofs."""
    listLog = dictControllerState["dictCommandLogByCampaign"].setdefault(
        sCampaignId, [])
    listLog.append({"sCommandKind": sCommandKind, "sStage": sStage})
    del listLog[:-I_MAX_COMMAND_LOG_ENTRIES]


def flistReadCampaignCommandLog(dictControllerState, sCampaignId):
    """Return a copy of one campaign's command log, submission order."""
    return [dict(dictEntry) for dictEntry in
            dictControllerState["dictCommandLogByCampaign"].get(
                sCampaignId, [])]


async def fgenericSubmitCampaignCommand(dictControllerState, sCampaignId,
                                        sCommandKind, ffnExecuteCommand):
    """Run one bounded command under the campaign's serialization lock.

    Commands for one campaign execute strictly in submission order
    (``asyncio.Lock`` wakes waiters first-in-first-out on one event
    loop); commands for different campaigns are independent. The
    command's return value is the caller's — an ``HTTPException`` raised
    inside the executor propagates unchanged, so a route's refusal
    semantics survive the serialization. The started/settled stages are
    recorded even when the executor raises, so the log shows a failed
    command as settled rather than vanished.
    """
    if sCommandKind not in LIST_CONTROLLER_COMMANDS:
        raise CouncilCommandError(
            f"unknown council command {sCommandKind!r}; the vocabulary "
            f"is {LIST_CONTROLLER_COMMANDS}")
    _fnRecordCommandEvent(
        dictControllerState, sCampaignId, sCommandKind, "submitted")
    async with _flockForCampaign(dictControllerState, sCampaignId):
        _fnRecordCommandEvent(
            dictControllerState, sCampaignId, sCommandKind, "started")
        try:
            return await ffnExecuteCommand()
        finally:
            _fnRecordCommandEvent(
                dictControllerState, sCampaignId, sCommandKind, "settled")
