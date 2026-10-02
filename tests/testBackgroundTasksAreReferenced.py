"""A task nobody awaits is held by something until it ends.

The event loop keeps only a weak reference to a task, so a task created
and dropped can be collected mid-flight, taking its work with it and
raising nothing. Four sites scheduled work on their own and kept nothing
(the dependency scan, the remote refresh, a fenced socket's close, and
the reproduction worker's private copy of the fix). They now share one
holder, and each is asserted to use it.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import backgroundTasks, pipelineServer, sessionLifecycle
from vaibify.gui.routes import remoteRefreshRoutes, reproductionRoutes


def testAHeldTaskIsReferencedUntilItEnds():
    async def fnDrive():
        eventRelease = asyncio.Event()
        taskHeld = asyncio.ensure_future(eventRelease.wait())
        backgroundTasks.fnKeepTaskReferenced(taskHeld)
        bHeldWhileRunning = taskHeld in backgroundTasks._SET_LIVE_TASKS
        eventRelease.set()
        await taskHeld
        await asyncio.sleep(0)
        return bHeldWhileRunning, taskHeld in backgroundTasks._SET_LIVE_TASKS

    bHeldWhileRunning, bHeldAfterwards = asyncio.run(fnDrive())
    assert bHeldWhileRunning is True
    assert bHeldAfterwards is False


def testAFailedTaskIsReleasedToo():
    async def fnFail():
        raise RuntimeError("the background work failed")

    async def fnDrive():
        taskFailing = asyncio.ensure_future(fnFail())
        backgroundTasks.fnKeepTaskReferenced(taskFailing)
        with pytest.raises(RuntimeError):
            await taskFailing
        await asyncio.sleep(0)
        return taskFailing in backgroundTasks._SET_LIVE_TASKS

    assert asyncio.run(fnDrive()) is False


@pytest.mark.falsification
def testTheDependencyScanTaskIsKept(monkeypatch):
    """Kills: scheduling the dependency scan and dropping the task."""
    listKept = []
    monkeypatch.setattr(pipelineServer, "fnKeepTaskReferenced", listKept.append)

    async def fnDrive():
        with patch.object(
            pipelineServer, "_fnScanDependenciesBackground", AsyncMock(),
        ):
            pipelineServer._fnLaunchDependencyScan({}, "cid", {})
        await asyncio.sleep(0)

    asyncio.run(fnDrive())
    assert len(listKept) == 1


@pytest.mark.falsification
def testTheRemoteRefreshTaskIsKept(monkeypatch):
    """Kills: starting the refresh worker and dropping the task."""
    listKept = []
    monkeypatch.setattr(
        remoteRefreshRoutes, "fnKeepTaskReferenced", listKept.append)
    monkeypatch.setattr(
        remoteRefreshRoutes, "fdictRequireLaneTupleForCommit",
        lambda requestHttp, sContainerId, sWhat: {"sContainerName": "name"})
    monkeypatch.setattr(
        remoteRefreshRoutes.remoteCheckState, "fnMarkChecking",
        lambda tCheckKey, sService: None)

    async def fnDrive():
        with patch.object(
            remoteRefreshRoutes, "_fnRunRefreshWorker", AsyncMock(),
        ):
            dictAnswer = await remoteRefreshRoutes._fdictStartTheRefresh(
                {}, "cid", {"sProjectRepoPath": "/workspace/p"}, MagicMock(),
                ["sZenodo"], SimpleNamespace(app=SimpleNamespace(state=None)))
        await asyncio.sleep(0)
        return dictAnswer

    dictAnswer = asyncio.run(fnDrive())
    assert dictAnswer["listChecking"] == ["sZenodo"]
    assert len(listKept) == 1


@pytest.mark.falsification
def testAFencedSocketsCloseTaskIsKept(monkeypatch):
    """Kills: scheduling the close of fenced sockets and dropping the task."""
    listKept = []
    monkeypatch.setattr(
        sessionLifecycle, "fnKeepTaskReferenced", listKept.append)

    async def fnDrive():
        with patch.object(
            sessionLifecycle, "_fnCloseDetachedConnections", AsyncMock(),
        ):
            sessionLifecycle.fnScheduleConnectionFencing([MagicMock()])
        await asyncio.sleep(0)

    asyncio.run(fnDrive())
    assert len(listKept) == 1


def testFencingWithNoRunningLoopCreatesNoOrphanedCoroutine():
    """Without a running loop there is nothing to schedule on."""
    with patch.object(
        sessionLifecycle, "_fnCloseDetachedConnections",
    ) as mockClose:
        sessionLifecycle.fnScheduleConnectionFencing([MagicMock()])
    mockClose.assert_not_called()


def testTheReproductionWorkerUsesTheSharedHolder():
    assert reproductionRoutes.fnKeepTaskReferenced is (
        backgroundTasks.fnKeepTaskReferenced)
