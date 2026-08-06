"""Terminal containment (slice 3d) — the machinery, unit level.

The real-container halves of cases 43/44/45 live in
``tests/testTerminalContainmentLive.py``; this file proves the
machinery against stub connections: the journaled create → journal →
start split, the group-reporting wrapper, terminate-and-prove's three
outcomes, the fail-closed undiscovered-group path, the input fence,
and the journal's terminal probe refusing to settle on
``Running == false`` alone. Falsification-marked tests record their
kill on a ``Kills:`` line and in ``tests/falsificationRegistry.py``.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vaibify.config import operationJournal
from vaibify.gui import terminalContainment
from vaibify.gui.terminalContainment import (
    TerminalContainmentError,
    TerminalExecutionRecord,
    fdictTerminateAndProveRecord,
    fnRegisterTerminalRecord,
    fsBuildGroupReportingCommand,
)
from vaibify.gui.terminalSession import TerminalSession

S_PROJECT = "termproj"
S_CONTAINER_ID = "termcid456"


# ---------------------------------------------------------------------
# Stub Docker connection with the containment-probe surface.
# ---------------------------------------------------------------------

class _StubContainmentConnection:
    """In-memory stand-in exposing the containment-probe methods."""

    def __init__(self, iMemberCount=0, bConclusive=True, bRunning=False):
        self.iMemberCount = iMemberCount
        self.bConclusive = bConclusive
        self.dictExecInspect = {"Running": bRunning}
        self.listSignals = []
        self.sMarkerPath = ""
        self.sMarkerContent = "7 7 7\n"
        self.dictJournalAtExecStart = None
        self.iSignalsUntilEmpty = -1

    def fsExecCreate(
        self, sContainerId, sCommand="/bin/bash", sUser=None,
        listCommand=None,
    ):
        if listCommand is not None:
            sWrapper = listCommand[2]
            iStart = sWrapper.index("/tmp/.vaibifyTerminalGroup.")
            self.sMarkerPath = sWrapper[iStart:].split("'")[0].split(
                ".partial",
            )[0]
        return "stub-terminal-exec"

    def fsocketExecStart(self, sExecId):
        self.dictJournalAtExecStart = (
            operationJournal.fdictReadJournalOutcome(S_PROJECT)
        )
        return SimpleNamespace(_sock=MagicMock(), close=lambda: None)

    def ftRunRootShellProbe(self, sContainerId, sScript):
        if self.sMarkerPath and self.sMarkerPath in sScript:
            return (0, self.sMarkerContent)
        return (1, "")

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        if not self.bConclusive:
            return {
                "bConclusive": False, "iMemberCount": -1,
                "sDetail": "stub inconclusive",
            }
        return {
            "bConclusive": True, "iMemberCount": self.iMemberCount,
            "sDetail": f"{self.iMemberCount} live member(s)",
        }

    def fnSignalProcessGroupMembers(
        self, sContainerId, iProcessGroup, sSignalName,
    ):
        self.listSignals.append(sSignalName)
        if self.iSignalsUntilEmpty >= 0 and (
            len(self.listSignals) >= self.iSignalsUntilEmpty
        ):
            self.iMemberCount = 0

    def fdictInspectExec(self, sExecId):
        return dict(self.dictExecInspect)


def _frecordBuildJournaled(
    connectionStub, iProcessGroup=777, appState=None,
):
    """Create a registered record backed by a real journal entry."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "terminal", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {
            "sDockerExecId": "stub-terminal-exec",
            "sDockerContainerId": S_CONTAINER_ID,
        },
    )
    if iProcessGroup > 0:
        operationJournal.fnAmendInFlightHolderIdentity(
            S_PROJECT, sOperationId,
            {"iHolderProcessGroup": iProcessGroup},
        )
    recordTerminal = TerminalExecutionRecord(
        sOperationId=sOperationId, sContainerName=S_PROJECT,
        sContainerId=S_CONTAINER_ID, sDockerExecId="stub-terminal-exec",
        iOwnerGeneration=1, connectionDocker=connectionStub,
        dictRegistry=None, iProcessGroup=iProcessGroup,
    )
    fnRegisterTerminalRecord(
        appState if appState is not None else SimpleNamespace(),
        recordTerminal,
    )
    return recordTerminal


