"""A run's own outputs are never reported as a modification nobody made.

Found by driving a template project through the CLI alone
(``tests/testCliTemplateProjectTowardLevelThreeLive.py``): each command
connects, and the connect's change detection compared the outputs a run
had just written with a baseline taken before the run, because nothing
polls during a command-line run. Every step the run executed then read
"modified", and running it again could not clear it. The dashboard met
the same race for any output written after a run's last poll.

The runner now measures the outputs of the steps it executed just
before its terminal state is written, and records them in that write;
the first check that sees the finished run merges them into its
baseline, once. These drive the REAL measurement, merge and change
detection; only the container stat is a double, answering the mtimes a
stat would.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui import workflowManager
from vaibify.gui.fileStatusManager import (
    _fdictDetectChangedFiles,
    _fnMergeIntoModTimeBaseline,
)
from vaibify.gui.routes import pipelineRoutes
from vaibify.gui.runOutputBaseline import (
    S_RUN_OUTPUT_MODTIMES_KEY,
    fdictMeasureRunOutputModTimes,
    fnAbsorbFinishedRunOutputs,
)


S_CONTAINER_ID = "containerOne"
S_WORKFLOW_PATH = "/repo/.vaibify/projects/project.json"
S_FIT_OUTPUT = "/repo/stepFit/fitResults.json"
S_PLOT_OUTPUT = "/repo/stepPlot/summary.json"
S_INPUT_PATH = "/repo/inputData.json"


class _StatDouble:
    """Answers the typed stat with whatever mtimes the files hold now."""

    def __init__(self, dictModTimes):
        self.dictModTimes = dictModTimes

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        return {sPath: self.dictModTimes[sPath] for sPath in listPaths
                if sPath in self.dictModTimes}


def _fdictWorkflow(sLoadedFrom=S_WORKFLOW_PATH):
    return {
        "sProjectRepoPath": "/repo",
        workflowManager.S_LOADED_FROM_KEY: sLoadedFrom,
        "listSteps": [
            {"sName": "Fit", "sDirectory": "stepFit",
             "saOutputDataFiles": ["fitResults.json"],
             "saInputDataFiles": ["inputData.json"],
             "dictVerification": {}},
            {"sName": "Plot", "sDirectory": "stepPlot",
             "saOutputDataFiles": ["summary.json"],
             "dictVerification": {}},
        ],
    }


def _fdictPoll(dictCtx, dictWorkflow, dictModTimes, dictPipelineState):
    """One check, as the poll and every command's connect make it."""
    fnAbsorbFinishedRunOutputs(
        dictCtx, S_CONTAINER_ID, dictWorkflow, dictPipelineState)
    return _fdictDetectChangedFiles(
        dictCtx, S_CONTAINER_ID, dictWorkflow, dict(dictModTimes),
        dictVars=workflowManager.fdictBuildGlobalVariables(
            dictWorkflow, S_WORKFLOW_PATH),
        bPipelineRunning=bool(dictPipelineState.get("bRunning")),
    )


def _ftRunTheFitStep(dictCtx, dictWorkflow):
    """Baseline before; the fit step runs and writes; return its state."""
    dictFiles = {S_FIT_OUTPUT: "100", S_PLOT_OUTPUT: "100",
                 S_INPUT_PATH: "50"}
    assert _fdictPoll(dictCtx, dictWorkflow, dictFiles, {}) == {}
    dictFiles[S_FIT_OUTPUT] = "200"
    dictState = {"bRunning": False, "sWorkflowPath": S_WORKFLOW_PATH,
                 "sStartTime": "t0", "sEndTime": "t1",
                 "dictStepStats": {"1": {"iExitCode": 0}}}
    dictState[S_RUN_OUTPUT_MODTIMES_KEY] = fdictMeasureRunOutputModTimes(
        _StatDouble(dictFiles), S_CONTAINER_ID, dictState, dictWorkflow,
        S_WORKFLOW_PATH)
    return dictFiles, dictState


