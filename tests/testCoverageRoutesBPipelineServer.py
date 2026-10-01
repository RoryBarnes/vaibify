"""Coverage of pipelineServer's refusal, fallback and degrade paths.

Every test here drives a branch whose job is to say NO honestly or to
keep going honestly: a figure fallback that tries to leave the project
root, a run refused because the bound workflow is stale, a durable
dispatch the real carrier will not launch, a connect whose project
file cannot be loaded, a bootstrap with no capability. The Docker
daemon is a fake at the connection boundary; ``fsValidatePathWithinRoot``,
the carrier, the lane-tuple builders and the refusal builders are the
real code. Keys are kept distinct (container name != Docker id) so a
name-versus-id mixup would fail.
"""

import asyncio
import hashlib
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from vaibify.config import registryManager
from vaibify.gui import containerOwnership, pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testPipelineServerFull import MockDockerFull


S_DOCKER_ID = "dockerIdAlpha0001"
S_CONTAINER_NAME = "containerNameAlpha"
S_PROJECT_ROOT = "/workspace"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/pipelineAlpha.json"
S_LEASE_ID = "leaseValueAlpha"
S_AGENT_TOKEN = "agentTokenValueAlpha"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistryAndHome(tmp_path, monkeypatch):
    """Point the project registry and every ~ lookup at ``tmp_path``."""
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
    yield sHome


def fnWriteRegistry(listProjects):
    """Write the project registry file with the given entries."""
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY, exist_ok=True)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileHandle:
        json.dump({"listProjects": listProjects}, fileHandle)


class RecordingFetchConnection:
    """A Docker double whose file fetch fails and records every path."""

    def __init__(self, sFailureText="No such container: dockerIdAlpha0001"):
        self.listFetchedPaths = []
        self.sFailureText = sFailureText

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        self.listFetchedPaths.append(sPath)
        raise RuntimeError(self.sFailureText)


# ---------------------------------------------------------------
# Figure fallback: the fallback path is validated against the root
# ---------------------------------------------------------------


@pytest.mark.parametrize("sWorkdir", [
    "../../etc",
    "/etc",
    "/workspace/../etc",
])
def testFigureFallbackRefusesAWorkdirThatLeavesTheRoot(sWorkdir):
    """A traversal in sWorkdir is refused before the container is read."""
    connectionDocker = RecordingFetchConnection()
    with pytest.raises(HTTPException) as excInfo:
        pipelineServer.fbaFetchFigureWithFallback(
            connectionDocker, S_DOCKER_ID,
            "/workspace/projectAlpha/stepAlpha/figure.pdf",
            "/workspace/projectAlpha", sWorkdir, "passwd",
            sProjectRoot=S_PROJECT_ROOT,
        )
    assert excInfo.value.status_code == 403
    assert excInfo.value.detail == "Path traversal is not permitted"
    assert "/etc" not in excInfo.value.detail
    assert connectionDocker.listFetchedPaths == [
        "/workspace/projectAlpha/stepAlpha/figure.pdf",
    ]


def testFigureFallbackMissAnswers404WithASanitizedCause():
    """A fallback the daemon cannot serve says why, in the user's words."""
    connectionDocker = RecordingFetchConnection()
    with pytest.raises(HTTPException) as excInfo:
        pipelineServer.fbaFetchFigureWithFallback(
            connectionDocker, S_DOCKER_ID,
            "/workspace/projectAlpha/figure.pdf",
            "/workspace/projectAlpha", "stepAlpha", "figure.pdf",
            sProjectRoot=S_PROJECT_ROOT,
        )
    assert excInfo.value.status_code == 404
    assert excInfo.value.detail == (
        "Figure not found: Container not found. It may have stopped."
    )
    assert connectionDocker.listFetchedPaths[-1] == (
        "/workspace/projectAlpha/stepAlpha/figure.pdf"
    )


def testConvertCommandCopiesARasterPlotRatherThanConverting():
    sCommand = pipelineServer._fsBuildConvertCommand(
        "/workspace/plots dir/figure.png", "/workspace/standard", "figure.png",
    )
    assert sCommand.startswith("cp -f ")
    assert "'/workspace/plots dir/figure.png'" in sCommand
    assert "/workspace/standard/figure_standard.png" in sCommand
    assert sCommand.endswith("|| true")
    assert "pdftoppm" not in sCommand


