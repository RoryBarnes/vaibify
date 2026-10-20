"""The researcher's command line reaches what the dashboard reaches.

Covers the pieces that give every dashboard action a CLI equivalent:
the researcher-only catalog (generated into ``vaibify do``, never
shipped into the container, never callable on the agent lane), the hub
control-plane lane (a project by name, no claim), the hand-written
``vaibify credential`` (the token never leaves the request body), the
interactive steps of a run (answered at the terminal, or refused at
once with --non-interactive), and the raw-body upload.
"""

import inspect
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli import actionCommands, hubSession
from vaibify.cli.main import main
from vaibify.gui import actionCatalog, researcherActions
from tests.cliRunnerStreams import frunnerSeparatingStreams


S_SENTINEL_TOKEN = "sentinel-token-q7Zx9-NEVER-PRINTED"


# --------------------------------------------------- the researcher list


def testEveryResearcherActionHasAGeneratedCommand():
    for dictEntry in researcherActions.LIST_RESEARCHER_ACTIONS:
        commandAction = actionCommands.fnDoCommand.commands.get(
            dictEntry["sName"])
        assert commandAction is not None, dictEntry["sName"]
        setParameters = {parameter.name for parameter in commandAction.params}
        for sPlaceholder in actionCommands.flistArgumentPlaceholders(
                dictEntry):
            assert sPlaceholder.lower() in setParameters, (
                dictEntry["sName"], sPlaceholder)


@pytest.mark.falsification
def testTheResearcherListIsNeverShippedNorCallableByAnAgent():
    """Kills: merging the researcher list into the shipped agent catalog."""
    setShipped = {dictEntry["sName"] for dictEntry in
                  actionCatalog.fdictBuildCatalogJson()["listActions"]}
    for dictEntry in researcherActions.LIST_RESEARCHER_ACTIONS:
        assert dictEntry["sName"] not in setShipped, dictEntry["sName"]
        assert actionCatalog.fbAgentLanePermitsRoute(
            dictEntry["sMethod"], dictEntry["sPath"]) is False, (
            dictEntry["sName"])


def testResearcherActionsSendEveryQueryFieldOnTheQueryString():
    """The route's signature is the authority, as for the agent list."""
    from pydantic import BaseModel
    from tests.testArchitecturalInvariants import (
        _fappBuildApplication, _fbParameterIsNotAQueryField,
        _fbParameterIsPathPlaceholder,
    )
    from tests.testSecurityBoundaryInvariants import _fappBuildHubApplication
    dictRoutes = {}
    for app in (_fappBuildApplication(), _fappBuildHubApplication()):
        for route in app.routes:
            for sMethod in getattr(route, "methods", None) or ():
                dictRoutes[(sMethod, route.path)] = route
    listUndeclared = []
    for dictEntry in researcherActions.LIST_RESEARCHER_ACTIONS:
        route = dictRoutes.get((dictEntry["sMethod"], dictEntry["sPath"]))
        assert route is not None, dictEntry["sName"]
        setDeclared = set(dictEntry.get("saQueryFields") or ())
        for sName, parameter in inspect.signature(
                route.endpoint).parameters.items():
            if _fbParameterIsPathPlaceholder(sName, route.path) or (
                    _fbParameterIsNotAQueryField(sName, parameter, BaseModel)):
                continue
            if sName not in setDeclared:
                listUndeclared.append(f"{dictEntry['sName']} -> {sName}")
    assert listUndeclared == []


# ------------------------------------------------------------- dispatch


