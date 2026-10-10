"""Classify what the reapers leave behind, with evidence, for the hub.

A reaper deletes what it can PROVE is garbage. Everything else that
looks left over is listed here, each item with the evidence vaibify
has and the tier that evidence earns:

- ``proven`` -- vaibify launched it, or the item is a plain fact (a
  container created without ``--init``).
- ``possibly`` -- it may be left over, and it may be the researcher's
  own: an interactive session nobody recorded, a ``caffeinate -s`` not
  in the spawn ledger.
- ``unknown`` -- a question the scan could not answer, said so.

The scan runs as the last step of every reaper pass
(:func:`remnantReapers.fnRunReaperPass`), never on a request path:
every Docker read is bounded by the memory sampler's deadline, and the
result is cached on ``app.state.dictRemnantScan`` for routes to read
with no I/O. Removal (the routes) re-verifies each item's identity
live before acting; an item id is a digest of its category and
identity, so a stale id reports "already gone".

Sentences are worded here and rendered verbatim by the frontend.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from vaibify.config import keepAliveManager, operationJournal
from vaibify.config.connectionAvailability import fbDockerReachable
from vaibify.config.processLiveness import (
    fdatetimeParseClaimIso, flistEnumerateProcessesNamed,
)
from . import cliShellContainment, containerMemorySampler, remnantReapers
from . import sleepPrevention, terminalContainment

logger = logging.getLogger("vaibify")

__all__ = [
    "S_ACTION_KILL",
    "S_ACTION_NONE",
    "S_ACTION_REMOVE",
    "S_ACTION_STOP",
    "S_ACTION_TERMINATE",
    "S_CATEGORY_CONTAINER_WITHOUT_INIT",
    "S_CATEGORY_QUARANTINED_RECORD",
    "S_CATEGORY_UNOWNED_SESSION_LANE",
    "S_CATEGORY_UNREGISTERED_KEEP_ALIVE",
    "S_CATEGORY_UNTRACKED_SESSION",
    "S_CATEGORY_UNTRACKED_STOPPED_CONTAINER",
    "S_GLYPH_TITLE",
    "S_LIVE_LANE_LABEL",
    "S_TIER_POSSIBLY",
    "S_TIER_PROVEN",
    "S_TIER_UNKNOWN",
    "T_TEST_LANE_NAME_PREFIXES",
    "fdictCreateRemnantScanState",
    "fdictParseProcessTable",
    "fdictScanState",
    "fdictSummarizeForPoll",
    "flistClassifyContainerProcesses",
    "flistClassifyHostKeepAlives",
    "flistClassifyStoppedContainers",
    "fnRunRemnantScan",
    "fsItemId",
]

S_GLYPH_TITLE = "Leftover processes and files"

S_TIER_PROVEN = "proven"
S_TIER_POSSIBLY = "possibly"
S_TIER_UNKNOWN = "unknown"

S_ACTION_TERMINATE = "terminate"
S_ACTION_KILL = "kill"
S_ACTION_STOP = "stop"
S_ACTION_REMOVE = "remove"
S_ACTION_NONE = "none"

S_CATEGORY_UNTRACKED_SESSION = "untrackedSession"
S_CATEGORY_QUARANTINED_RECORD = "quarantinedRecord"
S_CATEGORY_UNREGISTERED_KEEP_ALIVE = "unregisteredKeepAlive"
S_CATEGORY_UNOWNED_SESSION_LANE = "unownedSessionLane"
S_CATEGORY_CONTAINER_WITHOUT_INIT = "containerWithoutInit"
S_CATEGORY_UNTRACKED_STOPPED_CONTAINER = "untrackedStoppedContainer"

# The label every live test lane stamps on the containers it creates
# (``tests/liveContainerLabels.py`` holds the same string, and a test
# pins the two equal). A container carrying it was created by vaibify's
# own suite, which is proof enough to list it for removal.
S_LIVE_LANE_LABEL = "vaibify-live-test-lane"

# Test-lane containers from before the label existed carry one of these
# name prefixes: attributable, so listed, but only as "possibly".
T_TEST_LANE_NAME_PREFIXES = (
    "vaibifyTermContain", "vaibifyDisposable", "vaibifyNetProbe",
    "vaibifyCouncilContext",
)

S_KEEP_ALIVE_COMMAND = "caffeinate"
S_RESEARCHERS_OWN_KEEP_ALIVE_COMMAND = "caffeinate -s"


def fdictCreateRemnantScanState():
    """Return the empty scan state the hub stores on ``app.state``."""
    return {
        "sScannedIso": "", "bScanning": False, "sScanError": "",
        "listItems": [],
    }


def fdictScanState(appState):
    """Return the app's scan state, creating it on first use."""
    dictScan = getattr(appState, "dictRemnantScan", None)
    if dictScan is None:
        dictScan = fdictCreateRemnantScanState()
        appState.dictRemnantScan = dictScan
    return dictScan


