"""Watch each owned project container's memory, and remember its kills.

A process in a container that reaches its memory limit is killed by the
kernel with SIGKILL. The victim cannot report it, and before this module
existed nothing else did: the counters that record it were read only for
disposable and council containers, and a researcher whose AI agent was
killed saw ``Killed`` in a terminal and nothing on the dashboard.

This module is the record and what it says. The Docker side -- the
sampler that fills it, and the evidence read from a stopped container
before its removal -- is ``containerMemorySampler``, which changes for
different reasons (deadlines, threads, the daemon's answers) and
depends on this module, never the reverse.

The record keeps two things apart. ``dictCurrent`` is the latest
measurement, and it can become unknown at any time. ``listIncidents``
is history: a kill once recorded is never removed by a later failure to
read, and a container recreated under the same name starts new counters
without erasing the old container's incidents, each of which carries
the id of the container it happened in.

Detection is best-effort, and every sentence is worded to match:

* Memory can reach the limit between two samples, so no advance warning
  is promised.
* The kill counter and the limit-event counter are aggregate evidence.
  A rise in both within one window may be two separate events, so no
  sentence says one caused the other.
* The victim is never named. Naming it needs the kernel log of the
  Docker VM, which only a privileged container could read.
* History is held in memory, so a hub restart forgets incidents of
  containers since removed, and a running container's earlier kills
  are reported again as kills vaibify did not observe the time of.

Staleness is judged when the record is READ, never on a timer: a
sampler that has stopped cannot clear its own flag.

OOM incidents are logged WITHOUT the ``sContainerId`` extra. That extra
routes a record into the host-incident ring, whose latest entry the
stale-heartbeat reconcile copies into a run's failure cause with no time
correlation; a kill logged that way would be reported as a dead
runner's cause.
"""

import logging
import threading
from datetime import datetime, timezone

from vaibify.docker import cgroupMemory

logger = logging.getLogger("vaibify")

__all__ = [
    "F_MEMORY_SAMPLE_INTERVAL_SECONDS",
    "F_MEMORY_READ_TIMEOUT_SECONDS",
    "F_MEMORY_STALE_AFTER_SECONDS",
    "F_NEAR_ENTER_FRACTION",
    "F_NEAR_LEAVE_FRACTION",
    "S_STATE_MEASURED",
    "S_STATE_TIMEOUT",
    "S_STATE_UNREADABLE",
    "S_STATE_NOT_RUNNING",
    "S_STATE_GONE",
    "S_STATE_NO_SAMPLE_YET",
    "S_LEVEL_OK",
    "S_LEVEL_NEAR",
    "S_LEVEL_UNKNOWN",
    "S_LEVEL_NOT_APPLICABLE",
    "S_INCIDENT_OOM_KILL",
    "S_INCIDENT_BEFORE_OBSERVATION",
    "S_INCIDENT_EXITED_OOM_KILLED",
    "S_REMEDY_OPEN_SETTINGS",
    "S_REMEDY_GIVE_DOCKER_MEMORY",
    "fdictCreateMemoryStore",
    "fnRecordMeasurement",
    "fnRecordFailure",
    "fnRecordExitedOomKill",
    "fdictDescribeMemory",
    "fdictDescribeMemoryForContainerId",
    "fsDescribeKillCount",
    "fsDescribeMemoryIncident",
    "fsFormatBytes",
    "fsNormalizeDockerTime",
    "fsDescribeExitedOomEvidence",
    "fnRecordExitedEvidenceForApp",
    "fsOwnerNameForContainerId",
]


F_MEMORY_SAMPLE_INTERVAL_SECONDS = 15.0
F_MEMORY_READ_TIMEOUT_SECONDS = 5.0
# Three sample intervals: one late sample is a slow daemon, three is a
# sampler that has stopped answering for this container.
F_MEMORY_STALE_AFTER_SECONDS = 3 * F_MEMORY_SAMPLE_INTERVAL_SECONDS
# Hysteresis: "near" is entered at 85% of a finite limit and left only
# below 80%, so a working set hovering at the threshold does not flap.
F_NEAR_ENTER_FRACTION = 0.85
F_NEAR_LEAVE_FRACTION = 0.80

