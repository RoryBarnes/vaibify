"""Per-container state must be evicted, and must not cross projects.

Two kinds of in-process state are keyed by the container a researcher
opened, and each had a lifecycle hole:

* three caches in the route context (the live image identity, the
  pinned-image presence, the pending project-creation suggestion) were
  written per container and never swept when the container went away,
  so a rebuilt container inherited the previous container's image
  identity;
* the environment-archive deposit record was keyed by container alone,
  so a second project's deposit erased the first project's failed record
  -- and with it the reason the researcher still had to be told.

The discovery test reads the SOURCE for every route-context cache
written under a container id, so a cache added tomorrow cannot be
forgotten the same way.
"""

import ast
import pathlib

import pytest

from vaibify.gui import archiveProgress, verificationProgress
from vaibify.gui.fileStatusManager import (
    _LIST_CONTAINER_KEYED_CACHES,
    fsetSweepAllContainerCaches,
)

PATH_PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "vaibify"


def _fsContextCacheName(nodeExpression):
    """Return ``x`` for ``dictCtx["x"]``, ``.get("x")`` or ``.setdefault("x")``."""
    if (
        isinstance(nodeExpression, ast.Subscript)
        and isinstance(nodeExpression.value, ast.Name)
        and nodeExpression.value.id == "dictCtx"
        and isinstance(nodeExpression.slice, ast.Constant)
    ):
        return nodeExpression.slice.value
    if (
        isinstance(nodeExpression, ast.Call)
        and isinstance(nodeExpression.func, ast.Attribute)
        and nodeExpression.func.attr in ("get", "setdefault")
        and isinstance(nodeExpression.func.value, ast.Name)
        and nodeExpression.func.value.id == "dictCtx"
        and nodeExpression.args
        and isinstance(nodeExpression.args[0], ast.Constant)
    ):
        return nodeExpression.args[0].value
    return ""


def _fsetFindContainerKeyedContextCaches():
    """Return every cache name the source writes under a container id."""
    setNames = set()
    for pathModule in PATH_PACKAGE.rglob("*.py"):
        treeModule = ast.parse(
            pathModule.read_text(encoding="utf-8", errors="replace"),
        )
        for node in ast.walk(treeModule):
            if not (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.slice, ast.Name)
                and "ContainerId" in node.slice.id
            ):
                continue
            sName = _fsContextCacheName(node.value)
            if sName:
                setNames.add(sName)
    return setNames


@pytest.mark.falsification
def testEveryCacheWrittenUnderAContainerIdIsInTheSweep():
    """A per-container cache that the sweep does not know is never evicted.

    Kills: dropping a cache from ``_LIST_CONTAINER_KEYED_CACHES``.
    """
    setDiscovered = _fsetFindContainerKeyedContextCaches()
    assert setDiscovered, "the discovery found nothing, so proves nothing"
    setUnswept = setDiscovered - set(_LIST_CONTAINER_KEYED_CACHES)
    assert not setUnswept, (
        "these route-context caches are written under a container id but "
        "never evicted when the container goes away; append them to "
        f"_LIST_CONTAINER_KEYED_CACHES: {sorted(setUnswept)}"
    )


@pytest.mark.falsification
def testTheImageIdentityAndCreationCachesAreEvictedWithTheirContainer():
    """A removed container leaves nothing behind in the three caches.

    Kills: dropping any of the three from the sweep list, which left a
    rebuilt container reading its predecessor's image identity.
    """
    listCaches = [
        "dictLiveImageIdentities", "dictPinnedImagePresence",
        "dictProjectCreationRequests",
    ]
    dictCtx = {
        sName: {"gone-container": {"marker": 1}, "kept-container": {"m": 2}}
        for sName in listCaches
    }
    fsetSweepAllContainerCaches(dictCtx, ["kept-container"])
    for sName in listCaches:
        assert list(dictCtx[sName]) == ["kept-container"], sName


@pytest.fixture
def dictDepositsEmpty(monkeypatch):
    monkeypatch.setattr(archiveProgress, "DICT_DEPOSITS", {})


@pytest.mark.falsification
def testASecondProjectsDepositDoesNotEraseTheFirstProjectsFailure(
    dictDepositsEmpty,
):
    """Two projects share one container; their records must not share a key.

    Kills: keying the deposit record by container alone, so the second
    project's registration replaced the first project's failed record.
    """
    archiveProgress.fnRecordFailure("box", "/workspace/alpha", "upload cut")
    archiveProgress.fnRegisterDeposit("box", None, "/workspace/bravo")
    assert archiveProgress.fdictReadDeposit("box", "/workspace/alpha") == {
        "sPhase": archiveProgress.S_PHASE_FAILED, "iBytesRead": 0,
        "iBytesTotal": 0, "iAttempt": 0, "sReason": "upload cut",
        "bStoppable": False, "bStopRequested": False, "listAttempts": [],
    }
    assert archiveProgress.fdictReadDeposit(
        "box", "/workspace/bravo",
    )["sPhase"] == archiveProgress.S_PHASE_STARTING
    assert archiveProgress.fbDepositIsLive("box") is True


