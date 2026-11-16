"""A configuration that widens what the container reaches runs only once approved.

Ruling D2 (2026-11-04): ``networkMode: open``, ``x11Forwarding``, a
``bindMounts`` entry or an ``allowedHosts`` entry is read from
vaibify.yml at creation, and that file can be changed by a hand that is
not vaibify's. The launch functions are the guard: a widening
configuration with no host-held approval of exactly its values is
refused before any argument exists. The start route's courtesy refusal,
the approval route, and the four researcher-only routes that record
approval as they write sit on top of that and are tested against the
real hub application with a real, isolated registry, the container
name distinct from the container id.
"""

import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.testContainerLifecycleGating import (  # noqa: F401
    appHub,
    fclientAuthenticated,
    fixtureIsolateHostState,
    fnRegisterProject,
)
from vaibify.config import reachApproval, registryManager
from vaibify.docker import containerManager
from vaibify.gui import actionCatalog, containerOwnership, startReservation

S_PROJECT = "reachProject"
S_AGENT_TOKEN = "agent-token-for-the-reach-test"


def fconfigFor(sNetworkMode="web", bX11Forwarding=False, listBindMounts=None,
               listAllowedHosts=None, sProjectName=S_PROJECT):
    return SimpleNamespace(
        sProjectName=sProjectName, sWorkspaceRoot="/workspace",
        sContainerUser="researcher", listPorts=[], listSecrets=[],
        iCpuLimit=0, fMemoryLimitGigabytes=0.0,
        sNetworkMode=sNetworkMode, bNetworkIsolation=False,
        bX11Forwarding=bX11Forwarding,
        listBindMounts=list(listBindMounts or []),
        listAllowedHosts=list(listAllowedHosts or []),
        features=SimpleNamespace(
            bGpu=False, bClaude=False, bCodex=False, bGemini=False,
            bOpenCode=False, bCline=False, bOpenHands=False, bPi=False,
        ),
    )


@pytest.fixture(autouse=True)
def fixtureNoX11AndABuiltImage(monkeypatch):
    monkeypatch.setattr(containerManager, "flistConfigureX11Args", lambda *tArgs: [])
    monkeypatch.setattr(
        containerManager, "fdictResolveLaunchPosture",
        lambda sProjectName, sImageReference="": {
            "sMode": "built", "bWithCredentials": True, "sImageDigest": "sha256:built"})


def fnServeRegistryEntry(monkeypatch, dictEntry):
    monkeypatch.setattr(
        containerManager, "_fdictRegisteredProjectOrNone", lambda sName: dictEntry)


# -- the rule ----------------------------------------------------------

def testOnlyTheFourFieldsWidenReach():
    assert reachApproval.fbConfigurationWidensReach(fconfigFor("web")) is False
    assert reachApproval.fbConfigurationWidensReach(fconfigFor("isolated")) is False
    assert reachApproval.fbConfigurationWidensReach(fconfigFor("open")) is True
    assert reachApproval.fbConfigurationWidensReach(
        fconfigFor("web", bX11Forwarding=True)) is True
    assert reachApproval.fbConfigurationWidensReach(fconfigFor(
        "web", listBindMounts=[{"host": "/tmp/data", "container": "/data"}])) is True
    assert reachApproval.fbConfigurationWidensReach(
        fconfigFor("allowlist", listAllowedHosts=["data.example.org"])) is True


@pytest.mark.falsification
def testTheFingerprintIsTheCanonicalJsonOfTheFourFields():
    """The expected hash is computed here from a literal canonical JSON
    string, independently of the module (the independent-oracle rule).

    Kills: hashing a different field set.
    """
    config = fconfigFor(
        "open", bX11Forwarding=True, listAllowedHosts=["b.example.org", "a.example.org"],
        listBindMounts=[{"host": "/tmp/data", "container": "/data", "readWrite": True}])
    config.iCpuLimit = 7
    config.listPorts = [{"host": 8888, "container": 8888}]
    sCanonical = (
        '{"bX11Forwarding":true,'
        '"listAllowedHosts":["b.example.org","a.example.org"],'
        '"listBindMounts":[{"container":"/data","host":"/tmp/data","readWrite":true}],'
        '"sNetworkMode":"open"}')
    sExpected = hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()
    assert reachApproval.fsComputeReachFingerprint(config) == sExpected
    config.iCpuLimit = 1
    assert reachApproval.fsComputeReachFingerprint(config) == sExpected
    config.listAllowedHosts = ["a.example.org", "b.example.org"]
    assert reachApproval.fsComputeReachFingerprint(config) != sExpected


