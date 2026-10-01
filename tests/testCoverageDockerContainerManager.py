"""The container-lifecycle gateway's probes, repairs and launch guard.

Every test replaces ``containerManager``'s ``subprocess`` with a
scripted docker CLI that records each argv and answers by subcommand,
so the assertions are about the exact commands vaibify issues and the
conclusions it draws from the daemon's answers -- including the
answers that are NOT answers (an absent CLI, a timeout, a non-zero
exit), which must never read as "no such container". The registry and
origin records are the real ones, redirected under ``tmp_path``.
Container names, container ids, image ids and reservation ids are all
distinct strings.
"""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.config import imageOrigins, registryManager
from vaibify.config.imageOrigins import (
    fdictBuildOriginRecord,
    fnWriteOriginRecord,
)
from vaibify.config.projectConfig import fconfigFromYamlDict
from vaibify.docker import containerManager


S_PROJECT_NAME = "managedProject"
S_CONTAINER_ID = "4a5b6c7d8e9f"
S_RESERVATION_ID = "0123456789abcdef0123456789abcdef"
S_BASE_ID = "sha256:" + "b" * 64
S_OTHER_ID = "sha256:" + "f" * 64

# conftest answers "no container" for every unit test; the repair tests
# drive the REAL status probe through their own scripted docker.
fdictRealGetContainerStatus = containerManager.fdictGetContainerStatus


class _ScriptedDocker:
    """A docker CLI stand-in: records argv, answers by subcommand."""

    def __init__(self, dictAnswers):
        self.dictAnswers = dictAnswers
        self.listCalls = []

    def run(self, saCommand, **kwargs):
        self.listCalls.append(list(saCommand))
        answerScripted = self.dictAnswers.get(saCommand[1])
        if isinstance(answerScripted, list):
            answerScripted = answerScripted.pop(0)
        if isinstance(answerScripted, BaseException):
            raise answerScripted
        if answerScripted is None:
            answerScripted = (0, "", "")
        iReturnCode, sStdout, sStderr = answerScripted
        return SimpleNamespace(
            returncode=iReturnCode, stdout=sStdout, stderr=sStderr,
        )

    def flistSubcommands(self):
        """Return the docker subcommand of each recorded call, in order."""
        return [saCommand[1] for saCommand in self.listCalls]


def fdockerInstallScripted(monkeypatch, dictAnswers):
    """Install a scripted docker as the module's subprocess."""
    dockerScripted = _ScriptedDocker(dictAnswers)
    monkeypatch.setattr(containerManager, "subprocess", SimpleNamespace(
        run=dockerScripted.run,
        TimeoutExpired=subprocess.TimeoutExpired,
        PIPE=subprocess.PIPE,
    ))
    return dockerScripted


@pytest.fixture(autouse=True)
def fnIsolateRegistryAndOrigins(tmp_path, monkeypatch):
    """Registry and origin records under tmp; X11 probing off the host."""
    sHome = str(tmp_path / "vaibifyHome")
    os.makedirs(sHome)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sHome)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sHome, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", os.path.join(sHome, "registry.lock"),
    )
    sOrigins = os.path.join(sHome, "imageOrigins")
    monkeypatch.setattr(imageOrigins, "fsOriginsDirectory", lambda: (
        os.makedirs(sOrigins, exist_ok=True) or sOrigins
    ))
    monkeypatch.setattr(containerManager, "flistConfigureX11Args", lambda: [])


def fconfigProject():
    """Return a real, validated configuration with no agents enabled."""
    return fconfigFromYamlDict({
        "projectName": S_PROJECT_NAME,
        "features": {"latex": False},
    })


# ---------------------------------------------------------------------
# Terminating a docker CLI process
# ---------------------------------------------------------------------


class _FakeDockerProcess:
    """A child docker process that exits when asked to terminate."""

    def __init__(self):
        self.returncode = None
        self.listSignals = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.listSignals.append("TERM")

    def wait(self, timeout=None):
        self.returncode = -15
        return self.returncode

    def kill(self):
        self.listSignals.append("KILL")


def testAProcessThatHonorsTermIsNeverKilled():
    """A clean exit within the grace window is reported as such."""
    processDocker = _FakeDockerProcess()
    dictOutcome = containerManager.fdictTerminateDockerProcess(
        processDocker, fGraceSeconds=0.01,
    )
    assert dictOutcome == {
        "bExited": True, "bTerminated": True, "bKilled": False,
        "iReturnCode": -15,
    }
    assert processDocker.listSignals == ["TERM"]


# ---------------------------------------------------------------------
# Reservation probes: unanswered is not "none"
# ---------------------------------------------------------------------


