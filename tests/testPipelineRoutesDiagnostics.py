"""Tests for the in-container diagnosability routes (R3).

Two new GET endpoints let an in-container Claude self-diagnose a dead
pipeline run without leaving the container:

- ``GET /api/pipeline/{id}/host-log-tail`` returns lines from
  ``~/.vaibify/vaibify.log`` filtered to the container.
- ``GET /api/pipeline/{id}/state`` is repurposed as the
  ``get-pipeline-state`` agent action (already existed for the
  dashboard; this test guards the agent-action decoration).
"""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from vaibify.gui import hostIncidents
from vaibify.gui.routes import pipelineRoutes


# -----------------------------------------------------------------------
# Pure helpers (no FastAPI fixture needed)
# -----------------------------------------------------------------------


def test_fiClampLineCount_default_under_max():
    iLines = pipelineRoutes._fiClampLineCount(200)
    assert iLines == 200


def test_fiClampLineCount_caps_at_max():
    iLines = pipelineRoutes._fiClampLineCount(10_000)
    assert iLines == pipelineRoutes.I_HOST_LOG_TAIL_MAX_LINES


def test_fiClampLineCount_floors_at_one():
    assert pipelineRoutes._fiClampLineCount(0) == 1
    assert pipelineRoutes._fiClampLineCount(-50) == 1


def test_fiClampLineCount_handles_non_int_input():
    """Defensive: route binds int, but treat invalid as default."""
    iLines = pipelineRoutes._fiClampLineCount("not-an-int")
    assert iLines == pipelineRoutes.I_HOST_LOG_TAIL_DEFAULT_LINES


def test_flistTailLogLinesForContainer_missing_file_returns_empty(tmp_path):
    sNotALog = tmp_path / "absent.log"
    listLines = pipelineRoutes._flistTailLogLinesForContainer(
        str(sNotALog), "cid-x", 100,
    )
    assert listLines == []


def test_flistTailLogLinesForContainer_filters_to_container(tmp_path):
    sLog = tmp_path / "vaibify.log"
    sLog.write_text(
        "line about cid-a: ok\n"
        "unrelated message\n"
        "another cid-a hit\n"
        "cid-b mention\n",
        encoding="utf-8",
    )
    listLines = pipelineRoutes._flistTailLogLinesForContainer(
        str(sLog), "cid-a", 100,
    )
    assert listLines == [
        "line about cid-a: ok",
        "another cid-a hit",
    ]


def test_flistTailLogLinesForContainer_caps_to_iLines(tmp_path):
    sLog = tmp_path / "vaibify.log"
    sLog.write_text(
        "\n".join(f"cid-z step {i}" for i in range(50)) + "\n",
        encoding="utf-8",
    )
    listLines = pipelineRoutes._flistTailLogLinesForContainer(
        str(sLog), "cid-z", 5,
    )
    assert len(listLines) == 5
    assert listLines[-1] == "cid-z step 49"
    assert listLines[0] == "cid-z step 45"


# -----------------------------------------------------------------------
# host-log-tail route via TestClient
# -----------------------------------------------------------------------


@pytest.fixture
def clientHttp():
    """A TestClient sharing the existing server-routes fixture pattern."""
    from fastapi.testclient import TestClient
    from vaibify.gui import pipelineServer
    from tests.testPipelineServerRoutes import _fmockCreateDocker
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", _fmockCreateDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


@pytest.fixture(autouse=True)
def fnClearIncidentRing():
    hostIncidents.fnResetHostIncidents()
    yield
    hostIncidents.fnResetHostIncidents()


