"""Tests for the ORPHANED_SESSION foundation (slices 1-2).

Covers the new structure: the two-valued owner/browser state fields, the
generation-matched connection decrement, the session-owner and
session-socket indices, the mutable pipeline-task generation, and the
``sessionLifecycle`` release authority that routes now commit through.

Fixtures keep the container NAME distinct from the Docker ID wherever
both appear (repo epistemics rule): the owner map is keyed by name, and
a fixture with name == id can green-light an id-keyed lookup bug.
"""

import asyncio
import logging
import time

import pytest

from vaibify.config import containerLock
from vaibify.gui import browserSession, containerOwnership, sessionLifecycle

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-0123456789ab"


@pytest.fixture(autouse=True)
def fixtureIsolateLockDirectory(tmp_path, monkeypatch):
    """Redirect the host flock directory to a per-test tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


def _frecordOwner(**dictOverrides):
    """Build an owner record whose name key differs from its Docker id."""
    dictFields = {
        "sLeaseId": "LEASE-A",
        "fileHandleLock": None,
        "sContainerId": S_CONTAINER_ID,
        "sBrowserSessionId": "session-a",
    }
    dictFields.update(dictOverrides)
    return containerOwnership.OwnerRecord(**dictFields)


# -- slice 1: record fields ----------------------------------------------


def testOwnerRecordFoundationFieldDefaults():
    """A fresh owner record starts ACTIVE at generation 1, never orphaned."""
    recordOwner = _frecordOwner()
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE
    assert recordOwner.iOwnerGeneration == 1
    assert recordOwner.fOrphanedSinceMonotonic == 0.0
    assert recordOwner.fLastAgentActivityMonotonic == 0.0
    assert recordOwner.iInFlightAgentRequests == 0
    assert recordOwner.bSocketEverExisted is False


def testIncrementLiveConnectionStampsSocketEverExisted():
    """The first live socket flips the zero-sockets orphan-trigger gate."""
    dictOwners = {S_PROJECT_NAME: _frecordOwner()}
    containerOwnership.fnIncrementLiveConnection(
        dictOwners, S_PROJECT_NAME,
    )
    assert dictOwners[S_PROJECT_NAME].bSocketEverExisted is True


def testRevokedBrowserSessionCredentialAuthorizesNothing():
    """A REVOKED record fails validation and keeps its last-seen stamp."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    assert sSessionId and browserSession.fbValidateCredential(
        dictStore, sCredential,
    )
    recordSession = dictStore["dictSessionsByCredential"][sCredential]
    recordSession.sState = browserSession.S_SESSION_STATE_REVOKED
    recordSession.fLastSeenMonotonic = 123.0
    assert browserSession.fbValidateCredential(
        dictStore, sCredential,
    ) is False, "a revoked credential must authorize nothing"
    assert recordSession.fLastSeenMonotonic == 123.0, (
        "a revoked credential must not refresh the session's liveness"
    )


# -- slice 1: generation-matched decrement --------------------------------


def testMatchedGenerationRecordDecrementsCounters():
    """A connection record carrying the current generation decrements."""
    dictOwners = {S_PROJECT_NAME: _frecordOwner(
        iLiveConnectionCount=2, iLivePipelineConnectionCount=1,
    )}
    recordConnection = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_PIPELINE,
    )
    containerOwnership.fnDecrementLiveConnectionForRecord(
        dictOwners, S_PROJECT_NAME, recordConnection,
    )
    recordOwner = dictOwners[S_PROJECT_NAME]
    assert recordOwner.iLiveConnectionCount == 1
    assert recordOwner.iLivePipelineConnectionCount == 0


