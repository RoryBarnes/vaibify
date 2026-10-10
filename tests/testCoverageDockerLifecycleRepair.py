"""A container repair runs inside the journal, under the established lock.

These tests drive ``containerLifecycleRepair`` against the REAL
operation journal and the REAL container flock, each redirected into
the test's own directory. Only the container mutation itself -- the
lifecycle gateway's ``docker restart`` / recreate, and the daemon's
inspect -- is stubbed, because that is where the Docker daemon starts.
The container name and container id are distinct throughout, so a
repair that passed one where the other belonged would fail.
"""

import os

import pytest

from vaibify.config import containerLock, operationJournal, registryManager
from vaibify.docker import containerLifecycleRepair, containerManager
from vaibify.docker.containerLifecycleRepair import (
    RepairRefusedError,
    fdictRecreateUnderJournal,
    fdictRepairDirectlyUnderFlock,
    fdictRestartUnderJournal,
    flistNameLiveWork,
)


S_CONTAINER_NAME = "repairTarget"
S_NEW_CONTAINER_ID = "c0ffee" + "1" * 58
S_IMAGE_IDENTITY = "sha256:" + "e" * 64


class _ConfigStub:
    sProjectName = S_CONTAINER_NAME


@pytest.fixture(autouse=True)
def fnIsolateJournalLockAndRegistry(tmp_path, monkeypatch):
    """Journal, lock and registry live under tmp, never ~/.vaibify."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    sRegistryDirectory = str(tmp_path / "registry")
    os.makedirs(sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )


def flistRecordGatewayCalls(monkeypatch, exceptionToRaise=None):
    """Stub the lifecycle gateway; record each call and the journal then."""
    listCalls = []

    def fdictFakeRepair(config, sContainerName, sOperation, sImageIdentity):
        dictJournalDuring = operationJournal.fdictReadJournalOutcome(
            sContainerName,
        )
        listCalls.append({
            "config": config, "sContainerName": sContainerName,
            "sOperation": sOperation, "sImageIdentity": sImageIdentity,
            "dictJournalDuring": dictJournalDuring,
        })
        if exceptionToRaise is not None:
            raise exceptionToRaise
        return {
            "sOperation": sOperation,
            "sContainerId": S_NEW_CONTAINER_ID if sOperation == "recreate"
            else "",
        }

    monkeypatch.setattr(
        containerManager, "fdictRepairContainerLifecycle", fdictFakeRepair,
    )
    return listCalls


def fnStubRunningImage(monkeypatch, sImage):
    """Answer the container inspect with a given image identity."""
    monkeypatch.setattr(
        containerManager, "fjsonInspectContainer",
        lambda sIdentifier: {"Image": sImage, "Name": "/" + sIdentifier},
    )


def fbJournalIsEmpty(sContainerName):
    """Return True when the container's journal records no operation."""
    dictOutcome = operationJournal.fdictReadJournalOutcome(sContainerName)
    return not (dictOutcome.get("dictOperations") or {})


def testAnIdleContainerNamesNoLiveWork():
    """No journal at all means nothing is in flight."""
    assert flistNameLiveWork(S_CONTAINER_NAME) == []


def testARestartIsJournaledInFlightWhileTheEffectHappens(monkeypatch):
    """The record exists BEFORE the mutation and is settled after it."""
    listCalls = flistRecordGatewayCalls(monkeypatch)
    listAnnounced = []
    dictOutcome = fdictRestartUnderJournal(
        S_CONTAINER_NAME, fnAnnounce=listAnnounced.append,
    )
    assert dictOutcome == {"bRepaired": True, "sOperation": "restart"}
    assert len(listCalls) == 1
    assert listCalls[0]["sContainerName"] == S_CONTAINER_NAME
    assert listCalls[0]["sOperation"] == "restart"
    dictOperations = listCalls[0]["dictJournalDuring"]["dictOperations"]
    assert len(dictOperations) == 1
    dictRecord = next(iter(dictOperations.values()))
    assert dictRecord["sState"] == operationJournal.S_OPERATION_STATE_IN_FLIGHT
    assert dictRecord["sTarget"] == "repair-restart"
    assert dictRecord["iHolderPid"] == os.getpid()
    assert fbJournalIsEmpty(S_CONTAINER_NAME)
    assert len(listAnnounced) == 1
    assert "kill every shell" in listAnnounced[0]


