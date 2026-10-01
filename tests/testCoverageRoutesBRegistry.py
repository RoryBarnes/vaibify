"""Coverage of the refusal and edge branches in ``registryRoutes``.

Every route here is driven over real HTTP against a registry file, a
journal and a lock directory that all live under ``tmp_path``; the
host project's registry NAME is kept distinct from both its new name
and any container id, so a handler that read one where it meant the
other could not pass by coincidence. Only true boundaries are doubled:
the Docker daemon (a fake connection) and the duplicate-name ``docker``
probe. The journal, the flock question, the reconciliation proof and
real ``git`` all run for real.
"""

import json
import os
import subprocess

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import registryManager
from vaibify.config.operationJournal import (
    fdictReadJournalOutcome,
    fnMarkOperationNeedsReconciliation,
    fnPromoteOperationToInFlight,
    fsPrepareOperation,
)
from vaibify.gui import containerOwnership, registryRoutes


S_HOST_NAME = "sandboxAlpha"
S_NEW_NAME = "project-beta"
S_CONTAINER_PROJECT = "project-gamma"
S_CONTAINER_ID = "c0ffee00ddba11"
S_AGENT_HEADER = {"X-Vaibify-Session": "agentTokenValue"}


@pytest.fixture(autouse=True)
def fixtureIsolateRegistryAndHome(tmp_path, monkeypatch):
    """Point the registry, ~ and the docker name probe at ``tmp_path``."""
    sHome = str(tmp_path / "home")
    os.makedirs(sHome, exist_ok=True)
    monkeypatch.setenv("HOME", sHome)
    sRegistryDirectory = os.path.join(sHome, ".vaibify")
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
    monkeypatch.setattr(
        registryRoutes, "_fbDockerContainerExists", lambda sName: False,
    )
    return sHome


def fsRegisterProject(sHome, sProjectName, sMode):
    """Create a project directory with a vaibify.yml and register it."""
    sDirectory = os.path.join(sHome, "projects", sProjectName)
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sDirectory, sMode=sMode)
    return sDirectory


def fsReadConfigText(sDirectory):
    with open(os.path.join(sDirectory, "vaibify.yml")) as fileConfig:
        return fileConfig.read()


def fappBuildRegistry(connectionDocker=None):
    """A bare hub app carrying only the registry routes."""
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *aArgs: None, "docker": connectionDocker}
    registryRoutes.fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def tupleHostClient(fixtureIsolateRegistryAndHome):
    """A hub serving one host sandbox; returns (client, app, directory)."""
    sDirectory = fsRegisterProject(
        fixtureIsolateRegistryAndHome, S_HOST_NAME, "host",
    )
    app = fappBuildRegistry()
    return TestClient(app), app, sDirectory


