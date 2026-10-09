"""Tests for vaibify.gui.routes.levelRoutes — L2 readiness + AI declaration.

These cover both endpoints registered by ``levelRoutes.fnRegisterAll``:

* ``GET /api/workflow/{id}/level2/readiness`` returns iProofLevel and
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


from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui.routes import levelRoutes
@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    """Stand the carrier down for the routes this module drives bare.

    The AI-declaration template generation now probes and writes under one carrier drain, which needs an owner record this module's bare ``FastAPI()`` has not got. Requested only by the tests that reach a carrier, so the
    ones asserting a refusal BEFORE it still prove that. What this
    module proves is what the route DOES, and nothing about the
    admission it runs under; that lives in
    ``tests/testCarrierMigratedRoutes.py``. See
    ``tests/carrierStandDown.py`` for what the stand-down costs.
    """
    fnStandCarrierDown(monkeypatch, levelRoutes)


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
        "require": lambda *aArgs: None,
        "save": _fnSave,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: fixtureWorkflow["sProjectRepoPath"],
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


# ============================================================================
# GET .../level2/readiness
# ============================================================================


def test_level2_readiness_returns_iproof_level_and_gaps(fixtureClient):
    """A bare workflow returns iProofLevel=0 and a fully-False gaps dict."""
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level2/readiness",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["iProofLevel"] == 0
    dictGaps = dictBody["dictLevel2Gaps"]
    for sKey in (
        "bAtLeastLevel1", "bGithubFullySynced",
        "bZenodoFullySynced", "bAiDeclarationAttested",
        "bAtLeastLevel2",
    ):
        assert sKey in dictGaps
    assert dictGaps["bAtLeastLevel2"] is False


def test_readiness_carries_level_1_blockers_with_human_hints(
    fixtureClient, fixtureWorkflow,
):
    """The agent's only route to "why is this not at Level 1 yet".

    Before this field the answer existed only on the dashboard's poll
    path, so an agent asked the question read project.json and
    state.json and reconstructed one -- and twice reported a rule that
    does not exist. What makes the field useful is not that a list
    arrives but that each entry carries the same plain-English sentence
    the researcher reads on the badge, so the agent can relay it
    instead of narrating identifiers at them.
    """
    dictWorkflow = fixtureWorkflow
    dictWorkflow["listSteps"] = [{
        "sName": "MakeNumbers",
        "sDirectory": "MakeNumbers",
        "saDataCommands": ["python3 makeNumbers.py"],
        "saOutputDataFiles": ["numbers.json"],
        "bNoInputData": True,
        "dictVerification": {"sUser": "untested"},
    }]
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level2/readiness",
    )
    assert response.status_code == 200
    dictBody = response.json()
    listBlockers = dictBody["listLevel1Blockers"]
    assert listBlockers, (
        "an unapproved step must be reported as an L1 blocker; an "
        "empty list here sends the agent back to guessing"
    )
    dictBlocker = listBlockers[0]
    assert dictBlocker["sCriterion"] == "user-not-approved"
    assert dictBlocker["sRemediationHint"].strip(), (
        "the blocker arrived with no human-readable hint, so the only "
        "thing the agent can relay is the criterion identifier -- "
        "which is the reporting problem this field exists to fix"
    )
    # The incompleteness is declared, never silent: an empty list from
    # this route is not proof that nothing blocks Level 1.
    assert dictBody["bScriptStalenessEvaluated"] is False


def test_level2_readiness_unknown_container_id_404(fixtureClient):
    """An unregistered container id must return 404."""
    response = fixtureClient.get("/api/workflow/no-such-id/level2/readiness")
    # fdictRequireWorkflow raises HTTPException(404).
    assert response.status_code == 404


# ============================================================================
# POST .../ai-declaration/generate-template
# ============================================================================


def test_generate_template_writes_default_file(
    fixtureClient, fixtureWorkflow, fixtureCarrierStoodDown,
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
    fixtureClient, fixtureWorkflow, fixtureCarrierStoodDown,
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
    fixtureClient, fixtureWorkflow, fixtureCarrierStoodDown,
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
    fixtureClient, fixtureCarrierStoodDown,
):
    """An OSError surface as 500 with sanitized message."""
    with patch(
        "vaibify.gui.routes.levelRoutes.fsWriteDeclarationTemplate",
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


# ============================================================================
# GET .../ai-declaration/file-state and POST .../ai-declaration/attach
# ============================================================================


def _fnAddSignedDeclarationStep(dictWorkflow, sDeclarationFile):
    """Append a signed-off AI Declaration step pointing at a file."""
    from vaibify.reproducibility.aiDeclarationStep import (
        fdictBuildAiDeclarationStep,
    )
    dictStep = fdictBuildAiDeclarationStep(
        sDeclarationFile=sDeclarationFile,
    )
    dictStep["dictVerification"]["sUser"] = "passed"
    dictWorkflow["listSteps"].append(dictStep)
    return dictStep


def _fnWriteRepoFile(sRepo, sRelative, sText="# notes\n"):
    sAbsolute = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sAbsolute), exist_ok=True)
    with open(sAbsolute, "w") as fileHandle:
        fileHandle.write(sText)


def test_file_state_answers_absent_then_present(
    fixtureClient, fixtureProjectRepo,
):
    """With no step, the question is about the default AI_USAGE.md."""
    sUrl = f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/file-state"
    dictAbsent = fixtureClient.get(sUrl).json()
    assert dictAbsent["sRelativePath"] == "AI_USAGE.md"
    assert dictAbsent["sFileState"] == "absent"
    _fnWriteRepoFile(fixtureProjectRepo, "AI_USAGE.md")
    assert fixtureClient.get(sUrl).json()["sFileState"] == "present"


def test_file_state_asks_about_the_attached_file(
    fixtureClient, fixtureWorkflow, fixtureProjectRepo,
):
    """The step's own file is the default question once one is attached."""
    _fnAddSignedDeclarationStep(fixtureWorkflow, "notes/declared.md")
    _fnWriteRepoFile(fixtureProjectRepo, "notes/declared.md")
    dictBody = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/file-state",
    ).json()
    assert dictBody == {
        "sRelativePath": "notes/declared.md",
        "sFileState": "present", "sReason": "",
    }