def testARecordCoversExactlyTheValuesItWasMadeFor():
    config = fconfigFor("open")
    dictRecord = reachApproval.fdictBuildReachApprovalRecord(config)
    assert dictRecord["dictReachSettings"]["sNetworkMode"] == "open"
    assert dictRecord["sRecordedIso"]
    assert reachApproval.fbApprovalCoversConfiguration(dictRecord, config) is True
    assert reachApproval.fbApprovalCoversConfiguration(
        dictRecord, fconfigFor("open", bX11Forwarding=True)) is False
    assert reachApproval.fbApprovalCoversConfiguration(None, config) is False


def testThePromptNamesEachWideningFieldAndItsStatus():
    config = fconfigFor("open", bX11Forwarding=True, listAllowedHosts=["x.example.org"],
                        listBindMounts=[{"host": "/tmp/data", "container": "/data"}])
    dictPrompt = reachApproval.fdictBuildReachPrompt(config, None)
    assert dictPrompt["sTitle"] == reachApproval.S_REACH_PROMPT_TITLE
    assert len(dictPrompt["listBullets"]) == 4
    assert all("not yet approved" in sBullet for sBullet in dictPrompt["listBullets"])
    assert "not yet been approved on this computer" in dictPrompt["sText"]
    assert "/tmp/data (read-only)" in dictPrompt["sText"]
    dictRecord = reachApproval.fdictBuildReachApprovalRecord(fconfigFor("open"))
    dictPrompt = reachApproval.fdictBuildReachPrompt(config, dictRecord)
    assert "approved on " in dictPrompt["listBullets"][0]
    assert "changed since" not in dictPrompt["listBullets"][0]
    assert "not yet approved" not in dictPrompt["listBullets"][1]
    assert "changed since you approved it" in dictPrompt["listBullets"][1]
    assert "something other than this dashboard" in dictPrompt["sText"]
    assert reachApproval.fdictBuildReachPrompt(
        fconfigFor("open"), None, bRegistered=False)["sText"].endswith(
            "register it first.")


# -- the launch functions refuse ---------------------------------------

@pytest.mark.falsification
def testEveryLaunchFunctionRefusesAnUnapprovedWideningConfiguration(monkeypatch):
    """Kills: skipping the approval judgement in the run-argument builder."""
    fnServeRegistryEntry(monkeypatch, {"sName": S_PROJECT})
    for sCommand in ("_fsRunDetachedCommand", "_fnRunDockerCommand"):
        monkeypatch.setattr(
            containerManager, sCommand,
            lambda saCommand: pytest.fail("a container was launched"))
    monkeypatch.setattr(
        containerManager, "_fsRunKillableDockerCommand",
        lambda *tArgs, **dictKeywords: pytest.fail("a container was created"))
    listBuilt = []
    monkeypatch.setattr(
        containerManager, "_flistBuildProcessModeArgs",
        lambda *tArgs: listBuilt.append(tArgs) or [])
    config = fconfigFor("open")
    sReservation = "0123456789abcdef0123456789abcdef"
    for fnLaunch in (
        lambda: containerManager.flistBuildRunArgs(config),
        lambda: containerManager.fnStartContainer(config, "/d"),
        lambda: containerManager.fsStartContainerDetached(config, "/d"),
        lambda: containerManager.fsCreateContainerForReservation(config, sReservation),
        lambda: containerManager.fsRecreateContainerDetachedFromImage(config, "sha256:x"),
    ):
        with pytest.raises(reachApproval.ReachApprovalRequiredError) as errorRaised:
            fnLaunch()
        assert "--approve-reach" in str(errorRaised.value)
    assert listBuilt == []


def testANonWideningConfigurationNeverAsks(monkeypatch):
    fnServeRegistryEntry(monkeypatch, None)
    assert containerManager.fdictBuildReachPromptIfUnapproved(fconfigFor("web")) is None
    assert containerManager.fdictBuildReachPromptIfUnapproved(
        fconfigFor("isolated")) is None


def testARecordForOtherValuesAsksAgainAndACoveringOneDoesNot(monkeypatch):
    config = fconfigFor("open")
    dictRecord = reachApproval.fdictBuildReachApprovalRecord(config)
    fnServeRegistryEntry(monkeypatch, {
        "sName": S_PROJECT, registryManager.S_REACH_APPROVAL_KEY: dictRecord})
    assert containerManager.fdictBuildReachPromptIfUnapproved(config) is None
    dictPrompt = containerManager.fdictBuildReachPromptIfUnapproved(
        fconfigFor("open", bX11Forwarding=True))
    assert dictPrompt is not None and "changed since" in dictPrompt["sText"]
    fnServeRegistryEntry(monkeypatch, None)
    dictPrompt = containerManager.fdictBuildReachPromptIfUnapproved(config)
    assert dictPrompt["bRegistered"] is False
    with pytest.raises(reachApproval.ReachApprovalRequiredError, match="not registered"):
        containerManager.flistBuildRunArgs(config)


