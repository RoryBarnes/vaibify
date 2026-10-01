"""The git-tracked snapshot scope degrades honestly when a read fails.

Every entry point that weighs, observes or records the tracked scope
depends on a container read that can fail. The contract is that a
failure is NEVER a partial answer: an observation refuses through the
caller's own refusal, a staleness poll reports ``bSuccess`` False, a
probe reports ``bOffered`` False, and each says why. The container is
the only boundary, so it is a scripted double; the inventory store is
the real one under conftest's redirected snapshot-scope directory.
"""

import json
import os

import pytest

from vaibify.gui import agentCouncilSnapshotScope as snapshotScope


S_CONTAINER_ID = "c0ntainer0id01"
S_REPO_ROOT = "/workspace/repoAlpha"
S_RESOURCE = "projectAlpha"
DICT_BOUNDS = {"iMaxSnapshotFileCount": 100,
               "iMaxSnapshotMemberBytes": 10_000,
               "iMaxSnapshotTotalBytes": 100_000}


class CaptureRefusedError(Exception):
    """The capture's own refusal type, as a caller would supply it."""


def fnRefuseCapture(sReason):
    raise CaptureRefusedError(sReason)


def fbExcludeNothing(sPath):
    return None


class ScriptedRepositoryDocker:
    """Answers the three repository reads the scope module makes."""

    def __init__(self, dictTrackedRead=None, dictUntrackedRead=None,
                 errorOnTrackedRead=None):
        self.dictTrackedRead = dictTrackedRead or {
            "bSuccess": True, "sHeadSha": "headcommit0001",
            "sPorcelainDigest": "porcelain0001", "iChangedCount": 0,
            "dictEntries": {"stepAlpha/dataFile.csv": {
                "sMode": "100644", "listStages": [0], "sType": "file",
                "sIdentity": "1" * 40, "iSizeBytes": 120}}}
        self.dictUntrackedRead = dictUntrackedRead or {
            "bSuccess": True, "bComplete": True,
            "listEntries": [["scratch/notes.txt", "untracked", 40]]}
        self.errorOnTrackedRead = errorOnTrackedRead
        self.listReads = []

    def fdictFetchTrackedIdentities(self, sContainerId, sRepoRoot):
        self.listReads.append(("tracked", sContainerId, sRepoRoot))
        if self.errorOnTrackedRead is not None:
            raise self.errorOnTrackedRead
        return self.dictTrackedRead

    def fdictFetchUntrackedInventory(self, sContainerId, sRepoRoot):
        self.listReads.append(("untracked", sContainerId, sRepoRoot))
        return self.dictUntrackedRead

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoRoot):
        self.listReads.append(("worktree", sContainerId, sRepoRoot))
        return {"bSuccess": True, "sHeadSha": "whole"}


DICT_FAILED_READ = {"bSuccess": False, "sReason": "git is not installed"}


# ----- observation ---------------------------------------------------------


def testAFailedTrackedReadRefusesTheCaptureThroughItsOwnRefusal():
    dockerRepository = ScriptedRepositoryDocker(
        dictTrackedRead=DICT_FAILED_READ)
    with pytest.raises(CaptureRefusedError) as excInfo:
        snapshotScope.fdictObserveTrackedScope(
            dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, fbExcludeNothing,
            fnRefuseCapture)
    assert "(git is not installed)" in str(excInfo.value)
    assert "coherence cannot be established" in str(excInfo.value)


def testAFailedTrackedReadWithNoDetailSaysSo():
    dockerRepository = ScriptedRepositoryDocker(
        dictTrackedRead={"bSuccess": False})
    with pytest.raises(CaptureRefusedError) as excInfo:
        snapshotScope.fdictObserveTrackedScope(
            dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, fbExcludeNothing,
            fnRefuseCapture)
    assert "(no detail)" in str(excInfo.value)