def fnRunGit(sDirectory, *listArguments):
    subprocess.run(
        ["git", *listArguments], cwd=sDirectory, check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------
# Project git remote
# ---------------------------------------------------------------------


def testGitRemoteReadReturnsTheOriginOfTheProjectRepository(tupleHostClient):
    clientHub, _app, sDirectory = tupleHostClient
    fnRunGit(sDirectory, "init", "-q")
    fnRunGit(
        sDirectory, "remote", "add", "origin",
        "https://forge.example/owner/repositoryAlpha.git",
    )
    responseHttp = clientHub.get(f"/api/registry/{S_HOST_NAME}/git-remote")
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["sRemoteUrl"] == (
        "https://forge.example/owner/repositoryAlpha.git"
    )


def testGitRemoteReadOfAVanishedDirectoryIsA404(tupleHostClient):
    clientHub, _app, sDirectory = tupleHostClient
    os.remove(os.path.join(sDirectory, "vaibify.yml"))
    os.rmdir(sDirectory)
    responseHttp = clientHub.get(f"/api/registry/{S_HOST_NAME}/git-remote")
    assert responseHttp.status_code == 404
    assert "no longer exists" in responseHttp.json()["detail"]
    assert sDirectory not in responseHttp.text


def testGitRemoteReadRefusesTheAgentLane(tupleHostClient):
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.get(
        f"/api/registry/{S_HOST_NAME}/git-remote", headers=S_AGENT_HEADER,
    )
    assert responseHttp.status_code == 403
    assert "agentTokenValue" not in responseHttp.text


def testGitRemoteSetWritesTheOriginAndReadsItBack(tupleHostClient):
    clientHub, _app, sDirectory = tupleHostClient
    fnRunGit(sDirectory, "init", "-q")
    responseHttp = clientHub.post(
        f"/api/registry/{S_HOST_NAME}/git-remote",
        json={"sRemoteUrl": "https://forge.example/owner/repositoryBeta.git"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json() == {
        "bSuccess": True,
        "sRemoteUrl": "https://forge.example/owner/repositoryBeta.git",
    }
    processRead = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=sDirectory,
        capture_output=True, text=True,
    )
    assert processRead.stdout.strip() == (
        "https://forge.example/owner/repositoryBeta.git"
    )


def testGitRemoteSetRefusesAnOptionShapedUrl(tupleHostClient):
    """A value git would read as a flag is argument injection."""
    clientHub, _app, sDirectory = tupleHostClient
    fnRunGit(sDirectory, "init", "-q")
    responseHttp = clientHub.post(
        f"/api/registry/{S_HOST_NAME}/git-remote",
        json={"sRemoteUrl": "--upload-pack=touch injected"},
    )
    assert responseHttp.status_code == 400
    assert "begin with '-'" in responseHttp.json()["detail"]
    assert not os.path.exists(os.path.join(sDirectory, "injected"))


def testGitRemoteSetOnANonRepositoryIsA500NamingTheCause(tupleHostClient):
    clientHub, _app, sDirectory = tupleHostClient
    responseHttp = clientHub.post(
        f"/api/registry/{S_HOST_NAME}/git-remote",
        json={"sRemoteUrl": "https://forge.example/owner/repositoryBeta.git"},
    )
    assert responseHttp.status_code == 500
    assert "is a git repository" in responseHttp.json()["detail"]
    assert sDirectory not in responseHttp.text


def testGitRemoteSetRefusesTheAgentLane(tupleHostClient):
    clientHub, _app, sDirectory = tupleHostClient
    fnRunGit(sDirectory, "init", "-q")
    responseHttp = clientHub.post(
        f"/api/registry/{S_HOST_NAME}/git-remote",
        json={"sRemoteUrl": "https://forge.example/owner/repositoryBeta.git"},
        headers=S_AGENT_HEADER,
    )
    assert responseHttp.status_code == 403
    processRead = subprocess.run(
        ["git", "remote"], cwd=sDirectory, capture_output=True, text=True,
    )
    assert processRead.stdout.strip() == ""


# ---------------------------------------------------------------------
# Create a host folder
# ---------------------------------------------------------------------


def testCreateHostFolderRefusesADotDotName(tupleHostClient, tmp_path):
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.post(
        "/api/host-directories/create",
        json={"sParentPath": os.environ["HOME"], "sFolderName": ".."},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == "Invalid folder name"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permission bits",
)
def testCreateHostFolderInAnUnwritableParentIsA403(tupleHostClient):
    clientHub, _app, _sDirectory = tupleHostClient
    sParent = os.path.join(os.environ["HOME"], "readOnlyParent")
    os.makedirs(sParent)
    os.chmod(sParent, 0o500)
    try:
        responseHttp = clientHub.post(
            "/api/host-directories/create",
            json={"sParentPath": sParent, "sFolderName": "folderAlpha"},
        )
    finally:
        os.chmod(sParent, 0o700)
    assert responseHttp.status_code == 403
    assert responseHttp.json()["detail"] == "Cannot create directory"
    assert not os.path.exists(os.path.join(sParent, "folderAlpha"))


# ---------------------------------------------------------------------
# Pinned environment
# ---------------------------------------------------------------------


def testPinnedEnvironmentOfACloneWithNoEnvelopeIsNotObtainable(
    tupleHostClient,
):
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.get(
        f"/api/registry/{S_HOST_NAME}/pinned-environment",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["bObtainable"] is False
    assert dictBody["sRefusal"]
    assert dictBody["dictDaemon"]["bReachable"] is False


def testPinnedEnvironmentRefusesTheAgentLane(tupleHostClient):
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.get(
        f"/api/registry/{S_HOST_NAME}/pinned-environment",
        headers=S_AGENT_HEADER,
    )
    assert responseHttp.status_code == 403
    assert "host state" in responseHttp.json()["detail"]


# ---------------------------------------------------------------------
# Convert to container and promote to a host Project
# ---------------------------------------------------------------------


def fsConvertUrl(sName):
    return f"/api/registry/{sName}/convert-to-container"


def fsPromoteUrl(sName):
    return f"/api/registry/{sName}/promote-to-host-project"


def testConvertRefusesWhileAnotherSessionHoldsTheProject(tupleHostClient):
    clientHub, app, sDirectory = tupleHostClient
    app.state.dictContainerOwners[S_HOST_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId="leaseOfAnotherTab", fileHandleLock=None,
        )
    )
    sConfigBefore = fsReadConfigText(sDirectory)
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 409
    assert "open in a browser session" in (
        responseHttp.json()["detail"]["sMessage"]
    )
    assert "leaseOfAnotherTab" not in responseHttp.text
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert fsReadConfigText(sDirectory) == sConfigBefore


def testPromoteRefusesOverAnUnsettledJournal(tupleHostClient, monkeypatch):
    clientHub, _app, sDirectory = tupleHostClient
    monkeypatch.setattr(
        registryRoutes, "F_JOURNAL_SETTLE_DEADLINE_SECONDS", 0.0,
    )
    sOperationId = fsPrepareOperation(S_HOST_NAME, "terminal", S_HOST_NAME)
    fnPromoteOperationToInFlight(S_HOST_NAME, sOperationId, {
        "sDockerExecId": "execAlpha", "iHolderProcessGroup": 4242,
    })
    fnMarkOperationNeedsReconciliation(
        S_HOST_NAME, sOperationId, "containment unproven",
    )
    responseHttp = clientHub.post(
        fsPromoteUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 409
    sMessage = responseHttp.json()["detail"]["sMessage"]
    assert "not settled" in sMessage
    assert "before you promote it" in sMessage
    assert registryManager.fdictGetProject(S_HOST_NAME) is not None
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
    assert f"projectName: {S_HOST_NAME}" in fsReadConfigText(sDirectory)


def testConvertRefusesANameAnotherProjectAlreadyHolds(
    tupleHostClient, fixtureIsolateRegistryAndHome,
):
    clientHub, _app, _sDirectory = tupleHostClient
    fsRegisterProject(
        fixtureIsolateRegistryAndHome, S_CONTAINER_PROJECT, "container",
    )
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME), json={"sProjectName": S_CONTAINER_PROJECT},
    )
    assert responseHttp.status_code == 409
    assert f"'{S_CONTAINER_PROJECT}' is already registered" in (
        responseHttp.json()["detail"]
    )
    assert registryManager.fbIsHostProject(S_HOST_NAME)


def testConvertWithAnInjectingRepositoryIsRefusedAndStrandsNothing(
    tupleHostClient,
):
    """A config no later load could open is never written."""
    clientHub, _app, sDirectory = tupleHostClient
    sConfigBefore = fsReadConfigText(sDirectory)
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME),
        json={
            "sProjectName": S_NEW_NAME,
            "listRepositories": ["ext::sh -c touch$IFS/injected"],
        },
    )
    assert responseHttp.status_code == 400
    assert "converted configuration is invalid" in (
        responseHttp.json()["detail"]
    )
    assert fsReadConfigText(sDirectory) == sConfigBefore
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testConvertToAnArchiveWithNoPinnedImageIsRefusedByName(tupleHostClient):
    clientHub, _app, sDirectory = tupleHostClient
    sConfigBefore = fsReadConfigText(sDirectory)
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME),
        json={"sProjectName": S_NEW_NAME, "sEnvironmentSource": "archive"},
    )
    assert responseHttp.status_code == 409
    assert "does not pin an image vaibify can obtain" in (
        responseHttp.json()["detail"]["sMessage"]
    )
    assert fsReadConfigText(sDirectory) == sConfigBefore
    assert registryManager.fbIsHostProject(S_HOST_NAME)