@pytest.mark.parametrize("answerScripted", [
    subprocess.TimeoutExpired(["docker"], 10),
    FileNotFoundError("docker"),
    (1, "", "Cannot connect to the Docker daemon"),
])
def testAnUnansweredReservationQueryIsNotAnEmptyAnswer(
    monkeypatch, answerScripted,
):
    """A timeout, a missing CLI or an error is bAnswered False."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {"ps": answerScripted})
    dictFound = containerManager.fdictFindContainersForReservation(
        S_RESERVATION_ID,
    )
    assert dictFound == {"bAnswered": False, "listContainerIds": []}
    assert f"label=vaibify.reservation={S_RESERVATION_ID}" in (
        dockerScripted.listCalls[0]
    )


def testASettlementIsInconclusiveWhenTheConfirmationGoesUnanswered(
    monkeypatch,
):
    """Removing the containers proves nothing until the label reads empty."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "ps": [(0, "aaa111\nbbb222\n", ""), FileNotFoundError("docker")],
        "rm": (0, "", ""),
    })
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=False,
    )
    assert dictSettlement["bConclusive"] is False
    assert dictSettlement["listRemovedContainerIds"] == ["aaa111", "bbb222"]
    assert "stopped answering" in dictSettlement["sDetail"]
    assert ["docker", "rm", "-f", "aaa111"] in dockerScripted.listCalls
    assert ["docker", "rm", "-f", "bbb222"] in dockerScripted.listCalls


# ---------------------------------------------------------------------
# Repairs through the lifecycle gateway
# ---------------------------------------------------------------------


def testARestartRepairRestartsByNameAndReportsNoNewId(monkeypatch):
    """A restart keeps the container, so there is no new id to report."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {"restart": None})
    dictOutcome = containerManager.fdictRepairContainerLifecycle(
        fconfigProject(), S_PROJECT_NAME, "restart",
    )
    assert dictOutcome == {"sOperation": "restart", "sContainerId": ""}
    assert dockerScripted.listCalls == [["docker", "restart", S_PROJECT_NAME]]


def testAFailedRestartRaisesWithTheDaemonsReason(monkeypatch):
    """The caller turns this into a refusal; the reason must survive."""
    fdockerInstallScripted(monkeypatch, {
        "restart": (1, "", "  Error: No such container: managedProject \n"),
    })
    with pytest.raises(RuntimeError) as errorRaised:
        containerManager.fdictRepairContainerLifecycle(
            fconfigProject(), S_PROJECT_NAME, "restart",
        )
    assert str(errorRaised.value) == (
        "docker restart failed: Error: No such container: managedProject"
    )


@pytest.mark.parametrize("sOperation,sImageIdentity,sExpected", [
    ("rebuild", S_BASE_ID, "is not a container repair operation"),
    ("recreate", "", "requires the image identity"),
])
def testAnInvalidRepairIsRefusedBeforeDockerIsAsked(
    monkeypatch, sOperation, sImageIdentity, sExpected,
):
    """No docker command runs for an undeclared or identity-less repair."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {})
    with pytest.raises(ValueError) as errorRaised:
        containerManager.fdictRepairContainerLifecycle(
            fconfigProject(), S_PROJECT_NAME, sOperation, sImageIdentity,
        )
    assert sExpected in str(errorRaised.value)
    assert dockerScripted.listCalls == []


def testARecreateStopsTheOldContainerAndRunsTheImageId(monkeypatch):
    """The relaunch names the image ID, never the project's moving tag."""
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        fdictRealGetContainerStatus,
    )
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "inspect": (0, "exited\n", ""),
        "stop": None,
        "rm": None,
        "run": (0, S_CONTAINER_ID + "\n", ""),
    })
    dictOutcome = containerManager.fdictRepairContainerLifecycle(
        fconfigProject(), S_PROJECT_NAME, "recreate", S_BASE_ID,
    )
    assert dictOutcome == {
        "sOperation": "recreate", "sContainerId": S_CONTAINER_ID,
    }
    assert dockerScripted.flistSubcommands() == ["inspect", "stop", "rm", "run"]
    saRun = dockerScripted.listCalls[-1]
    assert saRun[-3:] == [S_BASE_ID, "sleep", "infinity"]
    assert f"{S_PROJECT_NAME}:latest" not in saRun
    assert "-d" in saRun
    assert saRun[saRun.index("--name") + 1] == S_PROJECT_NAME


