"""App-owned council registry: reservations, admission, idle veto, drain.

Phase 2 of the Agent Council (design/agentCouncil.md sections 9.3, 9.4,
21). Runner turns are paid, long-running work the hub must account for,
and a runner is NOT the active project container: it never belongs in
``dictContainerOwners`` or the commit-carrier registry. This module is
the single app-owned authority for that accounting. It grants no
container or provider permission — it only decides whether a turn may be
admitted, records write-ahead runner reservations, and reports whether
the hub is doing live council work.

Four responsibilities, each a materialized force (section 9.3):

- **Write-ahead reservations.** A reservation is recorded BEFORE its
  runner is created and settled only on proven destruction (section
  9.6). A pending reservation with no container is dropped on drain; a
  live one is destroyed; a quarantined one stays visible and keeps
  vetoing idle exit until reconciled.
- **Two-scope admission (section 9.4).** A turn is admitted only when
  both its campaign quota AND the hub-wide ceiling have room, so two
  simultaneous campaigns cannot each claim the full allowance. Ceilings
  cover concurrent runners, aggregate memory and CPU, concurrent API
  requests, and per-provider concurrency.
- **Compare-and-settle.** Every reservation carries an epoch, and a
  settle that names a stale epoch is refused, so a late callback from a
  destroyed runner can never erase the successor that reused nothing of
  it.
- **Idle-watchdog veto + shutdown drain.** ``fbCouncilRegistryReportsLiveWork``
  is the fail-closed predicate ``serverLifespan._fbHubShouldSelfExit``
  must consult so the hub cannot self-SIGTERM mid-turn (section 21);
  ``fdictDrainCouncilRegistry`` stops admitting and destroys or
  quarantines live runners before the ordinary release hooks run.

It is deliberately NOT ``dictContainerOwners`` and shares nothing with
it. The registry is a plain dict driven by module functions, the same
shape the campaign record uses, so ``app.state`` owns one value and no
class-instance identity has to be threaded through the protocol records
(section 9.8 forbids Docker ids and SDK objects in those).
"""

from vaibify.config import containerLock

from . import agentCouncilRunner

__all__ = [
    "S_COUNCIL_REGISTRY_STATE_KEY",
    "S_RESERVATION_PENDING",
    "S_RESERVATION_LIVE",
    "S_RESERVATION_QUARANTINED",
    "S_API_REQUEST_ACTIVE",
    "S_API_REQUEST_INTERRUPTED",
    "SET_LIVE_RESERVATION_STATUSES",
    "CouncilAdmissionError",
    "fdictBuildDefaultGlobalCeilings",
    "fdictCreateCouncilRegistry",
    "fdictReserveRunner",
    "fnMarkRunnerCreated",
    "fdictSettleReservation",
    "fbRegisterTurnInFlight",
    "fnRetireTurnInFlight",
    "fdictRecordApiRequest",
    "fnSettleApiRequest",
    "fbCouncilRegistryReportsLiveWork",
    "fbHubHasLiveCouncilWork",
    "fdictDrainCouncilRegistry",
    "fdictReconcileLabeledRunnersOnRestart",
]

# The single ``app.state`` attribute Phase 3 stores the registry under.
S_COUNCIL_REGISTRY_STATE_KEY = "dictCouncilRegistry"

S_RESERVATION_PENDING = "pending"
S_RESERVATION_LIVE = "live"
S_RESERVATION_QUARANTINED = "quarantined"
# A live reservation is any that still represents a runner the hub cannot
# prove is gone. All three keep the hub from retiring and hold budget.
SET_LIVE_RESERVATION_STATUSES = frozenset(
    {S_RESERVATION_PENDING, S_RESERVATION_LIVE, S_RESERVATION_QUARANTINED}
)

S_API_REQUEST_ACTIVE = "active"
S_API_REQUEST_INTERRUPTED = "interruptedUnknownUsage"


class CouncilAdmissionError(Exception):
    """A reservation or turn was asked for outside the registry contract."""


