"""Tests for uncovered lines in vaibify.gui.routes.workflowRoutes."""

import pytest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from vaibify.gui.routes.workflowRoutes import (
    _fnRejectDuplicateWorkflowName,
    _fsValidateRepoDirectory,
    fnRegisterAll,
)


# ── Line 29: _fnRejectDuplicateWorkflowName raises 409 ──────────

class TestFnRejectDuplicateWorkflowName:
    def test_duplicate_name_raises_409(self):
        """Cover line 29: HTTPException(409)."""
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ) as mockWm:
            mockWm.flistFindWorkflowsInContainer.return_value = [
                {
                    "sName": "myWorkflow",
                    "sPath": "/workspace/.vaibify/wf.json",
                },
            ]
            with pytest.raises(HTTPException) as excInfo:
                _fnRejectDuplicateWorkflowName(
                    mockDocker, "cid1", "myWorkflow"
                )
            assert excInfo.value.status_code == 409

    def test_no_duplicate_passes(self):
        """No exception when name is unique."""
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ) as mockWm:
            mockWm.flistFindWorkflowsInContainer.return_value = [
                {
                    "sName": "otherWorkflow",
                    "sPath": "/workspace/.vaibify/other.json",
                },
            ]
            _fnRejectDuplicateWorkflowName(
                mockDocker, "cid1", "myWorkflow"
            )


# ── Lines 42, 46: _fsValidateRepoDirectory error branches ───────

class TestFsValidateRepoDirectory:
    def test_empty_directory_raises_400(self):
        """Cover line 42: empty sRepoDirectory."""
        mockDocker = MagicMock()
        with pytest.raises(HTTPException) as excInfo:
            _fsValidateRepoDirectory(
                mockDocker, "cid1", "   "
            )
        assert excInfo.value.status_code == 400
        assert "required" in excInfo.value.detail

    def test_dotdot_raises_400(self):
        """Cover line 46: path traversal attempt."""
        mockDocker = MagicMock()
        with pytest.raises(HTTPException) as excInfo:
            _fsValidateRepoDirectory(
                mockDocker, "cid1", "repo/../etc"
            )
        assert excInfo.value.status_code == 400
        assert ".." in excInfo.value.detail

    def test_directory_not_found_raises_404(self):
        """Cover line 55: directory does not exist."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (1, "")
        with pytest.raises(HTTPException) as excInfo:
            _fsValidateRepoDirectory(
                mockDocker, "cid1", "nonexistent"
            )
        assert excInfo.value.status_code == 404

    def test_valid_directory_returns_full_path(self):
        """Happy path."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "")
        sResult = _fsValidateRepoDirectory(
            mockDocker, "cid1", "myrepo"
        )
        assert sResult == "/workspace/myrepo"


# ── Lines 72-73: _fnRegisterWorkflowSearch exception branch ─────

class TestWorkflowSearchRoute:
    def test_search_exception_raises_500(self):
        """Cover lines 72-73: exception in search."""
        app = FastAPI()
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
        }
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ) as mockWm, patch(
            "vaibify.gui.routes.workflowRoutes"
            "._fsSanitizeServerError",
            return_value="sanitized error",
        ):
            mockWm.flistFindWorkflowsInContainer.side_effect = (
                RuntimeError("docker error")
            )
            fnRegisterAll(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/workflows/cid1")
            assert response.status_code == 500

    def test_search_success(self):
        """Happy path."""
        app = FastAPI()
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
        }
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ) as mockWm:
            mockWm.flistFindWorkflowsInContainer.return_value = [
                {"sName": "wf1", "sPath": "/workspace/.vaibify/wf1.json"}
            ]
            fnRegisterAll(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/workflows/cid1")
            assert response.status_code == 200


# ── Lines 83-99: _fnRegisterRepoList route ──────────────────────

class TestRepoListRoute:
    def test_repo_list_success(self):
        """Cover lines 83-99: successful listing."""
        app = FastAPI()
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            0, "repoA\nrepoB\n"
        )
        dictCtx = {
            "docker": mockDocker,
            "require": MagicMock(),
        }
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ), patch(
            "vaibify.gui.routes.workflowRoutes"
            ".fdictHandleConnect",
            return_value={},
        ):
            fnRegisterAll(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/repos/cid1")
            assert response.status_code == 200
            dictResult = response.json()
            assert dictResult["listRepos"] == ["repoA", "repoB"]

    def test_repo_list_failure_raises_500(self):
        """Cover lines 93-94: non-zero exit code."""
        app = FastAPI()
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            1, "error"
        )
        dictCtx = {
            "docker": mockDocker,
            "require": MagicMock(),
        }
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ), patch(
            "vaibify.gui.routes.workflowRoutes"
            ".fdictHandleConnect",
            return_value={},
        ):
            fnRegisterAll(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/repos/cid1")
            assert response.status_code == 500

    def test_repo_list_filters_empty_lines(self):
        """Verify blank lines are filtered out."""
        app = FastAPI()
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            0, "repo1\n\n  \nrepo2\n"
        )
        dictCtx = {
            "docker": mockDocker,
            "require": MagicMock(),
        }
        with patch(
            "vaibify.gui.routes.workflowRoutes"
            ".workflowManager"
        ), patch(
            "vaibify.gui.routes.workflowRoutes"
            ".fdictHandleConnect",
            return_value={},
        ):
            fnRegisterAll(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/repos/cid1")
            assert response.json()["listRepos"] == [
                "repo1", "repo2"
            ]
