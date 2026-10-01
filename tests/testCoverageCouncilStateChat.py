"""The chairbot conversation's refusals, teardown faults and lifecycle edges.

``tests/testCouncilChat.py`` drives open/ask/close end to end. These
tests start from an already-open session record (built by the module's
own record constructor, over a real campaign in a real store) so the
branches that need a precise session state can be reached directly: a
refusal for each state a question cannot be served in, a runner that
cannot be rebuilt after resting, a daemon that will not prove the
runner gone, and an answer worker still running when the close
arrives. The Docker gateway is the only thing patched.
"""

import asyncio
import json
import threading
import time

import pytest

from vaibify.gui import agentCouncilCampaign
from vaibify.gui import agentCouncilChat as chat
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilStore


S_RESOURCE_ALPHA = "projectAlpha"
S_RESOURCE_BETA = "projectBeta"
S_REPO_PATH = "/workspace/repoAlpha"


class ChatGatewayDouble:
    """Answers the gateway calls a session teardown and a message make."""

    def __init__(self):
        self.sDestroyOutcome = "destroyed"
        self.errorOnDestroy = None
        self.bEgressRemovalProven = True
        self.eventReleaseExecute = None
        self.eventExecuteEntered = threading.Event()
        self.fnDuringExecute = None
        self.listDestroyed = []
        self.listRemovedScopes = []
        self.listExecuted = []

    def fnInstall(self, monkeypatch):
        monkeypatch.setattr(agentCouncilDockerGateway,
                            "fdictDestroyAndSettle", self.fdictDestroy)
        monkeypatch.setattr(
            agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
            self.fdictRemoveEgress)
        monkeypatch.setattr(agentCouncilDockerGateway,
                            "fdictExecuteBoundedTurn", self.fdictExecute)

    def fdictDestroy(self, dictGateway, sHandle):
        self.listDestroyed.append(sHandle)
        if self.errorOnDestroy is not None:
            raise self.errorOnDestroy
        return {"sOutcome": self.sDestroyOutcome,
                "sReason": "" if self.sDestroyOutcome == "destroyed"
                else "the daemon did not answer the absence probe"}

    def fdictRemoveEgress(self, dictGateway, sScope):
        self.listRemovedScopes.append(sScope)
        return {"saIndeterminateResources": [] if self.bEgressRemovalProven
                else [f"vaibifyCouncilProxy-{sScope}"]}

    def fdictExecute(self, dictGateway, sHandle, listCommand, *arguments):
        self.listExecuted.append(list(listCommand))
        self.eventExecuteEntered.set()
        if self.eventReleaseExecute is not None:
            self.eventReleaseExecute.wait(10)
        if self.fnDuringExecute is not None:
            self.fnDuringExecute()
        sStream = ('{"type": "system", "model": "claude-fake-5"}\n'
                   '{"type": "result", "result": '
                   + json.dumps("Because buffering dominates.") + "}\n")
        return {"iExitCode": 0, "sOutput": sStream,
                "bOutputCapExceeded": False, "bWallClockExceeded": False,
                "iOutputBytes": len(sStream), "bOomKilled": False,
                "fElapsedSeconds": 0.1}


def fdictBuildCampaign(sResourceName):
    listParticipants = [
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelOne"),
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelTwo"),
    ]
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "Should stepAlpha stream its output?", listParticipants,
        dictProjectIdentity={"sResourceName": sResourceName,
                             "sProjectRepoPath": S_REPO_PATH})
    dictCampaign["sState"] = "planReady"
    return dictCampaign


def ftOpenSession(tmp_path, dictControllerState, sResourceName=S_RESOURCE_ALPHA,
                  dictStore=None):
    """Register a campaign and an open session over it; return both ids."""
    if dictStore is None:
        dictStore = agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "agentCouncils"))
    dictCampaign = fdictBuildCampaign(sResourceName)
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    dictSession = chat._fdictCreateSessionRecord({
        "dictCampaign": dictCampaign, "sResourceName": sResourceName,
        "dictStore": dictStore, "dictRegistry": {},
        "sImageReference": "sha256:" + "9f" * 32,
        "ftStageRunnerCredential": lambda: ("unused", 0)})
    dictSession["dictGateway"] = {"bDouble": True}
    dictSession["sHandle"] = "handle-" + dictCampaign["sCampaignId"]
    dictSession["bEgressProvisioned"] = True
    chat._fdictSessionsByCampaign(dictControllerState)[
        dictCampaign["sCampaignId"]] = dictSession
    return dictCampaign["sCampaignId"], dictSession