S_STATE_MEASURED = "measured"
S_STATE_TIMEOUT = "timeout"
S_STATE_UNREADABLE = "unreadable"
S_STATE_NOT_RUNNING = "notRunning"
S_STATE_GONE = "gone"
S_STATE_NO_SAMPLE_YET = "noSampleYet"

S_LEVEL_OK = "ok"
S_LEVEL_NEAR = "near"
S_LEVEL_UNKNOWN = "unknown"
S_LEVEL_NOT_APPLICABLE = "notApplicable"

S_INCIDENT_OOM_KILL = "oomKill"
S_INCIDENT_BEFORE_OBSERVATION = "beforeObservation"
S_INCIDENT_EXITED_OOM_KILLED = "exitedOomKilled"

S_ENDING_DEPENDENTS = (
    "Other processes may have depended on the one killed; check that "
    "running work is still healthy.")
S_ENDING_RESUME = (
    "If an AI agent was running in the terminal, its conversation can "
    "usually be resumed (Claude Code: claude --resume).")
S_REMEDY_OPEN_SETTINGS = "To raise the limit, open Settings."
# A container with no limit of its own was killed because the memory
# Docker itself has ran out, which Settings cannot raise.
S_REMEDY_GIVE_DOCKER_MEMORY = (
    "To give it more memory, give Docker more memory in Docker's own "
    "settings.")

_I_BYTES_PER_GIGABYTE = 2 ** 30
_I_BYTES_PER_MEGABYTE = 2 ** 20
_S_DOCKER_ZERO_TIME_PREFIX = "0001-01-01"

_lockMemoryStore = threading.Lock()


# ---------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------

def fdictCreateMemoryStore():
    """Return an empty store: container name -> memory record."""
    return {}


def _fdictRecordFor(dictStore, sName, sContainerId):
    """Return the name's record, reset to this container id if it changed.

    A new id under the same name is a different container: its kernel
    counters start again from zero, so the old baseline and the old
    measurement go. Its incidents stay, each labeled with its own id.
    """
    dictRecord = dictStore.setdefault(sName, {
        "sContainerId": "", "dictCurrent": None, "dictBaseline": None,
        "listIncidents": [],
    })
    if sContainerId and dictRecord["sContainerId"] != sContainerId:
        dictRecord["sContainerId"] = sContainerId
        dictRecord["dictCurrent"] = None
        dictRecord["dictBaseline"] = None
    return dictRecord


def fnRecordMeasurement(
    dictStore, sName, sContainerId, dictParsed, dictState, datetimeNow,
):
    """Fold one successful cgroup read into the name's record."""
    with _lockMemoryStore:
        dictRecord = _fdictRecordFor(dictStore, sName, sContainerId)
        dictPrevious = dictRecord["dictCurrent"] or {}
        _fnFoldKillCounters(
            dictRecord, sName, sContainerId, dictParsed, dictState, datetimeNow)
        dictRecord["dictCurrent"] = _fdictBuildMeasuredCurrent(
            sContainerId, dictParsed, dictState, datetimeNow, dictPrevious)


def _fnFoldKillCounters(
    dictRecord, sName, sContainerId, dictParsed, dictState, datetimeNow,
):
    """Compare the kill counter with the baseline; record what rose."""
    iKills = dictParsed.get("iOomKillCount")
    if iKills is None:
        return
    dictBaseline = dictRecord["dictBaseline"]
    sNowIso = datetimeNow.isoformat()
    if dictBaseline is None and iKills > 0:
        _fnAppendIncident(dictRecord, sName, _fdictBeforeObservation(
            sContainerId, dictParsed, dictState, sNowIso))
    elif dictBaseline is not None and iKills > dictBaseline["iOomKillCount"]:
        _fnAppendIncident(dictRecord, sName, _fdictOomKillIncident(
            sContainerId, dictBaseline, dictParsed, sNowIso))
    dictRecord["dictBaseline"] = {
        "sContainerId": sContainerId, "iOomKillCount": iKills,
        "iOomCount": dictParsed.get("iOomCount"), "sSampledIso": sNowIso,
    }


