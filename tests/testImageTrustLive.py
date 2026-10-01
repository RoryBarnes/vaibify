"""The image-trust postures, against a real daemon.

The unit tests assert the argv. Only a real daemon proves what that argv
DOES to a container: that a restricted launch of an image whose utilities
only root can run does not stay running (and is never reported as running),
that the container it left behind really has no capabilities and no
root, and that the same image started as its author built it works.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from tests.liveContainerLabels import flistLabelArguments
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import imageTrust, registryManager
from vaibify.docker import containerManager
from vaibify.gui import startReservation

pytestmark = pytest.mark.docker_live

S_PROJECT = "vaibifytrustlive"
S_RESERVATION = "fedcba9876543210fedcba9876543210"
S_DOCKERFILE = (
    "FROM alpine:3.20\n"
    "RUN adduser -D -u 1000 researcher && chmod 700 /bin/busybox\n"
)


def fsDocker(*listArguments, bCheck=True):
    processDocker = subprocess.run(
        ["docker", *listArguments], capture_output=True, text=True)
    if bCheck:
        assert processDocker.returncode == 0, processDocker.stderr
    return processDocker.stdout.strip()


def fnRemoveEverything():
    subprocess.run(["docker", "rm", "-f", S_PROJECT], capture_output=True)
    subprocess.run(
        ["docker", "rmi", "-f", f"{S_PROJECT}:latest"], capture_output=True)
    subprocess.run(
        ["docker", "volume", "rm", "-f", f"{S_PROJECT}-workspace",
         f"{S_PROJECT}-credentials"], capture_output=True)


def fconfigLive():
    return SimpleNamespace(
        sProjectName=S_PROJECT, sWorkspaceRoot="/workspace",
        sContainerUser="researcher", listPorts=[], listBindMounts=[],
        listSecrets=[], bNetworkIsolation=True, iCpuLimit=1,
        fMemoryLimitGigabytes=0.0,
        features=SimpleNamespace(
            bGpu=False, bClaude=False, bCodex=False, bGemini=False,
            bOpenCode=False, bCline=False, bOpenHands=False, bPi=False,
        ),
    )


@pytest.fixture
def liveImage(tmp_path, monkeypatch):
    fnRequireDaemonReachable()
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": S_PROJECT, "sContainerName": S_PROJECT,
        "sDirectory": str(tmp_path), "sConfigPath": str(tmp_path / "v.yml"),
    }]})
    monkeypatch.setattr(containerManager, "flistConfigureX11Args", lambda: [])
    fnRemoveEverything()
    processBuild = subprocess.run(
        ["docker", "build", "-q", "-t", f"{S_PROJECT}:latest", "-"],
        input=S_DOCKERFILE, capture_output=True, text=True)
    assert processBuild.returncode == 0, processBuild.stderr
    yield fsDocker("image", "inspect", "--format", "{{.Id}}",
                   f"{S_PROJECT}:latest")
    fnRemoveEverything()


def fsCreateLabelled():
    """Create through vaibify's own function, labelled for the live sweep."""
    fnOriginal = containerManager._fsRunKillableDockerCommand

    def fnLabelled(saCommand, fnRegisterProcess=None):
        if saCommand[1] == "create":
            saCommand = saCommand[:2] + flistLabelArguments() + saCommand[2:]
        return fnOriginal(saCommand, fnRegisterProcess)

    containerManager._fsRunKillableDockerCommand = fnLabelled
    try:
        return containerManager.fsCreateContainerForReservation(
            fconfigLive(), S_RESERVATION)
    finally:
        containerManager._fsRunKillableDockerCommand = fnOriginal


def fnAnswer(sDigest, sChoice):
    registryManager.fnRecordImageTrust(
        S_PROJECT, imageTrust.fdictBuildTrustRecord(sDigest, sChoice, False))


def testAnUnansweredDigestCreatesNoContainerOnTheRealDaemon(liveImage):
    with pytest.raises(imageTrust.ImageTrustRequiredError):
        containerManager.fsCreateContainerForReservation(
            fconfigLive(), S_RESERVATION)
    assert fsDocker("ps", "-a", "--filter", f"name={S_PROJECT}", "-q") == ""


@pytest.mark.falsification
def testARestrictedLaunchOfAnImageThatNeedsRootFailsToStartAndIsNeverRunning(
    liveImage,
):
    """Kills: a restricted launch that keeps root, which would keep an
    image whose utilities only root can run alive and report it running."""
    fnAnswer(liveImage, "restricted")
    sContainerId = fsCreateLabelled()
    dictInspect = json.loads(fsDocker("inspect", sContainerId))[0]
    assert dictInspect["Config"]["User"] == "1000:1000"
    assert dictInspect["HostConfig"]["CapDrop"] == ["ALL"]
    assert not dictInspect["HostConfig"]["CapAdd"]
    assert "no-new-privileges" in " ".join(
        dictInspect["HostConfig"]["SecurityOpt"])
    assert dictInspect["Path"] == "/bin/sh"
    containerManager.fnStartCreatedContainer(sContainerId)
    assert containerManager.fbContainerIsRunning(S_PROJECT) is False
    dictState = json.loads(fsDocker("inspect", sContainerId))[0]["State"]
    assert dictState["Running"] is False and dictState["ExitCode"] != 0
    with pytest.raises(RuntimeError) as errorRaised:
        startReservation._fnConfirmIncarnationIsRunning(
            S_PROJECT, sContainerId)
    sShown = str(errorRaised.value)
    assert "not running" in sShown and "started restricted" in sShown


def testTheSameImageStartsAsItsAuthorBuiltIt(liveImage):
    fnAnswer(liveImage, "as-built")
    sContainerId = fsCreateLabelled()
    dictInspect = json.loads(fsDocker("inspect", sContainerId))[0]
    assert dictInspect["Config"]["User"] == "0"
    containerManager.fnStartCreatedContainer(sContainerId)
    assert containerManager.fbContainerIsRunning(S_PROJECT) is True
