"""Tests for the standalone-binary declaration model.

Covers the ``/api/workflow/{id}/binaries/declare`` endpoint
validation, the ``fdictCaptureSingleBinary`` capture helper, the
``fbBinaryCaptured`` predicate, and the ``fbWorkflowDeclaresBinaries``
state-machine gate.
"""

import json
import os
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.reproducibilityRoutes import fnRegisterAll
from vaibify.reproducibility.environmentSnapshot import (
    fbBinaryCaptured,
    fdictCaptureSingleBinary,
)
from vaibify.reproducibility.levelGates import fbWorkflowDeclaresBinaries


S_CONTAINER_ID = "binary_cid"


def _fdictBuildWorkflow(sProjectRepo):
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {},
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "bNoStandaloneBinaries": False,
        "listDeclaredBinaries": [],
    }


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    return sRepo


@pytest.fixture
def fixtureWorkflow(fixtureProjectRepo):
    return _fdictBuildWorkflow(fixtureProjectRepo)


@pytest.fixture
def fixtureClient(fixtureWorkflow):
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictWorkflows = {S_CONTAINER_ID: fixtureWorkflow}

    def _fnSave(sId, dictWf):
        pass

    dictCtx = {
        "docker": None,
        "workflows": dictWorkflows,
        "paths": {},
        "pipelineTasks": {},
        "sourceCodeDeps": {},
        "setAllowedContainers": {S_CONTAINER_ID},
        "sSessionToken": "tok",
        "require": lambda: None,
        "save": _fnSave,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: fixtureWorkflow["sProjectRepoPath"],
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


# ---------------------------------------------------------------------
# /api/workflow/{id}/binaries/declare validation
# ---------------------------------------------------------------------


def test_declare_waiver_requires_empty_list(fixtureClient):
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare",
        json={
            "bNoStandaloneBinaries": True,
            "listDeclaredBinaries": [
                {"sBinaryPath": "/x", "sPurpose": "y",
                 "sExpectedVersion": "1"},
            ],
        },
    )
    assert response.status_code == 400


def test_declare_non_waiver_requires_non_empty_list(fixtureClient):
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare",
        json={
            "bNoStandaloneBinaries": False,
            "listDeclaredBinaries": [],
        },
    )
    assert response.status_code == 400


def test_declare_entry_missing_fields_rejected(fixtureClient):
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare",
        json={
            "bNoStandaloneBinaries": False,
            "listDeclaredBinaries": [
                {"sBinaryPath": "/x", "sPurpose": "y"},
            ],
        },
    )
    assert response.status_code == 400


def test_declare_waiver_accepted(fixtureClient, fixtureWorkflow):
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare",
        json={
            "bNoStandaloneBinaries": True,
            "listDeclaredBinaries": [],
        },
    )
    assert response.status_code == 200
    assert fixtureWorkflow["bNoStandaloneBinaries"] is True
    assert fixtureWorkflow["listDeclaredBinaries"] == []


def test_declare_declaration_accepted(fixtureClient, fixtureWorkflow):
    listEntries = [
        {"sBinaryPath": "/usr/local/bin/vplanet",
         "sPurpose": "fwd model",
         "sExpectedVersion": "v3.0.0"},
    ]
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare",
        json={
            "bNoStandaloneBinaries": False,
            "listDeclaredBinaries": listEntries,
        },
    )
    assert response.status_code == 200
    assert fixtureWorkflow["listDeclaredBinaries"] == listEntries


# ---------------------------------------------------------------------
# fdictCaptureSingleBinary
# ---------------------------------------------------------------------


def _fsResolveRealBinary():
    """Return an absolute path to a real (non-symlink) binary on PATH."""
    for sCandidate in ("ls", "cat", "echo"):
        sPath = shutil.which(sCandidate)
        if not sPath:
            continue
        sReal = os.path.realpath(sPath)
        if os.path.isfile(sReal) and not os.path.islink(sReal):
            return sReal
    return None