def _fdictOomKillIncident(sContainerId, dictBaseline, dictParsed, sNowIso):
    """Return the incident for a kill-counter rise between two samples."""
    iKills = dictParsed["iOomKillCount"]
    iOomBefore = dictBaseline.get("iOomCount")
    iOomAfter = dictParsed.get("iOomCount")
    return {
        "sIncidentId": f"{S_INCIDENT_OOM_KILL}:{sContainerId}:{iKills}",
        "sContainerId": sContainerId, "sKind": S_INCIDENT_OOM_KILL,
        "iKillCount": iKills - dictBaseline["iOomKillCount"],
        "sWindowStartIso": dictBaseline["sSampledIso"],
        "sWindowEndIso": sNowIso,
        "bLimitEventInWindow": (
            iOomBefore is not None and iOomAfter is not None
            and iOomAfter > iOomBefore),
        "sLimitKind": dictParsed.get("sLimitKind"),
        "iLimitBytes": dictParsed.get("iLimitBytes"),
    }


def _fdictBeforeObservation(sContainerId, dictParsed, dictState, sNowIso):
    """Return the incident for kills counted before vaibify first looked."""
    iKills = dictParsed["iOomKillCount"]
    return {
        "sIncidentId": (
            f"{S_INCIDENT_BEFORE_OBSERVATION}:{sContainerId}:{iKills}"),
        "sContainerId": sContainerId,
        "sKind": S_INCIDENT_BEFORE_OBSERVATION, "iKillCount": iKills,
        "sWindowStartIso": fsNormalizeDockerTime(
            (dictState or {}).get("StartedAt", "")),
        "sWindowEndIso": sNowIso, "bLimitEventInWindow": False,
        "sLimitKind": dictParsed.get("sLimitKind"),
        "iLimitBytes": dictParsed.get("iLimitBytes"),
    }


def fnRecordExitedOomKill(dictStore, sName, dictEvidence):
    """Record that Docker says a stopped container's main process was OOM-killed."""
    sContainerId = dictEvidence.get("sContainerId", "")
    sFinishedIso = dictEvidence.get("sFinishedIso", "")
    dictIncident = {
        "sIncidentId": f"{S_INCIDENT_EXITED_OOM_KILLED}:{sContainerId}",
        "sContainerId": sContainerId,
        "sKind": S_INCIDENT_EXITED_OOM_KILLED, "iKillCount": 1,
        "sWindowStartIso": sFinishedIso, "sWindowEndIso": sFinishedIso,
        "bLimitEventInWindow": False,
        "sLimitKind": cgroupMemory.S_LIMIT_UNKNOWN, "iLimitBytes": None,
    }
    with _lockMemoryStore:
        dictRecord = _fdictRecordFor(dictStore, sName, "")
        _fnAppendIncident(dictRecord, sName, dictIncident)


def _fnAppendIncident(dictRecord, sName, dictIncident):
    """Append an incident once, and log it outside the host-incident ring."""
    setKnownIds = {d["sIncidentId"] for d in dictRecord["listIncidents"]}
    if dictIncident["sIncidentId"] in setKnownIds:
        return
    dictRecord["listIncidents"].append(dictIncident)
    logger.warning(
        "Out-of-memory kill in container %s (id %s): %s",
        sName, dictIncident["sContainerId"][:12],
        fsDescribeMemoryIncident(dictIncident, datetime.now(timezone.utc)),
    )


def fnRecordFailure(dictStore, sName, sContainerId, sState, sReason, datetimeNow):
    """Replace the current measurement with an unknown; keep the history."""
    with _lockMemoryStore:
        dictRecord = _fdictRecordFor(dictStore, sName, sContainerId)
        dictPrevious = dictRecord["dictCurrent"] or {}
        if dictPrevious.get("sState") != sState:
            logger.warning(
                "Memory of container %s could not be checked: %s",
                sName, _fsReasonForState(sState, sReason))
        dictCurrent = cgroupMemory.fdictParseCgroupMemory("")
        dictCurrent.update({
            "sContainerId": sContainerId or dictRecord["sContainerId"],
            "sSampledIso": datetimeNow.isoformat(), "sState": sState,
            "sReason": sReason, "bDockerReportsOomKilled": None,
            "bNear": bool(dictPrevious.get("bNear")),
            "sNearSinceIso": dictPrevious.get("sNearSinceIso", ""),
        })
        dictRecord["dictCurrent"] = dictCurrent


