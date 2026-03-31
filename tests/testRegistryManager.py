"""Tests for vaibify.config.registryManager."""

import json
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


def _fnWriteMinimalConfig(tmp_path, sProjectName="test-project"):
    """Create a minimal vaibify.yml in a temp project directory."""
    sProjectDir = str(tmp_path / sProjectName)
    os.makedirs(sProjectDir, exist_ok=True)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(f"projectName: {sProjectName}\n")
    return sProjectDir


# --- fdictLoadRegistry ---

def testLoadRegistryReturnsEmptyWhenNoFile():
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == {"listProjects": []}


def testLoadRegistryReturnsEmptyWhenCorrupt(tmp_path):
    sDir = registryManager._S_REGISTRY_DIRECTORY
    os.makedirs(sDir, exist_ok=True)
    sPath = registryManager._S_REGISTRY_PATH
    with open(sPath, "w") as fileHandle:
        fileHandle.write("not valid json{{{")
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == {"listProjects": []}


def testLoadRegistryReturnsContent(tmp_path):
    sDir = registryManager._S_REGISTRY_DIRECTORY
    os.makedirs(sDir, exist_ok=True)
    dictExpected = {"listProjects": [{"sName": "foo"}]}
    with open(registryManager._S_REGISTRY_PATH, "w") as fileHandle:
        json.dump(dictExpected, fileHandle)
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == dictExpected


# --- fnSaveRegistry ---

def testSaveRegistryCreatesDirectory():
    dictRegistry = {"listProjects": [{"sName": "bar"}]}
    registryManager.fnSaveRegistry(dictRegistry)
    assert os.path.isdir(registryManager._S_REGISTRY_DIRECTORY)
    dictLoaded = registryManager.fdictLoadRegistry()
    assert dictLoaded["listProjects"][0]["sName"] == "bar"


def testSaveRegistryAtomicOverwrite():
    registryManager.fnSaveRegistry({"listProjects": [{"sName": "a"}]})
    registryManager.fnSaveRegistry({"listProjects": [{"sName": "b"}]})
    dictLoaded = registryManager.fdictLoadRegistry()
    assert len(dictLoaded["listProjects"]) == 1
    assert dictLoaded["listProjects"][0]["sName"] == "b"


# --- fsDiscoverConfigInDirectory ---

def testDiscoverConfigFindsVaibifyYml(tmp_path):
    sProjectDir = str(tmp_path / "myproject")
    os.makedirs(sProjectDir)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("projectName: myproject\n")
    sResult = registryManager.fsDiscoverConfigInDirectory(sProjectDir)
    assert sResult == sConfigPath


def testDiscoverConfigRaisesWhenMissing(tmp_path):
    sEmptyDir = str(tmp_path / "empty")
    os.makedirs(sEmptyDir)
    with pytest.raises(FileNotFoundError):
        registryManager.fsDiscoverConfigInDirectory(sEmptyDir)



# --- fnAddProject ---