def testARecreateOfAnAbsentContainerDoesNotStopAnything(monkeypatch):
    """Nothing to stop means straight to the pinned relaunch."""
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        fdictRealGetContainerStatus,
    )
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "inspect": (1, "", "No such object"),
        "run": (0, S_CONTAINER_ID, ""),
    })
    containerManager.fdictRepairContainerLifecycle(
        fconfigProject(), S_PROJECT_NAME, "recreate", S_BASE_ID,
    )
    assert dockerScripted.flistSubcommands() == ["inspect", "run"]


def testAFailedStopRaisesAndNeverRemoves(monkeypatch):
    """A container that would not stop is not force-removed behind it."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "stop": (1, "", "permission denied\n"),
    })
    with pytest.raises(RuntimeError) as errorRaised:
        containerManager.fnStopContainer(S_PROJECT_NAME)
    assert str(errorRaised.value) == "docker stop failed: permission denied"
    assert dockerScripted.flistSubcommands() == ["stop"]


# ---------------------------------------------------------------------
# Image and daemon reads
# ---------------------------------------------------------------------


def testAnImageTagInspectParsesTheIdAndLabels(monkeypatch):
    """The launch guard judges the ID and labels the daemon holds NOW."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "image": (0, S_BASE_ID + '\t{"vaibify-overlays":"claude"}\n', ""),
    })
    assert containerManager.fdictInspectImageTag("anyRepo:latest") == {
        "sId": S_BASE_ID, "dictLabels": {"vaibify-overlays": "claude"},
    }
    assert dockerScripted.listCalls[0][-1] == "anyRepo:latest"


@pytest.mark.parametrize("sLabelsJson", ["{not json", "null", '["a"]', ""])
def testUnreadableImageLabelsDegradeToNoLabelsButKeepTheId(
    monkeypatch, sLabelsJson,
):
    """A label map that does not parse is empty, never a crash."""
    fdockerInstallScripted(monkeypatch, {
        "image": (0, f"{S_BASE_ID}\t{sLabelsJson}", ""),
    })
    assert containerManager.fdictInspectImageTag("anyRepo:latest") == {
        "sId": S_BASE_ID, "dictLabels": {},
    }


@pytest.mark.parametrize("answerScripted", [
    (0, "   \n", ""), (1, "", "No such image"),
])
def testAnImageTagThatResolvesToNothingIsNone(monkeypatch, answerScripted):
    """Both 'absent' and 'unanswered' read as not-the-recorded-image."""
    fdockerInstallScripted(monkeypatch, {"image": answerScripted})
    assert containerManager.fdictInspectImageTag("anyRepo:latest") is None


@pytest.mark.parametrize("answerScripted,sExpected", [
    ((0, "arm64\n", ""), "arm64"),
    ((1, "", "daemon down"), ""),
    (FileNotFoundError("docker"), ""),
])
def testTheDaemonArchitectureIsReadOrEmpty(
    monkeypatch, answerScripted, sExpected,
):
    """An unanswered daemon yields no architecture, never a guess."""
    dockerScripted = fdockerInstallScripted(
        monkeypatch, {"version": answerScripted},
    )
    assert containerManager.fsReadDaemonArchitectureQuietly() == sExpected
    assert dockerScripted.listCalls[0][:2] == ["docker", "version"]


@pytest.mark.parametrize("answerScripted", [
    FileNotFoundError("docker"),
    subprocess.TimeoutExpired(["docker"], 10),
    (1, "", "No such object"),
    (0, "not json", ""),
    (0, "[]", ""),
    (0, '["a string"]', ""),
])
def testAContainerInspectThatCannotAnswerIsEmpty(monkeypatch, answerScripted):
    """{} is 'unassessed', which callers must not read as 'no DNS set'."""
    fdockerInstallScripted(monkeypatch, {"inspect": answerScripted})
    assert containerManager.fjsonInspectContainer(S_CONTAINER_ID) == {}


def testAContainerInspectReturnsTheFirstObject(monkeypatch):
    """The whole specification of the one container asked about."""
    dictSpecification = {
        "Id": S_CONTAINER_ID, "Name": "/" + S_PROJECT_NAME,
        "HostConfig": {"NetworkMode": "bridge", "Dns": ["10.0.0.1"]},
    }
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "inspect": (0, json.dumps([dictSpecification]), ""),
    })
    assert containerManager.fjsonInspectContainer(
        S_CONTAINER_ID,
    ) == dictSpecification
    assert dockerScripted.listCalls[0] == ["docker", "inspect", S_CONTAINER_ID]


def testNoAgentBridgeForAConfigWithoutFeatures():
    """A config carrying no feature block never opens the host gateway."""
    saRunArgs = []
    containerManager._fnAddAgentHostBridge(
        SimpleNamespace(bNetworkIsolation=False, features=None), saRunArgs,
    )
    assert saRunArgs == []


