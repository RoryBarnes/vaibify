"""A council's chairbot conversations and event log survive the hub.

Source: ``vaibify/gui/agentCouncilChat.py``,
``vaibify/gui/agentCouncilChatRecords.py``,
``vaibify/gui/agentCouncilEventLog.py`` and
``vaibify/gui/agentCouncilStore.py``.

The rules are the researcher's (plan section 8): a hub shutdown or a
lease release is not the researcher ending a conversation, so it stays
OPEN on disk; only Close archives it; a question pending at shutdown is
never re-asked; the event log keeps every event, continues its
numbering across a restart and recovers a torn last line; retention
never deletes a campaign holding a conversation. Every assertion reads
the files on disk under ``tmp_path``.
"""

import asyncio
import json
import os

import pytest

from vaibify.gui import agentCouncilChat as chat
from vaibify.gui import (
    agentCouncilChatRecords, agentCouncilController, agentCouncilEventLog,
    agentCouncilStore,
)
from tests.testCoverageCouncilStateChat import (
    ChatGatewayDouble, fdictBuildCampaign, ftOpenSession,
)


def _fsCampaignDirectory(dictSession):
    return os.path.join(dictSession["dictStore"]["sDurableStoreRoot"],
                        dictSession["sCampaignId"])


def _flistChatFiles(dictSession):
    sDirectory = agentCouncilChatRecords.fsChatsDirectory(
        _fsCampaignDirectory(dictSession))
    listRecords = []
    for sName in sorted(os.listdir(sDirectory)):
        with open(os.path.join(sDirectory, sName)) as fileIn:
            listRecords.append(json.load(fileIn))
    return listRecords


def _fnAskAndAwait(dictControllerState, sCampaignId, sQuestion):
    async def fnRun():
        await chat.fdictAskChatQuestion(
            dictControllerState, sCampaignId, sQuestion)
        dictSession = chat._fdictSessionsByCampaign(
            dictControllerState)[sCampaignId]
        await dictSession["taskAnswer"]
    asyncio.run(fnRun())


@pytest.mark.falsification
def testAShutdownLeavesTheConversationOpenOnDisk(tmp_path, monkeypatch):
    """Kills: closing conversations at hub shutdown, as before."""
    ChatGatewayDouble().fnInstall(monkeypatch)
    dictControllerState = {"dictCampaignRuntime": {}}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    _fnAskAndAwait(dictControllerState, sCampaignId, "Why stream?")
    asyncio.run(agentCouncilController.fnAwaitControllerSettleOnShutdown(
        dictControllerState, bReleaseRunnerAccess=False))
    listRecords = _flistChatFiles(dictSession)
    assert [d["sStatus"] for d in listRecords] == ["open"]
    assert listRecords[0]["sState"] == "resting"
    assert [m["sText"] for m in listRecords[0]["listMessages"]] == [
        "Why stream?", "Because buffering dominates."]
    assert chat._fdictSessionsByCampaign(dictControllerState) == {}


def testCloseArchivesTheConversationReadOnly(tmp_path, monkeypatch):
    ChatGatewayDouble().fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    _fnAskAndAwait(dictControllerState, sCampaignId, "Why stream?")
    asyncio.run(chat.fdictCloseChatSession(dictControllerState, sCampaignId))
    listRecords = _flistChatFiles(dictSession)
    assert [d["sStatus"] for d in listRecords] == ["archived"]
    assert listRecords[0]["fClosedEpoch"] > 0
    dictDescribed = chat.fdictDescribeChatSession(
        dictControllerState, sCampaignId, _fsCampaignDirectory(dictSession))
    assert dictDescribed["bOpen"] is False
    assert dictDescribed["bResumable"] is False
    assert len(dictDescribed["listArchived"]) == 1


def testALeaseReleaseSetsTheConversationAside(tmp_path, monkeypatch):
    ChatGatewayDouble().fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    asyncio.run(chat.flistSetChatSessionsAsideForResource(
        dictControllerState, dictSession["sResourceName"]))
    assert [d["sStatus"] for d in _flistChatFiles(dictSession)] == ["open"]


