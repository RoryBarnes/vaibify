"""An image vaibify did not build runs only as the researcher chose.

The launch functions are the guard: an unanswered digest is refused
before any container exists, a restricted answer changes the arguments
that matter (no root, no capabilities, the idle entrypoint), and
credentials are attached only when the answer includes them. The
dashboard prompt and the answer route sit on top of that and are tested
against the real hub application.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.testContainerLifecycleGating import (  # noqa: F401
    appHub,
    fclientAuthenticated,
    fixtureIsolateHostState,
    fnRegisterProject,
)
from vaibify.config import imageTrust, registryManager
from vaibify.gui import actionCatalog, containerOwnership
from vaibify.docker import containerManager
from vaibify.docker.disposableSpecification import (
    LIST_IDLE_COMMAND,
    LIST_IDLE_ENTRYPOINT,
)
from vaibify.reproducibility.dockerfileComposer import S_RECIPE_IMAGE_LABEL

S_PROJECT = "thirdPartyProject"
S_DIGEST = "sha256:" + "a" * 64
S_OTHER_DIGEST = "sha256:" + "c" * 64
S_AGENT_TOKEN = "agent-token-for-the-trust-test"


def fdictImage(sDigest=S_DIGEST, bBuilt=False):
    return {
        "sId": sDigest,
        "dictLabels": {S_RECIPE_IMAGE_LABEL: "abc"} if bBuilt else {},
        "iSizeBytes": 1234, "sUser": "root",
        "listEntrypoint": ["/app/start"],
    }


def fdictProject(sChoice=None, sDigest=S_DIGEST, bCredentials=False,
                 bObtained=False):
    dictProject = {"sName": S_PROJECT}
    if bObtained:
        dictProject["dictImageSource"] = {"sSource": "archive"}
    if sChoice:
        dictProject["dictImageTrust"] = imageTrust.fdictBuildTrustRecord(
            sDigest, sChoice, bCredentials)
    return dictProject


def fconfigAgentProject():
    return SimpleNamespace(
        sProjectName=S_PROJECT, sWorkspaceRoot="/workspace",
        sContainerUser="researcher", listPorts=[], listBindMounts=[],
        listSecrets=[], bNetworkIsolation=False, iCpuLimit=0,
        fMemoryLimitGigabytes=0.0,
        features=SimpleNamespace(
            bGpu=False, bClaude=True, bCodex=False, bGemini=False,
            bOpenCode=False, bCline=False, bOpenHands=False, bPi=False,
            bClaudeAutoUpdate=True,
        ),
    )


@pytest.fixture(autouse=True)
def fixtureNoX11(monkeypatch):
    monkeypatch.setattr(containerManager, "flistConfigureX11Args", lambda: [])


# -- the rule ----------------------------------------------------------

def testAnImageVaibifyBuiltNeverAsks():
    dictPosture = imageTrust.fdictResolveLaunchPosture(
        fdictProject(), fdictImage(bBuilt=True), S_PROJECT)
    assert dictPosture["sMode"] == "built"
    assert dictPosture["bWithCredentials"] is True


@pytest.mark.falsification
def testAnUnlabelledImageCountsAsNotBuiltByVaibify():
    """Kills: reading an image with no build label as built by vaibify."""
    with pytest.raises(imageTrust.ImageTrustRequiredError) as errorRaised:
        imageTrust.fdictResolveLaunchPosture(
            fdictProject(), fdictImage(), S_PROJECT)
    assert errorRaised.value.sImageDigest == S_DIGEST


@pytest.mark.falsification
def testAnObtainedImageIsNotBuiltEvenWhenItCarriesTheLabel():
    """Kills: trusting the build label of an image the project obtained.

    The label travels with the bytes, so an author's image can wear it.
    """
    with pytest.raises(imageTrust.ImageTrustRequiredError):
        imageTrust.fdictResolveLaunchPosture(
            fdictProject(bObtained=True), fdictImage(bBuilt=True), S_PROJECT)


@pytest.mark.falsification
def testANewDigestAsksAgain():
    """Kills: keying the answer on the project instead of the digest."""
    dictProject = fdictProject("restricted", sDigest=S_DIGEST)
    imageTrust.fdictResolveLaunchPosture(
        dictProject, fdictImage(S_DIGEST), S_PROJECT)
    with pytest.raises(imageTrust.ImageTrustRequiredError):
        imageTrust.fdictResolveLaunchPosture(
            dictProject, fdictImage(S_OTHER_DIGEST), S_PROJECT)


def testInspectOnlyHasNoPersistentContainer():
    with pytest.raises(imageTrust.ImageTrustInspectOnlyError):
        imageTrust.fdictResolveLaunchPosture(
            fdictProject("inspect"), fdictImage(), S_PROJECT)


def testAnImageTheDaemonCannotDescribeIsNeverStarted():
    with pytest.raises(imageTrust.ImageProvenanceUnavailableError):
        imageTrust.fdictResolveLaunchPosture(fdictProject(), None, S_PROJECT)


def testInspectOnlyNeverCarriesCredentials():
    assert imageTrust.fdictBuildTrustRecord(
        S_DIGEST, "inspect", True)["bWithCredentials"] is False


# -- the arguments -----------------------------------------------------

def flistArguments(dictPosture, bCreateOnly=False):
    return containerManager.flistBuildRunArgs(
        fconfigAgentProject(), bDetached=not bCreateOnly,
        bCreateOnly=bCreateOnly, dictPosture=dictPosture)


def fdictPosture(sMode, bWithCredentials):
    return {"sMode": sMode, "bWithCredentials": bWithCredentials,
            "sImageDigest": S_DIGEST}


@pytest.mark.falsification
def testRestrictedDropsRootCapabilitiesAndTheImageEntrypoint():
    """Kills: leaving ``--user 0`` and the entrypoint capabilities on a
    restricted launch, which would hand the image root."""
    saArgs = flistArguments(fdictPosture("restricted", False))
    assert "0" not in saArgs[saArgs.index("--user"):saArgs.index("--user") + 2]
    assert saArgs[saArgs.index("--user") + 1] == "1000:1000"
    assert "--cap-add" not in saArgs
    assert saArgs[saArgs.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in saArgs
    assert saArgs[saArgs.index("--entrypoint") + 1] == LIST_IDLE_ENTRYPOINT[0]


def testRestrictedCommandIsTheDisposableLanesIdleCommand():
    saCommand = containerManager._flistAssembleRunCommand(
        fconfigAgentProject(), ["-d"], ["sleep", "infinity"],
        dictPosture=fdictPosture("restricted", False))
    assert saCommand[-len(LIST_IDLE_COMMAND):] == LIST_IDLE_COMMAND
    assert saCommand[-3] == S_PROJECT + ":latest"


def testAsBuiltKeepsTheImageEntrypointAndVaibifysLaunchGrants():
    saArgs = flistArguments(fdictPosture("as-built", False))
    assert saArgs[saArgs.index("--user") + 1] == "0"
    assert "--cap-add" in saArgs
    assert "--entrypoint" not in saArgs


@pytest.mark.falsification
def testCredentialsAreAttachedOnlyWhenTheAnswerIncludesThem():
    """Kills: attaching the credentials volume and agent bridge whatever
    the researcher answered."""
    saWithout = flistArguments(fdictPosture("as-built", False))
    saWith = flistArguments(fdictPosture("as-built", True))
    assert not any("python_keyring" in s for s in saWithout)
    assert "--add-host" not in saWithout
    assert any("python_keyring" in s for s in saWith)
    assert "--add-host" in saWith


@pytest.mark.falsification
def testWithheldCredentialsMountNoSecrets(monkeypatch):
    """Kills: mounting configured secrets into a container whose answer
    withheld credentials."""
    listMounted = []
    monkeypatch.setattr(
        containerManager, "_fnMountSingleSecret",
        lambda *args: listMounted.append(args))
    config = fconfigAgentProject()
    config.listSecrets = [{"name": "token", "method": "file"}]
    with patch(
        "vaibify.config.secretAvailability.flistFindUnresolvableSecrets",
        return_value=[],
    ):
        containerManager.flistMountSecrets(
            config, [], [], fdictPosture("as-built", False))
        assert listMounted == []
        containerManager.flistMountSecrets(
            config, [], [], fdictPosture("as-built", True))
    assert len(listMounted) == 1


# -- the launch functions refuse ---------------------------------------

@pytest.mark.falsification
def testEveryLaunchFunctionRefusesAnUnansweredDigest(monkeypatch):
    """Kills: skipping the posture judgement in a launch function."""
    monkeypatch.setattr(
        containerManager, "fdictInspectImageTag",
        lambda sReference: fdictImage())
    monkeypatch.setattr(
        registryManager, "fdictGetProject", lambda sName: fdictProject())
    monkeypatch.setattr(
        containerManager, "_fsRunDetachedCommand",
        lambda saCommand: pytest.fail("a container was launched"))
    monkeypatch.setattr(
        containerManager, "_fnRunDockerCommand",
        lambda saCommand: pytest.fail("a container was launched"))
    monkeypatch.setattr(
        containerManager, "_fsRunKillableDockerCommand",
        lambda *args, **kwargs: pytest.fail("a container was created"))
    config = fconfigAgentProject()
    sReservation = "0123456789abcdef0123456789abcdef"
    for fnLaunch in (
        lambda: containerManager.fnStartContainer(config, "/d"),
        lambda: containerManager.fsStartContainerDetached(config, "/d"),
        lambda: containerManager.fsCreateContainerForReservation(
            config, sReservation),
        lambda: containerManager.fsRecreateContainerDetachedFromImage(
            config, S_DIGEST),
    ):
        with pytest.raises(imageTrust.ImageTrustRequiredError):
            fnLaunch()


# -- the routes --------------------------------------------------------

@pytest.fixture
def clientHub(appHub, tmp_path, monkeypatch):
    clientOne = fclientAuthenticated(appHub)
    fnRegisterProject(clientOne, tmp_path, S_PROJECT)
    monkeypatch.setattr(
        containerManager, "fdictInspectImageTag",
        lambda sReference: fdictImage())
    return clientOne


def fdictAnswer(sChoice="restricted", bCredentials=False, sDigest=S_DIGEST):
    return {"sImageDigest": sDigest, "sChoice": sChoice,
            "bWithCredentials": bCredentials}


def testThePromptStatesTheImageAndServesTheSharedText(clientHub):
    dictPrompt = clientHub.get(f"/api/registry/{S_PROJECT}/image-trust").json()
    assert dictPrompt["sImageDigest"] == S_DIGEST
    assert dictPrompt["bBuiltByVaibify"] is False
    assert dictPrompt["sDeclaredUser"] == "root"
    assert dictPrompt["listDeclaredEntrypoint"] == ["/app/start"]
    assert dictPrompt["listOptions"] == imageTrust.LIST_TRUST_OPTIONS


@pytest.mark.falsification
def testTheStartRouteAsksBeforeCreatingAnything(clientHub):
    """Kills: dropping the preflight, which would reserve and journal a
    start the launch guard then refuses."""
    responseStart = clientHub.post(f"/api/containers/{S_PROJECT}/start")
    assert responseStart.status_code == 409
    dictDetail = responseStart.json()["detail"]
    assert dictDetail["sAction"] == "confirm-image-trust"
    assert dictDetail["dictImageTrust"]["sImageDigest"] == S_DIGEST


def testAnAnswerIsRecordedPerDigestAndRefusesAStaleOne(clientHub):
    responseStale = clientHub.post(
        f"/api/registry/{S_PROJECT}/image-trust",
        json=fdictAnswer(sDigest=S_OTHER_DIGEST))
    assert responseStale.status_code == 409
    responseBad = clientHub.post(
        f"/api/registry/{S_PROJECT}/image-trust",
        json=fdictAnswer(sChoice="trust-everything"))
    assert responseBad.status_code == 400
    responseGood = clientHub.post(
        f"/api/registry/{S_PROJECT}/image-trust",
        json=fdictAnswer("as-built", True))
    assert responseGood.status_code == 200
    dictStored = registryManager.fdictGetProject(S_PROJECT)["dictImageTrust"]
    assert dictStored["sImageDigest"] == S_DIGEST
    assert dictStored["sChoice"] == "as-built"
    assert dictStored["bWithCredentials"] is True
    assert dictStored["sRecordedIso"]


def testTheAgentLaneCannotGrantItselfTrust(appHub, clientHub):
    """The in-container agent is refused, and nothing is recorded.

    The agent token authorizes only the container whose id its request
    path names; this route names a project, so the request falls to the
    browser lane and is refused for want of a browser credential. The
    container name, id and project name are all different strings.
    """
    appHub.state.dictContainerOwners["someOtherContainer"] = (
        containerOwnership.OwnerRecord(
            sLeaseId="researcher-lease", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId="0123456789ab",
        )
    )
    clientAgent = TestClient(appHub, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    responseAgent = clientAgent.post(
        f"/api/registry/{S_PROJECT}/image-trust",
        json=fdictAnswer("as-built", True))
    assert responseAgent.status_code in (401, 403)
    assert "dictImageTrust" not in registryManager.fdictGetProject(S_PROJECT)