def fdictBuildDefaultGlobalCeilings():
    """Build conservative hub-wide ceilings for two competing campaigns.

    The per-campaign quota is deliberately below the hub-wide runner
    ceiling, so a single campaign cannot exhaust the hub and two
    campaigns competing are bounded by the global figure, not by twice
    the per-campaign figure (design section 9.4).
    """
    return {
        "iMaxConcurrentRunners": 4,
        "iMaxAggregateMemoryBytes": 6 * 1024 * 1024 * 1024,
        "fMaxAggregateCpu": 6.0,
        "iMaxConcurrentApiRequests": 8,
        "iPerProviderMaxConcurrent": 4,
        "iPerCampaignMaxConcurrentRunners": 3,
    }


def fdictCreateCouncilRegistry(dictGlobalCeilings=None):
    """Create the empty app-owned council registry."""
    dictCeilings = dict(fdictBuildDefaultGlobalCeilings())
    for sCeilingName, jsonCeilingValue in (dictGlobalCeilings or {}).items():
        if sCeilingName not in dictCeilings:
            raise CouncilAdmissionError(
                f"unknown council ceiling '{sCeilingName}'")
        dictCeilings[sCeilingName] = jsonCeilingValue
    return {
        "bAdmittingNewTurns": True,
        "dictGlobalCeilings": dictCeilings,
        "dictReservationsById": {},
        "dictApiRequestsById": {},
        "setTurnsInFlight": set(),
        "iEpochCounter": 0,
    }


def _flistLiveReservations(dictRegistry):
    return [dictReservation
            for dictReservation in dictRegistry["dictReservationsById"].values()
            if dictReservation["sStatus"] in SET_LIVE_RESERVATION_STATUSES]


def _fdictMeasureLoad(dictRegistry, sCampaignId, sProvider):
    """Measure current hub-wide and per-campaign resource commitment."""
    listLive = _flistLiveReservations(dictRegistry)
    return {
        "iHubRunners": len(listLive),
        "iHubMemoryBytes": sum(dictReservation["iMemoryBytes"]
                               for dictReservation in listLive),
        "fHubCpu": sum(dictReservation["fCpuCount"]
                       for dictReservation in listLive),
        "iCampaignRunners": len(
            [dictReservation for dictReservation in listLive
             if dictReservation["sCampaignId"] == sCampaignId]),
        "iProviderRunners": len(
            [dictReservation for dictReservation in listLive
             if dictReservation["sProvider"] == sProvider]),
        "iActiveApiRequests": len(
            [dictRequest
             for dictRequest in dictRegistry["dictApiRequestsById"].values()
             if dictRequest["sStatus"] == S_API_REQUEST_ACTIVE]),
    }


def _fsCheckAdmission(dictRegistry, sCampaignId, sProvider, dictRunnerCost):
    """Return a refusal reason, or None when both scopes have room.

    Both scopes are checked (design section 9.4): the hub-wide ceiling
    AND the per-campaign quota. A refusal names which bound would break,
    so a waiting turn's reason is legible rather than a bare False.
    """
    if not dictRegistry["bAdmittingNewTurns"]:
        return "the council registry is draining and admits no new turns"
    dictCeilings = dictRegistry["dictGlobalCeilings"]
    dictLoad = _fdictMeasureLoad(dictRegistry, sCampaignId, sProvider)
    iMemory = dictRunnerCost.get("iMemoryBytes", 0)
    fCpu = dictRunnerCost.get("fCpuCount", 0.0)
    if dictLoad["iHubRunners"] + 1 > dictCeilings["iMaxConcurrentRunners"]:
        return "hub-wide concurrent-runner ceiling reached"
    if (dictLoad["iHubMemoryBytes"] + iMemory
            > dictCeilings["iMaxAggregateMemoryBytes"]):
        return "hub-wide aggregate-memory ceiling reached"
    if dictLoad["fHubCpu"] + fCpu > dictCeilings["fMaxAggregateCpu"]:
        return "hub-wide aggregate-CPU ceiling reached"
    if (dictLoad["iProviderRunners"] + 1
            > dictCeilings["iPerProviderMaxConcurrent"]):
        return f"per-provider concurrency ceiling for {sProvider!r} reached"
    if (dictLoad["iCampaignRunners"] + 1
            > dictCeilings["iPerCampaignMaxConcurrentRunners"]):
        return "per-campaign concurrent-runner quota reached"
    return None


