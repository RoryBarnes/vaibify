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


def _dictNewStepPayload(sName="New"):
    """Return a valid create-step payload body.

    Distinct names matter now: the slug contract rejects two steps
    whose names map to the same directory.
    """
    return {
        "sName": sName,
        "sDirectory": "",
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
        json=_dictNewStepPayload("New Two"),
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


# -----------------------------------------------------------------------
# Input-data agent lane: add-input-data-file + declare-no-input-data
# -----------------------------------------------------------------------


def testAddInputDataFileAppendsAndSaves(tClientAndWorkflow):
    clientHttp, dictWorkflow, listSaves = tClientAndWorkflow(2)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/input-data",
        json={"sPath": "data/observations.csv"},
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bAdded"] is True
    assert dictWorkflow["listSteps"][1]["saInputDataFiles"] == [
        "data/observations.csv",
    ]
    assert len(listSaves) == 1


def testAddInputDataFileDeduplicatesWithoutSaving(tClientAndWorkflow):
    clientHttp, dictWorkflow, listSaves = tClientAndWorkflow(1)
    dictWorkflow["listSteps"][0]["saInputDataFiles"] = [
        "data/observations.csv",
    ]
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/input-data",
        json={"sPath": "data/observations.csv"},
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bAdded"] is False
    assert dictWorkflow["listSteps"][0]["saInputDataFiles"] == [
        "data/observations.csv",
    ]
    assert listSaves == []


def testAddInputDataFileRejectsTraversalAndTokens(tClientAndWorkflow):
    clientHttp, dictWorkflow, _ = tClientAndWorkflow(1)
    for sBadPath in ("../escape.csv", "/etc/passwd", "{Step01.out}"):
        responseHttp = clientHttp.post(
            f"/api/steps/{S_CONTAINER_ID}/0/input-data",
            json={"sPath": sBadPath},
        )
        assert responseHttp.status_code == 400, sBadPath
    assert "saInputDataFiles" not in dictWorkflow["listSteps"][0] or \
        dictWorkflow["listSteps"][0]["saInputDataFiles"] == []


def testAddInputDataFileOutOfRangeIs404(tClientAndWorkflow):
    clientHttp, _, _ = tClientAndWorkflow(1)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/7/input-data",
        json={"sPath": "data/raw.csv"},
    )
    assert responseHttp.status_code == 404


def testDeclareNoInputDataOnlyTouchesUndeclaredSteps(tClientAndWorkflow):
    clientHttp, dictWorkflow, listSaves = tClientAndWorkflow(3)
    dictWorkflow["listSteps"][0]["saInputDataFiles"] = ["data/a.csv"]
    dictWorkflow["listSteps"][1]["bNoInputData"] = True
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/declare-no-input-data",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["listDeclaredStepIndices"] == [2]
    assert dictWorkflow["listSteps"][2]["bNoInputData"] is True
    assert "bNoInputData" not in dictWorkflow["listSteps"][0]
    assert len(listSaves) == 1


def testDeclareNoInputDataNoOpWhenAllDeclared(tClientAndWorkflow):
    clientHttp, dictWorkflow, listSaves = tClientAndWorkflow(1)
    dictWorkflow["listSteps"][0]["bNoInputData"] = True
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/declare-no-input-data",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["listDeclaredStepIndices"] == []
    assert listSaves == []


def testFingerprintMismatchConflictsRegardlessOfSortOrder():
    """The compare-and-swap check is equality, not ordering.

    A stale base fingerprint must 409 whether it sorts above or below
    the current fingerprint — an ordering comparison would wave
    through every stale writer whose fingerprint happens to sort on
    the accepted side, silently clobbering the concurrent edit. A
    matching fingerprint and the ``None`` opt-out must both pass.
    """
    from fastapi import HTTPException
    from vaibify.gui import workflowManager
    dictWorkflow = _fdictBuildWorkflow(1)
    sCurrent = workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
    stepRoutes._fnRequireFingerprintMatch(dictWorkflow, sCurrent)
    stepRoutes._fnRequireFingerprintMatch(dictWorkflow, None)
    for sStale in (sCurrent + "0", sCurrent[:-1]):
        with pytest.raises(HTTPException) as excInfo:
            stepRoutes._fnRequireFingerprintMatch(dictWorkflow, sStale)
        assert excInfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Marker-follows-directory through the REAL route wiring (2026-07-18).
# The shipped bug: the align/rename routes passed
# dictWorkflow.get("sPath") — a key the workflow dict never carries —
# so the marker namespace slug was empty and every marker move
# silently no-op'd, orphaning verification records. The unit fixtures
# had encoded the same wrong key (green-stub trap); this test drives
# the actual route with the path where it really lives
# (dictCtx["paths"]) and a REAL marker file on disk.
# ---------------------------------------------------------------------------


class _FakeAlignDocker:
    """Exec results for the align cascade's directory move."""

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if "test -e" in sCommand:
            return (1, "")   # destination absent
        if "test -d" in sCommand:
            return (0, "")   # source present
        return (0, "")       # git mv succeeds


def testAlignRouteMovesTheMarkerThroughRealWiring(tmp_path):
    import json as moduleJson

    sMarkerDir = tmp_path / ".vaibify" / "test_markers" / "study"
    sMarkerDir.mkdir(parents=True)
    (sMarkerDir / "legacyDir.json").write_text(moduleJson.dumps(
        {"sLabel": "A01", "sDirectory": "legacyDir",
         "dictOutputHashes": {"legacyDir/out.csv": "abc"}},
    ))
    dictWorkflow = {
        "sWorkflowName": "Wiring Test",
        "sProjectRepoPath": str(tmp_path),
        "listSteps": [{
            "sName": "Old Step", "sDirectory": "legacyDir",
            "sLabel": "A01",
            "saOutputDataFiles": ["legacyDir/out.csv"],
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    listSaves = []
    dictCtx = _fdictBuildContext(dictWorkflow, listSaves)
    dictCtx["docker"] = _FakeAlignDocker()
    dictCtx["pipelineTasks"] = {}
    # The workflow file path lives HERE — never on the workflow dict.
    dictCtx["paths"] = {
        S_CONTAINER_ID: "/workspace/repo/.vaibify/workflows/study.json",
    }
    app = FastAPI()
    stepRoutes.fnRegisterAll(app, dictCtx)
    clientHttp = TestClient(app)

    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/align-directories",
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listSkipped"] == []
    assert len(dictResult["listAligned"]) == 1

    # The marker followed the directory: new name, rewritten record.
    assert not (sMarkerDir / "legacyDir.json").exists(), (
        "the old marker must not be left behind"
    )
    sNewMarker = sMarkerDir / "OldStep.json"
    assert sNewMarker.exists(), (
        "the verification marker must follow the directory rename"
    )
    dictMarker = moduleJson.loads(sNewMarker.read_text())
    assert dictMarker["sDirectory"] == "OldStep"
    assert dictMarker["dictOutputHashes"] == {
        "legacyDir/out.csv": "abc",
    }