# ---------------------------------------------------------------------
# The launch guard for an OBTAINED image, driven through real probes
# ---------------------------------------------------------------------


def fnRegisterObtainedProject(tmp_path, bEmulated, sObtainedPlatform):
    """Register a project whose image is obtained, with its origin record."""
    sDirectory = str(tmp_path / "obtainedClone")
    os.makedirs(sDirectory)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileHandle:
        fileHandle.write(f"projectName: {S_PROJECT_NAME}\n")
    registryManager.fnAddProject(sDirectory, sMode="host")
    registryManager.fnConvertProjectToContainer(
        S_PROJECT_NAME, S_PROJECT_NAME, {
            "sSource": "archive", "bAllowEmulation": bEmulated,
            "sPinnedImageReference": "registry.example/p@sha256:" + "a" * 64,
            "sRequiredPlatform": "linux/amd64", "listAuthorOverlays": [],
            "sAuthorRecipeFingerprint": "", "listAdditionalAgents": [],
            "listResolvedOverlays": None,
        },
    )
    fnWriteOriginRecord(S_PROJECT_NAME, fdictBuildOriginRecord(
        "registry.example/p@sha256:" + "a" * 64, S_BASE_ID, S_BASE_ID, [],
        "registry", "", "", "linux/amd64", sObtainedPlatform, bEmulated,
    ))


def testAnEmulatedRecordStartsWithoutAskingTheDaemonArchitecture(
    tmp_path, monkeypatch,
):
    """Emulation was consented to at acquisition; the start requests it."""
    fnRegisterObtainedProject(tmp_path, True, "linux/amd64")
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "image": (0, f"{S_BASE_ID}\t{{}}", ""),
        "version": (0, "arm64", ""),
    })
    saRunArgs = containerManager.flistBuildRunArgs(fconfigProject())
    assert saRunArgs[-2:] == ["--platform", "linux/amd64"]
    assert "version" not in dockerScripted.flistSubcommands()


def testAnUnansweredDaemonArchitectureDoesNotBlockTheStart(
    tmp_path, monkeypatch,
):
    """Unknown is not a mismatch; the recorded platform is still requested."""
    fnRegisterObtainedProject(tmp_path, False, "linux/amd64")
    dockerScripted = fdockerInstallScripted(monkeypatch, {
        "image": (0, f"{S_BASE_ID}\t{{}}", ""),
        "version": (1, "", "daemon down"),
    })
    saRunArgs = containerManager.flistBuildRunArgs(fconfigProject())
    assert saRunArgs[-2:] == ["--platform", "linux/amd64"]
    assert "version" in dockerScripted.flistSubcommands()


def testASwitchedDaemonArchitectureRefusesTheStart(tmp_path, monkeypatch):
    """An un-consented emulation is refused through the real probes."""
    fnRegisterObtainedProject(tmp_path, False, "linux/amd64")
    fdockerInstallScripted(monkeypatch, {
        "image": (0, f"{S_BASE_ID}\t{{}}", ""),
        "version": (0, "arm64\n", ""),
    })
    with pytest.raises(RuntimeError) as errorRaised:
        containerManager.flistBuildRunArgs(fconfigProject())
    assert "emulate an architecture nobody consented to" in str(
        errorRaised.value,
    )


def testTheLiveOriginIsReturnedOnlyWhileTheTagStillResolvesToIt(
    tmp_path, monkeypatch,
):
    """A tag that moved (a manual build) cannot inherit the archive's origin."""
    fnRegisterObtainedProject(tmp_path, False, "linux/amd64")
    fdockerInstallScripted(monkeypatch, {
        "image": [
            (0, f"{S_BASE_ID}\t{{}}", ""),
            (0, f"{S_OTHER_ID}\t{{}}", ""),
        ],
    })
    dictLive = containerManager.fdictLiveImageOriginForProject(S_PROJECT_NAME)
    assert dictLive["sRunningImageId"] == S_BASE_ID
    assert containerManager.fdictLiveImageOriginForProject(
        S_PROJECT_NAME,
    ) is None


def testAProjectThatBuildsItsImageHasNoLiveOriginAndDockerIsNotAsked(
    monkeypatch,
):
    """Only an obtained image has an origin; a built one never inherits it."""
    dockerScripted = fdockerInstallScripted(monkeypatch, {})
    assert containerManager.fdictLiveImageOriginForProject(
        "unregisteredProject",
    ) is None
    assert dockerScripted.listCalls == []
