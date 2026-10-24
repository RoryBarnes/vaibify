"""An in-container agent's edit reaches the dashboard that has the project open.

Every route save recorded a self-write fingerprint and never advanced
the workflow epoch, and the file-status poll attaches the workflow only
for a client whose epoch is stale. So a step an agent added never
reached the open tab: the researcher saw it only after leaving the
project and coming back (alpha tester, 2026-10-23). The agent lane is
the one origin of a save the open tab did not make, so an agent's
committed save advances the epoch and queues a notice naming the
change; the tab's own saves do neither, and a refused or failed save
announces nothing.

Driven over real HTTP with the browser and the agent as two distinct
credentials: the agent presents its container's own minted token,
never the browser's.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testWorkflowReloadIntegration import (
    _MockDocker,
    _S_CONTAINER_ID,
    _S_WORKFLOW_PATH,
    _fnConnect,
)
from vaibify.gui import actionCatalog
from vaibify.gui import agentClientFreshness
from vaibify.gui import pipelineServer
from vaibify.gui import researcherNotices


class _DockerServingSaves(_MockDocker):
    """The reload harness's double, plus the exec a committed save reaches."""

    def __init__(self):
        super().__init__()
        self.bFailWorkflowWrites = False

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        return SimpleNamespace(iExitCode=0, sStdout="", sStderr="")

    def fnWriteFile(self, sContainerId, sPath, baContent, **kwargs):
        if self.bFailWorkflowWrites and sPath == _S_WORKFLOW_PATH:
            raise OSError("the container refused the write")
        super().fnWriteFile(sContainerId, sPath, baContent, **kwargs)


@pytest.fixture
def tBrowserAndAgent():
    """Return ``(clientBrowser, clientAgent, docker, iEpoch, iNoticeId)``.

    The browser has connected and polled once, so the epoch and newest
    notice id it reports are the ones a settled tab holds.
    """
    dockerDouble = _DockerServingSaves()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: dockerDouble,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    clientBrowser = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
        raise_server_exceptions=False,
    )
    dictConnect = _fnConnect(clientBrowser)
    sAgentToken = next(
        iter(app.state.dictContainerOwners.values())
    ).sAgentToken
    assert sAgentToken and sAgentToken != clientBrowser.headers[
        "X-Session-Token"
    ]
    clientAgent = TestClient(app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: sAgentToken,
        agentClientFreshness.S_CLIENT_DIGEST_HEADER:
            agentClientFreshness.fsShippedClientDigest(),
        "Host": "host.docker.internal:8050",
    }, raise_server_exceptions=False)
    dictSettled = _fdictPoll(
        clientBrowser, dictConnect["iWorkflowEpoch"], -1,
    )
    return (
        clientBrowser, clientAgent, dockerDouble,
        dictSettled["iWorkflowEpoch"], dictSettled["iNewestNoticeId"],
    )