@pytest.mark.falsification
def testAPendingQuestionIsNeverReAsked(tmp_path, monkeypatch):
    """A question written pending, then a stop, comes back interrupted.

    Kills: writing the conversation only after the answer, which lost
    the question a shutdown interrupted.
    """
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)

    def fnFailBeforeAnswering():
        raise RuntimeError("the hub stopped")

    doubleGateway.fnDuringExecute = fnFailBeforeAnswering
    listWrittenBeforeTheRunner = []
    doubleGateway.fnDuringExecute = lambda: (
        listWrittenBeforeTheRunner.append(_flistChatFiles(dictSession)),
        fnFailBeforeAnswering())
    _fnAskAndAwait(dictControllerState, sCampaignId, "Refit it?")
    dictOnDisk = listWrittenBeforeTheRunner[0][0]
    assert dictOnDisk["sPendingMessageId"] == (
        dictOnDisk["listMessages"][-1]["sMessageId"])
    listMessages = agentCouncilChatRecords.flistMarkInterruptedQuestion(
        dictOnDisk)
    assert listMessages[-1]["bInterrupted"] is True
    assert listMessages[-1]["sText"] == "Refit it?"


def testReopeningContinuesTheSetAsideConversation(tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    _fnAskAndAwait(dictControllerState, sCampaignId, "Why stream?")
    asyncio.run(chat.fdictSetChatSessionAside(dictControllerState, sCampaignId))
    dictDescribed = chat.fdictDescribeChatSession(
        dictControllerState, sCampaignId, _fsCampaignDirectory(dictSession))
    assert dictDescribed["bResumable"] is True
    assert len(dictDescribed["listMessages"]) == 2
    dictReopened = chat._fdictCreateSessionRecord({
        "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
            dictSession["dictStore"], sCampaignId),
        "sResourceName": dictSession["sResourceName"],
        "dictStore": dictSession["dictStore"], "dictRegistry": {},
        "sImageReference": "sha256:" + "9f" * 32,
        "ftStageRunnerCredential": lambda: ("unused", 0)})
    chat._fnSeedSessionFromDisk(dictReopened)
    assert dictReopened["sSessionId"] == dictSession["sSessionId"]
    assert [m["sText"] for m in dictReopened["listMessages"]] == [
        "Why stream?", "Because buffering dominates."]


def testAnInterruptedQuestionIsNotQuotedToTheChairbot(tmp_path, monkeypatch):
    doubleGateway = ChatGatewayDouble()
    doubleGateway.fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    dictSession["listMessages"].append({
        "sMessageId": "message-0123456789ab", "sAuthor": "researcher",
        "sText": "INTERRUPTED-MARKER", "fRecordedEpoch": 1.0,
        "bInterrupted": True})
    listStdin = []
    monkeypatch.setattr(
        chat.agentCouncilCharter, "flistBuildChatQuotedMaterial",
        lambda dictCampaign, listMessages: listStdin.append(
            [m["sText"] for m in listMessages]) or [])
    _fnAskAndAwait(dictControllerState, sCampaignId, "Now?")
    assert listStdin == [["Now?"]]


def testConversationFilesArePrivate(tmp_path, monkeypatch):
    ChatGatewayDouble().fnInstall(monkeypatch)
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(tmp_path, dictControllerState)
    _fnAskAndAwait(dictControllerState, sCampaignId,
                   "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789")
    sDirectory = agentCouncilChatRecords.fsChatsDirectory(
        _fsCampaignDirectory(dictSession))
    assert oct(os.stat(sDirectory).st_mode & 0o777) == "0o700"
    for sName in os.listdir(sDirectory):
        assert oct(os.stat(os.path.join(sDirectory, sName)).st_mode
                   & 0o777) == "0o600"
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in json.dumps(
        _flistChatFiles(dictSession))


# ---------------------------------------------------------------------
# The event log
# ---------------------------------------------------------------------


def _fdictStoreWithCampaign(tmp_path, iEventCountBound=500):
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"),
        dictBounds={"iEventCountBound": iEventCountBound})
    dictCampaign = fdictBuildCampaign("projectAlpha")
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    return dictStore, dictCampaign["sCampaignId"]


def _fnAppendEvents(dictStore, sCampaignId, iCount):
    for iEvent in range(iCount):
        agentCouncilStore.fdictAppendCampaignEvent(
            dictStore, sCampaignId,
            {"sEventKind": "turnStarted", "sTurnId": f"turn-{iEvent}",
             "sDetail": ""})