def testProgressAndSettlingTouchOnlyTheLiveDeposit(dictDepositsEmpty):
    archiveProgress.fnRecordFailure("box", "/workspace/alpha", "upload cut")
    archiveProgress.fnRegisterDeposit("box", None, "/workspace/bravo")
    archiveProgress.fnRecordProgress(
        "box", archiveProgress.S_PHASE_UPLOADING, 5, 10,
    )
    archiveProgress.fnSettleDeposit("box")
    assert archiveProgress.fdictReadDeposit(
        "box", "/workspace/bravo",
    )["sPhase"] == archiveProgress.S_PHASE_SETTLED
    assert archiveProgress.fdictReadDeposit(
        "box", "/workspace/alpha",
    )["sPhase"] == archiveProgress.S_PHASE_FAILED
    assert archiveProgress.fbDepositIsLive("box") is False


def testForgettingOneProjectsDepositLeavesTheOthers(dictDepositsEmpty):
    archiveProgress.fnRecordFailure("box", "/workspace/alpha", "cut")
    archiveProgress.fnRecordFailure("box", "/workspace/bravo", "cut too")
    archiveProgress.fnForgetDeposit("box", "/workspace/alpha")
    assert archiveProgress.fdictReadDeposit("box", "/workspace/alpha") is None
    assert archiveProgress.fdictReadDeposit(
        "box", "/workspace/bravo",
    )["sReason"] == "cut too"


def testAProgressReportAfterTheDepositSettledDoesNotReviveIt(
    dictDepositsEmpty,
):
    """A late phase callback must not make a finished row pulse again."""
    archiveProgress.fnRegisterDeposit("box", None, "/workspace/alpha")
    archiveProgress.fnSettleDeposit("box")
    archiveProgress.fnRecordProgress(
        "box", archiveProgress.S_PHASE_VERIFYING, 0, 0,
    )
    assert archiveProgress.fbDepositIsLive("box") is False


class _FakeTask:
    """The part of an asyncio.Task the registry uses."""

    def __init__(self):
        self.listCallbacks = []
        self.bDone = False

    def add_done_callback(self, fnCallback):
        self.listCallbacks.append(fnCallback)

    def done(self):
        return self.bDone

    def finish(self):
        self.bDone = True
        for fnCallback in self.listCallbacks:
            fnCallback(self)


@pytest.fixture
def dictVerifyTasksEmpty(monkeypatch):
    monkeypatch.setattr(verificationProgress, "DICT_VERIFY_TASKS", {})


def testAFinishedVerificationLeavesNoRecordBehind(dictVerifyTasksEmpty):
    """The verify registry holds only LIVE work, so nothing to overwrite."""
    taskAlpha = _FakeTask()
    verificationProgress.fnRegisterTask(
        "box", taskAlpha, {"sProjectRepoPath": "/workspace/alpha",
                           "sPhase": "running"},
    )
    assert verificationProgress.fsProjectOfLiveVerification("box") == (
        "/workspace/alpha"
    )
    taskAlpha.finish()
    assert "box" not in verificationProgress.DICT_VERIFY_TASKS
    assert verificationProgress.fsProjectOfLiveVerification("box") == ""


def testALateCallbackFromAnEarlierVerificationDoesNotEvictTheNextOne(
    dictVerifyTasksEmpty,
):
    """A second project's verification survives the first one's callback."""
    taskAlpha = _FakeTask()
    taskBravo = _FakeTask()
    verificationProgress.fnRegisterTask(
        "box", taskAlpha, {"sProjectRepoPath": "/workspace/alpha"},
    )
    verificationProgress.fnRegisterTask(
        "box", taskBravo, {"sProjectRepoPath": "/workspace/bravo"},
    )
    taskAlpha.bDone = True
    taskAlpha.listCallbacks[0](taskAlpha)
    assert verificationProgress.DICT_VERIFY_TASKS["box"]["task"] is taskBravo
    assert verificationProgress.fdictReadStatus(
        "box", "/workspace/bravo",
    ) is not None
    assert verificationProgress.fdictReadStatus(
        "box", "/workspace/alpha",
    ) is None
