"""Legacy terminal records survive the withdrawal as quarantines.

Withdrawing the terminal route stops new terminal executions. It does
NOT stop old ones: a journal record written before the upgrade is on
disk, and the process it describes may still be running inside a
container that is still up. The dangerous move would be to treat "the
feature is gone" as "the record is finished" — that clears a quarantine
without proving anything, which is the exact class of lie the operation
journal exists to prevent.

So four properties hold together, and this file drives them against
each other rather than one at a time:

1. no NEW terminal record can be created (the parking controls in
   ``testArchitecturalInvariants`` cover the structural half);
2. an existing record keeps its container QUARANTINED;
3. it clears only when the container is positively stopped or the
   process group is proven empty — i.e. through reconciliation; and
4. dialling the withdrawn route does not settle it.

The user-visible cost is real and is release-note material: anyone
upgrading with a live terminal record finds that container quarantined
and needing ``vaibify reconcile``.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vaibify.config import operationJournal
from vaibify.gui import containerOwnership, webSocketAuthorization
from vaibify.gui.routes.terminalRoutes import _fnRegisterTerminalWs


S_CONTAINER_NAME = "LegacyTerminalProject"
S_CONTAINER_ID = "legacydockerid789"
S_EXEC_ID = "legacy-terminal-exec"
I_LEGACY_PROCESS_GROUP = 4242


class _StubProbeConnection:
    """A Docker stand-in that answers the terminal probe's questions.

    ``iMemberCount`` is what an in-container probe of the recorded
    process group would report; ``bRunning`` is the exec's own state.
    """

    def __init__(self, iMemberCount=1, bRunning=False, bConclusive=True):
        self.iMemberCount = iMemberCount
        self.bRunning = bRunning
        self.bConclusive = bConclusive

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID, "sName": S_CONTAINER_NAME,
        }]

    def fdictInspectExec(self, sExecId):
        return {"Running": self.bRunning}

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        return {
            "bConclusive": self.bConclusive,
            "iMemberCount": self.iMemberCount,
        }


def _fsWriteLegacyTerminalRecord(iProcessGroup=I_LEGACY_PROCESS_GROUP):
    """Write the on-disk journal record an upgrade would inherit."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "terminal", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sOperationId, {
            "sDockerExecId": S_EXEC_ID,
            "sDockerContainerId": S_CONTAINER_ID,
        },
    )
    operationJournal.fnAmendInFlightHolderIdentity(
        S_CONTAINER_NAME, sOperationId,
        {"iHolderProcessGroup": iProcessGroup},
    )
    return sOperationId


def _fdictJournalOperations():
    return operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME,
    )["dictOperations"]


def test_legacy_record_keeps_the_container_quarantined():
    """A surviving process group quarantines, withdrawal notwithstanding."""
    _fsWriteLegacyTerminalRecord()
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
        connectionDocker=_StubProbeConnection(iMemberCount=1),
    )
    assert dictResolution["sResolution"] == "QUARANTINED"
    assert _fdictJournalOperations() != {}, (
        "the record must survive the resolution; a cleared record is a "
        "claimable container with a live shell still writing to it"
    )


def test_dialling_the_withdrawn_route_does_not_settle_the_record():
    """The refusal is inert: it cannot clear what it did not prove.

    This is the adversarial half. The withdrawn route and the operation
    journal are separate subsystems, and the tempting shortcut when
    parking a feature is to have the parking sweep its leftovers. Here
    the route is dialled with the container's own valid lease -- the
    most privileged caller there is -- and the record must be exactly as
    it was, byte for byte, afterwards.
    """
    sOperationId = _fsWriteLegacyTerminalRecord()
    dictBefore = dict(_fdictJournalOperations()[sOperationId])

    dictCtx = {
        "docker": _StubProbeConnection(),
        "dictContainerOwners": {
            S_CONTAINER_NAME: containerOwnership.OwnerRecord(
                sLeaseId="owning-lease", fileHandleLock=None,
                sAgentToken="agent-token", sContainerId=S_CONTAINER_ID,
                sBrowserSessionId="browser-session-1",
            ),
        },
        "dictBrowserSessions": {},
        "require": lambda: None,
    }
    app = FastAPI()
    _fnRegisterTerminalWs(app, dictCtx)
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/terminal/{S_CONTAINER_ID}"
        "?sToken=any&sLeaseId=owning-lease",
        headers={"origin": "http://localhost"},
    ) as websocketClient:
        with pytest.raises(WebSocketDisconnect) as excInfo:
            websocketClient.receive_text()

    assert excInfo.value.code == (
        webSocketAuthorization.I_REJECT_TERMINAL_DISABLED
    )
    assert _fdictJournalOperations()[sOperationId] == dictBefore, (
        "withdrawing the route must not silently settle, amend, or "
        "clear a record it has proven nothing about"
    )


