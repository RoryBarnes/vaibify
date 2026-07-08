"""Tests for the AI Declaration add-step and determinism declare routes.

Covers the two POST endpoints added for the AICS ladder:

* ``POST /api/workflow/{id}/ai-declaration/add-step`` (levelRoutes)
  appends ``fdictBuildAiDeclarationStep(...)`` to the end of
  ``listSteps``, returns 409 when a declaration step already exists,
  and validates ``sDirectory`` (repo-relative, no ``..``, unique
  among step directories).
* ``POST /api/workflow/{id}/determinism/declare``
  (reproducibilityRoutes) writes ``dictWorkflow["dictDeterminism"]``
  with exactly the scalar keys ``determinismGate`` reads, rejecting
  empty bodies, unknown keys, and wrong types with 422.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes import levelRoutes, reproducibilityRoutes
from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
    S_DEFAULT_DECLARATION_DIRECTORY,
    S_DEFAULT_DECLARATION_FILENAME,
    S_DEFAULT_DECLARATION_STEP_NAME,
)
from vaibify.reproducibility.determinismGate import (
    fbWorkflowDeclaresDeterminism,
)


S_CONTAINER_ID = "declaration_cid"
S_ADD_STEP_URL = (
    f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/add-step"
)
S_DETERMINISM_URL = (
    f"/api/workflow/{S_CONTAINER_ID}/determinism/declare"
)


def _fdictBuildDataStep(sDirectory):
    """Return a minimal pre-existing data step for listSteps."""
    return {
        "sName": f"Existing {sDirectory}",
        "sDirectory": sDirectory,
        "saPlotCommands": [],
        "saPlotFiles": [],
    }


@pytest.fixture
def fixtureWorkflow(tmp_path):
    """Return a workflow dict with one pre-existing data step."""
    return {
        "sProjectRepoPath": str(tmp_path),
        "sPlotDirectory": "Plot",
        "listSteps": [_fdictBuildDataStep("makeData")],
    }


@pytest.fixture
def fixtureSaves():
    """Collect (sContainerId, dictWorkflow) tuples from dictCtx['save']."""
    return []


@pytest.fixture
def fixtureClient(fixtureWorkflow, fixtureSaves):
    """Build a TestClient with both route modules registered."""
    app = FastAPI()
    dictCtx = {
        "docker": None,
        "workflows": {S_CONTAINER_ID: fixtureWorkflow},
        "paths": {},
        "require": lambda: None,
        "save": lambda sId, dictWf: fixtureSaves.append((sId, dictWf)),
        "variables": lambda sId: {},
        "workflowDir": lambda sId: fixtureWorkflow["sProjectRepoPath"],
    }
    levelRoutes.fnRegisterAll(app, dictCtx)
    reproducibilityRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


# ============================================================================
# POST .../ai-declaration/add-step
# ============================================================================


def test_add_step_appends_declaration_step_at_end(
    fixtureClient, fixtureWorkflow, fixtureSaves,
):
    response = fixtureClient.post(S_ADD_STEP_URL, json={})
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["iIndex"] == len(fixtureWorkflow["listSteps"]) - 1
    dictStep = fixtureWorkflow["listSteps"][-1]
    assert dictStep["sStepKind"] == S_AI_DECLARATION_STEP_KIND
    assert dictStep["sName"] == S_DEFAULT_DECLARATION_STEP_NAME
    assert dictStep["sDirectory"] == S_DEFAULT_DECLARATION_DIRECTORY
    assert dictStep["sDeclarationFile"] == S_DEFAULT_DECLARATION_FILENAME
    assert dictStep["bInteractive"] is True
    assert fixtureSaves and fixtureSaves[-1][0] == S_CONTAINER_ID


def test_add_step_honors_body_overrides(fixtureClient, fixtureWorkflow):
    response = fixtureClient.post(S_ADD_STEP_URL, json={
        "sName": "Declare AI Assistance",
        "sDirectory": "declarations",
        "sDeclarationFile": "docs/AI_USAGE.md",
    })
    assert response.status_code == 200
    dictStep = fixtureWorkflow["listSteps"][-1]
    assert dictStep["sName"] == "Declare AI Assistance"
    assert dictStep["sDirectory"] == "declarations"
    assert dictStep["sDeclarationFile"] == "docs/AI_USAGE.md"


def test_add_step_409_when_declaration_step_exists(
    fixtureClient, fixtureWorkflow,
):
    assert fixtureClient.post(S_ADD_STEP_URL, json={}).status_code == 200
    response = fixtureClient.post(
        S_ADD_STEP_URL, json={"sDirectory": "anotherDirectory"},
    )
    assert response.status_code == 409
    assert "already has" in response.text
    assert len(fixtureWorkflow["listSteps"]) == 2


def test_add_step_rejects_absolute_directory(fixtureClient):
    response = fixtureClient.post(
        S_ADD_STEP_URL, json={"sDirectory": "/etc"},
    )
    assert response.status_code == 400
    assert "repo-relative" in response.text


def test_add_step_rejects_dotdot_directory(fixtureClient):
    response = fixtureClient.post(
        S_ADD_STEP_URL, json={"sDirectory": "../outside"},
    )
    assert response.status_code == 400
    assert "'..'" in response.text


def test_add_step_rejects_duplicate_directory(fixtureClient):
    response = fixtureClient.post(
        S_ADD_STEP_URL, json={"sDirectory": "makeData"},
    )
    assert response.status_code == 409
    assert "already used" in response.text


def test_add_step_rejects_escaping_declaration_file(fixtureClient):
    response = fixtureClient.post(
        S_ADD_STEP_URL, json={"sDeclarationFile": "../AI_USAGE.md"},
    )
    assert response.status_code == 400


def test_add_step_unknown_container_returns_404(fixtureClient):
    response = fixtureClient.post(
        "/api/workflow/no-such-id/ai-declaration/add-step", json={},
    )
    assert response.status_code == 404


# ============================================================================
# POST .../determinism/declare
# ============================================================================


def test_declare_determinism_writes_dict_and_saves(
    fixtureClient, fixtureWorkflow, fixtureSaves,
):
    response = fixtureClient.post(S_DETERMINISM_URL, json={
        "bAcceptBlasVariance": False,
        "dOmpNumThreads": 1,
        "sMklCbwr": "COMPATIBLE",
    })
    assert response.status_code == 200
    dictDeterminism = fixtureWorkflow["dictDeterminism"]
    assert dictDeterminism == {
        "bAcceptBlasVariance": False,
        "dOmpNumThreads": 1,
        "sMklCbwr": "COMPATIBLE",
    }
    assert response.json()["dictDeterminism"] == dictDeterminism
    assert fbWorkflowDeclaresDeterminism(fixtureWorkflow) is True
    assert fixtureSaves and fixtureSaves[-1][0] == S_CONTAINER_ID


def test_declare_determinism_single_key_merges_with_existing(
    fixtureClient, fixtureWorkflow,
):
    fixtureWorkflow["dictDeterminism"] = {"sMklCbwr": "AUTO"}
    response = fixtureClient.post(
        S_DETERMINISM_URL, json={"dOmpNumThreads": 4},
    )
    assert response.status_code == 200
    assert fixtureWorkflow["dictDeterminism"] == {
        "sMklCbwr": "AUTO", "dOmpNumThreads": 4,
    }


def test_declare_determinism_null_value_removes_the_key(
    fixtureClient, fixtureWorkflow,
):
    """A null value retracts a declared key.

    The endpoint merges keys, so before nulls were accepted a
    mistaken pin (an OpenMP thread count the researcher cleared in
    the form) survived every re-declaration — the GUI showed the old
    pin forever.
    """
    fixtureWorkflow["dictDeterminism"] = {
        "bAcceptBlasVariance": True, "dOmpNumThreads": 1,
    }
    response = fixtureClient.post(
        S_DETERMINISM_URL,
        json={"bAcceptBlasVariance": True, "dOmpNumThreads": None},
    )
    assert response.status_code == 200
    assert fixtureWorkflow["dictDeterminism"] == {
        "bAcceptBlasVariance": True,
    }


def test_declare_determinism_empty_body_returns_422(
    fixtureClient, fixtureWorkflow,
):
    response = fixtureClient.post(S_DETERMINISM_URL, json={})
    assert response.status_code == 422
    assert "dictDeterminism" not in fixtureWorkflow


def test_declare_determinism_rejects_unknown_key(
    fixtureClient, fixtureWorkflow,
):
    response = fixtureClient.post(
        S_DETERMINISM_URL, json={"sUnknownKey": "value"},
    )
    assert response.status_code == 422
    assert "Unknown determinism key" in response.text
    assert "dictDeterminism" not in fixtureWorkflow


@pytest.mark.parametrize("dictBadBody", [
    {"bAcceptBlasVariance": "yes"},
    {"bAcceptBlasVariance": 1},
    {"dOmpNumThreads": True},
    {"dOmpNumThreads": "4"},
    {"sMklCbwr": 5},
    {"sMklCbwr": ["COMPATIBLE"]},
    {"bAcceptBlasVariance": {"bNested": True}},
])
def test_declare_determinism_rejects_wrong_types(
    fixtureClient, fixtureWorkflow, dictBadBody,
):
    response = fixtureClient.post(S_DETERMINISM_URL, json=dictBadBody)
    assert response.status_code == 422
    assert "dictDeterminism" not in fixtureWorkflow