# -- the routes --------------------------------------------------------

def fnWriteConfig(sDirectory, sBody):
    with open(os.path.join(sDirectory, "vaibify.yml"), "w", encoding="utf-8") as fileYaml:
        fileYaml.write(f"projectName: {S_PROJECT}\n{sBody}")


@pytest.fixture
def clientHub(appHub, tmp_path):
    clientOne = fclientAuthenticated(appHub)
    sDirectory = fnRegisterProject(clientOne, tmp_path, S_PROJECT)
    fnWriteConfig(sDirectory, "networkMode: open\n")
    return clientOne


@pytest.mark.falsification
def testTheStartRouteAsksForApprovalBeforeReservingAnything(clientHub, appHub):
    """Kills: dropping the courtesy refusal, which would reserve and
    journal a start the launch guard then refuses."""
    responseStart = clientHub.post(f"/api/containers/{S_PROJECT}/start")
    assert responseStart.status_code == 409, responseStart.text
    dictDetail = responseStart.json()["detail"]
    assert dictDetail["sAction"] == "confirm-reach"
    assert "Network mode open" in dictDetail["dictReachPrompt"]["sText"]
    assert dictDetail["dictReachPrompt"]["sReachFingerprint"]
    assert S_PROJECT not in appHub.state.dictContainerOwners


def testThePromptRouteDescribesTheFileAsItIsNow(clientHub):
    dictPrompt = clientHub.get(f"/api/registry/{S_PROJECT}/reach-approval").json()
    assert dictPrompt["dictReachSettings"]["sNetworkMode"] == "open"
    assert dictPrompt["bAnswerCoversConfiguration"] is False
    assert dictPrompt["bRegistered"] is True


@pytest.mark.falsification
def testTheApprovalRouteRefusesAStaleFingerprintAndRecordsTheCurrentOne(clientHub):
    """Kills: approving blind, for a file that changed after the question."""
    responseStale = clientHub.post(
        f"/api/registry/{S_PROJECT}/reach-approval",
        json={"sReachFingerprint": "0" * 64})
    assert responseStale.status_code == 409
    assert "changed since" in responseStale.text
    assert registryManager.S_REACH_APPROVAL_KEY not in registryManager.fdictGetProject(S_PROJECT)
    sFingerprint = clientHub.get(
        f"/api/registry/{S_PROJECT}/reach-approval").json()["sReachFingerprint"]
    responseGood = clientHub.post(
        f"/api/registry/{S_PROJECT}/reach-approval",
        json={"sReachFingerprint": sFingerprint})
    assert responseGood.status_code == 200, responseGood.text
    dictStored = registryManager.fdictGetProject(S_PROJECT)[
        registryManager.S_REACH_APPROVAL_KEY]
    assert dictStored["sReachFingerprint"] == sFingerprint
    assert dictStored["dictReachSettings"]["sNetworkMode"] == "open"
    assert startReservation._fdictRefusalForUnapprovedReach(S_PROJECT) == {}
    assert clientHub.post(
        f"/api/registry/nobodyProject/reach-approval",
        json={"sReachFingerprint": sFingerprint}).status_code == 404


def testTheApprovalRouteHasNothingToApproveForANonWideningFile(clientHub, tmp_path):
    fnWriteConfig(str(tmp_path / S_PROJECT), "networkMode: web\n")
    responseNothing = clientHub.post(
        f"/api/registry/{S_PROJECT}/reach-approval",
        json={"sReachFingerprint": "0" * 64})
    assert responseNothing.status_code == 409
    assert "nothing to approve" in responseNothing.text


