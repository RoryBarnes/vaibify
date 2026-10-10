"""The `vaibify connect` and `vaibify verify` shells, recorded and ended.

A bare ``docker exec -it`` survives its client: close the window and
the shell, and every agent started in it, run on inside the container
with nothing recording them. This module wraps the CLI's interactive
exec in the containment seam's group-reporting script, records the
session it opened on the host, ends it with proof on every exit, and
reaps the sessions of CLIs that were killed outright.

It is a seam module beside ``terminalContainment``: it may build the
wrapper and discover the group, which no module outside the seam may.
It takes no container flock and writes no journal record, so a CLI
shell stays invisible to the quiescence claim (``docs/knownDebt.md``).
"""

import logging
import os
import signal

from .terminalContainment import (
    F_KILL_WAIT_SECONDS,
    TerminalContainmentError,
    fdictTerminateAndProveGroup,
    fiDiscoverTerminalProcessGroup,
    fsBuildGroupReportingCommand,
    fsMintGroupMarkerPath,
)

logger = logging.getLogger("vaibify")

__all__ = [
    "CliExecInterruptedError",
    "fdictReapOrphanedCliShells",
    "fnRunCleanedUpCliExec",
]

# Where a CLI exec records the in-container session it opened. On the
# host, so an agent inside the container cannot forge or remove one;
# one file per (container id, session id); the same 0700 registry
# discipline as the other ~/.vaibify directories.
_S_CLI_SHELL_DIRECTORY = os.path.expanduser("~/.vaibify/cliShells")
_S_CLI_SHELL_SUFFIX = ".json"


# /proc/<pid>/stat's start time is field 22; after "pid (comm) " is
# stripped the list begins at field 3, so it sits at index 19.
_I_START_CLOCK_INDEX_AFTER_COMM = 19


class CliExecInterruptedError(Exception):
    """The CLI exec's window closed or the CLI was asked to stop."""


def fnRunCleanedUpCliExec(sContainerName, sUser, listCommand):
    """Run ``docker exec -it`` such that closing the window ends it all.

    A bare ``docker exec -it`` survives its client: close the window
    and the shell, and every agent started in it, run on inside the
    container with nothing recording them. The command is therefore
    wrapped in the same group-reporting script the dashboard terminal
    uses, the session leader is discovered from its marker, a record
    is written on the host, and on every exit path the session is
    signalled and PROVEN empty before the record is deleted. A proof
    that cannot be reached keeps the record for the reaper
    (:func:`fdictReapOrphanedCliShells`), which acts once the CLI pid
    is provably dead. The TTY stays ``docker exec``'s own; Ctrl-C
    inside the shell reaches the remote shell as it always has.
    """
    from vaibify.docker.containerManager import fprocessLaunchInteractiveExec
    from vaibify.docker.dockerConnection import DockerConnection
    connectionDocker = DockerConnection()
    sContainerId = _fsRunningContainerIdForName(connectionDocker, sContainerName)
    sMarkerPath = fsMintGroupMarkerPath()
    sWrapperScript = fsBuildGroupReportingCommand(
        " ".join(listCommand), sMarkerPath,
    )
    processChild = fprocessLaunchInteractiveExec(
        sContainerName, sUser, sWrapperScript,
    )
    iSessionId = fiDiscoverTerminalProcessGroup(
        connectionDocker, sContainerId, sMarkerPath,
    )
    sRecordPath = _fsWriteCliShellRecord(
        connectionDocker, sContainerName, sContainerId, iSessionId,
    )
    _fnAwaitCliExecThenEndItsSession(
        processChild, connectionDocker, sContainerName, sContainerId,
        iSessionId, sRecordPath,
    )


