"""The "Set up Docker" routes, over real HTTP on the real hub app.

The guarantees here cross the HTTP boundary, so they are driven
through the hub's own middleware: the browser presents its redeemed
credential, and the in-container agent presents its container's own
minted token, a different value. The machine's facts are fixture
evidence (reading the suite host's real Docker would make the answer
depend on where the suite runs), and the step runner is replaced by a
recorder, so "the agent ran nothing" is an observation, not an
inference.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testRuntimeSetupPlan import fdictMacFacts
from vaibify.gui import containerOwnership, pipelineServer
from vaibify.gui.routes import runtimeSetupRoutes
from vaibify.install import runtimeSetupFacts, runtimeSetupRunner


S_AGENT_TOKEN = "agentTokenForContainerBeta"
S_AGENT_CONTAINER_ID = "containerBetaId0001"


class _DockerAbsent:
    """The hub's Docker double: this machine has no daemon yet."""

    def flistGetRunningContainers(self):
        return []


@pytest.fixture
def dictHub(monkeypatch):
    """Return the hub's two clients and what its runner was asked to run."""
    dictRecorded = {"listRuns": [], "dictFacts": fdictMacFacts()}
    def fdictReadFacts(bProbePrivilege=True):
        dictRecorded.setdefault("listPrivilegeProbes", []).append(
            bProbePrivilege)
        return dict(dictRecorded["dictFacts"])

    monkeypatch.setattr(runtimeSetupFacts, "fdictReadHostFacts",
                        fdictReadFacts)

    def fnRecordRun(dictPlan, sActionName, sChannel, fnEmitLine,
                    bInteractive=False, fnLockHeld=None):
        runtimeSetupRunner.fdictFindRunnableAction(dictPlan, sActionName)
        if fnLockHeld:
            fnLockHeld()
        fnEmitLine("ran " + sActionName)
        dictRecorded["listRuns"].append((sActionName, sChannel, dictPlan))
        dictRecorded["dictFacts"] = dict(
            dictRecorded["dictFacts"],
            listCommands=["brew", "colima", "docker"], bBuildxWorks=True)

    monkeypatch.setattr(runtimeSetupRunner, "fnRunAction", fnRecordRun)
    with patch.object(pipelineServer, "_fconnectionCreateDocker",
                      lambda: _DockerAbsent()):
        app = pipelineServer.fappCreateHubApplication()
    app.state.dictContainerOwners["projectBeta"] = (
        containerOwnership.OwnerRecord(
            sLeaseId="leaseBeta", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId=S_AGENT_CONTAINER_ID))
    sCredential = fsBootstrapCredential(app)
    assert sCredential != S_AGENT_TOKEN
    dictRecorded["clientBrowser"] = TestClient(
        app, headers={"X-Session-Token": sCredential},
        raise_server_exceptions=False)
    dictRecorded["clientAgent"] = TestClient(app, headers={
        "X-Vaibify-Session": S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    }, raise_server_exceptions=False)
    runtimeSetupRoutes._DICT_SETUP_PROGRESS["dequeTail"].clear()
    return dictRecorded


def testThePlanReachesTheBrowserWithoutAnyCommandToRun(dictHub):
    responseHttp = dictHub["clientBrowser"].get("/api/runtime/setup")
    assert responseHttp.status_code == 200, responseHttp.text
    dictPlan = responseHttp.json()
    dictStep = dictPlan["listSteps"][0]
    assert dictStep["sStepName"] == "install-runtime"
    assert dictStep["listActions"] == [
        {"sActionName": "install-with-homebrew", "sLabel": "Install"}]
    assert "listInvocations" not in responseHttp.text
    assert "listArgv" not in responseHttp.text


def testTheBrowserRunsTheNextStepAndGetsThePlanThatFollows(dictHub):
    responseHttp = dictHub["clientBrowser"].post(
        "/api/runtime/setup/run", json={"sActionName": "install-with-homebrew"})
    assert responseHttp.status_code == 200, responseHttp.text
    assert [tRun[0] for tRun in dictHub["listRuns"]] == [
        "install-with-homebrew"]
    assert responseHttp.json()["listSteps"][0]["sStepName"] == "start-colima"
    dictProgress = dictHub["clientBrowser"].get(
        "/api/runtime/setup/progress").json()
    assert dictProgress["listTail"] == ["ran install-with-homebrew"]
    assert dictProgress["sOutcome"] == "succeeded"


T_SETUP_ROUTES = (
    ("post", "/api/runtime/setup/run", {"sActionName": "install-with-homebrew"}),
    ("get", "/api/runtime/setup", None),
    ("get", "/api/runtime/setup/progress", None),
)


def _fresponseCall(client, sMethod, sPath, dictBody):
    fnCall = getattr(client, sMethod)
    return fnCall(sPath, json=dictBody) if dictBody else fnCall(sPath)


@pytest.mark.parametrize("sMethod, sPath, dictBody", T_SETUP_ROUTES)
def testTheInContainerAgentCanNeitherRunNorReadTheSetup(
        dictHub, sMethod, sPath, dictBody):
    """End to end: the hub refuses the agent's own token on every route."""
    responseHttp = _fresponseCall(
        dictHub["clientAgent"], sMethod, sPath, dictBody)
    assert responseHttp.status_code in (401, 403), responseHttp.text
    assert dictHub["listRuns"] == []
    assert S_AGENT_TOKEN not in responseHttp.text