def testAFailedRestartBecomesARefusalAndStillSettlesTheJournal(monkeypatch):
    """A daemon error reads as 'did not happen', and leaves no stuck record."""
    flistRecordGatewayCalls(
        monkeypatch, RuntimeError("docker restart failed: no such container"),
    )
    with pytest.raises(RepairRefusedError) as errorRaised:
        fdictRestartUnderJournal(S_CONTAINER_NAME)
    assert "docker restart failed" in str(errorRaised.value)
    assert fbJournalIsEmpty(S_CONTAINER_NAME)
    assert flistNameLiveWork(S_CONTAINER_NAME) == []


def testAGatewayValueErrorIsAlsoARefusal(monkeypatch):
    """Both failure classes the gateway raises reach the researcher alike."""
    flistRecordGatewayCalls(monkeypatch, ValueError("not a repair operation"))
    with pytest.raises(RepairRefusedError) as errorRaised:
        fdictRestartUnderJournal(S_CONTAINER_NAME)
    assert "not a repair operation" in str(errorRaised.value)


def testARecreateAnnouncesTheImageAndReturnsTheNewId(monkeypatch):
    """The announcement names the pinned image; the new id is reported."""
    fnStubRunningImage(monkeypatch, S_IMAGE_IDENTITY)
    listCalls = flistRecordGatewayCalls(monkeypatch)
    listAnnounced = []
    configProject = _ConfigStub()
    dictOutcome = fdictRecreateUnderJournal(
        configProject, S_CONTAINER_NAME, fnAnnounce=listAnnounced.append,
    )
    assert dictOutcome == {
        "bRepaired": True, "sOperation": "recreate",
        "sImageIdentity": S_IMAGE_IDENTITY,
        "sContainerId": S_NEW_CONTAINER_ID,
    }
    assert listCalls[0]["config"] is configProject
    assert listCalls[0]["sImageIdentity"] == S_IMAGE_IDENTITY
    dictRecord = next(iter(
        listCalls[0]["dictJournalDuring"]["dictOperations"].values()
    ))
    assert dictRecord["sTarget"] == "repair-recreate"
    assert S_IMAGE_IDENTITY in listAnnounced[0]
    assert "including /tmp, are discarded" in listAnnounced[0]
    assert fbJournalIsEmpty(S_CONTAINER_NAME)


def testARecreateWithAnUnresolvableImageTouchesNothing(monkeypatch):
    """The refusal happens before the journal or the gateway are reached."""
    fnStubRunningImage(monkeypatch, "")
    listCalls = flistRecordGatewayCalls(monkeypatch)
    with pytest.raises(RepairRefusedError):
        fdictRecreateUnderJournal(_ConfigStub(), S_CONTAINER_NAME)
    assert listCalls == []
    assert operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME,
    )["sReadState"] == "absent"


def testTheDirectLaneHoldsTheFlockDuringTheRepairAndReleasesIt(monkeypatch):
    """A hub starting mid-repair cannot take the container."""
    listLockAttempts = []

    def fdictRepairWhileProbingTheLock(
        config, sContainerName, sOperation, sImageIdentity,
    ):
        try:
            containerLock.ffileAcquireContainerLock(sContainerName, 9001)
            listLockAttempts.append("acquired")
        except containerLock.ContainerLockedError as errorLocked:
            listLockAttempts.append(errorLocked.iHolderPid)
        return {"sOperation": sOperation, "sContainerId": ""}

    monkeypatch.setattr(
        containerManager, "fdictRepairContainerLifecycle",
        fdictRepairWhileProbingTheLock,
    )
    dictOutcome = fdictRepairDirectlyUnderFlock(
        _ConfigStub(), S_CONTAINER_NAME, "restart",
    )
    assert dictOutcome["sOperation"] == "restart"
    assert listLockAttempts == [os.getpid()]
    fileHandle = containerLock.ffileAcquireContainerLock(S_CONTAINER_NAME, 0)
    containerLock.fnReleaseContainerLock(fileHandle)


def testTheDirectLaneRoutesRecreateToTheImagePinningPath(monkeypatch):
    """A recreate on the direct lane still resolves and pins the image ID."""
    fnStubRunningImage(monkeypatch, S_IMAGE_IDENTITY)
    listCalls = flistRecordGatewayCalls(monkeypatch)
    dictOutcome = fdictRepairDirectlyUnderFlock(
        _ConfigStub(), S_CONTAINER_NAME, "recreate",
    )
    assert dictOutcome["sImageIdentity"] == S_IMAGE_IDENTITY
    assert [dictCall["sOperation"] for dictCall in listCalls] == ["recreate"]
    assert listCalls[0]["sImageIdentity"] == S_IMAGE_IDENTITY