def testStaleGenerationRecordDecrementsNothing():
    """A stale-generation socket's finally must not touch the successor."""
    recordOwner = _frecordOwner(
        iLiveConnectionCount=1, iLivePipelineConnectionCount=1,
        iOwnerGeneration=2, fLastSeenMonotonic=456.0,
    )
    dictOwners = {S_PROJECT_NAME: recordOwner}
    recordStale = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_PIPELINE,
    )
    containerOwnership.fnDecrementLiveConnectionForRecord(
        dictOwners, S_PROJECT_NAME, recordStale,
    )
    assert recordOwner.iLiveConnectionCount == 1, (
        "a stale generation must decrement nothing"
    )
    assert recordOwner.iLivePipelineConnectionCount == 1
    assert recordOwner.fLastSeenMonotonic == 456.0, (
        "a stale generation must not refresh the grace clock"
    )


def testUnownedGenerationZeroNeverMatchesARealOwner():
    """A socket accepted while unowned (gen 0) cannot decrement gen 1."""
    assert containerOwnership.fiOwnerGenerationForName(
        {}, S_PROJECT_NAME,
    ) == 0
    dictOwners = {S_PROJECT_NAME: _frecordOwner(iLiveConnectionCount=1)}
    recordPreClaim = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=0, sLane=containerOwnership.S_LANE_TERMINAL,
    )
    containerOwnership.fnDecrementLiveConnectionForRecord(
        dictOwners, S_PROJECT_NAME, recordPreClaim,
    )
    assert dictOwners[S_PROJECT_NAME].iLiveConnectionCount == 1


# -- slice 1: session-socket index ----------------------------------------


def testSessionSocketIndexRegistersAndDrainsPerSession():
    """Records accumulate per session; the emptied set drops its key."""
    dictSockets = containerOwnership.fdictCreateSessionSocketIndex()
    recordFirst = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_PIPELINE,
    )
    recordSecond = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_TERMINAL,
    )
    containerOwnership.fnRegisterSessionSocket(dictSockets, recordFirst)
    containerOwnership.fnRegisterSessionSocket(dictSockets, recordSecond)
    assert dictSockets["session-a"] == {recordFirst, recordSecond}
    containerOwnership.fnDeregisterSessionSocket(dictSockets, recordFirst)
    assert dictSockets["session-a"] == {recordSecond}
    containerOwnership.fnDeregisterSessionSocket(dictSockets, recordSecond)
    assert "session-a" not in dictSockets


def testSessionSocketIndexIgnoresAnonymousAndAbsentRecords():
    """No session id, no index entry; None inputs are tolerated."""
    dictSockets = containerOwnership.fdictCreateSessionSocketIndex()
    recordAnonymous = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_PIPELINE,
    )
    containerOwnership.fnRegisterSessionSocket(dictSockets, recordAnonymous)
    assert dictSockets == {}
    containerOwnership.fnRegisterSessionSocket(None, recordAnonymous)
    containerOwnership.fnDeregisterSessionSocket(dictSockets, None)
    containerOwnership.fnDeregisterSessionSocket(None, recordAnonymous)


# -- slice 1: pipeline-task generation is mutable, read at completion -----


@pytest.mark.asyncio
async def testPipelineTaskGenerationIsRetaggedInPlaceNotSnapshotted(caplog):
    """The done-callback reads the task record's CURRENT generation.

    Retagging the task after registration (what a host transfer will do)
    must be visible at completion time — a snapshot captured at
    registration would attribute the completion to the old owner.
    """
    from vaibify.gui.pipelineServer import _fnRegisterPipelineTask
    caplog.set_level(logging.DEBUG, logger="vaibify")
    dictPipelineTasks = {}
    eventFinish = asyncio.Event()

    async def fnHoldUntilReleased():
        await eventFinish.wait()

    taskPipeline = asyncio.ensure_future(fnHoldUntilReleased())
    _fnRegisterPipelineTask(
        dictPipelineTasks, S_CONTAINER_ID, taskPipeline,
    )
    assert taskPipeline.iOwnerGeneration == 1
    taskPipeline.iOwnerGeneration = 2  # the transfer's in-place retag
    eventFinish.set()
    await taskPipeline
    await asyncio.sleep(0)
    assert S_CONTAINER_ID not in dictPipelineTasks
    assert any(
        "generation 2" in record.getMessage()
        for record in caplog.records
    ), "completion must be attributed to the retagged generation"