def _fsRunningContainerIdForName(connectionDocker, sContainerName):
    """Return the running container's id, or raise with the remedy."""
    for dictContainer in connectionDocker.flistGetRunningContainers():
        if dictContainer.get("sName") == sContainerName:
            return dictContainer["sContainerId"]
    raise TerminalContainmentError(
        f"Container '{sContainerName}' is not running; start it with "
        f"'vaibify start -p {sContainerName}' first."
    )


def _fnAwaitCliExecThenEndItsSession(
    processChild, connectionDocker, sContainerName, sContainerId,
    iSessionId, sRecordPath,
):
    """Wait for the exec; on any exit end its session, with proof."""

    def fnRaiseInterrupted(iSignal, _):
        raise CliExecInterruptedError(iSignal)

    dictPrevious = {
        iSignal: signal.signal(iSignal, fnRaiseInterrupted)
        for iSignal in (signal.SIGHUP, signal.SIGTERM)
    }
    try:
        processChild.wait()
    except CliExecInterruptedError:
        pass
    finally:
        for iSignal, fnHandler in dictPrevious.items():
            signal.signal(iSignal, fnHandler)
        _fnEndCliSessionAndRecord(
            connectionDocker, sContainerName, sContainerId, iSessionId,
            sRecordPath, processChild,
        )


def _fnEndCliSessionAndRecord(
    connectionDocker, sContainerName, sContainerId, iSessionId,
    sRecordPath, processChild=None,
):
    """TERM, then KILL, the session; delete the record only on proof."""
    dictProof = fdictTerminateAndProveGroup(
        connectionDocker, sContainerName, sContainerId, iSessionId,
    )
    if processChild is not None:
        from vaibify.docker.containerManager import fnAwaitProcessOrKill
        fnAwaitProcessOrKill(processChild, F_KILL_WAIT_SECONDS)
    if dictProof["bProvenEmpty"]:
        _fnUnlinkCliShellRecord(sRecordPath)
        return
    logger.warning(
        "CLI exec session %d in container '%s' could not be proven empty "
        "(%s); its record is kept for the reaper",
        iSessionId, sContainerName, dictProof["sDetail"],
    )


def _fsReadSessionLeaderStartClock(connectionDocker, sContainerId, iSessionId):
    """Return the leader's in-container start clock, or '' when unreadable.

    Field 22 of ``/proc/<pid>/stat`` (start time in clock ticks since
    boot), taken after the LAST ``)`` so a hostile comm cannot shift
    it. Together with the pid it names one process incarnation; a
    recycled pid shows a different clock. A typed file read, as every
    new container probe must be: no program of this module's runs.
    """
    try:
        baStat = connectionDocker.fbaFetchFile(
            sContainerId, f"/proc/{int(iSessionId)}/stat",
        )
    except Exception:  # noqa: BLE001 -- unreadable, never a wrong answer
        return ""
    listTail = baStat.decode("utf-8", "replace").rsplit(")", 1)[-1].split()
    return listTail[_I_START_CLOCK_INDEX_AFTER_COMM] if (
        len(listTail) > _I_START_CLOCK_INDEX_AFTER_COMM) else ""


def _fsWriteCliShellRecord(
    connectionDocker, sContainerName, sContainerId, iSessionId,
):
    """Record the opened session on the host; return the record's path."""
    from vaibify.config import pidFileRegistry
    from vaibify.config.processLiveness import fsNowClaimIso
    pidFileRegistry.fnEnsureDirectory(_S_CLI_SHELL_DIRECTORY)
    sRecordPath = os.path.join(
        _S_CLI_SHELL_DIRECTORY,
        f"{sContainerId}-{int(iSessionId)}{_S_CLI_SHELL_SUFFIX}",
    )
    dictPayload = {
        "sContainerId": sContainerId,
        "sContainerName": sContainerName,
        "iSessionId": int(iSessionId),
        "sSessionStartClock": _fsReadSessionLeaderStartClock(
            connectionDocker, sContainerId, iSessionId,
        ),
        "iCliPid": os.getpid(),
        "sCliStartedIso": fsNowClaimIso(),
    }
    with pidFileRegistry.ffileOpenNoFollow(sRecordPath) as fileHandle:
        pidFileRegistry.fnWritePayload(fileHandle, dictPayload)
    return sRecordPath


