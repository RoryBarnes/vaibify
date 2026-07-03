"""Tests for project-repo auto-detection and badge-state integration.

Covers two related concerns:

1. ``containerGit.fsDetectProjectRepoInContainer`` — unit behavior of
   the detector that locates the git work tree enclosing an active
   workflow's ``workflow.json``.
2. The badges route end-to-end — when ``sProjectRepoPath`` is
   populated, ``/api/git/{id}/badges`` hydrates real state; when it
   is empty, the route returns the explicit "not in a git
   repository" payload rather than silently reporting ``bIsRepo:
   False`` against the wrong root.
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.gui import containerGit
from vaibify.gui.routes import gitRoutes


# ----------------------------------------------------------------------
# fsDetectProjectRepoInContainer — unit level
# ----------------------------------------------------------------------


class _FakeDocker:
    """Minimal docker stub that serves canned (rc, stdout) by marker."""

    def __init__(self, listRules=None):
        self.listCommands = []
        self._listRules = listRules or []

    def ftResultExecuteCommand(self, sContainerId, sCommand, **_kwargs):
        self.listCommands.append(sCommand)
        for sMarker, iRc, sOut in self._listRules:
            if sMarker in sCommand:
                return (iRc, sOut)
        return (0, "")


def test_detect_returns_repo_path_for_workflow_in_repo():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 0, "/workspace/DemoRepo\n"),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid",
        "/workspace/DemoRepo/.vaibify/workflows/demo.json",
    )
    assert sResult == "/workspace/DemoRepo"


def test_detect_returns_empty_when_not_in_repo():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 128, ""),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid", "/workspace/workflow.json",
    )
    assert sResult == ""


# ----------------------------------------------------------------------
# gitRoutes /api/git/{id}/badges — scoped by sProjectRepoPath
# ----------------------------------------------------------------------


def _fdictBuildRoutesApp(mockDocker, dictWorkflow):
    """Build a minimal FastAPI app wired to gitRoutes handlers."""
    app = FastAPI()
    dictCtx = {
        "require": MagicMock(),
        "docker": mockDocker,
        "workflows": {"cid-demo": dictWorkflow},
    }
    gitRoutes.fnRegisterAll(app, dictCtx)
    return app


def test_badges_returns_not_a_repo_when_project_repo_missing():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "",
    }
    mockDocker = MagicMock()
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/badges")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictGit"]["bIsRepo"] is False
    assert "not in a git repository" in dictBody["dictGit"]["sReason"]
    assert dictBody["dictBadges"] == {}
    assert dictBody["listTracked"] == []


def test_badges_hydrates_when_project_repo_is_set():
    sRepo = "/workspace/DemoRepo"
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": "analysis",
            "saPlotCommands": [],
            "saPlotFiles": ["output.pdf"],
        }],
        "sProjectRepoPath": sRepo,
        "dictSyncStatus": {},
    }

    sPorcelain = "# branch.head main\n# branch.ab +0 -0\n"
    sCombinedStatus = (
        "true\n__VAIBIFY_HEAD__\n" + "b" * 40 + "\n"
        "__VAIBIFY_STATUS__\n" + sPorcelain
    )
    sTrackedJson = json.dumps([
        ".vaibify/workflows/demo.json",
    ]) + "\n"
    sHashesJson = json.dumps({
        ".vaibify/workflows/demo.json": "a" * 40,
    }) + "\n"

    def _fExec(sContainerId, sCommand, **_kw):
        if "status --porcelain=v2" in sCommand:
            return (0, sCombinedStatus)
        if "python3 -c" in sCommand and "glob" in sCommand:
            return (0, sTrackedJson)
        if "python3 -c" in sCommand and "hashlib" in sCommand:
            return (0, sHashesJson)
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec

    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/badges")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictGit"]["bIsRepo"] is True
    assert dictBody["dictGit"]["sBranch"] == "main"
    assert ".vaibify/workflows/demo.json" in dictBody["listTracked"]

    # Every containerGit call ran against the project repo, not /workspace.
    listContainerGitCalls = [
        c for c in mockDocker.ftResultExecuteCommand.call_args_list
        if "git" in str(c) or "python3 -c" in str(c)
    ]
    assert listContainerGitCalls, "expected at least one containerGit call"
    for call in listContainerGitCalls:
        sCmd = call.args[1]
        # No /workspace/... path should appear bare — only the
        # project-repo subdirectory should be used as workspace.
        assert "cd /workspace\n" not in sCmd
        assert "cd /workspace &&" not in sCmd


def test_status_returns_empty_status_when_project_repo_missing():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "",
    }
    mockDocker = MagicMock()
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/status")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bIsRepo"] is False
    assert "not in a git repository" in dictBody["sReason"]


def test_commit_canonical_409_when_project_repo_missing():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "",
    }
    mockDocker = MagicMock()
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/commit-canonical",
        json={"sCommitMessage": ""},
    )

    assert response.status_code == 409
    assert "Project repo not detected" in response.json()["detail"]


def test_commit_canonical_restricts_commit_to_curated_pathspec():
    """Pre-staged user file (data/evil.py) does NOT end up in the commit."""
    sRepo = "/workspace/DemoRepo"
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": sRepo,
        "dictSyncStatus": {},
    }
    # Porcelain v2 reports two index-only entries: the canonical
    # workflow file (uncommitted, will be included) and an attacker-
    # staged user file (must NOT be included in the curated commit).
    sH = "a" * 40
    sI = "b" * 40
    sPorcelain = (
        "# branch.head main\n"
        f"1 A. N... 000000 100644 100644 {sH} {sI} "
        f".vaibify/workflows/demo.json\n"
        f"1 A. N... 000000 100644 100644 {sH} {sI} "
        f"data/evil.py\n"
    )
    sCombinedStatus = (
        "true\n__VAIBIFY_HEAD__\n" + "b" * 40 + "\n"
        "__VAIBIFY_STATUS__\n" + sPorcelain
    )
    sTrackedJson = json.dumps([
        ".vaibify/workflows/demo.json",
    ]) + "\n"
    listIssued = []

    def _fExec(sContainerId, sCommand, **_kw):
        listIssued.append(sCommand)
        if "status --porcelain=v2" in sCommand:
            return (0, sCombinedStatus)
        if "python3 -c" in sCommand and "glob" in sCommand:
            return (0, sTrackedJson)
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/commit-canonical",
        json={"sCommitMessage": "canonical"},
    )

    assert response.status_code == 200
    listCommitCmds = [
        s for s in listIssued
        if "commit -m" in s
    ]
    assert listCommitCmds, "commit command should have been issued"
    for sCmd in listCommitCmds:
        assert "data/evil.py" not in sCmd, (
            "User-staged file must not appear in canonical commit "
            "pathspec"
        )
        assert " -- " in sCmd


def test_commit_canonical_listOnlyPaths_narrows_never_widens():
    """FALSIFICATION TARGET: the declaration button's scoped commit.
    ``listOnlyPaths`` must restrict the commit to the requested
    subset of the server-derived canonical needs-commit list, and a
    requested path OUTSIDE that list must be ignored — the filter
    can narrow but never widen."""
    sRepo = "/workspace/DemoRepo"
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": sRepo,
        "dictSyncStatus": {},
    }
    sH = "a" * 40
    sI = "b" * 40
    sPorcelain = (
        "# branch.head main\n"
        f"1 A. N... 000000 100644 100644 {sH} {sI} AI_USAGE.md\n"
        f"1 A. N... 000000 100644 100644 {sH} {sI} "
        f".vaibify/workflows/demo.json\n"
    )
    sCombinedStatus = (
        "true\n__VAIBIFY_HEAD__\n" + "b" * 40 + "\n"
        "__VAIBIFY_STATUS__\n" + sPorcelain
    )
    sTrackedJson = json.dumps([
        "AI_USAGE.md", ".vaibify/workflows/demo.json",
    ]) + "\n"
    listIssued = []

    def _fExec(sContainerId, sCommand, **_kw):
        listIssued.append(sCommand)
        if "status --porcelain=v2" in sCommand:
            return (0, sCombinedStatus)
        if "python3 -c" in sCommand and "glob" in sCommand:
            return (0, sTrackedJson)
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/commit-canonical",
        json={
            "sCommitMessage": "declaration",
            "listOnlyPaths": ["AI_USAGE.md", "data/evil.py"],
        },
    )

    assert response.status_code == 200
    assert response.json()["iFilesCommitted"] == 1
    listCommitCmds = [s for s in listIssued if "commit -m" in s]
    assert listCommitCmds, "commit command should have been issued"
    for sCmd in listCommitCmds:
        assert "AI_USAGE.md" in sCmd
        assert "demo.json" not in sCmd, (
            "listOnlyPaths must exclude canonical files not requested"
        )
        assert "data/evil.py" not in sCmd, (
            "a requested path outside the canonical list must be "
            "ignored"
        )


# ----------------------------------------------------------------------
# gitRoutes /api/git/{id}/untrack-ai-declaration — declaration-only
# ----------------------------------------------------------------------


def _fdictBuildDeclarationWorkflow(sRepo):
    """Workflow with one ai-declaration step declaring AI_USAGE.md."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "AI Declaration",
                "sStepKind": "ai-declaration",
                "sDirectory": "aiDeclaration",
                "sDeclarationFile": "aiDeclaration/AI_USAGE.md",
            },
        ],
        "sProjectRepoPath": sRepo,
        "dictSyncStatus": {},
    }


