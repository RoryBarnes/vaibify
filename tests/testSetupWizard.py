"""Tests for vaibify.install.setupServer setup wizard API routes."""

import yaml
import pytest
from fastapi.testclient import TestClient

from vaibify.install.setupServer import fappCreateSetupWizard


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
    responseHttp = clientHttp.get("/api/setup/templates")

    assert responseHttp.status_code == 200
    listTemplates = responseHttp.json()
    assert isinstance(listTemplates, list)


def test_validate_valid_config(clientHttp):
    dictPayload = {
        "sProjectName": "my_project",
        "sContainerUser": "researcher",
        "sPythonVersion": "3.12",
        "sBaseImage": "ubuntu:24.04",
        "sPackageManager": "pip",
        "listRepositories": [],
        "listFeatures": ["jupyter"],
        "listPipPackages": ["numpy"],
        "listAptPackages": ["gcc"],
    }

    responseHttp = clientHttp.post(
        "/api/setup/validate", json=dictPayload
    )

    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bValid"] is True
    assert dictResult["listErrors"] == []


def test_validate_missing_project_name(clientHttp):
    dictPayload = {
        "sProjectName": "",
        "sPackageManager": "pip",
    }

    responseHttp = clientHttp.post(
        "/api/setup/validate", json=dictPayload
    )

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
        "sProjectName": "saved_project",
        "sContainerUser": "researcher",
        "sPythonVersion": "3.12",
        "sBaseImage": "ubuntu:24.04",
        "sPackageManager": "pip",
        "listRepositories": [],
        "listFeatures": ["jupyter"],
        "listPipPackages": ["numpy>=1.24", "scipy"],
        "listAptPackages": ["libhdf5-dev"],
    }

    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )

    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True

    sExpectedPath = tmp_path / "vaibify.yml"
    assert sExpectedPath.exists()

    with open(sExpectedPath, "r") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)

    assert dictSaved["projectName"] == "saved_project"
    assert dictSaved["features"]["jupyter"] is True
    assert "numpy>=1.24" in dictSaved["pythonPackages"]
    assert "libhdf5-dev" in dictSaved["systemPackages"]


def test_save_rejects_invalid_config(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "sProjectName": "",
        "sPackageManager": "pip",
    }

    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )

    assert responseHttp.status_code == 400


def test_get_defaults(clientHttp):
    responseHttp = clientHttp.get("/api/setup/defaults")

    assert responseHttp.status_code == 200
    dictDefaults = responseHttp.json()
    assert isinstance(dictDefaults, dict)
    assert "packageManager" in dictDefaults


def test_get_existing_config_empty(clientHttp):
    responseHttp = clientHttp.get("/api/setup/config")

    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert isinstance(dictResult, dict)


def test_save_includes_all_features_as_bools(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "sProjectName": "feature_test",
        "sPackageManager": "pip",
        "listFeatures": ["latex", "claude"],
    }

    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )
    assert responseHttp.status_code == 200

    with open(tmp_path / "vaibify.yml", "r") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)

    dictFeatures = dictSaved["features"]
    assert dictFeatures["latex"] is True
    assert dictFeatures["claude"] is True
    assert dictFeatures["jupyter"] is False
    assert dictFeatures["gpu"] is False
