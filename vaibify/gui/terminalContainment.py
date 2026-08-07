"""Durable containment of browser terminal executions (design v13).

Every browser terminal is a Docker exec that can outlive its
WebSocket: closing the socket sends keystrokes, not proof of death,
and a shell that traps signals — or detaches a signal-trapping
descendant — keeps writing after ``exec_inspect`` reports
``Running == false``. So each terminal exec becomes a durable
``TerminalExecutionRecord``: a journal record (kind ``terminal``) in
the container's operation set carrying the exec id, the recorded
in-container session/process group, and the owner generation, plus an
in-memory registry entry on ``app.state`` for the live handles.

The group is knowable because the OCI runtime starts a tty exec as a
session leader (pid == pgid == sid, verified against a live daemon);
the wrapper command re-reports those ids from ``/proc/self/stat``
into a marker file before ``exec``-ing the shell, and the start path
fails closed — terminate what it can and refuse the terminal — when
the report never arrives or the exec was not a session leader.
Everything the shell spawns stays in that session (job control moves
children to new process groups but never out of the session), so
proving the SESSION empty subsumes proving the group empty. The one
adversary this cannot contain is a descendant that itself calls
``setsid``; the documented escape for that — and for every
unprovable outcome — is a container stop/restart after the record
quarantines.

Terminate-and-prove (:func:`fdictTerminateAndProveRecord`) is the
single exit for a terminal record, used by EVERY authority-ending
path (§6.1/§7/§10): socket close, explicit release, the safe reaper,
and hub shutdown. It fences further input (reversible), signals TERM
then KILL to every session member via a root probe exec, and settles
the journal record only when a probe PROVES no member remains;
anything indeterminate retains the record as
``NEEDS_RECONCILIATION`` — never an optimistic proceed.

Portability: the /proc walk and signalling run over ``/bin/sh``
inside the container (no bash, no ps), so any Linux image with a
POSIX shell is containable. The host side is pure standard library.
"""

__all__ = [
    "F_GROUP_DISCOVERY_TIMEOUT_SECONDS",
    "F_TERMINATE_WAIT_SECONDS",
    "F_KILL_WAIT_SECONDS",
    "S_TERMINAL_OPERATION_KIND",
    "TerminalContainmentError",
    "TerminalExecutionRecord",
    "fdictCreateTerminalRecordRegistry",
    "fsMintGroupMarkerPath",
    "fsBuildGroupReportingCommand",
    "fsPrepareTerminalOperation",
    "fnPromoteTerminalOperation",
    "fiDiscoverTerminalProcessGroup",
    "fnRecordTerminalProcessGroup",
    "fnRegisterTerminalRecord",
    "fdictTerminateAndProveRecord",
    "fdictDrainTerminalRecordsForContainer",
    "fdictDrainAllTerminalRecords",
    "fdictDrainSessionRecord",
    "fbContainerHasLiveTerminalRecords",
    "fsetNamesWithLiveTerminalRecords",
]

import logging
import re
import secrets
from typing import Optional
import shlex
import threading
import time
from dataclasses import dataclass

from vaibify.config import operationJournal
from vaibify.config.mutationAdmission import (
    fnAssertOperationAdmittedByIdentity,
)
from .containerOwnership import ffReadSecondsFromEnvironment

logger = logging.getLogger("vaibify")

S_TERMINAL_OPERATION_KIND = "terminal"

F_GROUP_DISCOVERY_TIMEOUT_SECONDS = ffReadSecondsFromEnvironment(
    "VAIBIFY_TERMINAL_GROUP_DISCOVERY_TIMEOUT_SECONDS", 5.0,
)
F_TERMINATE_WAIT_SECONDS = ffReadSecondsFromEnvironment(
    "VAIBIFY_TERMINAL_TERMINATE_WAIT_SECONDS", 3.0,
)
F_KILL_WAIT_SECONDS = ffReadSecondsFromEnvironment(
    "VAIBIFY_TERMINAL_KILL_WAIT_SECONDS", 2.0,
)
_F_PROBE_POLL_SECONDS = 0.2