def test_untrack_ai_declaration_refuses_non_declaration_path():
    """FALSIFICATION TARGET: the endpoint is scoped to declaration
    files. Any other path — canonical or not — must be refused, or
    the route becomes a general-purpose git rm an agent could aim at
    workflow.json or user data."""
    dictWorkflow = _fdictBuildDeclarationWorkflow("/workspace/DemoRepo")
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "data/results.json"},
    )

    assert response.status_code == 403
    listIssued = [
        tCall.args[1]
        for tCall in mockDocker.ftResultExecuteCommand.call_args_list
    ]
    assert not any("rm --cached" in s for s in listIssued), (
        "no git rm may be issued for a refused path"
    )


def test_untrack_ai_declaration_removes_only_the_declaration():
    """Happy path: git rm --cached plus a commit scoped to the
    declaration pathspec, inside the project repo."""
    sRepo = "/workspace/DemoRepo"
    dictWorkflow = _fdictBuildDeclarationWorkflow(sRepo)
    listIssued = []

    def _fExec(sContainerId, sCommand, **_kw):
        listIssued.append(sCommand)
        if "rev-parse HEAD" in sCommand:
            return (0, "c" * 40 + "\n")
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "aiDeclaration/AI_USAGE.md"},
    )

    assert response.status_code == 200
    assert response.json()["bSuccess"] is True
    listRemoveCmds = [s for s in listIssued if "rm --cached" in s]
    assert listRemoveCmds, "git rm --cached should have been issued"
    for sCmd in listRemoveCmds:
        assert "aiDeclaration/AI_USAGE.md" in sCmd
        assert sCmd.startswith("cd " + sRepo), (
            "removal must run inside the project repo"
        )
    listCommitCmds = [s for s in listIssued if "commit -m" in s]
    assert listCommitCmds, "the removal must be committed"
    for sCmd in listCommitCmds:
        assert " -- " in sCmd
        assert "aiDeclaration/AI_USAGE.md" in sCmd


