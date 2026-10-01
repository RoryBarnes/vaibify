"""Coverage of the refusals and the alignment batch in ``stepRoutes``.

Step names become directory names, so a name the slug contract rejects
must be refused with its reason, an out-of-range reorder must be a 400
that leaves the list alone, and the directory-alignment batch must
report every step it could not move while saving a workflow whose
directory moved and could not be moved back. The Docker exec boundary
is a double that answers from a script; the project repository is a
real directory read through the real host adapter.
"""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui.fileStatusManager import fsMarkerNameFromStepDirectory
from vaibify.gui.pipelineUtils import fsSlugFromStepName
from vaibify.gui.routes import stepRoutes


S_CONTAINER_ID = "d0cker1dstep"
S_WORKFLOW_FILENAME = "workflowAlpha.json"


class ScriptedConnection:
    """Answer each exec from a script, recording every command."""

    def __init__(self, listAnswers):
        self.listAnswers = list(listAnswers)
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return self.listAnswers.pop(0)


def fdictBuildStep(sName, sDirectory=None):
    """Return a minimal step; the directory defaults to the name's slug."""
    return {
        "sName": sName,
        "sDirectory": fsSlugFromStepName(sName) if sDirectory is None
        else sDirectory,
        "bPlotOnly": False,
        "bRunEnabled": True,
        "bInteractive": False,
        "saDataCommands": [],
        "saOutputDataFiles": [],
        "saTestCommands": [],
        "saPlotCommands": [],
        "saPlotFiles": [],
    }


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    fnStandCarrierDown(monkeypatch, stepRoutes)


def ftBuildClient(dictWorkflow, connectionDocker=None, sWorkflowPath=""):
    """Return ``(client, listSaves)`` over the step routes alone."""
    listSaves = []
    dictCtx = {
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {S_CONTAINER_ID: sWorkflowPath} if sWorkflowPath else {},
        "pipelineTasks": {},
        "docker": connectionDocker,
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: listSaves.append(
            [dictStep["sDirectory"] for dictStep in dictWf["listSteps"]],
        ),
        "variables": lambda sId: {},
    }
    app = FastAPI()
    stepRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), listSaves


def fdictNewStepBody(sName):
    return {
        "sName": sName, "sDirectory": "", "bPlotOnly": False,
        "saPlotCommands": [], "saPlotFiles": [],
    }


def testCreatingAStepWithAForbiddenNameIsRefusedWithTheReason(
    fixtureCarrierStoodDown,
):
    dictWorkflow = {"listSteps": [fdictBuildStep("Step Alpha")]}
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=fdictNewStepBody("../escape"),
    )
    assert responseHttp.status_code == 400
    assert "only letters, digits, spaces" in responseHttp.json()["detail"]
    assert len(dictWorkflow["listSteps"]) == 1
    assert listSaves == []


def testInsertingTheHundredthStepRaisesTheWarningOnce(
    fixtureCarrierStoodDown,
):
    dictWorkflow = {
        "bWarnedHundredSteps": False,
        "listSteps": [fdictBuildStep(f"Step {i}") for i in range(99)],
    }
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/insert/0",
        json=fdictNewStepBody("Step Inserted"),
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["bShouldWarnHundredSteps"] is True
    assert dictWorkflow["bWarnedHundredSteps"] is True
    assert dictWorkflow["listSteps"][0]["sName"] == "Step Inserted"
    assert len(listSaves) == 2


def testReorderingOutOfRangeIsA400ThatLeavesTheOrderAlone(
    fixtureCarrierStoodDown,
):
    dictWorkflow = {"listSteps": [
        fdictBuildStep("Step Alpha"), fdictBuildStep("Step Beta"),
    ]}
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/reorder",
        json={"iFromIndex": 0, "iToIndex": 7},
    )
    assert responseHttp.status_code == 400
    assert [d["sName"] for d in dictWorkflow["listSteps"]] == [
        "Step Alpha", "Step Beta",
    ]
    assert listSaves == []


def testRenamingToTheSameNameIsA400(fixtureCarrierStoodDown):
    dictWorkflow = {"listSteps": [fdictBuildStep("Step Alpha")]}
    clientHttp, _ = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Step Alpha", "bDryRun": True},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == (
        "The new name matches the current name"
    )


def testRenamingWithoutAProjectRepoIsAConflictAndSavesNothing(
    fixtureCarrierStoodDown,
):
    dictWorkflow = {"listSteps": [fdictBuildStep("Step Alpha")]}
    clientHttp, listSaves = ftBuildClient(
        dictWorkflow, sWorkflowPath="/workspace/.vaibify/workflows/w.json",
    )
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Step Gamma", "bDryRun": False},
    )
    assert responseHttp.status_code == 409
    assert "no project repo" in responseHttp.json()["detail"]
    assert dictWorkflow["listSteps"][0]["sName"] == "Step Alpha"
    assert listSaves == []


