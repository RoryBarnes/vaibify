"""Coverage of the pinned-image transitions and refusals in ``buildRoutes``.

The acquire, switch-to-building and switch-to-pinned-image routes each
rewrite what a project's name resolves to, so their refusals are the
contract: an agent lane is refused, a project whose image is built is
told there is nothing to obtain, and a transition under a container the
daemon has not confirmed gone is refused naming the stop-first action.
The registry is a real file under ``tmp_path``; only the Docker daemon
and the acquisition lane (a network and daemon boundary) are stubbed.
"""

import json
import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import registryManager
from vaibify.docker import containerManager
from vaibify.gui import buildRoutes


S_PROJECT_NAME = "projectAlpha"
S_CONTAINER_NAME = "containerAlphaRuntime"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistryAndHome(tmp_path, monkeypatch):
    """Point the registry and every ~ lookup at ``tmp_path``."""
    sRegistryDirectory = str(tmp_path / "vaibifyHome" / ".vaibify")
    monkeypatch.setenv("HOME", str(tmp_path / "vaibifyHome"))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )
    buildRoutes._DICT_BUILD_PROGRESS.clear()
    yield sRegistryDirectory
    buildRoutes._DICT_BUILD_PROGRESS.clear()


def fdictWriteProjectEntry(tmp_path, bObtained, sMode="container"):
    """Register one project whose container name differs from its name."""
    sDirectory = str(tmp_path / "projectDirectory")
    os.makedirs(sDirectory, exist_ok=True)
    sConfigPath = os.path.join(sDirectory, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(f"projectName: {S_PROJECT_NAME}\n")
    dictEntry = {
        "sName": S_PROJECT_NAME,
        "sDirectory": sDirectory,
        "sConfigPath": sConfigPath,
        "sContainerName": S_CONTAINER_NAME,
        "sMode": sMode,
    }
    if bObtained:
        dictEntry["dictImageSource"] = {
            "sSource": "archive", "sReference": "registry.example/alpha@x",
        }
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY, exist_ok=True)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileHandle:
        json.dump({"listProjects": [dictEntry]}, fileHandle)
    return dictEntry


def fdictReadRegistryEntry():
    """Return the one registered entry as it is on disk now."""
    with open(registryManager._S_REGISTRY_PATH) as fileHandle:
        return json.load(fileHandle)["listProjects"][0]


def fnSetContainerStatus(monkeypatch, dictStatus, listQueried=None):
    """Answer the daemon's container probe with ``dictStatus``."""

    def fdictAnswer(sContainerName):
        if listQueried is not None:
            listQueried.append(sContainerName)
        if isinstance(dictStatus, Exception):
            raise dictStatus
        return dictStatus

    monkeypatch.setattr(containerManager, "fdictGetContainerStatus", fdictAnswer)


@pytest.fixture
def clientBuild():
    app = FastAPI()
    buildRoutes.fnRegisterAll(app, {"require": lambda *aArgs: None})
    return TestClient(app)


def testAcquireRefusesTheAgentLane(clientBuild, tmp_path):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/acquire-image",
        headers={"X-Vaibify-Session": "agentTokenValue"},
    )
    assert responseHttp.status_code == 403
    assert "agent" in responseHttp.json()["detail"]
    assert "agentTokenValue" not in responseHttp.text


def testAcquireOfABuiltProjectSaysThereIsNothingToObtain(
    clientBuild, tmp_path,
):
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/acquire-image",
    )
    assert responseHttp.status_code == 409
    assert "no pinned image to obtain" in (
        responseHttp.json()["detail"]["sMessage"]
    )


def testAcquireRefusesWhenTheDaemonCannotBeAsked(
    clientBuild, tmp_path, monkeypatch,
):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    listQueried = []
    fnSetContainerStatus(
        monkeypatch, OSError("daemon socket gone"), listQueried,
    )
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/acquire-image",
    )
    assert responseHttp.status_code == 409
    dictDetail = responseHttp.json()["detail"]
    assert "could not be asked" in dictDetail["sMessage"]
    assert dictDetail["sAction"] == "stop-first"
    assert listQueried == [S_CONTAINER_NAME]
    assert S_PROJECT_NAME not in buildRoutes._DICT_BUILD_PROGRESS


