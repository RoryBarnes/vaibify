"""Kill-confirmed tests for the start reservation (design §13, slice 9).

Cases 19 (concurrent-start half), 21, 22, 24, 25, 28, 29, 40, and 41's
start transitions. Each drives the real reservation machinery against
real records, a real write-ahead journal, and — where the case is about
proving a process dead — a REAL child process, because the whole claim
under test is that a signal was delivered and an exit observed.

Every test here carries a ``Kills:`` line naming the mutation it was
proven to fail against, and a matching entry in
``tests/falsificationRegistry.py``.
"""

import asyncio
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.docker import containerManager
from vaibify.gui import (
    browserSession, commitCarrier, containerOwnership, sessionLifecycle,
    startReservation, startResultStore, terminalContainment,
)

pytestmark = pytest.mark.falsification

S_PROJECT_NAME = "ReservedProject"
S_OTHER_PROJECT_NAME = "OtherProject"
S_CONTAINER_ID = "cid-reserved-0123456789"
S_PARTIAL_CONTAINER_ID = "partialContainerId0123"

# A child that ignores SIGTERM and announces when its handler is armed,
# so a TERM sent afterwards proves something.
S_SIGNAL_IGNORING_CHILD = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "sys.stdout.write('ready\\n')\n"
    "sys.stdout.flush()\n"
    "time.sleep(60)\n"
)