def _fdictBuildMeasuredCurrent(
    sContainerId, dictParsed, dictState, datetimeNow, dictPrevious,
):
    """Return the current-measurement dict for one successful read."""
    bWasNear = bool(dictPrevious.get("bNear"))
    bNear = _fbComputeNear(dictParsed, bWasNear)
    sNearSinceIso = ""
    if bNear:
        sNearSinceIso = dictPrevious.get("sNearSinceIso") if bWasNear else ""
        sNearSinceIso = sNearSinceIso or datetimeNow.isoformat()
    dictCurrent = dict(dictParsed)
    dictCurrent.update({
        "sContainerId": sContainerId, "sSampledIso": datetimeNow.isoformat(),
        "sState": S_STATE_MEASURED, "sReason": "",
        "bDockerReportsOomKilled": bool((dictState or {}).get("OOMKilled")),
        "bNear": bNear, "sNearSinceIso": sNearSinceIso,
    })
    return dictCurrent


def _fbComputeNear(dictParsed, bWasNear):
    """Return whether the working set is near a finite limit, with hysteresis."""
    iLimit = dictParsed.get("iLimitBytes")
    iWorkingSet = dictParsed.get("iWorkingSetBytes")
    if dictParsed.get("sLimitKind") != cgroupMemory.S_LIMIT_FINITE:
        return False
    if not iLimit or iWorkingSet is None:
        return bWasNear
    fThreshold = F_NEAR_LEAVE_FRACTION if bWasNear else F_NEAR_ENTER_FRACTION
    return iWorkingSet >= fThreshold * iLimit


# ---------------------------------------------------------------------
# Reading the record: level, sentences, chip
# ---------------------------------------------------------------------

def fdictDescribeMemory(dictStore, sName, datetimeNow, bHostProject=False):
    """Return the route's answer for a name, judged as of ``datetimeNow``."""
    if bHostProject:
        return _fdictDescribeHostProject(sName)
    with _lockMemoryStore:
        dictRecord = dictStore.get(sName) or {}
        dictCurrent = dict(dictRecord.get("dictCurrent") or {})
        listIncidents = [dict(d) for d in dictRecord.get("listIncidents", [])]
    dictCurrent = _fdictDescribeCurrent(dictCurrent, datetimeNow)
    for dictIncident in listIncidents:
        dictIncident["sSentence"] = fsDescribeMemoryIncident(
            dictIncident, datetimeNow)
    iKillCount = sum(d["iKillCount"] for d in listIncidents)
    dictCurrent["sChipText"] = _fsChipText(dictCurrent, iKillCount)
    return {
        "sContainerName": sName, "dictCurrent": dictCurrent,
        "listIncidents": listIncidents, "iKillCount": iKillCount,
    }


def _fdictDescribeHostProject(sName):
    """Return the answer for a host project, which has no container."""
    return {
        "sContainerName": sName, "listIncidents": [], "iKillCount": 0,
        "dictCurrent": {
            "sState": "", "sLevel": S_LEVEL_NOT_APPLICABLE, "sChipText": "",
            "sSentence": (
                "This project runs on the host, outside any container, "
                "so vaibify does not watch its memory."),
        },
    }


def _fdictDescribeCurrent(dictCurrent, datetimeNow):
    """Attach the level, the sentence and the sample age to a measurement."""
    if not dictCurrent:
        dictCurrent = cgroupMemory.fdictParseCgroupMemory("")
        dictCurrent.update({
            "sState": S_STATE_NO_SAMPLE_YET, "sReason": "",
            "sSampledIso": "", "bNear": False, "sNearSinceIso": "",
        })
    fAge = _ffAgeSeconds(dictCurrent.get("sSampledIso"), datetimeNow)
    dictCurrent["fSampleAgeSeconds"] = fAge
    sLevel, sSentence = _ftLevelAndSentence(dictCurrent, fAge)
    dictCurrent["sLevel"] = sLevel
    dictCurrent["sSentence"] = sSentence
    return dictCurrent


