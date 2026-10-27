"""Only a full-sandbox member, and the replay of its claim, may execute.

A full-sandbox member is told it may edit, build and run its copy, so
its runner's two writable mounts allow a file to be executed: `./run.sh`
and a program the member compiles run there. The evidence sandbox that
replays such a claim must allow the same, or a claim resting on a built
program could never be confirmed. Every other council container (a
read-only council's runners, the chairbot chat, the credential test)
keeps both mounts noexec, which is defense in depth beneath their tool
restrictions.

What these tests pin is the request each caller makes and the mount
options it becomes. That the daemon honours them is
``tests/testAgentCouncilRunnerLive.py``'s job.
"""

import asyncio
import inspect

import pytest

from vaibify.gui import (
    agentCouncilChat,
    agentCouncilDockerGateway,
    agentCouncilProviders,
    agentCouncilRunner,
)


def _dictTmpfsFor(**dictKeywords):
    return agentCouncilRunner.fdictComposeRunnerCreateSpecification(
        "sha256:imageAlpha", "reservation-1", **dictKeywords)[
            "dictCreateKeywords"]["tmpfs"]


def testWritableMountsAreNoexecUnlessAskedOtherwise():
    """The default is stated, not inherited from Docker's own."""
    for sOptions in _dictTmpfsFor().values():
        assert sOptions.startswith("noexec,nosuid,nodev,"), sOptions


def testAnExecutableRunnerKeepsNosuidAndNodev():
    for sOptions in _dictTmpfsFor(bWritableMountsExecutable=True).values():
        assert sOptions.startswith("exec,nosuid,nodev,"), sOptions


def testExecutionChangesNothingElseInThePosture():
    """Only the exec flag differs: user, capabilities and network hold."""
    dictPlain = agentCouncilRunner.fdictComposeRunnerCreateSpecification(
        "sha256:imageAlpha", "reservation-1")["dictCreateKeywords"]
    dictExecutable = agentCouncilRunner.fdictComposeRunnerCreateSpecification(
        "sha256:imageAlpha", "reservation-1",
        bWritableMountsExecutable=True)["dictCreateKeywords"]
    for sKey in ("name", "tmpfs"):
        dictPlain.pop(sKey)
        dictExecutable.pop(sKey)
    assert dictPlain == dictExecutable


# Taken before any test replaces the function with its recorder.
SIGNATURE_RESERVE_AND_CREATE = inspect.signature(
    agentCouncilDockerGateway.fdictReserveAndCreateRunner)


def _fbExecutableRequested(tArguments, dictKeywords):
    """Read the flag the way the real gateway function would bind it."""
    boundArguments = SIGNATURE_RESERVE_AND_CREATE.bind(
        *tArguments, **dictKeywords)
    boundArguments.apply_defaults()
    return boundArguments.arguments["bWritableMountsExecutable"]


@pytest.fixture
def listCreationRequests(monkeypatch):
    """Record each runner request, then refuse it so nothing is built."""
    listRequests = []

    def fdictRefuse(*tArguments, **dictKeywords):
        listRequests.append(_fbExecutableRequested(tArguments, dictKeywords))
        return {"bCreated": False, "sHandle": "", "sRefusalReason": "stop"}

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictReserveAndCreateRunner", fdictRefuse)
    return listRequests


def _fnPrepareTurn(dictTurnRequest):
    connection = agentCouncilProviders.ClaudeRunnerConnection(
        {"bFakeGateway": True}, "campaign-1", "sha256:" + "00" * 32,
        b"tar", "opus", dictEgress=None)
    with pytest.raises(agentCouncilDockerGateway.CouncilGatewayError):
        asyncio.run(connection.fdictPrepareImmutableContext(dictTurnRequest))


@pytest.mark.falsification
def testAFullSandboxTurnAsksForAnExecutableRunner(listCreationRequests):
    """Kills: a full-sandbox runner left noexec."""
    _fnPrepareTurn({"bReadOnlyCouncil": False})
    assert listCreationRequests == [True]


@pytest.mark.falsification
def testAReadOnlyTurnAndAnUnstatedOneStayNoexec(listCreationRequests):
    """Kills: execution granted to a request that does not ask for it."""
    _fnPrepareTurn({"bReadOnlyCouncil": True})
    _fnPrepareTurn({})
    assert listCreationRequests == [False, False]


@pytest.mark.falsification
def testTheReplaySandboxCanExecuteWhatTheMemberBuilt(listCreationRequests):
    """Kills: the evidence sandbox unable to replay a built program."""
    fdictExecute = agentCouncilProviders.ffnBuildBaselineEvidenceExecutor(
        {"bFakeGateway": True}, "campaign-1", "sha256:" + "00" * 32,
        "snapshot-hash", b"tar")
    with pytest.raises(agentCouncilDockerGateway.CouncilGatewayError):
        fdictExecute({"sCommandText": "./run.sh"})
    assert listCreationRequests == [True]


def testTheChatRunnerStaysNoexec(monkeypatch, listCreationRequests):
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda *tArguments, **dictKeywords: None)
    monkeypatch.setattr(
        agentCouncilChat, "_fdictProvisionChatEgress",
        lambda dictSession, dictGateway: {
            "sNetworkName": "net", "sProxyInternalAddress": "172.30.0.2",
            "iProxyPort": 3128})
    dictSession = {
        "dictRegistry": {}, "sResourceName": "project", "sCampaignId": "c-1",
        "sProvider": "claude", "sImageReference": "sha256:abc",
    }
    with pytest.raises(agentCouncilChat.CouncilChatError):
        agentCouncilChat._fnBuildChatRunner(dictSession)
    assert listCreationRequests == [False]