def testInteractiveContextLookupReturnsOnlyThePublishedEntry(monkeypatch):
    dictInteractive = {"sMarker": "published"}
    monkeypatch.setitem(
        pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER,
        S_DOCKER_ID, dictInteractive,
    )
    assert pipelineServer.fdictInteractiveContextForContainer(
        S_DOCKER_ID,
    ) is dictInteractive
    assert pipelineServer.fdictInteractiveContextForContainer(
        S_CONTAINER_NAME,
    ) is None


# ---------------------------------------------------------------
# Durable dispatch: the REAL carrier and the real lane tuple
# ---------------------------------------------------------------


@pytest.fixture
def appHubWithOwner():
    """Build the real hub app and record one owner keyed by NAME."""
    from tests.testAgentLaneEnforcement import MockDockerConnection
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        app = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    app.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=S_LEASE_ID, fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId=S_DOCKER_ID,
        )
    )
    return app


def fwebsocketFake(app, dictHeaders=None, dictQuery=None):
    """Return the attributes a lane-tuple builder reads off a socket."""
    return SimpleNamespace(
        app=app, headers=dictHeaders or {}, query_params=dictQuery or {},
    )


def testDurableContextResolvesTheOwnerByDockerId(appHubWithOwner):
    """The owner map is keyed by name; the socket carries the Docker id."""
    websocketAgent = fwebsocketFake(
        appHubWithOwner, dictHeaders={"x-vaibify-session": S_AGENT_TOKEN},
    )
    dictCtx = {"dictContainerOwners": appHubWithOwner.state.dictContainerOwners}
    dictDurable = pipelineServer._fdictBuildDurableDispatchContext(
        websocketAgent, dictCtx, S_DOCKER_ID,
    )
    assert dictDurable["sName"] == S_CONTAINER_NAME
    assert dictDurable["appState"] is appHubWithOwner.state
    assert dictDurable["dictLaneTuple"]["sContainerName"] == S_CONTAINER_NAME
    assert dictDurable["dictLaneTuple"]["sAgentToken"] == S_AGENT_TOKEN


def testDurableContextIsNoneForASocketWithoutTheLease(appHubWithOwner):
    websocketStranger = fwebsocketFake(
        appHubWithOwner, dictQuery={"sLeaseId": "leaseValueForged"},
    )
    dictCtx = {"dictContainerOwners": appHubWithOwner.state.dictContainerOwners}
    assert pipelineServer._fdictBuildDurableDispatchContext(
        websocketStranger, dictCtx, S_DOCKER_ID,
    ) is None


def testDurableContextIsNoneForAnUnownedContainer(appHubWithOwner):
    websocketAgent = fwebsocketFake(
        appHubWithOwner, dictHeaders={"x-vaibify-session": S_AGENT_TOKEN},
    )
    dictCtx = {"dictContainerOwners": appHubWithOwner.state.dictContainerOwners}
    assert pipelineServer._fdictBuildDurableDispatchContext(
        websocketAgent, dictCtx, "dockerIdUnowned",
    ) is None


def testStaleLaneTupleIsRefusedAndNothingStarts(appHubWithOwner):
    """A lane tuple whose owner record moved on launches nothing."""
    listStarted = []

    def ftaskStart():
        listStarted.append(True)
        return None

    dictDurable = {
        "appState": appHubWithOwner.state,
        "sName": S_CONTAINER_NAME,
        "dictLaneTuple": {
            "sLane": "agent", "iOwnerGeneration": 99,
            "sAgentToken": S_AGENT_TOKEN, "sContainerName": S_CONTAINER_NAME,
        },
    }
    tResult = asyncio.run(pipelineServer._ftLaunchDispatchTask(
        dictDurable, S_DOCKER_ID, ftaskStart, "projectAlpha",
    ))
    assert tResult == (None, 1)
    assert listStarted == []