@pytest.fixture(autouse=True)
def fixtureIsolateLockDirectory(tmp_path, monkeypatch):
    """Redirect the host flock directory to a per-test tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fstateBuildAppState():
    """Return a bare app.state stand-in with every store slice 9 uses."""
    return SimpleNamespace(
        dictContainerOwners={},
        dictSessionOwner=containerOwnership.fdictCreateSessionOwnerIndex(),
        dictSessionSockets=(
            containerOwnership.fdictCreateSessionSocketIndex()
        ),
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
        dictDurableTaskRecords=(
            commitCarrier.fdictCreateDurableTaskRegistry()
        ),
        dictTerminalExecutionRecords=(
            terminalContainment.fdictCreateTerminalRecordRegistry()
        ),
        dictStartResults=startResultStore.fdictCreateStartResultStore(),
    )


def _fsRedeemSession(stateApp):
    """Return a genuine browser session id from a real redemption."""
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    sSessionId, _ = browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )
    return sSessionId


class ControlledStart:
    """A create-then-start substitute the test drives step by step.

    It behaves like the real worker at the points that matter: it
    registers a live launch process with the reservation's task record
    (so a cancel has something to signal), records a created container
    id when told to, and honours the cancel flag at a step boundary.
    """

    def __init__(self, bCreateContainer=True, bSpawnRealProcess=True):
        self.eventEntered = threading.Event()
        self.eventRelease = threading.Event()
        self.bCreateContainer = bCreateContainer
        self.bSpawnRealProcess = bSpawnRealProcess
        self.processChild = None

    def fsExecute(self, sName, reservation, configProject):
        recordTask = reservation.recordStartTask
        if self.bSpawnRealProcess:
            self.processChild = subprocess.Popen(
                [sys.executable, "-c", S_SIGNAL_IGNORING_CHILD],
                stdout=subprocess.PIPE, text=True,
            )
            assert self.processChild.stdout.readline().strip() == "ready"
            recordTask.fnAdoptProcess(self.processChild)
        if self.bCreateContainer:
            recordTask.sCreatedContainerId = S_PARTIAL_CONTAINER_ID
        self.eventEntered.set()
        self.eventRelease.wait(timeout=20.0)
        if recordTask.bCancelRequested:
            raise startReservation.StartCancelledError(
                "the start was cancelled"
            )
        return S_CONTAINER_ID

    def fnTerminateForTest(self):
        """Release the held worker so it can unwind after a cancel."""
        self.eventRelease.set()


class RecordingDaemon:
    """A declared, fail-closed stand-in for the docker settlement calls.

    It answers only the two questions the settlement asks, and records
    what it was asked to remove, so "only the labelled container is
    removed" is an observation rather than an assumption.
    """

    def __init__(self, listPresentIds, bAnswered=True):
        self.listPresentIds = list(listPresentIds)
        self.bAnswered = bAnswered
        self.listRemoved = []

    def fdictSettle(self, sReservationId, bLaunchWasKilled):
        if not self.bAnswered:
            return {
                "bConclusive": False, "listRemovedContainerIds": [],
                "sDetail": "the Docker daemon did not answer",
            }
        self.listRemoved.extend(self.listPresentIds)
        listRemoved = list(self.listPresentIds)
        self.listPresentIds = []
        return {
            "bConclusive": True, "listRemovedContainerIds": listRemoved,
            "sDetail": "no container bearing the reservation label remains",
        }


async def _fnBeginHeldStart(
    stateApp, monkeypatch, controller, sSessionId, sName=S_PROJECT_NAME,
):
    """Begin a start whose Docker half is held open, and return its body."""
    monkeypatch.setattr(
        startReservation, "_fsExecuteReservedStart", controller.fsExecute,
    )
    iCode, dictBody = await startReservation.ftBeginStart(
        stateApp, sName, sSessionId, SimpleNamespace(bNeverSleep=False),
        iPort=0,
    )
    assert iCode == 202, dictBody
    await _fnAwaitEventSet(controller.eventEntered)
    return dictBody


async def _fnAwaitEventSet(eventWaited, fTimeoutSeconds=10.0):
    """Wait for a worker-thread event WITHOUT blocking the event loop.

    A blocking ``Event.wait()`` here would stall the very loop that has
    to schedule the durable task, so the worker could never enter.
    """
    fDeadline = time.monotonic() + fTimeoutSeconds
    while not eventWaited.is_set():
        assert time.monotonic() < fDeadline, (
            "the start worker never reached its held step"
        )
        await asyncio.sleep(0.01)


async def _fnAwaitDurableTask(stateApp, sName=S_PROJECT_NAME):
    """Await the container's durable start task, tolerating its failure."""
    recordTask = stateApp.dictDurableTaskRecords.get(sName)
    if recordTask is None:
        return
    try:
        await asyncio.wait_for(recordTask.taskAsync, 20.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass


# ------------------------------------------------------------------
# Case 21 — the initiator cancels a stale-heartbeat start.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testInitiatorCancelOfAStaleStartKillsAndFreesTheContainer():
    """Case 21: TERM→KILL, exit confirmed, partial container removed.

    The whole hung-start exit, end to end, with a REAL SIGTERM-ignoring
    child standing in for the wedged docker CLI: the heartbeat is stale,
    the initiator cancels, the launch is escalated to KILL and confirmed
    exited, the partial container is removed by reservation label, the
    reservation is compare-and-deleted, and the container is claimable
    again afterwards. A mocked process would confirm whatever the code
    did; this one only dies if the escalation is real.

    Kills: in containerManager.fdictTerminateDockerProcess, replace the
    KILL escalation with ``return _fdictTerminationOutcome(processDocker,
    True, False)`` after the timeout — the SIGTERM-ignoring launch is
    then reported terminated while it is still alive, the settlement
    turns inconclusive, and the container is quarantined instead of
    claimable.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart()
    daemon = RecordingDaemon([S_PARTIAL_CONTAINER_ID])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            daemon.fdictSettle,
        )
        dictStart = await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sSessionId,
        )
        recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
        reservation = recordOwner.reservation
        reservation.fHeartbeatMonotonic = time.monotonic() - 10000.0
        assert startReservation.fbReservationHeartbeatIsStale(reservation)
        controller.eventRelease.set()
        iCode, dictCancel = await startReservation.ftCancelStart(
            stateApp, S_PROJECT_NAME, sSessionId,
            sReservationId=dictStart["sReservationId"],
        )
        await _fnAwaitDurableTask(stateApp)
    assert iCode == 200, dictCancel
    assert controller.processChild.poll() is not None, (
        "the launch process was not confirmed exited before cleanup"
    )
    assert daemon.listRemoved == [S_PARTIAL_CONTAINER_ID], (
        "the partial container was not removed by its reservation label"
    )
    assert S_PROJECT_NAME not in stateApp.dictContainerOwners, (
        "a cancelled start must free the container it reserved"
    )
    recordResult = stateApp.dictStartResults[dictStart["sReservationId"]]
    assert recordResult.sState == startResultStore.S_RESULT_FAILED
    assert recordResult.bQuarantined is False


# ------------------------------------------------------------------
# Case 22 — only the owning session may cancel.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testCancelFromANonOwningSessionIsRefused():
    """Case 22: a foreign session may not kill another's start.

    Cancellation destroys a container mid-creation, so it is exactly as
    privileged as the start itself. A second browser session — a real
    ``BrowserSessionRecord``, not a forged id — must be refused, and the
    start must still be running afterwards.

    Kills: in startReservation._tMarkCancelRequested, drop the
    ``recordOwner.sBrowserSessionId not in ("", sBrowserSessionId)``
    refusal — any session can then kill any start.
    """
    stateApp = _fstateBuildAppState()
    sOwnerSessionId = _fsRedeemSession(stateApp)
    sForeignSessionId = _fsRedeemSession(stateApp)
    assert sOwnerSessionId != sForeignSessionId
    controller = ControlledStart()
    daemon = RecordingDaemon([S_PARTIAL_CONTAINER_ID])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            daemon.fdictSettle,
        )
        dictStart = await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sOwnerSessionId,
        )
        iCode, dictBody = await startReservation.ftCancelStart(
            stateApp, S_PROJECT_NAME, sForeignSessionId,
            sReservationId=dictStart["sReservationId"],
        )
        assert iCode == 403, dictBody
        recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
        assert recordOwner.reservation is not None, (
            "a refused cancel must leave the start running"
        )
        assert not recordOwner.reservation.recordStartTask.bCancelRequested
        assert controller.processChild.poll() is None, (
            "a refused cancel must not have signalled the launch"
        )
        controller.eventRelease.set()
        await _fnAwaitDurableTask(stateApp)


# ------------------------------------------------------------------
# Case 24 — cleanup inspects and removes the partial container first.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testCancelAfterPartialCreationRemovesItBeforeClearingTheRecord():
    """Case 24: the partial container goes before the reservation does.

    Ordering is the safety argument: clearing the reservation first
    would leave a labelled, unowned container behind with nothing left
    pointing at it. The settlement is therefore observed to run BEFORE
    the record is cleared, and the reservation is still attached at that
    moment.

    Kills: in startReservation._fnSettleStartFailure, move the
    ``ftSettleFailedStartOwnership`` commit ahead of the
    ``fdictSettleReservationContainers`` call — the record is then
    cleared and the flock freed while the partial container is still
    being removed.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart()
    listObservations = []

    def _fdictSettleObserving(sReservationId, bLaunchWasKilled):
        recordOwner = stateApp.dictContainerOwners.get(S_PROJECT_NAME)
        listObservations.append({
            "sReservationId": sReservationId,
            "bReservationStillAttached": (
                recordOwner is not None and recordOwner.reservation
                is not None
            ),
        })
        return {
            "bConclusive": True,
            "listRemovedContainerIds": [S_PARTIAL_CONTAINER_ID],
            "sDetail": "the labelled container was removed",
        }

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            _fdictSettleObserving,
        )
        dictStart = await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sSessionId,
        )
        controller.eventRelease.set()
        await startReservation.ftCancelStart(
            stateApp, S_PROJECT_NAME, sSessionId,
        )
        await _fnAwaitDurableTask(stateApp)
    assert listObservations, "the cleanup never ran"
    assert listObservations[0]["sReservationId"] == (
        dictStart["sReservationId"]
    ), "cleanup must key on the reservation id, not the container name"
    assert listObservations[0]["bReservationStillAttached"] is True, (
        "the reservation was cleared before the partial container was "
        "removed, orphaning a labelled container"
    )
    assert S_PROJECT_NAME not in stateApp.dictContainerOwners