S_RECORD_STATE_LIVE = "live"
S_RECORD_STATE_SETTLED = "settled"
S_RECORD_STATE_QUARANTINED = "quarantined"

# The shell that becomes the terminal is a deployment constant, never
# user input; the allowlist keeps a future caller from smuggling shell
# metacharacters into the exec wrapper.
_REGEX_SAFE_SHELL_COMMAND = re.compile(r"^[A-Za-z0-9 _/.-]+$")

_LOCK_DRAIN_REGISTRY = threading.Lock()
_DICT_CONTAINER_DRAIN_LOCKS = {}


class TerminalContainmentError(RuntimeError):
    """A terminal execution could not be contained or proven dead."""


@dataclass(eq=False)
class TerminalExecutionRecord:
    """One live browser terminal's durable containment identity.

    ``iProcessGroup`` is 0 until the in-container wrapper reports the
    session/group leader id; a record still at 0 can never be proven
    group-empty and quarantines on any authority-ending path unless
    its exec provably never reached the shell. ``session`` is the
    ``TerminalSession`` whose input the drain fences; ``dictRegistry``
    is the ``app.state`` registry the record lives in, so every drain
    path can deregister it without reaching back to the application.
    """

    sOperationId: str
    sContainerName: str
    sContainerId: str
    sDockerExecId: str
    iOwnerGeneration: int
    connectionDocker: "DockerConnection"
    dictRegistry: Optional[dict]
    session: Optional["TerminalSession"] = None
    iProcessGroup: int = 0
    sState: str = S_RECORD_STATE_LIVE


def fdictCreateTerminalRecordRegistry():
    """Return the empty ``{sContainerName: {sOperationId: record}}`` map."""
    return {}


def _fdictRegistryForAppState(appState):
    """Return the app's terminal registry, creating it when absent."""
    dictRegistry = getattr(appState, "dictTerminalExecutionRecords", None)
    if dictRegistry is None:
        dictRegistry = fdictCreateTerminalRecordRegistry()
        appState.dictTerminalExecutionRecords = dictRegistry
    return dictRegistry


def _flockDrainForContainer(sContainerName):
    """Return the per-container drain lock, creating it synchronized.

    Serializes concurrent drains of one container (a socket-close
    drain racing an explicit release), so the second drainer waits
    and then observes an already-settled registry instead of racing
    the journal writes. Locks are never deleted; the map is bounded
    by the number of container names ever drained in this process.
    """
    with _LOCK_DRAIN_REGISTRY:
        lockExisting = _DICT_CONTAINER_DRAIN_LOCKS.get(sContainerName)
        if lockExisting is None:
            lockExisting = threading.Lock()
            _DICT_CONTAINER_DRAIN_LOCKS[sContainerName] = lockExisting
        return lockExisting


# ---------------------------------------------------------------------
# Start-side: the group-reporting wrapper and the journal glue.
# ---------------------------------------------------------------------

def fsMintGroupMarkerPath():
    """Return a fresh in-container marker path for one terminal start."""
    return f"/tmp/.vaibifyTerminalGroup.{secrets.token_hex(8)}"