def testSecondRunOfTheSameProjectIsRefusedWhileTheFirstIsLive(
    appHubWithOwner,
):
    """The carrier refuses a second live member with the same project key."""
    websocketAgent = fwebsocketFake(
        appHubWithOwner, dictHeaders={"x-vaibify-session": S_AGENT_TOKEN},
    )
    dictDurable = pipelineServer._fdictBuildDurableDispatchContext(
        websocketAgent,
        {"dictContainerOwners": appHubWithOwner.state.dictContainerOwners},
        S_DOCKER_ID,
    )

    async def fnScenario():
        eventRelease = asyncio.Event()
        listStarted = []

        def ftaskStart():
            listStarted.append(True)
            return asyncio.create_task(eventRelease.wait())

        tFirst = await pipelineServer._ftLaunchDispatchTask(
            dictDurable, S_DOCKER_ID, ftaskStart, "projectAlpha",
        )
        tSecond = await pipelineServer._ftLaunchDispatchTask(
            dictDurable, S_DOCKER_ID, ftaskStart, "projectAlpha",
        )
        eventRelease.set()
        await tFirst[0]
        return tFirst, tSecond, listStarted

    tFirst, tSecond, listStarted = asyncio.run(fnScenario())
    assert tFirst[0] is not None
    assert tSecond == (None, 1)
    assert listStarted == [True]


# ---------------------------------------------------------------
# Dispatch attribution and the safe-dispatch wrapper
# ---------------------------------------------------------------


DICT_SUPERVISED_PROVENANCE = {
    "dictSupervision": {"bEnabled": True},
}


class UnreachableConnection:
    """A Docker double on which every container call fails."""

    def __getattr__(self, sName):
        def fnFail(*aArgs, **dictArgs):
            raise ConnectionError("daemon unreachable")
        return fnFail


