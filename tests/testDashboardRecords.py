"""The saved dashboard layout: one host record per environment and per project file.

Source: ``vaibify/config/dashboardRecords.py`` and
``vaibify/gui/routes/dashboardStateRoutes.py``.

Every assertion reads the record files on disk under a private HOME, and
the route tests resolve the environment name from a container id that is
deliberately NOT the name (the repo's name-vs-id rule). The expected
revisions are counted by hand from the sequence of writes each test
makes, never read back from the code under test.
"""

import json
import os
import stat
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.config import dashboardRecords
from vaibify.gui import actionCatalog, researcherActions
from vaibify.gui.routes import dashboardStateRoutes

S_CONTAINER_ID = "a1b2c3d4e5f6"
S_ENVIRONMENT = "labEnvironment"
S_REPO = "/workspace/analysisRepo"
S_FIRST_PROJECT = S_REPO + "/.vaibify/projects/first.json"
S_SECOND_PROJECT = S_REPO + "/.vaibify/projects/second.json"
T_ROUTE_PATHS = (
    ("GET", "/api/dashboard-state/{sContainerId}/environment"),
    ("PUT", "/api/dashboard-state/{sContainerId}/environment"),
    ("GET", "/api/dashboard-state/{sContainerId}/project"),
    ("PUT", "/api/dashboard-state/{sContainerId}/project"),
)


@pytest.fixture()
def tClient(tmp_path, monkeypatch):
    """A route app whose container id is not the environment's name."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        dashboardStateRoutes, "fsContainerNameForId",
        lambda connectionDocker, sContainerId: (
            S_ENVIRONMENT if sContainerId == S_CONTAINER_ID else sContainerId
        ),
    )
    app = FastAPI()
    dashboardStateRoutes.fnRegisterAll(app, {"docker": SimpleNamespace()})
    return TestClient(app)


def _fresponsePutProject(client, sWorkflowPath, iBase, dictState):
    return client.put(
        f"/api/dashboard-state/{S_CONTAINER_ID}/project",
        json={"sProjectRepoPath": S_REPO, "sWorkflowPath": sWorkflowPath,
              "iBaseRevision": iBase, "dictState": dictState},
    )


def _fdictGetProject(client, sWorkflowPath):
    return client.get(
        f"/api/dashboard-state/{S_CONTAINER_ID}/project",
        params={"sProjectRepoPath": S_REPO, "sWorkflowPath": sWorkflowPath},
    ).json()


def _fresponsePutEnvironment(client, iBase, dictState):
    return client.put(
        f"/api/dashboard-state/{S_CONTAINER_ID}/environment",
        json={"iBaseRevision": iBase, "dictState": dictState},
    )


def testAnAbsentRecordIsRevisionZeroAndEmpty(tClient):
    dictRecord = tClient.get(
        f"/api/dashboard-state/{S_CONTAINER_ID}/environment").json()
    assert dictRecord == {"iRevision": 0, "dictState": {},
                          "bUnreadable": False}


def testTheRecordLivesUnderTheEnvironmentNameNotTheContainerId(tClient):
    dictState = {"dictTerminals": {"listPanes": []}}
    assert _fresponsePutEnvironment(tClient, 0, dictState).status_code == 200
    sPath = dashboardRecords.fsEnvironmentRecordPath(S_ENVIRONMENT)
    with open(sPath, encoding="utf-8") as fileRecord:
        dictOnDisk = json.load(fileRecord)
    assert dictOnDisk["dictState"] == dictState
    assert dictOnDisk["iRevision"] == 1
    assert not os.path.exists(os.path.join(
        dashboardRecords.fsDashboardStateRoot(), S_CONTAINER_ID))


def testRecordFilesAndDirectoriesArePrivate(tClient):
    _fresponsePutProject(tClient, S_FIRST_PROJECT, 0, {"sLeftTab": "steps"})
    sPath = dashboardRecords.fsProjectRecordPath(
        S_ENVIRONMENT, S_REPO, ".vaibify/projects/first.json")
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
    sDirectory = os.path.dirname(sPath)
    while sDirectory != os.path.dirname(dashboardRecords.fsDashboardStateRoot()):
        assert stat.S_IMODE(os.stat(sDirectory).st_mode) == 0o700, sDirectory
        sDirectory = os.path.dirname(sDirectory)


@pytest.mark.falsification
def testAStaleBaseRevisionIsRefusedAndChangesNothing(tClient):
    """A write based on revision 1 after revision 2 was stored is refused.

    Kills: dropping the base-revision comparison, which let a window's
    delayed snapshot overwrite a later change made in another window.
    """
    assert _fresponsePutEnvironment(
        tClient, 0, {"dictAgentCouncil": {"bOpen": False}}).status_code == 200
    assert _fresponsePutEnvironment(
        tClient, 1, {"dictAgentCouncil": {"bOpen": True}}).status_code == 200
    responseHttp = _fresponsePutEnvironment(
        tClient, 1, {"dictAgentCouncil": {"bOpen": False}})
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]["sError"] == "DASHBOARD-RECORD-CONFLICT"
    assert responseHttp.json()["detail"]["iRevision"] == 2
    dictRecord = dashboardRecords.fdictReadRecord(
        dashboardRecords.fsEnvironmentRecordPath(S_ENVIRONMENT))
    assert dictRecord["iRevision"] == 2
    assert dictRecord["dictState"] == {"dictAgentCouncil": {"bOpen": True}}


@pytest.mark.falsification
def testTwoConcurrentWritesAtOneBaseYieldExactlyOneSuccess(tmp_path, monkeypatch):
    """Released together on one base revision, exactly one write lands.

    Kills: comparing outside the lock, which let both writers read
    revision 0 and both store revision 1.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sPath = dashboardRecords.fsEnvironmentRecordPath(S_ENVIRONMENT)
    for iRound in range(20):
        barrierStart = threading.Barrier(2)
        listOutcomes = []

        def fnWrite(sTag):
            barrierStart.wait()
            try:
                dashboardRecords.fdictWriteRecordIfCurrent(
                    sPath, dashboardRecords.S_SCOPE_ENVIRONMENT, iRound,
                    {"dictAgentCouncil": {"sTag": sTag}}, {})
                listOutcomes.append(sTag)
            except dashboardRecords.RecordRevisionConflictError:
                listOutcomes.append("conflict")

        listThreads = [threading.Thread(target=fnWrite, args=(s,))
                       for s in ("first", "second")]
        for threadWriter in listThreads:
            threadWriter.start()
        for threadWriter in listThreads:
            threadWriter.join()
        assert sorted(listOutcomes).count("conflict") == 1, listOutcomes
        assert dashboardRecords.fdictReadRecord(sPath)["iRevision"] == iRound + 1