def _ftLevelAndSentence(dictCurrent, fAge):
    """Return ``(sLevel, sSentence)`` for a current measurement."""
    sState = dictCurrent["sState"]
    if sState != S_STATE_MEASURED:
        return S_LEVEL_UNKNOWN, _fsUnknownSentence(
            _fsReasonForState(sState, dictCurrent.get("sReason", "")))
    if fAge is None or fAge > F_MEMORY_STALE_AFTER_SECONDS:
        return S_LEVEL_UNKNOWN, _fsUnknownSentence(
            f"the last measurement is {int(fAge or 0)} seconds old")
    iWorkingSet = dictCurrent.get("iWorkingSetBytes")
    if dictCurrent.get("sLimitKind") == cgroupMemory.S_LIMIT_UNLIMITED:
        return S_LEVEL_OK, _fsUnlimitedSentence(iWorkingSet)
    if (dictCurrent.get("sLimitKind") != cgroupMemory.S_LIMIT_FINITE
            or iWorkingSet is None):
        return S_LEVEL_UNKNOWN, _fsUnknownSentence(
            "the container's memory figures were incomplete")
    if dictCurrent.get("bNear"):
        return S_LEVEL_NEAR, _fsNearSentence(dictCurrent)
    return S_LEVEL_OK, _fsInUseSentence(dictCurrent)


def _fsReasonForState(sState, sReason):
    """Return the clause naming why a measurement is unknown."""
    dictReasons = {
        S_STATE_TIMEOUT: (
            "the container did not answer within "
            f"{int(F_MEMORY_READ_TIMEOUT_SECONDS)} seconds"),
        S_STATE_NOT_RUNNING: "the container is not running",
        S_STATE_GONE: "Docker no longer has this container",
        S_STATE_NO_SAMPLE_YET: (
            "it has not been measured yet; the first measurement is "
            f"taken within {int(F_MEMORY_SAMPLE_INTERVAL_SECONDS)} seconds"),
    }
    return dictReasons.get(sState) or sReason or "the reason is unknown"


def _fsUnknownSentence(sReason):
    return f"Memory could not be checked: {sReason}."


def _fsUnlimitedSentence(iWorkingSet):
    if iWorkingSet is None:
        return "No memory limit."
    return (
        f"No memory limit. About {fsFormatBytes(iWorkingSet)} is in use "
        "(an estimate of the working set).")


def _fsInUseSentence(dictCurrent):
    return (
        f"About {fsFormatBytes(dictCurrent['iWorkingSetBytes'])} of this "
        f"container's {fsFormatBytes(dictCurrent['iLimitBytes'])} memory "
        "limit is in use (an estimate of the working set).")


def _fsNearSentence(dictCurrent):
    return (
        "Memory in use is near this container's limit: about "
        f"{fsFormatBytes(dictCurrent['iWorkingSetBytes'])} of "
        f"{fsFormatBytes(dictCurrent['iLimitBytes'])} (an estimate). At "
        "the limit the kernel kills a process in the container. "
        f"{S_REMEDY_OPEN_SETTINGS}")


def _fsChipText(dictCurrent, iKillCount):
    """Return the header chip's text: the estimate, the limit, the kills."""
    sKills = ""
    if iKillCount:
        sKills = f" · {_fsCountProcesses(iKillCount)} killed"
    sLevel = dictCurrent.get("sLevel")
    if sLevel == S_LEVEL_NOT_APPLICABLE:
        return ""
    if sLevel == S_LEVEL_UNKNOWN:
        return "Memory unknown" + sKills
    iWorkingSet = dictCurrent.get("iWorkingSetBytes")
    if dictCurrent.get("sLimitKind") == cgroupMemory.S_LIMIT_UNLIMITED:
        sUse = "" if iWorkingSet is None else (
            f" ~{fsFormatBytes(iWorkingSet)}")
        return f"Memory{sUse} · no limit" + sKills
    sUsed = fsFormatBytes(iWorkingSet).split(" ")[0]
    return (
        f"Memory ~{sUsed} / {fsFormatBytes(dictCurrent['iLimitBytes'])}"
        + sKills)