def fsItemId(sCategory, dictIdentity):
    """Return the digest that names one item across scans."""
    sCanonical = json.dumps(dictIdentity, sort_keys=True, default=str)
    return hashlib.sha256(
        f"{sCategory}\n{sCanonical}".encode("utf-8"),
    ).hexdigest()[:16]


def _fdictItem(
    sCategory, sTier, dictIdentity, sEvidence, sRemedy, sAction,
    sContainerName="", bConfirmRequired=False,
):
    """Return one item in the shape every category shares."""
    return {
        "sItemId": fsItemId(sCategory, dictIdentity),
        "sCategory": sCategory,
        "sTier": sTier,
        "sContainerName": sContainerName,
        "sEvidence": sEvidence,
        "sRemedy": sRemedy,
        "sAction": sAction,
        "bConfirmRequired": bConfirmRequired,
        "dictIdentity": dict(dictIdentity),
    }


# ---------------------------------------------------------------------
# The whole scan.
# ---------------------------------------------------------------------

async def fnRunRemnantScan(app, dictCtx):
    """Run one scan and store its result; a failure is a sentence, not a crash."""
    dictScan = fdictScanState(app.state)
    dictScan["bScanning"] = True
    try:
        dictScan["listItems"] = await _flistScanEverything(app, dictCtx)
        dictScan["sScanError"] = ""
    except Exception as errorAny:  # noqa: BLE001 -- reported, never raised
        logger.warning("remnant scan failed", exc_info=True)
        dictScan["sScanError"] = (
            f"The scan for leftover processes and files failed: "
            f"{type(errorAny).__name__}: {errorAny}. Check vaibify.log, "
            "then rescan."
        )
    finally:
        dictScan["bScanning"] = False
        dictScan["sScannedIso"] = datetime.now(timezone.utc).isoformat()


async def _flistScanEverything(app, dictCtx):
    """Run every classifier; the host ones in a thread, the Docker ones bounded."""
    connectionDocker = dictCtx.get("docker") if dictCtx else None
    listItems = await asyncio.to_thread(
        flistClassifyHostKeepAlives, app.state, _fdictRunningIdByName(connectionDocker),
    )
    if not fbDockerReachable(connectionDocker):
        listItems.append(_fdictItem(
            S_CATEGORY_UNTRACKED_SESSION, S_TIER_UNKNOWN, {"sScope": "daemon"},
            "Docker is unreachable, so nothing inside containers could be "
            "checked.", "Start Docker, then rescan.", S_ACTION_NONE,
        ))
        return listItems
    setRegisteredNames = await asyncio.to_thread(_fsetRegisteredContainerNames)
    listAll = await asyncio.to_thread(connectionDocker.flistListAllContainers)
    listItems.extend(flistClassifyStoppedContainers(listAll, setRegisteredNames))
    dictReadsInFlight = _fdictReadsInFlight(app.state)
    for dictContainer in listAll:
        if dictContainer["sStatus"] != "running":
            continue
        if dictContainer["sName"] not in setRegisteredNames:
            continue
        listItems.extend(await _flistScanOneRunningContainer(
            app.state, connectionDocker, dictContainer, dictReadsInFlight,
        ))
    return listItems


def _fdictReadsInFlight(appState):
    """Return the per-container in-flight read map, creating it once."""
    dictReads = getattr(appState, "dictRemnantReadsInFlight", None)
    if dictReads is None:
        dictReads = {}
        appState.dictRemnantReadsInFlight = dictReads
    return dictReads