def _fdictPoll(clientBrowser, iWorkflowEpoch, iLastNoticeId):
    response = clientBrowser.get(
        f"/api/pipeline/{_S_CONTAINER_ID}/file-status"
        f"?iWorkflowEpoch={iWorkflowEpoch}&iLastNoticeId={iLastNoticeId}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def _fresponseCreateStep(client, sName):
    return client.post(
        f"/api/steps/{_S_CONTAINER_ID}/create",
        json={"sName": sName, "sDirectory": ""},
    )


def _flistStepNames(dictPoll):
    return [
        dictStep["sName"]
        for dictStep in (dictPoll.get("dictWorkflow") or {}).get(
            "listSteps", [],
        )
    ]


@pytest.mark.falsification
def testAnAgentsNewStepReachesTheOpenTab(tBrowserAndAgent):
    """Kills: committing an agent's save without advancing the epoch."""
    clientBrowser, clientAgent, _, iEpoch, iNoticeId = tBrowserAndAgent
    assert _fresponseCreateStep(clientAgent, "Agent Step").status_code == 200
    dictPoll = _fdictPoll(clientBrowser, iEpoch, iNoticeId)
    assert dictPoll["bWorkflowReloaded"] is True
    assert dictPoll["sWorkflowReloadOrigin"] == "agent"
    assert _flistStepNames(dictPoll) == ["Agent Step"]
    assert [
        dictNotice["sMessage"]
        for dictNotice in dictPoll["listResearcherNotices"]
    ] == ["An in-container agent changed this project: the step creation."]


@pytest.mark.falsification
def testTheTabsOwnSaveIsNeitherReloadedNorAnnounced(tBrowserAndAgent):
    """Kills: advancing the epoch on every save, the researcher's too."""
    clientBrowser, _, _, iEpoch, iNoticeId = tBrowserAndAgent
    response = _fresponseCreateStep(clientBrowser, "Researcher Step")
    assert response.status_code == 200, response.text
    dictPoll = _fdictPoll(clientBrowser, iEpoch, iNoticeId)
    assert dictPoll["bWorkflowReloaded"] is False
    assert dictPoll["iWorkflowEpoch"] == iEpoch
    assert dictPoll["listResearcherNotices"] == []


def _fnAssertNothingAnnounced(clientBrowser, iEpoch, iNoticeId):
    dictPoll = _fdictPoll(clientBrowser, iEpoch, iNoticeId)
    assert dictPoll["iWorkflowEpoch"] == iEpoch
    assert dictPoll["listResearcherNotices"] == []


@pytest.mark.falsification
def testASaveRefusedForAnotherProjectAnnouncesNothing(tBrowserAndAgent):
    """Kills: announcing before the commit has returned.

    The container switched projects under a save still holding the
    previous one, so the save is refused before anything is written.
    """
    clientBrowser, clientAgent, _, iEpoch, iNoticeId = tBrowserAndAgent
    dictCtx = clientAgent.app.state.dictRouteContext
    dictCtx["paths"][_S_CONTAINER_ID] = (
        "/workspace/other/.vaibify/workflows/other.json"
    )
    response = _fresponseCreateStep(clientAgent, "Misdirected Step")
    dictCtx["paths"][_S_CONTAINER_ID] = _S_WORKFLOW_PATH
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["sRefusal"] == "project-switched"
    _fnAssertNothingAnnounced(clientBrowser, iEpoch, iNoticeId)


@pytest.mark.falsification
def testASaveWhoseWriteFailsAnnouncesNothing(tBrowserAndAgent):
    """Kills: announcing before the commit has returned."""
    clientBrowser, clientAgent, dockerDouble, iEpoch, iNoticeId = (
        tBrowserAndAgent
    )
    dockerDouble.bFailWorkflowWrites = True
    response = _fresponseCreateStep(clientAgent, "Unwritten Step")
    dockerDouble.bFailWorkflowWrites = False
    assert response.status_code >= 500, response.text
    _fnAssertNothingAnnounced(clientBrowser, iEpoch, iNoticeId)


def testANoticeRepeatsUntilItIsAcknowledged(tBrowserAndAgent):
    clientBrowser, clientAgent, _, iEpoch, iNoticeId = tBrowserAndAgent
    _fresponseCreateStep(clientAgent, "Agent Step")
    dictFirst = _fdictPoll(clientBrowser, iEpoch, iNoticeId)
    dictRepeated = _fdictPoll(clientBrowser, iEpoch, iNoticeId)
    assert dictFirst["listResearcherNotices"] == (
        dictRepeated["listResearcherNotices"]
    )
    assert len(dictFirst["listResearcherNotices"]) == 1
    dictAcknowledged = _fdictPoll(
        clientBrowser, dictFirst["iWorkflowEpoch"],
        dictFirst["iNewestNoticeId"],
    )
    assert dictAcknowledged["listResearcherNotices"] == []
    assert dictAcknowledged["bWorkflowReloaded"] is False


def testAStaleClientIsNoticedOncePerContainer(tBrowserAndAgent):
    """The agent is warned on every call; the researcher, once."""
    clientBrowser, clientAgent, _, iEpoch, iNoticeId = tBrowserAndAgent
    del clientAgent.headers[agentClientFreshness.S_CLIENT_DIGEST_HEADER]
    for sName in ("First Stale Step", "Second Stale Step"):
        response = _fresponseCreateStep(clientAgent, sName)
        assert agentClientFreshness.S_CLIENT_WARNING_HEADER in (
            response.headers
        )
    listMessages = [
        dictNotice["sMessage"]
        for dictNotice in _fdictPoll(
            clientBrowser, iEpoch, iNoticeId,
        )["listResearcherNotices"]
    ]
    assert listMessages.count(agentClientFreshness.S_STALE_CLIENT_NOTICE) == 1


def testAFreshTabIsGivenNoHistory():
    """A tab that has acknowledged nothing loaded the project as it is now."""
    dictCtx = {}
    researcherNotices.fnQueueResearcherNotice(
        dictCtx, "cid", researcherNotices.S_NOTICE_LEVEL_INFO, "earlier",
    )
    dictResponse = {}
    researcherNotices.fnAttachNoticesToPoll(dictCtx, "cid", dictResponse, -1)
    assert dictResponse == {
        "listResearcherNotices": [], "iNewestNoticeId": 1,
    }


def testTheNoticeListIsBoundedButItsIdsKeepRising():
    dictCtx = {}
    iQueued = researcherNotices.I_NOTICES_KEPT_PER_CONTAINER + 5
    for iNotice in range(iQueued):
        researcherNotices.fnQueueResearcherNotice(
            dictCtx, "cid", researcherNotices.S_NOTICE_LEVEL_INFO,
            f"notice {iNotice}",
        )
    listKept = researcherNotices.flistNoticesAfter(dictCtx, "cid", 0)
    assert len(listKept) == researcherNotices.I_NOTICES_KEPT_PER_CONTAINER
    assert listKept[-1]["iNoticeId"] == iQueued
    assert researcherNotices.fiNewestNoticeId(dictCtx, "cid") == iQueued


def testAnUnknownLevelIsRefused():
    with pytest.raises(ValueError):
        researcherNotices.fnQueueResearcherNotice(
            {}, "cid", "shout", "never",
        )