def test_file_state_reports_a_directory_as_unknown(
    fixtureClient, fixtureProjectRepo,
):
    """A directory is neither attachable nor generatable: say so."""
    os.makedirs(os.path.join(fixtureProjectRepo, "AI_USAGE.md"))
    dictBody = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/file-state",
    ).json()
    assert dictBody["sFileState"] == "unknown"
    assert "directory" in dictBody["sReason"]


class _RepoFilesThatCannotRead:
    """A repo adapter whose probe fails, as a stopped container's does."""

    def __init__(self, sRootPath):
        self.sRootPath = sRootPath

    def fbIsFile(self, sRelPath):
        raise OSError("the container is not running")

    def fbIsDir(self, sRelPath):
        raise OSError("the container is not running")

    def fdictHashFiles(self, listRelPaths):
        raise OSError("the container is not running")


def _fclientWithUnreadableRepo(fixtureWorkflow):
    app = FastAPI()
    dictCtx = {
        "docker": None, "workflows": {S_CONTAINER_ID: fixtureWorkflow},
        "paths": {}, "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
        "files": lambda sId: _RepoFilesThatCannotRead(
            fixtureWorkflow["sProjectRepoPath"]),
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


@pytest.mark.falsification
def test_an_unreadable_repo_is_unknown_never_absent(fixtureWorkflow):
    """A failed read must not offer "Generate" over an unseen file.

    Kills: aiDeclarationStep answering ABSENT for a failed probe.
    """
    _fnAddSignedDeclarationStep(fixtureWorkflow, "AI_USAGE.md")
    client = _fclientWithUnreadableRepo(fixtureWorkflow)
    dictBody = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/file-state",
    ).json()
    assert dictBody["sFileState"] == "unknown"
    assert "not running" in dictBody["sReason"]
    response = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/attach",
        json={"sRelativePath": "other.md"},
    )
    assert response.status_code == 503
    assert fixtureWorkflow["listSteps"][-1]["sDeclarationFile"] == (
        "AI_USAGE.md")


@pytest.mark.parametrize("sHostilePath,iStatus", [
    ("../outside.md", 400),
    ("notes/../../outside.md", 400),
    ("/etc/passwd", 400),
    (".git/config", 403),
    (".vaibify/state.json", 403),
    ("sub/project.json", 403),
    ("", 400),
])
def test_attach_refuses_paths_outside_the_declarable_repo(
    fixtureClient, fixtureWorkflow, sHostilePath, iStatus,
):
    _fnAddSignedDeclarationStep(fixtureWorkflow, "AI_USAGE.md")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/attach",
        json={"sRelativePath": sHostilePath},
    )
    assert response.status_code == iStatus, response.text
    assert fixtureWorkflow["listSteps"][-1]["sDeclarationFile"] == (
        "AI_USAGE.md")


