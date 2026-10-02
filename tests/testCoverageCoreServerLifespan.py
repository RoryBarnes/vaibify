"""serverLifespan's resilience: hooks, loops and the idle veto.

The background loops are driven for real with a tiny interval and
cancelled once the behavior under test has been observed; the Docker
daemon is a recorder. What is asserted is that one failure never ends
the loop or the lifespan it belongs to, and that the idle veto answers
from its evidence.
"""

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from vaibify.config import registryManager
from vaibify.gui import reproductionProgress, serverLifespan


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Point the registry at an absent file under tmp_path."""
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(tmp_path / "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )


class DockerListRecorder:
    """Answers the running-container listing, or raises on demand."""

    def __init__(self, listRunning=None, exceptionList=None):
        self.listRunning = listRunning or []
        self.exceptionList = exceptionList
        self.iCalls = 0

    def flistGetRunningContainers(self):
        self.iCalls += 1
        if self.exceptionList:
            raise self.exceptionList
        return list(self.listRunning)


def fappBuild(**dictState):
    """Return a minimal app object with the given state attributes."""
    return SimpleNamespace(state=SimpleNamespace(**dictState))


def testLifespanRunsEveryHookEvenWhenSomeFail(caplog):
    """A failing startup or shutdown hook is logged; the others still run."""
    listEvents = []

    def fnFailingStart(app):
        raise RuntimeError("startup broke")

    async def fnRecordingStart(app):
        listEvents.append("start")

    def fnFailingStop(app):
        raise ValueError("shutdown broke")

    def fnRecordingStop(app):
        listEvents.append("stop")

    app = fappBuild(
        listLifespanStartup=[fnFailingStart, fnRecordingStart],
        listLifespanShutdown=[fnFailingStop, fnRecordingStop],
    )

    async def fnDriveLifespan():
        async with serverLifespan._fcontextLifespanShared(app):
            listEvents.append("serving")

    with caplog.at_level(logging.WARNING, logger="vaibify"):
        asyncio.run(fnDriveLifespan())
    assert listEvents == ["start", "serving", "stop"]
    listMessages = [recordLog.getMessage() for recordLog in caplog.records]
    assert any("startup hook fnFailingStart failed: RuntimeError" in s
               for s in listMessages)
    assert any("shutdown hook fnFailingStop failed: ValueError" in s
               for s in listMessages)


def testSweepTickEvictsCachesOfContainersThatStopped():
    """Only the running container keeps its cached workflow and path."""
    dictCtx = {
        "docker": DockerListRecorder([
            {"sContainerId": "idRunning", "sName": "nameRunning"},
            {"sContainerId": "", "sName": "nameWithoutId"},
        ]),
        "workflows": {"idRunning": {"sName": "a"}, "idStopped": {"sName": "b"}},
        "paths": {"idRunning": "/r/a.json", "idStopped": "/r/b.json"},
    }
    asyncio.run(serverLifespan._fnRunOneContainerSweep(dictCtx))
    assert list(dictCtx["workflows"]) == ["idRunning"]
    assert list(dictCtx["paths"]) == ["idRunning"]


def testSweepTickLeavesCachesAloneWhenDockerCannotList(caplog):
    """A listing failure is logged and evicts nothing."""
    dictCtx = {
        "docker": DockerListRecorder(exceptionList=RuntimeError("daemon gone")),
        "workflows": {"idStopped": {"sName": "b"}},
    }
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        asyncio.run(serverLifespan._fnRunOneContainerSweep(dictCtx))
    assert dictCtx["workflows"] == {"idStopped": {"sName": "b"}}
    assert any("Could not list running containers" in r.getMessage()
               for r in caplog.records)


def fnRunLoopUntilTicks(fcoroutineLoop, listTicks, iWantedTicks):
    """Run a loop coroutine until it has ticked enough, then cancel it."""

    async def fnDrive():
        taskLoop = asyncio.create_task(fcoroutineLoop)
        fDeadline = time.monotonic() + 5.0
        while len(listTicks) < iWantedTicks and time.monotonic() < fDeadline:
            await asyncio.sleep(0.01)
        taskLoop.cancel()
        await taskLoop
        return taskLoop

    return asyncio.run(fnDrive())


def testContainerSweepLoopSurvivesAFailingTick(monkeypatch, caplog):
    """A tick that raises is logged, and the next tick still runs."""
    listTicks = []

    async def fnTick(dictCtx):
        listTicks.append(dictCtx)
        if len(listTicks) == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(serverLifespan, "_fnRunOneContainerSweep", fnTick)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        taskLoop = fnRunLoopUntilTicks(
            serverLifespan._fnPeriodicContainerSweepLoop({"k": 1}, 0.01),
            listTicks, 2,
        )
    assert len(listTicks) >= 2
    assert taskLoop.done() and not taskLoop.cancelled()
    assert any("sweep iteration failed" in r.getMessage() for r in caplog.records)


def testDisposableReclaimLoopSurvivesAFailingPass(monkeypatch, caplog):
    """A failed reclaim pass is logged, and the loop keeps reclaiming."""
    listTicks = []

    def fdictReclaim():
        listTicks.append(1)
        if len(listTicks) == 1:
            raise RuntimeError("daemon hiccup")
        return {}

    monkeypatch.setattr(serverLifespan, "_fdictReclaimOnce", fdictReclaim)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        fnRunLoopUntilTicks(
            serverLifespan._fnDisposableReclaimLoop(0.01), listTicks, 2,
        )
    assert len(listTicks) >= 2
    assert any("Disposable reclaim iteration failed" in r.getMessage()
               for r in caplog.records)


def testReclaimOnceReturnsTheDisposableModulesAnswer(monkeypatch):
    """One pass is exactly one call to the disposable reclaimer."""
    from vaibify.docker import disposableContainer
    dictAnswer = {"listReclaimed": ["disposableAlpha"]}
    monkeypatch.setattr(
        disposableContainer, "fdictReclaimStrandedDisposables",
        lambda: dictAnswer,
    )
    assert serverLifespan._fdictReclaimOnce() is dictAnswer


def testLiveReproductionVetoesIdleSelfExit(monkeypatch):
    """An idle-looking hub holding a reproduction must not exit."""
    app = fappBuild(iActiveWebSockets=0, fLastActivityMonotonic=0.0)
    monkeypatch.setattr(
        reproductionProgress, "fbHubHoldsLiveReproduction", lambda: False,
    )
    assert serverLifespan._fbHubShouldSelfExit(app, {}, 1.0) is True
    monkeypatch.setattr(
        reproductionProgress, "fbHubHoldsLiveReproduction", lambda: True,
    )
    assert serverLifespan._fbHubShouldSelfExit(app, {}, 1.0) is False


def testOwnedNameIsBusyWhenDockerCannotAnswer():
    """A Docker failure while checking an owner's run fails safe to busy."""
    dictCtx = {"docker": DockerListRecorder(exceptionList=OSError("no daemon"))}
    assert serverLifespan._fbOwnedNamePipelineRunning(
        fappBuild(), dictCtx, "containerNameAlpha",
    ) is True
    assert dictCtx["docker"].iCalls == 1