def _fdictDispatch(sAction, listArgs):
    """Run one generated action with the hub faked; return what it sent."""
    dictSent = {"listOpened": [], "listCalls": []}

    def fdictHub(sContainerName, iPort=None):
        dictSent["listOpened"].append(("hub", sContainerName))
        return {"sBaseUrl": "http://127.0.0.1:1", "sCredential": "c",
                "sContainerName": sContainerName, "sContainerId": "",
                "sLeaseId": "", "sWorkflowPath": ""}

    def fdictResearcher(sContainerName, iPort=None, sWorkflowPath=None):
        dictSent["listOpened"].append(("claim", sContainerName))
        return {"sBaseUrl": "http://127.0.0.1:1", "sCredential": "c",
                "sContainerName": sContainerName, "sContainerId": "cid-77",
                "sLeaseId": "lease", "sWorkflowPath": "/w.json"}

    def fiSend(dictSession, sMethod, sPath, dictFields, bJson,
               fTimeoutSeconds, baRawBody=None):
        dictSent["listCalls"].append((sMethod, sPath, dictFields, baRawBody))
        return 0

    with patch.object(actionCommands, "_fsResolveContainerName",
                      return_value="projectAlpha"), \
            patch.object(hubSession, "fdictOpenHubControlSession", fdictHub), \
            patch.object(hubSession, "fdictOpenResearcherSession",
                         fdictResearcher), \
            patch.object(hubSession, "fnReleaseContainer"), \
            patch.object(hubSession, "fiSendHttpAction", fiSend):
        result = CliRunner().invoke(main, ["do", sAction] + listArgs)
    return result, dictSent


@pytest.mark.falsification
def testAHubLaneActionAddressesTheProjectByNameAndClaimsNothing():
    """Kills: dispatching a control-plane action through a container claim."""
    result, dictSent = _fdictDispatch(
        "set-container-settings", ["iCpuLimit=4"])
    assert result.exit_code == 0, result.output
    assert dictSent["listOpened"] == [("hub", "projectAlpha")]
    assert dictSent["listCalls"] == [(
        "POST", "/api/containers/projectAlpha/settings", {"iCpuLimit": 4},
        None)]


def testAContainerLaneResearcherActionClaimsTheContainer():
    result, dictSent = _fdictDispatch(
        "configure-supervision", ["bEnabled=true"])
    assert result.exit_code == 0, result.output
    assert dictSent["listOpened"] == [("claim", "projectAlpha")]
    assert dictSent["listCalls"][0][:3] == (
        "POST", "/api/workflow/cid-77/supervision/configure",
        {"bEnabled": True})


def testTheUploadStreamsALocalFileAsTheBody(tmp_path):
    pathLocal = tmp_path / "samples.csv"
    pathLocal.write_bytes(b"a,b\n1,2\n")
    result, dictSent = _fdictDispatch(
        "upload-file-stream",
        ["sLocalPath=" + str(pathLocal), "sDestination=/workspace/repo"])
    assert result.exit_code == 0, result.output
    sMethod, sPath, dictBody, baBody = dictSent["listCalls"][0]
    assert baBody == b"a,b\n1,2\n"
    assert "iSizeBytes=8" in sPath and "sFilename=samples.csv" in sPath
    assert "sLocalPath" not in sPath and not dictBody


# ----------------------------------------------------------- credential


@pytest.mark.falsification
def testTheCredentialTokenTravelsOnlyInTheRequestBody(monkeypatch):
    """Kills: echoing the token, or passing it anywhere but the body."""
    from vaibify.cli import commandCredential
    listRequests = []

    def ftSend(dictSession, sMethod, sPath, dictFields=None, *args, **kw):
        listRequests.append((sMethod, sPath, json.dumps(dictFields)))
        return 400, {"detail": "the token was refused by zenodo"}

    monkeypatch.setattr(commandCredential, "_fsResolveContainerName",
                        lambda sName: "projectAlpha")
    monkeypatch.setattr(hubSession, "fdictOpenResearcherSession",
                        lambda sName, iPort=None: {"sContainerId": "cid-77"})
    monkeypatch.setattr(hubSession, "fnReleaseContainer",
                        lambda dictSession: None)
    monkeypatch.setattr(hubSession, "ftSendSessionRequest", ftSend)
    listArgv = ["credential", "set", "zenodo", "--instance",
                "production"]
    result = frunnerSeparatingStreams().invoke(
        main, listArgv, input=S_SENTINEL_TOKEN + "\n")
    assert result.exit_code == 1
    assert S_SENTINEL_TOKEN not in result.stdout
    assert S_SENTINEL_TOKEN not in result.stderr
    assert all(S_SENTINEL_TOKEN not in sArgument for sArgument in listArgv)
    assert len(listRequests) == 1
    sMethod, sPath, sBody = listRequests[0]
    assert (sMethod, sPath) == ("POST", "/api/sync/cid-77/setup")
    assert S_SENTINEL_TOKEN not in sPath
    assert json.loads(sBody) == {
        "sService": "zenodo", "sProjectId": "", "sToken": S_SENTINEL_TOKEN,
        "sZenodoInstance": "production"}