def testAReloadStartsFromTheStoredRevision(tClient):
    """Save, read back as a reloaded page would, save again: accepted."""
    _fresponsePutProject(tClient, S_FIRST_PROJECT, 0, {"sLeftTab": "steps"})
    dictReloaded = _fdictGetProject(tClient, S_FIRST_PROJECT)
    assert dictReloaded["iRevision"] == 1
    responseHttp = _fresponsePutProject(
        tClient, S_FIRST_PROJECT, dictReloaded["iRevision"],
        {"sLeftTab": "files"})
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"iRevision": 2}


@pytest.mark.falsification
def testTwoProjectFilesInOneRepositoryKeepSeparateRecords(tClient):
    """Sibling project files never overwrite one another's layout.

    Kills: keying the project record by the repository alone.
    """
    _fresponsePutProject(tClient, S_FIRST_PROJECT, 0, {"sLeftTab": "steps"})
    _fresponsePutProject(tClient, S_SECOND_PROJECT, 0, {"sLeftTab": "files"})
    assert _fdictGetProject(tClient, S_FIRST_PROJECT)["dictState"] == {
        "sLeftTab": "steps"}
    assert _fdictGetProject(tClient, S_SECOND_PROJECT)["dictState"] == {
        "sLeftTab": "files"}
    assert dashboardRecords.fbRemoveProjectRecord(
        S_ENVIRONMENT, S_REPO, ".vaibify/projects/first.json") is True
    assert _fdictGetProject(tClient, S_FIRST_PROJECT)["iRevision"] == 0
    assert _fdictGetProject(tClient, S_SECOND_PROJECT)["dictState"] == {
        "sLeftTab": "files"}


def testAProjectFileOutsideItsRepositoryIsNeverSaved(tClient):
    responseHttp = _fresponsePutProject(
        tClient, "/elsewhere/project.json", 0, {"sLeftTab": "steps"})
    assert responseHttp.status_code == 400
    assert not os.path.exists(dashboardRecords.fsDashboardStateRoot())


@pytest.mark.parametrize("sPath", [
    S_REPO + "/../escape/project.json", S_REPO + "//x.json", "relative.json",
])
def testAnUnnormalizedProjectPathIsRefused(tClient, sPath):
    assert _fresponsePutProject(
        tClient, sPath, 0, {"sLeftTab": "steps"}).status_code == 400


