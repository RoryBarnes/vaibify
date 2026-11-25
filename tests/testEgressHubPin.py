"""A sealed container's proxy serves the hub that owns it, and says when it does not.

A container launched by one hub and taken over by another used to keep
a proxy pinned to the first hub's port while its agent was told the
second's, so every call the agent made was refused and the agent sent
the researcher to reconnect, which rewrote the session file and changed
nothing (researcher-reported, 2026-11-24). These tests drive the hub's
half: the connect path pins the proxy BEFORE it writes the session
file, from the same port, and the dashboard's network banner names
both ports when they differ. Container id and name are kept distinct
throughout, and the ports are two values no default would produce.
"""

import logging
from unittest.mock import MagicMock

import pytest

from vaibify.docker import projectEgress
from vaibify.gui import actionCatalog, agentSessionBridge, pipelineServer
from vaibify.gui import projectEgressLifecycle
from vaibify.gui.routes import systemRoutes

S_UUID = "c" * 32
S_CONTAINER_ID = "c7f3a90b21de"
S_CONTAINER_NAME = "projectEmber"


@pytest.fixture
def listEvents(monkeypatch):
    """Record the pin and the session-file write, in the order they happen."""
    listRecorded = []
    monkeypatch.setattr(
        pipelineServer, "fsContainerNameForId",
        lambda connectionDocker, sContainerId: (
            S_CONTAINER_NAME if sContainerId == S_CONTAINER_ID else sContainerId))
    monkeypatch.setattr(
        "vaibify.config.registryManager.fdictGetProject",
        lambda sName: {"sProjectUuid": S_UUID} if sName == S_CONTAINER_NAME else None)
    monkeypatch.setattr(
        projectEgress, "fsPinProjectEgressHub",
        lambda sProjectUuid, iHubPort: listRecorded.append(
            ("pin", sProjectUuid, iHubPort)) or projectEgress.S_PIN_PINNED)
    return listRecorded


def _fconnectionRecordingSessionWrites(listEvents):
    """A docker connection whose session-file write lands in the event list."""
    connectionDocker = MagicMock()

    def fnRecordWrite(sContainerId, sPath, baContent, **dictKeywords):
        if sPath == actionCatalog.S_SESSION_ENV_PATH:
            listEvents.append(("session", sContainerId, baContent.decode("utf-8")))

    connectionDocker.fnWriteFile.side_effect = fnRecordWrite
    return connectionDocker


@pytest.mark.falsification
def testTheProxyIsPinnedBeforeTheAgentIsToldWhichHubToDial(listEvents):
    """Kills: a session push that writes the session file without pinning the proxy."""
    agentSessionBridge.fnPushAgentSessionToContainer(
        _fconnectionRecordingSessionWrites(listEvents), S_CONTAINER_ID, "agent-token", 8053)
    assert [tEvent[:2] for tEvent in listEvents] == [
        ("pin", S_UUID), ("session", S_CONTAINER_ID)]
    assert listEvents[0][2] == 8053
    assert "VAIBIFY_HOST_URL=http://host.docker.internal:8053\n" in listEvents[1][2]


def testATestHarnessHubPinsThePortItsSessionFileNames(listEvents):
    agentSessionBridge.fnPushAgentSessionToContainer(
        _fconnectionRecordingSessionWrites(listEvents), S_CONTAINER_ID, "agent-token", 0)
    assert listEvents[0] == ("pin", S_UUID, 8050)
    assert "VAIBIFY_HOST_URL=http://host.docker.internal:8050\n" in listEvents[1][2]


def testTheConnectPathPinsThroughTheSessionPush(listEvents):
    pipelineServer._fnPushAgentSession(
        {"docker": _fconnectionRecordingSessionWrites(listEvents), "iPort": 8053},
        S_CONTAINER_ID)
    assert [tEvent[0] for tEvent in listEvents] == ["pin", "session"]


def testAContainerWithNoRegisteredIdentityHasNothingToPin(listEvents):
    agentSessionBridge.fnPushAgentSessionToContainer(
        _fconnectionRecordingSessionWrites(listEvents), "unknown-id", "agent-token", 8053)
    assert [tEvent[0] for tEvent in listEvents] == ["session"]


