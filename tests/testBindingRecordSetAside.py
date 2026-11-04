"""An unreadable binding record is moved aside, and a readable one never is.

Source: ``vaibify/config/remoteBindings.py`` (``fsSetAsideUnreadableRecord``),
``vaibify/gui/routes/bindingRoutes.py`` (``POST .../set-aside``) and
``vaibify/cli/commandBindings.py`` (``vaibify bindings --set-aside``).

Before 2026-11-03 the dashboard said "Move it aside" with no way to do
it, so recovering took a terminal on the host and a reload. The rename
happens under the record's lock, its path comes from the project's
identity and nothing in the request, and it is refused while the record
can be read, so a valid confirmation is never set aside. Every refusal
here is checked against the files on disk.
"""

import os
import re
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from vaibify.config import remoteBindings
from tests.testRemoteBindings import (  # noqa: F401 -- fixtures
    S_CONTAINER_ID, S_PROJECT_NAME, _fresponseConfirmReviewed,
    fnIsolateHostState, tApp,
)

S_SET_ASIDE_PATH = f"/api/bindings/{S_CONTAINER_ID}/set-aside"
S_READ_PATH = f"/api/bindings/{S_CONTAINER_ID}"
BA_UNREADABLE = b'{"sProjectUuid": "not even this project", "dictWorkflows": {}'


def _fnPlantRecord(sUuid, baContent):
    os.makedirs(remoteBindings.fsBindingsDirectory(), exist_ok=True)
    with open(remoteBindings.fsBindingPath(sUuid), "wb") as fileRecord:
        fileRecord.write(baContent)


def _flistBindingsDirectory():
    return sorted(os.listdir(remoteBindings.fsBindingsDirectory()))


def _fbaRead(sPath):
    with open(sPath, "rb") as fileRecord:
        return fileRecord.read()


@pytest.mark.falsification
def testAReadableRecordIsNeverMovedAside(tApp):
    """A confirmed record stays exactly where and what it is.

    Kills: dropping the readable-record refusal, which moved a valid
    confirmation aside and with it every remote the researcher confirmed.
    """
    client, _, sUuid = tApp
    assert _fresponseConfirmReviewed(client, "overleaf").status_code == 200
    sPath = remoteBindings.fsBindingPath(sUuid)
    baBefore = _fbaRead(sPath)
    listBefore = _flistBindingsDirectory()
    responseHttp = client.post(S_SET_ASIDE_PATH)
    assert responseHttp.status_code == 409, responseHttp.text
    assert responseHttp.json()["detail"]["sError"] == "BINDING-RECORD-VALID"
    assert _fbaRead(sPath) == baBefore
    assert _flistBindingsDirectory() == listBefore


def testAnAbsentRecordIsNotMovedAside(tApp):
    client, _, sUuid = tApp
    responseHttp = client.post(S_SET_ASIDE_PATH)
    assert responseHttp.status_code == 409, responseHttp.text
    assert responseHttp.json()["detail"]["sError"] == "BINDING-RECORD-VALID"
    assert not os.path.lexists(remoteBindings.fsBindingPath(sUuid))


def testAnUnreadableRecordIsRenamedAndTheNextReadStartsEmpty(tApp):
    client, _, sUuid = tApp
    _fnPlantRecord(sUuid, BA_UNREADABLE)
    dictBefore = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    assert dictBefore["dictRecordState"]["bValid"] is False
    responseHttp = client.post(S_SET_ASIDE_PATH)
    assert responseHttp.status_code == 200, responseHttp.text
    dictAnswer = responseHttp.json()
    assert re.fullmatch(
        re.escape(sUuid) + r"\.json\.unreadable-\d{8}T\d{6}Z",
        dictAnswer["sSetAsideName"])
    assert dictAnswer["dictRecordState"] == {"bValid": True}
    sSetAside = os.path.join(
        remoteBindings.fsBindingsDirectory(), dictAnswer["sSetAsideName"])
    assert _fbaRead(sSetAside) == BA_UNREADABLE
    assert not os.path.lexists(remoteBindings.fsBindingPath(sUuid))
    dictAfter = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    assert dictAfter["dictRecordState"] == {"bValid": True}
    assert dictAfter["listEntries"]
    assert all(dictEntry["dictConfirmed"] is None
               for dictEntry in dictAfter["listEntries"])


def testTheNewNameCarriesTheUtcStampBesideTheRecord(tApp):
    _client, _, sUuid = tApp
    _fnPlantRecord(sUuid, BA_UNREADABLE)
    sName = remoteBindings.fsSetAsideUnreadableRecord(
        sUuid, sUtcStamp="20261103T120000Z")
    assert sName == f"{sUuid}.json.unreadable-20261103T120000Z"
    assert _flistBindingsDirectory() == sorted([
        f"{sUuid}.json.lock", sName])