def _fdictRunningIdByName(connectionDocker):
    """Return ``{sName: sContainerId}`` for running containers, or {}."""
    if not fbDockerReachable(connectionDocker):
        return {}
    try:
        return {
            dictRow["sName"]: dictRow["sContainerId"]
            for dictRow in connectionDocker.flistGetRunningContainers()
        }
    except Exception:  # noqa: BLE001 -- unknown, reported by the Docker leg
        return {}


def _fsetRegisteredContainerNames():
    """Return the registry's container-mode project names."""
    from vaibify.config.registryManager import flistGetAllProjects
    return {
        dictProject.get("sContainerName") or dictProject.get("sName", "")
        for dictProject in flistGetAllProjects()
        if dictProject.get("sMode", "container") != "host"
    }


async def _flistScanOneRunningContainer(
    appState, connectionDocker, dictContainer, dictReadsInFlight,
):
    """Read one container (bounded) and classify what it holds."""
    sName, sContainerId = dictContainer["sName"], dictContainer["sContainerId"]
    sTable = await containerMemorySampler.fgenericReadWithDeadline(
        dictReadsInFlight, f"remnant-processes:{sContainerId}",
        connectionDocker.fsReadProcessTable, sContainerId,
    )
    dictHostConfig = await containerMemorySampler.fgenericReadWithDeadline(
        dictReadsInFlight, f"remnant-hostconfig:{sContainerId}",
        connectionDocker.fdictReadContainerHostConfig, sContainerId,
    )
    listTtyExecIds = await containerMemorySampler.fgenericReadWithDeadline(
        dictReadsInFlight, f"remnant-execs:{sContainerId}",
        _flistRunningTtyExecIds, connectionDocker, sContainerId,
    )
    dictTable = fdictParseProcessTable(sTable) if sTable is not None else None
    return await asyncio.to_thread(
        flistClassifyContainerProcesses, appState, sName, sContainerId,
        dictTable, dictHostConfig, listTtyExecIds,
    )


def _flistRunningTtyExecIds(connectionDocker, sContainerId):
    """Return the ids of running execs the daemon says have a TTY."""
    listTty = []
    for sExecId in connectionDocker.flistRunningExecIdentifiers(sContainerId):
        dictInspect = connectionDocker.fdictInspectExec(sExecId)
        if (dictInspect.get("ProcessConfig") or {}).get("tty"):
            listTty.append(sExecId)
    return listTty


# ---------------------------------------------------------------------
# The process table.
# ---------------------------------------------------------------------

def fdictParseProcessTable(sTable):
    """Parse the typed read's marked text into rows plus its clocks.

    Returns ``{dictClock, listRows}`` where each row carries integer
    ``iPid, iParentPid, iProcessGroup, iSessionId, iTty, iStartTicks,
    iRssPages, iUid``, the one-letter ``sState`` and ``sCommand``. A
    text without the header, or with none, is a table the scan cannot
    trust and raises.
    """
    listLines = sTable.splitlines()
    if not listLines or not listLines[0].startswith("@@ clock "):
        raise ValueError("the process table carries no clock header")
    listClock = listLines[0].split()[2:]
    dictClock = {
        "iBootEpoch": int(listClock[0]), "iTicksPerSecond": int(listClock[1]),
        "iPageBytes": int(listClock[2]), "iProbePid": int(listClock[3]),
        "iProbeParentPid": int(listClock[4]),
    }
    listRows = []
    for sLine in listLines[1:]:
        listFields = sLine.split()
        if len(listFields) < 10:
            continue
        try:
            listRows.append({
                "iPid": int(listFields[0]), "iParentPid": int(listFields[1]),
                "iProcessGroup": int(listFields[2]),
                "iSessionId": int(listFields[3]), "iTty": int(listFields[4]),
                "sState": listFields[5], "iStartTicks": int(listFields[6]),
                "iRssPages": int(listFields[7]), "iUid": int(listFields[8]),
                "sCommand": listFields[9],
            })
        except ValueError:
            continue
    return {"dictClock": dictClock, "listRows": listRows}


def _fsStartIsoFromTicks(dictClock, iStartTicks):
    """Convert a /proc start time to an ISO instant, or '' without a boot time."""
    if not dictClock["iBootEpoch"] or not dictClock["iTicksPerSecond"]:
        return ""
    datetimeStart = datetime.fromtimestamp(
        dictClock["iBootEpoch"], tz=timezone.utc,
    ) + timedelta(seconds=iStartTicks / dictClock["iTicksPerSecond"])
    return datetimeStart.isoformat(timespec="seconds")


