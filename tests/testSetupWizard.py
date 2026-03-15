"""Tests for vaibcask.install.setupServer setup wizard API routes."""

import yaml
import pytest
from fastapi.testclient import TestClient

from vaibcask.install.setupServer import fappCreateSetupWizard


@pytest.fixture
def clientHttp(tmp_path):
    """Create a TestClient for the setup wizard app."""
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    return TestClient(app)


@pytest.fixture
def sOutputDirectory(tmp_path):
    """Return the tmp_path as a string for config file assertions."""
    return str(tmp_path)


def test_get_templates_returns_list(clientHttp):
    responseHttp = clientHttp.get("/api/templates")

    assert responseHttp.status_code == 200
    listTemplates = responseHttp.json()
    assert isinstance(listTemplates, list)


def test_validate_valid_config(clientHttp):
    dictPayload = {
        "projectName": "my_project",
        "containerUser": "researcher",
        "pythonVersion": "3.12",
        "baseImage": "ubuntu:24.04",
        "packageManager": "pip",
        "repositories": [],
        "features": {"jupyter": True, "gpu": False},
    }

    responseHttp = clientHttp.post("/api/validate", json=dictPayload)

    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bValid"] is True
    assert dictResult["listErrors"] == []


def test_validate_missing_project_name(clientHttp):
    dictPayload = {
        "projectName": "",
        "packageManager": "pip",
        "features": {},
    }

    responseHttp = clientHttp.post("/api/validate", json=dictPayload)

    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bValid"] is False
    assert len(dictResult["listErrors"]) > 0
    bFoundNameError = any(
        "projectName" in sError
        for sError in dictResult["listErrors"]
    )
    assert bFoundNameError


def test_save_writes_yaml_file(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "projectName": "saved_project",
        "containerUser": "researcher",
        "pythonVersion": "3.12",
        "baseImage": "ubuntu:24.04",
        "packageManager": "pip",
        "repositories": [],
        "features": {"jupyter": True},
    }

    responseHttp = clientHttp.post("/api/save", json=dictPayload)

    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True

    sExpectedPath = tmp_path / "vaibcask.yml"
    assert sExpectedPath.exists()

    with open(sExpectedPath, "r") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)

    assert dictSaved["projectName"] == "saved_project"
    assert dictSaved["features"]["jupyter"] is True


def test_save_rejects_invalid_config(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "projectName": "",
        "packageManager": "pip",
    }

    responseHttp = clientHttp.post("/api/save", json=dictPayload)

    assert responseHttp.status_code == 400


def test_get_defaults(clientHttp):
    responseHttp = clientHttp.get("/api/defaults")

    assert responseHttp.status_code == 200
    dictDefaults = responseHttp.json()
    assert isinstance(dictDefaults, dict)
    assert "packageManager" in dictDefaults