def testAttributionFailureIsLoggedAndNeverBlocks(caplog):
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectAlpha",
        "dictAiProvenance": dict(DICT_SUPERVISED_PROVENANCE),
    }
    with caplog.at_level(logging.WARNING):
        pipelineServer._fnRecordDispatchAttribution(
            UnreachableConnection(), S_DOCKER_ID, dictWorkflow, "runAll",
        )
    assert any(
        "Dispatch attribution failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testSafeDispatchReportsAFailedRunWithoutLeakingTheRawError(caplog):
    """A dispatch that raises answers a sanitized ``failed`` event."""
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectAlpha",
        "dictAiProvenance": dict(DICT_SUPERVISED_PROVENANCE),
        "listSteps": [],
    }
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    with caplog.at_level(logging.WARNING):
        asyncio.run(pipelineServer._fnSafeDispatch(
            "runSelected", {"sRunMode": "modeUnknownAlpha"},
            UnreachableConnection(), S_DOCKER_ID, dictWorkflow,
            {S_DOCKER_ID: S_WORKFLOW_PATH}, "/workspace/projectAlpha",
            fnCallback, {},
        ))
    assert listEvents == [{
        "sType": "failed", "iExitCode": 1,
        "sMessage": "Pipeline action failed. Check server logs for details.",
    }]
    assert any(
        "Dispatch attribution failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


# ---------------------------------------------------------------
# The stale-workflow refusal
# ---------------------------------------------------------------


BA_WORKFLOW_BYTES = b'{"listSteps": []}'
S_WORKFLOW_FINGERPRINT = hashlib.sha256(BA_WORKFLOW_BYTES).hexdigest()


class WorkflowBytesConnection:
    """A Docker double serving (or refusing) the bound workflow's bytes."""

    def __init__(self, baBytes=None, errorRead=None):
        self.baBytes = baBytes
        self.errorRead = errorRead

    def fbaFetchFile(self, sContainerId, sPath):
        if self.errorRead is not None:
            raise self.errorRead
        return self.baBytes


def fdictRunStaleCheck(
    connectionDocker, sAction="runAll", dictRequest=None,
    dictPathCache=None, sFingerprint=S_WORKFLOW_FINGERPRINT,
    fdictReload=None,
):
    """Run the pre-dispatch freshness check against one bound workflow."""
    dictCtx = {"docker": connectionDocker, "workflows": {}}
    dictBound = {"_sSourceFingerprint": sFingerprint}
    return asyncio.run(pipelineServer._fdictStaleWorkflowRefusal(
        dictCtx, S_DOCKER_ID, sAction, dictRequest or {}, dictBound,
        {S_DOCKER_ID: S_WORKFLOW_PATH} if dictPathCache is None
        else dictPathCache,
        fdictReloadBoundWorkflow=fdictReload,
    ))


def testStaleCheckIgnoresActionsThatRunNothingNew():
    assert fdictRunStaleCheck(
        WorkflowBytesConnection(errorRead=OSError("never read")),
        sAction="kill",
    ) is None


def testStaleCheckHasNothingToCompareWithoutAPathOrFingerprint():
    connectionDocker = WorkflowBytesConnection(errorRead=OSError("unread"))
    assert fdictRunStaleCheck(connectionDocker, dictPathCache={}) is None
    assert fdictRunStaleCheck(connectionDocker, sFingerprint="") is None


def testUnreadableProjectFileRefusesTheRunAndSaysSo():
    dictRefusal = fdictRunStaleCheck(
        WorkflowBytesConnection(
            errorRead=OSError("read failed at /Users/someone/secret"),
        ),
        dictRequest={"listStepIndices": [2]},
    )
    assert dictRefusal["sType"] == "runRefused"
    assert dictRefusal["sReason"] == "workflowSuperseded"
    assert dictRefusal["listStepIndices"] == [2]
    assert "could not be read for the pre-dispatch check" in (
        dictRefusal["sMessage"]
    )
    assert "/Users/someone" not in dictRefusal["sMessage"]
    assert "nothing was started" in dictRefusal["sMessage"]


def testDiskChangeWithAFailingReloadStillRefuses(caplog):
    """A reload that fails leaves no fingerprint to re-acknowledge."""

    def fdictReloadFails():
        raise FileNotFoundError("project.json vanished")

    with caplog.at_level(logging.WARNING):
        dictRefusal = fdictRunStaleCheck(
            WorkflowBytesConnection(baBytes=b'{"listSteps": [1]}'),
            fdictReload=fdictReloadFails,
        )
    assert dictRefusal["sType"] == "runRefused"
    assert dictRefusal["sCurrentSourceFingerprint"] == ""
    assert "changed on disk" in dictRefusal["sMessage"]
    assert any(
        "Agent project reload failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


# ---------------------------------------------------------------
# Remote-overwrite gate helpers
# ---------------------------------------------------------------


def testGateSelectsNoStepsForAnUngatedAction():
    dictWorkflow = {"listSteps": [{"sName": "stepAlpha"}]}
    assert pipelineServer._flistGateStepIndices(
        "verify", {}, dictWorkflow,
    ) == []


def testSelectedMalformedStepContributesNoRemotePaths():
    dictWorkflow = {"listSteps": ["notAStepDictionary", {"sName": "b"}]}
    assert pipelineServer._fdictCollectRemoteOverwritePaths(
        "runSelected", {"listStepIndices": [0, 1]}, dictWorkflow,
    ) == {}


def testExistingRemotePathsWithNoPathsAsksTheContainerNothing():
    assert pipelineServer._flistExistingRemotePaths(
        UnreachableConnection(), S_DOCKER_ID, "/workspace/projectAlpha", [],
    ) == []


# ---------------------------------------------------------------
# Agent project load and background scans
# ---------------------------------------------------------------


class ProjectFileConnection(MockDockerFull):
    """MockDockerFull serving one project repo at a distinct path."""

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        if "git rev-parse --show-toplevel" in sCommand:
            return (0, "/workspace/projectAlpha\n")
        return super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir,
        )


def testAgentProjectWorkflowCarriesItsDetectedRepository():
    dictCtx = {"docker": ProjectFileConnection()}
    dictWorkflow = pipelineServer._fdictLoadAgentProjectWorkflow(
        dictCtx, S_DOCKER_ID,
        "/workspace/projectAlpha/.vaibify/workflows/pipelineAlpha.json",
    )
    assert dictWorkflow["sProjectRepoPath"] == "/workspace/projectAlpha"
    assert dictWorkflow["sWorkflowName"] == "Test Pipeline"


def testViewerOwnershipIsSkippedWithoutAnOwnerMap():
    dictCtx = {"docker": None}
    assert pipelineServer._fnRegisterViewerServedContainer(
        dictCtx, S_DOCKER_ID, "sessionAlpha",
    ) is None
    assert "sViewerLease" not in dictCtx


def testDependencyScanFailureIsLoggedAndCachesNothing(caplog):
    dictCtx = {"docker": UnreachableConnection(), "sourceCodeDeps": {}}
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectAlpha",
        "listSteps": [{"sName": "stepAlpha", "sDirectory": "stepAlpha",
                       "saDataCommands": ["python dataAlpha.py"]}],
    }
    with patch(
        "vaibify.gui.routes.scriptRoutes.fdictScanAllDependencies",
        side_effect=ConnectionError("daemon unreachable"),
    ), caplog.at_level(logging.WARNING):
        asyncio.run(pipelineServer._fnScanDependenciesBackground(
            dictCtx, S_DOCKER_ID, dictWorkflow,
        ))
    assert dictCtx["sourceCodeDeps"] == {}
    assert any(
        "Source-code dep scan failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testDependencyScanLaunchWithoutALoopIsANoOp(caplog):
    with caplog.at_level(logging.DEBUG, logger="vaibify"):
        pipelineServer._fnLaunchDependencyScan({}, S_DOCKER_ID, {})
    assert any(
        "No event loop for dependency scan" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testSupervisedIntervalFailureNeverBreaksConnect(caplog):
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectAlpha",
        "dictAiProvenance": {"dictSupervision": {
            "bEnabled": True, "sLastManifestDigest": "digestBefore",
        }},
    }

    def ffilesUnreachable(sContainerId):
        raise ConnectionError("daemon unreachable")

    dictCtx = {"files": ffilesUnreachable}
    with caplog.at_level(logging.WARNING):
        pipelineServer._fnCheckSupervisedIntervalAtConnect(
            dictCtx, S_DOCKER_ID, dictWorkflow,
        )
    assert dictWorkflow["dictAiProvenance"]["dictSupervision"][
        "sLastManifestDigest"] == "digestBefore"
    assert any(
        "Supervised interval check failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testConftestRefreshFailureIsLoggedNotRaised(caplog):
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectAlpha",
        "listSteps": [{"sDirectory": "stepAlpha"}],
    }
    with patch(
        "vaibify.gui.conftestManager.fnEnsureConftestsCurrent",
        side_effect=ConnectionError("daemon unreachable"),
    ), caplog.at_level(logging.WARNING):
        asyncio.run(pipelineServer._fnRefreshConftestsAndMigrateMarkers(
            {"docker": UnreachableConnection()}, S_DOCKER_ID, dictWorkflow,
            "/workspace/projectAlpha/.vaibify/workflows/pipelineAlpha.json",
        ))
    assert any(
        "Conftest refresh / marker migration failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


# ---------------------------------------------------------------
# Envelope image currency and derivation
# ---------------------------------------------------------------


class UnreadableRepoFiles:
    """A repo adapter whose container read fails outright."""

    def fbIsFile(self, sRelativePath):
        raise OSError("container read failed")


def testUnreadableEnvelopeDeterminesNothing():
    """An envelope that cannot be read is never painted as a mismatch."""
    dictCtx = {"dictLiveImageIdentities": {S_DOCKER_ID: {
        "sImageId": "sha256:runningAlpha",
    }}}
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        dictCtx, S_DOCKER_ID, UnreadableRepoFiles(),
    )
    assert dictAnswer["bPinnedImageIsLive"] is None
    assert dictAnswer["sPinnedImageDigest"] == ""
    assert dictAnswer["bEnvironmentObtained"] is False


def testDerivationForADifferentPinReportsNoRelation(tmp_path):
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    os.makedirs(tmp_path / "repo" / ".vaibify")
    (tmp_path / "repo" / ".vaibify" / "environment.json").write_text(
        json.dumps({"dictContainer": {
            "sImageDigest": "registry.example/alpha@sha256:pinned",
        }}),
    )
    dictCtx = {"dictLiveImageIdentities": {S_DOCKER_ID: {
        "sImageId": "sha256:runningAlpha",
        "dictDerivation": {
            "sPinnedImageReference": "registry.example/other@sha256:x",
            "bRunningIsBase": True,
        },
    }}}
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        dictCtx, S_DOCKER_ID, ffilesEnsureRepoFiles(str(tmp_path / "repo")),
    )
    assert dictAnswer["bEnvironmentObtained"] is True
    assert "sRelation" not in dictAnswer
    assert dictAnswer["bPinnedImageIsLive"] is False