def testAHeldFlockRefusesTheDirectLaneNamingTheHolder(monkeypatch):
    """A live vaibify process owns the container; the repair belongs there."""
    listCalls = flistRecordGatewayCalls(monkeypatch)
    fileHandleHeld = containerLock.ffileAcquireContainerLock(
        S_CONTAINER_NAME, 8123,
    )
    try:
        with pytest.raises(RepairRefusedError) as errorRaised:
            fdictRepairDirectlyUnderFlock(
                _ConfigStub(), S_CONTAINER_NAME, "restart",
            )
    finally:
        containerLock.fnReleaseContainerLock(fileHandleHeld)
    sMessage = str(errorRaised.value)
    assert f"pid={os.getpid()}" in sMessage
    assert "port=8123" in sMessage
    assert listCalls == []


def testAnUnsafeContainerNameIsRefusedBeforeAnyLock(monkeypatch):
    """A name that could escape the lock directory is a refusal, not a path."""
    listCalls = flistRecordGatewayCalls(monkeypatch)
    with pytest.raises(RepairRefusedError):
        fdictRepairDirectlyUnderFlock(
            _ConfigStub(), "../escape", "restart",
        )
    assert listCalls == []
    assert not os.path.exists(
        os.path.join(os.path.dirname(containerLock._S_LOCK_DIRECTORY),
                     "escape.lock"),
    )


def fnWriteMalformedJournal(sContainerName):
    """Leave torn bytes where the container's journal belongs."""
    sPath = operationJournal.fsJournalPathFor(sContainerName)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        fileHandle.write("{ not json")


def testAMalformedJournalRefusesTheDirectLane(monkeypatch):
    """A quarantined container is never repaired around its quarantine."""
    listCalls = flistRecordGatewayCalls(monkeypatch)
    fnWriteMalformedJournal(S_CONTAINER_NAME)
    with pytest.raises(RepairRefusedError):
        fdictRepairDirectlyUnderFlock(
            _ConfigStub(), S_CONTAINER_NAME, "restart",
        )
    assert listCalls == []


def testAQuarantineRefusalNamesTheQuarantineNotAPhantomHolder(monkeypatch):
    """The researcher is told WHY, per the module's own comment."""
    flistRecordGatewayCalls(monkeypatch)
    fnWriteMalformedJournal(S_CONTAINER_NAME)
    with pytest.raises(RepairRefusedError) as errorRaised:
        fdictRepairDirectlyUnderFlock(
            _ConfigStub(), S_CONTAINER_NAME, "restart",
        )
    sMessage = str(errorRaised.value)
    assert "pid=0" not in sMessage
    assert "quarantined" in sMessage


def testABusyRefusalNamesTheRunningOperationsNotAPhantomHolder(monkeypatch):
    """A container in use by a journaled worker is not 'held by pid=0'."""
    from vaibify.config import containerLock

    def fileRefuseAsBusy(sContainerName, iTimeout):
        raise containerLock.ContainerBusyOperationError(
            sContainerName, ["operationAlpha", "operationBravo"],
        )

    monkeypatch.setattr(
        containerLock, "ffileAcquireContainerLock", fileRefuseAsBusy,
    )
    with pytest.raises(RepairRefusedError) as errorRaised:
        fdictRepairDirectlyUnderFlock(
            _ConfigStub(), S_CONTAINER_NAME, "restart",
        )
    sMessage = str(errorRaised.value)
    assert "pid=0" not in sMessage
    assert "2 journaled operation(s) running" in sMessage


def testTheReadOnlyLiveWorkViewDoesNotPersistAResolution(monkeypatch):
    """Naming live work is a display read; it never rewrites the journal."""
    fnWriteMalformedJournal(S_CONTAINER_NAME)
    sPath = operationJournal.fsJournalPathFor(S_CONTAINER_NAME)
    with open(sPath, "rb") as fileHandle:
        byteBefore = fileHandle.read()
    with pytest.raises(RepairRefusedError) as errorRaised:
        flistNameLiveWork(S_CONTAINER_NAME)
    assert f"vaibify reconcile {S_CONTAINER_NAME}" in str(errorRaised.value)
    with open(sPath, "rb") as fileHandle:
        assert fileHandle.read() == byteBefore
    assert containerLifecycleRepair.fsDescribeRestartConsequences(
        S_CONTAINER_NAME,
    ).startswith(f"Restarting '{S_CONTAINER_NAME}'")
