"""The hub's in-process records name the project they describe.

A container hosts several projects, and a dozen records the hub keeps in
memory were keyed by container alone: a Level 3 verification, an
environment deposit, a falsification run, a dependency scan, the output
checksum cache, the lock verdict, the remote checks, the git-fetch
throttle and the push dedupe. Each one showed one project's state on
another's page, erased another project's record, or reused another
project's answer after the dashboard switched projects (audit,
2026-09-24).

Every test here holds two projects in ONE container with distinct
repositories, and names the mutation it kills.
"""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui import archiveProgress
from vaibify.gui import pipelineServer
from vaibify.gui import verificationProgress
from vaibify.gui import workflowManager
from vaibify.gui.routes import pipelineRoutes
from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import lockSatisfaction


S_CONTAINER = "cid-several-projects"
S_REPO_FIRST = "/workspace/firstProject"
S_REPO_SECOND = "/workspace/secondProject"


class _HeldTask:
    def done(self):
        return False

    def add_done_callback(self, _fnCallback):
        return None


@pytest.fixture(autouse=True)
def fixtureForgetEverything():
    """Leave no module-level record behind for another test."""
    yield
    verificationProgress.DICT_VERIFY_TASKS.pop(S_CONTAINER, None)
    for sRepo in (S_REPO_FIRST, S_REPO_SECOND):
        verificationProgress.fnForgetNoVerdict(S_CONTAINER, sRepo)
        archiveProgress.fnForgetDeposit(S_CONTAINER, sRepo)
    lockSatisfaction.DICT_LAST_LOCK_SATISFACTION.clear()
    syncRoutes._DICT_RECENT_PUSH_RESULTS.clear()


@pytest.mark.falsification
def testAVerificationIsShownOnlyOnItsOwnProject():
    """Kills: reporting the container's verification to every project."""
    verificationProgress.fnRegisterTask(S_CONTAINER, _HeldTask(), {
        "sPhase": "running", "sProjectRepoPath": S_REPO_FIRST,
    })
    assert verificationProgress.fbVerificationIsLive(
        S_CONTAINER, S_REPO_FIRST,
    )
    assert verificationProgress.fdictReadStatus(
        S_CONTAINER, S_REPO_SECOND,
    ) is None
    assert not verificationProgress.fbVerificationIsLive(
        S_CONTAINER, S_REPO_SECOND,
    )


@pytest.mark.falsification
def testAnotherProjectsAttemptKeepsThisProjectsNoVerdictReason():
    """Kills: forgetting every project's reason when one project retries."""
    verificationProgress.fnRecordNoVerdict(
        S_CONTAINER, S_REPO_FIRST, ["the image is not pinned"], 2.5, "",
    )
    verificationProgress.fnForgetNoVerdict(S_CONTAINER, S_REPO_SECOND)
    assert verificationProgress.fdictReadNoVerdict(
        S_CONTAINER, S_REPO_FIRST,
    )["listReasons"] == ["the image is not pinned"]
    assert verificationProgress.fdictReadNoVerdict(
        S_CONTAINER, S_REPO_SECOND,
    ) is None


@pytest.mark.falsification
def testADepositIsShownAndForgottenOnlyForItsOwnProject():
    """Kills: forgetting the container's deposit when another project answers."""
    archiveProgress.fnRecordFailure(
        S_CONTAINER, S_REPO_FIRST, "the upload was refused",
    )
    assert archiveProgress.fdictReadDeposit(S_CONTAINER, S_REPO_SECOND) is None
    archiveProgress.fnForgetDeposit(S_CONTAINER, S_REPO_SECOND)
    assert archiveProgress.fdictReadDeposit(
        S_CONTAINER, S_REPO_FIRST,
    )["sReason"] == "the upload was refused"


@pytest.mark.falsification
def testADependencyScanIsReadOnlyForTheWorkflowItScanned():
    """Kills: one scan per container, drawn on whichever project is open."""
    sFirstPath = S_REPO_FIRST + "/.vaibify/projects/first.json"
    sSecondPath = S_REPO_SECOND + "/.vaibify/projects/second.json"
    dictCtx = {"sourceCodeDeps": {S_CONTAINER: {sFirstPath: {0: {1}}}}}
    assert pipelineServer.fdictCachedSourceCodeDeps(
        dictCtx, S_CONTAINER, {workflowManager.S_LOADED_FROM_KEY: sFirstPath},
    ) == {0: {1}}
    assert pipelineServer.fdictCachedSourceCodeDeps(
        dictCtx, S_CONTAINER,
        {workflowManager.S_LOADED_FROM_KEY: sSecondPath},
    ) is None


