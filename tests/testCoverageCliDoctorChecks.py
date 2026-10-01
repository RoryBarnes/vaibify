"""Branch coverage for the project- and host-scope doctor checks.

The journal is exercised for real, in the redirected journal directory
the suite's conftest provides; the container is a scripted stand-in
exposing only the reads each check performs; the daemon's answers come
from its ``docker info`` and ``docker inspect`` boundaries. Every
three-state answer is driven through each of its states, because the
failure these checks exist to prevent is "not checked" rendered as
"ok".
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from vaibify.cli import doctorHostChecks, doctorProjectChecks
from vaibify.cli.preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
    S_SCOPE_HOST, S_SCOPE_PROJECT,
)
from vaibify.config import operationJournal
from vaibify.docker import dockerContext
from vaibify.docker.dockerContext import S_RUNTIME_COLIMA
from vaibify.reproducibility import environmentSnapshot, imageDeposit

S_CONTAINER_NAME = "projectAlpha"
S_REPO_PATH = "/workspace/projectAlpha"
S_PINNED_DIGEST = "registry.example/projectalpha@sha256:" + "a" * 64
S_LIVE_IMAGE_ID = "sha256:" + "b" * 64
S_OTHER_DIGEST = "registry.example/projectalpha@sha256:" + "c" * 64


class _ContainerReads:
    """The container reads the project checks perform, scripted."""

    def __init__(
        self, dictFiles=None, listEntries=None, listExists=None,
        dictOwnership=None, bListingRaises=False, bExistsRaises=False,
    ):
        self.dictFiles = dictFiles or {}
        self.listEntries = listEntries or []
        self.listExists = listExists
        self.dictOwnership = dictOwnership or {}
        self.bListingRaises = bListingRaises
        self.bExistsRaises = bExistsRaises
        self.listOwnershipCalls = []

    def fbaFetchFile(self, sContainerName, sPath):
        if sPath not in self.dictFiles:
            raise FileNotFoundError(sPath)
        return self.dictFiles[sPath]

    def flistDirectoryEntries(self, sContainerName, sPath):
        if self.bListingRaises:
            raise RuntimeError("exec failed")
        return self.listEntries

    def flistContainerPathsExist(self, sContainerName, listPaths):
        if self.bExistsRaises:
            raise RuntimeError("exec failed")
        return self.listExists

    def fdictFindForeignOwnedPaths(self, sContainerName, sRepoPath, iUid):
        self.listOwnershipCalls.append((sContainerName, sRepoPath, iUid))
        return self.dictOwnership


# ---------------------------------------------------------------------
# Repository discovery
# ---------------------------------------------------------------------


@pytest.mark.parametrize("dictReads", [
    {"bListingRaises": True},
    {"listEntries": []},
    {"listEntries": ["projectAlpha"], "bExistsRaises": True},
])
def testDiscoveryAnswersEmptyWhenTheWorkspaceCannotBeRead(dictReads):
    assert doctorProjectChecks.fsDiscoverProjectRepoPath(
        _ContainerReads(**dictReads), S_CONTAINER_NAME, "/workspace",
    ) == ""


def testDiscoveryReturnsTheFirstDirectoryCarryingGit():
    connectionReads = _ContainerReads(
        listEntries=["scratch", "projectAlpha"], listExists=[False, True],
    )
    assert doctorProjectChecks.fsDiscoverProjectRepoPath(
        connectionReads, S_CONTAINER_NAME, "/workspace",
    ) == S_REPO_PATH


# ---------------------------------------------------------------------
# Journal quarantine, against a real journal file
# ---------------------------------------------------------------------


def fsJournalPath(sContainerName):
    """Return the journal file path in the redirected journal directory."""
    return os.path.join(
        operationJournal._S_JOURNAL_DIRECTORY,
        sContainerName + ".operationJournal",
    )


def fiDeadProcessId():
    """Return the pid of a child that has already exited."""
    processDead = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processDead.wait()
    return processDead.pid


def fnJournalUnsettledRecords(sContainerName, iCount):
    """Journal iCount records whose writer died mid-operation."""
    iDeadPid = fiDeadProcessId()
    for iIndex in range(iCount):
        sOperationId = operationJournal.fsPrepareOperation(
            sContainerName, "helper", f"dataFile{iIndex}.csv",
        )
        operationJournal.fnPromoteOperationToInFlight(
            sContainerName, sOperationId,
            {"iHolderPid": iDeadPid, "iHolderProcessGroup": iDeadPid},
        )
        operationJournal.fnMarkOperationNeedsReconciliation(
            sContainerName, sOperationId, "writer died",
        )


def testNoJournalIsASettledPass():
    listResults = doctorProjectChecks.flistCheckJournalQuarantine(
        S_CONTAINER_NAME,
    )
    assert [r.sLevel for r in listResults] == [S_LEVEL_OK]
    assert "no operation journal record" in listResults[0].sMessage


def testLiveOperationIsBusyNotQuarantined():
    sOperationId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "helper", "dataFile.csv",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sOperationId,
        {"iHolderPid": os.getpid(), "iHolderProcessGroup": os.getpgid(0)},
    )
    preflightResult = doctorProjectChecks.flistCheckJournalQuarantine(
        S_CONTAINER_NAME,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_OK
    assert "busy, not quarantined" in preflightResult.sMessage


def testQuarantineNamesTheReconcileCommandAndCapsTheRecordList():
    fnJournalUnsettledRecords(S_CONTAINER_NAME, 7)
    preflightResult = doctorProjectChecks.flistCheckJournalQuarantine(
        S_CONTAINER_NAME,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_FAIL
    assert preflightResult.sScope == S_SCOPE_PROJECT
    assert preflightResult.sCommand == f"vaibify reconcile {S_CONTAINER_NAME}"
    assert "QUARANTINED" in preflightResult.sMessage
    assert preflightResult.sRemediation.count("(helper): dataFile") == 5
    assert "... and 2 more" in preflightResult.sRemediation


def testMalformedJournalQuarantinesAndSaysItCannotBeRead():
    os.makedirs(operationJournal._S_JOURNAL_DIRECTORY, exist_ok=True)
    with open(fsJournalPath(S_CONTAINER_NAME), "w") as fileHandle:
        fileHandle.write("{not json")
    preflightResult = doctorProjectChecks.flistCheckJournalQuarantine(
        S_CONTAINER_NAME,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_FAIL
    assert "malformed" in preflightResult.sMessage
    assert "the journal itself could not be read" in (
        preflightResult.sRemediation
    )


def testUnresolvableJournalIsUnassessedNotPassed():
    preflightResult = doctorProjectChecks.flistCheckJournalQuarantine(
        "../escape",
    )[0]
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED
    assert "could not be resolved" in preflightResult.sMessage


# ---------------------------------------------------------------------
# Envelope currency
# ---------------------------------------------------------------------


def fnPinLiveIdentity(monkeypatch, dictIdentity=None, bRaises=False):
    """Answer the daemon's inspect of the running image."""
    def fdictCapture(sContainerName):
        assert sContainerName == S_CONTAINER_NAME
        if bRaises:
            raise RuntimeError("docker is not on PATH")
        return dictIdentity

    monkeypatch.setattr(
        environmentSnapshot, "fdictCaptureLiveImageIdentity", fdictCapture,
    )