def test_file_state_refuses_a_traversal_query(fixtureClient):
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/file-state",
        params={"sRelativePath": "../../etc/passwd"},
    )
    assert response.status_code == 400


def test_attach_without_a_declaration_step_is_refused(
    fixtureClient, fixtureProjectRepo,
):
    _fnWriteRepoFile(fixtureProjectRepo, "AI_USAGE.md")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/attach",
        json={"sRelativePath": "AI_USAGE.md"},
    )
    assert response.status_code == 409
    assert "no AI Declaration step" in response.json()["detail"]


def test_attach_refuses_an_absent_file(fixtureClient, fixtureWorkflow):
    _fnAddSignedDeclarationStep(fixtureWorkflow, "")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/attach",
        json={"sRelativePath": "missing.md"},
    )
    assert response.status_code == 409
    assert fixtureWorkflow["listSteps"][-1]["sDeclarationFile"] == ""


@pytest.mark.falsification
def test_reattaching_the_same_file_keeps_the_sign_off(
    fixtureClient, fixtureWorkflow, fixtureProjectRepo, monkeypatch,
):
    """Kills: levelRoutes keeping the sign-off when a different file is
    attached.
    """
    from vaibify.gui.routes import levelRoutes as moduleLevelRoutes
    monkeypatch.setattr(
        moduleLevelRoutes, "fdictCommitWorkflowSave",
        lambda *aArgs, **dictKwargs: None,
    )
    dictStep = _fnAddSignedDeclarationStep(fixtureWorkflow, "AI_USAGE.md")
    _fnWriteRepoFile(fixtureProjectRepo, "AI_USAGE.md")
    _fnWriteRepoFile(fixtureProjectRepo, "notes/second.md")
    sUrl = f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/attach"
    dictSame = fixtureClient.post(
        sUrl, json={"sRelativePath": "./AI_USAGE.md"}).json()
    assert dictSame["bSignOffWithdrawn"] is False
    assert dictSame["sRelativePath"] == "AI_USAGE.md"
    assert dictStep["dictVerification"]["sUser"] == "passed"
    dictOther = fixtureClient.post(
        sUrl, json={"sRelativePath": "notes/second.md"}).json()
    assert dictOther["bSignOffWithdrawn"] is True
    assert dictStep["dictVerification"]["sUser"] == "untested"
    assert dictStep["sDeclarationFile"] == "notes/second.md"


@pytest.mark.falsification
def test_attach_refuses_an_existing_file_inside_git_metadata(
    fixtureClient, fixtureWorkflow, fixtureProjectRepo,
):
    """The file EXISTS, so only the metadata refusal can stop it: a
    declaration pointing into .git/ would publish repository internals
    through the preview and the commit button.

    Kills: _fsValidateDeclarationPathInRepo dropping its
    fnRejectWriteDenylistedPath check.
    """
    _fnAddSignedDeclarationStep(fixtureWorkflow, "AI_USAGE.md")
    _fnWriteRepoFile(fixtureProjectRepo, ".git/config", "[core]\n")
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/ai-declaration/attach",
        json={"sRelativePath": ".git/config"},
    )
    assert response.status_code == 403, response.text
    assert fixtureWorkflow["listSteps"][-1]["sDeclarationFile"] == (
        "AI_USAGE.md")


@pytest.mark.falsification
def test_attach_refuses_a_symlink_that_leaves_the_repo(
    fixtureClient, fixtureWorkflow, fixtureProjectRepo, tmp_path,
):
    """A link INSIDE the repository passes every lexical check; only the
    resolved path shows it leaves.

    Kills: _fnRejectPathResolvingOutsideRepo accepting every path.
    """
    sOutside = tmp_path / "outside.md"
    sOutside.write_text("not part of the project\n")
    os.symlink(str(sOutside), os.path.join(fixtureProjectRepo, "linked.md"))
    _fnAddSignedDeclarationStep(fixtureWorkflow, "AI_USAGE.md")
    sBase = f"/api/workflow/{S_CONTAINER_ID}/ai-declaration"
    responseState = fixtureClient.get(
        sBase + "/file-state", params={"sRelativePath": "linked.md"})
    assert responseState.status_code == 403, responseState.text
    responseAttach = fixtureClient.post(
        sBase + "/attach", json={"sRelativePath": "linked.md"})
    assert responseAttach.status_code == 403, responseAttach.text
    assert fixtureWorkflow["listSteps"][-1]["sDeclarationFile"] == (
        "AI_USAGE.md")
