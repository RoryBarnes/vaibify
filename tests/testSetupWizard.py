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


def test_wizard_does_not_offer_the_rejected_uv_manager():
    """uv was listed in the wizard but rejected by the backend.

    The offered options must match what _flistCollectErrors accepts
    (pip/conda/mamba); offering uv produced a config the same server
    then refused to save.
    """
    import os
    sHtml = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vaibify", "gui", "static", "setupWizard.html",
        ),
        encoding="utf-8",
    ).read()
    iSelect = sHtml.find('id="packageManager"')
    sSelect = sHtml[iSelect:sHtml.find("</select>", iSelect)]
    assert '"uv"' not in sSelect, "uv is not an accepted package manager"
    for sManager in ("pip", "conda", "mamba"):
        assert f'"{sManager}"' in sSelect


def test_validate_still_rejects_uv_if_posted_directly():
    """Belt: even a hand-crafted uv POST is refused, not silently saved."""
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    client = TestClient(fappCreateSetupWizard(sOutputDirectory="/tmp"))
    responseHttp = client.post("/api/setup/validate", json={
        "sProjectName": "p", "sPackageManager": "uv",
    })
    dictResult = responseHttp.json()
    assert dictResult["bValid"] is False
    assert any("packageManager" in s for s in dictResult["listErrors"])


def test_wizard_no_longer_collects_a_discarded_zenodo_id():
    """The Zenodo deposition ID is a per-workflow field, not build config.

    It was collected into vaibify.yml, which has no home for it, and
    silently dropped. The wizard no longer collects it (it is set per
    workflow in project.json).
    """
    import os
    sBase = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vaibify", "gui", "static",
    )
    sHtml = open(
        os.path.join(sBase, "setupWizard.html"), encoding="utf-8",
    ).read()
    sJs = open(
        os.path.join(sBase, "scriptSetupWizard.js"), encoding="utf-8",
    ).read()
    assert "zenodoDepositionId" not in sHtml
    assert "zenodoDepositionId" not in sJs
    assert "sZenodoDepositionId" not in sJs


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


def test_save_claude_auto_update_default_true(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "sProjectName": "claude_default",
        "sPackageManager": "pip",
        "listFeatures": ["claude"],
    }
    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )
    assert responseHttp.status_code == 200

    with open(tmp_path / "vaibify.yml", "r") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)
    assert dictSaved["features"]["claudeAutoUpdate"] is True


def test_save_claude_auto_update_explicit_false(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "sProjectName": "claude_off",
        "sPackageManager": "pip",
        "listFeatures": ["claude"],
        "bClaudeAutoUpdate": False,
    }
    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )
    assert responseHttp.status_code == 200

    with open(tmp_path / "vaibify.yml", "r") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)
    assert dictSaved["features"]["claudeAutoUpdate"] is False


def test_existing_config_returns_auto_update_flag(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)

    dictPayload = {
        "sProjectName": "existing_claude",
        "sPackageManager": "pip",
        "listFeatures": ["claude"],
        "bClaudeAutoUpdate": False,
    }
    clientHttp.post("/api/setup/save", json=dictPayload)

    responseHttp = clientHttp.get("/api/setup/config")
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bClaudeAutoUpdate"] is False


def test_setup_wizard_persists_each_new_agent_auto_update_flag(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    responseHttp = clientHttp.post(
        "/api/setup/save", json={
            "sProjectName": "multi-agent",
            "sPackageManager": "pip",
            "listFeatures": ["codex", "gemini"],
            "bCodexAutoUpdate": False,
            "bGeminiAutoUpdate": True,
        },
    )
    assert responseHttp.status_code == 200
    with open(tmp_path / "vaibify.yml", "r") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)
    assert dictSaved["features"]["codexAutoUpdate"] is False
    assert dictSaved["features"]["geminiAutoUpdate"] is True


def test_wizard_has_a_single_save_action_button():
    """One save action, not two identical buttons hitting two endpoints.

    An earlier pass renamed the build button to 'Save Configuration'
    without noticing the primary save button already carried that label,
    leaving two identical buttons that both wrote the same YAML.
    """
    import os
    sBase = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vaibify", "gui", "static",
    )
    sHtml = open(
        os.path.join(sBase, "setupWizard.html"), encoding="utf-8",
    ).read()
    iStart = sHtml.find('class="setup-actions"')
    sActions = sHtml[iStart:sHtml.find("</div>", iStart)]
    assert sActions.count("<button") == 1, (
        "the setup actions must expose exactly one save button"
    )
    assert "btnBuildContainer" not in sHtml
    sJs = open(
        os.path.join(sBase, "scriptSetupWizard.js"), encoding="utf-8",
    ).read()
    assert "fnBuildContainer" not in sJs
    assert "vaibify build" in sJs, (
        "the save toast should name the build next step"
    )