# ------------------------------------------------------------------
# Case 25 — a stale cleanup callback cannot delete a newer reservation.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testAStaleSettlementCannotDeleteANewerReservation():
    """Case 25: compare-and-delete on the reservation id, never ABA.

    A settlement callback from an earlier attempt arrives after a second
    start has already reserved the container. It may publish its own
    outcome, but it must not clear the successor's reservation or free
    the flock the successor holds — the exact ABA delete the stable id
    exists to prevent.

    Kills: in startReservation._fbCommitFailedStart, replace the
    ``recordOwner.reservation is reservation`` compare with
    ``recordOwner.reservation is not None`` — the stale settlement then
    deletes the live reservation and releases a container mid-start.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart()
    daemon = RecordingDaemon([])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            daemon.fdictSettle,
        )
        dictFirst = await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sSessionId,
        )
        recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
        reservationStale = recordOwner.reservation
        # A newer attempt takes the record over, exactly as a retry
        # after the first attempt's response was lost would.
        reservationNewer = startReservation.StartReservation(
            sReservationId="c" * 32,
            recordStartTask=startReservation.StartTaskRecord(
                sStartTaskId="newerTask",
                sJournalOperationId=(
                    reservationStale.recordStartTask.sJournalOperationId
                ),
            ),
        )
        recordOwner.reservation = reservationNewer
        # The stale attempt is ending (cancelled), so its settlement
        # callback runs against a record a NEWER reservation now owns.
        reservationStale.recordStartTask.bCancelRequested = True
        controller.eventRelease.set()
        await _fnAwaitDurableTask(stateApp)
    assert recordOwner.reservation is reservationNewer, (
        "a stale settlement deleted a NEWER reservation (the ABA delete)"
    )
    assert S_PROJECT_NAME in stateApp.dictContainerOwners, (
        "a stale settlement released a container a newer start holds"
    )
    recordResult = stateApp.dictStartResults[dictFirst["sReservationId"]]
    assert recordResult.sState == startResultStore.S_RESULT_FAILED


# ------------------------------------------------------------------
# Case 29 — cleanup keys on the label, and an uncertain daemon
# quarantines rather than releasing.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testAnInconclusiveSettlementQuarantinesInsteadOfReleasing():
    """Case 29: uncertain means quarantined, never claimable.

    Killing the CLI does not prove the daemon abandoned the request. So
    when the settlement cannot answer, the container keeps its flock,
    its journal record is poisoned so the quarantine survives this
    process, the owner record is poisoned so no claim, transfer,
    release, or reap can touch it in this one, and the researcher is
    told to run ``vaibify reconcile``.

    Kills: in startReservation._fbCommitFailedStart, take the
    journal-settling branch unconditionally (``if True:`` in place of
    ``if dictSettlement["bConclusive"]:``) — an uncertain container then
    settles its write-ahead record and is left unpoisoned, so both this
    hub and the next treat it as clean while the daemon may still be
    creating it. (The neighbouring release predicate is a second layer:
    a poisoned record refuses release anyway, so mutating it alone is
    not observable — this test is honest about which guard it proves.)
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart()
    daemon = RecordingDaemon([], bAnswered=False)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            daemon.fdictSettle,
        )
        dictStart = await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sSessionId,
        )
        sOperationId = stateApp.dictContainerOwners[
            S_PROJECT_NAME
        ].reservation.recordStartTask.sJournalOperationId
        controller.eventRelease.set()
        await startReservation.ftCancelStart(
            stateApp, S_PROJECT_NAME, sSessionId,
        )
        await _fnAwaitDurableTask(stateApp)
    recordOwner = stateApp.dictContainerOwners.get(S_PROJECT_NAME)
    assert recordOwner is not None, (
        "an unproven container must NOT be released; its flock is what "
        "keeps a second owner off it"
    )
    assert recordOwner.poison is not None
    assert containerOwnership.fbOwnerIsReapable(recordOwner) is False
    iCodeClaim, dictClaim = containerOwnership.ftClaim(
        stateApp.dictContainerOwners, S_PROJECT_NAME, "", 0,
    )
    assert iCodeClaim == 409 and dictClaim.get("bPoisoned") is True
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        S_PROJECT_NAME,
    )
    assert dictOutcomeRead["dictOperations"][sOperationId]["sState"] == (
        operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
    ), "the quarantine must survive this hub, so it lives in the journal"
    recordResult = stateApp.dictStartResults[dictStart["sReservationId"]]
    assert recordResult.bQuarantined is True
    assert "reconcile" in recordResult.sSafeError