def testSleepPreventionSweepRunsOnlyOnHubsAndNeverRaises(monkeypatch, caplog):
    """A viewer skips the sweep; a hub's failing sweep is only logged."""
    from vaibify.gui import sleepPrevention
    listSwept = []

    def fnSweep(appState, dictCtx):
        listSwept.append(appState)
        raise RuntimeError("caffeinate missing")

    monkeypatch.setattr(sleepPrevention, "fnSweepWorkLaneKeepAlives", fnSweep)
    serverLifespan._fnSweepSleepPreventionForApp(fappBuild(), {})
    assert listSwept == []
    appHub = fappBuild(bReapOwnerships=True)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        serverLifespan._fnSweepSleepPreventionForApp(appHub, {})
    assert listSwept == [appHub.state]
    assert any("Sleep-prevention sweep failed" in r.getMessage()
               for r in caplog.records)


def testAbsentOwnerRecordIsNeverAttended():
    """No owner record means no browser can be holding the claim."""
    assert serverLifespan._fbOwningBrowserStillAttends(
        fappBuild(), {}, "containerNameAlpha",
    ) is False


def testIdleWatchdogSurvivesAFailingTick(monkeypatch, caplog):
    """A reaper failure inside the watchdog is logged; ticks continue."""
    listTicks = []

    def fnReap(app, dictCtx, dictPipelineRunningByName=None):
        listTicks.append(1)
        raise RuntimeError("reaper broke")

    monkeypatch.setattr(serverLifespan, "_fnReapIdleOwnershipsForApp", fnReap)
    app = fappBuild(iActiveWebSockets=1)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        fnRunLoopUntilTicks(
            serverLifespan._fnIdleShutdownWatchdogLoop(app, {}, 0.01, 1.0),
            listTicks, 2,
        )
    assert len(listTicks) >= 2
    assert any("Idle-shutdown watchdog iteration failed" in r.getMessage()
               for r in caplog.records)