def test_legacy_record_clears_on_a_proven_empty_group():
    """The one automatic exit: the exec is dead AND the group is empty."""
    _fsWriteLegacyTerminalRecord()
    dictProven = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
        connectionDocker=_StubProbeConnection(iMemberCount=0),
    )
    assert dictProven["sResolution"] == "SETTLED"
    assert _fdictJournalOperations() == {}


def test_a_quarantined_record_stays_quarantined_once_it_is_persisted():
    """A determinate negative is not re-litigated by a later probe.

    Once a surviving process group has been observed, the record is
    persisted ``NEEDS_RECONCILIATION``. A subsequent probe that happens
    to see an empty group must NOT auto-clear it: the group emptying
    later is consistent with a detached descendant having re-parented,
    exited, or simply moved, and "the second look was cleaner" is not
    proof about the first. Reconciliation -- which stops the container
    or proves it absent -- is the only exit.
    """
    _fsWriteLegacyTerminalRecord()
    dictFirst = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
        connectionDocker=_StubProbeConnection(iMemberCount=2),
    )
    assert dictFirst["sResolution"] == "QUARANTINED"

    dictSecond = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
        connectionDocker=_StubProbeConnection(iMemberCount=0),
    )
    assert dictSecond["sResolution"] == "QUARANTINED", (
        "a persisted determinate negative must not be cleared by a "
        "later, luckier probe"
    )
    assert _fdictJournalOperations() != {}


def test_an_inconclusive_probe_never_clears_the_legacy_record():
    """An unreachable prober is not evidence of absence.

    The upgrade case makes this sharper than usual: the container the
    record names may have been removed by hand between sessions, and a
    probe that cannot answer looks superficially like a probe that
    answered "nothing there". Only the second may clear.
    """
    _fsWriteLegacyTerminalRecord()
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
        connectionDocker=_StubProbeConnection(
            iMemberCount=0, bConclusive=False,
        ),
    )
    assert dictResolution["sResolution"] != "SETTLED"
    assert _fdictJournalOperations() != {}


def test_no_docker_connection_leaves_the_record_standing():
    """With no verifier at all the container stays quarantined."""
    _fsWriteLegacyTerminalRecord()
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME, connectionDocker=None,
    )
    assert dictResolution["sResolution"] != "SETTLED"
    assert _fdictJournalOperations() != {}


def test_the_registry_starts_empty_after_an_upgrade():
    """A restarted hub inherits the disk record, not the live handles.

    ``TerminalExecutionRecord`` handles live on ``app.state`` and die
    with the process, so after an upgrade there is nothing in memory to
    drain -- which is precisely why the durable journal record, and not
    the registry, is what keeps the container quarantined.
    """
    from vaibify.gui import terminalContainment

    appState = SimpleNamespace(
        dictTerminalExecutionRecords=(
            terminalContainment.fdictCreateTerminalRecordRegistry()
        ),
    )
    _fsWriteLegacyTerminalRecord()
    assert not terminalContainment.fbContainerHasLiveTerminalRecords(
        appState, S_CONTAINER_NAME,
    )
    assert _fdictJournalOperations() != {}


def test_no_terminal_process_can_be_created_through_the_route():
    """The alpha gate half of the setsid problem.

    ``testTerminalContainmentLive`` demonstrates that a ``setsid``
    descendant escapes the recorded process group and that the record
    then settles CLEAN over a live process -- the reason the terminal is
    disabled. That demonstration drives ``TerminalSession`` directly, on
    purpose, so it keeps proving the boundary is invalid no matter what
    the route does.

    This is the other half, and it is deliberately not the same test:
    the route cannot create such a process at all, because it never
    constructs a session. A ``TerminalSession`` that explodes on
    construction proves it -- a route that merely returned early would
    still pass a check of the response code.
    """
    from vaibify.gui.routes import terminalRoutes

    assert not hasattr(terminalRoutes, "TerminalSession"), (
        "terminalRoutes still holds a TerminalSession reference; the "
        "parking controls exist so the name is not even in scope, and "
        "a module that can name it can call it"
    )

    dictCtx = {"docker": _StubProbeConnection()}
    app = FastAPI()
    _fnRegisterTerminalWs(app, dictCtx)
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/terminal/{S_CONTAINER_ID}?sToken=x&sLeaseId=y",
        headers={"origin": "http://localhost"},
    ) as websocketClient:
        with pytest.raises(WebSocketDisconnect) as excInfo:
            websocketClient.receive_text()
    assert excInfo.value.code == (
        webSocketAuthorization.I_REJECT_TERMINAL_DISABLED
    )