def test_untrack_ai_declaration_surfaces_git_failure():
    """A failed git rm (e.g. the file is not tracked) must surface as
    an error, never a silent success — the dashboard is ground truth."""
    dictWorkflow = _fdictBuildDeclarationWorkflow("/workspace/DemoRepo")

    def _fExec(sContainerId, sCommand, **_kw):
        if "rm --cached" in sCommand:
            return (128, "fatal: pathspec did not match any files")
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "aiDeclaration/AI_USAGE.md"},
    )

    assert response.status_code == 409
    assert "rm --cached failed" in response.json()["detail"]


def test_untrack_ai_declaration_rejects_pathspec_magic():
    """FALSIFICATION TARGET (security, 2026-07-02): git treats a
    ``:``-prefixed pathspec as magic — ``:(glob)**`` matches every
    tracked file — and the membership check alone cannot catch it
    because a hostile workflow.json can declare the magic string as
    its own sDeclarationFile. The route must refuse any ``:``-
    prefixed path even when it IS the declared declaration file."""
    dictWorkflow = _fdictBuildDeclarationWorkflow("/workspace/DemoRepo")
    dictWorkflow["listSteps"][0]["sDeclarationFile"] = ":(glob)**"
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": ":(glob)**"},
    )

    assert response.status_code == 403
    listIssued = [
        tCall.args[1]
        for tCall in mockDocker.ftResultExecuteCommand.call_args_list
    ]
    assert not any("rm --cached" in s for s in listIssued)