# ------------------------------------------------------------------
# Case 28 / 40 — the durable result outlives the reservation.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testAFailedStartIsRetrievableAfterOwnershipIsReleased():
    """Cases 28 and 40: a lost FAILED response is still recoverable.

    The response is lost, the reservation is gone, and the ownership has
    been released — so there is no owner record left to authorize
    anything. The initiating session must still learn WHY, through its
    bounded entitlement, and must gain no container authority in the
    process. A different session gets nothing at all.

    Kills: in startResultStore.fnCloseStartResult, return before writing
    the state (``if recordResult is None: return`` → ``return``) so no
    outcome is ever recorded — the initiating tab can then never learn
    that its start failed once ownership is gone.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    sOtherSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart(bCreateContainer=False)
    daemon = RecordingDaemon([])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            daemon.fdictSettle,
        )
        dictStart = await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sSessionId,
        )
        controller.eventRelease.set()
        await startReservation.ftCancelStart(
            stateApp, S_PROJECT_NAME, sSessionId,
        )
        await _fnAwaitDurableTask(stateApp)
    assert S_PROJECT_NAME not in stateApp.dictContainerOwners
    iCode, dictBody = startReservation.ftPollStartStatus(
        stateApp, S_PROJECT_NAME, sSessionId,
    )
    assert iCode == 200, dictBody
    assert dictBody["sState"] == startResultStore.S_RESULT_FAILED
    assert dictBody["sReservationId"] == dictStart["sReservationId"]
    assert "sLeaseId" not in dictBody and "sAgentToken" not in dictBody, (
        "a failure entitlement must convey no container authority"
    )
    iCodeOther, _ = startReservation.ftPollStartStatus(
        stateApp, S_PROJECT_NAME, sOtherSessionId,
    )
    assert iCodeOther == 403


@pytest.mark.asyncio
async def testALostSuccessResponseStillYieldsTheOwnerDerivedLease():
    """Case 28 (success half): the lease is derived, never stored.

    The tab that requested the start never saw the response. Polling
    again must hand back the lease the OWNER RECORD holds right now —
    which is what makes the delivery transfer-safe — and never a value
    copied into the result at settlement time.

    Kills: in startReservation._tDeliverSucceededResult, replace
    ``recordOwner.sLeaseId`` with ``recordResult.sReservationId`` — the
    tab is handed a value that authorizes nothing and every subsequent
    owner-scoped call is refused.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart(bSpawnRealProcess=False)
    with pytest.MonkeyPatch.context() as monkeypatch:
        await _fnBeginHeldStart(
            stateApp, monkeypatch, controller, sSessionId,
        )
        controller.eventRelease.set()
        await _fnAwaitDurableTask(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.reservation is None
    iCode, dictBody = startReservation.ftPollStartStatus(
        stateApp, S_PROJECT_NAME, sSessionId,
    )
    assert iCode == 200, dictBody
    assert dictBody["sState"] == startResultStore.S_RESULT_SUCCEEDED
    assert dictBody["sLeaseId"] == recordOwner.sLeaseId
    assert containerOwnership.fbBrowserSessionOwnsLease(
        stateApp.dictContainerOwners, S_PROJECT_NAME, sSessionId,
        dictBody["sLeaseId"],
    ), "the delivered lease must actually authorize the container"


# ------------------------------------------------------------------
# Case 19 — the concurrent-start half of cardinality.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testAConcurrentClaimAndStartResolveToOneOwnerRecord():
    """Case 19 (start half): claim A and start B race to ONE record.

    Two DIFFERENT containers take two DIFFERENT per-container locks, so
    only the hub-wide cardinality lock can serialize the reverse-index
    check between them. Fired concurrently, exactly one of the claim and
    the start may win, and the session must end holding exactly one
    container.

    Kills: in sessionLifecycle._tReserveForStartUnderLocks, pass
    ``dictSessionOwner=None`` into the claim — the START path then runs
    no reverse-index check at all and the session ends up holding two
    containers. (Dropping the cardinality LOCK is not separately
    observable here: both critical sections are synchronous, so the
    event loop cannot interleave them; the lock is the guard for a
    future critical section that awaits, and the canonical order it
    keeps is asserted structurally, not by this mutant.)
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart(bSpawnRealProcess=False)
    controller.eventRelease.set()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            startReservation, "_fsExecuteReservedStart", controller.fsExecute,
        )
        tResults = await asyncio.gather(
            sessionLifecycle.ftClaimWithCardinality(
                stateApp, S_OTHER_PROJECT_NAME, "", 0,
                sBrowserSessionId=sSessionId,
            ),
            startReservation.ftBeginStart(
                stateApp, S_PROJECT_NAME, sSessionId,
                SimpleNamespace(bNeverSleep=False), iPort=0,
            ),
        )
        await _fnAwaitDurableTask(stateApp)
    listCodes = sorted(iCode for iCode, _ in tResults)
    assert listCodes in ([200, 409], [202, 409]), (
        f"the race granted both: {tResults}"
    )
    assert len(stateApp.dictSessionOwner) == 1, (
        "one session ended up holding two containers: "
        f"{stateApp.dictSessionOwner}"
    )
    assert len(stateApp.dictContainerOwners) == 1


# ------------------------------------------------------------------
# Case 41 — a hub killed at each start transition leaves no
# auto-clearing residue.
# ------------------------------------------------------------------

def _fiDeadPid():
    """Return the pid of a process that has certainly exited."""
    processDead = subprocess.Popen([sys.executable, "-c", "pass"])
    processDead.wait(timeout=10)
    return processDead.pid


class InspectingDaemon:
    """Answers only ``fdictInspectContainerIfPresent`` — fail closed."""

    def __init__(self, bContainerPresent):
        self.bContainerPresent = bContainerPresent

    def fdictInspectContainerIfPresent(self, sContainerId):
        if not self.bContainerPresent:
            return None
        return {"sContainerId": sContainerId, "sState": "created"}


@pytest.mark.parametrize(
    "sTransition, dictIdentity, bContainerPresent, sExpected", [
        ("killed before spawn", None, False, "SETTLED"),
        ("killed after spawn, before identity persist",
         {"iHolderPid": None, "sReservationLabel": "d" * 32}, False,
         "QUARANTINED"),
        ("killed after identity persist, container left behind",
         {"iHolderPid": None, "sReservationLabel": "d" * 32,
          "sDockerContainerId": S_PARTIAL_CONTAINER_ID}, True,
         "QUARANTINED"),
        ("killed after identity persist, nothing created",
         {"iHolderPid": None, "sReservationLabel": "d" * 32,
          "sDockerContainerId": S_PARTIAL_CONTAINER_ID}, False,
         "SETTLED"),
    ],
)
def testAKilledStartNeverAutoClearsIntoAClaimableContainer(
    sTransition, dictIdentity, bContainerPresent, sExpected,
):
    """Case 41 (start half): every kill point resolves honestly.

    A hub SIGKILLed mid-start leaves its write-ahead record behind. The
    auto-probe tier may clear it ONLY where nothing can have been
    left behind: a PREPARED record (killed before the promote that
    PRECEDES ``docker create``, so no container was ever requested) and
    a record whose recorded container is definitively absent. The two
    dangerous points must quarantine — a record carrying only the
    reservation label (killed after spawn, before the container id was
    known, so the daemon may have created one nothing can name) and a
    record whose container still exists.

    Kills: in operationJournal._fdictProbeStartOperation, answer the
    label-only record ``_fdictProbeOutcome(False, True, False, ...)``
    (settled) instead of unsupported — a start killed between spawn and
    identity-persist then auto-clears, and the next hub claims a
    container the daemon may have created.
    """
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT_NAME, "start", S_PROJECT_NAME,
    )
    if dictIdentity is not None:
        dictResolved = dict(dictIdentity, iHolderPid=_fiDeadPid())
        operationJournal.fnPromoteOperationToInFlight(
            S_PROJECT_NAME, sOperationId, dictResolved,
        )
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_PROJECT_NAME, connectionDocker=InspectingDaemon(bContainerPresent),
    )
    assert dictResolution["sResolution"] == sExpected, (
        f"{sTransition}: {dictResolution}"
    )
    if sExpected == "QUARANTINED":
        assert operationJournal.fdictReadJournalOutcome(
            S_PROJECT_NAME,
        )["dictOperations"], (
            "a quarantined record must remain on disk for reconciliation"
        )


def testTheJournalDirectoryIsIsolatedForTheseTests():
    """The harness guard: these tests never touch the real journal.

    Every case here writes real write-ahead records, and one of them
    deliberately leaves a QUARANTINE behind. Landing that in the
    researcher's ``~/.vaibify/journal`` would make a real container
    unclaimable until they ran ``vaibify reconcile`` — a test suite
    breaking the machine it runs on, the same hazard class as the
    startup sweep that deleted a live bind-mounted credential file.

    Kills: in tests/conftest.py, point the autouse journal fixture at
    the module's own directory instead of tmp_path — the isolation is
    then a no-op and these tests write the real journal.
    """
    assert "operationJournalIsolated" in (
        operationJournal._S_JOURNAL_DIRECTORY
    ), "these tests would otherwise write the researcher's real journal"
    assert not os.path.expanduser("~/.vaibify/journal") in (
        operationJournal._S_JOURNAL_DIRECTORY
    )


@pytest.mark.asyncio
async def testAFailedStartNeverReleasesOwnershipItDidNotCreate():
    """A start that JOINS an existing owner must not release it on failure.

    The researcher owns a container and clicks Start on one that is
    already running. The start refuses — and before this guard, the
    refusal ran the settlement's release against the *pre-existing*
    owner record, dropping their lease, freeing the flock, and clearing
    their cardinality entry while the container went on running. A
    failed start may only release ownership the start itself
    established.

    Kills: in sessionLifecycle.ftSettleFailedStartOwnership, drop the
    ``if not bStartOwnsTheRecord: return`` guard — the failure then
    releases the owner record the start merely borrowed, and a valid
    owner silently loses the container they still hold.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    iCode, dictClaim = await sessionLifecycle.ftClaimWithCardinality(
        stateApp, S_PROJECT_NAME, "", 0, sBrowserSessionId=sSessionId,
    )
    assert iCode == 200, dictClaim
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    sLeasePriorToStart = recordOwner.sLeaseId

    def _fsRefuseAlreadyRunning(sName, reservation, configProject):
        raise RuntimeError("container already running")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            startReservation, "_fsExecuteReservedStart",
            _fsRefuseAlreadyRunning,
        )
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            lambda sReservationId, bLaunchWasKilled: {
                "bConclusive": True, "listRemovedContainerIds": [],
                "sDetail": "no container bearing the reservation label",
            },
        )
        iCodeStart, dictStart = await startReservation.ftBeginStart(
            stateApp, S_PROJECT_NAME, sSessionId,
            SimpleNamespace(bNeverSleep=False), iPort=0,
        )
        assert iCodeStart == 202, dictStart
        for _ in range(200):
            if getattr(
                stateApp.dictContainerOwners.get(S_PROJECT_NAME),
                "reservation", None,
            ) is None:
                break
            await asyncio.sleep(0.05)

    assert S_PROJECT_NAME in stateApp.dictContainerOwners, (
        "a failed start released an owner record it did not create"
    )
    recordAfter = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordAfter.sLeaseId == sLeasePriorToStart, (
        "the pre-existing owner's lease was rotated by a failed start"
    )
    assert stateApp.dictSessionOwner.get(sSessionId) == S_PROJECT_NAME, (
        "the owner's cardinality entry was dropped by a failed start"
    )


