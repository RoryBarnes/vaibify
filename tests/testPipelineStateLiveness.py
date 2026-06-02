"""Tests for the always-on runner-liveness reconciler.

The watchdog must declare a vanished runner dead at every read site,
not only when ``GET /api/pipeline/{id}/state`` is polled. This module
exercises ``pipelineState.fdictReadReconciledState`` directly with a
mock Docker connection so the contract is testable without a live
container.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.gui.pipelineState import (
    I_EXIT_CODE_RUNNER_DISAPPEARED,
    I_HEARTBEAT_STALE_SECONDS,
    S_STATE_PATH,
    S_STATE_PATH_TEMP,
    fdictReadReconciledState,
    fdictReadState,
)


class MockDockerConnection:
    """Mock docker that backs an in-memory filesystem with atomic rename."""

    def __init__(self):
        self.dictFiles = {}
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        if sCommand.startswith("mv "):
            return self._ftHandleRename(sContainerId, sCommand)
        if sCommand.startswith("cat "):
            return self._ftHandleRead(sContainerId, sCommand)
        if "rm -f" in sCommand:
            self._fnHandleRemove(sContainerId, sCommand)
            return (0, "")
        return (1, "")

    def _ftHandleRename(self, sContainerId, sCommand):
        listParts = sCommand.split()
        sSrc, sDst = listParts[1], listParts[2]
        sKey = (sContainerId, sSrc)
        if sKey not in self.dictFiles:
            return (1, "")
        self.dictFiles[(sContainerId, sDst)] = self.dictFiles.pop(sKey)
        return (0, "")

    def _ftHandleRead(self, sContainerId, sCommand):
        sPath = sCommand.split()[1]
        sKey = (sContainerId, sPath)
        if sKey not in self.dictFiles:
            return (1, "")
        return (0, self.dictFiles[sKey].decode("utf-8"))

    def _fnHandleRemove(self, sContainerId, sCommand):
        for sToken in sCommand.split():
            if sToken.startswith("/"):
                self.dictFiles.pop((sContainerId, sToken), None)


def _fdictBuildRunningStateWithAge(fSecondsAgo):
    """Return a state dict whose heartbeat is ``fSecondsAgo`` in the past."""
    dtHeartbeat = (
        datetime.now(timezone.utc) - timedelta(seconds=fSecondsAgo)
    )
    return {
        "bRunning": True,
        "sAction": "run-all",
        "sLogPath": "/tmp/run.log",
        "sStartTime": dtHeartbeat.isoformat(),
        "sEndTime": "",
        "iExitCode": -1,
        "iActiveStep": 2,
        "iStepCount": 5,
        "dictStepResults": {},
        "listRecentOutput": [],
        "iRunnerPid": 4242,
        "sLastHeartbeat": dtHeartbeat.isoformat(),
        "sFailureReason": "",
    }


def _fnSeedContainerFile(mockDocker, sContainerId, dictState):
    """Populate the mock's backing store with an initial state file."""
    sContent = json.dumps(dictState, indent=2).encode("utf-8")
    mockDocker.dictFiles[(sContainerId, S_STATE_PATH)] = sContent


@pytest.mark.asyncio
async def testStaleHeartbeatFlipsBRunning():
    """A heartbeat older than the staleness window flips bRunning to False."""
    mockDocker = MockDockerConnection()
    dictState = _fdictBuildRunningStateWithAge(
        I_HEARTBEAT_STALE_SECONDS * 2,
    )
    _fnSeedContainerFile(mockDocker, "ctr1", dictState)
    dictCtx = {"docker": mockDocker}
    dictReconciled = await fdictReadReconciledState(dictCtx, "ctr1")
    assert dictReconciled["bRunning"] is False
    assert dictReconciled["iExitCode"] == I_EXIT_CODE_RUNNER_DISAPPEARED
    assert "heartbeat_stale" in dictReconciled["sFailureReason"]


@pytest.mark.asyncio
async def testReconciliationPersistsToDisk():
    """The reconciled state lands in the on-disk file via atomic rename."""
    mockDocker = MockDockerConnection()
    _fnSeedContainerFile(
        mockDocker, "ctr1",
        _fdictBuildRunningStateWithAge(I_HEARTBEAT_STALE_SECONDS * 2),
    )
    dictCtx = {"docker": mockDocker}
    await fdictReadReconciledState(dictCtx, "ctr1")
    dictPersisted = fdictReadState(mockDocker, "ctr1")
    assert dictPersisted["bRunning"] is False
    assert dictPersisted["iExitCode"] == I_EXIT_CODE_RUNNER_DISAPPEARED
    assert (
        ("ctr1", S_STATE_PATH_TEMP) not in mockDocker.dictFiles
    ), "temp file should be renamed away after atomic write"