# ---------------------------------------------------- interactive steps


class _RecordingConnection:
    """A pipeline socket replaying events and recording what is sent."""

    def __init__(self, listEvents):
        self.listEvents = [json.dumps(dictEvent) for dictEvent in listEvents]
        self.listSent = []

    def recv(self, timeout=None):
        return self.listEvents.pop(0)

    def send(self, sMessage):
        self.listSent.append(json.loads(sMessage))


_DICT_PAUSE = {"sType": "interactivePause", "iStepIndex": 1,
               "sStepName": "Inspect fits"}


@pytest.mark.falsification
def testANonInteractiveRunStopsAtAnInteractiveStepAndSaysWhy(capsys):
    """Kills: waiting out the timeout instead of refusing at once."""
    connection = _RecordingConnection([_DICT_PAUSE])
    iExit = hubSession._fiStreamUntilTerminalEvent(
        connection, False, 5.0, True)
    assert iExit == hubSession.I_INTERACTIVE_REFUSED_EXIT_CODE
    assert connection.listSent == []
    assert "Inspect fits" in capsys.readouterr().err


class _RecordingSocket(_RecordingConnection):
    """The same, usable as the context manager ``connect`` returns."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.falsification
def testANonInteractiveRunIsStoppedSoItsReleaseNeedNotWait(monkeypatch):
    """Kills: leaving the paused run to hold the container, so the
    release waits out the whole timeout before the command exits 3."""
    listRequests = []
    listSleeps = []
    dictHub = {"bStopped": False}

    def ftSend(sBaseUrl, sCredential, sMethod, sPath, dictFields,
               fTimeoutSeconds, **dictKeywords):
        listRequests.append((sMethod, sPath, dictKeywords.get("sLeaseId")))
        if sPath.endswith("/kill"):
            dictHub["bStopped"] = True
            return 200, {"bSuccess": True}
        if dictHub["bStopped"]:
            return 200, {"bReleased": True}
        return 409, {"sMessage": "a run is paused at an interactive step"}

    def fnSleep(fSeconds):
        listSleeps.append(fSeconds)
        assert len(listSleeps) < 3, "the release is waiting on the run"

    monkeypatch.setattr(hubSession, "ftSendHttpRequest", ftSend)
    monkeypatch.setattr(hubSession.time, "sleep", fnSleep)
    socketScripted = _RecordingSocket([
        {"sType": "workflowBound", "sWorkflowPath": "/workspace/p.json",
         "sExactSourceFingerprint": "f" * 64}, _DICT_PAUSE])
    dictSession = {"sBaseUrl": "http://127.0.0.1:1", "sCredential": "c",
                   "sContainerName": "projectAlpha",
                   "sContainerId": "cid-77", "sLeaseId": "lease-1"}
    with patch("websockets.sync.client.connect",
               return_value=socketScripted):
        iExit = hubSession.fiStreamPipelineAction(
            dictSession, {"sAction": "runAll"}, False, 300.0,
            bNonInteractive=True)
    hubSession.fnReleaseContainer(dictSession, fWaitSeconds=None)
    assert iExit == hubSession.I_INTERACTIVE_REFUSED_EXIT_CODE
    assert listRequests == [
        ("POST", "/api/pipeline/cid-77/kill", "lease-1"),
        ("POST", "/api/registry/projectAlpha/release", "lease-1")]
    assert listSleeps == []


def testAnInteractiveRunAsksThenReportsTheExitCode(monkeypatch):
    listAnswers = ["r", 0]
    monkeypatch.setattr(hubSession.click, "prompt",
                        lambda *args, **kwargs: listAnswers.pop(0))
    connection = _RecordingConnection([
        _DICT_PAUSE,
        {"sType": "interactiveTerminalStart", "sStepName": "Inspect fits",
         "dictStep": {"saDataCommands": ["python inspect.py"]}},
        {"sType": "completed", "iExitCode": 0},
    ])
    assert hubSession._fiStreamUntilTerminalEvent(
        connection, False, 5.0, False) == 0
    assert connection.listSent == [
        {"sAction": "interactiveResume"},
        {"sAction": "interactiveComplete", "iExitCode": 0}]