# ------------------------------------------------------------------
# Wave 6: the start-versus-transfer barrier at MULTIPLE phases, and
# the cardinality LOCK isolated from the cardinality REGISTRY.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def testATransferBeforeTheLaunchRefusesAndChangesNothing():
    """A transfer arriving between the reservation and the launch.

    The existing barrier drives a transfer against a start whose durable
    task is already registered, and it is ADOPTED. This is the earlier
    phase, and it resolves the other way: the journal record is
    IN_FLIGHT but no durable task exists yet, so the record is genuinely
    un-attributable and the transfer refuses. That is the fail-closed
    answer, and it is right -- what would be wrong is refusing while
    ALSO disturbing something.

    So the assertion that matters is the second one: the refusal must
    leave the ownership, the generation and the reservation exactly as
    they were. A refusal that half-committed would strand a start with
    no owner to deliver its outcome to.

    Kills: in sessionLifecycle, dropping the unsettled-journal refusal
    (``continue`` in place of the ``return`` on the un-attributable
    record), so a transfer commits over a start whose journal record
    nothing can account for.
    """
    from tests.testHostTransfer import (
        S_PROJECT_NAME as S_TRANSFER_PROJECT,
        _fstateBuildAppState as _fstateTransfer,
        _fsMintTransferCapability, _tSeedOwnedContainer, _tTransfer,
    )
    stateApp = _fstateTransfer()
    _tSeedOwnedContainer(stateApp)

    sOperationId = operationJournal.fsPrepareOperation(
        S_TRANSFER_PROJECT, "start", S_TRANSFER_PROJECT,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_TRANSFER_PROJECT, sOperationId,
        {"iHolderPid": 1, "sReservationLabel": "b" * 32},
    )
    recordOwner = stateApp.dictContainerOwners[S_TRANSFER_PROJECT]
    sLeaseBefore = recordOwner.sLeaseId
    iGenerationBefore = recordOwner.iOwnerGeneration
    reservation = startReservation.StartReservation(
        sReservationId="b" * 32,
        recordStartTask=startReservation.StartTaskRecord(
            sStartTaskId="preLaunchTask", sJournalOperationId=sOperationId,
        ),
        identityOwnership=containerOwnership.fidentityRecordOwnership(
            recordOwner, containerOwnership.S_NO_PRIOR_OWNER,
        ),
    )
    recordOwner.reservation = reservation

    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)

    assert sOutcome == sessionLifecycle.S_TRANSFER_REFUSED, (
        f"an un-attributable journal record must refuse: {dictPayload}"
    )
    assert "reconcile" in dictPayload["sMessage"], (
        "the refusal must name its recovery"
    )
    recordAfter = stateApp.dictContainerOwners[S_TRANSFER_PROJECT]
    assert recordAfter.sLeaseId == sLeaseBefore, (
        "a refused transfer rotated the lease anyway"
    )
    assert recordAfter.iOwnerGeneration == iGenerationBefore
    assert recordAfter.reservation is reservation, (
        "a refused transfer disturbed the in-flight start"
    )
    operationJournal.fnSettleOperation(S_TRANSFER_PROJECT, sOperationId)