def test_fdictCaptureSingleBinary_returns_expected_shape():
    """Capture shape is {sBinaryPath, sSha256, sVersion}."""
    sBin = _fsResolveRealBinary()
    if not sBin:
        pytest.skip("No non-symlink binary available on PATH")
    dictResult = fdictCaptureSingleBinary(sBin)
    assert dictResult["sBinaryPath"] == sBin
    assert isinstance(dictResult["sSha256"], str)
    assert len(dictResult["sSha256"]) == 64


def test_fdictCaptureSingleBinary_missing_path_returns_nones():
    dictResult = fdictCaptureSingleBinary("/nonexistent/binary/path")
    assert dictResult["sSha256"] is None
    assert dictResult["sVersion"] is None


# ---------------------------------------------------------------------
# fbBinaryCaptured
# ---------------------------------------------------------------------


def test_fbBinaryCaptured_finds_nested_layout():
    dictEnv = {
        "dictHostBinaries": {
            "listBinaries": [
                {"sBinaryPath": "/usr/local/bin/vplanet",
                 "sSha256": "a" * 64, "sVersion": "v3.0.0"},
            ],
        },
    }
    assert fbBinaryCaptured(dictEnv, "/usr/local/bin/vplanet")
    assert not fbBinaryCaptured(dictEnv, "/usr/local/bin/other")


def test_fbBinaryCaptured_finds_flat_layout():
    dictEnv = {
        "listBinaries": [
            {"sBinaryPath": "/opt/x", "sSha256": "b" * 64},
        ],
    }
    assert fbBinaryCaptured(dictEnv, "/opt/x")


def test_fbBinaryCaptured_rejects_empty():
    assert not fbBinaryCaptured({}, "/x")
    assert not fbBinaryCaptured({"listBinaries": []}, "/x")
    assert not fbBinaryCaptured(None, "/x")


# ---------------------------------------------------------------------
# fbWorkflowDeclaresBinaries
# ---------------------------------------------------------------------


def test_fbWorkflowDeclaresBinaries_accepts_waiver():
    assert fbWorkflowDeclaresBinaries({
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
    })


def test_fbWorkflowDeclaresBinaries_accepts_declaration():
    assert fbWorkflowDeclaresBinaries({
        "bNoStandaloneBinaries": False,
        "listDeclaredBinaries": [
            {"sBinaryPath": "/x", "sPurpose": "y",
             "sExpectedVersion": "1"},
        ],
    })


def test_fbWorkflowDeclaresBinaries_rejects_no_answer():
    assert not fbWorkflowDeclaresBinaries({"listSteps": []})


def test_fbWorkflowDeclaresBinaries_rejects_waiver_with_entries():
    assert not fbWorkflowDeclaresBinaries({
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [
            {"sBinaryPath": "/x", "sPurpose": "y",
             "sExpectedVersion": "1"},
        ],
    })


def test_fbWorkflowDeclaresBinaries_rejects_malformed_entries():
    assert not fbWorkflowDeclaresBinaries({
        "bNoStandaloneBinaries": False,
        "listDeclaredBinaries": [{"sBinaryPath": "/x"}],
    })


# ---------------------------------------------------------------------
# Capture endpoint smoke
# ---------------------------------------------------------------------


def test_capture_endpoint_persists_to_environment_json(
    fixtureClient, fixtureWorkflow,
):
    """The capture endpoint writes the binary to environment.json."""
    sBin = _fsResolveRealBinary()
    if not sBin:
        pytest.skip("No non-symlink binary available on PATH")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/capture",
        json={"sBinaryPath": sBin},
    )
    assert response.status_code == 200
    sRepo = fixtureWorkflow["sProjectRepoPath"]
    pathEnv = os.path.join(sRepo, ".vaibify", "environment.json")
    with open(pathEnv, "r", encoding="utf-8") as fileHandle:
        dictPayload = json.load(fileHandle)
    assert fbBinaryCaptured(dictPayload, sBin)
