"""The agent lane may edit a step's definition, never its attestation.

``update-step`` is agent-safe, and ``StepUpdateRequest`` carries
``dictVerification``: the researcher's own sign-off. Through a real
``TestClient`` with a real agent token (container name != container id)
these tests drive the PUT the way a compromised in-container agent
could, and assert on what the stored workflow says afterwards.
"""

import copy

import pytest
from fastapi.testclient import TestClient

from vaibify.gui import actionCatalog
from vaibify.gui import pipelineServer
from tests import testAgentLaneEnforcement as agentLaneModule
from tests.sessionTokenTestHelper import fsBootstrapCredential

S_CONTAINER_ID = agentLaneModule.S_CONTAINER_ID
S_CONTAINER_NAME = agentLaneModule.S_CONTAINER_NAME
S_AGENT_TOKEN = agentLaneModule.S_AGENT_TOKEN
S_WORKFLOW_PATH = agentLaneModule.S_WORKFLOW_PATH
S_FAR_FUTURE = "2099-01-01 00:00:00 UTC"


class ClockedDockerDouble(agentLaneModule.MockDockerConnection):
    """The lane-enforcement double, plus a container clock it can report.

    ``sClockReading`` is what the container's clock reads; a class
    attribute so a test can make it differ from the hub's, and ``None``
    makes the clock unreadable.
    """

    sClockReading = "2026-03-04 05:06:07 UTC"

    def fsReadClockUtc(self, sContainerId):
        if self.sClockReading is None:
            raise OSError("Cannot read the container's clock")
        return self.sClockReading


def ftBuildLanes(classDocker=ClockedDockerDouble):
    """Return (browser client, agent client, application) on one app."""
    with pytest.MonkeyPatch.context() as monkeyContext:
        monkeyContext.setattr(
            pipelineServer, "_fconnectionCreateDocker", classDocker,
        )
        appViewer = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    clientBrowser = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    responseConnect = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseConnect.status_code == 200, responseConnect.text
    clientBrowser.headers["X-Vaibify-Lease"] = (
        responseConnect.json()["sLeaseId"])
    appViewer.state.dictContainerOwners[S_CONTAINER_NAME].sAgentToken = (
        S_AGENT_TOKEN)
    clientAgent = TestClient(
        appViewer,
        headers={
            actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
            "Host": "host.docker.internal:8050",
        },
    )
    return clientBrowser, clientAgent, appViewer


@pytest.fixture
def tupleLanes():
    return ftBuildLanes()


def _fdictStoredVerification(clientBrowser):
    responseHttp = clientBrowser.get(
        f"/api/steps/{S_CONTAINER_ID}/0")
    assert responseHttp.status_code == 200, responseHttp.text
    return responseHttp.json()["dictVerification"]


@pytest.mark.falsification
def testAnAgentCannotWriteTheResearchersAttestation(tupleLanes):
    """An agent token must not set ``sUser: passed`` through update-step.

    The PUT is agent-safe because an agent legitimately edits a step's
    commands; ``dictVerification`` rode in the same body unfiltered, so
    the sign-off the PROOF levels rest on was writable by the process
    the sign-off exists to check.

    Kills: stepRoutes.fdictUpdateStep: the agent-lane allowlist branch
    `if fbRequestRidesAgentLane(requestHttp):` neutralized to
    `if False:`.
    """
    clientBrowser, clientAgent, _appViewer = tupleLanes
    dictBefore = copy.deepcopy(_fdictStoredVerification(clientBrowser))
    responseHttp = clientAgent.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {
            "sUser": "passed", "sUnitTest": "passed",
            "sLastUserUpdate": S_FAR_FUTURE,
        }},
    )
    assert responseHttp.status_code == 403, responseHttp.text
    assert "dictVerification" in responseHttp.text
    assert _fdictStoredVerification(clientBrowser) == dictBefore


@pytest.mark.parametrize("sField,value", [
    ("bInteractive", True),
    ("bRunEnabled", False),
    ("dictTests", {}),
    ("dictRunStats", {"fWallClock": 1.0}),
    ("listRemoteData", []),
    ("dictPlotFileCategories", {}),
])
def testAnAgentIsRefusedEveryFieldOffTheAllowlistByName(
    tupleLanes, sField, value,
):
    _clientBrowser, clientAgent, _appViewer = tupleLanes
    responseHttp = clientAgent.put(
        f"/api/steps/{S_CONTAINER_ID}/0", json={sField: value})
    assert responseHttp.status_code == 403, responseHttp.text
    assert sField in responseHttp.text


def testAnAgentStillEditsAStepDefinition(tupleLanes):
    clientBrowser, clientAgent, _appViewer = tupleLanes
    responseHttp = clientAgent.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"sDescription": "Fits the model.",
              "saDataCommands": ["python fit.py"]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictStep = clientBrowser.get(
        f"/api/steps/{S_CONTAINER_ID}/0").json()
    assert dictStep["sDescription"] == "Fits the model."
    assert dictStep["saDataCommands"] == ["python fit.py"]


def testTheBrowserCanStillWriteTheVerification(tupleLanes):
    clientBrowser, _clientAgent, _appViewer = tupleLanes
    responseHttp = clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {"sUser": "passed"}},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert _fdictStoredVerification(clientBrowser)["sUser"] == "passed"


@pytest.mark.falsification
def testAClientSuppliedFutureTimestampIsNotStored(tupleLanes):
    """A far-future ``sLastUserUpdate`` would make a sign-off immune.

    Every later "was the plot changed after the sign-off" comparison
    reads that timestamp, so the server stamps it from its own clock.

    Kills: stepRoutes.fdictUpdateStep: the call
    `_fnStampServerSideUserUpdate(...)` removed.
    """
    clientBrowser, _clientAgent, _appViewer = tupleLanes
    responseHttp = clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {
            "sUser": "passed", "sLastUserUpdate": S_FAR_FUTURE}},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    sStored = _fdictStoredVerification(clientBrowser)["sLastUserUpdate"]
    assert sStored != S_FAR_FUTURE
    assert sStored.endswith(" UTC") and sStored < "2099"


def testAnUnchangedSignOffKeepsTheStoredTimestamp(tupleLanes):
    clientBrowser, _clientAgent, _appViewer = tupleLanes
    clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {"sUser": "passed"}},
    )
    sFirst = _fdictStoredVerification(clientBrowser)["sLastUserUpdate"]
    clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {
            "sUser": "passed", "sLastUserUpdate": S_FAR_FUTURE}},
    )
    dictAfter = _fdictStoredVerification(clientBrowser)
    assert dictAfter["sLastUserUpdate"] == sFirst


def testEveryAllowedFieldIsARealRequestField():
    from vaibify.gui.pipelineServer import StepUpdateRequest
    from vaibify.gui.routes import stepRoutes
    setUnknown = (
        set(stepRoutes.SET_AGENT_WRITABLE_STEP_FIELDS)
        - set(StepUpdateRequest.model_fields)
    )
    assert setUnknown == set()
    assert "dictVerification" not in (
        stepRoutes.SET_AGENT_WRITABLE_STEP_FIELDS)