def fdictDescribeMemoryForContainerId(appState, sContainerId, datetimeNow):
    """Return the route's answer for the Docker id a URL carries.

    The record is keyed by container NAME, as the owner map is; the id
    is resolved through the owner record that holds it -- the same
    resolution the route's authorization made -- so the answer never
    costs a daemon call.
    """
    from vaibify.config.registryManager import fbIsHostProject
    sName = fsOwnerNameForContainerId(
        getattr(appState, "dictContainerOwners", {}) or {}, sContainerId)
    sKey = sName or sContainerId
    return fdictDescribeMemory(
        getattr(appState, "dictContainerMemory", {}), sKey, datetimeNow,
        bHostProject=fbIsHostProject(sKey))


def fsDescribeKillCount(iKillCount):
    """Return the Resource Monitor's kill-count text, or "" for none."""
    if not iKillCount:
        return ""
    return f"{_fsCountProcesses(iKillCount)} killed for lack of memory"


def fsFormatBytes(iBytes):
    """Return a byte count in the units the limit fields use (GB = 2**30)."""
    if iBytes < _I_BYTES_PER_GIGABYTE:
        return f"{round(iBytes / _I_BYTES_PER_MEGABYTE)} MB"
    fGigabytes = iBytes / _I_BYTES_PER_GIGABYTE
    return f"{fGigabytes:.1f}".rstrip("0").rstrip(".") + " GB"


def _ffAgeSeconds(sSampledIso, datetimeNow):
    """Return seconds since a sample, or None when it has no time."""
    datetimeSampled = _fdatetimeParseIso(sSampledIso)
    if datetimeSampled is None:
        return None
    return max(0.0, (datetimeNow - datetimeSampled).total_seconds())


# ---------------------------------------------------------------------
# The incident sentences: one function, server-side
# ---------------------------------------------------------------------

def fsDescribeMemoryIncident(dictIncident, datetimeNow):
    """Return the sentence the dashboard shows for one incident.

    Counters are aggregate evidence, not event correlation: no sentence
    claims that two counter changes describe one event, a limit is
    mentioned only when the limit-event counter rose in the same window,
    and nothing claims the limit was not involved.
    """
    sKind = dictIncident.get("sKind")
    if sKind == S_INCIDENT_BEFORE_OBSERVATION:
        sOpening = _fsBeforeObservationOpening(dictIncident, datetimeNow)
    elif sKind == S_INCIDENT_EXITED_OOM_KILLED:
        sOpening = _fsExitedOpening(dictIncident, datetimeNow)
    else:
        sOpening = _fsOomKillOpening(dictIncident, datetimeNow)
    return " ".join([
        sOpening, S_ENDING_DEPENDENTS, S_ENDING_RESUME,
        _fsRemedyFor(dictIncident)])


def _fsOomKillOpening(dictIncident, datetimeNow):
    sStart = _fsFormatClock(dictIncident.get("sWindowStartIso"), datetimeNow)
    sEnd = _fsFormatClock(dictIncident.get("sWindowEndIso"), datetimeNow)
    sOpening = (
        f"Between {sStart} and {sEnd} the kernel's out-of-memory killer "
        f"killed {_fsCountProcesses(dictIncident['iKillCount'])} in this "
        "container.")
    return sOpening + " " + _fsLimitClause(dictIncident)


def _fsLimitClause(dictIncident):
    """Return what the counters say about the container's own limit."""
    iLimit = dictIncident.get("iLimitBytes")
    if dictIncident.get("bLimitEventInWindow"):
        sLimit = f"its {fsFormatBytes(iLimit)} memory limit" if iLimit else (
            "its memory limit")
        return (
            f"The container also recorded reaching {sLimit} during that "
            "interval.")
    if dictIncident.get("sLimitKind") == cgroupMemory.S_LIMIT_UNLIMITED:
        return "This container has no memory limit of its own."
    return (
        "Whether this container's own limit was involved could not be "
        "established.")


def _fsBeforeObservationOpening(dictIncident, datetimeNow):
    sStarted = _fsFormatClock(dictIncident.get("sWindowStartIso"), datetimeNow)
    sSince = f"since it started at {sStarted}" if sStarted else (
        "since it started")
    return (
        "The kernel's out-of-memory killer has killed "
        f"{_fsCountProcesses(dictIncident['iKillCount'])} in this container "
        f"{sSince}; vaibify did not observe when.")


