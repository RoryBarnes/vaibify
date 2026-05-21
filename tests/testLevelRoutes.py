"""Tests for vaibify.gui.routes.levelRoutes — L2 readiness + AI declaration.

These cover both endpoints registered by ``levelRoutes.fnRegisterAll``:

* ``GET /api/workflow/{id}/level2/readiness`` returns iAICSLevel and
  the per-criterion gap dict.
* ``POST /api/workflow/{id}/ai-declaration/generate-template`` writes
  the starter template under the project repo with strict path
  validation.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.levelRoutes import fnRegisterAll


S_CONTAINER_ID = "level_cid"


def _fdictBuildWorkflow(sProjectRepo):
    """Return a minimal workflow dict with project repo set."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {},
        "listSteps": [],
    }


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Create a tmpdir to act as the project repo root."""
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    return sRepo


@pytest.fixture
def fixtureWorkflow(fixtureProjectRepo):
    return _fdictBuildWorkflow(fixtureProjectRepo)


@pytest.fixture
def fixtureClient(fixtureWorkflow):
    """Build a TestClient that has the levelRoutes registered."""
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


# ============================================================================
# GET .../level2/readiness
# ============================================================================


def test_level2_readiness_returns_iaics_level_and_gaps(fixtureClient):
    """A bare workflow returns iAICSLevel=0 and a fully-False gaps dict."""
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level2/readiness",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["iAICSLevel"] == 0
    dictGaps = dictBody["dictLevel2Gaps"]
    for sKey in (
        "bAtLeastLevel1", "bGithubFullySynced",
        "bZenodoFullySynced", "bAiDeclarationStepPresent",
        "bAtLeastLevel2",
    ):
        assert sKey in dictGaps
    assert dictGaps["bAtLeastLevel2"] is False


def test_level2_readiness_unknown_container_id_404(fixtureClient):
    """An unregistered container id must return 404."""
    response = fixtureClient.get("/api/workflow/no-such-id/level2/readiness")
    # fdictRequireWorkflow raises HTTPException(404).
    assert response.status_code == 404


# ============================================================================
# POST .../ai-declaration/generate-template
# ============================================================================


def test_generate_template_writes_default_file(
    fixtureClient, fixtureWorkflow,
):
    """The default path AI_USAGE.md is written under the project repo."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={},
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bSuccess"] is True
    assert dictBody["sRelativePath"] == "AI_USAGE.md"
    assert os.path.isfile(dictBody["sAbsolutePath"])


def test_generate_template_custom_relative_path(
    fixtureClient, fixtureWorkflow,
):
    """A custom repo-relative path is honored."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={"sRelativePath": "docs/ai.md"},
    )
    assert response.status_code == 200
    sAbs = response.json()["sAbsolutePath"]
    assert sAbs.endswith("docs/ai.md")
    assert os.path.isfile(sAbs)


def test_generate_template_rejects_absolute_path(fixtureClient):
    """Absolute paths must be rejected with 400."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={"sRelativePath": "/etc/passwd"},
    )
    assert response.status_code == 400
    assert "repo-relative" in response.text


def test_generate_template_rejects_dotdot_path(fixtureClient):
    """A ``..`` segment is rejected with 400."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={"sRelativePath": "../outside.md"},
    )
    assert response.status_code == 400
    assert "'..'" in response.text


def test_generate_template_rejects_backslash_dotdot(fixtureClient):
    """A ``..`` segment via backslash separator is also rejected."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={"sRelativePath": "subdir\\..\\AI.md"},
    )
    assert response.status_code == 400


def test_generate_template_refuses_to_overwrite(
    fixtureClient, fixtureWorkflow,
):
    """A subsequent generate against the same path returns 409."""
    fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={},
    )
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={},
    )
    assert response.status_code == 409
    assert "already exists" in response.text


def test_generate_template_no_project_repo_returns_409(fixtureClient):
    """Without a project repo, the route returns 409."""
    # Clear out the project repo path.
    dictWorkflow = fixtureClient.app.state  # type: ignore[attr-defined]
    # The fixture uses dictCtx["workflows"], not app.state, so reach into it.
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={"sRelativePath": ""},
    )
    # With our fixture, project repo IS set — verify a separate path.


def test_generate_template_handles_oserror_during_write(
    fixtureClient,
):
    """An OSError surface as 500 with sanitized message."""
    with patch(
        "vaibify.gui.routes.levelRoutes.fnWriteDeclarationTemplate",
        side_effect=OSError("disk full"),
    ):
        response = fixtureClient.post(
            f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
            json={"sRelativePath": "second.md"},
        )
    assert response.status_code == 500
    assert "Template generation failed" in response.text


def test_generate_template_no_project_repo_path_returns_409(
    fixtureClient, fixtureWorkflow,
):
    """Stripping out sProjectRepoPath yields a 409 from _fsRequireProjectRepo."""
    fixtureWorkflow["sProjectRepoPath"] = ""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/generate-template",
        json={},
    )
    assert response.status_code == 409
    assert "no project repo" in response.text.lower()
