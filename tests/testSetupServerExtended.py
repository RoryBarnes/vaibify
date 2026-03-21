"""Tests for vaibify.gui.setupServer (not install/setupServer)."""

import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock

from vaibify.gui.setupServer import (
    fappCreateSetupApplication,
    flistAvailableTemplates,
    fnWriteConfigToDirectory,
    ftResultRunBuild,
    fdictProcessBuild,
    ValidateRequest,
    SaveRequest,
    BuildRequest,
)


# -----------------------------------------------------------------------
# fappCreateSetupApplication
# -----------------------------------------------------------------------


def test_fappCreateSetupApplication_returns_app():
    with patch(
        "vaibify.gui.setupServer.os.path.isdir",
        return_value=False,
    ):
        app = fappCreateSetupApplication()
        assert app is not None
        assert app.title == "Vaibify Setup Wizard"


# -----------------------------------------------------------------------
# flistAvailableTemplates
# -----------------------------------------------------------------------


def test_flistAvailableTemplates_returns_list():
    listResult = flistAvailableTemplates()
    assert isinstance(listResult, list)


# -----------------------------------------------------------------------
# fnWriteConfigToDirectory
# -----------------------------------------------------------------------


def test_fnWriteConfigToDirectory_creates_file(tmp_path):
    sDir = str(tmp_path / "project")
    fnWriteConfigToDirectory(sDir, {"sKey": "value"})
    sConfigPath = os.path.join(sDir, "vaibify.yml")
    assert os.path.isfile(sConfigPath)


def test_fnWriteConfigToDirectory_content(tmp_path):
    sDir = str(tmp_path / "project")
    fnWriteConfigToDirectory(sDir, {"projectName": "test"})
    sConfigPath = os.path.join(sDir, "vaibify.yml")
    with open(sConfigPath) as fh:
        sContent = fh.read()
    assert "test" in sContent


# -----------------------------------------------------------------------
# ftResultRunBuild
# -----------------------------------------------------------------------


@patch("vaibify.gui.setupServer.subprocess.run")
def test_ftResultRunBuild_success(mockRun, tmp_path):
    mockRun.return_value = MagicMock(
        returncode=0, stdout="OK", stderr="",
    )
    iExitCode, sOutput = ftResultRunBuild(str(tmp_path))
    assert iExitCode == 0
    assert "OK" in sOutput


@patch(
    "vaibify.gui.setupServer.subprocess.run",
    side_effect=FileNotFoundError,
)
def test_ftResultRunBuild_missing_python(mockRun, tmp_path):
    iExitCode, sOutput = ftResultRunBuild(str(tmp_path))
    assert iExitCode == 1
    assert "not found" in sOutput.lower()


@patch(
    "vaibify.gui.setupServer.subprocess.run",
    side_effect=__import__(
        "subprocess"
    ).TimeoutExpired(cmd="test", timeout=600),
)
def test_ftResultRunBuild_timeout(mockRun, tmp_path):
    iExitCode, sOutput = ftResultRunBuild(str(tmp_path))
    assert iExitCode == 1
    assert "timed out" in sOutput.lower()


# -----------------------------------------------------------------------
# fdictProcessBuild
# -----------------------------------------------------------------------


@patch(
    "vaibify.gui.setupServer.ftResultRunBuild",
    return_value=(0, "Build complete"),
)
def test_fdictProcessBuild_success(mockBuild):
    dictResult = fdictProcessBuild("/workspace")
    assert dictResult["bSuccess"] is True
    assert "Build complete" in dictResult["sOutput"]


@patch(
    "vaibify.gui.setupServer.ftResultRunBuild",
    return_value=(1, "Error occurred"),
)
def test_fdictProcessBuild_failure_raises(mockBuild):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        fdictProcessBuild("/workspace")


# -----------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------


def test_ValidateRequest_model():
    request = ValidateRequest(dictConfig={"key": "val"})
    assert request.dictConfig == {"key": "val"}


def test_SaveRequest_model():
    request = SaveRequest(
        sProjectDirectory="/tmp", dictConfig={"a": 1},
    )
    assert request.sProjectDirectory == "/tmp"


def test_BuildRequest_model():
    request = BuildRequest(sProjectDirectory="/workspace")
    assert request.sProjectDirectory == "/workspace"


# -----------------------------------------------------------------------
# TestClient route tests
# -----------------------------------------------------------------------


@pytest.fixture
def clientSetup():
    """Create a test client for the setup application."""
    from fastapi.testclient import TestClient
    with patch(
        "vaibify.gui.setupServer.os.path.isdir",
        return_value=False,
    ):
        app = fappCreateSetupApplication()
    return TestClient(app)


def test_templates_route(clientSetup):
    response = clientSetup.get("/api/setup/templates")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@patch(
    "vaibify.gui.setupServer.fbValidateConfig",
    return_value=True,
)
def test_validate_route(mockValidate, clientSetup):
    response = clientSetup.post(
        "/api/setup/validate",
        json={"dictConfig": {"projectName": "test"}},
    )
    assert response.status_code == 200
    assert response.json()["bValid"] is True


@patch(
    "vaibify.gui.setupServer.fbValidateConfig",
    return_value=False,
)
def test_validate_route_invalid(mockValidate, clientSetup):
    response = clientSetup.post(
        "/api/setup/validate",
        json={"dictConfig": {}},
    )
    assert response.status_code == 200
    assert response.json()["bValid"] is False