def testAcquireRefusesWhileTheContainerExists(
    clientBuild, tmp_path, monkeypatch,
):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    fnSetContainerStatus(
        monkeypatch,
        {"bExists": True, "bRunning": False, "sStatus": "exited"},
    )
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/acquire-image",
    )
    assert responseHttp.status_code == 409
    dictDetail = responseHttp.json()["detail"]
    assert "(exited)" in dictDetail["sMessage"]
    assert dictDetail["sAction"] == "stop-first"


def testAcquireSuccessRecordsLinesAndReturnsTheOrigin(
    clientBuild, tmp_path, monkeypatch,
):
    from vaibify.cli import configLoader
    from vaibify.docker import pinnedImageAcquisition
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    listCalls = []

    def fdictFakeAcquire(
        dictProject, bAllowEmulation, fnLine, sDockerDir, bWithoutAdditions,
    ):
        listCalls.append(
            (dictProject["sContainerName"], bAllowEmulation,
             sDockerDir, bWithoutAdditions),
        )
        fnLine("pulling layer one\n")
        return {"sRunningImageId": "sha256:imageRunning"}

    monkeypatch.setattr(
        pinnedImageAcquisition, "fdictAcquireForProject", fdictFakeAcquire,
    )
    monkeypatch.setattr(configLoader, "fsDockerDir", lambda: "dockerDirectory")
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/acquire-image"
        "?bAllowEmulation=true&bWithoutAdditions=true",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["dictImageOrigin"] == {
        "sRunningImageId": "sha256:imageRunning",
    }
    assert listCalls == [
        (S_CONTAINER_NAME, True, "dockerDirectory", True),
    ]
    dictProgress = clientBuild.get(
        f"/api/containers/{S_PROJECT_NAME}/build/progress",
    ).json()
    assert dictProgress["sOutcome"] == "succeeded"
    assert dictProgress["bLive"] is False
    assert dictProgress["saTailLines"] == ["pulling layer one"]


def testAcquireFailureClosesTheRecordAndCarriesTheAction(
    clientBuild, tmp_path, monkeypatch,
):
    from vaibify.docker import pinnedImageAcquisition
    fdictWriteProjectEntry(tmp_path, bObtained=True)

    class AcquisitionRefusal(RuntimeError):
        sAction = "retry-without-additions"

    def fnFailingAcquire(*aArgs, **dictArgs):
        raise AcquisitionRefusal("baseline overlays could not be proven")

    monkeypatch.setattr(
        pinnedImageAcquisition, "fdictAcquireForProject", fnFailingAcquire,
    )
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/acquire-image",
    )
    assert responseHttp.status_code == 500
    dictDetail = responseHttp.json()["detail"]
    assert dictDetail["sAction"] == "retry-without-additions"
    assert "baseline overlays could not be proven" in dictDetail["sError"]
    dictProgress = buildRoutes._DICT_BUILD_PROGRESS[S_PROJECT_NAME]
    assert dictProgress["bLive"] is False
    assert dictProgress["sOutcome"] == "failed"
    assert list(dictProgress["dequeTail"]) == [
        "error: baseline overlays could not be proven",
    ]


def testSwitchToBuildingOfABuiltProjectIsANoOp(clientBuild, tmp_path):
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-building",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSwitched"] is False
    assert "already builds" in responseHttp.json()["sMessage"]


def testSwitchToBuildingRefusesWhileABuildIsLive(clientBuild, tmp_path):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    buildRoutes._fdictOpenBuildProgress(S_PROJECT_NAME)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-building",
    )
    assert responseHttp.status_code == 409
    assert "already running" in responseHttp.json()["detail"]["sMessage"]
    assert "dictImageSource" in fdictReadRegistryEntry()


def testSwitchToBuildingClearsTheSourceOnceTheContainerIsGone(
    clientBuild, tmp_path, monkeypatch,
):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    fnSetContainerStatus(
        monkeypatch, {"bExists": False, "bRunning": False, "sStatus": ""},
    )
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-building",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json() == {
        "bSwitched": True,
        "sBuildPath": f"/api/containers/{S_PROJECT_NAME}/build",
    }
    assert "dictImageSource" not in fdictReadRegistryEntry()


