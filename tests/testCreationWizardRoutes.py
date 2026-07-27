"""Tests for creation wizard API routes in registryRoutes."""

import os

import pytest

from vaibify.config import registryManager


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect registry to a temp directory for every test."""
    sRegistryDir = str(tmp_path / ".vaibify")
    sRegistryPath = os.path.join(sRegistryDir, "registry.json")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", sRegistryPath,
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDir, "registry.lock"),
    )


@pytest.fixture
def fixtureApp():
    """Create a hub-mode app with Docker mocked out."""
    from fastapi import FastAPI
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes

    app = FastAPI()
    dictCtx = {"require": lambda: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def fixtureClient(fixtureApp):
    """Create a test client for the hub app."""
    from starlette.testclient import TestClient
    return TestClient(fixtureApp)


# --- GET /api/setup/templates ---

def testGetTemplatesSuccess(fixtureClient, tmp_path, monkeypatch):
    sTemplateDir = str(tmp_path / "templates")
    os.makedirs(os.path.join(sTemplateDir, "sandbox"))
    os.makedirs(os.path.join(sTemplateDir, "workflow"))
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    )
    response = fixtureClient.get("/api/setup/templates")
    assert response.status_code == 200
    dictResult = response.json()
    assert "sandbox" in dictResult["listTemplates"]
    assert "workflow" in dictResult["listTemplates"]


def testGetTemplatesMissingDirectory(fixtureClient, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        Path("/nonexistent/templates"),
    )
    response = fixtureClient.get("/api/setup/templates")
    assert response.status_code == 404


# --- GET /api/setup/templates/{sName} ---

def testGetTemplateConfigSuccess(
    fixtureClient, tmp_path, monkeypatch,
):
    sTemplateDir = str(tmp_path / "templates" / "sandbox")
    os.makedirs(sTemplateDir)
    sConfPath = os.path.join(sTemplateDir, "container.conf")
    with open(sConfPath, "w") as fileHandle:
        fileHandle.write("")
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    )
    response = fixtureClient.get("/api/setup/templates/sandbox")
    assert response.status_code == 200
    assert "listRepositories" in response.json()


def testGetTemplateConfigNotFound(fixtureClient, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        Path("/nonexistent/templates"),
    )
    response = fixtureClient.get("/api/setup/templates/missing")
    assert response.status_code == 404


# --- POST /api/projects/create ---

def testCreateProjectSuccess(
    fixtureClient, tmp_path, monkeypatch,
):
    sTemplateDir = str(tmp_path / "templates" / "sandbox")
    os.makedirs(sTemplateDir)
    sConfPath = os.path.join(sTemplateDir, "container.conf")
    with open(sConfPath, "w") as fileHandle:
        fileHandle.write("")
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes.os.path.expanduser",
        lambda _: str(tmp_path),
    )
    sProjectDir = str(tmp_path / "my-project")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "my-project",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["bSuccess"] is True
    assert os.path.isfile(os.path.join(sProjectDir, "vaibify.yml"))


def testCreateProjectWithRepositories(
    fixtureClient, tmp_path, monkeypatch,
):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    sTemplateDir = str(tmp_path / "templates" / "sandbox")
    os.makedirs(sTemplateDir)
    sConfPath = os.path.join(sTemplateDir, "container.conf")
    with open(sConfPath, "w") as fileHandle:
        fileHandle.write("")
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes.os.path.expanduser",
        lambda _: str(tmp_path),
    )
    sProjectDir = str(tmp_path / "my-project")
    listUrls = [
        "https://github.com/example/foo.git",
        "https://github.com/example/bar.git",
    ]
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "my-project",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": listUrls,
        },
    )
    assert response.status_code == 200
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    configLoaded = fconfigLoadFromFile(sConfigPath)
    assert len(configLoaded.listRepositories) == 2
    listNames = [r["name"] for r in configLoaded.listRepositories]
    listRepoUrls = [r["url"] for r in configLoaded.listRepositories]
    assert "foo" in listNames
    assert "bar" in listNames
    assert listUrls[0] in listRepoUrls
    assert listUrls[1] in listRepoUrls


def _fnPrepSandboxTemplate(tmp_path, monkeypatch):
    """Stub a sandbox template and home dir for create-project tests."""
    sTemplateDir = str(tmp_path / "templates" / "sandbox")
    os.makedirs(sTemplateDir)
    with open(os.path.join(
            sTemplateDir, "container.conf"), "w") as fileHandle:
        fileHandle.write("")
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes.os.path.expanduser",
        lambda _: str(tmp_path),
    )


def testCreateProjectPersistsFeatures(
    fixtureClient, tmp_path, monkeypatch,
):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    sProjectDir = str(tmp_path / "feat-project")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "feat-project",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": [],
            "listFeatures": ["claude", "jupyter", "gpu"],
        },
    )
    assert response.status_code == 200
    config = fconfigLoadFromFile(
        os.path.join(sProjectDir, "vaibify.yml"))
    assert config.features.bClaude is True
    assert config.features.bJupyter is True
    assert config.features.bGpu is True
    assert config.features.bRLanguage is False
    assert config.features.bJulia is False
    assert config.features.bClaudeAutoUpdate is True


def testCreateProjectDisablesClaudeAutoUpdate(
    fixtureClient, tmp_path, monkeypatch,
):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    sProjectDir = str(tmp_path / "claude-noauto")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "claude-noauto",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": [],
            "listFeatures": ["claude"],
            "bClaudeAutoUpdate": False,
        },
    )
    assert response.status_code == 200
    config = fconfigLoadFromFile(
        os.path.join(sProjectDir, "vaibify.yml"))
    assert config.features.bClaude is True
    assert config.features.bClaudeAutoUpdate is False


def testCreateProjectPersistsCodexAndGeminiSettings(
    fixtureClient, tmp_path, monkeypatch,
):
    """Provider fields do not collapse into the legacy Claude setting."""
    from vaibify.config.projectConfig import fconfigLoadFromFile
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    sProjectDir = str(tmp_path / "multi-agent")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "multi-agent",
            "sTemplateName": "sandbox",
            "listFeatures": ["codex", "gemini"],
            "bCodexAutoUpdate": False,
            "bGeminiAutoUpdate": True,
        },
    )
    assert response.status_code == 200
    config = fconfigLoadFromFile(os.path.join(sProjectDir, "vaibify.yml"))
    assert config.features.bCodex is True
    assert config.features.bCodexAutoUpdate is False
    assert config.features.bGemini is True
    assert config.features.bGeminiAutoUpdate is True


def testCreateProjectPersistsGithubAuthSecret(
    fixtureClient, tmp_path, monkeypatch,
):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    sProjectDir = str(tmp_path / "auth-on")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "auth-on",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": [],
            "bUseGithubAuth": True,
        },
    )
    assert response.status_code == 200
    config = fconfigLoadFromFile(
        os.path.join(sProjectDir, "vaibify.yml"))
    listSecrets = config.listSecrets
    assert len(listSecrets) == 1
    assert listSecrets[0]["name"] == "gh_token"
    assert listSecrets[0]["method"] == "gh_auth"


def testCreateProjectOmitsGithubAuthWhenDisabled(
    fixtureClient, tmp_path, monkeypatch,
):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    sProjectDir = str(tmp_path / "auth-off")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "auth-off",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": [],
            "bUseGithubAuth": False,
        },
    )
    assert response.status_code == 200
    config = fconfigLoadFromFile(
        os.path.join(sProjectDir, "vaibify.yml"))
    assert config.listSecrets == []


def testCreateProjectPersistsPackagesAndToggles(
    fixtureClient, tmp_path, monkeypatch,
):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    sProjectDir = str(tmp_path / "pkg-project")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "pkg-project",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "listRepositories": [],
            "listSystemPackages": ["gfortran", "libhdf5-dev"],
            "listPythonPackages": ["numpy", "matplotlib"],
            "sPackageManager": "conda",
            "bNeverSleep": True,
            "bNetworkIsolation": True,
            "sContainerUser": "researcher",
            "sBaseImage": "ubuntu:24.04",
        },
    )
    assert response.status_code == 200
    config = fconfigLoadFromFile(
        os.path.join(sProjectDir, "vaibify.yml"))
    assert "gfortran" in config.listSystemPackages
    assert "libhdf5-dev" in config.listSystemPackages
    assert "numpy" in config.listPythonPackages
    assert "matplotlib" in config.listPythonPackages
    assert config.sPackageManager == "conda"
    assert config.bNeverSleep is True
    assert config.bNetworkIsolation is True


@pytest.mark.falsification
def testCreateProjectRejectsCondaPackages(fixtureClient, tmp_path,
                                          monkeypatch):
    """Kills: accepting a package list the image build never installs.

    This test previously asserted the opposite -- that ``scipy``
    round-tripped into ``vaibify.yml``. It did, and nothing ever
    installed it: the Dockerfile has no ``conda install`` step and no
    build argument carries the list, so the researcher got a container
    without the package and no indication of it. Recording a request
    the build silently drops is the config-file equivalent of a
    dashboard showing a state the container is not in.

    Mutation: drop the ``_fnRejectUninstallablePackages`` call from the
    create route and the request is accepted again.
    """
    _fnPrepSandboxTemplate(tmp_path, monkeypatch)
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": str(tmp_path / "conda-project"),
            "sProjectName": "conda-project",
            "sTemplateName": "sandbox",
            "sPythonVersion": "3.12",
            "sPackageManager": "conda",
            "listCondaPackages": ["scipy"],
        },
    )
    assert response.status_code == 400
    assert "conda" in response.json()["detail"].lower()
    assert not (tmp_path / "conda-project" / "vaibify.yml").exists()


def testCreateProjectRelativePathRejected(fixtureClient):
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": "relative/path",
            "sProjectName": "test",
            "sTemplateName": "sandbox",
        },
    )
    assert response.status_code == 400


def testCreateProjectOutsideHomeRejected(fixtureClient):
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": "/tmp/escape-attempt",
            "sProjectName": "test",
            "sTemplateName": "sandbox",
        },
    )
    assert response.status_code == 403


def testCreateProjectBadTemplateReturns404(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes.os.path.expanduser",
        lambda _: str(tmp_path),
    )
    os.makedirs(str(tmp_path / "templates"))
    sProjectDir = str(tmp_path / "my-project")
    response = fixtureClient.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "my-project",
            "sTemplateName": "nonexistent",
        },
    )
    assert response.status_code == 404
