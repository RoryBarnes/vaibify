"""Tests for the single-writer state-write queue in pipelineState.

These cover audit items CRITICAL #2 / HIGH #12 (lock-only-around-memory
state writes plus a dedicated writer thread per container) and the
HIGH #13 lock-dict eviction helper.
"""

import json
import threading
import time

import pytest

from vaibify.gui.pipelineState import (
    S_STATE_PATH,
    StateWriter,
    fdictBuildInitialState,
    fdictBuildHeartbeatUpdate,
    fnEvictStateLockForContainer,
)


class MockDockerConnection:
    """Mock Docker connection that models temp-file + rename writes."""

    def __init__(self):
        self.dictFiles = {}
        self.listCommands = []
        self.fSleepPerWrite = 0.0
        self.lockRecord = threading.Lock()

    def fnWriteFile(self, sContainerId, sPath, baContent):
        if self.fSleepPerWrite > 0:
            time.sleep(self.fSleepPerWrite)
        with self.lockRecord:
            self.dictFiles[(sContainerId, sPath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        with self.lockRecord:
            self.listCommands.append(sCommand)
        if sCommand.startswith("mv "):
            listParts = sCommand.split()
            sSrc, sDst = listParts[1], listParts[2]
            sKey = (sContainerId, sSrc)
            with self.lockRecord:
                if sKey not in self.dictFiles:
                    return (1, "")
                self.dictFiles[(sContainerId, sDst)] = (
                    self.dictFiles.pop(sKey)
                )
            return (0, "")
        return (1, "")


def _fdictLatestState(mockDocker, sContainerId):
    """Return the last persisted state dict, or None."""
    baStored = mockDocker.dictFiles.get((sContainerId, S_STATE_PATH))
    if baStored is None:
        return None
    return json.loads(baStored.decode("utf-8"))


# ---------------------------------------------------------------------------
# StateWriter — basic enqueue/persist contract
# ---------------------------------------------------------------------------


def test_state_writer_persists_initial_state_on_start():
    """fnStart triggers an initial persist of the dictState passed in."""
    mockDocker = MockDockerConnection()
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    stateWriter.fnStart()
    stateWriter.fnStop()
    dictRead = _fdictLatestState(mockDocker, "ctr1")
    assert dictRead is not None
    assert dictRead["bRunning"] is True
    assert dictRead["sAction"] == "runAll"


def test_state_writer_persists_enqueued_update():
    mockDocker = MockDockerConnection()
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    stateWriter.fnStart()
    stateWriter.fnEnqueueUpdate({"iActiveStep": 2})
    stateWriter.fnStop()
    dictRead = _fdictLatestState(mockDocker, "ctr1")
    assert dictRead["iActiveStep"] == 2


def test_state_writer_persists_step_result():
    mockDocker = MockDockerConnection()
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    stateWriter.fnStart()
    stateWriter.fnEnqueueStepResult({
        "iStepNumber": 1, "sStatus": "passed", "iExitCode": 0,
    })
    stateWriter.fnStop()
    dictRead = _fdictLatestState(mockDocker, "ctr1")
    assert dictRead["dictStepResults"]["1"]["sStatus"] == "passed"


# ---------------------------------------------------------------------------
# Architectural fix: lock not held across docker I/O
# ---------------------------------------------------------------------------


def test_producer_does_not_block_on_slow_docker_write():
    """A slow docker write must not delay enqueue from a producer.

    Pre-R2 the heartbeat thread held the lock across docker I/O,
    so a 3-second exec could starve the next heartbeat. The writer
    thread now owns docker I/O exclusively — producers return as
    soon as the in-memory update is merged.
    """
    mockDocker = MockDockerConnection()
    mockDocker.fSleepPerWrite = 0.5
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    stateWriter.fnStart()
    fEnqueueStart = time.monotonic()
    for iBeat in range(5):
        stateWriter.fnEnqueueUpdate(fdictBuildHeartbeatUpdate())
    fEnqueueElapsed = time.monotonic() - fEnqueueStart
    stateWriter.fnStop()
    # 5 enqueues, each ~microseconds; if the lock were held across
    # the 0.5 s sleep, this would be > 2.5 s.
    assert fEnqueueElapsed < 0.3, (
        f"producer blocked on docker I/O: {fEnqueueElapsed:.2f}s"
    )


# ---------------------------------------------------------------------------
# Shutdown ordering: HIGH #12 race fix
# ---------------------------------------------------------------------------


def test_state_writer_drains_queue_on_shutdown():
    """fnStop joins without timeout — the final state survives shutdown.

    Pre-R2 the runner used join(timeout=2). A heartbeat mid-write
    could land after the finalize, overwriting bRunning: False with
    a stale bRunning: True. With the single-writer queue, fnStop
    drains every pending write before the thread exits.
    """
    mockDocker = MockDockerConnection()
    mockDocker.fSleepPerWrite = 0.05
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    stateWriter.fnStart()
    for iBeat in range(10):
        stateWriter.fnEnqueueUpdate(fdictBuildHeartbeatUpdate())
    stateWriter.fnEnqueueUpdate(
        {"bRunning": False, "iExitCode": 0}
    )
    stateWriter.fnStop()
    dictRead = _fdictLatestState(mockDocker, "ctr1")
    assert dictRead["bRunning"] is False
    assert dictRead["iExitCode"] == 0


# ---------------------------------------------------------------------------
# Resilience: writer survives a docker failure
# ---------------------------------------------------------------------------


def test_state_writer_logs_and_continues_on_write_failure(caplog):
    """A transient docker failure is logged; later writes still land."""
    import logging as _logging
    mockDocker = MockDockerConnection()

    iCalls = {"i": 0}
    fnOriginal = mockDocker.fnWriteFile

    def fnFlaky(sContainerId, sPath, baContent):
        iCalls["i"] += 1
        if iCalls["i"] == 2:
            raise RuntimeError("docker hiccup")
        fnOriginal(sContainerId, sPath, baContent)

    mockDocker.fnWriteFile = fnFlaky
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    with caplog.at_level(_logging.WARNING, logger="vaibify"):
        stateWriter.fnStart()
        stateWriter.fnEnqueueUpdate({"iActiveStep": 1})
        # Coalesce window for the next two writes.
        time.sleep(0.1)
        stateWriter.fnEnqueueUpdate({"iActiveStep": 2})
        stateWriter.fnStop()
    assert any("state write failed" in rec.message
               for rec in caplog.records)
    dictRead = _fdictLatestState(mockDocker, "ctr1")
    assert dictRead["iActiveStep"] == 2


# ---------------------------------------------------------------------------
# Lock-dict eviction (HIGH #13)
# ---------------------------------------------------------------------------


def test_evict_state_lock_drops_entry():
    """Eviction removes a per-container lock once the container is gone."""
    # asyncio.Lock() in 3.9 binds to a current event loop; use sentinels
    # for the dict-eviction test since the helper only checks key membership.
    dictCtx = {
        "dictPipelineStateLocks": {
            "ctr-stopped": object(),
            "ctr-running": object(),
        },
    }
    fnEvictStateLockForContainer(dictCtx, "ctr-stopped")
    assert "ctr-stopped" not in dictCtx["dictPipelineStateLocks"]
    assert "ctr-running" in dictCtx["dictPipelineStateLocks"]


def test_evict_state_lock_unknown_container_is_no_op():
    dictCtx = {"dictPipelineStateLocks": {}}
    # Must not raise even when the dict is absent or empty.
    fnEvictStateLockForContainer(dictCtx, "missing")
    fnEvictStateLockForContainer({}, "missing")


# ---------------------------------------------------------------------------
# docker.errors.APIError is absorbed in the read path
# ---------------------------------------------------------------------------


def test_read_state_absorbs_docker_api_error():
    """A docker APIError during the cat must degrade to None, not raise."""
    import docker.errors
    from vaibify.gui.pipelineState import fdictReadState

    class MockBadDocker:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            raise docker.errors.APIError("docker daemon contention")

    assert fdictReadState(MockBadDocker(), "ctr1") is None


# ---------------------------------------------------------------------------
# Output coalescing
# ---------------------------------------------------------------------------


def test_output_lines_accumulate_in_state():
    """Output lines update in-memory state; the next write coalesces them."""
    mockDocker = MockDockerConnection()
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    stateWriter = StateWriter(mockDocker, "ctr1", dictState)
    stateWriter.fnStart()
    for sLine in ("a", "b", "c"):
        stateWriter.fnEnqueueOutputLine(sLine)
    stateWriter.fnEnqueueUpdate({"iActiveStep": 1})
    stateWriter.fnStop()
    dictRead = _fdictLatestState(mockDocker, "ctr1")
    assert "a" in dictRead["listRecentOutput"]
    assert "c" in dictRead["listRecentOutput"]