@pytest.mark.asyncio
async def testAFailedStartAfterATransferDoesNotFreeTheSuccessor():
    """The LAST phase: the transfer lands, then the start settles.

    The barrier tests cover a transfer arriving while a start runs. This
    is what happens next, and it is the phase where the damage would be
    done: the start fails, and its settlement runs against an ownership
    that is no longer the one it created. Driven through the real
    settlement, with the successor's lease, generation and session all
    distinct from the originals so a comparison that checked only one of
    them would still pass.

    Kills: in sessionLifecycle._fbStartMayFreeOwnership, comparing only
    ``identityOwnership.bEstablishedTheOwnership`` -- the Boolean this
    identity replaced, which stays true across a transfer and would free
    the successor's ownership.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart(bSpawnRealProcess=False)
    daemon = RecordingDaemon([])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            startReservation, "_fsExecuteReservedStart", controller.fsExecute,
        )
        monkeypatch.setattr(
            containerManager, "fdictSettleReservationContainers",
            daemon.fdictSettle,
        )
        iCode, _dictBody = await startReservation.ftBeginStart(
            stateApp, S_PROJECT_NAME, sSessionId,
            SimpleNamespace(bNeverSleep=False), iPort=0,
        )
        assert iCode == 202
        await _fnAwaitEventSet(controller.eventEntered)

        recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
        recordOwner.sLeaseId = "successor-lease"
        recordOwner.iOwnerGeneration += 1
        recordOwner.sBrowserSessionId = "successor-session"

        # Make the held launch FAIL, which is the path whose settlement
        # decides whether ownership is freed.
        recordOwner.reservation.recordStartTask.bCancelRequested = True
        controller.eventRelease.set()
        await _fnAwaitDurableTask(stateApp)

    assert S_PROJECT_NAME in stateApp.dictContainerOwners, (
        "the start's settlement freed the SUCCESSOR's ownership; a "
        "transfer hands the container over, it does not hand it back"
    )
    assert stateApp.dictContainerOwners[S_PROJECT_NAME].sLeaseId == (
        "successor-lease"
    )


@pytest.mark.asyncio
async def testTheStartTakesTheCardinalityLockNotJustTheIndex():
    """The LOCK path, isolated from the registry path it guards.

    Case 19 proves the reverse-index CHECK happens: drop the index and
    one session ends up holding two containers. It cannot prove the
    cardinality LOCK is taken, because both critical sections are
    synchronous and the event loop cannot interleave them -- so a
    version that read the index without the lock passes that test.

    This isolates the other half. The test holds the hub's cardinality
    lock itself and asserts the start BLOCKS -- which it can only do by
    trying to acquire it. Without the lock the start sails past a held
    lock and answers immediately, and the guard for the first critical
    section that ever awaits would have been silently removed.

    Kills: in sessionLifecycle.ftReserveContainerForStart, replacing the
    ``async with _flockObtainSessionCardinality(dictLockStore):`` with
    ``if True:`` -- the reservation then arbitrates ownership without
    the hub-wide lock.
    """
    stateApp = _fstateBuildAppState()
    sSessionId = _fsRedeemSession(stateApp)
    controller = ControlledStart(bSpawnRealProcess=False)
    controller.eventRelease.set()
    lockCardinality = sessionLifecycle.flockSessionCardinalityForAppState(
        stateApp,
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            startReservation, "_fsExecuteReservedStart", controller.fsExecute,
        )
        await lockCardinality.acquire()
        taskStart = asyncio.ensure_future(startReservation.ftBeginStart(
            stateApp, S_PROJECT_NAME, sSessionId,
            SimpleNamespace(bNeverSleep=False), iPort=0,
        ))
        try:
            await asyncio.wait_for(asyncio.shield(taskStart), 0.25)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError(
                "the start completed while the hub-wide cardinality "
                "lock was held, so it never acquired it"
            )
        assert S_PROJECT_NAME not in stateApp.dictContainerOwners, (
            "the start arbitrated ownership without the cardinality lock"
        )
        lockCardinality.release()
        iCode, _dictBody = await taskStart
        assert iCode == 202
        await _fnAwaitDurableTask(stateApp)