def testATakenNameIsRefusedAndOverwritesNothing(tApp):
    _client, _, sUuid = tApp
    _fnPlantRecord(sUuid, BA_UNREADABLE)
    sTaken = remoteBindings.fsBindingPath(sUuid) + ".unreadable-20261103T120000Z"
    with open(sTaken, "wb") as fileTaken:
        fileTaken.write(b"set aside earlier")
    with pytest.raises(OSError, match="already exists"):
        remoteBindings.fsSetAsideUnreadableRecord(
            sUuid, sUtcStamp="20261103T120000Z")
    assert _fbaRead(sTaken) == b"set aside earlier"
    assert _fbaRead(remoteBindings.fsBindingPath(sUuid)) == BA_UNREADABLE


def testALinkedRecordIsNeitherFollowedNorMoved(tApp, tmp_path):
    client, _, sUuid = tApp
    sElsewhere = str(tmp_path / "elsewhere.json")
    with open(sElsewhere, "wb") as fileElsewhere:
        fileElsewhere.write(BA_UNREADABLE)
    os.makedirs(remoteBindings.fsBindingsDirectory(), exist_ok=True)
    os.symlink(sElsewhere, remoteBindings.fsBindingPath(sUuid))
    responseHttp = client.post(S_SET_ASIDE_PATH)
    assert responseHttp.status_code == 409, responseHttp.text
    assert "is a symbolic link" in responseHttp.json()["detail"]
    assert os.path.islink(remoteBindings.fsBindingPath(sUuid))
    assert _fbaRead(sElsewhere) == BA_UNREADABLE
    assert not any(".unreadable-" in sName for sName in _flistBindingsDirectory())


@pytest.mark.falsification
def testTheAgentLaneCannotSetARecordAside(tApp):
    """Moving the record is the researcher's; an agent token is refused.

    Kills: dropping the agent-lane refusal from the set-aside handler.
    """
    client, _, sUuid = tApp
    _fnPlantRecord(sUuid, BA_UNREADABLE)
    responseHttp = client.post(
        S_SET_ASIDE_PATH, headers={"X-Vaibify-Session": "agent-token"})
    assert responseHttp.status_code == 403
    assert _fbaRead(remoteBindings.fsBindingPath(sUuid)) == BA_UNREADABLE
    assert not any(".unreadable-" in sName for sName in _flistBindingsDirectory())


def _fnRouteTheCliThroughTheApp(monkeypatch, client):
    from vaibify.cli import configLoader, hubSession
    monkeypatch.setattr(
        configLoader, "fconfigResolveProject",
        lambda sProjectName: SimpleNamespace(sProjectName=S_PROJECT_NAME),
    )
    monkeypatch.setattr(
        hubSession, "fdictOpenResearcherSession",
        lambda sName, iPort, sPath: {
            "sContainerId": S_CONTAINER_ID, "sWorkflowPath": "",
        },
    )
    monkeypatch.setattr(hubSession, "fnReleaseContainer", lambda dictSession: None)

    def ftSend(dictSession, sMethod, sPath, dictFields):
        responseHttp = (client.get(sPath, params=dictFields) if sMethod == "GET"
                        else client.post(sPath, json=dictFields))
        return responseHttp.status_code, responseHttp.json()

    monkeypatch.setattr(hubSession, "ftSendSessionRequest", ftSend)


def testTheCliOffersAndPerformsTheSetAside(tApp, monkeypatch):
    from vaibify.cli import commandBindings
    client, _, sUuid = tApp
    _fnPlantRecord(sUuid, BA_UNREADABLE)
    _fnRouteTheCliThroughTheApp(monkeypatch, client)
    resultReview = CliRunner().invoke(commandBindings.fnBindingsCommand, [])
    assert resultReview.exit_code == 0, resultReview.output
    assert f"vaibify bindings --set-aside -p {S_PROJECT_NAME}" in resultReview.output
    resultSetAside = CliRunner().invoke(
        commandBindings.fnBindingsCommand, ["--set-aside"])
    assert resultSetAside.exit_code == 0, resultSetAside.output
    assert f"Moved the unreadable binding record aside as {sUuid}.json.unreadable-" in (
        resultSetAside.output)
    assert not os.path.lexists(remoteBindings.fsBindingPath(sUuid))


def testTheCliReportsTheRefusalInTheServersWords(tApp, monkeypatch):
    from vaibify.cli import commandBindings
    client, _, _sUuid = tApp
    _fnRouteTheCliThroughTheApp(monkeypatch, client)
    resultSetAside = CliRunner().invoke(
        commandBindings.fnBindingsCommand, ["--set-aside"])
    assert resultSetAside.exit_code == 4
    assert "can be read, so nothing was moved aside" in resultSetAside.output
