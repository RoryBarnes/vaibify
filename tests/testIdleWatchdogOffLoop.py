"""The idle watchdog's container probes run off the hub's event loop.

A watchdog tick asks the daemon about every held container. Run on the
loop's own thread, a slow or wedged daemon froze every route for as long
as it took to answer. Each probe now runs on a worker thread; the reaper,
which mutates the owner map the routes share, stays on the loop and
reads the probe's answer instead of asking the daemon itself.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import pipelineServer, serverLifespan
from tests.testSafeReaper import (
    S_PROJECT_NAME,
    _appBuildReaperApplication,
    _recordBuildOrphanedRecord,
)


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirectories(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fdictRunOneWatchdogTick():
    """Drive the watchdog through one tick; return the thread of each probe."""
    dictThreadByCall = {}
    app = SimpleNamespace(state=SimpleNamespace(
        bReapOwnerships=True,
        dictContainerOwners={S_PROJECT_NAME: object()},
        fLastActivityMonotonic=0.0,
    ))

    def flistRunningContainers():
        dictThreadByCall["docker"] = threading.get_ident()
        return []

    def fnSweep(appSwept, dictCtx):
        dictThreadByCall["sweep"] = threading.get_ident()

    def fbShouldSelfExit(appAsked, dictCtx, fTimeout):
        dictThreadByCall["selfExit"] = threading.get_ident()
        return True

    def fnReap(appReaped, dictCtx, dictPipelineRunningByName=None):
        dictThreadByCall["reap"] = threading.get_ident()
        dictThreadByCall["snapshot"] = dictPipelineRunningByName

    dictCtx = {"docker": SimpleNamespace(
        flistGetRunningContainers=flistRunningContainers)}

    async def fnDrive():
        dictThreadByCall["loop"] = threading.get_ident()
        await serverLifespan._fnIdleShutdownWatchdogLoop(
            app, dictCtx, 0.001, 1.0,
        )

    with patch(
        "vaibify.config.registryManager.fbIsHostProject", lambda sName: False,
    ), patch.object(
        serverLifespan, "_fnSweepSleepPreventionForApp", fnSweep,
    ), patch.object(
        serverLifespan, "_fnReapIdleOwnershipsForApp", fnReap,
    ), patch.object(
        pipelineServer, "_fbHubShouldSelfExit", fbShouldSelfExit,
    ), patch.object(serverLifespan.os, "kill", lambda iPid, iSignal: None):
        asyncio.run(asyncio.wait_for(fnDrive(), timeout=10.0))
    return dictThreadByCall


@pytest.mark.falsification
def testTheOwnedPipelineProbeRunsOffTheEventLoop():
    """Kills: asking the daemon about every owner on the event loop."""
    dictThreadByCall = _fdictRunOneWatchdogTick()
    assert dictThreadByCall["docker"] != dictThreadByCall["loop"]
    assert dictThreadByCall["snapshot"] == {S_PROJECT_NAME: False}


@pytest.mark.falsification
def testTheSleepPreventionSweepRunsOffTheEventLoop():
    """Kills: the keep-alive sweep's daemon queries on the event loop."""
    dictThreadByCall = _fdictRunOneWatchdogTick()
    assert dictThreadByCall["sweep"] != dictThreadByCall["loop"]


@pytest.mark.falsification
def testTheSelfExitDecisionRunsOffTheEventLoop():
    """Kills: the busy-veto's daemon queries on the event loop."""
    dictThreadByCall = _fdictRunOneWatchdogTick()
    assert dictThreadByCall["selfExit"] != dictThreadByCall["loop"]


def testTheReaperStaysOnTheLoopWithTheOwnerMap():
    """The owner map is shared with the routes, so its reaper never leaves."""
    dictThreadByCall = _fdictRunOneWatchdogTick()
    assert dictThreadByCall["reap"] == dictThreadByCall["loop"]


@pytest.mark.falsification
def testAnOwnerMissingFromTheSnapshotIsNotReaped():
    """A record claimed after the probe was taken reads as busy.

    Kills: defaulting a name absent from the snapshot to "not running".
    """
    recordOwner = _recordBuildOrphanedRecord()
    app, dictCtx = _appBuildReaperApplication(recordOwner)
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx, {})
    assert S_PROJECT_NAME in app.state.dictContainerOwners
    serverLifespan._fnReapIdleOwnershipsForApp(
        app, dictCtx, {S_PROJECT_NAME: True})
    assert S_PROJECT_NAME in app.state.dictContainerOwners
    serverLifespan._fnReapIdleOwnershipsForApp(
        app, dictCtx, {S_PROJECT_NAME: False})
    assert S_PROJECT_NAME not in app.state.dictContainerOwners