def _flistSessionLeaders(dictTable):
    """Return the exec-root session leaders: ppid 0, not pid 1, own sid, a tty."""
    dictClock = dictTable["dictClock"]
    setProbe = {dictClock["iProbePid"], dictClock["iProbeParentPid"]}
    return [
        dictRow for dictRow in dictTable["listRows"]
        if dictRow["iParentPid"] == 0 and dictRow["iPid"] != 1
        and dictRow["iSessionId"] == dictRow["iPid"] and dictRow["iTty"] != 0
        and dictRow["iPid"] not in setProbe
    ]


def _flistSessionMembers(dictTable, iSessionId):
    """Return every live row of one session (zombies excluded)."""
    return [
        dictRow for dictRow in dictTable["listRows"]
        if dictRow["iSessionId"] == iSessionId and dictRow["sState"] != "Z"
    ]


# ---------------------------------------------------------------------
# Container classifiers.
# ---------------------------------------------------------------------

def flistClassifyContainerProcesses(
    appState, sName, sContainerId, dictTable, dictHostConfig, listTtyExecIds,
):
    """Classify one running registered container's sessions and init state."""
    listItems = list(_flistQuarantinedRecordItems(sName))
    if dictHostConfig is not None and dictTable is not None:
        listItems.extend(_flistInitItems(sName, sContainerId, dictTable, dictHostConfig))
    if dictTable is None or listTtyExecIds is None:
        listItems.append(_fdictItem(
            S_CATEGORY_UNTRACKED_SESSION, S_TIER_UNKNOWN,
            {"sContainerId": sContainerId, "sScope": "unread"},
            f"The processes in container '{sName}' could not be read "
            "within the deadline.", "Rescan once the container answers.",
            S_ACTION_NONE, sContainerName=sName,
        ))
        return listItems
    listItems.extend(_flistSessionItems(
        appState, sName, sContainerId, dictTable, listTtyExecIds,
    ))
    return listItems


def _flistQuarantinedRecordItems(sName):
    """One proven item per NEEDS_RECONCILIATION journal record."""
    dictOperations = operationJournal.fdictReadJournalOutcome(sName)["dictOperations"]
    return [
        _fdictItem(
            S_CATEGORY_QUARANTINED_RECORD, S_TIER_PROVEN,
            {"sContainerName": sName, "sOperationId": sOperationId},
            f"Container '{sName}' holds a quarantined {dictRecord.get('sKind', '')} "
            f"record ({sOperationId}) that no automatic check could settle.",
            f"Run 'vaibify reconcile {sName}' to settle it.", S_ACTION_NONE,
            sContainerName=sName,
        )
        for sOperationId, dictRecord in sorted(dictOperations.items())
        if dictRecord.get("sState") == (
            operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION)
    ]


def _flistInitItems(sName, sContainerId, dictTable, dictHostConfig):
    """A proven fact: the container runs without an init process."""
    if dictHostConfig.get("Init") is True:
        return []
    iZombies = sum(1 for dictRow in dictTable["listRows"] if dictRow["sState"] == "Z")
    sZombies = (
        f"it holds {iZombies} zombie process(es) nothing will reap"
        if iZombies else "no zombie is present now"
    )
    return [_fdictItem(
        S_CATEGORY_CONTAINER_WITHOUT_INIT, S_TIER_PROVEN,
        {"sContainerId": sContainerId},
        f"Container '{sName}' was created without an init process; "
        f"{sZombies}.",
        "Use Restart Container from the environment's menu: it recreates "
        "the container with an init process after previewing what it "
        "discards.", S_ACTION_NONE, sContainerName=sName,
    )]