def test_host_log_tail_returns_filtered_lines(clientHttp, tmp_path):
    sLog = tmp_path / "vaibify.log"
    sLog.write_text(
        "2026-06-16 INFO vaibify: connected to abc123container\n"
        "2026-06-16 INFO vaibify: unrelated noise\n"
        "2026-06-16 ERROR vaibify: died abc123container ws closed\n",
        encoding="utf-8",
    )
    with patch.object(
        pipelineRoutes, "_fsResolveHostLogPath", return_value=str(sLog),
    ):
        responseHttp = clientHttp.get(
            "/api/pipeline/abc123container/host-log-tail",
            params={"iLines": 50},
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["iRequestedLines"] == 50
    assert dictBody["iEffectiveLines"] == 50
    assert len(dictBody["listLines"]) == 2
    assert "abc123container" in dictBody["listLines"][0]


def test_host_log_tail_clamps_iLines_above_max(clientHttp, tmp_path):
    sLog = tmp_path / "vaibify.log"
    sLog.write_text("", encoding="utf-8")
    with patch.object(
        pipelineRoutes, "_fsResolveHostLogPath", return_value=str(sLog),
    ):
        responseHttp = clientHttp.get(
            "/api/pipeline/abc123container/host-log-tail",
            params={"iLines": 100_000},
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["iEffectiveLines"] == (
        pipelineRoutes.I_HOST_LOG_TAIL_MAX_LINES
    )


def test_host_log_tail_default_lines_when_omitted(clientHttp, tmp_path):
    sLog = tmp_path / "vaibify.log"
    sLog.write_text("", encoding="utf-8")
    with patch.object(
        pipelineRoutes, "_fsResolveHostLogPath", return_value=str(sLog),
    ):
        responseHttp = clientHttp.get(
            "/api/pipeline/abc123container/host-log-tail",
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["iEffectiveLines"] == (
        pipelineRoutes.I_HOST_LOG_TAIL_DEFAULT_LINES
    )


def test_host_log_tail_returns_incidents_for_container(clientHttp, tmp_path):
    sLog = tmp_path / "vaibify.log"
    sLog.write_text("", encoding="utf-8")
    hostIncidents.fnRecordHostIncident(
        "abc123container",
        {
            "sIso": "2026-06-16T01:00:00+00:00",
            "sLevel": "ERROR",
            "sLogger": "vaibify",
            "sMessage": "ws closed",
            "sExceptionRepr": "RuntimeError('boom')",
        },
    )
    with patch.object(
        pipelineRoutes, "_fsResolveHostLogPath", return_value=str(sLog),
    ):
        responseHttp = clientHttp.get(
            "/api/pipeline/abc123container/host-log-tail",
            params={"iLines": 10},
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert len(dictBody["listIncidents"]) == 1
    assert dictBody["listIncidents"][0]["sExceptionRepr"] == (
        "RuntimeError('boom')"
    )


# -----------------------------------------------------------------------
# Agent-action wiring: /state route exposes get-pipeline-state
# -----------------------------------------------------------------------


def test_pipeline_state_route_decorated_as_agent_action():
    """The dashboard's /state poll doubles as the agent diagnostics endpoint."""
    from vaibify.gui import pipelineServer, actionCatalog
    from unittest.mock import MagicMock
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = pipelineServer.fappCreateApplication(iExpectedPort=0)
    dictByPath = {
        route.path: route.endpoint
        for route in app.routes
        if hasattr(route, "path") and hasattr(route, "endpoint")
    }
    fnEndpoint = dictByPath.get("/api/pipeline/{sContainerId}/state")
    assert fnEndpoint is not None
    assert getattr(fnEndpoint, "_sAgentActionName", None) == (
        "get-pipeline-state"
    )
    assert actionCatalog.fdictLookupAction("get-pipeline-state") is not None


def test_host_log_tail_route_decorated_as_agent_action():
    from vaibify.gui import pipelineServer, actionCatalog
    from unittest.mock import MagicMock
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = pipelineServer.fappCreateApplication(iExpectedPort=0)
    dictByPath = {
        route.path: route.endpoint
        for route in app.routes
        if hasattr(route, "path") and hasattr(route, "endpoint")
    }
    sPath = "/api/pipeline/{sContainerId}/host-log-tail"
    fnEndpoint = dictByPath.get(sPath)
    assert fnEndpoint is not None
    assert getattr(fnEndpoint, "_sAgentActionName", None) == (
        "get-host-log-tail"
    )
    assert actionCatalog.fdictLookupAction("get-host-log-tail") is not None
