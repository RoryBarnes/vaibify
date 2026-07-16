"""Tests for workflow-size guardrails on stepRoutes create/insert.

These exercises focus on the 100-step warning flag and the 500-step
hard cap added in WI-5/WI-6. The test client registers only the step
routes with a small in-memory dictCtx so the cases stay independent
of the wider pipelineServer wiring.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes import stepRoutes


S_CONTAINER_ID = "container-abc"


def _fdictBuildStep(iIndex):
    """Return a minimal step dict suitable for listSteps."""
    return {
        "sName": f"Step {iIndex}",
        "sDirectory": f"step{iIndex:03d}",
        "bPlotOnly": False,
        "bRunEnabled": True,
        "bInteractive": False,
        "saDataCommands": [],
        "saOutputDataFiles": [],
        "saTestCommands": [],
        "saPlotCommands": [],
        "saPlotFiles": [],
    }


def _fdictBuildWorkflow(iStepCount, bWarned=False):
    """Return a workflow dict with ``iStepCount`` synthetic steps."""
    return {
        "sWorkflowName": "Cap Test",
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": 1,
        "bWarnedHundredSteps": bWarned,
        "listSteps": [_fdictBuildStep(i) for i in range(iStepCount)],
    }


def _fdictBuildContext(dictWorkflow, listSaves):
    """Return a minimal dictCtx wiring only what stepRoutes consumes."""
    dictWorkflows = {S_CONTAINER_ID: dictWorkflow}

    def fnRequire():
        return None

    def fnSave(sContainerId, dictWorkflowIn):
        listSaves.append(len(dictWorkflowIn["listSteps"]))

    def fnVariables(sContainerId):
        return {}

    return {
        "workflows": dictWorkflows,
        "require": fnRequire,
        "save": fnSave,
        "variables": fnVariables,
    }


@pytest.fixture
def tClientAndWorkflow():
    """Return ``(clientHttp, dictWorkflow, listSaves)`` factory builder.

    The fixture yields a callable so each test can size the workflow
    independently.
    """

    def fbuild(iStepCount, bWarned=False):
        dictWorkflow = _fdictBuildWorkflow(iStepCount, bWarned=bWarned)
        listSaves = []
        dictCtx = _fdictBuildContext(dictWorkflow, listSaves)
        app = FastAPI()
        stepRoutes.fnRegisterAll(app, dictCtx)
        return TestClient(app), dictWorkflow, listSaves

    return fbuild


def _dictNewStepPayload():
    """Return a valid create-step payload body."""
    return {
        "sName": "New",
        "sDirectory": "newStep",
        "bPlotOnly": False,
        "saPlotCommands": [],
        "saPlotFiles": [],
    }


def testCreateStepRejectedAt500Cap(tClientAndWorkflow):
    clientHttp, dictWorkflow, _ = tClientAndWorkflow(500)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=_dictNewStepPayload(),
    )
    assert responseHttp.status_code == 400
    assert "exceed 500 steps" in responseHttp.json()["detail"]
    assert len(dictWorkflow["listSteps"]) == 500


def testInsertStepRejectedAt500Cap(tClientAndWorkflow):
    clientHttp, dictWorkflow, _ = tClientAndWorkflow(500)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/insert/0",
        json=_dictNewStepPayload(),
    )
    assert responseHttp.status_code == 400
    assert "exceed 500 steps" in responseHttp.json()["detail"]
    assert len(dictWorkflow["listSteps"]) == 500


def testWarnHundredFlagPersistsOnCrossing(tClientAndWorkflow):
    clientHttp, dictWorkflow, _ = tClientAndWorkflow(99)
    responseFirst = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=_dictNewStepPayload(),
    )
    assert responseFirst.status_code == 200
    assert responseFirst.json()["bShouldWarnHundredSteps"] is True
    assert dictWorkflow["bWarnedHundredSteps"] is True
    responseSecond = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=_dictNewStepPayload(),
    )
    assert responseSecond.status_code == 200
    assert responseSecond.json()["bShouldWarnHundredSteps"] is False


def testWarnHundredNotFlippedBelowThreshold(tClientAndWorkflow):
    clientHttp, dictWorkflow, _ = tClientAndWorkflow(50)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=_dictNewStepPayload(),
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bShouldWarnHundredSteps"] is False
    assert dictWorkflow["bWarnedHundredSteps"] is False