def fsBuildGroupReportingCommand(sShellCommand, sMarkerPath):
    """Return the ``/bin/sh -c`` script that reports its group, then execs.

    The script reads its own ``/proc/self/stat`` (fields after the
    LAST ``)`` so a hostile comm cannot shift them), writes
    ``pid pgid sid`` to the marker via write-then-rename so the host
    never reads a partial line, and only then ``exec``s the terminal
    shell — the shell keeps the reported pid, and a failure at any
    step exits without ever starting a shell, which is what lets the
    abort path settle an undiscovered dead exec honestly.
    """
    if not _REGEX_SAFE_SHELL_COMMAND.match(sShellCommand or ""):
        raise TerminalContainmentError(
            f"Refusing terminal shell command {sShellCommand!r}: only "
            "plain program paths and arguments are allowed in the "
            "containment wrapper"
        )
    sQuotedMarker = shlex.quote(sMarkerPath)
    return (
        "sStatContent=$(cat /proc/self/stat) && "
        'sStatTail="${sStatContent##*) }" && '
        "set -- $sStatTail && "
        f"printf '%s %s %s\\n' \"$$\" \"$3\" \"$4\" "
        f"> {sQuotedMarker}.partial && "
        f"mv {sQuotedMarker}.partial {sQuotedMarker} && "
        f"exec {sShellCommand}"
    )


def fsPrepareTerminalOperation(sContainerName, sContainerId):
    """Write the PREPARED terminal record; refuse a quarantined set.

    The pre-launch half of the two-phase journal (design §8). The
    quarantine check runs BEFORE anything is created so a container
    with an unreconciled record refuses new terminals with a helpful
    error instead of minting an exec it would immediately have to
    kill.
    """
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        sContainerName,
    )
    for sRecordId, dictRecord in dictOutcomeRead["dictOperations"].items():
        if dictRecord["sState"] == (
            operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
        ):
            raise TerminalContainmentError(
                f"Container '{sContainerName}' has a quarantined journal "
                f"record ({sRecordId}); no new terminal may open until "
                "it is reconciled"
            )
    return operationJournal.fsPrepareOperation(
        sContainerName, S_TERMINAL_OPERATION_KIND, sContainerId,
    )


def fnPromoteTerminalOperation(
    sContainerName, sOperationId, sExecId, sContainerId, iOwnerGeneration,
):
    """Journal the real exec id BEFORE exec_start, then identity-gate."""
    dictIdentity = {
        "sDockerExecId": sExecId,
        "sDockerContainerId": sContainerId,
    }
    if iOwnerGeneration > 0:
        dictIdentity["iOwnerGeneration"] = iOwnerGeneration
    operationJournal.fnPromoteOperationToInFlight(
        sContainerName, sOperationId, dictIdentity,
    )
    fnAssertOperationAdmittedByIdentity(
        sContainerName, sOperationId, dictIdentity,
    )


def fiDiscoverTerminalProcessGroup(
    connectionDocker, sContainerId, sMarkerPath, fTimeoutSeconds=None,
):
    """Read the wrapper's marker and return the proven containment id.

    Polls until the marker appears, then requires the reporter to be a
    session leader (pid == sid): the recorded id then names a session
    no unrelated process can ever join, so signalling and proving by
    it can neither miss a descendant nor touch a stranger. A reporter
    that is NOT a session leader would leave job-control descendants
    provably uncontainable, so it raises instead of proceeding.
    """
    if fTimeoutSeconds is None:
        fTimeoutSeconds = F_GROUP_DISCOVERY_TIMEOUT_SECONDS
    sQuotedMarker = shlex.quote(sMarkerPath)
    sScript = f"cat {sQuotedMarker} && rm -f -- {sQuotedMarker}"
    fDeadline = time.monotonic() + fTimeoutSeconds
    while True:
        try:
            iExitCode, sOutput = connectionDocker.ftRunRootShellProbe(
                sContainerId, sScript,
            )
        except Exception:
            iExitCode, sOutput = (-1, "")
        if iExitCode == 0:
            return _fiParseLeaderFromMarker(sOutput)
        if time.monotonic() >= fDeadline:
            raise TerminalContainmentError(
                "The terminal shell never reported its process group "
                f"within {fTimeoutSeconds} seconds; the terminal is "
                "refused because an unrecorded shell cannot be contained"
            )
        time.sleep(0.05)


