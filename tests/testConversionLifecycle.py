"""A containerization converts nothing until the image is in hand.

The hub-owned lifecycle is driven for real: the worker downloads the
author's image from a misbehaving loopback server into a fake image
store, stages it, and commits through the two-lock, journalled
transaction. What is asserted is what a researcher would find on disk
and in the registry: a host project untouched while the download runs
and when it fails, a container exactly once when it succeeds, a
rollback that removes only this transaction's artifacts after a crash,
a recovery that leaves a commit another process holds alone, and two
host projects that cannot both take one container name.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest

from tests.depositFaultServer import FaultInjectingDepositServer
from tests.testCoverageRoutesBRegistry import (  # noqa: F401 - fixtures
    S_HOST_NAME, S_NEW_NAME, fixtureIsolateRegistryAndHome, fsRegisterProject,
)
from tests.testImageAcquisition import FakeImageStore, _FakeImage
from vaibify.config import imageOrigins, pendingConversions, registryManager
from vaibify.docker import disposableContainer
from vaibify.gui import conversionLifecycle, hostModeReturn, serverLifespan
from vaibify.gui.registryRoutes import ConvertToContainerRequest
from vaibify.reproducibility import downloadPartial, reproductionSource, zenodoClient


I_KIBIBYTE = 1024
BA_IMAGE = bytes(range(256)) * (2 * I_KIBIBYTE)   # 512 KiB, served as a plain tar
S_SHA = "sha256:" + hashlib.sha256(BA_IMAGE).hexdigest()
S_PIN = "registry.example/project@sha256:" + "a" * 64
S_DOI = "10.5281/zenodo.5150"
S_TARBALL_NAME = "environment-image.tar"
S_LOADED_ID = "sha256:" + "e" * 64


class _TaggableImage(_FakeImage):
    """A fake image that remembers the names it is given."""

    def __init__(self, sId, sArchitecture, storeImages):
        super().__init__(sId, sArchitecture)
        self._storeImages = storeImages

    def tag(self, sRepository, tag=None):
        self._storeImages.dictHeld[f"{sRepository}:{tag}"] = self


class _ImageStoreWithNames(FakeImageStore):
    """The fake daemon, able to tag and untag what it loaded."""

    def __init__(self):
        super().__init__(bRegistryServes=False)
        self.images.remove = self._fnRemove
        fnLoadOriginal = self.images.load

        def fnLoadTaggable(fileStream):
            listLoaded = fnLoadOriginal(fileStream)
            imageTaggable = _TaggableImage(S_LOADED_ID, "amd64", self)
            self.dictHeld[S_LOADED_ID] = imageTaggable
            return listLoaded
        self.images.load = fnLoadTaggable

    def _fnRemove(self, sReference):
        if sReference not in self.dictHeld:
            raise RuntimeError("404 No such image")
        del self.dictHeld[sReference]

    def flistNamesOf(self, sId):
        return sorted(
            sName for sName, image in self.dictHeld.items()
            if image.id == sId and not sName.startswith("sha256:")
        )


def fdictRecord():
    return {
        "sVersionDoi": S_DOI, "sConceptDoi": "10.5281/zenodo.5149",
        "sTarballName": S_TARBALL_NAME, "sTarballSha256": S_SHA,
        "iTarballBytes": len(BA_IMAGE), "sProvenance": "original",
        "sImageDigest": S_PIN, "sArchitecture": "amd64",
        "sZenodoService": "zenodo",
    }


@pytest.fixture
def fixtureConversionWorld(fixtureIsolateRegistryAndHome, monkeypatch):
    """A host project whose clone pins the fault server's deposit.

    Returns a namespace with the server, the fake store, the project
    directory, and a function that writes the conversion record and
    starts the REAL worker (the root conftest stubs the worker's seam;
    this fixture puts it back).
    """
    sDirectory = fsRegisterProject(fixtureIsolateRegistryAndHome, S_HOST_NAME, "host")
    os.makedirs(os.path.join(sDirectory, ".vaibify"), exist_ok=True)
    with open(os.path.join(sDirectory, ".vaibify", "environment.json"), "w") as fileEnvelope:
        json.dump({"dictContainer": {
            "sImageDigest": S_PIN, "sArchitecture": "amd64",
            "dictImageArchive": fdictRecord(),
        }}, fileEnvelope)
    monkeypatch.setattr(
        reproductionSource, "fdictDescribePinnedEnvironment",
        lambda sPath: {
            "bObtainable": True, "sRefusal": "", "sPinnedImageReference": S_PIN,
            "sRequiredPlatform": "linux/amd64", "bDepositOnRecord": True,
            "sDepositVersionDoi": S_DOI, "sZenodoService": "zenodo",
        },
    )
    storeImages = _ImageStoreWithNames()
    monkeypatch.setattr(
        disposableContainer, "fdockerCreateDisposableClient", lambda *aArgs: storeImages,
    )
    monkeypatch.setattr(
        conversionLifecycle, "_fnStartWorker", conversionLifecycle.fnStartConversionWorker,
    )
    from vaibify.reproducibility import resumableDownload
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_CAP_SECONDS", 0.02)
    with FaultInjectingDepositServer(
        BA_IMAGE, sFilePath="/records/5150/files/" + S_TARBALL_NAME,
    ) as serverFault:
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": serverFault.sBase, "sandbox": serverFault.sBase,
        })
        conversionLifecycle.fnInstallHubContext(_fappFake(), {"docker": None})
        yield SimpleNamespace(
            serverFault=serverFault, storeImages=storeImages,
            sDirectory=sDirectory, sConfigPath=os.path.join(sDirectory, "vaibify.yml"),
            fdictBegin=lambda: _fdictBegin(sDirectory),
        )
        _fnWaitForTheWorkerToEnd()


def _fappFake(dictOwners=None):
    return SimpleNamespace(state=SimpleNamespace(
        dictContainerOwners=dict(dictOwners or {}), iActiveWebSockets=0,
        fLastActivityMonotonic=0.0,
    ))


def _fdictBegin(sDirectory):
    dictRequest = _fdictRequestDump(ConvertToContainerRequest(
        sProjectName=S_NEW_NAME, sEnvironmentSource="archive", iCpuLimit=2,
        listFeatures=[],
    ))
    dictProject = registryManager.fdictGetProject(S_HOST_NAME)
    dictImageSource = {
        "sSource": "archive", "bAllowEmulation": False,
        "sPinnedImageReference": S_PIN, "sRequiredPlatform": "linux/amd64",
        "listAuthorOverlays": [], "sAuthorRecipeFingerprint": "",
        "listAdditionalAgents": [], "listResolvedOverlays": None,
    }
    return conversionLifecycle.fdictBeginPendingConversion(
        dictProject, S_HOST_NAME, S_NEW_NAME, dictRequest, dictImageSource,
        {"sChoice": "as-built", "bWithCredentials": False,
         "sPinnedImageReference": S_PIN, "sImageDigest": ""},
        {"saRelativePaths": ["data/one.csv"], "bRestoreCommittedFiles": False},
    )


def _fdictRequestDump(request):
    fdictDump = getattr(request, "model_dump", None)
    return dict(fdictDump() if fdictDump is not None else request.dict())


def _fnWaitUntil(fbCondition, fSeconds=15.0):
    fDeadline = time.monotonic() + fSeconds
    while time.monotonic() < fDeadline:
        if fbCondition():
            return
        time.sleep(0.02)
    raise AssertionError("the condition did not hold in time")


def _fnWaitForTheWorkerToEnd():
    _fnWaitUntil(lambda: not conversionLifecycle.fbHubHoldsLiveConversion(), 30.0)


def _fsConfig(sConfigPath):
    with open(sConfigPath, "r", encoding="utf-8") as fileConfig:
        return fileConfig.read()


def _fdictView():
    return conversionLifecycle.fdictReadConversionView(S_HOST_NAME)


# ---------------------------------------------------------------------
# Nothing is converted until the image is in hand.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_conversion_converts_nothing_while_the_download_runs_and_cancel_leaves_no_trace(
    fixtureConversionWorld,
):
    """Kills: switching the registry entry when the conversion begins."""
    dictWorld = fixtureConversionWorld
    dictWorld.serverFault.dictBehavior["tStallAfterBytes"] = (100 * I_KIBIBYTE, 60.0)
    sConfigBefore = _fsConfig(dictWorld.sConfigPath)
    dictView = dictWorld.fdictBegin()
    assert dictView["sState"] == pendingConversions.S_STATE_OBTAINING
    _fnWaitUntil(lambda: _fdictView()["dictDownload"]["iBytes"] >= 100 * I_KIBIBYTE)
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
    assert _fsConfig(dictWorld.sConfigPath) == sConfigBefore
    assert _fdictView()["bLive"] is True
    assert serverLifespan._fbHubShouldSelfExit(_fappFake(), {"docker": None}, 0.0) is False
    conversionLifecycle.fnCancelConversion(S_HOST_NAME)
    assert _fdictView() is None
    assert downloadPartial.flistDescribePartials() == []
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert _fsConfig(dictWorld.sConfigPath) == sConfigBefore


@pytest.mark.falsification
def test_a_spent_download_waits_and_try_again_resumes_into_a_commit(fixtureConversionWorld):
    """Kills: never committing after the image is staged."""
    dictWorld = fixtureConversionWorld
    dictWorld.serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE] + [0] * 6
    sConfigBefore = _fsConfig(dictWorld.sConfigPath)
    dictWorld.fdictBegin()
    _fnWaitUntil(lambda: _fdictView()["sState"] == pendingConversions.S_STATE_WAITING)
    dictView = _fdictView()
    assert dictView["dictFailure"]["bPartialKept"] is True
    assert dictView["dictFailure"]["iBytesOnDisk"] == 100 * I_KIBIBYTE
    assert dictView["dictDownload"]["iBytes"] == 100 * I_KIBIBYTE
    assert "attempts" in dictView["dictFailure"]["sSentence"]
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert _fsConfig(dictWorld.sConfigPath) == sConfigBefore
    # The record reads WAITING a moment before the worker thread leaves
    # the live map (it stays live through its commit attempt), and the
    # self-exit veto reads that map; wait for the thread, not the record.
    _fnWaitForTheWorkerToEnd()
    assert serverLifespan._fbHubShouldSelfExit(_fappFake(), {"docker": None}, 0.0) is True
    conversionLifecycle.fnRetryConversion(S_HOST_NAME)
    _fnWaitUntil(lambda: _fdictView() is None, 30.0)
    assert dictWorld.serverFault.listFileRequests[-1]["dictHeaders"]["range"] == (
        f"bytes={100 * I_KIBIBYTE}-"
    )
    dictEntry = registryManager.fdictGetProject(S_NEW_NAME)
    assert dictEntry["sMode"] == "container"
    assert dictEntry["dictImageSource"]["listResolvedOverlays"] == []
    assert dictEntry["dictImageTrust"]["sChoice"] == "as-built"
    assert dictEntry["dictPendingSeed"]["saRelativePaths"] == ["data/one.csv"]
    assert registryManager.fdictGetProject(S_HOST_NAME) is None
    assert "cpuLimit: 2" in _fsConfig(dictWorld.sConfigPath)
    dictOrigin = imageOrigins.fdictReadOriginRecord(S_NEW_NAME)
    assert dictOrigin["sBaseImageId"] == S_LOADED_ID
    assert dictOrigin["sTransactionId"]
    assert dictWorld.storeImages.flistNamesOf(S_LOADED_ID) == [f"{S_NEW_NAME}:latest"]
    assert downloadPartial.flistDescribePartials() == []


@pytest.mark.falsification
def test_a_host_project_containerized_under_its_own_name_commits(fixtureConversionWorld):
    """Kills: the recheck reading the source entry as a taken destination name.

    The QuickStart's reader keeps the project's name when containerizing
    it, so the registry holds the destination name throughout -- as the
    HOST entry the conversion started from. The live journey found the
    commit refusing exactly that (2026-10-14).
    """
    dictWorld = fixtureConversionWorld
    dictRequest = _fdictRequestDump(ConvertToContainerRequest(
        sProjectName=S_HOST_NAME, sEnvironmentSource="archive", iCpuLimit=2,
        listFeatures=[],
    ))
    conversionLifecycle.fdictBeginPendingConversion(
        registryManager.fdictGetProject(S_HOST_NAME), S_HOST_NAME, S_HOST_NAME,
        dictRequest, {
            "sSource": "archive", "bAllowEmulation": False,
            "sPinnedImageReference": S_PIN, "sRequiredPlatform": "linux/amd64",
            "listAuthorOverlays": [], "sAuthorRecipeFingerprint": "",
            "listAdditionalAgents": [], "listResolvedOverlays": None,
        },
        {"sChoice": "as-built", "bWithCredentials": False,
         "sPinnedImageReference": S_PIN, "sImageDigest": ""},
        {"saRelativePaths": [], "bRestoreCommittedFiles": False},
    )
    _fnWaitUntil(lambda: _fdictView() is None, 30.0)
    dictEntry = registryManager.fdictGetProject(S_HOST_NAME)
    assert dictEntry["sMode"] == "container"
    assert dictEntry["dictImageTrust"]["sChoice"] == "as-built"
    assert dictWorld.storeImages.flistNamesOf(S_LOADED_ID) == [f"{S_HOST_NAME}:latest"]
    assert imageOrigins.fdictReadOriginRecord(S_HOST_NAME)["sTransactionId"]


# ---------------------------------------------------------------------
# The commit waits for the project to be closed; Finish commits later.
# ---------------------------------------------------------------------


def _fnStageWithoutCommitting(dictWorld, monkeypatch):
    """Run the worker to READY while a commit is refused as busy."""
    appBusy = _fappFake({S_HOST_NAME: object()})
    conversionLifecycle.fnInstallHubContext(appBusy, {"docker": None})
    dictWorld.fdictBegin()
    _fnWaitUntil(lambda: _fdictView()["sState"] == pendingConversions.S_STATE_READY, 30.0)
    _fnWaitForTheWorkerToEnd()
    return appBusy


@pytest.mark.falsification
def test_an_open_project_refuses_the_commit_and_finish_commits_once_closed(
    fixtureConversionWorld, monkeypatch,
):
    """Kills: committing under a project a browser session still holds."""
    dictWorld = fixtureConversionWorld
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert dictWorld.storeImages.flistNamesOf(S_LOADED_ID) == [
        f"{S_NEW_NAME}:{conversionLifecycle.S_STAGING_TAG}",
    ]
    with pytest.raises(conversionLifecycle.ConversionBusyError):
        conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    appBusy.state.dictContainerOwners.clear()
    conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"
    assert _fdictView() is None
    assert dictWorld.storeImages.flistNamesOf(S_LOADED_ID) == [f"{S_NEW_NAME}:latest"]


@pytest.mark.falsification
def test_the_hub_holds_the_conversion_live_through_the_commit_attempt(
    fixtureConversionWorld, monkeypatch,
):
    """Kills: dropping the live record before the worker's own commit.

    The watchdog's veto and the "already being containerized" refusal
    both read the live record. Dropping it the moment the image was
    staged let the hub read "idle" during the switch, and let a Finish
    from a browser race the worker's commit for the source lock -- the
    crash-after-each-write test met that race on Linux (2026-10-14).
    """
    dictWorld = fixtureConversionWorld
    listLiveDuringCommit = []
    fnRealCommit = conversionLifecycle.fnCommitConversion

    def fnCommitRecordingLiveness(sHostName):
        listLiveDuringCommit.append(conversionLifecycle.fbHubHoldsLiveConversion())
        return fnRealCommit(sHostName)

    monkeypatch.setattr(conversionLifecycle, "fnCommitConversion", fnCommitRecordingLiveness)
    dictWorld.fdictBegin()
    _fnWaitUntil(lambda: _fdictView() is None, 30.0)
    _fnWaitForTheWorkerToEnd()
    assert listLiveDuringCommit == [True]
    assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"


# ---------------------------------------------------------------------
# A crash at each commit step: roll back, or roll forward.
# ---------------------------------------------------------------------


_T_COMMIT_STEPS = ("tag", "config", "origin", "registry")


def _fnCrashAfter(monkeypatch, sStep):
    """Make the named commit write raise AFTER it has written."""
    dictTargets = {
        "tag": (disposableContainer, "fnTagImage"),
        "config": (conversionLifecycle, "_fnRewriteConfigDurably"),
        "origin": (imageOrigins, "fnWriteOriginRecord"),
        "registry": (registryManager, "fnConvertProjectToContainer"),
    }
    moduleTarget, sAttribute = dictTargets[sStep]
    fnReal = getattr(moduleTarget, sAttribute)

    def fnWriteThenDie(*aArguments, **dictKeywords):
        fnReal(*aArguments, **dictKeywords)
        raise RuntimeError(f"power lost after the {sStep} write")

    monkeypatch.setattr(moduleTarget, sAttribute, fnWriteThenDie)


@pytest.mark.falsification
@pytest.mark.parametrize("sStep", _T_COMMIT_STEPS)
def test_a_commit_cut_short_rolls_back_before_the_switch_and_forward_after(
    fixtureConversionWorld, monkeypatch, sStep,
):
    """Kills: a rollback that leaves the rewritten vaibify.yml in place."""
    dictWorld = fixtureConversionWorld
    sConfigBefore = _fsConfig(dictWorld.sConfigPath)
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    appBusy.state.dictContainerOwners.clear()
    with pytest.MonkeyPatch.context() as patchCrash:
        _fnCrashAfter(patchCrash, sStep)
        with pytest.raises(RuntimeError, match="power lost"):
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    assert pendingConversions.fdictReadConversionRecord(S_HOST_NAME)["dictJournal"]
    conversionLifecycle.fnRecoverPendingConversionsAtStartup()
    if sStep == "registry":
        assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"
        assert _fdictView() is None
        assert dictWorld.storeImages.flistNamesOf(S_LOADED_ID) == [f"{S_NEW_NAME}:latest"]
        return
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
    assert _fsConfig(dictWorld.sConfigPath) == sConfigBefore
    assert imageOrigins.fdictReadOriginRecord(S_NEW_NAME) is None
    assert dictWorld.storeImages.flistNamesOf(S_LOADED_ID) == [
        f"{S_NEW_NAME}:{conversionLifecycle.S_STAGING_TAG}",
    ]
    dictRecord = pendingConversions.fdictReadConversionRecord(S_HOST_NAME)
    assert dictRecord["sState"] == pendingConversions.S_STATE_READY
    assert dictRecord["dictJournal"] is None
    conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"


def test_a_rollback_never_removes_a_tag_another_entry_holds(fixtureConversionWorld, monkeypatch):
    """A :latest tag the registry already names belongs to that conversion."""
    dictWorld = fixtureConversionWorld
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    appBusy.state.dictContainerOwners.clear()
    with pytest.MonkeyPatch.context() as patchCrash:
        _fnCrashAfter(patchCrash, "origin")
        with pytest.raises(RuntimeError):
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    # Somebody else's successful conversion now owns the name and the tag.
    sOther = fsRegisterProject(os.path.dirname(os.path.dirname(dictWorld.sDirectory)), "otherHost", "host")
    del sOther
    registryManager.fnConvertProjectToContainer("otherHost", S_NEW_NAME)
    conversionLifecycle.fnRecoverPendingConversionsAtStartup()
    assert f"{S_NEW_NAME}:latest" in dictWorld.storeImages.flistNamesOf(S_LOADED_ID)
    assert pendingConversions.fdictReadConversionRecord(S_HOST_NAME)["dictJournal"] is None


# ---------------------------------------------------------------------
# Locks: a commit another process holds is left alone; one name, one host.
# ---------------------------------------------------------------------


S_LOCK_HOLDER_PROGRAM = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from vaibify.config import pendingConversions
    pendingConversions._S_CONVERSIONS_DIRECTORY = sys.argv[2]
    fileLock = (
        pendingConversions.ffileTakeSourceLock(sys.argv[4])
        if sys.argv[3] == "source"
        else pendingConversions.ffileTakeDestinationLock(sys.argv[4])
    )
    print("held", flush=True)
    time.sleep(60)
""")