@pytest.mark.falsification
def testTheSequenceContinuesAcrossARestart(tmp_path):
    """Kills: restarting the numbering at 1 after a reload."""
    dictStore, sCampaignId = _fdictStoreWithCampaign(tmp_path)
    _fnAppendEvents(dictStore, sCampaignId, 7)
    dictReloaded = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=dictStore["sDurableStoreRoot"])
    agentCouncilStore.fdictReloadDurableCampaigns(dictReloaded)
    dictNext = agentCouncilStore.fdictAppendCampaignEvent(
        dictReloaded, sCampaignId, {"sEventKind": "turnStarted",
                                    "sTurnId": "after", "sDetail": ""})
    assert dictNext["iSequence"] == 8
    dictEvents = agentCouncilStore.fdictCollectCampaignEvents(
        dictReloaded, sCampaignId, 0)
    assert [d["iSequence"] for d in dictEvents["listEvents"]] == list(
        range(1, 9)), "the Agent tabs reopen with the events they had"


@pytest.mark.falsification
def testATornLastLineIsTruncatedAndRecovered(tmp_path):
    """Kills: reading a torn final line as an event."""
    dictStore, sCampaignId = _fdictStoreWithCampaign(tmp_path)
    _fnAppendEvents(dictStore, sCampaignId, 3)
    sPath = dictStore["dictEntriesById"][sCampaignId]["sEventLogPath"]
    with open(sPath, "ab") as fileLog:
        fileLog.write(b'{"iSequence": 4, "sEventKind": "turnSta')
    iSizeComplete = os.path.getsize(sPath) - len(
        b'{"iSequence": 4, "sEventKind": "turnSta')
    assert agentCouncilEventLog.fiRecoverEventLog(sPath) == 3
    assert os.path.getsize(sPath) == iSizeComplete
    with open(sPath, "ab") as fileLog:
        fileLog.write(b"not json at all\n")
    assert agentCouncilEventLog.fiRecoverEventLog(sPath) == 3
    assert os.path.getsize(sPath) == iSizeComplete


def testTheLogIsRedactedAndPrivate(tmp_path):
    dictStore, sCampaignId = _fdictStoreWithCampaign(tmp_path)
    agentCouncilStore.fdictAppendCampaignEvent(dictStore, sCampaignId, {
        "sEventKind": "turnOutput", "sTurnId": "t",
        "sDetail": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"})
    sPath = dictStore["dictEntriesById"][sCampaignId]["sEventLogPath"]
    assert oct(os.stat(sPath).st_mode & 0o777) == "0o600"
    with open(sPath) as fileLog:
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in fileLog.read()


def testPagingBackReachesEveryEventInBoundedPages(tmp_path, monkeypatch):
    monkeypatch.setattr(agentCouncilEventLog, "I_PAGE_BLOCK_BYTES", 256)
    dictStore, sCampaignId = _fdictStoreWithCampaign(
        tmp_path, iEventCountBound=5)
    _fnAppendEvents(dictStore, sCampaignId, 40)
    dictEvents = agentCouncilStore.fdictCollectCampaignEvents(
        dictStore, sCampaignId, 0)
    assert dictEvents["iLowestRetainedSequence"] == 36
    listSeen = []
    iCursor = -1
    for _ in range(20):
        dictPage = agentCouncilStore.fdictCollectEarlierCampaignEvents(
            dictStore, sCampaignId, iCursor, 7)
        assert len(dictPage["listEvents"]) <= 7
        listSeen = [d["iSequence"] for d in dictPage["listEvents"]] + listSeen
        iCursor = dictPage["iNextBeforeOffset"]
        if dictPage["bStartReached"]:
            break
    assert listSeen == list(range(1, 41))


@pytest.mark.falsification
def testRetentionSparesACampaignHoldingAConversation(tmp_path, monkeypatch):
    """Kills: evicting chat-bearing campaigns like any other."""
    ChatGatewayDouble().fnInstall(monkeypatch)
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"),
        dictBounds={"iRetainedCampaignCount": 1})
    dictControllerState = {}
    sCampaignId, dictSession = ftOpenSession(
        tmp_path, dictControllerState, dictStore=dictStore)
    dictStore["dictEntriesById"][sCampaignId]["dictCampaign"]["sState"] = (
        "failed")
    asyncio.run(chat.fdictCloseChatSession(dictControllerState, sCampaignId))
    for _ in range(2):
        agentCouncilStore.fdictRegisterStartedCampaign(
            dictStore, fdictBuildCampaign("projectAlpha"))
    assert sCampaignId in dictStore["dictEntriesById"]
    assert os.path.isdir(_fsCampaignDirectory(dictSession))