class NamedContainerConnection:
    """A Docker double that maps the Docker id to the project NAME."""

    def flistGetRunningContainers(self):
        return [{"sContainerId": S_DOCKER_ID, "sName": "projectAlpha"}]


def testDerivationReadsTheOriginRecordOfTheResolvedName(monkeypatch):
    from vaibify.config import imageOrigins
    from vaibify.docker import containerManager
    fnWriteRegistry([{
        "sName": "projectAlpha", "sDirectory": "/unused",
        "sConfigPath": "/unused/vaibify.yml",
        "sContainerName": "projectAlpha", "sMode": "container",
        "dictImageSource": {"sSource": "archive"},
    }])
    imageOrigins.fnWriteOriginRecord("projectAlpha", {
        "sBaseImageId": "sha256:baseAlpha",
        "sRunningImageId": "sha256:derivedAlpha",
        "sPinnedImageReference": "registry.example/alpha@sha256:pinned",
    })
    listInspected = []

    def fdictInspect(sReference):
        listInspected.append(sReference)
        return {"sId": "sha256:derivedAlpha", "dictLabels": {
            imageOrigins.S_PINNED_BASE_LABEL: "sha256:baseAlpha",
        }}

    monkeypatch.setattr(containerManager, "fdictInspectImageTag", fdictInspect)
    dictDerivation = pipelineServer._fdictDescribeImageDerivation(
        {"docker": NamedContainerConnection()}, S_DOCKER_ID,
        {"sImageId": "sha256:derivedAlpha"},
    )
    assert listInspected == ["projectAlpha:latest"]
    assert dictDerivation == {
        "sPinnedBaseImageId": "sha256:baseAlpha",
        "sPinnedImageReference": "registry.example/alpha@sha256:pinned",
        "bRunningIsBase": False,
    }