def ffnRemoveEntryThenCall(fnWriter, sRemovedName):
    """Model a concurrent CLI ``remove`` landing just before the writer."""

    def fnRacingWriter(*listArguments):
        registryManager.fnRemoveProject(sRemovedName)
        return fnWriter(*listArguments)

    return fnRacingWriter


def testConvertWhoseEntryVanishedMidRequestIsA404(
    tupleHostClient, monkeypatch,
):
    clientHub, _app, _sDirectory = tupleHostClient
    monkeypatch.setattr(
        registryManager, "fnConvertProjectToContainer",
        ffnRemoveEntryThenCall(
            registryManager.fnConvertProjectToContainer, S_HOST_NAME,
        ),
    )
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 404
    assert S_HOST_NAME in responseHttp.json()["detail"]
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testPromoteWhoseEntryVanishedMidRequestIsA404(
    tupleHostClient, monkeypatch,
):
    clientHub, _app, _sDirectory = tupleHostClient
    monkeypatch.setattr(
        registryManager, "fnPromoteHostProject",
        ffnRemoveEntryThenCall(
            registryManager.fnPromoteHostProject, S_HOST_NAME,
        ),
    )
    responseHttp = clientHub.post(
        fsPromoteUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 404
    assert S_HOST_NAME in responseHttp.json()["detail"]
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testPromoteOfAnAlreadyBrokenConfigIsRefusedAndNotRenamed(
    tupleHostClient,
):
    """An invalid file is never stranded under a new name."""
    clientHub, _app, sDirectory = tupleHostClient
    sBroken = f"projectName: {S_HOST_NAME}\npackageManager: bogusManager\n"
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(sBroken)
    responseHttp = clientHub.post(
        fsPromoteUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 400
    assert "promoted configuration is invalid" in (
        responseHttp.json()["detail"]
    )
    assert fsReadConfigText(sDirectory) == sBroken
    assert registryManager.fdictGetProject(S_HOST_NAME) is not None
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testPromoteWhoseWorkflowCannotBeWrittenIsA500AndRenamesNothing(
    tupleHostClient,
):
    clientHub, _app, sDirectory = tupleHostClient
    os.makedirs(os.path.join(sDirectory, ".vaibify"))
    with open(os.path.join(sDirectory, ".vaibify", "projects"), "w"):
        pass
    responseHttp = clientHub.post(
        fsPromoteUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 500
    sDetail = responseHttp.json()["detail"]
    assert "Could not create the Project's workflow file" in sDetail
    assert "Nothing was renamed" in sDetail
    assert registryManager.fdictGetProject(S_HOST_NAME) is not None
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
    assert f"projectName: {S_HOST_NAME}" in fsReadConfigText(sDirectory)


# ---------------------------------------------------------------------
# Reconcile a quarantine this hub holds
# ---------------------------------------------------------------------


class FakeConnectionGroupProbe:
    """Exec settled; the group probe answers a configured member count."""

    def __init__(self, iMemberCount):
        self.iMemberCount = iMemberCount

    def fdictInspectExec(self, sDockerExecId):
        del sDockerExecId
        return {"Running": False}

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        del sContainerId, iProcessGroup
        return {
            "bConclusive": True, "iMemberCount": self.iMemberCount,
            "sDetail": f"{self.iMemberCount} live member(s)",
        }


def fsJournalTerminalRecord():
    """Journal a NEEDS_RECONCILIATION terminal record; return its id."""
    sOperationId = fsPrepareOperation(
        S_CONTAINER_PROJECT, "terminal", S_CONTAINER_ID,
    )
    fnPromoteOperationToInFlight(S_CONTAINER_PROJECT, sOperationId, {
        "sDockerExecId": "execBeta",
        "sDockerContainerId": S_CONTAINER_ID,
        "iHolderProcessGroup": 2126,
    })
    fnMarkOperationNeedsReconciliation(
        S_CONTAINER_PROJECT, sOperationId, "containment unproven",
    )
    return sOperationId


def ftupleHeldHub(tmp_path, iMemberCount):
    """A hub whose owner record holds the project's flock handle."""
    app = fappBuildRegistry(FakeConnectionGroupProbe(iMemberCount))
    fileHandleLock = open(str(tmp_path / "heldFlock.lock"), "w")
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="leaseHeld", fileHandleLock=fileHandleLock,
        sContainerId=S_CONTAINER_ID,
    )
    app.state.dictContainerOwners[S_CONTAINER_PROJECT] = recordOwner
    return TestClient(app), recordOwner, fileHandleLock


def fbJournalHasRecord(sOperationId):
    dictOutcome = fdictReadJournalOutcome(S_CONTAINER_PROJECT)
    return sOperationId in (dictOutcome.get("dictOperations") or {})


def testHeldReconcileProvesClearsPoisonAndTheRecord(tmp_path):
    sOperationId = fsJournalTerminalRecord()
    clientHub, recordOwner, fileHandleLock = ftupleHeldHub(tmp_path, 0)
    recordOwner.poison = object()
    try:
        responseHttp = clientHub.post(
            f"/api/registry/{S_CONTAINER_PROJECT}/reconcile",
            json={"listExpectedOperationIds": [sOperationId]},
        )
    finally:
        fileHandleLock.close()
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["bReconciled"] is True
    assert recordOwner.poison is None
    assert not fbJournalHasRecord(sOperationId)


def testHeldReconcileOverALiveProcessIsRefusedAndKeepsEverything(tmp_path):
    sOperationId = fsJournalTerminalRecord()
    clientHub, recordOwner, fileHandleLock = ftupleHeldHub(tmp_path, 1)
    objectPoison = object()
    recordOwner.poison = objectPoison
    try:
        responseHttp = clientHub.post(
            f"/api/registry/{S_CONTAINER_PROJECT}/reconcile",
            json={"listExpectedOperationIds": [sOperationId]},
        )
    finally:
        fileHandleLock.close()
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]
    assert recordOwner.poison is objectPoison
    assert fbJournalHasRecord(sOperationId)


# ---------------------------------------------------------------------
# Claim-time helpers over a Docker double
# ---------------------------------------------------------------------


class FakeConnectionRunningContainers:
    """Answer the running-container listing, or fail like a lost daemon."""

    def __init__(self, listRows=None, bRaise=False):
        self.listRows = listRows or []
        self.bRaise = bRaise

    def flistGetRunningContainers(self):
        if self.bRaise:
            raise ConnectionError("daemon socket reset")
        return list(self.listRows)


class FakeRunningTask:
    def done(self):
        return False


def testResolveContainerIdReturnsTheIdNotTheName(tupleHostClient):
    connectionDocker = FakeConnectionRunningContainers([
        {"sName": "projectOther", "sContainerId": "0therc0ntainer"},
        {"sName": S_CONTAINER_PROJECT, "sContainerId": S_CONTAINER_ID},
    ])
    assert registryRoutes._fsResolveContainerId(
        {"docker": connectionDocker}, S_CONTAINER_PROJECT,
    ) == S_CONTAINER_ID


def testResolveContainerIdOfAFailingDaemonIsEmpty(tupleHostClient):
    assert registryRoutes._fsResolveContainerId(
        {"docker": FakeConnectionRunningContainers(bRaise=True)},
        S_CONTAINER_PROJECT,
    ) == ""


def testResolveContainerIdOfAHostProjectIsItsName(tupleHostClient):
    assert registryRoutes._fsResolveContainerId(
        {"docker": FakeConnectionRunningContainers(bRaise=True)},
        S_HOST_NAME,
    ) == S_HOST_NAME


def testRunningPipelineIsLookedUpByContainerId(tupleHostClient):
    """The live run is keyed by the Docker id, never the project name."""
    connectionDocker = FakeConnectionRunningContainers([
        {"sName": S_CONTAINER_PROJECT, "sContainerId": S_CONTAINER_ID},
    ])
    dictCtx = {
        "docker": connectionDocker,
        "pipelineTasks": {
            S_CONTAINER_ID: {"/workspace/repositoryAlpha": FakeRunningTask()},
        },
    }
    assert registryRoutes._fbNameHasRunningPipeline(
        dictCtx, object(), S_CONTAINER_PROJECT,
    ) is True
    dictCtxKeyedByName = dict(dictCtx, pipelineTasks={
        S_CONTAINER_PROJECT: {"/workspace/repositoryAlpha": FakeRunningTask()},
    })
    assert registryRoutes._fbNameHasRunningPipeline(
        dictCtxKeyedByName, object(), S_CONTAINER_PROJECT,
    ) is False


def testRunningPipelineCheckIsBusyWhenTheListingRaisesMidQuery(
    tupleHostClient,
):
    """An error while listing running containers keeps the owner in place.

    This is the one fail-safe leg. The check is NOT busy when no daemon
    is present at all; that fail-open leg is pinned separately below.
    """
    assert registryRoutes._fbNameHasRunningPipeline(
        {"docker": FakeConnectionRunningContainers(bRaise=True)},
        object(), S_CONTAINER_PROJECT,
    ) is True


class FakeConnectionWithNoDaemonLeg(FakeConnectionRunningContainers):
    """A router whose Docker leg is absent, as when the daemon is down."""

    def fbDockerLegPresent(self):
        return False


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG: registryRoutes._fbNameHasRunningPipeline documents that a "
    "Docker outage fails safe to busy, but returns False when no daemon "
    "is reachable, so a take-over proceeds over a run it cannot see."
))
@pytest.mark.parametrize("dictCtx", [
    {"docker": None},
    {"docker": FakeConnectionWithNoDaemonLeg()},
], ids=["noConnection", "daemonLegAbsent"])
def testRunningPipelineCheckIsBusyWhenNoDaemonCanBeAsked(
    tupleHostClient, dictCtx,
):
    assert registryRoutes._fbNameHasRunningPipeline(
        dictCtx, object(), S_CONTAINER_PROJECT,
    ) is True