# -- slices 1-2: the session-owner index stays in sync --------------------


def testSessionOwnerIndexTracksClaimAndRelease():
    """Claim writes the reverse index; a lease-proven release drops it."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    iStatusCode, dictPayload = containerOwnership.ftClaim(
        dictOwners, S_PROJECT_NAME, "", iPort=8050,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        dictSessionOwner=dictSessionOwner,
    )
    assert iStatusCode == 200
    assert dictSessionOwner == {"session-a": S_PROJECT_NAME}
    bReleased = containerOwnership.fnReleaseOwnership(
        dictOwners, S_PROJECT_NAME, dictPayload["sLeaseId"],
        sBrowserSessionId="session-a", dictSessionOwner=dictSessionOwner,
    )
    assert bReleased is True
    assert dictSessionOwner == {}


def testSessionOwnerIndexSurvivesAForeignReleaseAttempt():
    """A refused release must leave the reverse index untouched."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    containerOwnership.ftClaim(
        dictOwners, S_PROJECT_NAME, "", iPort=8050,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        dictSessionOwner=dictSessionOwner,
    )
    bReleased = containerOwnership.fnReleaseOwnership(
        dictOwners, S_PROJECT_NAME, "not-the-lease",
        sBrowserSessionId="session-b", dictSessionOwner=dictSessionOwner,
    )
    assert bReleased is False
    assert dictSessionOwner == {"session-a": S_PROJECT_NAME}


def testReaperDropsTheSessionOwnerIndexEntry():
    """A reaped ownership also clears its cardinality index entry."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    containerOwnership.ftClaim(
        dictOwners, S_PROJECT_NAME, "", iPort=8050,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        dictSessionOwner=dictSessionOwner,
    )
    dictOwners[S_PROJECT_NAME].fLastSeenMonotonic = (
        time.monotonic() - 10_000.0
    )
    listReaped = containerOwnership.flistReapIdleOwnerships(
        dictOwners, dictSessionOwner=dictSessionOwner,
    )
    assert listReaped == [S_PROJECT_NAME]
    assert dictSessionOwner == {}


# -- slice 2: the sessionLifecycle release authority ----------------------


class _StateStub:
    """A bare app.state stand-in the authority can provision itself on."""

    def __init__(self, dictContainerOwners, dictSessionOwner):
        self.dictContainerOwners = dictContainerOwners
        self.dictSessionOwner = dictSessionOwner


@pytest.mark.asyncio
async def testReleaseExplicitPreservesThePrimitiveVerdicts():
    """The authority answers exactly as the primitive did: owner True,
    foreign lease False with the record retained, unknown name False."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    _, dictPayload = containerOwnership.ftClaim(
        dictOwners, S_PROJECT_NAME, "", iPort=8050,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        dictSessionOwner=dictSessionOwner,
    )
    stateStub = _StateStub(dictOwners, dictSessionOwner)
    assert await sessionLifecycle.fbReleaseExplicit(
        stateStub, S_PROJECT_NAME, "copied-value",
        sBrowserSessionId="session-b",
    ) is False
    assert S_PROJECT_NAME in dictOwners, (
        "a foreign release must not drop the true owner's record"
    )
    assert await sessionLifecycle.fbReleaseExplicit(
        stateStub, "AbsentProject", dictPayload["sLeaseId"],
        sBrowserSessionId="session-a",
    ) is False
    assert await sessionLifecycle.fbReleaseExplicit(
        stateStub, S_PROJECT_NAME, dictPayload["sLeaseId"],
        sBrowserSessionId="session-a",
    ) is True
    assert dictOwners == {} and dictSessionOwner == {}


