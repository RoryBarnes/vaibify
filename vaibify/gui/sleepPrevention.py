"""Sleep prevention that follows work in a container, not a browser tab.

The macOS ``caffeinate`` keep-alive used to have exactly one lifetime:
the ownership record's. ``containerOwnership._fnForceReleaseOwnership``
stops it, so the machine became sleepable the moment a record was
dropped — roughly a reconnect window plus a reap grace after the
browser went away. A dashboard-launched pipeline survived that only
because the reaper is vetoed while vaibify's own ``bRunning`` flag is
set. Work vaibify did not launch — a job the researcher backgrounded in
a terminal, an exec an in-container agent started, or any exec at all
after the hub that launched it was restarted — has no such flag, so the
record was reaped, the keep-alive died, and the laptop slept with the
job still running. The colima VM suspends rather than dies, so the run
is FROZEN, not killed, and looks healthy right up until somebody reads
the timestamps.

So this module gives sleep prevention a second, independent lane whose
lifetime is the WORK's, not the session's:

* The **session lane** is unchanged. It is keyed by the container name,
  started when a container starts, and stopped when the record drops.
* The **work lane** is keyed by :func:`fsWorkLaneKeepAliveName` — a name
  Docker cannot itself produce, so the two lanes can never stop each
  other's process. It is asserted and withdrawn purely from observed
  evidence of running work, on the hub watchdog's cadence.

Evidence, never proof
---------------------
The signal is ``flistRunningExecIdentifiers``: does the daemon report
any exec session in this container still running? That is EVIDENCE of
work. It is not proof of work's ABSENCE — a ``setsid`` descendant whose
parent exec has exited is invisible to it, exactly as it is invisible to
``terminalContainment``'s process-group prover. Vaibify cannot prove
what is running inside a container, and this module does not claim to;
it claims only that when it sees a running exec it keeps the machine
awake, and that when it sees none it stops paying for a keep-alive it
has no reason to hold.

Because the work lane is derived from observation rather than from a
record, a hub that crashed and was restarted re-establishes the
keep-alive for work its predecessor launched. That was impossible while
the keep-alive's only lifetime was an in-process ownership record, and
it is the property the 2026-08-29 experiment made worth having: an exec
survives the death of the client holding its stream.
"""

__all__ = [
    "S_WORK_LANE_SEPARATOR",
    "fsWorkLaneKeepAliveName",
    "fbContainerShowsRunningWorkEvidence",
    "fnSweepWorkLaneKeepAlives",
]

import logging

from vaibify.config import keepAliveManager

logger = logging.getLogger("vaibify")

# Docker container names match [a-zA-Z0-9][a-zA-Z0-9_.-]*, so "@" cannot
# occur in one. A work-lane registry name is therefore unreachable by
# the session lane no matter what a container is called, which is what
# keeps one lane from stopping the other's caffeinate.
S_WORK_LANE_SEPARATOR = "@"

_S_WORK_LANE_SUFFIX = S_WORK_LANE_SEPARATOR + "work"


def fsWorkLaneKeepAliveName(sContainerName):
    """Return the keep-alive registry name of a container's work lane."""
    return sContainerName + _S_WORK_LANE_SUFFIX


def _fsContainerNameFromWorkLaneName(sRegistryName):
    """Return the container a work-lane registry name belongs to, or ''."""
    if not sRegistryName.endswith(_S_WORK_LANE_SUFFIX):
        return ""
    return sRegistryName[: -len(_S_WORK_LANE_SUFFIX)]


def fbContainerShowsRunningWorkEvidence(connectionDocker, sContainerId):
    """Return True when the daemon reports a running exec in a container.

    A daemon that cannot answer is read as EVIDENCE PRESENT. The two
    errors are not symmetric: holding a keep-alive nothing needs costs
    the researcher some battery, while withdrawing one under a running
    multi-day job costs the job. An unreadable container that is still
    running keeps its keep-alive; a container that is no longer running
    is not consulted at all (see :func:`fnSweepWorkLaneKeepAlives`).
    """
    try:
        return bool(
            connectionDocker.flistRunningExecIdentifiers(sContainerId),
        )
    except Exception:
        logger.warning(
            "Could not read exec liveness for container %s; keeping the "
            "work-lane keep-alive rather than sleeping under it",
            sContainerId[:12], exc_info=True,
        )
        return True


def fnSweepWorkLaneKeepAlives(appState, dictCtx):
    """Assert or withdraw every work-lane keep-alive from live evidence.

    One pass of the hub watchdog. Candidates are the running containers
    this hub holds no ownership record for, plus every container that
    already has a work lane. Owned containers are skipped on the way IN
    because their session lane already covers them and their steady
    dashboard polling would otherwise churn a caffeinate every tick; an
    ESTABLISHED lane is still maintained after its container is claimed,
    because a claim does not start a session-lane keep-alive and
    dropping the work lane there would withdraw the only protection the
    running work has.
    """
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return
    connectionDocker = dictCtx.get("docker") if dictCtx else None
    if connectionDocker is None:
        return
    dictRunningIdByName = _fdictRunningContainerIdsByName(connectionDocker)
    if dictRunningIdByName is None:
        return
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    setCandidateNames = _fsetWorkLaneCandidateNames(
        dictRunningIdByName, dictContainerOwners,
    )
    for sName in sorted(setCandidateNames):
        sContainerId = dictRunningIdByName.get(sName, "")
        bEvidence = bool(sContainerId) and (
            fbContainerShowsRunningWorkEvidence(
                connectionDocker, sContainerId,
            )
        )
        _fnApplyWorkLaneDecision(sName, bEvidence)


def _fdictRunningContainerIdsByName(connectionDocker):
    """Return ``{sName: sContainerId}`` for running containers, or None.

    ``None`` means the daemon could not be asked, which is not the same
    as "nothing is running": answering it as an empty set would stop
    every work lane on the host the first time Docker hiccuped.
    """
    try:
        listContainers = connectionDocker.flistGetRunningContainers()
    except Exception:
        logger.warning(
            "Could not list running containers for the sleep-prevention "
            "sweep; leaving every work-lane keep-alive as it is",
            exc_info=True,
        )
        return None
    return {
        dictRow.get("sName", ""): dictRow.get("sContainerId", "")
        for dictRow in listContainers
        if dictRow.get("sName", "")
    }


def _fsetWorkLaneCandidateNames(dictRunningIdByName, dictContainerOwners):
    """Return the container names this pass must decide the lane for."""
    setCandidates = {
        sName for sName in dictRunningIdByName
        if sName not in dictContainerOwners
    }
    for sRegistryName in keepAliveManager.flistKeepAliveNames():
        sName = _fsContainerNameFromWorkLaneName(sRegistryName)
        if sName:
            setCandidates.add(sName)
    return setCandidates


def _fnApplyWorkLaneDecision(sName, bEvidence):
    """Start or stop one container's work-lane keep-alive, idempotently."""
    sRegistryName = fsWorkLaneKeepAliveName(sName)
    bLaneIsLive = keepAliveManager.fbKeepAliveIsLive(sRegistryName)
    if bEvidence and not bLaneIsLive:
        keepAliveManager.fnStartKeepAlive(sRegistryName)
        logger.info(
            "SLEEP PREVENTION holding the machine awake for container "
            "%r: the daemon reports a running exec in it",
            sName,
        )
        return
    if not bEvidence and bLaneIsLive:
        keepAliveManager.fnStopKeepAlive(sRegistryName)
        logger.info(
            "SLEEP PREVENTION released for container %r: no running "
            "exec is visible in it",
            sName,
        )