def _flistSessionItems(appState, sName, sContainerId, dictTable, listTtyExecIds):
    """Possibly-orphaned interactive sessions nobody recorded."""
    if terminalContainment.fbContainerDrainInProgress(sName):
        return []
    if _fbHeldByAnotherHub(appState, sName):
        return [_fdictItem(
            S_CATEGORY_UNTRACKED_SESSION, S_TIER_UNKNOWN,
            {"sContainerId": sContainerId, "sScope": "peerHub"},
            f"Container '{sName}' is held by another vaibify window, which "
            "owns whatever runs in it.", "Review it from that window.",
            S_ACTION_NONE, sContainerName=sName,
        )]
    dictKnown = _fdictKnownGroups(appState, sName, sContainerId)
    if dictKnown is None:
        return []
    listLeaders = _flistSessionLeaders(dictTable)
    listUnjournaled = [
        dictRow for dictRow in listLeaders
        if dictRow["iPid"] not in dictKnown["setJournaled"]
    ]
    iUnjournaledTtyExecs = len(set(listTtyExecIds) - dictKnown["setJournaledExecIds"])
    if iUnjournaledTtyExecs != len(listUnjournaled):
        return [_fdictItem(
            S_CATEGORY_UNTRACKED_SESSION, S_TIER_UNKNOWN,
            {"sContainerId": sContainerId, "sScope": "attribution"},
            f"Sessions in container '{sName}' could not be attributed: the "
            f"daemon reports {iUnjournaledTtyExecs} interactive exec(s) vaibify "
            f"did not open, but {len(listUnjournaled)} session leader(s) were "
            "found.", "Rescan; if it persists, run 'vaibify reconcile "
            f"{sName}'.", S_ACTION_NONE, sContainerName=sName,
        )]
    sLaneNote = _fsWorkLaneNote(sName)
    return [
        _fdictSessionItem(sName, sContainerId, dictTable, dictRow, sLaneNote)
        for dictRow in listUnjournaled
        if dictRow["iPid"] not in dictKnown["setCliShells"]
    ]


def _fbHeldByAnotherHub(appState, sName):
    """Return True when a hub other than this one holds the container's flock."""
    from vaibify.config.containerLock import fbContainerLockIsHeld
    if sName in (getattr(appState, "dictContainerOwners", {}) or {}):
        return False
    return fbContainerLockIsHeld(sName)


def _fdictKnownGroups(appState, sName, sContainerId):
    """Return the groups vaibify already accounts for, or None to skip.

    None means a record exists whose group is not yet known: the
    discovery window, in which a fresh session would read as untracked.
    """
    setJournaled, setExecIds = set(), set()
    dictOperations = operationJournal.fdictReadJournalOutcome(sName)["dictOperations"]
    for dictRecord in dictOperations.values():
        if dictRecord.get("sKind") != terminalContainment.S_TERMINAL_OPERATION_KIND:
            continue
        if not dictRecord.get("iHolderProcessGroup"):
            return None
        setJournaled.add(int(dictRecord["iHolderProcessGroup"]))
        if dictRecord.get("sDockerExecId"):
            setExecIds.add(dictRecord["sDockerExecId"])
    dictRegistry = getattr(appState, "dictTerminalExecutionRecords", None) or {}
    for recordTerminal in (dictRegistry.get(sName) or {}).values():
        if not recordTerminal.iProcessGroup:
            return None
        setJournaled.add(recordTerminal.iProcessGroup)
        setExecIds.add(recordTerminal.sDockerExecId)
    setCliShells = {
        dictRecord["iSessionId"]
        for dictRecord in cliShellContainment.flistCliShellRecords()
        if dictRecord["sContainerId"] == sContainerId
    }
    return {"setJournaled": setJournaled, "setJournaledExecIds": setExecIds,
            "setCliShells": setCliShells}


def _fsWorkLaneNote(sName):
    """Return the sentence for a container whose sessions hold a keep-alive."""
    if keepAliveManager.fbKeepAliveIsLive(sleepPrevention.fsWorkLaneKeepAliveName(sName)):
        return (" While it runs, vaibify keeps this machine awake for it "
                "(the work-lane keep-alive).")
    return ""