def fdictReserveRunner(dictRegistry, sCampaignId, sReservationId, sProvider,
                       dictRunnerCost):
    """Record a write-ahead runner reservation, or refuse admission.

    Called BEFORE the runner is created. On success the reservation is
    ``pending`` and holds budget immediately, so a competing campaign's
    admission sees it; the caller creates the runner and then calls
    :func:`fnMarkRunnerCreated`. Returns ``{"bReserved", "sRefusalReason",
    "dictReservation"}``; a refusal reserves nothing.
    """
    if sReservationId in dictRegistry["dictReservationsById"]:
        raise CouncilAdmissionError(
            f"reservation {sReservationId!r} already exists; a reservation "
            "id is minted once per runner")
    sRefusalReason = _fsCheckAdmission(
        dictRegistry, sCampaignId, sProvider, dictRunnerCost)
    if sRefusalReason is not None:
        return {"bReserved": False, "sRefusalReason": sRefusalReason,
                "dictReservation": None}
    dictRegistry["iEpochCounter"] += 1
    dictReservation = {
        "sReservationId": sReservationId,
        "sCampaignId": sCampaignId,
        "sProvider": sProvider,
        "sStatus": S_RESERVATION_PENDING,
        "sContainerId": "",
        "iMemoryBytes": dictRunnerCost.get("iMemoryBytes", 0),
        "fCpuCount": dictRunnerCost.get("fCpuCount", 0.0),
        "iEpoch": dictRegistry["iEpochCounter"],
    }
    dictRegistry["dictReservationsById"][sReservationId] = dictReservation
    return {"bReserved": True, "sRefusalReason": "",
            "dictReservation": dict(dictReservation)}


def fnMarkRunnerCreated(dictRegistry, sReservationId, sContainerId):
    """Bind a created container to its pending reservation (pending→live)."""
    dictReservation = dictRegistry["dictReservationsById"].get(sReservationId)
    if dictReservation is None:
        raise CouncilAdmissionError(
            f"no reservation {sReservationId!r} to bind a container to")
    dictReservation["sStatus"] = S_RESERVATION_LIVE
    dictReservation["sContainerId"] = sContainerId


def fdictSettleReservation(dictRegistry, sReservationId, sOutcome,
                           iExpectedEpoch=None):
    """Settle a reservation, compare-and-settle against its epoch.

    ``sOutcome`` is one of the runner module's destruction outcomes.
    Proven destruction removes the reservation and frees its budget; a
    quarantine keeps it visible (still holding budget and still vetoing
    idle exit) until reconciliation proves it gone. A settle whose
    ``iExpectedEpoch`` does not match the live reservation is refused, so
    a stale callback cannot erase a successor; a settle for an
    already-removed reservation is a no-op, never an error.
    """
    dictReservation = dictRegistry["dictReservationsById"].get(sReservationId)
    if dictReservation is None:
        return {"bSettled": False, "sReason": "reservationAlreadyGone"}
    if iExpectedEpoch is not None and (
            dictReservation["iEpoch"] != iExpectedEpoch):
        return {"bSettled": False, "sReason": "staleEpochRefused"}
    if sOutcome == agentCouncilRunner.S_OUTCOME_DESTROYED:
        del dictRegistry["dictReservationsById"][sReservationId]
        return {"bSettled": True, "sReason": "destroyed"}
    dictReservation["sStatus"] = S_RESERVATION_QUARANTINED
    return {"bSettled": True, "sReason": "quarantined"}


def fbRegisterTurnInFlight(dictRegistry, sCampaignId, sTurnId):
    """Claim a turn slot; refuse a duplicate turn already in flight."""
    tTurnKey = (sCampaignId, sTurnId)
    if tTurnKey in dictRegistry["setTurnsInFlight"]:
        return False
    dictRegistry["setTurnsInFlight"].add(tTurnKey)
    return True