@pytest.mark.falsification
def testARecordCannotHoldAStatusField(tClient):
    """Only layout fields are stored; a verdict or badge is refused.

    Kills: dropping the per-scope field allow-list, which let a saved
    layout carry state the dashboard would then show without asking.
    """
    responseHttp = _fresponsePutProject(
        tClient, S_FIRST_PROJECT, 0,
        {"sLeftTab": "steps", "dictStepStatus": {"A01": "passed"}})
    assert responseHttp.status_code == 400
    assert "dictStepStatus" in responseHttp.json()["detail"]["sMessage"]
    assert _fdictGetProject(tClient, S_FIRST_PROJECT)["iRevision"] == 0


def testAnOversizedRecordIsRefused(tClient):
    sHuge = "x" * (dashboardRecords.I_MAXIMUM_RECORD_BYTES + 1)
    assert _fresponsePutEnvironment(
        tClient, 0, {"dictTerminals": {"sNote": sHuge}}).status_code == 400


def testAnUnreadableRecordSaysSoAndIsReplacedByTheNextSave(tClient):
    sPath = dashboardRecords.fsEnvironmentRecordPath(S_ENVIRONMENT)
    os.makedirs(os.path.dirname(sPath))
    with open(sPath, "w", encoding="utf-8") as fileRecord:
        fileRecord.write("{not json")
    dictRecord = tClient.get(
        f"/api/dashboard-state/{S_CONTAINER_ID}/environment").json()
    assert dictRecord["bUnreadable"] is True
    assert dictRecord["iRevision"] == 0
    assert _fresponsePutEnvironment(
        tClient, 0, {"dictTerminals": {}}).status_code == 200


@pytest.mark.falsification
def testTheAgentLaneIsRefusedOnEveryRoute(tClient):
    """An agent token reads nothing and writes nothing, on all four routes.

    Kills: dropping the per-handler agent refusal from the environment
    PUT, which let an agent token rewrite how the researcher works.
    """
    dictHeaders = {"X-Vaibify-Session": "agent-token"}
    dictProject = {"sProjectRepoPath": S_REPO, "sWorkflowPath": S_FIRST_PROJECT}
    for sMethod, sRoute in (("GET", "/environment"), ("PUT", "/environment"),
                            ("GET", "/project"), ("PUT", "/project")):
        sUrl = f"/api/dashboard-state/{S_CONTAINER_ID}{sRoute}"
        if sMethod == "GET":
            responseHttp = tClient.get(
                sUrl, headers=dictHeaders, params=dictProject)
        else:
            responseHttp = tClient.put(sUrl, headers=dictHeaders, json=dict(
                dictProject, iBaseRevision=0, dictState={}))
        assert responseHttp.status_code == 403, (sMethod, sRoute)
    assert not os.path.exists(dashboardRecords.fsDashboardStateRoot())


@pytest.mark.parametrize("tRoute", T_ROUTE_PATHS)
def testTheCatalogRefusesTheAgentEveryRoute(tRoute):
    assert actionCatalog.fbAgentLanePermitsRoute(*tRoute) is False


def testNoReadIsRegisteredAsAGuiOnlyMutation():
    assert not [t for t in researcherActions.SET_GUI_ONLY_ROUTES
                if t[0] == "GET"]
    for tRoute in T_ROUTE_PATHS:
        if tRoute[0] == "PUT":
            assert tRoute in researcherActions.SET_GUI_ONLY_ROUTES


def testAnEnvironmentDeletionTakesItsRecords(tmp_path, monkeypatch):
    from vaibify.gui import environmentDeletion
    monkeypatch.setenv("HOME", str(tmp_path))
    sPath = dashboardRecords.fsEnvironmentRecordPath(S_ENVIRONMENT)
    dashboardRecords.fdictWriteRecordIfCurrent(
        sPath, dashboardRecords.S_SCOPE_ENVIRONMENT, 0, {}, {})
    assert environmentDeletion._flistRemoveDashboardRecords(
        S_ENVIRONMENT) == ["dashboard layout records"]
    assert not os.path.exists(os.path.dirname(sPath))
    assert environmentDeletion._flistRemoveDashboardRecords(S_ENVIRONMENT) == []


def testTheRecordsAreAHostFileTheEnvironmentNameOwns(tmp_path, monkeypatch):
    """A rename moves what ``environmentResources`` lists, so it is listed."""
    from vaibify.gui import environmentResources
    monkeypatch.setenv("HOME", str(tmp_path))
    dictKinds = dict(environmentResources.flistHostFilesOwnedBy(S_ENVIRONMENT))
    assert dictKinds["dashboard layout records"] == (
        dashboardRecords.fsEnvironmentDirectoryFor(S_ENVIRONMENT))