def fdictEnvelopeFiles(objPayload):
    """Return a container file map holding one envelope payload."""
    sPath = S_REPO_PATH + "/.vaibify/environment.json"
    if isinstance(objPayload, bytes):
        return {sPath: objPayload}
    return {sPath: json.dumps(objPayload).encode("utf-8")}


@pytest.mark.parametrize("dictFiles", [
    {},
    fdictEnvelopeFiles(b"{truncated"),
    fdictEnvelopeFiles(["not", "an", "object"]),
    fdictEnvelopeFiles({"sImageDigest": ""}),
])
def testNoReadablePinIsUnassessed(monkeypatch, dictFiles):
    fnPinLiveIdentity(monkeypatch, {
        "sImageDigest": S_PINNED_DIGEST, "sImageId": S_LIVE_IMAGE_ID,
    })
    preflightResult = doctorProjectChecks.flistCheckEnvelopeCurrency(
        _ContainerReads(dictFiles=dictFiles), S_CONTAINER_NAME, S_REPO_PATH,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED


def testUncapturableLiveImageIsUnassessedEvenWithAPin(monkeypatch):
    fnPinLiveIdentity(monkeypatch, bRaises=True)
    preflightResult = doctorProjectChecks.flistCheckEnvelopeCurrency(
        _ContainerReads(
            dictFiles=fdictEnvelopeFiles({"sImageDigest": S_PINNED_DIGEST}),
        ),
        S_CONTAINER_NAME, S_REPO_PATH,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED


@pytest.mark.parametrize("dictPayload", [
    {"sImageDigest": S_PINNED_DIGEST},
    {"dictContainer": {"sImageDigest": S_PINNED_DIGEST}},
])
def testPinMatchingTheRegistryDigestPasses(monkeypatch, dictPayload):
    fnPinLiveIdentity(monkeypatch, {
        "sImageDigest": S_PINNED_DIGEST, "sImageId": S_LIVE_IMAGE_ID,
    })
    preflightResult = doctorProjectChecks.flistCheckEnvelopeCurrency(
        _ContainerReads(dictFiles=fdictEnvelopeFiles(dictPayload)),
        S_CONTAINER_NAME, S_REPO_PATH,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_OK


def testPinMatchingOnlyTheRawImageIdStillPasses(monkeypatch):
    fnPinLiveIdentity(monkeypatch, {
        "sImageDigest": S_OTHER_DIGEST, "sImageId": S_LIVE_IMAGE_ID,
    })
    preflightResult = doctorProjectChecks.flistCheckEnvelopeCurrency(
        _ContainerReads(
            dictFiles=fdictEnvelopeFiles({"sImageDigest": S_LIVE_IMAGE_ID}),
        ),
        S_CONTAINER_NAME, S_REPO_PATH,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_OK


def testDivergedPinWarnsNamingBothImages(monkeypatch):
    fnPinLiveIdentity(monkeypatch, {
        "sImageDigest": S_OTHER_DIGEST, "sImageId": S_LIVE_IMAGE_ID,
    })
    preflightResult = doctorProjectChecks.flistCheckEnvelopeCurrency(
        _ContainerReads(
            dictFiles=fdictEnvelopeFiles({"sImageDigest": S_PINNED_DIGEST}),
        ),
        S_CONTAINER_NAME, S_REPO_PATH,
    )[0]
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert S_PINNED_DIGEST in preflightResult.sMessage
    assert S_OTHER_DIGEST in preflightResult.sMessage
    assert "Regenerate the environment snapshot" in (
        preflightResult.sRemediation
    )


# ---------------------------------------------------------------------
# Workspace ownership
# ---------------------------------------------------------------------


def flistRunOwnership(dictOwnership):
    """Run the ownership check against one scripted probe answer."""
    connectionReads = _ContainerReads(dictOwnership=dictOwnership)
    listResults = doctorProjectChecks.flistCheckWorkspaceOwnership(
        connectionReads, S_CONTAINER_NAME, S_REPO_PATH,
    )
    assert connectionReads.listOwnershipCalls == [
        (S_CONTAINER_NAME, S_REPO_PATH, 1000),
    ]
    return listResults


def testOwnershipProbeThatCouldNotRunIsUnassessed():
    listResults = flistRunOwnership(
        {"bAnswered": False, "sError": "find: not found"},
    )
    assert [r.sLevel for r in listResults] == [S_LEVEL_NOT_CHECKED]
    assert "find: not found" in listResults[0].sMessage


def testRootOwnedWithReadableMountsRecommendsARestart():
    listResults = flistRunOwnership({
        "bAnswered": True, "listRootOwned": ["dataFile.csv"],
        "listOtherOwned": [], "bMountInfoReadable": True,
    })
    assert len(listResults) == 1
    assert listResults[0].sLevel == S_LEVEL_WARN
    assert listResults[0].sCommand == "vaibify stop && vaibify start"
    assert "dataFile.csv" in listResults[0].sMessage


def testRootOwnedWithUnreadableMountsDoesNotPromiseARestart():
    preflightResult = flistRunOwnership({
        "bAnswered": True, "listRootOwned": ["dataFile.csv"],
        "listOtherOwned": [], "bMountInfoReadable": False,
    })[0]
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert preflightResult.sCommand == ""
    assert "will NOT repair" in preflightResult.sRemediation


def testRootAndThirdPartyOwnershipAreSeparateFindings():
    listResults = flistRunOwnership({
        "bAnswered": True, "listRootOwned": ["dataFile.csv"],
        "listOtherOwned": ["plotAlpha.pdf", "notes.txt"],
        "bMountInfoReadable": True,
    })
    assert [r.sName for r in listResults] == [
        "workspace-ownership", "workspace-ownership-foreign",
    ]
    assert "plotAlpha.pdf, notes.txt" in listResults[1].sMessage
    assert "restart will NOT repair" in listResults[1].sRemediation


# ---------------------------------------------------------------------
# Startup observations
# ---------------------------------------------------------------------


def flistRunObservations(dictFiles):
    """Run the startup-observations report against a container file map."""
    return doctorProjectChecks.flistReportStartupObservations(
        _ContainerReads(dictFiles=dictFiles), S_CONTAINER_NAME, "/workspace",
    )


def fdictMarkerFiles(listObservations):
    """Return a container file map holding one readiness marker."""
    return {"/workspace/.vaibify/.entrypoint_ready": json.dumps(
        {"listObservations": listObservations},
    ).encode("utf-8")}


def testMissingMarkerIsUnassessed():
    preflightResult = flistRunObservations({})[0]
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED
    assert "readiness marker could not be read" in preflightResult.sMessage


def testMarkerWithoutObservationsSaysTheImagePredatesThem():
    preflightResult = flistRunObservations(fdictMarkerFiles([]))[0]
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED
    assert "predates them" in preflightResult.sMessage


def testUnconcerningObservationsPassWithTheirCount():
    preflightResult = flistRunObservations(fdictMarkerFiles([
        {"sCode": "dns-resolved", "sVerdict": "ok", "sIso": "t0"},
        {"sCode": "dns-resolved", "sVerdict": "ok", "sIso": "t1"},
    ]))[0]
    assert preflightResult.sLevel == S_LEVEL_OK
    assert "2 observation(s), none concerning" in preflightResult.sMessage


def testConcerningObservationsWarnAsHistoryWithTheirRemedies():
    preflightResult = flistRunObservations(fdictMarkerFiles([
        {"sCode": "dns-resolution-failed", "sVerdict": "warn",
         "sIso": "2026-01-01T00:00:00", "sSubject": "git.example"},
        {"sCode": "a-code-from-the-future", "sVerdict": "warn",
         "sIso": "2026-01-02T00:00:00", "sSubject": "stepAlpha"},
        {"sCode": "dns-resolved", "sVerdict": "ok"},
    ]))[0]
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert "2 concerning observation(s)" in preflightResult.sMessage
    assert "history, not current state" in preflightResult.sMessage
    listLines = preflightResult.sRemediation.split("\n")
    assert len(listLines) == 2
    assert listLines[0].startswith("  [2026-01-01T00:00:00] git.example:")
    assert "vaibify repair dns" in listLines[0]
    assert "does not recognise" in listLines[1]
    assert " -- " not in listLines[1]


# ---------------------------------------------------------------------
# Host checks: deposit scratch space
# ---------------------------------------------------------------------


def fconfigForHost(**dictOverrides):
    """Return a project config carrying only what the host checks read."""
    dictValues = {
        "sProjectName": "projectAlpha", "iCpuLimit": 0,
        "fMemoryLimitGigabytes": 0.0,
    }
    dictValues.update(dictOverrides)
    return SimpleNamespace(**dictValues)


def fnPinRuntime(monkeypatch, sRuntime):
    """Fix the runtime classifier's answer the remedies are chosen from."""
    monkeypatch.setattr(
        doctorHostChecks, "fdictClassifyDockerRuntime", lambda: {
            "sRuntime": sRuntime, "sContextName": "",
            "sColimaProfile": "default", "sEndpoint": "",
            "bDaemonAnswered": True,
        },
    )


def fnPinImageSize(monkeypatch, iBytes):
    """Answer the daemon's image-size query, checking the reference."""
    def fiSize(sImageReference):
        assert sImageReference == "projectAlpha:latest"
        return iBytes

    monkeypatch.setattr(imageDeposit, "fiReadImageSizeBytes", fiSize)


def fnPinFreeBytes(monkeypatch, iFreeBytes, listAskedPaths):
    """Answer disk_usage for the scratch filesystem, recording the path."""
    def tUsage(sPath):
        listAskedPaths.append(sPath)
        return SimpleNamespace(total=0, used=0, free=iFreeBytes)

    monkeypatch.setattr(doctorHostChecks.shutil, "disk_usage", tUsage)


def testDeepestExistingAncestorWalksUpToARealDirectory(tmp_path):
    sMissing = str(tmp_path / "not" / "yet" / "created")
    assert doctorHostChecks._fsDeepestExistingAncestor(sMissing) == str(
        tmp_path,
    )


def testUnsizableImageLeavesTheDepositSpaceUnassessed(monkeypatch):
    fnPinImageSize(monkeypatch, 0)
    preflightResult = doctorHostChecks.flistCheckDepositScratchSpace(
        fconfigForHost(),
    )[0]
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED
    assert preflightResult.sScope == S_SCOPE_HOST


def testEnoughRoomPassesAndMeasuresAnExistingAncestor(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    fnPinImageSize(monkeypatch, 2 ** 30)
    listAskedPaths = []
    fnPinFreeBytes(monkeypatch, 10 * 2 ** 30, listAskedPaths)
    preflightResult = doctorHostChecks.flistCheckDepositScratchSpace(
        fconfigForHost(),
    )[0]
    assert preflightResult.sLevel == S_LEVEL_OK
    assert "needs about 1.2 GB here and 10.0 GB is free" in (
        preflightResult.sMessage
    )
    assert listAskedPaths == [str(tmp_path)]
    assert not os.path.exists(str(tmp_path / ".vaibify")), (
        "doctor created the scratch directory it was only asked about"
    )


def testTooLittleRoomWarnsWithAHostDiskRemedyNotADaemonOne(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    fnPinImageSize(monkeypatch, 4 * 2 ** 30)
    fnPinFreeBytes(monkeypatch, 2 ** 30, [])
    fnPinRuntime(monkeypatch, S_RUNTIME_COLIMA)
    preflightResult = doctorHostChecks.flistCheckDepositScratchSpace(
        fconfigForHost(),
    )[0]
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert "would need about 5.0 GB" in preflightResult.sMessage
    assert "only 1.0 GB is free" in preflightResult.sMessage
    assert preflightResult.sRemediation.startswith("Free space on this")
    assert "colima delete" not in preflightResult.sCommand


# ---------------------------------------------------------------------
# Host checks: CPU and memory against the daemon
# ---------------------------------------------------------------------


def fnPinDaemonInfo(monkeypatch, jsonInfo):
    """Answer ``docker info`` with jsonInfo."""
    monkeypatch.setattr(
        dockerContext, "_fdictReadDockerInfoJson", lambda: jsonInfo,
    )


def testSilentDaemonLeavesAllocationUnassessed(monkeypatch):
    fnPinDaemonInfo(monkeypatch, {})
    listResults = doctorHostChecks.flistCheckResourceAllocation(
        fconfigForHost(),
    )
    assert [r.sLevel for r in listResults] == [S_LEVEL_NOT_CHECKED]


def testHostSizedCpuRequestAboveTheDaemonWarns(monkeypatch):
    fnPinDaemonInfo(monkeypatch, {"NCPU": 4, "MemTotal": 8 * 2 ** 30})
    monkeypatch.setattr(doctorHostChecks.os, "cpu_count", lambda: 10)
    listResults = doctorHostChecks.flistCheckResourceAllocation(
        fconfigForHost(),
    )
    assert [r.sName for r in listResults] == ["cpu-allocation"]
    assert listResults[0].sLevel == S_LEVEL_WARN
    assert "request 9 CPUs but the daemon has 4" in listResults[0].sMessage


def testConfiguredCpuCapWithinTheDaemonPasses(monkeypatch):
    fnPinDaemonInfo(monkeypatch, {"NCPU": 4, "MemTotal": 8 * 2 ** 30})
    monkeypatch.setattr(doctorHostChecks.os, "cpu_count", lambda: 10)
    listResults = doctorHostChecks.flistCheckResourceAllocation(
        fconfigForHost(iCpuLimit=2, fMemoryLimitGigabytes=4.0),
    )
    assert [(r.sName, r.sLevel) for r in listResults] == [
        ("cpu-allocation", S_LEVEL_OK),
    ]
    assert "request 2 CPUs; the daemon reports 4" in listResults[0].sMessage


def testUnknownHostCoreCountStillRequestsAtLeastOne(monkeypatch):
    fnPinDaemonInfo(monkeypatch, {"NCPU": 1, "MemTotal": 2 ** 30})
    monkeypatch.setattr(doctorHostChecks.os, "cpu_count", lambda: None)
    listResults = doctorHostChecks.flistCheckResourceAllocation(
        fconfigForHost(),
    )
    assert "request 1 CPUs" in listResults[0].sMessage
    assert listResults[0].sLevel == S_LEVEL_OK


def testMemoryCapAboveTheDaemonWarnsWithARuntimeRemedy(monkeypatch):
    fnPinDaemonInfo(monkeypatch, {"NCPU": 8, "MemTotal": 8 * 2 ** 30})
    monkeypatch.setattr(doctorHostChecks.os, "cpu_count", lambda: 4)
    fnPinRuntime(monkeypatch, S_RUNTIME_COLIMA)
    listResults = doctorHostChecks.flistCheckResourceAllocation(
        fconfigForHost(fMemoryLimitGigabytes=16.0),
    )
    assert [r.sName for r in listResults] == [
        "cpu-allocation", "memory-allocation",
    ]
    preflightMemory = listResults[1]
    assert preflightMemory.sLevel == S_LEVEL_WARN
    assert "caps memory at 16 GB but the daemon only has 8.0 GB" in (
        preflightMemory.sMessage
    )
    assert preflightMemory.sRemediation
