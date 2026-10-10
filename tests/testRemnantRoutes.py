"""The remnant routes act only on what they re-verify, through authorities.

The hub application is real, the connection double's container NAME
differs from its id, and every request is made over HTTP as the browser
makes it. What is pinned: the agent lane is refused on all three
routes; a rescan only sets the loop's event; an id the scan no longer
holds answers "already gone"; a session whose pid was recycled is
refused without a signal; and each action reaches exactly its named
authority.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import keepAliveManager
from vaibify.docker import containerManager
from vaibify.gui import pipelineServer, remnantScanner, terminalContainment
from tests.sessionTokenTestHelper import fsBootstrapCredential

S_NAME = "scannedProject"
S_ID = "5ca11ed" * 9 + "5ca1"
S_TABLE = f"@@ clock 1700000000 100 4096 900 899\n1 0 1 1 0 S 10 300 0 sleep\n40 0 40 40 34816 S 500 1000 1000 bash\n"


class _ConnectionFake:
    def __init__(self):
        self.sTable = S_TABLE
        self.listAll = [{"sContainerId": "id-stopped", "sName": "vaibifyTermContain1",
                         "sStatus": "exited", "dictLabels": {}}]

    def flistGetRunningContainers(self):
        return [{"sContainerId": S_ID, "sShortId": S_ID[:12], "sName": S_NAME,
                 "sImage": "img"}]

    def fsReadProcessTable(self, sContainerId):
        assert sContainerId == S_ID
        return self.sTable

    def flistListAllContainers(self):
        return list(self.listAll)


@pytest.fixture
def tHub():
    connectionFake = _ConnectionFake()
    with patch.object(pipelineServer, "_fconnectionCreateDocker", lambda: connectionFake):
        app = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    clientBrowser = TestClient(app, headers={"X-Session-Token": fsBootstrapCredential(app)})
    return app, clientBrowser, connectionFake


def _fdictSessionItem():
    return {"sItemId": "s1", "sCategory": remnantScanner.S_CATEGORY_UNTRACKED_SESSION,
            "sTier": "possibly", "sContainerName": S_NAME, "sEvidence": "e", "sRemedy": "r",
            "sAction": remnantScanner.S_ACTION_TERMINATE, "bConfirmRequired": False,
            "dictIdentity": {"sContainerId": S_ID, "iLeaderPid": 40, "iStartTicks": 500}}


def test_every_route_refuses_the_agent_token_lane(tHub):
    app, clientBrowser, _ = tHub
    dictAgent = {"X-Vaibify-Session": "agent-token-from-inside-a-container"}
    assert clientBrowser.get("/api/system/remnants", headers=dictAgent).status_code == 403
    assert clientBrowser.post("/api/system/remnants/rescan", headers=dictAgent).status_code == 403
    assert clientBrowser.post("/api/system/remnants/remove", headers=dictAgent,
                              json={"listItemIds": ["s1"]}).status_code == 403


def test_the_read_returns_the_cached_scan_and_the_reaper_health(tHub):
    app, clientBrowser, _ = tHub
    app.state.dictRemnantScan["listItems"] = [_fdictSessionItem()]
    app.state.dictRemnantScan["sScannedIso"] = "2026-01-01T00:00:00+00:00"
    app.state.dictReaperHealth = {"x": {"sOutcome": "ran", "iRemoved": 2}}
    dictBody = clientBrowser.get("/api/system/remnants").json()
    assert dictBody["listItems"][0]["sItemId"] == "s1"
    assert dictBody["dictReaperHealth"] == {"x": {"sOutcome": "ran", "iRemoved": 2}}
    assert dictBody["sGlyphTitle"] == "Leftover processes and files"
    assert dictBody["sScannedIso"] == "2026-01-01T00:00:00+00:00"


def test_a_rescan_only_sets_the_loops_event(tHub):
    app, clientBrowser, _ = tHub
    app.state.eventReaperRescan = asyncio.Event()
    with patch.object(remnantScanner, "fnRunRemnantScan") as mockScan:
        assert clientBrowser.post("/api/system/remnants/rescan").json() == {"bRequested": True}
        mockScan.assert_not_called()
    assert app.state.eventReaperRescan.is_set()


def test_a_stale_id_answers_already_gone(tHub):
    app, clientBrowser, _ = tHub
    app.state.eventReaperRescan = asyncio.Event()
    [dictOutcome] = clientBrowser.post(
        "/api/system/remnants/remove", json={"listItemIds": ["nope"]}).json()["listOutcomes"]
    assert dictOutcome["bRemoved"] is False
    assert "already gone" in dictOutcome["sOutcome"]
    assert app.state.eventReaperRescan.is_set(), "a removal always triggers a pass"


def test_a_session_is_ended_through_terminate_and_prove_after_a_live_match(tHub, monkeypatch):
    app, clientBrowser, _ = tHub
    app.state.dictRemnantScan["listItems"] = [_fdictSessionItem()]
    listCalls = []
    monkeypatch.setattr(terminalContainment, "fdictTerminateAndProveGroup",
                        lambda c, sName, sId, iGroup: listCalls.append((sName, sId, iGroup))
                        or {"bProvenEmpty": True, "sDetail": ""})
    [dictOutcome] = clientBrowser.post(
        "/api/system/remnants/remove", json={"listItemIds": ["s1"]}).json()["listOutcomes"]
    assert listCalls == [(S_NAME, S_ID, 40)]
    assert dictOutcome["bRemoved"] is True and "proven empty" in dictOutcome["sOutcome"]


@pytest.mark.falsification
def test_a_session_whose_pid_was_recycled_is_refused_without_a_signal(tHub, monkeypatch):
    """Kills: ``_fbSessionLeaderStillMatches`` short-circuited to True, so
    a recycled pid, a different process with the same number, is ended.

    Oracle: the process table the connection returns now, against the
    identity the scan recorded; the same pid with a later start clock
    is a different process.
    """
    app, clientBrowser, connectionFake = tHub
    app.state.dictRemnantScan["listItems"] = [_fdictSessionItem()]
    connectionFake.sTable = S_TABLE.replace("40 0 40 40 34816 S 500", "40 0 40 40 34816 S 777")
    listCalls = []
    monkeypatch.setattr(terminalContainment, "fdictTerminateAndProveGroup",
                        lambda *tArgs: listCalls.append(tArgs) or {"bProvenEmpty": True, "sDetail": ""})
    [dictOutcome] = clientBrowser.post(
        "/api/system/remnants/remove", json={"listItemIds": ["s1"]}).json()["listOutcomes"]
    assert listCalls == [], "a recycled pid was signalled"
    assert dictOutcome["bRemoved"] is False and "already gone" in dictOutcome["sOutcome"]


def test_each_action_reaches_exactly_its_authority(tHub, monkeypatch):
    app, clientBrowser, connectionFake = tHub
    listCalls = []
    monkeypatch.setattr(keepAliveManager, "fnStopProvablyOursKeepAlive",
                        lambda iPid: listCalls.append(("ours", iPid)))
    monkeypatch.setattr(keepAliveManager, "fnStopKeepAliveProcess",
                        lambda iPid, sIso: listCalls.append(("gated", iPid, sIso)))
    monkeypatch.setattr(keepAliveManager, "fnStopKeepAlive",
                        lambda sName: listCalls.append(("lane", sName)))
    monkeypatch.setattr(keepAliveManager, "fdictReadKeepAliveRecord",
                        lambda sName: {"iPid": 77, "sStartedIso": "x"})
    monkeypatch.setattr(containerManager, "fnRemoveStoppedContainerById",
                        lambda sId: listCalls.append(("remove", sId)))
    from datetime import datetime, timezone
    monkeypatch.setattr("vaibify.config.processLiveness.flistEnumerateProcessesNamed",
                        lambda sName: [{"iPid": 55, "iParentPid": 1, "sCommand": "caffeinate -s",
                                        "datetimeStart": datetime(2026, 1, 1, tzinfo=timezone.utc)}])
    app.state.dictRemnantScan["listItems"] = [
        {"sItemId": "k1", "sTier": "proven", "sAction": remnantScanner.S_ACTION_KILL,
         "sContainerName": "", "dictIdentity": {"iPid": 44, "sStartedIso": "i"}},
        {"sItemId": "k2", "sTier": "possibly", "sAction": remnantScanner.S_ACTION_KILL,
         "sContainerName": "", "dictIdentity": {"iPid": 55, "sStartedIso": "2026-01-01T00:00:00+00:00"}},
        {"sItemId": "k3", "sTier": "possibly", "sAction": remnantScanner.S_ACTION_KILL,
         "sContainerName": "", "dictIdentity": {"iPid": 55, "sStartedIso": "2020-01-01T00:00:00+00:00"}},
        {"sItemId": "l1", "sTier": "possibly", "sAction": remnantScanner.S_ACTION_STOP,
         "sContainerName": "lane", "dictIdentity": {"sRegistryName": "lane", "iPid": 77}},
        {"sItemId": "r1", "sTier": "proven", "sAction": remnantScanner.S_ACTION_REMOVE,
         "sContainerName": "vaibifyTermContain1", "dictIdentity": {"sContainerId": "id-stopped"}},
        {"sItemId": "n1", "sTier": "proven", "sAction": remnantScanner.S_ACTION_NONE,
         "sContainerName": "", "dictIdentity": {}},
    ]
    listOutcomes = clientBrowser.post("/api/system/remnants/remove", json={
        "listItemIds": ["k1", "k2", "k3", "l1", "r1", "n1"]}).json()["listOutcomes"]
    assert listCalls == [("ours", 44), ("gated", 55, "2026-01-01T00:00:00+00:00"),
                         ("lane", "lane"), ("remove", "id-stopped")]
    dictByItem = {d["sItemId"]: d for d in listOutcomes}
    assert dictByItem["k3"]["bRemoved"] is False and "already gone" in dictByItem["k3"]["sOutcome"]
    assert dictByItem["n1"]["bRemoved"] is False and "cannot be removed" in dictByItem["n1"]["sOutcome"]
    assert all(dictByItem[s]["bRemoved"] for s in ("k1", "k2", "l1", "r1"))


def test_a_running_container_is_not_removed(tHub, monkeypatch):
    app, clientBrowser, connectionFake = tHub
    connectionFake.listAll[0]["sStatus"] = "running"
    listCalls = []
    monkeypatch.setattr(containerManager, "fnRemoveStoppedContainerById",
                        lambda sId: listCalls.append(sId))
    app.state.dictRemnantScan["listItems"] = [
        {"sItemId": "r1", "sTier": "proven", "sAction": remnantScanner.S_ACTION_REMOVE,
         "sContainerName": "vaibifyTermContain1", "dictIdentity": {"sContainerId": "id-stopped"}}]
    [dictOutcome] = clientBrowser.post(
        "/api/system/remnants/remove", json={"listItemIds": ["r1"]}).json()["listOutcomes"]
    assert listCalls == [] and dictOutcome["bRemoved"] is False
    assert "running now" in dictOutcome["sOutcome"]