def testResolveContainerIdOfAStoppedProjectIsEmpty(tupleHostClient):
    connectionDocker = FakeConnectionRunningContainers([
        {"sName": "projectOther", "sContainerId": "0therc0ntainer"},
    ])
    assert registryRoutes._fsResolveContainerId(
        {"docker": connectionDocker}, S_CONTAINER_PROJECT,
    ) == ""


def testAStoppedContainerHasNoRunningPipeline(tupleHostClient):
    """A container absent from the running list cannot be mid-run."""
    dictCtx = {
        "docker": FakeConnectionRunningContainers([
            {"sName": "projectOther", "sContainerId": S_CONTAINER_ID},
        ]),
        "pipelineTasks": {
            S_CONTAINER_ID: {"/workspace/repositoryAlpha": FakeRunningTask()},
        },
    }
    assert registryRoutes._fbNameHasRunningPipeline(
        dictCtx, object(), S_CONTAINER_PROJECT,
    ) is False


# ---------------------------------------------------------------------
# Refusals that name another holder of the new name or the project
# ---------------------------------------------------------------------


def ffnRegisterNameThenCall(fnWriter, sHome, sTakenName):
    """Model a concurrent ``vaibify add`` taking the new name mid-request."""

    def fnRacingWriter(*listArguments):
        fsRegisterProject(sHome, sTakenName, "container")
        return fnWriter(*listArguments)

    return fnRacingWriter