@pytest.mark.asyncio
async def testLifecycleLockStoreKeepsOneLockPerContainerName():
    """The keyed drain lock is stable per name and never deleted."""
    dictLockStore = sessionLifecycle.fdictCreateLifecycleLockStore()
    lockFirst = sessionLifecycle._flockObtainContainerMutation(
        dictLockStore, S_PROJECT_NAME,
    )
    lockSecond = sessionLifecycle._flockObtainContainerMutation(
        dictLockStore, S_PROJECT_NAME,
    )
    assert lockFirst is lockSecond
    lockOther = sessionLifecycle._flockObtainContainerMutation(
        dictLockStore, "OtherProject",
    )
    assert lockOther is not lockFirst
    lockCardinality = sessionLifecycle._flockObtainSessionCardinality(
        dictLockStore,
    )
    assert lockCardinality is (
        sessionLifecycle._flockObtainSessionCardinality(dictLockStore)
    )
    assert set(dictLockStore["dictContainerMutationLocks"]) == {
        S_PROJECT_NAME, "OtherProject",
    }


def testNoRouteModuleCallsTheReleasePrimitivesDirectly():
    """sessionLifecycle is the only caller of the release primitives.

    A route that drops an owner record through the primitive bypasses
    the canonical lock order, so any new direct call outside
    ``containerOwnership`` (the primitives) and ``sessionLifecycle``
    (the authority) fails here.
    """
    from pathlib import Path
    pathGui = Path(containerOwnership.__file__).resolve().parent
    setAllowedFiles = {"containerOwnership.py", "sessionLifecycle.py"}
    listOffenders = []
    for pathFile in sorted(pathGui.rglob("*.py")):
        if pathFile.name in setAllowedFiles:
            continue
        sSource = pathFile.read_text(encoding="utf-8")
        if (
            "fnReleaseOwnership(" in sSource
            or "_fnForceReleaseOwnership(" in sSource
        ):
            listOffenders.append(pathFile.name)
    assert listOffenders == [], (
        "release primitives called outside the sessionLifecycle "
        f"authority: {listOffenders}"
    )


# -- slice 4: cardinality — one container per session ----------------------


def testConflictingHeldContainerReadsTheReverseIndex():
    """Only a bound session holding a DIFFERENT container conflicts."""
    dictSessionOwner = {"session-a": S_PROJECT_NAME}
    assert containerOwnership.fsConflictingHeldContainer(
        dictSessionOwner, "session-a", "OtherProject",
    ) == S_PROJECT_NAME
    assert containerOwnership.fsConflictingHeldContainer(
        dictSessionOwner, "session-a", S_PROJECT_NAME,
    ) == "", "the session's own container is never a conflict"
    assert containerOwnership.fsConflictingHeldContainer(
        dictSessionOwner, "", "OtherProject",
    ) == "", "a transitional unbound caller never conflicts"
    assert containerOwnership.fsConflictingHeldContainer(
        None, "session-a", "OtherProject",
    ) == "", "an absent reverse index never conflicts"


def testClaimPrimitiveRefusesASecondContainerForABoundSession():
    """One session, two containers: the second claim is a named 409."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    iFirstStatus, _ = containerOwnership.ftClaim(
        dictOwners, S_PROJECT_NAME, "", iPort=8050,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        dictSessionOwner=dictSessionOwner,
    )
    assert iFirstStatus == 200
    iSecondStatus, dictRefusal = containerOwnership.ftClaim(
        dictOwners, "SecondProject", "", iPort=8050,
        sContainerId="cid-fedcba987654", sBrowserSessionId="session-a",
        dictSessionOwner=dictSessionOwner,
    )
    assert iSecondStatus == 409
    assert dictRefusal["sHeldContainerName"] == S_PROJECT_NAME
    assert S_PROJECT_NAME in dictRefusal["sMessage"]
    assert "SecondProject" not in dictOwners, (
        "a refused claim must not mint a second owner record"
    )
    assert dictSessionOwner == {"session-a": S_PROJECT_NAME}


def testUnboundTransitionalClaimsBypassTheCardinalityCheck():
    """Shared-token claims (no session binding) must not regress."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    for sName, iPort in ((S_PROJECT_NAME, 8050), ("SecondProject", 8051)):
        iStatusCode, _ = containerOwnership.ftClaim(
            dictOwners, sName, "", iPort=iPort,
            sBrowserSessionId="", dictSessionOwner=dictSessionOwner,
        )
        assert iStatusCode == 200
    assert dictSessionOwner == {}, (
        "an unbound claim records no cardinality entry"
    )