def testAConfirmedTestCategoryDropNeedsNoFurtherConsent():
    dictStep = {"dictTests": {"dictQualitative": {"saCommands": ["pytest"]}}}
    dictUpdates = {"dictTests": {}}
    stepRoutes._fnRequireTestCategoryConfirm(dictStep, dictUpdates, True)
    with pytest.raises(Exception) as excInfo:
        stepRoutes._fnRequireTestCategoryConfirm(dictStep, dictUpdates, False)
    assert excInfo.value.status_code == 400
    assert "dictQualitative" in excInfo.value.detail


def testAlignmentReportsEveryStepItCouldNotMove(fixtureCarrierStoodDown):
    """Conforming, templated and invalid-name steps are each handled."""
    dictWorkflow = {"listSteps": [
        fdictBuildStep("Step Conforming"),
        fdictBuildStep("Step Templated", "{sRoot}/legacyTemplated"),
        fdictBuildStep("Bad_Name!", "legacyBad"),
    ]}
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/align-directories",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["listAligned"] == []
    assert len(dictBody["listSkipped"]) == 1
    assert dictBody["listSkipped"][0]["sLabel"] == "step 2"
    assert "only letters, digits" in dictBody["listSkipped"][0]["sReason"]
    assert dictWorkflow["listSteps"][1]["sDirectory"] == (
        "{sRoot}/legacyTemplated"
    )
    assert listSaves == []


def testAnAlignmentThatCannotBeUndoneIsSavedAndReported(
    tmp_path, fixtureCarrierStoodDown,
):
    """A split between disk and workflow must be persisted, never lost."""
    sProjectRepo = str(tmp_path / "projectRepo")
    sOldDirectory = "legacyDirectory"
    sMarkerDirectory = os.path.join(
        sProjectRepo, ".vaibify", "test_markers", "workflowAlpha",
    )
    os.makedirs(sMarkerDirectory)
    with open(os.path.join(
        sMarkerDirectory, fsMarkerNameFromStepDirectory(sOldDirectory),
    ), "w") as fileHandle:
        fileHandle.write("{corrupt marker")
    dictWorkflow = {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [fdictBuildStep("Step Alpha", sOldDirectory)],
    }
    connectionDocker = ScriptedConnection([
        (1, ""), (0, ""), (0, ""), (1, "mv: device busy"),
    ])
    clientHttp, listSaves = ftBuildClient(
        dictWorkflow, connectionDocker,
        sWorkflowPath=sProjectRepo + "/.vaibify/workflows/"
        + S_WORKFLOW_FILENAME,
    )
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/align-directories",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["listAligned"] == []
    assert "could not be undone" in dictBody["listSkipped"][0]["sReason"]
    sNewDirectory = fsSlugFromStepName("Step Alpha")
    assert listSaves == [[sNewDirectory]]
    assert dictWorkflow["listSteps"][0]["sName"] == "Step Alpha"
    assert len(connectionDocker.listCommands) == 4
    assert all(
        sId == S_CONTAINER_ID for sId, _ in connectionDocker.listCommands
    )
    assert json.dumps(dictBody).count(sProjectRepo) == 0


def testAFailedDirectoryMoveIsA500AndTheWorkflowIsUntouched(
    tmp_path, fixtureCarrierStoodDown,
):
    sProjectRepo = str(tmp_path / "projectRepo")
    os.makedirs(sProjectRepo)
    sOldDirectory = fsSlugFromStepName("Step Alpha")
    dictWorkflow = {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [fdictBuildStep("Step Alpha")],
    }
    connectionDocker = ScriptedConnection([
        (1, ""), (0, ""), (128, "fatal: source directory is busy"),
    ])
    clientHttp, listSaves = ftBuildClient(
        dictWorkflow, connectionDocker,
        sWorkflowPath=sProjectRepo + "/.vaibify/workflows/"
        + S_WORKFLOW_FILENAME,
    )
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Step Gamma", "bDryRun": False},
    )
    assert responseHttp.status_code == 500
    assert responseHttp.json()["detail"] == (
        "Could not move the step directory: "
        "fatal: source directory is busy"
    )
    assert dictWorkflow["listSteps"][0]["sName"] == "Step Alpha"
    assert dictWorkflow["listSteps"][0]["sDirectory"] == sOldDirectory
    assert listSaves == []