@pytest.mark.parametrize("errorRaised", [
    projectEgress.EgressSetupError("docker did not answer"),
    PermissionError("the egress directory is not writable"),
])
def testAPinThatFailsIsLoggedAndTheSessionIsStillWritten(
        monkeypatch, listEvents, caplog, errorRaised):
    def fnRaise(sProjectUuid, iHubPort):
        raise errorRaised

    monkeypatch.setattr(projectEgress, "fsPinProjectEgressHub", fnRaise)
    with caplog.at_level(logging.WARNING):
        agentSessionBridge.fnPushAgentSessionToContainer(
            _fconnectionRecordingSessionWrites(listEvents), S_CONTAINER_ID,
            "agent-token", 8053)
    assert [tEvent[0] for tEvent in listEvents] == ["session"]
    assert any("hub port 8053" in record.getMessage() for record in caplog.records)


def testAnOlderProxyIsLoggedWithItsRemedy(monkeypatch, listEvents, caplog):
    monkeypatch.setattr(projectEgress, "fsPinProjectEgressHub",
                        lambda sProjectUuid, iHubPort: projectEgress.S_PIN_FIXED)
    with caplog.at_level(logging.WARNING):
        projectEgressLifecycle.fnPinProjectEgressToHub(None, S_CONTAINER_ID, 8053)
    assert any("restart the container" in record.getMessage()
               for record in caplog.records)


# The sentence the maintainer approved on 2026-11-24, with both ports filled in.
@pytest.mark.falsification
@pytest.mark.parametrize("iPinned, iAgentHubPort", [(8051, 8053), (9007, 9001)])
def testAnOlderProxyServingAnotherHubNamesBothPortsAndTheRestart(iPinned, iAgentHubPort):
    """Kills: a sentence that names a fixed port instead of this situation's."""
    sSentence = projectEgressLifecycle.fsDescribeHubPinMismatch(
        {"sPin": "fixed", "iPinnedHubPort": iPinned}, iAgentHubPort)
    assert sSentence == (
        f"This container's network proxy serves hub port {iPinned}; this "
        f"dashboard is {iAgentHubPort}. Restart the container to reach it "
        "from here.")


def testAFilePinnedProxyServingAnotherHubIsFixedByReopening():
    assert projectEgressLifecycle.fsDescribeHubPinMismatch(
        {"sPin": "pinned", "iPinnedHubPort": 0}, 8053) == (
        "This container's network proxy serves no hub; this dashboard is "
        "8053. Reopen the container from this dashboard to point the proxy "
        "here.")


@pytest.mark.parametrize("dictPin", [
    {"sPin": "pinned", "iPinnedHubPort": 8053},
    {"sPin": "fixed", "iPinnedHubPort": 8053},
    {"sPin": "absent", "iPinnedHubPort": 0},
    {},
])
def testAProxyServingThisHubOrNoProxySaysNothing(dictPin):
    assert projectEgressLifecycle.fsDescribeHubPinMismatch(dictPin, 8053) == ""


def testTheBannerComparesWithThePortTheSessionFileNames(monkeypatch):
    """A test-harness hub (port 0) tells its agent 8050; a proxy on 8050 agrees."""
    monkeypatch.setattr(pipelineServer, "fsContainerNameForId",
                        lambda connectionDocker, sContainerId: S_CONTAINER_NAME)
    monkeypatch.setattr("vaibify.config.registryManager.fdictGetProject",
                        lambda sName: {"sProjectUuid": S_UUID})
    monkeypatch.setattr(projectEgress, "fdictDescribeProjectEgress", lambda sUuid: {
        "sProxyState": "running", "sPin": "pinned", "iPinnedHubPort": 8050,
        "listRefused": []})
    dictNetwork = {}
    systemRoutes._fnDescribeProxyInto(dictNetwork, None, S_CONTAINER_ID, 0)
    assert dictNetwork["sHubPinMismatch"] == ""
    systemRoutes._fnDescribeProxyInto(dictNetwork, None, S_CONTAINER_ID, 8053)
    assert "serves hub port 8050; this dashboard is 8053" in dictNetwork["sHubPinMismatch"]