def _fdictJournalOperations():
    return operationJournal.fdictReadJournalOutcome(S_PROJECT)[
        "dictOperations"
    ]


# ---------------------------------------------------------------------
# The group-reporting wrapper.
# ---------------------------------------------------------------------

def test_wrapper_reports_group_then_execs_the_shell():
    sCommand = fsBuildGroupReportingCommand("/bin/bash", "/tmp/.m1")
    assert "/proc/self/stat" in sCommand
    assert sCommand.endswith("exec /bin/bash")
    assert "> /tmp/.m1.partial" in sCommand
    assert "mv /tmp/.m1.partial /tmp/.m1" in sCommand


def test_wrapper_refuses_shell_metacharacters():
    with pytest.raises(TerminalContainmentError):
        fsBuildGroupReportingCommand("/bin/bash; rm -rf /", "/tmp/.m1")


def test_marker_parse_requires_a_session_leader():
    assert terminalContainment._fiParseLeaderFromMarker("42 42 42\n") == 42
    with pytest.raises(TerminalContainmentError):
        terminalContainment._fiParseLeaderFromMarker("42 42 7\n")
    with pytest.raises(TerminalContainmentError):
        terminalContainment._fiParseLeaderFromMarker("nonsense\n")


# ---------------------------------------------------------------------
# The journaled start split (create -> journal -> start -> discover).
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_start_journals_the_exec_id_before_exec_start():
    """The terminal exec id is durable BEFORE the exec starts.

    The create → journal → start split (design §8 applied to
    terminals): a crash between the two leaves an identified,
    probeable record, never a writer nobody can name.

    Kills: moving ``fnPromoteTerminalOperation`` after
    ``fsocketExecStart`` in ``TerminalSession._fnStartContained``.
    """
    connectionStub = _StubContainmentConnection()
    appState = SimpleNamespace()
    session = TerminalSession(
        connectionStub, S_CONTAINER_ID, sUser="testuser",
        dictContainment={
            "appState": appState, "sContainerName": S_PROJECT,
            "iOwnerGeneration": 3,
        },
    )
    session.fnStart()
    dictAtStart = connectionStub.dictJournalAtExecStart["dictOperations"]
    assert len(dictAtStart) == 1
    dictRecord = next(iter(dictAtStart.values()))
    assert dictRecord["sState"] == "IN_FLIGHT"
    assert dictRecord["sDockerExecId"] == "stub-terminal-exec"
    assert dictRecord["iOwnerGeneration"] == 3
    assert session.recordContainment.iProcessGroup == 7
    dictAfter = _fdictJournalOperations()
    assert next(iter(dictAfter.values()))["iHolderProcessGroup"] == 7
    assert appState.dictTerminalExecutionRecords[S_PROJECT]