def testStalenessInTrackedScopeReportsAFailedReadAsUnsuccessful():
    dockerRepository = ScriptedRepositoryDocker(
        dictTrackedRead=DICT_FAILED_READ)
    dictObserved = snapshotScope.fdictObserveForStaleness(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT,
        snapshotScope.fdictComposeSnapshotScope("gitTracked"),
        fbExcludeNothing)
    assert dictObserved["bSuccess"] is False
    assert "git is not installed" in dictObserved["sReason"]
    assert [tRead[0] for tRead in dockerRepository.listReads] == ["tracked"]


def testStalenessInTrackedScopeReportsAnUnrepresentableIndex():
    dockerRepository = ScriptedRepositoryDocker(dictTrackedRead={
        "bSuccess": True, "dictEntries": {"conflicted.txt": {
            "sMode": "100644", "listStages": [1, 2, 3], "sType": "file"}}})
    dictObserved = snapshotScope.fdictObserveForStaleness(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT,
        snapshotScope.fdictComposeSnapshotScope("gitTracked"),
        fbExcludeNothing)
    assert dictObserved["bSuccess"] is False
    assert "unresolved merge conflicts" in dictObserved["sReason"]
    assert "'conflicted.txt'" in dictObserved["sReason"]


def testStalenessInWholeDirectoryScopeReadsTheWorktree():
    dockerRepository = ScriptedRepositoryDocker(
        dictTrackedRead=DICT_FAILED_READ)
    dictObserved = snapshotScope.fdictObserveForStaleness(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, None, fbExcludeNothing)
    assert dictObserved == {"bSuccess": True, "sHeadSha": "whole"}
    assert dockerRepository.listReads == [
        ("worktree", S_CONTAINER_ID, S_REPO_ROOT)]


# ----- the probe -------------------------------------------------------------


def testAProbeWhoseTrackedReadFailsOffersNothing():
    dictOffer = snapshotScope.fdictProbeTrackedScope(
        ScriptedRepositoryDocker(dictTrackedRead=DICT_FAILED_READ),
        S_CONTAINER_ID, S_REPO_ROOT, DICT_BOUNDS, fbExcludeNothing)
    assert dictOffer["bOffered"] is False
    assert "git is not installed" in dictOffer["sReason"]
    assert "sObservationId" not in dictOffer


def testAProbeWhoseUntrackedInventoryFailsOffersNothing():
    dictOffer = snapshotScope.fdictProbeTrackedScope(
        ScriptedRepositoryDocker(dictUntrackedRead={
            "bSuccess": False, "sReason": "permission denied"}),
        S_CONTAINER_ID, S_REPO_ROOT, DICT_BOUNDS, fbExcludeNothing)
    assert dictOffer == {
        "bOffered": False,
        "sReason": "the untracked-file inventory could not be read "
                   "(permission denied)"}


def testAProbeSurvivesAnUnreadableInventoryEntryAndSupersedesTheOld():
    dockerRepository = ScriptedRepositoryDocker()
    dictFirst = snapshotScope.fdictProbeTrackedScope(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, DICT_BOUNDS,
        fbExcludeNothing)
    sInventories = os.path.join(
        snapshotScope.fsResolveSnapshotScopeDirectory(), "inventories")
    sDangling = os.path.join(sInventories, "dangling.jsonl.gz")
    os.symlink(os.path.join(sInventories, "absentTarget"), sDangling)
    dictSecond = snapshotScope.fdictProbeTrackedScope(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, DICT_BOUNDS,
        fbExcludeNothing)
    assert dictSecond["bOffered"] is True
    assert dictSecond["sObservationId"] != dictFirst["sObservationId"]
    assert os.path.islink(sDangling), "an unreadable entry is skipped"
    assert not os.path.exists(os.path.join(
        sInventories, dictFirst["sObservationId"] + ".jsonl.gz"))
    with pytest.raises(snapshotScope.OmissionInventoryExpiredError):
        snapshotScope.fdictReadOmissionPage(
            dictFirst["sObservationId"], S_CONTAINER_ID, "scratch",
            "untracked")


@pytest.mark.parametrize("sObservationId", [
    "", "../../etc/passwd", "ABCDEF" * 6, "0" * 31, None])
