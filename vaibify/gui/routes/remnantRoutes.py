"""List and remove the leftover processes and files the scanner found.

Three browser-only routes over ``remnantScanner``'s cached scan: a read
of the scan plus the reaper health, a rescan request that only sets
the reaper loop's event (never a scan inline: the enforced-lane
ContextVar follows work into a thread and would refuse the execs), and
a removal that re-verifies every chosen item's identity live and acts
only through the named authorities -- the group terminate-and-prove,
the start-clock-gated keep-alive kill, the registry stop, and the
lifecycle gateway's removal. Each item answers with one sentence; a
refusal becomes that item's sentence rather than aborting the batch.

All three read host state, so each rejects the in-container agent
lane, and the two writes are absent from the agent catalog.
"""

__all__ = ["fnRegisterAll"]

import asyncio
from typing import List

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config import keepAliveManager
from .. import remnantReapers, remnantScanner, terminalContainment
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising, fnRejectAgentTokenLane,
)
from ..routeScope import S_CARRIER_SEPARATE_AUTHORITY, ffnDeclareCarrierMode


class RemnantRemovalRequest(BaseModel):
    """The item ids the researcher selected in the panel."""

    listItemIds: List[str]


def _fnRegisterReadRemnants(app, dictCtx):
    """Register GET /api/system/remnants."""
    del dictCtx

    @app.get("/api/system/remnants")
    async def fdictReadRemnants(requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        dictScan = remnantScanner.fdictScanState(app.state)
        return {
            "sScannedIso": dictScan["sScannedIso"],
            "bScanning": dictScan["bScanning"],
            "sScanError": dictScan["sScanError"],
            "listItems": list(dictScan["listItems"]),
            "dictReaperHealth": dict(remnantReapers.fdictReaperHealth(app)),
            "sGlyphTitle": remnantScanner.S_GLYPH_TITLE,
        }


def _fnRegisterRescan(app, dictCtx):
    """Register POST /api/system/remnants/rescan: sets the event, nothing else."""
    del dictCtx

    @app.post("/api/system/remnants/rescan")
    async def fdictRequestRescan(requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        remnantReapers.fnRequestRescan(app)
        return {"bRequested": True}


def _fnRegisterRemove(app, dictCtx):
    """Register POST /api/system/remnants/remove.

    separate-authority: the route admits nothing itself. Every effect
    goes through an authority with its own proof -- the terminal seam's
    terminate-and-prove, the keep-alive registry's start-clock-gated
    kill, or the lifecycle gateway's removal -- after this route has
    re-verified, live, that the item is still the one the scan showed.
    """

    @app.post("/api/system/remnants/remove")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRemoveRemnants(
        request: RemnantRemovalRequest, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        if not request.listItemIds:
            raise HTTPException(400, "listItemIds names nothing to remove")
        dictItemsById = {
            dictItem["sItemId"]: dictItem
            for dictItem in remnantScanner.fdictScanState(app.state)["listItems"]
        }
        listOutcomes = []
        for sItemId in request.listItemIds:
            listOutcomes.append(await _fdictRemoveOneItem(
                dictCtx, sItemId, dictItemsById.get(sItemId),
            ))
        remnantReapers.fnRequestRescan(app)
        return {"listOutcomes": listOutcomes}


async def _fdictRemoveOneItem(dictCtx, sItemId, dictItem):
    """Act on one item through its authority; answer with one sentence."""
    if dictItem is None:
        return _fdictOutcome(sItemId, False, (
            "This item is no longer in the scan: it is already gone, or "
            "the scan has moved on. Rescan to see what remains."))
    dictCarried = await asyncio.to_thread(
        fdictCarryARefusalBackInsteadOfRaising,
        lambda: _fsActOnItem(dictCtx, dictItem),
    )
    if dictCarried["errorRefused"] is not None:
        return _fdictOutcome(sItemId, False, str(dictCarried["errorRefused"].detail))
    return _fdictOutcome(sItemId, True, dictCarried["objResult"])


def _fdictOutcome(sItemId, bRemoved, sOutcome):
    """Return the one shape every removal outcome shares."""
    return {"sItemId": sItemId, "bRemoved": bRemoved, "sOutcome": sOutcome}


def _fsActOnItem(dictCtx, dictItem):
    """Dispatch one item to its authority; return the outcome sentence.

    Runs in a worker thread. A refusal is an HTTPException the caller
    carries back as the item's sentence.
    """
    sAction = dictItem["sAction"]
    if sAction == remnantScanner.S_ACTION_TERMINATE:
        return _fsTerminateSession(dictCtx, dictItem)
    if sAction == remnantScanner.S_ACTION_KILL:
        return _fsKillKeepAlive(dictItem)
    if sAction == remnantScanner.S_ACTION_STOP:
        return _fsStopSessionLane(dictItem)
    if sAction == remnantScanner.S_ACTION_REMOVE:
        return _fsRemoveStoppedContainer(dictCtx, dictItem)
    raise HTTPException(409, (
        "This item cannot be removed from here; its evidence names what "
        "to do instead."))


def _fnRefuseAlreadyGone(sWhat):
    """Refuse an item whose live identity no longer matches: not a removal."""
    raise HTTPException(
        409, f"{sWhat} is already gone, or has been replaced since the scan.")


def _fbSessionLeaderStillMatches(connectionDocker, sContainerId, iLeaderPid, iStartTicks):
    """Re-read the process table; True only for the same pid AND start clock."""
    dictTable = remnantScanner.fdictParseProcessTable(
        connectionDocker.fsReadProcessTable(sContainerId))
    return any(
        dictRow["iPid"] == iLeaderPid and dictRow["iStartTicks"] == iStartTicks
        for dictRow in dictTable["listRows"]
    )


def _fsTerminateSession(dictCtx, dictItem):
    """End an untracked session through terminate-and-prove."""
    connectionDocker = dictCtx.get("docker")
    dictIdentity = dictItem["dictIdentity"]
    if connectionDocker is None:
        raise HTTPException(409, "Docker is unreachable; start it, then rescan.")
    try:
        bMatches = _fbSessionLeaderStillMatches(
            connectionDocker, dictIdentity["sContainerId"],
            dictIdentity["iLeaderPid"], dictIdentity["iStartTicks"],
        )
    except Exception as errorAny:  # noqa: BLE001 -- a refusal, carried back
        raise HTTPException(409, (
            f"The container's processes could not be re-read: {errorAny}. "
            "Rescan, then try again."))
    if not bMatches:
        _fnRefuseAlreadyGone(f"Session {dictIdentity['iLeaderPid']}")
    dictProof = terminalContainment.fdictTerminateAndProveGroup(
        connectionDocker, dictItem["sContainerName"],
        dictIdentity["sContainerId"], dictIdentity["iLeaderPid"],
    )
    if dictProof["bProvenEmpty"]:
        return (f"Session {dictIdentity['iLeaderPid']} in container "
                f"'{dictItem['sContainerName']}' was ended and proven empty.")
    raise HTTPException(409, (
        f"Session {dictIdentity['iLeaderPid']} could not be proven empty: "
        f"{dictProof['sDetail']}. Rescan to see what survived."))


def _fsKillKeepAlive(dictItem):
    """Kill a keep-alive: through the ledger when proven, start-clock gated otherwise.

    Both exits wait for the process to leave the process table and say
    so; a caffeinate still listed when the wait ends is reported as
    signalled, never as ended.
    """
    iPid = dictItem["dictIdentity"]["iPid"]
    sStartedIso = dictItem["dictIdentity"].get("sStartedIso", "")
    if dictItem["sTier"] != remnantScanner.S_TIER_PROVEN and not (
        _fbKeepAliveStillMatches(iPid, sStartedIso)
    ):
        _fnRefuseAlreadyGone(f"caffeinate {iPid}")
    try:
        bEnded = (
            keepAliveManager.fbStopProvablyOursKeepAlive(iPid)
            if dictItem["sTier"] == remnantScanner.S_TIER_PROVEN
            else keepAliveManager.fbStopKeepAliveProcess(iPid, sStartedIso)
        )
    except ValueError:
        _fnRefuseAlreadyGone(f"caffeinate {iPid}")
    if bEnded:
        return f"caffeinate {iPid} was ended; the machine may sleep again."
    raise HTTPException(409, (
        f"caffeinate {iPid} was signalled to end but is still running; "
        "rescan to see whether it stopped."))


def _fbKeepAliveStillMatches(iPid, sStartedIso):
    """True only when a caffeinate with this pid still runs with that start.

    The start instant comes from ``ps``'s whole-second elapsed time, so
    two readings of one process differ by up to a second; the match is
    the liveness module's tolerance, never string equality.
    """
    from vaibify.config.processLiveness import fbStartClockIsConsistentWithClaim
    dictProcess = (keepAliveManager.fdictEnumerateKeepAlivesByPid() or {}).get(iPid)
    if dictProcess is None or dictProcess["datetimeStart"] is None:
        return False
    return fbStartClockIsConsistentWithClaim(dictProcess["datetimeStart"], sStartedIso)


def _fsStopSessionLane(dictItem):
    """Stop a session-lane keep-alive the registry still names the same pid for."""
    sRegistryName = dictItem["dictIdentity"]["sRegistryName"]
    dictRecord = keepAliveManager.fdictReadKeepAliveRecord(sRegistryName)
    if dictRecord.get("iPid") != dictItem["dictIdentity"]["iPid"]:
        _fnRefuseAlreadyGone(f"The keep-alive for '{sRegistryName}'")
    keepAliveManager.fnStopKeepAlive(sRegistryName)
    return f"The keep-alive for '{sRegistryName}' was ended."


def _fsRemoveStoppedContainer(dictCtx, dictItem):
    """Remove a stopped, untracked container the daemon still lists as such."""
    from vaibify.docker import containerManager
    connectionDocker = dictCtx.get("docker")
    sContainerId = dictItem["dictIdentity"]["sContainerId"]
    if connectionDocker is None:
        raise HTTPException(409, "Docker is unreachable; start it, then rescan.")
    listMatching = [
        dictContainer for dictContainer in connectionDocker.flistListAllContainers()
        if dictContainer["sContainerId"] == sContainerId
    ]
    if not listMatching:
        _fnRefuseAlreadyGone(f"Container '{dictItem['sContainerName']}'")
    if listMatching[0]["sStatus"] == "running":
        raise HTTPException(409, (
            f"Container '{dictItem['sContainerName']}' is running now; "
            "it is not removed."))
    try:
        containerManager.fnRemoveStoppedContainerById(sContainerId)
    except RuntimeError as errorRemove:
        raise HTTPException(409, str(errorRemove))
    return f"Container '{dictItem['sContainerName']}' was removed; its volumes are kept."


def fnRegisterAll(app, dictCtx):
    """Register the remnant routes."""
    _fnRegisterReadRemnants(app, dictCtx)
    _fnRegisterRescan(app, dictCtx)
    _fnRegisterRemove(app, dictCtx)
