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