@pytest.mark.asyncio
async def testClaimWithCardinalityAuthorityPreservesTheVerdicts():
    """The lifecycle claim answers as the primitive does: grant, the
    idempotent same-container reclaim, and the cardinality 409."""
    dictOwners = {}
    dictSessionOwner = containerOwnership.fdictCreateSessionOwnerIndex()
    stateStub = _StateStub(dictOwners, dictSessionOwner)
    iStatusCode, dictPayload = (
        await sessionLifecycle.ftClaimWithCardinality(
            stateStub, S_PROJECT_NAME, "", 8050,
            sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        )
    )
    assert iStatusCode == 200
    iReclaimStatus, dictReclaim = (
        await sessionLifecycle.ftClaimWithCardinality(
            stateStub, S_PROJECT_NAME, dictPayload["sLeaseId"], 8050,
            sContainerId=S_CONTAINER_ID, sBrowserSessionId="session-a",
        )
    )
    assert iReclaimStatus == 200
    assert dictReclaim["sLeaseId"] == dictPayload["sLeaseId"]
    iRefusedStatus, dictRefusal = (
        await sessionLifecycle.ftClaimWithCardinality(
            stateStub, "SecondProject", "", 8050,
            sBrowserSessionId="session-a",
        )
    )
    assert iRefusedStatus == 409
    assert dictRefusal["sHeldContainerName"] == S_PROJECT_NAME
    assert dictSessionOwner == {"session-a": S_PROJECT_NAME}


def testNoRouteModuleCallsTheClaimPrimitiveDirectly():
    """sessionLifecycle is the only caller of the claim primitive.

    The claim commit must acquire container-mutation → cardinality in
    canonical order, so a route calling
    ``containerOwnership.ftClaim(`` directly would bypass the lock
    that makes the cardinality read-check-write atomic. Mirrors
    ``testNoRouteModuleCallsTheReleasePrimitivesDirectly``.
    """
    from pathlib import Path
    pathGui = Path(containerOwnership.__file__).resolve().parent
    setAllowedFiles = {"containerOwnership.py", "sessionLifecycle.py"}
    listOffenders = [
        pathFile.name
        for pathFile in sorted(pathGui.rglob("*.py"))
        if pathFile.name not in setAllowedFiles
        and "ftClaim(" in pathFile.read_text(encoding="utf-8")
    ]
    assert listOffenders == [], (
        "the claim primitive is called outside the sessionLifecycle "
        f"authority: {listOffenders}"
    )


# -- config knobs ----------------------------------------------------------


def testEnvironmentSecondsKnobParsesAndFailsSafe(monkeypatch, caplog):
    """Unset yields the default, a number parses, garbage falls back."""
    monkeypatch.delenv("VAIBIFY_SAMPLE_KNOB_SECONDS", raising=False)
    assert containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_SAMPLE_KNOB_SECONDS", 30.0,
    ) == 30.0
    monkeypatch.setenv("VAIBIFY_SAMPLE_KNOB_SECONDS", "12.5")
    assert containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_SAMPLE_KNOB_SECONDS", 30.0,
    ) == 12.5
    monkeypatch.setenv("VAIBIFY_SAMPLE_KNOB_SECONDS", "soon")
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        assert containerOwnership.ffReadSecondsFromEnvironment(
            "VAIBIFY_SAMPLE_KNOB_SECONDS", 30.0,
        ) == 30.0
    assert any(
        "VAIBIFY_SAMPLE_KNOB_SECONDS" in record.getMessage()
        for record in caplog.records
    )


def testLifecycleConstantsExistForTheLaterSlices():
    """The declared knobs are positive floats the later slices consume."""
    for fKnob in (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS,
        sessionLifecycle.F_SLIDING_IDLE_SECONDS,
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS,
        sessionLifecycle.F_LIFECYCLE_EVALUATOR_CADENCE_SECONDS,
    ):
        assert isinstance(fKnob, float) and fKnob > 0