def _fsExitedOpening(dictIncident, datetimeNow):
    sFinished = _fsFormatClock(dictIncident.get("sWindowEndIso"), datetimeNow)
    sAt = f" at {sFinished}" if sFinished else ""
    return (
        "Docker reports that the previous container's main process (id "
        f"{dictIncident.get('sContainerId', '')[:12]}) was killed for lack "
        f"of memory{sAt}.")


def _fsRemedyFor(dictIncident):
    if dictIncident.get("sLimitKind") == cgroupMemory.S_LIMIT_UNLIMITED:
        return S_REMEDY_GIVE_DOCKER_MEMORY
    return S_REMEDY_OPEN_SETTINGS


def _fsCountProcesses(iCount):
    return f"{iCount} process" if iCount == 1 else f"{iCount} processes"


def _fsFormatClock(sIso, datetimeNow):
    """Return a local clock time, with the date when it is not today."""
    datetimeWhen = _fdatetimeParseIso(sIso)
    if datetimeWhen is None:
        return ""
    datetimeLocal = datetimeWhen.astimezone()
    sClock = datetimeLocal.strftime("%I:%M:%S %p").lstrip("0")
    if datetimeLocal.date() == datetimeNow.astimezone().date():
        return sClock
    return datetimeLocal.strftime("%b ") + str(datetimeLocal.day) + ", " + sClock


def _fdatetimeParseIso(sIso):
    """Return an aware datetime for an ISO string, or None."""
    if not sIso:
        return None
    try:
        datetimeParsed = datetime.fromisoformat(sIso)
    except ValueError:
        return None
    if datetimeParsed.tzinfo is None:
        return datetimeParsed.replace(tzinfo=timezone.utc)
    return datetimeParsed


def fsNormalizeDockerTime(sDockerTime):
    """Return Docker's RFC 3339 nanosecond time as a Python ISO string.

    Docker writes ``2026-10-08T21:52:01.123456789Z``; Python before 3.11
    parses neither the ``Z`` nor nine fractional digits. The zero time
    ``0001-01-01T00:00:00Z`` means "never", and becomes "".
    """
    sText = (sDockerTime or "").strip()
    if not sText or sText.startswith(_S_DOCKER_ZERO_TIME_PREFIX):
        return ""
    sText = sText[:-1] + "+00:00" if sText.endswith("Z") else sText
    sMain, sSeparator, sFraction = sText.partition(".")
    if sSeparator:
        iZone = next(
            (iIndex for iIndex, sCharacter in enumerate(sFraction)
             if sCharacter in "+-"), len(sFraction))
        sText = sMain + "." + sFraction[:iZone][:6] + sFraction[iZone:]
    datetimeParsed = _fdatetimeParseIso(sText)
    return datetimeParsed.isoformat() if datetimeParsed else ""


# ---------------------------------------------------------------------
# A stopped container's evidence, and the owner a URL's id names
# ---------------------------------------------------------------------

def fsDescribeExitedOomEvidence(dictEvidence):
    """Return the CLI's one line for an OOM-killed previous container."""
    dictIncident = {
        "sKind": S_INCIDENT_EXITED_OOM_KILLED,
        "sContainerId": dictEvidence.get("sContainerId", ""),
        "sWindowEndIso": dictEvidence.get("sFinishedIso", ""),
    }
    return (
        _fsExitedOpening(dictIncident, datetime.now(timezone.utc))
        + " To give it more memory, raise memoryLimitGigabytes in "
        "vaibify.yml.")


def fnRecordExitedEvidenceForApp(appState, sName, dictEvidence):
    """Record a removed container's OOM evidence in the hub's memory store."""
    if not dictEvidence or not dictEvidence.get("bOomKilled"):
        return
    dictStore = getattr(appState, "dictContainerMemory", None)
    if dictStore is not None:
        fnRecordExitedOomKill(dictStore, sName, dictEvidence)


def fsOwnerNameForContainerId(dictContainerOwners, sContainerId):
    """Return the owner-map name whose record holds this Docker id, or ""."""
    for sName, recordOwner in list(dictContainerOwners.items()):
        if getattr(recordOwner, "sContainerId", "") == sContainerId:
            return sName
    return ""