def test_start_refuses_a_quarantined_container():
    """A container with an unreconciled record admits no new terminal."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "terminal", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "old-exec"},
    )
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_PROJECT, sOperationId, sNote="left by a crash",
    )
    session = TerminalSession(
        _StubContainmentConnection(), S_CONTAINER_ID,
        dictContainment={
            "appState": SimpleNamespace(), "sContainerName": S_PROJECT,
            "iOwnerGeneration": 1,
        },
    )
    with pytest.raises(TerminalContainmentError, match="quarantined"):
        session.fnStart()


def test_failed_discovery_of_a_live_shell_quarantines_and_raises():
    """No marker + a live exec = an uncontainable shell: fail closed."""
    connectionStub = _StubContainmentConnection(bRunning=True)
    connectionStub.sMarkerContent = ""

    def fnNeverFindMarker(sContainerId, sScript):
        return (1, "")
    connectionStub.ftRunRootShellProbe = fnNeverFindMarker
    session = TerminalSession(
        connectionStub, S_CONTAINER_ID,
        dictContainment={
            "appState": SimpleNamespace(), "sContainerName": S_PROJECT,
            "iOwnerGeneration": 1,
        },
    )
    with pytest.raises(TerminalContainmentError):
        with _fnPatchedDiscoveryTimeout(0.05):
            session.fnStart()
    dictOperations = _fdictJournalOperations()
    assert len(dictOperations) == 1
    assert next(iter(dictOperations.values()))["sState"] == (
        "NEEDS_RECONCILIATION"
    )


def _fnPatchedDiscoveryTimeout(fSeconds):
    from unittest.mock import patch
    return patch.object(
        terminalContainment, "F_GROUP_DISCOVERY_TIMEOUT_SECONDS", fSeconds,
    )


# ---------------------------------------------------------------------
# Terminate-and-prove: the three outcomes.
# ---------------------------------------------------------------------

def test_proven_empty_group_settles_the_journal_record():
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState = SimpleNamespace()
    recordTerminal = _frecordBuildJournaled(
        connectionStub, appState=appState,
    )
    dictOutcome = fdictTerminateAndProveRecord(
        recordTerminal, fTerminateWaitSeconds=0.01, fKillWaitSeconds=0.01,
    )
    assert dictOutcome["bProvenEmpty"] is True
    assert _fdictJournalOperations() == {}
    assert not terminalContainment.fbContainerHasLiveTerminalRecords(
        appState, S_PROJECT,
    )


@pytest.mark.falsification
def test_surviving_group_member_quarantines_never_settles():
    """A member that survives TERM and KILL retains-and-quarantines.

    Design v13 §6.1: when complete termination cannot be demonstrated
    the record quarantines — never an optimistic proceed.

    Kills: treating an inconclusive/non-empty final probe as proven
    empty in ``fdictTerminateAndProveRecord`` (the
    ``_fbProbeProvesEmpty`` guard inverted).
    """
    connectionStub = _StubContainmentConnection(iMemberCount=2)
    recordTerminal = _frecordBuildJournaled(connectionStub)
    dictOutcome = fdictTerminateAndProveRecord(
        recordTerminal, fTerminateWaitSeconds=0.01, fKillWaitSeconds=0.01,
    )
    assert dictOutcome["bProvenEmpty"] is False
    assert connectionStub.listSignals == ["TERM", "KILL"]
    dictOperations = _fdictJournalOperations()
    assert next(iter(dictOperations.values()))["sState"] == (
        "NEEDS_RECONCILIATION"
    )


def test_inconclusive_probe_quarantines():
    connectionStub = _StubContainmentConnection(bConclusive=False)
    recordTerminal = _frecordBuildJournaled(connectionStub)
    dictOutcome = fdictTerminateAndProveRecord(
        recordTerminal, fTerminateWaitSeconds=0.01, fKillWaitSeconds=0.01,
    )
    assert dictOutcome["bProvenEmpty"] is False
    dictOperations = _fdictJournalOperations()
    assert next(iter(dictOperations.values()))["sState"] == (
        "NEEDS_RECONCILIATION"
    )


def test_group_dying_after_kill_settles():
    connectionStub = _StubContainmentConnection(iMemberCount=1)
    connectionStub.iSignalsUntilEmpty = 2
    recordTerminal = _frecordBuildJournaled(connectionStub)
    dictOutcome = fdictTerminateAndProveRecord(
        recordTerminal, fTerminateWaitSeconds=0.01, fKillWaitSeconds=0.5,
    )
    assert dictOutcome["bProvenEmpty"] is True
    assert connectionStub.listSignals == ["TERM", "KILL"]
    assert _fdictJournalOperations() == {}


def test_drain_fences_the_session_input():
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    recordTerminal = _frecordBuildJournaled(connectionStub)
    session = TerminalSession(connectionStub, S_CONTAINER_ID)
    session._bRunning = True
    session._socketExec = SimpleNamespace(_sock=MagicMock())
    recordTerminal.session = session
    fdictTerminateAndProveRecord(
        recordTerminal, fTerminateWaitSeconds=0.01, fKillWaitSeconds=0.01,
    )
    session.fnSendInput(b"echo leaked\n")
    session._socketExec._sock.sendall.assert_not_called()
    session.fnLiftInputFence()
    session.fnSendInput(b"ls\n")
    session._socketExec._sock.sendall.assert_called_once()


def test_undiscovered_group_settles_only_a_dead_exec():
    """No recorded group: a dead exec settles, a live one quarantines."""
    connectionDead = _StubContainmentConnection(bRunning=False)
    recordDead = _frecordBuildJournaled(connectionDead, iProcessGroup=0)
    dictOutcome = fdictTerminateAndProveRecord(recordDead)
    assert dictOutcome["bProvenEmpty"] is True
    assert _fdictJournalOperations() == {}

    connectionLive = _StubContainmentConnection(bRunning=True)
    recordLive = _frecordBuildJournaled(connectionLive, iProcessGroup=0)
    dictOutcome = fdictTerminateAndProveRecord(recordLive)
    assert dictOutcome["bProvenEmpty"] is False
    dictOperations = _fdictJournalOperations()
    assert next(iter(dictOperations.values()))["sState"] == (
        "NEEDS_RECONCILIATION"
    )


def test_drain_session_record_ignores_test_doubles():
    assert terminalContainment.fdictDrainSessionRecord(MagicMock()) is None


# ---------------------------------------------------------------------
# The journal's terminal probe (case 43, unit half).
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_terminal_probe_refuses_to_settle_on_exec_dead_alone():
    """``Running == false`` with a non-empty group quarantines.

    The exact codex-round-12 hole: a detached, signal-trapping
    descendant survives the exec, so the terminal kind must prove the
    GROUP empty, not the exec dead. The real-container half is
    ``testTerminalContainmentLive.py`` (case 43).

    Kills: making ``_fdictProbeTerminalOperation`` return the exec
    probe's settled verdict without the group-emptiness probe.
    """
    _frecordBuildJournaled(_StubContainmentConnection())
    connectionStub = _StubContainmentConnection(
        iMemberCount=1, bRunning=False,
    )
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_PROJECT, connectionDocker=connectionStub,
    )
    assert dictResolution["sResolution"] == "QUARANTINED"
    assert "outlived" in dictResolution["sQuarantineReason"]


def test_terminal_probe_settles_on_dead_exec_and_empty_group():
    _frecordBuildJournaled(_StubContainmentConnection())
    connectionStub = _StubContainmentConnection(
        iMemberCount=0, bRunning=False,
    )
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_PROJECT, connectionDocker=connectionStub,
    )
    assert dictResolution["sResolution"] == "SETTLED"
    assert _fdictJournalOperations() == {}


def test_terminal_probe_reports_a_live_exec_as_busy():
    _frecordBuildJournaled(_StubContainmentConnection())
    connectionStub = _StubContainmentConnection(bRunning=True)
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_PROJECT, connectionDocker=connectionStub,
    )
    assert dictResolution["sResolution"] == "BUSY"


def test_terminal_probe_without_group_is_a_determinate_negative():
    _frecordBuildJournaled(
        _StubContainmentConnection(), iProcessGroup=0,
    )
    connectionStub = _StubContainmentConnection(
        iMemberCount=0, bRunning=False,
    )
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_PROJECT, connectionDocker=connectionStub,
    )
    assert dictResolution["sResolution"] == "QUARANTINED"
    assert "never learned" in dictResolution["sQuarantineReason"]


# ---------------------------------------------------------------------
# The journal is a per-container SET (case 45, unit half).
# ---------------------------------------------------------------------

def test_settling_one_terminal_leaves_sibling_records_live():
    """Two terminals and a pipeline exec coexist; one settle is one."""
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState = SimpleNamespace()
    recordFirst = _frecordBuildJournaled(
        connectionStub, iProcessGroup=701, appState=appState,
    )
    _frecordBuildJournaled(
        connectionStub, iProcessGroup=702, appState=appState,
    )
    sExecOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "exec", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sExecOperationId, {"sDockerExecId": "pipeline-exec"},
    )
    assert len(_fdictJournalOperations()) == 3
    fdictTerminateAndProveRecord(
        recordFirst, fTerminateWaitSeconds=0.01, fKillWaitSeconds=0.01,
    )
    dictRemaining = _fdictJournalOperations()
    assert len(dictRemaining) == 2
    assert recordFirst.sOperationId not in dictRemaining
    assert sExecOperationId in dictRemaining
    assert terminalContainment.fbContainerHasLiveTerminalRecords(
        appState, S_PROJECT,
    )


# ---------------------------------------------------------------------
# Journal amendment rules.
# ---------------------------------------------------------------------

def test_amend_never_rewrites_a_recorded_identity():
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "terminal", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "exec-a"},
    )
    operationJournal.fnAmendInFlightHolderIdentity(
        S_PROJECT, sOperationId, {"iHolderProcessGroup": 41},
    )
    with pytest.raises(operationJournal.OperationJournalRecordError):
        operationJournal.fnAmendInFlightHolderIdentity(
            S_PROJECT, sOperationId, {"iHolderProcessGroup": 42},
        )
    with pytest.raises(operationJournal.OperationJournalRecordError):
        operationJournal.fnAmendInFlightHolderIdentity(
            S_PROJECT, sOperationId, {"sNote": "not an identity"},
        )


def test_amend_refuses_a_prepared_record():
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "terminal", S_CONTAINER_ID,
    )
    with pytest.raises(operationJournal.OperationJournalRecordError):
        operationJournal.fnAmendInFlightHolderIdentity(
            S_PROJECT, sOperationId, {"iHolderProcessGroup": 41},
        )


# ---------------------------------------------------------------------
# Wiring: the authority-ending paths drive terminate-and-prove.
# ---------------------------------------------------------------------

def _fappStateBuildOwned(connectionStub, sLeaseId="lease-t1"):
    """Return an appState owning S_PROJECT with one live terminal."""
    from vaibify.gui.containerOwnership import OwnerRecord
    recordOwner = OwnerRecord(
        sLeaseId=sLeaseId, fileHandleLock=None,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="sess-t1",
    )
    appState = SimpleNamespace(
        dictContainerOwners={S_PROJECT: recordOwner},
        dictSessionOwner={}, dictMutationSupervisors={},
        dictDurableTaskRecords={},
    )
    recordTerminal = _frecordBuildJournaled(
        connectionStub, appState=appState,
    )
    return appState, recordOwner, recordTerminal


def test_release_drains_terminals_before_committing():
    """A permitted release terminates-and-proves every terminal (§10)."""
    import asyncio
    from vaibify.gui import sessionLifecycle
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState, _, _ = _fappStateBuildOwned(connectionStub)
    bReleased = asyncio.run(
        sessionLifecycle.fbReleaseExplicit(
            appState, S_PROJECT, "lease-t1",
            sBrowserSessionId="sess-t1",
        ),
    )
    assert bReleased is True
    assert connectionStub.listSignals == ["TERM"]
    assert _fdictJournalOperations() == {}
    assert not terminalContainment.fbContainerHasLiveTerminalRecords(
        appState, S_PROJECT,
    )


def test_refused_release_never_touches_the_owners_terminals():
    """An unauthorized release attempt drains nothing."""
    import asyncio
    from vaibify.gui import sessionLifecycle
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState, _, recordTerminal = _fappStateBuildOwned(connectionStub)
    bReleased = asyncio.run(
        sessionLifecycle.fbReleaseExplicit(
            appState, S_PROJECT, "copied-lease",
            sBrowserSessionId="sess-attacker",
        ),
    )
    assert bReleased is False
    assert connectionStub.listSignals == []
    assert recordTerminal.sState == "live"
    assert len(_fdictJournalOperations()) == 1


def test_release_quarantines_an_unprovable_terminal_and_proceeds():
    """Release retains-and-quarantines when the group cannot be proven."""
    import asyncio
    from unittest.mock import patch
    from vaibify.gui import sessionLifecycle
    connectionStub = _StubContainmentConnection(iMemberCount=2)
    appState, _, _ = _fappStateBuildOwned(connectionStub)
    with patch.object(
        terminalContainment, "F_TERMINATE_WAIT_SECONDS", 0.01,
    ), patch.object(terminalContainment, "F_KILL_WAIT_SECONDS", 0.01):
        bReleased = asyncio.run(
            sessionLifecycle.fbReleaseExplicit(
                appState, S_PROJECT, "lease-t1",
                sBrowserSessionId="sess-t1",
            ),
        )
    assert bReleased is True
    dictOperations = _fdictJournalOperations()
    assert next(iter(dictOperations.values()))["sState"] == (
        "NEEDS_RECONCILIATION"
    )


def test_reaper_drains_terminals_of_a_reapable_owner():
    """The reaper settles a dead session's terminal before releasing."""
    import time as moduleTime
    from vaibify.gui import serverLifespan
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState, recordOwner, _ = _fappStateBuildOwned(connectionStub)
    appState.bReapOwnerships = True
    recordOwner.fLastSeenMonotonic = moduleTime.monotonic() - 99999.0
    app = SimpleNamespace(state=appState)
    dictCtx = {"docker": MagicMock(flistGetRunningContainers=lambda: [])}
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT not in appState.dictContainerOwners
    assert _fdictJournalOperations() == {}
    assert connectionStub.listSignals == ["TERM"]


