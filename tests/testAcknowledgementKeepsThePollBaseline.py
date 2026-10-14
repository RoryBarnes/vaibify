"""Acknowledging a step must not make its untouched inputs read as changed.

Researcher-reported (QuickStart section 5, 2026-10-13): after Run All,
the first step's warning column said its input data file had "changed
after you verified". The file had not changed and nobody had verified.
Every passing step makes the dashboard acknowledge it, and the
acknowledgment used to REPLACE the poll's mtime baseline with that
step's outputs alone; the next poll then found the input missing from
the baseline, and a missing path reads as changed.

These drive the real acknowledge route between two real polls. Only
the container stat is replaced, by the mtimes a stat would return.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui import workflowManager
from vaibify.gui.fileStatusManager import _fdictDetectChangedFiles
from vaibify.gui.routes import pipelineRoutes


S_CONTAINER_ID = "containerOne"
S_REPO_ROOT = "/repo"
S_INPUT_PATH = "/repo/inputData.json"
S_OUTPUT_PATH = "/repo/stepOne/fitResults.json"
DICT_FILE_VARIABLES = {
    "sPlotDirectory": "Plot", "sFigureType": "pdf",
    "sRepoRoot": S_REPO_ROOT,
}


def _fdictBuildWorkflow():
    """Return a one-step workflow that declares one input and one output."""
    return {
        "sProjectRepoPath": S_REPO_ROOT,
        workflowManager.S_LOADED_FROM_KEY:
            "/repo/.vaibify/projects/project.json",
        "listSteps": [{
            "sName": "Step One",
            "sDirectory": "stepOne",
            "saOutputDataFiles": ["fitResults.json"],
            "saInputDataFiles": ["inputData.json"],
            "dictVerification": {},
        }],
    }


def _fdictBuildContext(dictWorkflow):
    """Return the hub context the acknowledge route reads."""
    return {
        "docker": MagicMock(),
        "require": MagicMock(),
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "save": MagicMock(),
        "variables": MagicMock(return_value=dict(DICT_FILE_VARIABLES)),
    }


def _fnAcknowledgeStepOne(monkeypatch, dictCtx, dictWorkflow, sNewMtime):
    """POST acknowledge-step for step 0 with its output stat as given."""
    fnStandCarrierDown(monkeypatch, pipelineRoutes)
    app = FastAPI()
    with patch(
        "vaibify.gui.routes.pipelineRoutes.fdictRequireWorkflow",
        return_value=dictWorkflow,
    ), patch(
        "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
        return_value={S_OUTPUT_PATH: sNewMtime},
    ):
        pipelineRoutes._fnRegisterAcknowledgeStep(app, dictCtx)
        response = TestClient(app).post(
            "/api/pipeline/" + S_CONTAINER_ID + "/acknowledge-step/0",
        )
    assert response.status_code == 200


def _fdictPoll(dictCtx, dictWorkflow, dictModTimes):
    """Run the poll's change detection as the idle dashboard does."""
    return _fdictDetectChangedFiles(
        dictCtx, S_CONTAINER_ID, dictWorkflow, dictModTimes,
        dictVars=dict(DICT_FILE_VARIABLES), bPipelineRunning=False,
    )


@pytest.mark.falsification
def testAnAcknowledgedStepsUntouchedInputIsNotReportedChanged(monkeypatch):
    """Kills: the acknowledgment replacing the baseline with its outputs."""
    dictWorkflow = _fdictBuildWorkflow()
    dictCtx = _fdictBuildContext(dictWorkflow)
    _fdictPoll(dictCtx, dictWorkflow, {
        S_INPUT_PATH: "1000", S_OUTPUT_PATH: "1000",
    })
    _fnAcknowledgeStepOne(monkeypatch, dictCtx, dictWorkflow, "2000")
    dictChanged = _fdictPoll(dictCtx, dictWorkflow, {
        S_INPUT_PATH: "1000", S_OUTPUT_PATH: "2000",
    })
    assert dictChanged == {}


def testAnInputEditedAfterTheAcknowledgmentIsStillReported(monkeypatch):
    dictWorkflow = _fdictBuildWorkflow()
    dictCtx = _fdictBuildContext(dictWorkflow)
    _fdictPoll(dictCtx, dictWorkflow, {
        S_INPUT_PATH: "1000", S_OUTPUT_PATH: "1000",
    })
    _fnAcknowledgeStepOne(monkeypatch, dictCtx, dictWorkflow, "2000")
    dictChanged = _fdictPoll(dictCtx, dictWorkflow, {
        S_INPUT_PATH: "3000", S_OUTPUT_PATH: "2000",
    })
    assert dictChanged == {0: [S_INPUT_PATH]}


def testAnAcknowledgmentBeforeAnyPollLeavesNothingToCompare(monkeypatch):
    dictWorkflow = _fdictBuildWorkflow()
    dictCtx = _fdictBuildContext(dictWorkflow)
    _fnAcknowledgeStepOne(monkeypatch, dictCtx, dictWorkflow, "2000")
    dictChanged = _fdictPoll(dictCtx, dictWorkflow, {
        S_INPUT_PATH: "1000", S_OUTPUT_PATH: "2000",
    })
    assert dictChanged == {}