# ----- a question the session cannot serve --------------------------------


@pytest.mark.parametrize("dictState,sFragment", [
    ({"bClosing": True}, "this conversation is closing"),
    ({"bSuspending": True}, "being rested; ask again in a moment"),
    ({"sState": "answering"}, "still answering the previous message"),
    ({"sState": "failed", "sFailureReason": "the runner faulted"},
     "no longer usable: the runner faulted — close the conversation"),
])
def testAQuestionTheSessionCannotServeIsRefusedAndNotRecorded(
        tmp_path, dictState, sFragment):
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSession.update(dictState)
    with pytest.raises(chat.CouncilChatError) as excInfo:
        asyncio.run(chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, "Why stream?"))
    assert sFragment in str(excInfo.value)
    assert dictSession["listMessages"] == []
    assert dictSession["taskAnswer"] is None


def testATranscriptAtItsBoundRefusesRatherThanTruncates(tmp_path):
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSession["listMessages"] = [{"sMessageId": f"m{iIndex}"} for iIndex
                                   in range(chat.I_MAX_CHAT_MESSAGES)]
    with pytest.raises(chat.CouncilChatError) as excInfo:
        asyncio.run(chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, "One more?"))
    assert f"its {chat.I_MAX_CHAT_MESSAGES}-message bound" in str(
        excInfo.value)
    assert len(dictSession["listMessages"]) == chat.I_MAX_CHAT_MESSAGES


def testAnEmptyQuestionIsRefused(tmp_path):
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    with pytest.raises(chat.CouncilChatError) as excInfo:
        asyncio.run(chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, "  \n\t "))
    assert str(excInfo.value) == "a question must not be empty"


# ----- the answer worker -------------------------------------------------------


def testAnAnswerForACampaignWhoseRecordVanishedFailsTheSession(
        tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)

    async def fnAskThenLoseTheRecord():
        await chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, "Why stream?")
        dictSession["dictStore"]["dictEntriesById"].pop(sCampaignId)
        await dictSession["taskAnswer"]

    asyncio.run(fnAskThenLoseTheRecord())
    assert dictSession["sState"] == chat.S_CHAT_STATE_FAILED
    assert dictSession["sFailureReason"].startswith(
        "CouncilChatError: this campaign's record is gone")
    assert doubleGateway.listExecuted == [], "nothing may run blind"
    assert dictSession["sPendingMessageId"] == ""


def testARestingSessionThatCannotBeRebuiltFailsAndKeepsTheQuestionOut(
        tmp_path, monkeypatch):
    def fdockerRefuseClient(*arguments, **dictKeywords):
        raise RuntimeError("the daemon is unreachable")

    monkeypatch.setattr(agentCouncilDockerGateway,
                        "fdockerCreateCouncilClient", fdockerRefuseClient)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSession.update({"sState": chat.S_CHAT_STATE_RESTING,
                        "dictGateway": None, "sHandle": "",
                        "bEgressProvisioned": False})
    with pytest.raises(RuntimeError):
        asyncio.run(chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, "Still there?"))
    assert dictSession["sState"] == chat.S_CHAT_STATE_FAILED
    assert dictSession["sFailureReason"] == (
        "the chairbot's runner could not be rebuilt after resting: "
        "the daemon is unreachable")
    assert dictSession["listMessages"] == []


def testACloseDoesNotWaitForeverOnAWorkerAndTheLateAnswerIsDropped(
        tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "F_CHAT_WORKER_SETTLE_SECONDS", 0.05)
    doubleGateway = ChatGatewayDouble()
    doubleGateway.eventReleaseExecute = threading.Event()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)

    async def fdictAskThenCloseMidAnswer():
        await chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, "Why stream?")
        while not doubleGateway.eventExecuteEntered.is_set():
            await asyncio.sleep(0.01)
        fStart = time.monotonic()
        dictSettled = await chat.fdictCloseChatSession(
            dictControllerState, sCampaignId)
        fElapsed = time.monotonic() - fStart
        doubleGateway.eventReleaseExecute.set()
        await dictSession["taskAnswer"]
        return dictSettled, fElapsed

    dictSettled, fElapsed = asyncio.run(fdictAskThenCloseMidAnswer())
    assert dictSettled == {"bSettled": True, "sOutcome": "destroyed",
                           "sReason": ""}
    assert fElapsed < 5.0
    assert sCampaignId not in chat._fdictSessionsByCampaign(
        dictControllerState)
    assert [dictMessage["sAuthor"] for dictMessage
            in dictSession["listMessages"]] == ["researcher"]