@pytest.mark.asyncio
async def testFreshHeartbeatLeftAlone():
    """A heartbeat inside the window leaves state unchanged and unwritten."""
    mockDocker = MockDockerConnection()
    dictFresh = _fdictBuildRunningStateWithAge(1)
    _fnSeedContainerFile(mockDocker, "ctr1", dictFresh)
    dictCtx = {"docker": mockDocker}
    iWritesBefore = sum(
        1 for sCmd in mockDocker.listCommands if sCmd.startswith("mv ")
    )
    dictResult = await fdictReadReconciledState(dictCtx, "ctr1")
    iWritesAfter = sum(
        1 for sCmd in mockDocker.listCommands if sCmd.startswith("mv ")
    )
    assert dictResult["bRunning"] is True
    assert iWritesAfter == iWritesBefore


@pytest.mark.asyncio
async def testMissingStateReturnsNone():
    """An absent state file resolves to None without raising."""
    mockDocker = MockDockerConnection()
    dictCtx = {"docker": mockDocker}
    dictResult = await fdictReadReconciledState(dictCtx, "ctr1")
    assert dictResult is None


@pytest.mark.asyncio
async def testSecondReadIsNoop():
    """Once reconciled, a subsequent read does not rewrite the file."""
    mockDocker = MockDockerConnection()
    _fnSeedContainerFile(
        mockDocker, "ctr1",
        _fdictBuildRunningStateWithAge(I_HEARTBEAT_STALE_SECONDS * 2),
    )
    dictCtx = {"docker": mockDocker}
    await fdictReadReconciledState(dictCtx, "ctr1")
    iRenamesAfterFirst = sum(
        1 for sCmd in mockDocker.listCommands if sCmd.startswith("mv ")
    )
    await fdictReadReconciledState(dictCtx, "ctr1")
    iRenamesAfterSecond = sum(
        1 for sCmd in mockDocker.listCommands if sCmd.startswith("mv ")
    )
    assert iRenamesAfterSecond == iRenamesAfterFirst


@pytest.mark.asyncio
async def testConcurrentReadsObserveReconciledState():
    """Parallel reconcilers under the per-container lock agree on the outcome."""
    mockDocker = MockDockerConnection()
    _fnSeedContainerFile(
        mockDocker, "ctr1",
        _fdictBuildRunningStateWithAge(I_HEARTBEAT_STALE_SECONDS * 2),
    )
    dictCtx = {"docker": mockDocker}
    listResults = await asyncio.gather(*[
        fdictReadReconciledState(dictCtx, "ctr1") for _ in range(8)
    ])
    for dictResult in listResults:
        assert dictResult["bRunning"] is False
        assert dictResult["iExitCode"] == I_EXIT_CODE_RUNNER_DISAPPEARED
    dictPersisted = fdictReadState(mockDocker, "ctr1")
    assert dictPersisted["bRunning"] is False


@pytest.mark.asyncio
async def testPerContainerLocksAreIndependent():
    """One container's reconciliation never serializes with another's."""
    mockDocker = MockDockerConnection()
    _fnSeedContainerFile(
        mockDocker, "ctr1",
        _fdictBuildRunningStateWithAge(I_HEARTBEAT_STALE_SECONDS * 2),
    )
    _fnSeedContainerFile(
        mockDocker, "ctr2",
        _fdictBuildRunningStateWithAge(1),
    )
    dictCtx = {"docker": mockDocker}
    tResults = await asyncio.gather(
        fdictReadReconciledState(dictCtx, "ctr1"),
        fdictReadReconciledState(dictCtx, "ctr2"),
    )
    assert tResults[0]["bRunning"] is False
    assert tResults[1]["bRunning"] is True
    dictLocks = dictCtx["dictPipelineStateLocks"]
    assert dictLocks["ctr1"] is not dictLocks["ctr2"]


@pytest.mark.asyncio
async def testLegacyStateLacksHeartbeatLeftAlone():
    """Pre-heartbeat state files (no sLastHeartbeat) are not reconciled."""
    mockDocker = MockDockerConnection()
    dictLegacy = _fdictBuildRunningStateWithAge(99999)
    del dictLegacy["sLastHeartbeat"]
    _fnSeedContainerFile(mockDocker, "ctr1", dictLegacy)
    dictCtx = {"docker": mockDocker}
    dictResult = await fdictReadReconciledState(dictCtx, "ctr1")
    assert dictResult["bRunning"] is True