def fnRetireTurnInFlight(dictRegistry, sCampaignId, sTurnId):
    """Release a turn slot as the turn settles."""
    dictRegistry["setTurnsInFlight"].discard((sCampaignId, sTurnId))


def fdictRecordApiRequest(dictRegistry, sRequestId, sCampaignId, sProvider):
    """Record an active outbound API request (metadata only, secret-free)."""
    if sRequestId in dictRegistry["dictApiRequestsById"]:
        raise CouncilAdmissionError(
            f"API request {sRequestId!r} already recorded")
    dictRequest = {
        "sRequestId": sRequestId,
        "sCampaignId": sCampaignId,
        "sProvider": sProvider,
        "sStatus": S_API_REQUEST_ACTIVE,
    }
    dictRegistry["dictApiRequestsById"][sRequestId] = dictRequest
    return dict(dictRequest)


def fnSettleApiRequest(dictRegistry, sRequestId, sStatus):
    """Settle an API request; an unproven termination is interrupted-unknown."""
    dictRequest = dictRegistry["dictApiRequestsById"].get(sRequestId)
    if dictRequest is None:
        return
    if sStatus == S_API_REQUEST_ACTIVE:
        raise CouncilAdmissionError(
            "an API request settles to a terminal or interrupted status, "
            "never back to active")
    dictRequest["sStatus"] = sStatus


def fbCouncilRegistryReportsLiveWork(dictRegistry):
    """Return True while the hub is doing live, paid council work.

    The idle-watchdog veto (design section 21): any live reservation
    (pending, live or quarantined), any active API request, or any turn
    in flight is live work that must keep the hub from self-retiring.
    """
    if _flistLiveReservations(dictRegistry):
        return True
    if any(dictRequest["sStatus"] == S_API_REQUEST_ACTIVE
           for dictRequest in dictRegistry["dictApiRequestsById"].values()):
        return True
    return bool(dictRegistry["setTurnsInFlight"])


def fbHubHasLiveCouncilWork(app):
    """Fail-closed idle veto predicate over ``app.state`` for Phase 3.

    ``serverLifespan._fbHubShouldSelfExit`` calls this alongside its
    WebSocket and held-container checks; a True answer vetoes the
    self-exit. Absence of the registry means no council has ever run, so
    there is no council work to veto — that is the only False shortcut,
    and it is guarded so the predicate is safe to call before Phase 3
    installs the registry. Any registry that exists is asked directly.
    """
    dictRegistry = getattr(
        app.state, S_COUNCIL_REGISTRY_STATE_KEY, None)
    if not isinstance(dictRegistry, dict):
        return False
    return fbCouncilRegistryReportsLiveWork(dictRegistry)


def fdictDrainCouncilRegistry(dictRegistry, dockerCouncil):
    """Stop admitting new turns and settle every live runner.

    The clean-shutdown drain (design section 9.3): first close admission
    so no turn slips in behind us, then destroy each live runner and
    prove absence — a destroyed runner is freed, an indeterminate answer
    leaves a visible quarantine, never a clean completion. Pending
    reservations that never became a container are dropped. Active API
    requests are marked interrupted-unknown-usage. Returns a report the
    caller can surface; transport closing is the caller's, because the
    registry holds no transport handle.
    """
    dictRegistry["bAdmittingNewTurns"] = False
    listDestroyed = []
    listQuarantined = []
    for dictReservation in list(
            dictRegistry["dictReservationsById"].values()):
        _fnDrainOneReservation(
            dictRegistry, dockerCouncil, dictReservation,
            listDestroyed, listQuarantined)
    listInterruptedApi = []
    for dictRequest in dictRegistry["dictApiRequestsById"].values():
        if dictRequest["sStatus"] == S_API_REQUEST_ACTIVE:
            dictRequest["sStatus"] = S_API_REQUEST_INTERRUPTED
            listInterruptedApi.append(dictRequest["sRequestId"])
    return {
        "listDestroyed": listDestroyed,
        "listQuarantined": listQuarantined,
        "listInterruptedApi": listInterruptedApi,
    }