def testConvertWhoseNewNameWasTakenMidRequestIsA409(
    tupleHostClient, monkeypatch, fixtureIsolateRegistryAndHome,
):
    clientHub, _app, _sDirectory = tupleHostClient
    monkeypatch.setattr(
        registryManager, "fnConvertProjectToContainer",
        ffnRegisterNameThenCall(
            registryManager.fnConvertProjectToContainer,
            fixtureIsolateRegistryAndHome, S_NEW_NAME,
        ),
    )
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 409
    assert S_NEW_NAME in responseHttp.json()["detail"]
    assert registryManager.fbIsHostProject(S_HOST_NAME)


def testPromoteWhoseNewNameWasTakenMidRequestIsA409(
    tupleHostClient, monkeypatch, fixtureIsolateRegistryAndHome,
):
    clientHub, _app, _sDirectory = tupleHostClient
    monkeypatch.setattr(
        registryManager, "fnPromoteHostProject",
        ffnRegisterNameThenCall(
            registryManager.fnPromoteHostProject,
            fixtureIsolateRegistryAndHome, S_NEW_NAME,
        ),
    )
    responseHttp = clientHub.post(
        fsPromoteUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 409
    assert S_NEW_NAME in responseHttp.json()["detail"]
    assert registryManager.fdictGetProject(S_HOST_NAME) is not None


def testConvertRefusesANameADockerContainerAlreadyCarries(
    tupleHostClient, monkeypatch,
):
    clientHub, _app, sDirectory = tupleHostClient
    listAsked = []

    def fbContainerExists(sName):
        listAsked.append(sName)
        return sName == S_NEW_NAME

    monkeypatch.setattr(
        registryRoutes, "_fbDockerContainerExists", fbContainerExists,
    )
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
    )
    assert responseHttp.status_code == 409
    assert (
        f"A Docker container named '{S_NEW_NAME}' already exists"
        in responseHttp.json()["detail"]
    )
    assert listAsked == [S_NEW_NAME]
    assert registryManager.fbIsHostProject(S_HOST_NAME)


