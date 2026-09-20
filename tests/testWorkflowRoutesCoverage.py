"""Tests for uncovered lines in vaibify.gui.routes.workflowRoutes."""

import pytest
from types import SimpleNamespace
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

    def _fresponseSearchFailingWith(self, error):
        app = FastAPI()
        dictCtx = {"docker": MagicMock(), "require": MagicMock()}
        with patch(
            "vaibify.gui.routes.workflowRoutes.workflowManager"
        ) as mockWm:
            mockWm.flistFindWorkflowsInContainer.side_effect = error
            fnRegisterAll(app, dictCtx)
            return TestClient(app).get("/api/workflows/cid1")

    def test_a_daemon_answer_that_the_container_is_not_running_is_a_409(self):
        """The daemon's 409 'is not running' names a stopped container,
        and the remedy is to start it."""
        error = RuntimeError(
            "409 Client Error: Conflict (container cid1 is not running)",
        )
        error.response = SimpleNamespace(status_code=409)
        response = self._fresponseSearchFailingWith(error)
        assert response.status_code == 409
        assert "Start it" in response.json()["detail"]

    @pytest.mark.falsification
    def test_a_name_conflict_is_not_read_as_a_stopped_container(self):
        """A create-time name conflict is also a 409 Conflict. Reading
        '409' and 'conflict' in the prose as 'stopped' sent a researcher
        to start a container that was already running; the classifier
        reads the daemon's status code and its 'not running' answer.

        Kills: classifying by the words '409' and 'conflict' in the
        message again, under which this name conflict is a 409 'not
        running'.
        """
        error = RuntimeError(
            "409 Client Error: Conflict (Conflict. The container name "
            "\"/cid1\" is already in use)",
        )
        error.response = SimpleNamespace(status_code=409)
        response = self._fresponseSearchFailingWith(error)
        assert response.status_code == 500
        assert "Search failed" in response.json()["detail"]

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


class TestWorkflowCreationRequestRoute:
    """POST /api/workflows/{id}/request-creation — the agent's
    create-project action records a request for the researcher; it
    must never create a project itself."""

    def _ftBuildClientAndCtx(self):
        app = FastAPI()
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
            "dictProjectCreationRequests": {},
        }
        fnRegisterAll(app, dictCtx)
        return TestClient(app), dictCtx

    def test_request_is_recorded_not_created(self):
        client, dictCtx = self._ftBuildClientAndCtx()
        response = client.post(
            "/api/workflows/cid1/request-creation",
            json={"sWorkflowName": "  Waste Heat  ",
                  "sRepoDirectory": "waste_heat"},
        )
        assert response.status_code == 200
        dictBody = response.json()
        assert dictBody["bCreated"] is False
        assert "researcher-only" in dictBody["sMessage"]
        assert dictCtx["dictProjectCreationRequests"]["cid1"] == {
            "sSuggestedName": "Waste Heat",
            "sSuggestedDirectory": "waste_heat",
        }
        dictCtx["docker"].fnWriteFile.assert_not_called()

    def test_request_truncates_suggestions_to_two_hundred(self):
        """Suggestion strings are capped at exactly 200 characters so
        an agent cannot bloat the in-memory request store."""
        client, dictCtx = self._ftBuildClientAndCtx()
        response = client.post(
            "/api/workflows/cid1/request-creation",
            json={"sWorkflowName": "n" * 250,
                  "sRepoDirectory": "d" * 250},
        )
        assert response.status_code == 200
        dictStored = dictCtx["dictProjectCreationRequests"]["cid1"]
        assert len(dictStored["sSuggestedName"]) == 200
        assert len(dictStored["sSuggestedDirectory"]) == 200

    def test_request_accepts_empty_suggestions(self):
        client, dictCtx = self._ftBuildClientAndCtx()
        response = client.post(
            "/api/workflows/cid1/request-creation", json={},
        )
        assert response.status_code == 200
        assert dictCtx["dictProjectCreationRequests"]["cid1"] == {
            "sSuggestedName": "",
            "sSuggestedDirectory": "",
        }


# The former /api/repos/{id} repo-list route has moved to
# repoRoutes.py as /api/repos/{id}/status.  Tests for the new
# endpoint live in testRepoRoutes.py.