@pytest.mark.falsification
@pytest.mark.parametrize("sMethod, sPath, dictBody", T_SETUP_ROUTES)
def testEachSetupRouteRefusesTheAgentLaneItself(
        dictHub, sMethod, sPath, dictBody):
    """The handlers' own refusal, with no middleware in front of them.

    The hub's middleware refuses the agent token first, so the end-to-end
    test above cannot see whether the handlers would. Mounted on a bare
    app, only the handler can refuse.

    Kills: a setup handler that does not reject the agent lane.
    """
    from fastapi import FastAPI
    appBare = FastAPI()
    runtimeSetupRoutes.fnRegisterAll(appBare, {})
    responseHttp = _fresponseCall(TestClient(appBare, headers={
        "X-Vaibify-Session": S_AGENT_TOKEN}), sMethod, sPath, dictBody)
    assert responseHttp.status_code == 403, responseHttp.text
    assert dictHub["listRuns"] == []


def testAnActionThatIsNotTheNextStepsIsRefusedAndRunsNothing(dictHub):
    responseHttp = dictHub["clientBrowser"].post(
        "/api/runtime/setup/run", json={"sActionName": "start-new-colima"})
    assert responseHttp.status_code == 409
    assert "not an action of the next step" in responseHttp.json()[
        "detail"]["sMessage"]
    assert dictHub["listRuns"] == []


@pytest.mark.parametrize("dictBody", [
    {"sActionName": "start-new-colima", "iCpus": "4; rm -rf ~"},
    {"sActionName": "start-new-colima", "iCpus": True},
    {"sActionName": "start-new-colima", "iMemoryGigabytes": 4.5},
])
def testASettingThatIsNotAWholeNumberIsRefusedAtTheBoundary(
        dictHub, dictBody):
    responseHttp = dictHub["clientBrowser"].post(
        "/api/runtime/setup/run", json=dictBody)
    assert responseHttp.status_code == 422
    assert dictHub["listRuns"] == []


def testASettingTheMachineCannotHoldIsRefusedWithItsBounds(dictHub):
    dictHub["dictFacts"] = fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True)
    responseHttp = dictHub["clientBrowser"].post(
        "/api/runtime/setup/run",
        json={"sActionName": "start-new-colima", "iMemoryGigabytes": 64})
    assert responseHttp.status_code == 422
    assert "between 2 and 16" in responseHttp.json()["detail"]["sMessage"]
    assert dictHub["listRuns"] == []


def testTheEditedSettingsAreTheOnesTheRunnerIsGiven(dictHub):
    dictHub["dictFacts"] = fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True)
    responseHttp = dictHub["clientBrowser"].post(
        "/api/runtime/setup/run", json={
            "sActionName": "start-new-colima", "iCpus": 3,
            "iMemoryGigabytes": 6})
    assert responseHttp.status_code == 200, responseHttp.text
    dictPlan = dictHub["listRuns"][0][2]
    listArgv = dictPlan["listSteps"][0]["listActions"][0][
        "listInvocations"][0]["listArgv"]
    assert listArgv[-4:] == ["--cpus", "3", "--memory", "6"]


def testTheHubLoadCheckNeverProbesSudo(dictHub):
    """A failed ``sudo -n`` is logged; the per-page check must not ask it."""
    responseHttp = dictHub["clientBrowser"].get(
        "/api/runtime/setup", params={"bAdvisory": "true"})
    assert responseHttp.status_code == 200, responseHttp.text
    assert dictHub["listPrivilegeProbes"] == [False]
    dictHub["clientBrowser"].get("/api/runtime/setup")
    assert dictHub["listPrivilegeProbes"] == [False, True]


def _fdictAdvisoryAnswer(dictHub, dictFacts):
    dictHub["dictFacts"] = dictFacts
    responseHttp = dictHub["clientBrowser"].get(
        "/api/runtime/setup", params={"bAdvisory": "true"})
    assert responseHttp.status_code == 200, responseHttp.text
    return responseHttp.json()


def testTheHubLoadCheckNamesTheStepItsBannerShowsWithoutItsCommands(
        dictHub):
    """The banner's step comes from the hub, stripped like every step."""
    dictAnswer = _fdictAdvisoryAnswer(dictHub, fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bDaemonAnswers=True,
        bBuildxWorks=True, sActiveColimaProfile="default",
        listColimaMachines=[{
            "sProfile": "default", "sStatus": "Running", "iCpus": 2,
            "iMemoryBytes": 2 * 2 ** 30, "sArchitecture": "aarch64"}]))
    dictStep = dictAnswer["dictHubBannerStep"]
    assert dictStep["sStepName"] == "resize-colima"
    assert "listInvocations" not in dictStep["listActions"][0]


def testTheHubLoadCheckNamesNoBannerStepForAMissingBuildx(dictHub):
    dictAnswer = _fdictAdvisoryAnswer(dictHub, fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bDaemonAnswers=True,
        sRuntime="docker-desktop"))
    assert dictAnswer["listSteps"], dictAnswer
    assert dictAnswer["dictHubBannerStep"] is None