def _fdictSessionItem(sName, sContainerId, dictTable, dictLeader, sLaneNote):
    """One possibly-orphaned session, with what is known about it."""
    from vaibify.docker.imageBuilder import DICT_AGENT_COMMANDS
    listMembers = _flistSessionMembers(dictTable, dictLeader["iPid"])
    setCommands = {dictRow["sCommand"] for dictRow in listMembers}
    listAgents = sorted(setCommands & set(DICT_AGENT_COMMANDS.values()))
    fMegabytes = sum(dictRow["iRssPages"] for dictRow in listMembers) * (
        dictTable["dictClock"]["iPageBytes"]) / (1024 * 1024)
    sStartedIso = _fsStartIsoFromTicks(dictTable["dictClock"], dictLeader["iStartTicks"])
    sAgents = (
        f" It is running an AI agent ({', '.join(listAgents)}); ending it "
        "ends that agent's work." if listAgents else ""
    )
    return _fdictItem(
        S_CATEGORY_UNTRACKED_SESSION, S_TIER_POSSIBLY,
        {"sContainerId": sContainerId, "iLeaderPid": dictLeader["iPid"],
         "iStartTicks": dictLeader["iStartTicks"]},
        f"Interactive session {dictLeader['iPid']} ({dictLeader['sCommand']}) "
        f"in container '{sName}', started {sStartedIso or 'at an unknown time'}, "
        f"{len(listMembers)} process(es), {fMegabytes:.0f} MB, "
        f"user {dictLeader['iUid']}. vaibify did not open it: it may be your "
        f"own docker exec, or an editor's.{sAgents}{sLaneNote}",
        "Select it and choose Remove selected to end every process in "
        "the session, with proof.", S_ACTION_TERMINATE,
        sContainerName=sName, bConfirmRequired=bool(listAgents),
    )


# ---------------------------------------------------------------------
# Host classifiers.
# ---------------------------------------------------------------------

def flistClassifyHostKeepAlives(appState, dictRunningIdByName):
    """Keep-alives no registry holds, and session lanes no hub owns."""
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return []
    listItems = []
    dictRegistered = _fdictRegistryPids()
    listFound = flistEnumerateProcessesNamed(S_KEEP_ALIVE_COMMAND)
    if listFound is None:
        listItems.append(_fdictItem(
            S_CATEGORY_UNREGISTERED_KEEP_ALIVE, S_TIER_UNKNOWN, {"sScope": "ps"},
            "The host's processes could not be listed, so keep-alives were "
            "not checked.", "Rescan.", S_ACTION_NONE,
        ))
        listFound = []
    dictLedger = keepAliveManager.fdictReadSpawnLedger()
    for dictProcess in listFound:
        if dictProcess["iPid"] in dictRegistered:
            continue
        dictItem = _fdictKeepAliveItem(dictProcess, dictLedger)
        if dictItem is not None:
            listItems.append(dictItem)
    listItems.extend(_flistUnownedSessionLaneItems(
        appState, dictRegistered, dictRunningIdByName,
    ))
    return listItems


def _fdictRegistryPids():
    """Return ``{iPid: sRegistryName}`` for every keep-alive a registry holds."""
    dictPids = {}
    for sRegistryName in keepAliveManager.flistKeepAliveNames():
        iPid = keepAliveManager.fdictReadKeepAliveRecord(sRegistryName).get("iPid")
        if iPid:
            dictPids[int(iPid)] = sRegistryName
    return dictPids


def _fdictKeepAliveItem(dictProcess, dictLedger):
    """Classify one caffeinate no registry holds, or None to ignore it."""
    iPid = dictProcess["iPid"]
    sStartedIso = (
        dictProcess["datetimeStart"].isoformat(timespec="seconds")
        if dictProcess["datetimeStart"] is not None else ""
    )
    if iPid in dictLedger and keepAliveManager.fbCaffeinateIsProvablyOurs(iPid):
        return _fdictItem(
            S_CATEGORY_UNREGISTERED_KEEP_ALIVE, S_TIER_PROVEN,
            {"iPid": iPid, "sStartedIso": dictLedger[iPid]["sStartedIso"]},
            f"caffeinate {iPid} was launched by vaibify for "
            f"'{dictLedger[iPid]['sName']}' at {dictLedger[iPid]['sStartedIso']} "
            "and no registry holds it any more; it keeps this machine awake "
            "for nothing.", "Remove it to let the machine sleep.", S_ACTION_KILL,
        )
    if dictProcess["sCommand"] == S_RESEARCHERS_OWN_KEEP_ALIVE_COMMAND and (
        dictProcess["iParentPid"] == 1
    ):
        return _fdictItem(
            S_CATEGORY_UNREGISTERED_KEEP_ALIVE, S_TIER_POSSIBLY,
            {"iPid": iPid, "sStartedIso": sStartedIso},
            f"caffeinate {iPid} (caffeinate -s, started "
            f"{sStartedIso or 'at an unknown time'}) is not in vaibify's spawn "
            "ledger: it may be one an earlier vaibify launched, or one you "
            "started yourself, as the Docker status hint suggests.",
            "Remove it only if you did not start it yourself.", S_ACTION_KILL,
            bConfirmRequired=True,
        )
    return None


