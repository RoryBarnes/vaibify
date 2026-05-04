"""Tests for POST /api/workflow/{id}/manifest/verify."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.pipelineRoutes import fnRegisterAll
from vaibify.reproducibility import manifestWriter


S_CONTAINER_ID = "verify_cid"


def _fdictBuildWorkflow(sProjectRepo):
    """Return a minimal workflow dict with two declared output files."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [
            {
                "sDirectory": "step01",
                "saDataFiles": ["step01/data.csv"],
                "saPlotFiles": [],
                "saOutputFiles": ["step01/results.json"],
            },
        ],
    }


def _fnWriteFile(sPath, sContent):
    """Write text content to sPath, creating parents as needed."""
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(sContent)


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Create a temp project repo with two output files."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
    _fnWriteFile(
        os.path.join(sRepo, "step01", "data.csv"), "alpha,beta\n1,2\n",
    )
    _fnWriteFile(
        os.path.join(sRepo, "step01", "results.json"), '{"value": 42}\n',
    )
    return sRepo


@pytest.fixture
def fixtureClient(fixtureProjectRepo):
    """Build a minimal app with pipelineRoutes wired to a temp workflow."""
    app = FastAPI()
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflows = {S_CONTAINER_ID: dictWorkflow}
    dictCtx = {
        "docker": None,
        "workflows": dictWorkflows,
        "paths": {},
        "pipelineTasks": {},
        "sourceCodeDeps": {},
        "setAllowedContainers": {S_CONTAINER_ID},
        "sSessionToken": "tok",
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: fixtureProjectRepo,
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


def testVerifyManifestReturnsEmptyOnHappyPath(
    fixtureProjectRepo, fixtureClient,
):
    """Manifest is current → response reports zero mismatches."""
    manifestWriter.fnWriteManifest(
        fixtureProjectRepo,
        _fdictBuildWorkflow(fixtureProjectRepo),
    )
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/verify",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["iTotal"] == 2
    assert dictBody["iMatching"] == 2
    assert dictBody["listMismatches"] == []


def testVerifyManifestReportsDriftedFile(
    fixtureProjectRepo, fixtureClient,
):
    """A file modified after manifest write surfaces in listMismatches."""
    manifestWriter.fnWriteManifest(
        fixtureProjectRepo,
        _fdictBuildWorkflow(fixtureProjectRepo),
    )
    _fnWriteFile(
        os.path.join(fixtureProjectRepo, "step01", "data.csv"),
        "drifted,values\n9,9\n",
    )
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/verify",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["iMatching"] == 1
    listPaths = [d["sPath"] for d in dictBody["listMismatches"]]
    assert "step01/data.csv" in listPaths


def testVerifyManifestReturns404ForUnknownWorkflow(fixtureClient):
    """Unknown container id is rejected with 404."""
    response = fixtureClient.post(
        "/api/workflow/no_such_cid/manifest/verify",
    )
    assert response.status_code == 404


def testVerifyManifestReturns409WhenManifestMissing(fixtureClient):
    """No MANIFEST.sha256 yields a 409 with an actionable message."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/verify",
    )
    assert response.status_code == 409
    assert "MANIFEST" in response.json()["detail"]


def testVerifyManifestITotalMatchesManifestCountNotWorkflowOutputs(
    fixtureProjectRepo, fixtureClient,
):
    """iTotal reflects manifest entry count, not workflow output count."""
    sManifest = os.path.join(fixtureProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write("# extra entries beyond workflow outputs\n")
        for iIndex in range(5):
            sHash = chr(ord("a") + iIndex) * 64
            fileHandle.write(f"{sHash}  step01/extra_{iIndex}.dat\n")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/verify",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["iTotal"] == 5
    assert dictBody["iMatching"] == 5 - len(dictBody["listMismatches"])


def testVerifyManifestReturns422WhenManifestIsMalformed(
    fixtureProjectRepo, fixtureClient,
):
    """A malformed manifest line yields 422, not 500.

    The parser raises ``ValueError`` on lines missing the two-space
    separator. The route must translate that to an unprocessable-entity
    response with an actionable message and no stack-trace leak.
    """
    sManifest = os.path.join(fixtureProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write("# malformed: no two-space separator below\n")
        fileHandle.write("garbage_line_with_no_separator\n")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/verify",
    )
    assert response.status_code == 422
    sDetail = response.json()["detail"]
    assert "MANIFEST" in sDetail
    assert "malformed" in sDetail.lower()
    # The detail must not leak the absolute project repo path.
    assert fixtureProjectRepo not in sDetail