def _fiParseLeaderFromMarker(sOutput):
    """Parse ``pid pgid sid`` and require a session leader."""
    listFields = sOutput.split()
    try:
        iPid, iProcessGroup, iSessionId = (
            int(listFields[0]), int(listFields[1]), int(listFields[2]),
        )
    except (IndexError, ValueError):
        raise TerminalContainmentError(
            f"The terminal group marker is unparseable: {sOutput[:80]!r}"
        )
    if iPid != iSessionId or iPid != iProcessGroup:
        raise TerminalContainmentError(
            f"The terminal shell (pid {iPid}) is not a session leader "
            f"(pgid {iProcessGroup}, sid {iSessionId}); its descendants "
            "would be uncontainable, so the terminal is refused"
        )
    return iPid


def fnRecordTerminalProcessGroup(
    sContainerName, sOperationId, iProcessGroup,
):
    """Amend the journal record with the discovered group id."""
    operationJournal.fnAmendInFlightHolderIdentity(
        sContainerName, sOperationId,
        {"iHolderProcessGroup": iProcessGroup},
    )


def fnRegisterTerminalRecord(appState, recordTerminal):
    """Add a live record to the app's terminal registry."""
    dictRegistry = _fdictRegistryForAppState(appState)
    recordTerminal.dictRegistry = dictRegistry
    dictRegistry.setdefault(
        recordTerminal.sContainerName, {},
    )[recordTerminal.sOperationId] = recordTerminal


def _fnDeregisterTerminalRecord(recordTerminal):
    """Drop a drained record from its registry, pruning empty maps."""
    dictRegistry = recordTerminal.dictRegistry
    if not isinstance(dictRegistry, dict):
        return
    dictByOperation = dictRegistry.get(recordTerminal.sContainerName)
    if not dictByOperation:
        return
    dictByOperation.pop(recordTerminal.sOperationId, None)
    if not dictByOperation:
        dictRegistry.pop(recordTerminal.sContainerName, None)


# ---------------------------------------------------------------------
# Terminate-and-prove: the single exit for a terminal record.
# ---------------------------------------------------------------------

def fdictTerminateAndProveRecord(
    recordTerminal,
    fTerminateWaitSeconds=None,
    fKillWaitSeconds=None,
):
    """Fence, terminate the recorded group, and PROVE it empty.

    Outcomes (design v13 §6.1): a conclusive empty group settles the
    journal record; anything else — a surviving member, an
    inconclusive probe, an unreachable daemon — retains the record as
    ``NEEDS_RECONCILIATION`` so the container quarantines until
    reconciliation or a container restart proves it clean. Never an
    optimistic proceed. Callers serialize through the per-container
    drain lock (:func:`fdictDrainTerminalRecordsForContainer`); this
    function assumes it is the record's only drainer.
    """
    if recordTerminal.sState != S_RECORD_STATE_LIVE:
        return {
            "bProvenEmpty": recordTerminal.sState == S_RECORD_STATE_SETTLED,
            "sDetail": f"record already {recordTerminal.sState}",
        }
    _fnFenceSessionQuietly(recordTerminal)
    if recordTerminal.iProcessGroup <= 0:
        return _fdictResolveUndiscoveredRecord(recordTerminal)
    fTermWait = (
        F_TERMINATE_WAIT_SECONDS if fTerminateWaitSeconds is None
        else fTerminateWaitSeconds
    )
    fKillWait = (
        F_KILL_WAIT_SECONDS if fKillWaitSeconds is None
        else fKillWaitSeconds
    )
    dictProbe = _fdictSignalAndAwaitEmpty(recordTerminal, "TERM", fTermWait)
    if not _fbProbeProvesEmpty(dictProbe):
        dictProbe = _fdictSignalAndAwaitEmpty(
            recordTerminal, "KILL", fKillWait,
        )
    if _fbProbeProvesEmpty(dictProbe):
        return _fdictSettleProvenRecord(recordTerminal, dictProbe)
    return _fdictQuarantineRecord(
        recordTerminal,
        "the terminal process group could not be proven empty: "
        f"{dictProbe.get('sDetail', '')}",
    )