def _fnUnlinkCliShellRecord(sRecordPath):
    """Delete a CLI shell record; a missing one is already gone."""
    from vaibify.config import pidFileRegistry
    pidFileRegistry.fnUnlinkQuietly(sRecordPath)


def fdictReapOrphanedCliShells(connectionDocker):
    """End the sessions of CLI execs whose CLI died, on proof.

    A record is acted on only when its CLI pid is provably dead
    (missing, or recycled after the recorded start). The leader's
    in-container identity is re-verified against the recorded start
    clock before anything is signalled, so a recycled pid is never
    killed; a record whose container is gone, stopped or recreated is
    simply deleted. A live CLI's record is left alone.
    """
    from vaibify.config import pidFileRegistry
    from vaibify.config.processLiveness import fbIsProcessAliveSince
    listEnded, listKept, listDeleted = [], [], []
    for sRecordPath in pidFileRegistry.flistRegistryFiles(
        _S_CLI_SHELL_DIRECTORY, _S_CLI_SHELL_SUFFIX,
    ):
        dictRecord = pidFileRegistry.fdictReadPayload(sRecordPath)
        if not _fbCliShellRecordIsWellFormed(dictRecord):
            _fnUnlinkCliShellRecord(sRecordPath)
            listDeleted.append(sRecordPath)
            continue
        if fbIsProcessAliveSince(
            dictRecord["iCliPid"], dictRecord["sCliStartedIso"],
        ):
            listKept.append(sRecordPath)
            continue
        sVerdict = _fsReapOneDeadCliShell(connectionDocker, dictRecord, sRecordPath)
        {"ended": listEnded, "kept": listKept, "deleted": listDeleted}[
            sVerdict].append(sRecordPath)
    return {"listEnded": listEnded, "listKept": listKept,
            "listDeleted": listDeleted}


def _fbCliShellRecordIsWellFormed(dictRecord):
    """Return True when a record carries every field the reaper reads."""
    from vaibify.config.processLiveness import fbIsUsablePid
    return (
        isinstance(dictRecord.get("sContainerId"), str)
        and bool(dictRecord.get("sContainerId"))
        and isinstance(dictRecord.get("sContainerName"), str)
        and fbIsUsablePid(dictRecord.get("iSessionId"))
        and fbIsUsablePid(dictRecord.get("iCliPid"))
        and isinstance(dictRecord.get("sCliStartedIso"), str)
    )


def _fsReapOneDeadCliShell(connectionDocker, dictRecord, sRecordPath):
    """Reap one record whose CLI is dead; return ended, kept or deleted."""
    sContainerId = dictRecord["sContainerId"]
    dictState = connectionDocker.fdictReadContainerState(sContainerId)
    if not dictState or not dictState.get("Running"):
        _fnUnlinkCliShellRecord(sRecordPath)
        return "deleted"
    sLiveClock = _fsReadSessionLeaderStartClock(
        connectionDocker, sContainerId, dictRecord["iSessionId"],
    )
    if sLiveClock != dictRecord.get("sSessionStartClock", "") or not sLiveClock:
        logger.info(
            "CLI exec record %s names a session leader that is gone or "
            "recycled; the record is dropped and nothing is signalled",
            os.path.basename(sRecordPath),
        )
        _fnUnlinkCliShellRecord(sRecordPath)
        return "deleted"
    dictProof = fdictTerminateAndProveGroup(
        connectionDocker, dictRecord["sContainerName"], sContainerId,
        dictRecord["iSessionId"],
    )
    if not dictProof["bProvenEmpty"]:
        return "kept"
    _fnUnlinkCliShellRecord(sRecordPath)
    return "ended"