def test_untrack_git_command_disables_pathspec_magic():
    """Second wall for the same attack: even if a magic path slipped
    past the route filter, the container command must run with
    GIT_LITERAL_PATHSPECS=1 so git matches the string literally."""
    listIssued = []

    def _fExec(sContainerId, sCommand, **_kw):
        listIssued.append(sCommand)
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    containerGit.ftResultGitRemoveCachedInContainer(
        mockDocker, "cid", ["AI_USAGE.md"],
        sWorkspace="/workspace/DemoRepo",
    )
    assert listIssued, "command should have been issued"
    assert "GIT_LITERAL_PATHSPECS=1" in listIssued[0]
    assert listIssued[0].index("GIT_LITERAL_PATHSPECS=1") < (
        listIssued[0].index("rm --cached")
    )


def test_untrack_ai_declaration_ignores_other_steps_files():
    """Guard hardening (review 2026-07-02): with TWO declaration
    steps, untracking one file must never sweep the other into the
    git command — kills a mutant that passes listDeclared instead of
    the single requested path."""
    sRepo = "/workspace/DemoRepo"
    dictWorkflow = _fdictBuildDeclarationWorkflow(sRepo)
    dictWorkflow["listSteps"].append({
        "sName": "AI Declaration B",
        "sStepKind": "ai-declaration",
        "sDirectory": "aiDeclarationB",
        "sDeclarationFile": "aiDeclarationB/AI_USAGE_B.md",
    })
    listIssued = []

    def _fExec(sContainerId, sCommand, **_kw):
        listIssued.append(sCommand)
        if "rev-parse HEAD" in sCommand:
            return (0, "c" * 40 + "\n")
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "aiDeclarationB/AI_USAGE_B.md"},
    )

    assert response.status_code == 200
    listGitCmds = [
        s for s in listIssued
        if "rm --cached" in s or "commit -m" in s
    ]
    assert listGitCmds
    for sCmd in listGitCmds:
        assert "aiDeclarationB/AI_USAGE_B.md" in sCmd
        assert "aiDeclaration/AI_USAGE.md" not in sCmd, (
            "only the requested declaration may be touched"
        )


def test_untrack_ai_declaration_surfaces_commit_failure():
    """The rm-succeeded-but-commit-failed branch must return 500 with
    the git message, never a silent success (review 2026-07-02)."""
    dictWorkflow = _fdictBuildDeclarationWorkflow("/workspace/DemoRepo")

    def _fExec(sContainerId, sCommand, **_kw):
        if "rm --cached" in sCommand:
            return (0, "")
        if "commit -m" in sCommand:
            return (1, "nothing to commit, working tree clean")
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "aiDeclaration/AI_USAGE.md"},
    )

    assert response.status_code == 500
    assert "git commit failed" in response.json()["detail"]