class _TwoCachesDocker:
    """Each project's persisted checksum cache holds a different sha."""

    def fbaFetchFile(self, _sContainerId, sPath, iMaxBytes=None):
        sSha = "11" if sPath.startswith(S_REPO_FIRST) else "22"
        return (
            '{"out/a.dat": {"iMtime": 1700, "sSha256": "' + sSha + '"}}'
        ).encode("utf-8")

    def __getattr__(self, sName):
        return MagicMock()


@pytest.mark.falsification
def testEachProjectHasItsOwnChecksumCache():
    """Kills: one checksum cache per container, hydrated from the first project."""
    dictCtx = {"docker": _TwoCachesDocker()}
    dictFirst = pipelineRoutes._fdictManifestShaCache(
        dictCtx, S_CONTAINER, S_REPO_FIRST,
    )
    dictSecond = pipelineRoutes._fdictManifestShaCache(
        dictCtx, S_CONTAINER, S_REPO_SECOND,
    )
    assert dictFirst.get("out/a.dat", {}).get("sSha256") == "11"
    assert dictSecond.get("out/a.dat", {}).get("sSha256") == "22"


@pytest.mark.falsification
def testALockVerdictIsKeptPerProject():
    """Kills: one lock verdict per container, overwritten by the next project."""
    lockSatisfaction.fnRecordLockSatisfaction(
        lockSatisfaction.ftLockVerdictKey(S_CONTAINER, S_REPO_FIRST),
        {"sState": "satisfied", "sFingerprint": "f1"},
    )
    lockSatisfaction.fnRecordLockSatisfaction(
        lockSatisfaction.ftLockVerdictKey(S_CONTAINER, S_REPO_SECOND),
        {"sState": "mismatch", "sFingerprint": "f2"},
    )
    assert lockSatisfaction.fdictReadLockSatisfaction(
        lockSatisfaction.ftLockVerdictKey(S_CONTAINER, S_REPO_FIRST), "f1",
    )["sState"] == "satisfied"


def _fdictPushFor(sWorkdir, sHeadSha, listPushes):
    """Run the push worker for one project with its HEAD probe stubbed."""
    def fdictRecordPush(_dictCtx, _sId, sWorkdirPushed, _request):
        listPushes.append(sWorkdirPushed)
        return {"bSuccess": True, "sCommitHash": "new"}

    with patch.object(
        syncRoutes, "_fsGitHeadShaForDedupeKey", return_value=sHeadSha,
    ), patch.object(
        syncRoutes, "_fnAssertGithubTokenBoundToRemote",
    ), patch.object(
        syncRoutes, "_fdictRunGithubPushBlocking", fdictRecordPush,
    ):
        dictPushed = syncRoutes._fdictPushToGithubBlocking(
            {"docker": MagicMock()}, S_CONTAINER, sWorkdir,
            MagicMock(listFilePaths=["paper.tex"]), "payload-hash", 100.0,
        )
    if dictPushed["tDedupeKey"] and not dictPushed["bDeduped"]:
        syncRoutes._fnRecordRecentPush(
            dictPushed["tDedupeKey"], dictPushed["dictResult"], 100.0,
        )
    return dictPushed


@pytest.mark.falsification
def testAPushIsNeverReplayedToAnotherProject():
    """Kills: a dedupe key without the project, which hands the second
    project the first project's push result for the same file list."""
    listPushes = []
    _fdictPushFor(S_REPO_FIRST, "abc123", listPushes)
    dictSecond = _fdictPushFor(S_REPO_SECOND, "abc123", listPushes)
    assert dictSecond["bDeduped"] is False
    assert listPushes == [S_REPO_FIRST, S_REPO_SECOND]


def testAPushWithAnUnreadableHeadIsNeverDeduplicated():
    """An empty sha names no commit, so it cannot key a replay."""
    listPushes = []
    _fdictPushFor(S_REPO_FIRST, "", listPushes)
    dictAgain = _fdictPushFor(S_REPO_FIRST, "", listPushes)
    assert dictAgain["bDeduped"] is False
    assert listPushes == [S_REPO_FIRST, S_REPO_FIRST]