def testTheAgentLaneCannotApproveReach(appHub, clientHub):
    """The in-container agent is refused, and nothing is recorded; the
    container name, id and project name are all different strings."""
    appHub.state.dictContainerOwners["someOtherContainer"] = (
        containerOwnership.OwnerRecord(
            sLeaseId="researcher-lease", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId="0123456789ab"))
    clientAgent = TestClient(appHub, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050"})
    sFingerprint = clientHub.get(
        f"/api/registry/{S_PROJECT}/reach-approval").json()["sReachFingerprint"]
    responseAgent = clientAgent.post(
        f"/api/registry/{S_PROJECT}/reach-approval",
        json={"sReachFingerprint": sFingerprint})
    assert responseAgent.status_code in (401, 403)
    assert registryManager.S_REACH_APPROVAL_KEY not in registryManager.fdictGetProject(S_PROJECT)


# -- vaibify's own writes record approval ------------------------------

def fdictStoredApproval():
    return registryManager.fdictGetProject(S_PROJECT).get(
        registryManager.S_REACH_APPROVAL_KEY)


def testTheSettingsRouteRecordsApprovalForTheFileItWrote(clientHub, tmp_path):
    fnWriteConfig(str(tmp_path / S_PROJECT), "networkMode: web\n")
    responseSave = clientHub.post(
        f"/api/containers/{S_PROJECT}/settings", json={"sNetworkMode": "open"})
    assert responseSave.status_code == 200, responseSave.text
    assert fdictStoredApproval()["dictReachSettings"]["sNetworkMode"] == "open"
    assert startReservation._fdictRefusalForUnapprovedReach(S_PROJECT) == {}
    responseBack = clientHub.post(
        f"/api/containers/{S_PROJECT}/settings", json={"sNetworkMode": "web"})
    assert responseBack.status_code == 200, responseBack.text
    assert fdictStoredApproval() is None


def testTheAllowHostRouteRecordsApprovalForTheFileItWrote(clientHub, tmp_path):
    fnWriteConfig(str(tmp_path / S_PROJECT), "networkMode: allowlist\n")
    responseAllow = clientHub.post(
        f"/api/containers/{S_PROJECT}/network/allow-host",
        json={"sHost": "data.example.org"})
    assert responseAllow.status_code == 200, responseAllow.text
    assert fdictStoredApproval()["dictReachSettings"]["listAllowedHosts"] == [
        "data.example.org"]
    assert startReservation._fdictRefusalForUnapprovedReach(S_PROJECT) == {}


def testProjectCreationRecordsApprovalForTheFileItWrote(appHub, tmp_path, monkeypatch):
    sTemplateDirectory = str(tmp_path / "templates" / "sandbox")
    os.makedirs(sTemplateDirectory)
    with open(os.path.join(sTemplateDirectory, "container.conf"), "w") as fileConf:
        fileConf.write("")
    monkeypatch.setattr(
        "vaibify.config.templateManager._PATH_TEMPLATES", tmp_path / "templates")
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes.os.path.expanduser", lambda _: str(tmp_path))
    clientOne = fclientAuthenticated(appHub)
    sDirectory = str(tmp_path / "created-project")
    responseCreate = clientOne.post("/api/projects/create", json={
        "sDirectory": sDirectory, "sProjectName": "created-project",
        "sTemplateName": "sandbox", "sPythonVersion": "3.12",
        "listRepositories": [], "sNetworkMode": "open", "bX11Forwarding": True})
    assert responseCreate.status_code == 200, responseCreate.text
    dictStored = registryManager.fdictGetProject("created-project")[
        registryManager.S_REACH_APPROVAL_KEY]
    assert dictStored["dictReachSettings"] == {
        "sNetworkMode": "open", "bX11Forwarding": True,
        "listBindMounts": [], "listAllowedHosts": []}
    assert startReservation._fdictRefusalForUnapprovedReach("created-project") == {}


def testConversionCarriesTheRewrittenFilesApprovalIntoTheEntry(tmp_path):
    from vaibify.gui import conversionLifecycle
    sHostDirectory = str(tmp_path / "hostProject")
    os.makedirs(sHostDirectory)
    sConfigPath = os.path.join(sHostDirectory, "vaibify.yml")
    with open(sConfigPath, "w", encoding="utf-8") as fileYaml:
        fileYaml.write("projectName: hostProject\nnetworkMode: open\n")
    registryManager.fnAddProject(sHostDirectory, sMode="host")
    dictApproval = conversionLifecycle._fdictReachApprovalForRewrittenConfig(sConfigPath)
    assert dictApproval["dictReachSettings"]["sNetworkMode"] == "open"
    registryManager.fnConvertProjectToContainer(
        "hostProject", "convertedProject", dictReachApproval=dictApproval)
    assert registryManager.fdictGetProject("convertedProject")[
        registryManager.S_REACH_APPROVAL_KEY] == dictApproval
    with open(sConfigPath, "w", encoding="utf-8") as fileYaml:
        fileYaml.write("projectName: hostProject\nnetworkMode: web\n")
    assert conversionLifecycle._fdictReachApprovalForRewrittenConfig(sConfigPath) is None
    registryManager.fsReturnProjectToHost("convertedProject")
    assert registryManager.S_REACH_APPROVAL_KEY not in registryManager.fdictGetProject(
        "hostProject")