def testSwitchToBuildingRefusesTheAgentLane(clientBuild, tmp_path):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-building",
        headers={"X-Vaibify-Session": "agentTokenValue"},
    )
    assert responseHttp.status_code == 403
    assert "dictImageSource" in fdictReadRegistryEntry()


def testSwitchToPinnedOfAnObtainedProjectPointsAtAcquire(
    clientBuild, tmp_path,
):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-pinned-image",
    )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bSwitched"] is False
    assert dictBody["sAcquirePath"] == (
        f"/api/containers/{S_PROJECT_NAME}/acquire-image"
    )


def testSwitchToPinnedRefusesWhileTheContainerExists(
    clientBuild, tmp_path, monkeypatch,
):
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    fnSetContainerStatus(
        monkeypatch,
        {"bExists": True, "bRunning": True, "sStatus": "running"},
    )
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-pinned-image",
    )
    assert responseHttp.status_code == 409
    assert "Switching to the pinned image" in (
        responseHttp.json()["detail"]["sMessage"]
    )
    assert "dictImageSource" not in fdictReadRegistryEntry()


def testSwitchToPinnedWritesTheSourceTheConversionBuilds(
    clientBuild, tmp_path, monkeypatch,
):
    from vaibify.gui import pinnedEnvironmentConversion
    fdictWriteProjectEntry(tmp_path, bObtained=False)
    listEmulationFlags = []

    def fdictFakeSource(dictProject, bAllowEmulation):
        listEmulationFlags.append(bAllowEmulation)
        return {"sSource": "archive", "sReference": "registry.example/a@y"}

    monkeypatch.setattr(
        pinnedEnvironmentConversion,
        "fdictBuildArchiveImageSourceForSwitch", fdictFakeSource,
    )
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/switch-to-pinned-image"
        "?bAllowEmulation=true",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["bSwitched"] is True
    assert listEmulationFlags == [True]
    assert fdictReadRegistryEntry()["dictImageSource"] == {
        "sSource": "archive", "sReference": "registry.example/a@y",
    }


def testUnreadableConfigurationPassesThePreflight(
    clientBuild, tmp_path, monkeypatch,
):
    """An unreadable vaibify.yml is the build's failure, not a preflight's."""
    from vaibify.cli import configLoader
    fdictWriteProjectEntry(tmp_path, bObtained=False)

    def fnRaiseUnreadable(sPath):
        raise ValueError("unreadable vaibify.yml")

    monkeypatch.setattr(configLoader, "fconfigLoadFromPath", fnRaiseUnreadable)
    responseHttp = clientBuild.post(
        f"/api/containers/{S_PROJECT_NAME}/build?bPreflightOnly=true",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bReady": True}
    assert S_PROJECT_NAME not in buildRoutes._DICT_BUILD_PROGRESS


def testUnknownProjectIsA404ThatNamesIt(clientBuild, tmp_path):
    fdictWriteProjectEntry(tmp_path, bObtained=True)
    responseHttp = clientBuild.post(
        "/api/containers/projectMissing/switch-to-building",
    )
    assert responseHttp.status_code == 404
    assert "projectMissing" in responseHttp.json()["detail"]
    assert str(tmp_path) not in responseHttp.text


def testANotCheckedPreflightIsLoggedNeverRefused(
    clientBuild, tmp_path, caplog,
):
    """An index that could not be asked is not a reason to refuse a build."""
    dictEntry = fdictWriteProjectEntry(tmp_path, bObtained=False)
    with open(dictEntry["sConfigPath"], "w") as fileHandle:
        fileHandle.write(
            f"projectName: {S_PROJECT_NAME}\n"
            "pythonPackages:\n  - packageAlpha\n"
            "pipInstallFlags: --index-url https://index.example/simple\n"
        )
    with caplog.at_level("INFO", logger="vaibify.gui.buildRoutes"):
        responseHttp = clientBuild.post(
            f"/api/containers/{S_PROJECT_NAME}/build?bPreflightOnly=true",
        )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json() == {"bReady": True}
    assert any(
        "were not checked" in recordLog.getMessage()
        for recordLog in caplog.records
    )