S_LOCK_HOLDER_SCRIPT = """
import sys
from vaibify.config import containerLock
containerLock._S_LOCK_DIRECTORY = sys.argv[1]
fileHandle = containerLock.ffileAcquireContainerLock(sys.argv[2], 8123)
sys.stdout.write("held\\n")
sys.stdout.flush()
sys.stdin.read()
"""


def testPromoteRefusesWhileAnotherProcessHoldsTheFlock(tupleHostClient):
    """A second vaibify process is the flock axis of the busy refusal."""
    import sys
    from vaibify.config import containerLock
    clientHub, _app, _sDirectory = tupleHostClient
    processHolder = subprocess.Popen(
        [sys.executable, "-c", S_LOCK_HOLDER_SCRIPT,
         containerLock._S_LOCK_DIRECTORY, S_HOST_NAME],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    try:
        assert processHolder.stdout.readline().strip() == "held"
        responseHttp = clientHub.post(
            fsPromoteUrl(S_HOST_NAME), json={"sProjectName": S_NEW_NAME},
        )
    finally:
        processHolder.stdin.close()
        processHolder.wait(timeout=10)
    assert responseHttp.status_code == 409
    sMessage = responseHttp.json()["detail"]["sMessage"]
    assert "in use by another vaibify session" in sMessage
    assert "then promote it" in sMessage
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
