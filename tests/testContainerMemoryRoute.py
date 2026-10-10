"""GET /api/monitor/{id}/memory: the memory watch's answer over real HTTP.

The URL carries the Docker ID; the record is keyed by the container
NAME, as the owner map is. The harness keeps the two distinct
("test-container" versus "abc123container"), so a route that looked the
record up by the id it was handed would find nothing and fail here.

The route never execs: it reads the record the sampler keeps. The test
proves that by making every container-touching method of the Docker leg
raise once the session has claimed the container.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.docker import cgroupMemory
from vaibify.gui import containerMemoryWatch, pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testPipelineServerRoutes import (
    S_CONTAINER_ID, S_WORKFLOW_PATH, _fmockCreateDocker,
)


S_CONTAINER_NAME = "test-container"
I_GIGABYTE = 2 ** 30


@pytest.fixture
def clientAndApp():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", _fmockCreateDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    client = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)})
    return client, app


def _fnClaim(client):
    responseConnect = client.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH})
    assert responseConnect.status_code == 200, responseConnect.text
    client.headers["X-Vaibify-Lease"] = responseConnect.json()["sLeaseId"]


def _fnRefuseEveryContainerCall(app):
    connectionLeg = app.state.dictRouteContext["docker"]

    def fnRefuse(*args, **kwargs):
        raise AssertionError("the memory route reached the container")

    for sMethod in (
        "ftResultExecuteCommand", "ftRunInContainerStreamed",
        "fsReadCgroupMemory", "fdictReadContainerState",
    ):
        setattr(connectionLeg, sMethod, fnRefuse)


def _fnSeedMeasurement(app, iKills=0):
    dictParsed = cgroupMemory.fdictParseCgroupMemory("")
    dictParsed.update({
        "sLimitKind": cgroupMemory.S_LIMIT_FINITE,
        "iLimitBytes": 6 * I_GIGABYTE, "iUsageBytes": 2 * I_GIGABYTE,
        "iInactiveFileBytes": 0, "iWorkingSetBytes": 2 * I_GIGABYTE,
        "iOomKillCount": iKills, "iOomCount": 0,
    })
    containerMemoryWatch.fnRecordMeasurement(
        app.state.dictContainerMemory, S_CONTAINER_NAME, S_CONTAINER_ID,
        dictParsed, {"Running": True}, datetime.now(timezone.utc))


@pytest.mark.falsification
def testTheRouteAnswersFromTheNameKeyedRecordForTheIdInTheUrl(clientAndApp):
    """Kills: looking the record up by the Docker id the URL carries."""
    client, app = clientAndApp
    _fnClaim(client)
    assert S_CONTAINER_NAME in app.state.dictContainerOwners
    _fnSeedMeasurement(app)
    _fnRefuseEveryContainerCall(app)
    responseMemory = client.get(f"/api/monitor/{S_CONTAINER_ID}/memory")
    assert responseMemory.status_code == 200, responseMemory.text
    dictBody = responseMemory.json()
    assert dictBody["sContainerName"] == S_CONTAINER_NAME
    assert dictBody["dictCurrent"]["sLevel"] == containerMemoryWatch.S_LEVEL_OK
    assert dictBody["dictCurrent"]["sChipText"] == "Memory ~2 / 6 GB"
    assert dictBody["dictCurrent"]["fSampleAgeSeconds"] is not None


def testAContainerNotYetSampledReadsUnknownNotOk(clientAndApp):
    client, _app = clientAndApp
    _fnClaim(client)
    dictCurrent = client.get(
        f"/api/monitor/{S_CONTAINER_ID}/memory").json()["dictCurrent"]
    assert dictCurrent["sState"] == containerMemoryWatch.S_STATE_NO_SAMPLE_YET
    assert dictCurrent["sLevel"] == containerMemoryWatch.S_LEVEL_UNKNOWN


def testIncidentsArriveWithTheirSentences(clientAndApp):
    client, app = clientAndApp
    _fnClaim(client)
    _fnSeedMeasurement(app, iKills=2)
    dictBody = client.get(f"/api/monitor/{S_CONTAINER_ID}/memory").json()
    assert dictBody["iKillCount"] == 2
    assert [d["sIncidentId"] for d in dictBody["listIncidents"]] == [
        f"beforeObservation:{S_CONTAINER_ID}:2"]
    assert "vaibify did not observe when" in (
        dictBody["listIncidents"][0]["sSentence"])


def testAnUnclaimedContainerIsRefused(clientAndApp):
    client, _app = clientAndApp
    responseMemory = client.get(f"/api/monitor/{S_CONTAINER_ID}/memory")
    assert responseMemory.status_code == 403


def testTheResourceMonitorCarriesTheKillCount(clientAndApp):
    client, app = clientAndApp
    _fnClaim(client)
    _fnSeedMeasurement(app, iKills=1)
    with patch(
        "vaibify.gui.routes.systemRoutes.fdictGetContainerStats",
        return_value={"bAvailable": True, "sMemoryUsage": "2GiB",
                      "sMemoryLimit": "6GiB"},
    ):
        dictStats = client.get(f"/api/monitor/{S_CONTAINER_ID}").json()
    assert dictStats["sMemoryKillText"] == (
        "1 process killed for lack of memory")