def testAddProjectRegistersSuccessfully(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    listProjects = registryManager.flistGetAllProjects()
    assert len(listProjects) == 1
    assert listProjects[0]["sName"] == "test-project"
    assert listProjects[0]["sDirectory"] == sProjectDir


def testAddProjectRejectsDuplicateName(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "dup-project")
    registryManager.fnAddProject(sProjectDir)
    with pytest.raises(ValueError, match="Container.*already registered"):
        registryManager.fnAddProject(sProjectDir)


def testAddProjectAllowsSameNameFromDifferentDirectory(tmp_path):
    """After removing a container, re-adding from a new dir succeeds."""
    sProjectDirA = _fnWriteMinimalConfig(tmp_path, "reuse-project")
    registryManager.fnAddProject(sProjectDirA)
    registryManager.fnRemoveProject("reuse-project")
    sProjectDirB = str(tmp_path / "alt" / "reuse-project")
    os.makedirs(sProjectDirB, exist_ok=True)
    sConfigPath = os.path.join(sProjectDirB, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("projectName: reuse-project\n")
    registryManager.fnAddProject(sProjectDirB)
    assert len(registryManager.flistGetAllProjects()) == 1


def testAddProjectRejectsMissingConfig(tmp_path):
    sEmptyDir = str(tmp_path / "no-config")
    os.makedirs(sEmptyDir)
    with pytest.raises(FileNotFoundError):
        registryManager.fnAddProject(sEmptyDir)


# --- fnRemoveProject ---

def testRemoveProjectDeletesEntry(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    registryManager.fnRemoveProject("test-project")
    assert len(registryManager.flistGetAllProjects()) == 0


def testRemoveProjectRaisesWhenNotFound():
    with pytest.raises(KeyError, match="not found"):
        registryManager.fnRemoveProject("nonexistent")


# --- fdictGetProject ---

def testGetProjectReturnsEntry(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    dictProject = registryManager.fdictGetProject("test-project")
    assert dictProject is not None
    assert dictProject["sName"] == "test-project"


def testGetProjectReturnsNoneWhenMissing():
    assert registryManager.fdictGetProject("ghost") is None


# --- flistGetAllProjectsWithStatus ---

def testGetAllProjectsWithStatusEnrichesEntries(
    tmp_path, monkeypatch,
):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        lambda sTag: False,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": False, "bRunning": False,
            "sStatus": "not found",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert len(listResult) == 1
    assert listResult[0]["bImageExists"] is False
    assert listResult[0]["bRunning"] is False
    assert listResult[0]["sStatus"] == "not built"


def testStatusRunningWhenContainerActive(tmp_path, monkeypatch):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        lambda sTag: True,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": True, "bRunning": True,
            "sStatus": "running",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "running"
    assert listResult[0]["bRunning"] is True


def testStatusStoppedWhenImageExistsButNotRunning(
    tmp_path, monkeypatch,
):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        lambda sTag: True,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": False, "bRunning": False,
            "sStatus": "not found",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "stopped"


# -----------------------------------------------------------------------
# _fnWriteRegistryAtomic — exception path
# -----------------------------------------------------------------------


def testWriteRegistryAtomicCleansUpOnReplaceError(
    tmp_path, monkeypatch,
):
    import vaibify.config.registryManager as rm
    monkeypatch.setattr(rm, "_S_REGISTRY_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(
        rm, "_S_REGISTRY_PATH", str(tmp_path / "registry.json"),
    )

    def fRaisOnReplace(sSrc, sDst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fRaisOnReplace)
    with pytest.raises(OSError, match="replace failed"):
        rm._fnWriteRegistryAtomic({"listProjects": []})
    listTmpFiles = list(tmp_path.glob("*.tmp"))
    assert len(listTmpFiles) == 0, "Temp file should be cleaned up"


# -----------------------------------------------------------------------
# fdictLoadRegistry — non-dict value in file
# -----------------------------------------------------------------------


def testLoadRegistryReturnsEmptyWhenNotDict(tmp_path):
    """Line 29: registry file contains a list instead of a dict."""
    sDir = registryManager._S_REGISTRY_DIRECTORY
    os.makedirs(sDir, exist_ok=True)
    sPath = registryManager._S_REGISTRY_PATH
    with open(sPath, "w") as fileHandle:
        fileHandle.write('["not", "a", "dict"]')
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == {"listProjects": []}


# -----------------------------------------------------------------------
# fsGetContainerUser
# -----------------------------------------------------------------------


def testGetContainerUserReturnsResearcherWhenNotRegistered():
    """Lines 204-206: project not in registry returns fallback."""
    sResult = registryManager.fsGetContainerUser("nonexistent-container")
    assert sResult == "researcher"


def testGetContainerUserReturnsResearcherOnConfigError(
    tmp_path, monkeypatch,
):
    """Lines 207-212: config load fails returns fallback."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "user-project")
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.cli.configLoader.fconfigLoadFromPath",
        lambda sPath: (_ for _ in ()).throw(
            RuntimeError("config broken")
        ),
    )
    sResult = registryManager.fsGetContainerUser("user-project")
    assert sResult == "researcher"


def testGetContainerUserReturnsActualUser(tmp_path, monkeypatch):
    """Lines 204-210: successful path returns container user."""
    from types import SimpleNamespace
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "real-project")
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.cli.configLoader.fconfigLoadFromPath",
        lambda sPath: SimpleNamespace(sContainerUser="scientist"),
    )
    sResult = registryManager.fsGetContainerUser("real-project")
    assert sResult == "scientist"