def _fnFenceSessionQuietly(recordTerminal):
    """Fence further input on the record's terminal session, if any."""
    session = recordTerminal.session
    if session is not None and hasattr(session, "fnFenceInput"):
        session.fnFenceInput()


def _fdictSignalAndAwaitEmpty(recordTerminal, sSignalName, fWaitSeconds):
    """Signal the group, then poll until proven empty or the bound."""
    try:
        recordTerminal.connectionDocker.fnSignalProcessGroupMembers(
            recordTerminal.sContainerId, recordTerminal.iProcessGroup,
            sSignalName,
        )
    except Exception as error:
        logger.warning(
            "Terminal group %s signal failed for container '%s': %s",
            sSignalName, recordTerminal.sContainerName, error,
        )
    fDeadline = time.monotonic() + fWaitSeconds
    while True:
        dictProbe = _fdictProbeGroupQuietly(recordTerminal)
        if _fbProbeProvesEmpty(dictProbe):
            return dictProbe
        if time.monotonic() >= fDeadline:
            return dictProbe
        time.sleep(_F_PROBE_POLL_SECONDS)


def _fdictProbeGroupQuietly(recordTerminal):
    """Run one group-emptiness probe, folding errors to inconclusive."""
    try:
        return recordTerminal.connectionDocker.fdictProbeProcessGroupMembers(
            recordTerminal.sContainerId, recordTerminal.iProcessGroup,
        )
    except Exception as error:
        return {
            "bConclusive": False, "iMemberCount": -1,
            "sDetail": f"process-group probe raised: {error}",
        }


def _fbProbeProvesEmpty(dictProbe):
    """Return True only for a conclusive zero-member probe."""
    return bool(dictProbe.get("bConclusive")) and (
        dictProbe.get("iMemberCount") == 0
    )


def _fdictResolveUndiscoveredRecord(recordTerminal):
    """Resolve a record whose group was never learned, failing closed.

    The wrapper writes its marker BEFORE ``exec``-ing the shell and no
    input can have reached the exec (input flows only through the
    session socket, which is handed out after discovery succeeds), so
    a dead exec here provably never spawned anything: settle. A LIVE
    exec whose group is unknown is uncontainable: quarantine.
    """
    try:
        dictInspect = recordTerminal.connectionDocker.fdictInspectExec(
            recordTerminal.sDockerExecId,
        )
    except Exception as error:
        return _fdictQuarantineRecord(
            recordTerminal,
            "the terminal's process group was never learned and the "
            f"exec state could not be read: {error}",
        )
    if dictInspect.get("Running") is False:
        try:
            operationJournal.fnSettleOperation(
                recordTerminal.sContainerName, recordTerminal.sOperationId,
            )
        except operationJournal.OperationJournalError as error:
            return _fdictQuarantineRecord(
                recordTerminal, f"could not settle the record: {error}",
            )
        recordTerminal.sState = S_RECORD_STATE_SETTLED
        _fnDeregisterTerminalRecord(recordTerminal)
        return {
            "bProvenEmpty": True,
            "sDetail": "the exec died before the wrapper reached the "
                       "shell; nothing was ever spawned",
        }
    return _fdictQuarantineRecord(
        recordTerminal,
        "a live terminal shell never reported its process group and "
        "cannot be contained",
    )


def _fdictSettleProvenRecord(recordTerminal, dictProbe):
    """Settle the journal record for a proven-empty group."""
    try:
        operationJournal.fnSettleOperation(
            recordTerminal.sContainerName, recordTerminal.sOperationId,
        )
    except operationJournal.OperationJournalError as error:
        return _fdictQuarantineRecord(
            recordTerminal,
            f"the group is empty but the record could not settle: {error}",
        )
    recordTerminal.sState = S_RECORD_STATE_SETTLED
    _fnDeregisterTerminalRecord(recordTerminal)
    return {"bProvenEmpty": True, "sDetail": dictProbe.get("sDetail", "")}