def _fprocessHoldLock(sKind, sName):
    sRepository = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(conversionLifecycle.__file__),
    )))
    processChild = subprocess.Popen(
        [sys.executable, "-c", S_LOCK_HOLDER_PROGRAM, sRepository,
         pendingConversions._S_CONVERSIONS_DIRECTORY, sKind, sName],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert processChild.stdout.readline().strip() == "held"
    return processChild


@pytest.mark.falsification
def test_recovery_leaves_a_commit_another_process_holds_alone(
    fixtureConversionWorld, monkeypatch,
):
    """A real process holds the source lock; recovery does nothing until it dies.

    Kills: recovery that does not take the commit lock first.
    """
    dictWorld = fixtureConversionWorld
    sConfigBefore = _fsConfig(dictWorld.sConfigPath)
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    appBusy.state.dictContainerOwners.clear()
    with pytest.MonkeyPatch.context() as patchCrash:
        _fnCrashAfter(patchCrash, "config")
        with pytest.raises(RuntimeError):
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    processHolder = _fprocessHoldLock("source", S_HOST_NAME)
    try:
        conversionLifecycle.fnRecoverPendingConversionsAtStartup()
        assert pendingConversions.fdictReadConversionRecord(S_HOST_NAME)["dictJournal"]
        assert _fsConfig(dictWorld.sConfigPath) != sConfigBefore
        with pytest.raises(pendingConversions.ConversionLockHeldError):
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    finally:
        processHolder.kill()
        processHolder.wait(timeout=10)
    conversionLifecycle.fnRecoverPendingConversionsAtStartup()
    assert pendingConversions.fdictReadConversionRecord(S_HOST_NAME)["dictJournal"] is None
    assert _fsConfig(dictWorld.sConfigPath) == sConfigBefore


def test_a_destination_name_another_process_is_committing_is_refused_by_name(
    fixtureConversionWorld, monkeypatch,
):
    dictWorld = fixtureConversionWorld
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    appBusy.state.dictContainerOwners.clear()
    processHolder = _fprocessHoldLock("destination", S_NEW_NAME)
    try:
        with pytest.raises(pendingConversions.ConversionLockHeldError, match=S_NEW_NAME):
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
        assert registryManager.fbIsHostProject(S_HOST_NAME)
    finally:
        processHolder.kill()
        processHolder.wait(timeout=10)


def test_a_destination_name_another_operation_holds_waits_like_an_open_project(
    fixtureConversionWorld, monkeypatch,
):
    """A rename or start of the destination name holds its lifecycle lock.

    The commit is then BUSY, not refused: the staged image is kept and
    Finish commits once the name is free.
    """
    from tests.lifecycleLockHolder import (
        fcontextHoldLifecycleLockInAnotherProcess,
    )
    dictWorld = fixtureConversionWorld
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    appBusy.state.dictContainerOwners.clear()
    with fcontextHoldLifecycleLockInAnotherProcess(S_NEW_NAME, "renamed"):
        with pytest.raises(
            conversionLifecycle.ConversionBusyError,
            match=f"'{S_NEW_NAME}' is being renamed",
        ):
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
        assert registryManager.fbIsHostProject(S_HOST_NAME)
    conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"


@pytest.mark.falsification
def test_two_host_projects_cannot_both_take_one_container_name(
    fixtureConversionWorld, monkeypatch,
):
    """Kills: the commit not rechecking that the destination name is free."""
    dictWorld = fixtureConversionWorld
    appBusy = _fnStageWithoutCommitting(dictWorld, monkeypatch)
    appBusy.state.dictContainerOwners.clear()
    # The other host project won the name first, with the same image.
    fsRegisterProject(os.path.dirname(os.path.dirname(dictWorld.sDirectory)), "otherHost", "host")
    registryManager.fnConvertProjectToContainer("otherHost", S_NEW_NAME)
    imageOrigins.fnWriteOriginRecord(S_NEW_NAME, {"sTransactionId": "winner"})
    sConfigBefore = _fsConfig(dictWorld.sConfigPath)
    with pytest.raises(conversionLifecycle.ConversionRefusedError, match="taken now"):
        conversionLifecycle.fnCommitConversion(S_HOST_NAME)
    assert imageOrigins.fdictReadOriginRecord(S_NEW_NAME) == {"sTransactionId": "winner"}
    assert _fsConfig(dictWorld.sConfigPath) == sConfigBefore
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert f"{S_NEW_NAME}:latest" not in dictWorld.storeImages.flistNamesOf(S_LOADED_ID)


# ---------------------------------------------------------------------
# Interruption: a hub that died mid-download.
# ---------------------------------------------------------------------


def test_a_record_without_a_live_worker_reads_as_interrupted_and_resumes(fixtureConversionWorld):
    dictWorld = fixtureConversionWorld
    dictWorld.serverFault.dictBehavior["tStallAfterBytes"] = (100 * I_KIBIBYTE, 60.0)
    dictWorld.fdictBegin()
    _fnWaitUntil(lambda: _fdictView()["dictDownload"]["iBytes"] >= 100 * I_KIBIBYTE)
    # The hub "dies": the live record is forgotten without a cancel.
    with conversionLifecycle._LOCK_LIVE:
        dictLive = conversionLifecycle._DICT_LIVE.pop(S_HOST_NAME)
    from vaibify.reproducibility import resumableDownload
    resumableDownload.fnRequestCancel(dictLive["dictControl"])
    dictLive["threadWorker"].join(10)
    dictView = _fdictView()
    assert dictView["sState"] == conversionLifecycle.S_VIEW_INTERRUPTED
    assert dictView["bLive"] is False
    dictWorld.serverFault.dictBehavior["tStallAfterBytes"] = None
    dictWorld.serverFault.eventRelease.set()
    conversionLifecycle.fnRetryConversion(S_HOST_NAME)
    _fnWaitUntil(lambda: _fdictView() is None, 30.0)
    assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"


# ---------------------------------------------------------------------
# The way back for an entry v0.1.0 stranded.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_only_the_conversions_edits_restore_the_committed_config_silently():
    """Kills: treating a runtime-key difference as the researcher's own edit."""
    dictCommitted = {"projectName": "demo", "pythonPackages": ["numpy"],
                     "features": {"jupyter": True}}
    dictConverted = {
        "projectName": "demo-container", "pythonPackages": ["numpy"],
        "cpuLimit": 2, "memoryLimitGigabytes": 4.0,
        "features": {"jupyter": True, "claude": True, "claudeAutoUpdate": True},
    }
    assert hostModeReturn.fbOnlyTheConversionsEdits(dictConverted, dictCommitted) is True
    dictEdited = dict(dictConverted, pythonPackages=["numpy", "scipy"])
    assert hostModeReturn.fbOnlyTheConversionsEdits(dictEdited, dictCommitted) is False
    assert hostModeReturn.fbOnlyTheConversionsEdits(dictConverted, None) is False


def test_a_stranded_entry_returns_to_host_mode(fixtureIsolateRegistryAndHome, monkeypatch):
    sDirectory = fsRegisterProject(fixtureIsolateRegistryAndHome, S_HOST_NAME, "host")
    registryManager.fnConvertProjectToContainer(
        S_HOST_NAME, S_NEW_NAME, {"sSource": "archive"}, {"sChoice": "as-built"},
    )
    monkeypatch.setattr(
        "vaibify.config.connectionAvailability.fbDockerReachable", lambda connection: True,
    )
    monkeypatch.setattr(
        disposableContainer, "fdockerCreateDisposableClient", lambda *aArgs: FakeImageStore(),
    )
    monkeypatch.setattr(
        reproductionSource, "fsReadCommittedFileOrNone",
        lambda sRepo, sRelative: f"projectName: {S_HOST_NAME}\n",
    )
    dictProject = registryManager.fdictGetProject(S_NEW_NAME)
    with open(dictProject["sConfigPath"], "a", encoding="utf-8") as fileConfig:
        fileConfig.write("cpuLimit: 2\n")
    dictJudgement = hostModeReturn.fdictJudgeReturnToHostMode(dictProject, object())
    assert dictJudgement["bOnlyTheConversionsEdits"] is True
    hostModeReturn.fnRestoreCommittedConfig(dictProject, dictJudgement["sCommittedConfig"])
    assert registryManager.fsReturnProjectToHost(S_NEW_NAME) == S_HOST_NAME
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
    assert _fsConfig(os.path.join(sDirectory, "vaibify.yml")) == f"projectName: {S_HOST_NAME}\n"


def test_an_entry_with_an_origin_record_is_not_stranded(fixtureIsolateRegistryAndHome, monkeypatch):
    fsRegisterProject(fixtureIsolateRegistryAndHome, S_HOST_NAME, "host")
    registryManager.fnConvertProjectToContainer(S_HOST_NAME, S_NEW_NAME, {"sSource": "archive"})
    imageOrigins.fnWriteOriginRecord(S_NEW_NAME, {"sBaseImageId": "x"})
    with pytest.raises(hostModeReturn.HostModeReturnRefusedError, match="obtained its image already"):
        hostModeReturn.fdictJudgeReturnToHostMode(registryManager.fdictGetProject(S_NEW_NAME), object())


@pytest.mark.falsification
def test_an_entry_whose_image_exists_is_not_stranded(fixtureIsolateRegistryAndHome, monkeypatch):
    """Kills: returning to host mode an entry whose image the daemon still holds."""
    fsRegisterProject(fixtureIsolateRegistryAndHome, S_HOST_NAME, "host")
    registryManager.fnConvertProjectToContainer(S_HOST_NAME, S_NEW_NAME, {"sSource": "archive"})
    monkeypatch.setattr(
        "vaibify.config.connectionAvailability.fbDockerReachable", lambda connection: True,
    )
    storeImages = FakeImageStore()
    storeImages.dictHeld[f"{S_NEW_NAME}:latest"] = _FakeImage("sha256:" + "9" * 64, "amd64")
    monkeypatch.setattr(
        disposableContainer, "fdockerCreateDisposableClient", lambda *aArgs: storeImages,
    )
    with pytest.raises(hostModeReturn.HostModeReturnRefusedError, match="An image exists"):
        hostModeReturn.fdictJudgeReturnToHostMode(registryManager.fdictGetProject(S_NEW_NAME), object())


# ---------------------------------------------------------------------
# Findings of the 2026-10-14 adversarial review of the lifecycle.
# ---------------------------------------------------------------------


def _fnPauseTheCommitBeforeItsFirstWrite(monkeypatch):
    """Hold a commit after its rechecks, lock held, nothing journalled yet."""
    eventInside, eventFinish = threading.Event(), threading.Event()
    fnOriginalRefuse = conversionLifecycle._fnRefuseIfBusy

    def fnRefuseThenPause(sHostName):
        fnOriginalRefuse(sHostName)
        eventInside.set()
        assert eventFinish.wait(timeout=10.0)

    monkeypatch.setattr(conversionLifecycle, "_fnRefuseIfBusy", fnRefuseThenPause)
    return eventInside, eventFinish


@pytest.mark.falsification
def test_cancel_cannot_slip_under_a_commit_that_holds_the_source_lock(
    fixtureConversionWorld, monkeypatch,
):
    """Kills: cancelling without the commit's source lock, so the record
    is removed while the commit is between its rechecks and its writes.
    """
    dictWorld = fixtureConversionWorld
    _fnStageWithoutCommitting(dictWorld, monkeypatch)
    conversionLifecycle.fnInstallHubContext(_fappFake(), {"docker": None})
    eventInside, eventFinish = _fnPauseTheCommitBeforeItsFirstWrite(monkeypatch)
    listErrors = []

    def fnCommit():
        try:
            conversionLifecycle.fnCommitConversion(S_HOST_NAME)
        except Exception as error:  # noqa: BLE001 -- the test reads it
            listErrors.append(error)

    threadCommit = threading.Thread(target=fnCommit, daemon=True)
    threadCommit.start()
    assert eventInside.wait(timeout=10.0)
    try:
        with pytest.raises(pendingConversions.ConversionLockHeldError):
            conversionLifecycle.fnCancelConversion(S_HOST_NAME)
        assert _fdictView() is not None, "the record was removed under the commit"
    finally:
        eventFinish.set()
        threadCommit.join(timeout=10.0)
    assert listErrors == []
    assert registryManager.fdictGetProject(S_NEW_NAME)["sMode"] == "container"
    assert _fdictView() is None


@pytest.mark.falsification
def test_cancel_refuses_a_journalled_record_and_leaves_its_journal(
    fixtureConversionWorld, monkeypatch,
):
    """A record carrying a journal is a commit awaiting recovery; Cancel
    must not delete the one thing recovery reads.

    Kills: removing a journalled record on Cancel.
    """
    dictWorld = fixtureConversionWorld
    _fnStageWithoutCommitting(dictWorld, monkeypatch)
    dictRecord = pendingConversions.fdictReadConversionRecord(S_HOST_NAME)
    pendingConversions.fnWriteConversionRecord({
        **dictRecord,
        "sState": pendingConversions.S_STATE_COMMITTING,
        "dictJournal": {"sTransactionId": "review-probe", "sConfigBase64": "",
                        "sRunningImageId": ""},
    })
    with pytest.raises(conversionLifecycle.ConversionRefusedError) as errorRaised:
        conversionLifecycle.fnCancelConversion(S_HOST_NAME)
    assert "being finished" in str(errorRaised.value)
    dictAfter = pendingConversions.fdictReadConversionRecord(S_HOST_NAME)
    assert dictAfter is not None and dictAfter["dictJournal"]["sTransactionId"] == "review-probe"