def _flistUnownedSessionLaneItems(appState, dictRegistered, dictRunningIdByName):
    """Session-lane keep-alives of running containers no hub owns, after one interval."""
    dictOwners = getattr(appState, "dictContainerOwners", {}) or {}
    datetimeCutoff = datetime.now(timezone.utc) - timedelta(
        seconds=remnantReapers.F_REAPER_PASS_INTERVAL_SECONDS)
    listItems = []
    for iPid, sRegistryName in sorted(dictRegistered.items()):
        if sleepPrevention.S_WORK_LANE_SEPARATOR in sRegistryName:
            continue
        if sRegistryName in dictOwners or sRegistryName not in dictRunningIdByName:
            continue
        dictRecord = keepAliveManager.fdictReadKeepAliveRecord(sRegistryName)
        datetimeStarted = fdatetimeParseClaimIso(dictRecord.get("sStartedIso", ""))
        if datetimeStarted is None or datetimeStarted > datetimeCutoff:
            continue
        listItems.append(_fdictItem(
            S_CATEGORY_UNOWNED_SESSION_LANE, S_TIER_POSSIBLY,
            {"sRegistryName": sRegistryName, "iPid": iPid},
            f"The keep-alive for '{sRegistryName}' (caffeinate {iPid}) is held "
            "although no vaibify window holds that container; the container is "
            "running, so the next claim of it adopts the keep-alive.",
            "Remove it if nobody is going to claim the container.", S_ACTION_STOP,
            sContainerName=sRegistryName,
        ))
    return listItems


def flistClassifyStoppedContainers(listAll, setRegisteredNames):
    """Stopped containers vaibify created but no longer tracks."""
    listItems = []
    for dictContainer in listAll:
        if dictContainer["sStatus"] == "running" or dictContainer["sName"] in setRegisteredNames:
            continue
        if S_LIVE_LANE_LABEL in dictContainer["dictLabels"]:
            sTier, sWhy = S_TIER_PROVEN, "it carries vaibify's test-lane label"
        elif dictContainer["sName"].startswith(T_TEST_LANE_NAME_PREFIXES):
            sTier, sWhy = S_TIER_POSSIBLY, "its name is one vaibify's test lanes use"
        else:
            continue
        listItems.append(_fdictItem(
            S_CATEGORY_UNTRACKED_STOPPED_CONTAINER, sTier,
            {"sContainerId": dictContainer["sContainerId"]},
            f"Stopped container '{dictContainer['sName']}' is not in the "
            f"registry, and {sWhy}.",
            "Remove it; its volumes are kept.", S_ACTION_REMOVE,
            sContainerName=dictContainer["sName"],
        ))
    return listItems


# ---------------------------------------------------------------------
# The poll summary: no I/O.
# ---------------------------------------------------------------------

def fdictSummarizeForPoll(appState):
    """Return the glyph's summary from state alone."""
    dictScan = fdictScanState(appState)
    listItems = dictScan["listItems"]
    dictHealth = getattr(appState, "dictReaperHealth", None) or {}
    bReaperFailed = any(
        dictRecord.get("sOutcome") == remnantReapers.S_OUTCOME_FAILED
        for dictRecord in dictHealth.values()
    )
    return {
        "iCount": len(listItems),
        "iProvenCount": sum(1 for dictItem in listItems if dictItem["sTier"] == S_TIER_PROVEN),
        "sScannedIso": dictScan["sScannedIso"],
        "sGlyphTitle": _fsGlyphTitle(len(listItems), bReaperFailed, dictScan["sScanError"]),
        "bReaperFailed": bReaperFailed,
        "bScanning": dictScan["bScanning"],
    }


def _fsGlyphTitle(iCount, bReaperFailed, sScanError):
    """Word the glyph's tooltip from the counts, in one place."""
    if bReaperFailed:
        return "A cleanup could not run; click to review."
    if sScanError:
        return "The scan for leftover processes and files failed; click to review."
    return f"{iCount} leftover processes or files; click to review."