def _fdictQuarantineRecord(recordTerminal, sReason):
    """Retain-and-quarantine: poison the journal record, deregister."""
    try:
        operationJournal.fnMarkOperationNeedsReconciliation(
            recordTerminal.sContainerName, recordTerminal.sOperationId,
            sNote=sReason,
        )
    except operationJournal.OperationJournalError as error:
        logger.warning(
            "Could not poison terminal record %s for container '%s': %s",
            recordTerminal.sOperationId, recordTerminal.sContainerName,
            error,
        )
    recordTerminal.sState = S_RECORD_STATE_QUARANTINED
    _fnDeregisterTerminalRecord(recordTerminal)
    logger.warning(
        "Terminal execution %s in container '%s' retained-and-"
        "quarantined: %s",
        recordTerminal.sOperationId, recordTerminal.sContainerName,
        sReason,
    )
    return {"bProvenEmpty": False, "sDetail": sReason}


# ---------------------------------------------------------------------
# Drains: the per-path entry points (close / release / reap / shutdown).
# ---------------------------------------------------------------------

def fdictDrainTerminalRecordsForContainer(appState, sContainerName):
    """Terminate-and-prove every live terminal record of one container.

    Blocking (Docker probes); async callers wrap it in a worker
    thread. After it returns, every record that existed is settled or
    quarantined — the invariant the release path, the reaper, and the
    shutdown drain rely on.
    """
    with _flockDrainForContainer(sContainerName):
        dictRegistry = _fdictRegistryForAppState(appState)
        listRecords = list(
            (dictRegistry.get(sContainerName) or {}).values(),
        )
        listSettled, listQuarantined = [], []
        for recordTerminal in listRecords:
            dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
            if dictOutcome["bProvenEmpty"]:
                listSettled.append(recordTerminal.sOperationId)
            else:
                listQuarantined.append(recordTerminal.sOperationId)
        return {
            "listSettledOperationIds": listSettled,
            "listQuarantinedOperationIds": listQuarantined,
        }


def fdictDrainAllTerminalRecords(appState):
    """Drain every container's terminal records (the shutdown path)."""
    dictRegistry = _fdictRegistryForAppState(appState)
    dictOutcomes = {}
    for sContainerName in list(dictRegistry.keys()):
        dictOutcomes[sContainerName] = fdictDrainTerminalRecordsForContainer(
            appState, sContainerName,
        )
    return dictOutcomes


def fdictDrainSessionRecord(session):
    """Drain the containment record attached to one terminal session.

    The socket-close path (§7: a socket closing is not a terminal
    dying). A session without a real record — the unwired library
    path, or a test double — drains nothing.
    """
    recordTerminal = getattr(session, "recordContainment", None)
    if not isinstance(recordTerminal, TerminalExecutionRecord):
        return None
    with _flockDrainForContainer(recordTerminal.sContainerName):
        return fdictTerminateAndProveRecord(recordTerminal)


def fbContainerHasLiveTerminalRecords(appState, sContainerName):
    """Return True while any terminal record of the container is live.

    The reaper's terminal veto (§7): an owner record may not be reaped
    while a terminal execution is neither settled nor quarantined.
    """
    dictRegistry = getattr(appState, "dictTerminalExecutionRecords", None)
    if not dictRegistry:
        return False
    return bool(dictRegistry.get(sContainerName))


def fsetNamesWithLiveTerminalRecords(appState):
    """Return container names still holding live terminal records.

    The shutdown flock-retention view (§8): a flock is never released
    while a terminal group in that container may still write.
    """
    dictRegistry = getattr(appState, "dictTerminalExecutionRecords", None)
    if not dictRegistry:
        return set()
    return {
        sContainerName
        for sContainerName, dictByOperation in dictRegistry.items()
        if dictByOperation
    }