def test_reaper_leaves_a_live_sessions_terminal_alone():
    """A connected owner is not reapable; its terminal is untouched."""
    from vaibify.gui import serverLifespan
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState, recordOwner, recordTerminal = _fappStateBuildOwned(
        connectionStub,
    )
    appState.bReapOwnerships = True
    recordOwner.iLiveConnectionCount = 1
    app = SimpleNamespace(state=appState)
    dictCtx = {"docker": MagicMock(flistGetRunningContainers=lambda: [])}
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT in appState.dictContainerOwners
    assert recordTerminal.sState == "live"
    assert connectionStub.listSignals == []


def test_shutdown_drain_hook_terminates_live_terminals():
    """The lifespan drain hook proves every terminal group dead (§8)."""
    import asyncio
    from vaibify.gui import appFactory
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState, _, _ = _fappStateBuildOwned(connectionStub)
    appState.listLifespanShutdown = []
    appState.bMutationAdmissionsClosed = False
    app = SimpleNamespace(state=appState)
    appFactory._fnRegisterShutdownDrainGuardedMutations(app)
    asyncio.run(appState.listLifespanShutdown[0](app))
    assert _fdictJournalOperations() == {}
    assert connectionStub.listSignals == ["TERM"]


@pytest.mark.falsification
def test_shutdown_retains_the_flock_of_a_live_terminal_container():
    """A still-live terminal record keeps its container's flock held.

    Design §8 (case 44, shutdown half): the flock-release hook must
    skip a container whose terminal group may still write, exactly as
    it skips live mutation work.

    Kills: dropping ``fsetNamesWithLiveTerminalRecords`` from the
    retained-name union in ``appFactory.fnReleaseAllContainerLocks``.
    """
    import asyncio
    from vaibify.gui import appFactory
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    appState, _, _ = _fappStateBuildOwned(connectionStub)
    appState.listLifespanShutdown = []
    app = SimpleNamespace(state=appState)
    appFactory._fnRegisterHubShutdownReleaseLocks(app)
    asyncio.run(appState.listLifespanShutdown[0](app))
    assert S_PROJECT in appState.dictContainerOwners


@pytest.mark.falsification
def test_socket_close_drains_the_containment_record():
    """Closing the terminal WebSocket terminates-and-proves (§7).

    A closed socket is not a dead terminal: the run loop's teardown
    must drain the containment record, not merely send exit
    keystrokes and close the socket.

    Kills: dropping the ``fdictDrainSessionRecord`` call from
    ``pipelineServer.fnRunTerminalSession``'s ``finally``.
    """
    import asyncio
    from unittest.mock import AsyncMock
    from vaibify.gui import pipelineServer
    connectionStub = _StubContainmentConnection(iMemberCount=0)
    recordTerminal = _frecordBuildJournaled(connectionStub)
    session = TerminalSession(connectionStub, S_CONTAINER_ID)
    session._bRunning = True
    session._socketExec = SimpleNamespace(
        _sock=MagicMock(), close=lambda: None,
    )
    recordTerminal.session = session
    session.recordContainment = recordTerminal
    websocketFake = AsyncMock()
    websocketFake.receive = AsyncMock(
        return_value={"type": "websocket.disconnect"},
    )
    asyncio.run(
        pipelineServer.fnRunTerminalSession(session, websocketFake, {}),
    )
    assert recordTerminal.sState == "settled"
    assert _fdictJournalOperations() == {}
