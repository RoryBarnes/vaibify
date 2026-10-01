"""A council runner is created with the limits its snapshot was admitted against.

The snapshot bound scales with the daemon's memory (a 64 GiB daemon
admits a 2 GiB working tree), but every runner was created with the
floor limits: a 512 MiB working-tree tmpfs. Consent was spent, the
snapshot was admitted, and then every turn failed copying it in. The
runtime now resolves the limits from the daemon it creates runners on
and threads them to every runner it creates.
"""

from unittest.mock import patch

import pytest

from vaibify.gui import agentCouncilCapacity
from vaibify.gui import agentCouncilChat
from vaibify.gui import agentCouncilController as controller
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilProviders
from vaibify.gui import agentCouncilRunner

I_LARGE_DAEMON_MEMORY_BYTES = 64 * 1024 ** 3


class FakeDaemonClient:
    """A docker-py client that only answers ``info``."""

    def __init__(self, iMemoryBytes):
        self.iMemoryBytes = iMemoryBytes

    def info(self):
        return {"MemTotal": self.iMemoryBytes, "NCPU": 8}


class BrokenDaemonClient:
    def info(self):
        raise RuntimeError("the daemon did not answer")


def _fdictFloorLimits():
    return agentCouncilRunner.fdictBuildDefaultRunnerLimits()


def testALargeDaemonGetsARunnerAsLargeAsItsAdmittedSnapshot():
    dictLimits = agentCouncilRunner.fdictBuildRunnerLimitsForDaemon(
        FakeDaemonClient(I_LARGE_DAEMON_MEMORY_BYTES))
    dictCapacity = agentCouncilCapacity.fdictResolveCouncilCapacityFromClient(
        FakeDaemonClient(I_LARGE_DAEMON_MEMORY_BYTES))
    assert dictLimits["iWorkingTreeBytes"] == (
        dictCapacity["iMaxSnapshotTotalBytes"])
    assert dictLimits["iWorkingTreeBytes"] > (
        _fdictFloorLimits()["iWorkingTreeBytes"])


@pytest.mark.parametrize("dockerClient", [
    BrokenDaemonClient(), FakeDaemonClient(0), None])
def testADaemonThatCannotBeAskedGetsTheFloorLimits(dockerClient):
    assert agentCouncilRunner.fdictBuildRunnerLimitsForDaemon(
        dockerClient) == _fdictFloorLimits()


def testTheClientAndTheConnectionResolveTheSameCapacity():
    class FakeConnection:
        def fdictReadDaemonCapacity(self):
            return {"iMemoryBytes": I_LARGE_DAEMON_MEMORY_BYTES,
                    "iCpuCount": 8}

    assert agentCouncilCapacity.fdictResolveCouncilCapacity(
        FakeConnection()) == (
        agentCouncilCapacity.fdictResolveCouncilCapacityFromClient(
            FakeDaemonClient(I_LARGE_DAEMON_MEMORY_BYTES)))


def _fdictRuntimeOnADaemon(iMemoryBytes):
    return {
        "sCampaignId": "campaign-x",
        "sImageReference": "sha256:abc",
        "sSnapshotIdentity": "snapshot-x",
        "baSnapshotTar": b"",
        "dictCampaign": {"dictSettings": {}},
        "ftStageRunnerCredential": lambda *a, **k: ("", 0),
        "dictRunnerAccess": {"dictEgress": {}},
        "dictGateway": {"dockerCouncil": FakeDaemonClient(iMemoryBytes)},
    }


@pytest.mark.falsification
def testAParticipantRunnerIsCreatedWithTheDaemonsLimits(monkeypatch):
    """Kills: building a participant connection without the daemon's limits."""
    dictSeen = {}

    def fconnectionRecord(*tArguments, **dictKeywords):
        dictSeen.update(dictKeywords)
        return object()

    monkeypatch.setattr(
        agentCouncilProviders, "ClaudeRunnerConnection", fconnectionRecord)
    monkeypatch.setattr(
        controller, "_fdictProvisionRunnerAccessOnce",
        lambda dictRuntime: {"dictEgress": {}})
    dictRuntime = _fdictRuntimeOnADaemon(I_LARGE_DAEMON_MEMORY_BYTES)
    controller.fconnectionBuildParticipantConnection(
        dictRuntime, {"sParticipantId": "p-1", "sProvider": "claude",
                      "sRequestedModel": "opus"})
    assert dictSeen["dictLimits"]["iWorkingTreeBytes"] > (
        _fdictFloorLimits()["iWorkingTreeBytes"])


@pytest.mark.falsification
def testTheBaselineSandboxIsCreatedWithTheDaemonsLimits():
    """Kills: building the baseline executor without the daemon's limits."""
    dictSeen = {}

    def ffnBuildExecutor(*tArguments, **dictKeywords):
        dictSeen.update(dictKeywords)
        return lambda dictRequest: {}

    dictRuntime = _fdictRuntimeOnADaemon(I_LARGE_DAEMON_MEMORY_BYTES)
    dictRuntime["fdictExecuteBaselineEvidence"] = None
    with patch.object(
        agentCouncilProviders, "ffnBuildBaselineEvidenceExecutor",
        ffnBuildExecutor,
    ):
        controller._fdictExecuteBaselineEvidenceLazily(
            dictRuntime, {"sCommandText": "true"})
    assert dictSeen["dictLimits"]["iWorkingTreeBytes"] > (
        _fdictFloorLimits()["iWorkingTreeBytes"])


def testTheLimitsAreResolvedOncePerRuntime():
    dictRuntime = _fdictRuntimeOnADaemon(I_LARGE_DAEMON_MEMORY_BYTES)
    dictFirst = controller._fdictResolveRuntimeRunnerLimits(dictRuntime)
    dictRuntime["dictGateway"] = {"dockerCouncil": FakeDaemonClient(0)}
    assert controller._fdictResolveRuntimeRunnerLimits(dictRuntime) is dictFirst


@pytest.mark.falsification
def testTheChairbotChatRunnerIsCreatedWithTheDaemonsLimitsAndCost(
    monkeypatch,
):
    """Kills: creating the chat runner at the floor under a large snapshot."""
    listCalls = []
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda *a, **k: FakeDaemonClient(I_LARGE_DAEMON_MEMORY_BYTES))
    monkeypatch.setattr(
        agentCouncilChat, "_fdictProvisionChatEgress",
        lambda dictSession, dictGateway: {
            "sNetworkName": "net", "sProxyInternalAddress": "172.30.0.2",
            "iProxyPort": 3128})

    def fdictReserve(*tArguments, **dictKeywords):
        listCalls.append(tArguments)
        return {"bCreated": False, "sHandle": "", "sRefusalReason": "stop"}

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictReserveAndCreateRunner", fdictReserve)
    dictSession = {
        "dictRegistry": {}, "sResourceName": "project", "sCampaignId": "c-1",
        "sProvider": "claude", "sImageReference": "sha256:abc",
    }
    with pytest.raises(agentCouncilChat.CouncilChatError):
        agentCouncilChat._fnBuildChatRunner(dictSession)
    dictCost, dictLimits = listCalls[0][3], listCalls[0][5]
    assert dictLimits["iWorkingTreeBytes"] > (
        _fdictFloorLimits()["iWorkingTreeBytes"])
    assert dictCost["iMemoryBytes"] == dictLimits["iMemoryBytes"]
