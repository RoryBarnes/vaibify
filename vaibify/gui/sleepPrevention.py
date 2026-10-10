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
    "fconfigLoadForSessionLane",
    "fdictReapSessionLanesOfStoppedContainers",
    "fnApplySessionLaneSetting",
    "fnEnsureSessionLaneForClaim",
    "fnSweepWorkLaneKeepAlives",
]

import logging

from vaibify.config import keepAliveManager
from vaibify.config.connectionAvailability import fbDockerReachable

logger = logging.getLogger("vaibify")

# Whether the previous sweep failed to list the daemon. A daemon that
# is down stays down for many sixty-second ticks, and a traceback on
# every one of them buried the log of the hub that was actually
# failing (measured, 2026-09-18); the outage is reported once, and its
# end once.
_bDaemonListingFailed = False

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
    if not fbDockerReachable(connectionDocker):
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
    global _bDaemonListingFailed
    try:
        listContainers = connectionDocker.flistGetRunningContainers()
    except Exception as error:
        if not _bDaemonListingFailed:
            logger.warning(
                "Could not list running containers for the sleep-prevention "
                "sweep (%s); leaving every work-lane keep-alive as it is "
                "until the daemon answers again", error,
            )
        _bDaemonListingFailed = True
        return None
    if _bDaemonListingFailed:
        logger.info("The daemon answers the sleep-prevention sweep again")
    _bDaemonListingFailed = False
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


def fconfigLoadForSessionLane(sName):
    """Return the project's config, or None when it cannot be loaded.

    The non-raising sibling of the registry routes' loader: a claim must
    succeed whether or not ``vaibify.yml`` loads, so a lane the config
    cannot decide is simply not started, and the reason is logged.
    """
    from vaibify.cli.configLoader import fconfigLoadFromPath
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(sName)
    if not dictProject or not dictProject.get("sConfigPath"):
        return None
    try:
        return fconfigLoadFromPath(dictProject["sConfigPath"])
    except Exception as error:  # noqa: BLE001 — a lane, not the claim
        logger.warning(
            "SLEEP PREVENTION cannot read the config of %r to decide its "
            "session lane: %s", sName, error,
        )
        return None


def fnEnsureSessionLaneForClaim(sName, sContainerId):
    """Hold a claimed, running ``neverSleep`` container awake.

    The session lane used to start only when vaibify STARTED a
    container, so a hub that restarted and claimed a running
    ``neverSleep`` container held nothing, and the machine stayed awake
    only while some other lane happened to. A claim now starts the lane
    when the project asks for it and none is live; the liveness check
    is what keeps a tab reload from churning a process, and what lets
    this hub adopt a keep-alive a crashed hub left behind.
    """
    if not sContainerId or sContainerId == sName:
        return
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return
    configProject = fconfigLoadForSessionLane(sName)
    if not getattr(configProject, "bNeverSleep", False):
        return
    if keepAliveManager.fbKeepAliveIsLive(sName):
        return
    keepAliveManager.fnStartKeepAlive(sName)
    logger.info(
        "SLEEP PREVENTION holding the machine awake for claimed neverSleep "
        "container %r", sName,
    )


def fnApplySessionLaneSetting(appState, dictCtx, sName, bNeverSleep):
    """Start or stop the session lane of a held, running container.

    Called when ``neverSleep`` is saved from the dashboard. A container
    this hub does not hold, or that is not running, is left alone: the
    next start or claim reads the file.
    """
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return
    if sName not in getattr(appState, "dictContainerOwners", {}):
        return
    connectionDocker = dictCtx.get("docker") if dictCtx else None
    dictRunningIdByName = (
        _fdictRunningContainerIdsByName(connectionDocker)
        if fbDockerReachable(connectionDocker) else None
    )
    if dictRunningIdByName is None or sName not in dictRunningIdByName:
        return
    if bNeverSleep and not keepAliveManager.fbKeepAliveIsLive(sName):
        keepAliveManager.fnStartKeepAlive(sName)
    elif not bNeverSleep:
        keepAliveManager.fnStopKeepAlive(sName)


def fdictReapSessionLanesOfStoppedContainers(dictCtx):
    """Stop every session-lane keep-alive whose container is not running.

    A reaper (see ``remnantReapers``). A running container's lane is
    left alone, whoever started it, for the next claim to adopt; a lane
    whose container is gone holds the machine awake for nothing.
    """
    from . import remnantReapers
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return remnantReapers.fdictBuildReaperOutcome(
            remnantReapers.S_OUTCOME_RAN)
    connectionDocker = dictCtx.get("docker") if dictCtx else None
    if not fbDockerReachable(connectionDocker):
        return remnantReapers.fdictBuildReaperOutcome(
            remnantReapers.S_OUTCOME_FORBIDDEN,
            sReason="the Docker daemon is unreachable",
            sRemedy="Start Docker, then rescan.",
        )
    dictRunningIdByName = _fdictRunningContainerIdsByName(connectionDocker)
    if dictRunningIdByName is None:
        return remnantReapers.fdictBuildReaperOutcome(
            remnantReapers.S_OUTCOME_FORBIDDEN,
            sReason="the Docker daemon could not list running containers",
            sRemedy="Check that Docker answers 'docker ps', then rescan.",
        )
    iRemoved = 0
    for sRegistryName in keepAliveManager.flistKeepAliveNames():
        if S_WORK_LANE_SEPARATOR in sRegistryName:
            continue
        if sRegistryName in dictRunningIdByName:
            continue
        keepAliveManager.fnStopKeepAlive(sRegistryName)
        iRemoved += 1
    return remnantReapers.fdictBuildReaperOutcome(
        remnantReapers.S_OUTCOME_RAN, iRemoved=iRemoved)