def _fmoduleImportCouncilGateway():
    """Import the council Docker gateway at call time, not import time.

    The gateway imports this registry for its reserve/settle
    bookkeeping, so a module-level import here would be a cycle; the
    drain and reconcile lanes are the registry's only two SDK-touching
    moments, and both borrow the gateway's container-id primitives.
    """
    from . import agentCouncilDockerGateway
    return agentCouncilDockerGateway


def _fnDrainOneReservation(dictRegistry, dockerCouncil, dictReservation,
                           listDestroyed, listQuarantined):
    """Destroy one reservation's runner and record where it landed."""
    moduleGateway = _fmoduleImportCouncilGateway()
    sReservationId = dictReservation["sReservationId"]
    iEpoch = dictReservation["iEpoch"]
    if not dictReservation["sContainerId"]:
        del dictRegistry["dictReservationsById"][sReservationId]
        return
    dictDestroyed = moduleGateway.fdictDestroyRunnerAndProveAbsence(
        dockerCouncil, dictReservation["sContainerId"])
    fdictSettleReservation(
        dictRegistry, sReservationId, dictDestroyed["sOutcome"], iEpoch)
    if dictDestroyed["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED:
        listDestroyed.append(sReservationId)
    else:
        listQuarantined.append(sReservationId)


def fdictReconcileLabeledRunnersOnRestart(dictRegistry, dockerCouncil):
    """Discover labeled survivors of a hub crash and settle each one.

    Called on restart before any new council starts (design section
    9.4): every council container carries its reservation id as a label,
    so a restarted hub with no in-memory reservations rediscovers the
    survivors and drives each through the same stop/remove/prove
    transaction. A destroyed survivor is done; a quarantined one is
    recorded so it stays visible. Returns the reconciliation report.

    A survivor whose project container is held by a LIVE PEER is spared
    (see :func:`_fbSurvivorBelongsToALivePeerHub`). The council label is
    daemon-wide, so without that test a second hub booting beside a
    working one destroyed its runners mid-deliberation.
    """
    moduleGateway = _fmoduleImportCouncilGateway()
    listSurvivors = moduleGateway.flistDiscoverLabeledRunners(
        dockerCouncil)
    listDestroyed = []
    listQuarantined = []
    listSpared = []
    for dictSurvivor in listSurvivors:
        if _fbSurvivorBelongsToALivePeerHub(dictSurvivor):
            listSpared.append(dictSurvivor["sReservationId"])
            continue
        dictDestroyed = moduleGateway.fdictDestroyRunnerAndProveAbsence(
            dockerCouncil, dictSurvivor["sContainerId"])
        if dictDestroyed["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED:
            listDestroyed.append(dictSurvivor["sReservationId"])
        else:
            listQuarantined.append(dictSurvivor["sReservationId"])
    return {
        "iSurvivorsDiscovered": len(listSurvivors),
        "listDestroyed": listDestroyed,
        "listQuarantined": listQuarantined,
        "listSparedToLivePeer": listSpared,
    }


def _fbSurvivorBelongsToALivePeerHub(dictSurvivor):
    """Report whether another live hub owns this survivor's project.

    The council label is daemon-wide, so "I found a council container"
    never meant "this container is mine". The lease does: a project
    container is held by an exclusive ``fcntl.flock`` at
    ``~/.vaibify/locks/<name>.lock``, and ``fdictReadLockHolder``
    answers with a holder ONLY when a different live process holds it —
    it returns empty for an absent lock, for a stale holder (reaping it
    on the spot), and for this very process. So the three cases that
    must still be swept all sweep:

    - a crashed hub's orphan (the kernel dropped its flock when it died)
    - a survivor this process itself owns
    - an unlabeled survivor, which is unattributable and therefore
      cannot be shown to belong to anyone living

    Only the fourth case — a peer that is demonstrably alive and holding
    the lease — is spared, which is the case that was destroying live
    deliberations.
    """
    sResourceName = dictSurvivor.get("sResourceName", "")
    if not sResourceName:
        return False
    return bool(containerLock.fdictReadLockHolder(sResourceName))
