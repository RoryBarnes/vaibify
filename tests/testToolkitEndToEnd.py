"""End-to-end smoke test for the toolkit container creation chain.

Exercises the creation wizard API with the toolkit template and
then the repoRoutes auto-seed path, without starting any Docker
container.  The two halves use independent fake infrastructure:
the wizard half uses the real templateManager against a monkey-
patched templates directory, the repoRoutes half uses an in-memory
fake docker connection similar to the one in testRepoRoutes.py.
"""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.config import registryManager
from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
from vaibify.gui.routes.repoRoutes import fnRegisterAll


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the project registry into a temp directory."""
    sRegistryDir = str(tmp_path / ".vaibify")
    sRegistryPath = os.path.join(sRegistryDir, "registry.json")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", sRegistryPath,
    )


class FakeDockerMinimal:
    """Minimal fake docker for the repoRoutes auto-seed path."""

    def __init__(self):
        self.dictRepos = {}
        self.dictFiles = {}

    def fnAddRepo(self, sName, sUrl):
        self.dictRepos[sName] = sUrl

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if sCommand.startswith("cat /workspace/.vaibify/"):
            sContent = self.dictFiles.get(
                "/workspace/.vaibify/tracked_repos.json", ""
            )
            if sContent:
                return (0, sContent)
            return (1, "")
        if sCommand.startswith("mkdir -p"):
            return (0, "")
        if "find /workspace -mindepth 2" in sCommand:
            listOut = [
                f"/workspace/{s}" for s in self.dictRepos
            ]
            return (0, "\n".join(listOut) + "\n")
        if sCommand.startswith("test -d /workspace/"):
            sName = sCommand.split(
                "/workspace/")[1].split("/")[0]
            if sName in self.dictRepos:
                return (0, "yes\n")
            return (0, "no\n")
        if sCommand.startswith("git -C"):
            return self._ftGit(sCommand)
        return (0, "")

    def _ftGit(self, sCommand):
        sName = sCommand.split(
            "/workspace/")[1].split(" ")[0]
        sUrl = self.dictRepos.get(sName)
        if sUrl is None:
            return (128, "")
        if "config --get remote.origin.url" in sCommand:
            return (0, sUrl + "\n")
        if "rev-parse --abbrev-ref HEAD" in sCommand:
            return (0, "main\n")
        if "status --porcelain" in sCommand:
            return (0, "")
        return (0, "")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[sPath] = baContent.decode("utf-8")


def _fnSeedToolkitTemplate(tmp_path, monkeypatch):
    """Point the template manager at a temp toolkit template."""
    sTemplateDir = str(tmp_path / "templates" / "toolkit")
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


def _fclientBuildRegistry():
    """Build a hub-mode FastAPI TestClient."""
    app = FastAPI()
    dictCtx = {"require": lambda: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return TestClient(app)


def _fclientBuildRepos(fakeDocker):
    """Build a FastAPI TestClient with repoRoutes wired up."""
    app = FastAPI()
    dictCtx = {"require": lambda: None, "docker": fakeDocker}
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


def testToolkitCreationChainWritesYamlWithRepositories(
    tmp_path, monkeypatch,
):
    _fnSeedToolkitTemplate(tmp_path, monkeypatch)
    clientHub = _fclientBuildRegistry()
    sProjectDir = str(tmp_path / "test_toolkit")
    listUrls = [
        "https://github.com/example/foo.git",
        "https://github.com/example/bar.git",
    ]
    response = clientHub.post(
        "/api/projects/create",
        json={
            "sDirectory": sProjectDir,
            "sProjectName": "test_toolkit",
            "sTemplateName": "toolkit",
            "sPythonVersion": "3.12",
            "listRepositories": listUrls,
        },
    )
    assert response.status_code == 200
    sYaml = os.path.join(sProjectDir, "vaibify.yml")
    assert os.path.isfile(sYaml)
    configLoaded = fconfigLoadFromFile(sYaml)
    assert len(configLoaded.listRepositories) == 2
    listLoadedNames = sorted(
        r["name"] for r in configLoaded.listRepositories
    )
    listLoadedUrls = sorted(
        r["url"] for r in configLoaded.listRepositories
    )
    assert listLoadedNames == ["bar", "foo"]
    assert listLoadedUrls == sorted(listUrls)


def testToolkitAutoSeedsReposAsTracked(tmp_path, monkeypatch):
    fakeDocker = FakeDockerMinimal()
    fakeDocker.fnAddRepo("foo", "https://github.com/example/foo.git")
    fakeDocker.fnAddRepo("bar", "https://github.com/example/bar.git")
    clientRepos = _fclientBuildRepos(fakeDocker)
    response = clientRepos.get("/api/repos/cid_toolkit/status")
    assert response.status_code == 200
    dictBody = response.json()
    listTrackedNames = sorted(
        d["sName"] for d in dictBody["listTracked"]
    )
    assert listTrackedNames == ["bar", "foo"]
    assert dictBody["listUndecided"] == []
    assert dictBody["listIgnored"] == []
    dictStored = json.loads(
        fakeDocker.dictFiles[
            "/workspace/.vaibify/tracked_repos.json"
        ]
    )
    listStoredUrls = sorted(
        r["sUrl"] for r in dictStored["listTracked"]
    )
    assert listStoredUrls == [
        "https://github.com/example/bar.git",
        "https://github.com/example/foo.git",
    ]