def testAMalformedObservationIdIsRefusedBeforeAnyFileIsOpened(
        sObservationId):
    with pytest.raises(snapshotScope.OmissionInventoryExpiredError) as \
            excInfo:
        snapshotScope.fdictReadOmissionPage(
            sObservationId, S_CONTAINER_ID, "scratch", "untracked")
    assert str(excInfo.value) == "unknown observation"


# ----- the remembered scope ----------------------------------------------------


def testARememberedScopeFromAnotherVersionReadsAsNothingRemembered():
    sDirectory = snapshotScope.fsResolveSnapshotScopeDirectory()
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "rememberedScopes.json"), "w",
              encoding="utf-8") as fileRemembered:
        json.dump({f"{S_RESOURCE}|{S_REPO_ROOT}": {
            "sScope": "gitTracked", "iScopeVersion": 99}}, fileRemembered)
    assert snapshotScope.fdictReadRememberedScope(
        S_RESOURCE, S_REPO_ROOT) is None


def testARememberedScopeRoundTrips():
    snapshotScope.fnRememberScope(
        S_RESOURCE, S_REPO_ROOT,
        snapshotScope.fdictComposeSnapshotScope("gitTracked"))
    assert snapshotScope.fdictReadRememberedScope(
        S_RESOURCE, S_REPO_ROOT) == {"sScope": "gitTracked",
                                      "iScopeVersion": 1}
    assert snapshotScope.fdictReadRememberedScope(
        "projectBeta", S_REPO_ROOT) is None


# ----- capture-time omissions ---------------------------------------------------


def testATrackedCaptureThatCannotListItsOmissionsIsRefused(tmp_path):
    dockerRepository = ScriptedRepositoryDocker(dictUntrackedRead={
        "bSuccess": False, "sReason": "find timed out"})
    dictIdentity = snapshotScope.fdictObserveTrackedScope(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, fbExcludeNothing,
        fnRefuseCapture)
    sInventoryPath = str(tmp_path / "omissions.jsonl.gz")
    with pytest.raises(CaptureRefusedError) as excInfo:
        snapshotScope.fdictRecordCaptureOmissions(
            dockerRepository, S_CONTAINER_ID, S_REPO_ROOT, sInventoryPath,
            snapshotScope.fdictComposeSnapshotScope("gitTracked"),
            dictIdentity, fnRefuseCapture)
    assert "(find timed out)" in str(excInfo.value)
    assert "cannot say what it left out" in str(excInfo.value)
    assert not os.path.exists(sInventoryPath)


def testAWholeDirectoryCaptureRecordsNoOmissions(tmp_path):
    dockerRepository = ScriptedRepositoryDocker()
    assert snapshotScope.fdictRecordCaptureOmissions(
        dockerRepository, S_CONTAINER_ID, S_REPO_ROOT,
        str(tmp_path / "omissions.jsonl.gz"),
        snapshotScope.fdictComposeSnapshotScope("wholeDirectory"),
        {}, fnRefuseCapture) == {}
    assert dockerRepository.listReads == []


# ----- the capabilities offer ----------------------------------------------------


def testAnUnweighableTrackedSetLeavesTheSizeRefusalAndSaysWhy():
    dockerRepository = ScriptedRepositoryDocker(
        errorOnTrackedRead=PermissionError("container read denied"))
    dictCapabilities = {
        "sUnavailableIn": "snapshot-too-large",
        "sReason": "The repository is too large.",
        "dictSnapshotFeasibility": dict(
            DICT_BOUNDS, bFits=False, bResolvableByExcludingFiles=False),
    }
    snapshotScope.fnApplyTrackedScopeOffer(
        dockerRepository, S_CONTAINER_ID, S_RESOURCE, S_REPO_ROOT,
        dictCapabilities)
    assert dictCapabilities["dictTrackedScopeOffer"] == {
        "bOffered": False,
        "sReason": "the git-tracked files could not be weighed "
                   "(PermissionError)"}
    assert dictCapabilities["sUnavailableIn"] == "snapshot-too-large"
    assert dictCapabilities["sReason"].startswith(
        "The repository is too large. Copying only the files git tracks "
        "does not help: the git-tracked files could not be weighed")
    assert "bNeedsSnapshotChoice" not in dictCapabilities