# ----- teardown that cannot be proven ------------------------------------------


def testAGatewayRefusalOnDestroyIsQuarantineNotSettlement(
        tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.errorOnDestroy = agentCouncilDockerGateway.\
        CouncilGatewayError("the target no longer carries this label")
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    sHandle = dictSession["sHandle"]
    dictSettled = asyncio.run(chat.fdictCloseChatSession(
        dictControllerState, sCampaignId))
    assert dictSettled["bSettled"] is False
    assert dictSettled["sOutcome"] == "quarantined"
    assert "no longer carries this label" in dictSettled["sReason"]
    assert dictSession["sHandle"] == sHandle, "the handle must be kept"
    assert dictSession["sState"] == chat.S_CHAT_STATE_FAILED
    assert sCampaignId in chat._fdictSessionsByCampaign(dictControllerState)
    assert doubleGateway.listRemovedScopes == [sCampaignId + "-chat"], (
        "the egress is still attempted when the runner is unproven")


def testRestingOverAnUnprovenTeardownFailsTheSession(tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.sDestroyOutcome = "quarantined"
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSettled = asyncio.run(chat.fdictRestChatConversation(
        dictControllerState, sCampaignId))
    assert dictSettled["bSettled"] is False
    assert dictSession["sState"] == chat.S_CHAT_STATE_FAILED
    assert dictSession["sFailureReason"] == (
        "the daemon did not answer the absence probe")
    assert dictSession["bSuspending"] is False


def testRestingAFailedConversationClosesIt(tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSession.update({"sState": chat.S_CHAT_STATE_FAILED,
                        "sFailureReason": "earlier fault"})
    dictSettled = asyncio.run(chat.fdictRestChatConversation(
        dictControllerState, sCampaignId))
    assert dictSettled["bSettled"] is True
    assert sCampaignId not in chat._fdictSessionsByCampaign(
        dictControllerState)
    listEvents = agentCouncilStore.fdictCollectCampaignEvents(
        dictSession["dictStore"], sCampaignId, 0)["listEvents"]
    assert [dictEvent["sEventKind"] for dictEvent in listEvents] == [
        "chairbotChatClosed"]


def testResourceDrainClosesOnlyThatResourcesConversations(
        tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"))
    sAlphaId, _ = ftOpenSession(tmp_path, dictControllerState,
                                S_RESOURCE_ALPHA, dictStore)
    sBetaId, dictBetaSession = ftOpenSession(
        tmp_path, dictControllerState, S_RESOURCE_BETA, dictStore)
    listUnsettled = asyncio.run(chat.flistCloseChatSessionsForResource(
        dictControllerState, S_RESOURCE_ALPHA))
    assert listUnsettled == []
    dictSessions = chat._fdictSessionsByCampaign(dictControllerState)
    assert list(dictSessions) == [sBetaId]
    assert doubleGateway.listDestroyed == ["handle-" + sAlphaId]
    assert dictBetaSession["sState"] == chat.S_CHAT_STATE_READY


# ----- the reaper -----------------------------------------------------------------


def testTheReaperClosesAFailedConversationPastItsBound(tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSession.update({
        "sState": chat.S_CHAT_STATE_FAILED, "sFailureReason": "fault",
        "fOpenedMonotonic": time.monotonic()
        - chat.F_CHAT_SESSION_CEILING_SECONDS - 1.0})
    iActedOn = asyncio.run(chat.fiReapExpiredChatSessions(
        dictControllerState))
    assert iActedOn == 1
    assert sCampaignId not in chat._fdictSessionsByCampaign(
        dictControllerState)
    assert doubleGateway.listDestroyed == ["handle-" + sCampaignId]