@pytest.mark.falsification
def testTheFirstCheckAfterARunDoesNotReportTheRunsOwnOutputs():
    """Kills: never merging the run's own output mtimes into the baseline."""
    dictCtx, dictWorkflow = {}, _fdictWorkflow()
    dictFiles, dictState = _ftRunTheFitStep(dictCtx, dictWorkflow)
    assert dictState[S_RUN_OUTPUT_MODTIMES_KEY] == {S_FIT_OUTPUT: "200"}
    assert _fdictPoll(dictCtx, dictWorkflow, dictFiles, dictState) == {}


@pytest.mark.falsification
def testAChangeAcknowledgedAfterTheRunIsNotReportedAgain():
    """Kills: merging the same run's mtimes on every check."""
    dictCtx, dictWorkflow = {}, _fdictWorkflow()
    dictFiles, dictState = _ftRunTheFitStep(dictCtx, dictWorkflow)
    dictFiles[S_FIT_OUTPUT] = "300"
    assert list(_fdictPoll(dictCtx, dictWorkflow, dictFiles, dictState)) == [0]
    _fnMergeIntoModTimeBaseline(
        dictCtx, S_CONTAINER_ID, {S_FIT_OUTPUT: "300"}, dictWorkflow)
    assert _fdictPoll(dictCtx, dictWorkflow, dictFiles, dictState) == {}


def testAChangeMadeAfterTheRunStillReadsAsChanged():
    dictCtx, dictWorkflow = {}, _fdictWorkflow()
    dictFiles, dictState = _ftRunTheFitStep(dictCtx, dictWorkflow)
    dictFiles[S_FIT_OUTPUT] = "300"
    dictFiles[S_PLOT_OUTPUT] = "300"
    assert sorted(_fdictPoll(dictCtx, dictWorkflow, dictFiles, dictState)) == [
        0, 1]


def testOnlyTheStepsTheRunExecutedAreMeasured():
    dictWorkflow = _fdictWorkflow()
    dictState = {"dictStepStats": {"2": {"iExitCode": 0}}}
    assert fdictMeasureRunOutputModTimes(
        _StatDouble({S_FIT_OUTPUT: "1", S_PLOT_OUTPUT: "2"}),
        S_CONTAINER_ID, dictState, dictWorkflow, S_WORKFLOW_PATH,
    ) == {S_PLOT_OUTPUT: "2"}


def testAnotherProjectsRunIsNeverMerged():
    dictCtx = {}
    dictWorkflow = _fdictWorkflow()
    dictFiles, dictState = _ftRunTheFitStep(dictCtx, dictWorkflow)
    dictState["sWorkflowPath"] = "/other/.vaibify/projects/project.json"
    assert list(_fdictPoll(dictCtx, dictWorkflow, dictFiles, dictState)) == [0]


def testAFailedMeasurementHidesNothing():
    dictState = {"dictStepStats": {"1": {}}}
    connectionBroken = MagicMock()
    connectionBroken.fdictStatPathMtimes.side_effect = RuntimeError("gone")
    assert fdictMeasureRunOutputModTimes(
        connectionBroken, S_CONTAINER_ID, dictState, _fdictWorkflow(),
        S_WORKFLOW_PATH) == {}


def testTheStateRouteDoesNotShipTheMeasurement(monkeypatch):
    fnStandCarrierDown(monkeypatch, pipelineRoutes)
    dictCtx = {"require": MagicMock(), "dictSyncEpochs": {}}
    app = FastAPI()
    with patch.object(pipelineRoutes, "_fsRunStateProjectForRequest",
                      return_value="/repo"), \
            patch("vaibify.gui.pipelineState.fdictReadReconciledState",
                  AsyncMock(return_value={
                      "bRunning": False,
                      S_RUN_OUTPUT_MODTIMES_KEY: {S_FIT_OUTPUT: "200"}})):
        pipelineRoutes._fnRegisterPipelineState(app, dictCtx)
        response = TestClient(app).get(
            "/api/pipeline/" + S_CONTAINER_ID + "/state")
    assert response.status_code == 200, response.text
    assert S_RUN_OUTPUT_MODTIMES_KEY not in response.json()
    assert response.json()["bRunning"] is False
