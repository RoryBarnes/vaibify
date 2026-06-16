"""Tests that the stale-heartbeat reconciler stamps host cause-of-death.

The container-Claude diagnosability story (R3): the on-disk
``pipeline_state.json`` must carry the host-side reason a runner died,
not just the symptom ``heartbeat_stale (...)``. These tests verify
that :func:`pipelineState._fdictReconcileStaleHeartbeat` correctly
copies the latest host-incident into the reconciled state, and that
:func:`pipelineState.fdictReadReconciledState` threads the ring buffer
through end-to-end.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.gui import hostIncidents
from vaibify.gui.pipelineState import (
    I_EXIT_CODE_RUNNER_DISAPPEARED,
    I_HEARTBEAT_STALE_SECONDS,
    S_STATE_PATH,
    _fdictReconcileStaleHeartbeat,
    fdictReadReconciledState,
)


@pytest.fixture(autouse=True)
def fnClearRing():
    """Reset the host-incident ring before and after every test."""
    hostIncidents.fnResetHostIncidents()
    yield
    hostIncidents.fnResetHostIncidents()


class MockDockerConnection:
    """Tiny in-memory backing store mirroring the real Docker contract."""

    def __init__(self):
        self.dictFiles = {}

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if sCommand.startswith("mv "):
            tParts = sCommand.split()
            sSrc, sDst = tParts[1], tParts[2]
            tKey = (sContainerId, sSrc)
            if tKey not in self.dictFiles:
                return (1, "")
            self.dictFiles[(sContainerId, sDst)] = self.dictFiles.pop(tKey)
            return (0, "")
        if sCommand.startswith("cat "):
            sPath = sCommand.split()[1]
            tKey = (sContainerId, sPath)
            if tKey not in self.dictFiles:
                return (1, "")
            return (0, self.dictFiles[tKey].decode("utf-8"))
        return (0, "")


def _fdictBuildStaleRunningState(iActiveStep=4):
    """Return a state dict whose heartbeat is already past the window."""
    dtStale = (
        datetime.now(timezone.utc)
        - timedelta(seconds=I_HEARTBEAT_STALE_SECONDS * 2)
    )
    return {
        "bRunning": True,
        "sAction": "run-all",
        "sLogPath": "/tmp/run.log",
        "sStartTime": dtStale.isoformat(),
        "sEndTime": "",
        "iExitCode": -1,
        "iActiveStep": iActiveStep,
        "iStepCount": 8,
        "dictStepResults": {},
        "listRecentOutput": [],
        "iRunnerPid": 1234,
        "sLastHeartbeat": dtStale.isoformat(),
        "sFailureReason": "",
    }


def _fnSeed(mockDocker, sContainerId, dictState):
    """Seed the mock backing store with an initial state file."""
    mockDocker.dictFiles[(sContainerId, S_STATE_PATH)] = (
        json.dumps(dictState).encode("utf-8")
    )


# -----------------------------------------------------------------------
# _fdictReconcileStaleHeartbeat
# -----------------------------------------------------------------------


def test_reconcile_without_incident_keeps_blank_cause():
    dictState = _fdictBuildStaleRunningState(iActiveStep=3)
    dictReconciled = _fdictReconcileStaleHeartbeat(dictState)
    assert dictReconciled["sFailureCauseHost"] == ""
    assert dictReconciled["sLastHostIncidentIso"] == ""
    assert dictReconciled["bRunning"] is False
    assert dictReconciled["iExitCode"] == I_EXIT_CODE_RUNNER_DISAPPEARED


def test_reconcile_captures_active_step_before_overlay_wipes_it():
    """iActiveStepAtDeath preserves the step that was running."""
    dictState = _fdictBuildStaleRunningState(iActiveStep=7)
    dictReconciled = _fdictReconcileStaleHeartbeat(dictState)
    assert dictReconciled["iActiveStepAtDeath"] == 7
    # The overlay still wipes iActiveStep to -1 for the running banner.
    assert dictReconciled["iActiveStep"] == -1


def test_reconcile_stamps_incident_repr_into_sFailureCauseHost():
    dictState = _fdictBuildStaleRunningState()
    dictIncident = {
        "sIso": "2026-06-16T12:34:56+00:00",
        "sLevel": "ERROR",
        "sLogger": "vaibify",
        "sMessage": "Pipeline action 'runAll' failed: ws closed",
        "sExceptionRepr": "RuntimeError('websocket.send after close')",
    }
    dictReconciled = _fdictReconcileStaleHeartbeat(
        dictState, dictIncident=dictIncident,
    )
    assert dictReconciled["sFailureCauseHost"] == (
        "RuntimeError('websocket.send after close')"
    )
    assert dictReconciled["sLastHostIncidentIso"] == (
        "2026-06-16T12:34:56+00:00"
    )


def test_reconcile_falls_back_to_message_when_no_exception_repr():
    dictState = _fdictBuildStaleRunningState()
    dictIncident = {
        "sIso": "2026-06-16T00:00:00+00:00",
        "sMessage": "host-side oom kill",
        "sExceptionRepr": "",
    }
    dictReconciled = _fdictReconcileStaleHeartbeat(
        dictState, dictIncident=dictIncident,
    )
    assert dictReconciled["sFailureCauseHost"] == "host-side oom kill"


def test_reconcile_preserves_existing_failure_reason_format():
    dictState = _fdictBuildStaleRunningState()
    dictReconciled = _fdictReconcileStaleHeartbeat(dictState)
    assert "heartbeat_stale" in dictReconciled["sFailureReason"]


# -----------------------------------------------------------------------
# fdictReadReconciledState end-to-end with the ring buffer
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def testReadReconciledStateStampsLatestIncident():
    """A pre-existing host incident is stamped into the reconciled file."""
    mockDocker = MockDockerConnection()
    _fnSeed(
        mockDocker, "ctr-die",
        _fdictBuildStaleRunningState(iActiveStep=5),
    )
    hostIncidents.fnRecordHostIncident(
        "ctr-die",
        {
            "sIso": "2026-06-16T10:00:00+00:00",
            "sLevel": "ERROR",
            "sLogger": "vaibify",
            "sMessage": "Pipeline action 'runAll' failed: closed",
            "sExceptionRepr": "RuntimeError('ws closed mid-run')",
        },
    )
    dictCtx = {"docker": mockDocker}
    dictReconciled = await fdictReadReconciledState(dictCtx, "ctr-die")
    assert dictReconciled["sFailureCauseHost"] == (
        "RuntimeError('ws closed mid-run')"
    )
    assert dictReconciled["iActiveStepAtDeath"] == 5
    assert dictReconciled["sLastHostIncidentIso"] == (
        "2026-06-16T10:00:00+00:00"
    )


@pytest.mark.asyncio
async def testReadReconciledStatePersistsHostCauseToFile():
    """The stamped fields survive a re-read so the container can see them."""
    mockDocker = MockDockerConnection()
    _fnSeed(mockDocker, "ctr-1", _fdictBuildStaleRunningState())
    hostIncidents.fnRecordHostIncident(
        "ctr-1",
        {
            "sIso": "2026-06-16T01:02:03+00:00",
            "sMessage": "any",
            "sExceptionRepr": "KeyError('dictWorkflow')",
        },
    )
    dictCtx = {"docker": mockDocker}
    await fdictReadReconciledState(dictCtx, "ctr-1")
    sBaContent = mockDocker.dictFiles[("ctr-1", S_STATE_PATH)]
    dictOnDisk = json.loads(sBaContent.decode("utf-8"))
    assert dictOnDisk["sFailureCauseHost"] == "KeyError('dictWorkflow')"
    assert "iActiveStepAtDeath" in dictOnDisk


@pytest.mark.asyncio
async def testReadReconciledStateBlankCauseWhenNoIncidentRecorded():
    """Absence of a host incident leaves sFailureCauseHost empty, not missing."""
    mockDocker = MockDockerConnection()
    _fnSeed(mockDocker, "ctr-quiet", _fdictBuildStaleRunningState())
    dictCtx = {"docker": mockDocker}
    dictReconciled = await fdictReadReconciledState(dictCtx, "ctr-quiet")
    assert dictReconciled["sFailureCauseHost"] == ""
    assert dictReconciled["sLastHostIncidentIso"] == ""