# ---------------------------------------------------------------
# Connect: refusals and load failures over real HTTP
# ---------------------------------------------------------------


class UnloadableWorkflowConnection(MockDockerFull):
    """MockDockerFull whose project file holds a non-object JSON value."""

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath.endswith(".json"):
            return b"[1, 2, 3]"
        return super().fbaFetchFile(sContainerId, sPath)


def fclientViewer(fconnectionCreate):
    """Build a viewer app over a Docker double; return an authed client."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", fconnectionCreate,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot=S_PROJECT_ROOT, sTerminalUserArg="researcher",
        )
    return TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
    )


def testConnectRefusesAWorkflowPathOutsideTheRoot():
    clientHttp = fclientViewer(MockDockerFull)
    responseHttp = clientHttp.post(
        f"/api/connect/{S_DOCKER_ID}",
        params={"sWorkflowPath": "/workspace/../etc/passwd.json"},
    )
    assert responseHttp.status_code == 403
    assert responseHttp.json()["detail"] == "Path traversal is not permitted"


def testConnectToAHostEntryWithNoDirectoryIsA409():
    fnWriteRegistry([{
        "sName": "hostProjectAlpha", "sMode": "host",
        "sConfigPath": "", "sContainerName": "hostProjectAlpha",
    }])
    clientHttp = fclientViewer(MockDockerFull)
    responseHttp = clientHttp.post(
        "/api/connect/hostProjectAlpha",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 409
    assert "hostProjectAlpha" in responseHttp.json()["detail"]["sMessage"]


def testConnectNamesTheProjectFileItCouldNotLoad():
    clientHttp = fclientViewer(UnloadableWorkflowConnection)
    responseHttp = clientHttp.post(
        f"/api/connect/{S_DOCKER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"]["sMessage"].startswith(
        "The project file could not be loaded:",
    )


# ---------------------------------------------------------------
# Bootstrap and transfer: malformed bodies are merely invalid
# ---------------------------------------------------------------


def testBootstrapWithAMalformedBodyIsAnInvalidCapability():
    clientHttp = fclientViewer(MockDockerFull)
    responseHttp = clientHttp.post(
        "/api/bootstrap", content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert responseHttp.status_code == 401
    assert responseHttp.json()["detail"] == (
        "Invalid or expired bootstrap capability."
    )
    assert "sCredential" not in responseHttp.text


def testTransferRefusesTheAgentLane():
    clientHttp = fclientViewer(MockDockerFull)
    responseHttp = clientHttp.post(
        "/api/transfer", json={"sCapability": "capabilityAlpha"},
        headers={"X-Vaibify-Session": S_AGENT_TOKEN},
    )
    assert responseHttp.status_code == 403
    assert "agent" in responseHttp.json()["detail"]
    assert S_AGENT_TOKEN not in responseHttp.text


def testTransferWithAMalformedBodyTransfersNothing():
    clientHttp = fclientViewer(MockDockerFull)
    responseHttp = clientHttp.post(
        "/api/transfer", content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert responseHttp.status_code != 200
    dictBody = responseHttp.json()
    assert dictBody["sOutcome"] != "transferred"
    assert "sCredential" not in dictBody


def testViewerConnectFromASecondBrowserSessionIsRefused():
    """First connect owns the viewer's container; a second session is 409."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerFull,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot=S_PROJECT_ROOT, sTerminalUserArg="researcher",
        )
    sCredentialFirst = fsBootstrapCredential(app)
    sCredentialSecond = fsBootstrapCredential(app)
    assert sCredentialFirst != sCredentialSecond
    clientFirst = TestClient(app, headers={"X-Session-Token": sCredentialFirst})
    clientSecond = TestClient(
        app, headers={"X-Session-Token": sCredentialSecond},
    )
    responseFirst = clientFirst.post(
        f"/api/connect/{S_DOCKER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseFirst.status_code == 200, responseFirst.text
    responseSecond = clientSecond.post(
        f"/api/connect/{S_DOCKER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseSecond.status_code == 409
    assert responseSecond.json()["detail"] == (
        "In use in another browser session"
    )
    assert sCredentialFirst not in responseSecond.text
    assert responseFirst.json()["sLeaseId"] not in responseSecond.text


class ScriptedPipelineSocket:
    """A pipeline socket that delivers scripted frames, then disconnects."""

    def __init__(self, listFrames):
        self.listFrames = list(listFrames)
        self.listSent = []

    async def receive_text(self):
        from starlette.websockets import WebSocketDisconnect
        if not self.listFrames:
            raise WebSocketDisconnect(code=1000)
        return json.dumps(self.listFrames.pop(0))

    async def send_json(self, dictEvent):
        self.listSent.append(dictEvent)


def testMessageLoopAnswersARefusedDurableLaunchWithRunRefused(
    appHubWithOwner,
):
    """A launch the carrier refuses is told to the socket, never started."""
    from starlette.websockets import WebSocketDisconnect
    dictDurable = {
        "appState": appHubWithOwner.state,
        "sName": S_CONTAINER_NAME,
        "dictLaneTuple": {
            "sLane": "agent", "iOwnerGeneration": 99,
            "sAgentToken": S_AGENT_TOKEN, "sContainerName": S_CONTAINER_NAME,
        },
    }
    websocketScripted = ScriptedPipelineSocket([
        {"sAction": "verify", "listStepIndices": [0]},
    ])
    dictPipelineTasks = {}
    connectionDocker = UnreachableConnection()
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(pipelineServer.fnPipelineMessageLoop(
            websocketScripted, connectionDocker, S_DOCKER_ID,
            {"listSteps": [], "sProjectRepoPath": "/workspace/projectAlpha"},
            {S_DOCKER_ID: S_WORKFLOW_PATH}, "/workspace/projectAlpha",
            dictPipelineTasks=dictPipelineTasks,
            dictDurableContext=dictDurable,
        ))
    assert len(websocketScripted.listSent) == 1
    dictEvent = websocketScripted.listSent[0]
    assert dictEvent["sType"] == "runRefused"
    assert dictEvent["sAction"] == "verify"
    assert dictEvent["listStepIndices"] == [0]
    assert dictPipelineTasks == {}
    assert S_DOCKER_ID not in (
        pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER
    )
