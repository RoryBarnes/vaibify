"""Ask-the-chairbot conversations kept on disk, so a restart loses none.

One file per conversation:
``~/.vaibify/agentCouncils/<campaign>/chats/<conversation>.json``,
mode 600 in a mode-700 directory, written atomically after every state
change, with the campaign records' credential redaction applied to
every message. It is kept apart from ``provenance.json``: a chat answer
is not plan provenance.

A conversation is OPEN (it may be resting, its runner gone, until the
next question wakes one) or ARCHIVED (the researcher closed it; read
only). A hub shutdown or a lease release leaves it open; only the
explicit Close archives it.

A question is recorded with its id as ``sPendingMessageId`` BEFORE the
runner is asked, so a conversation reloaded with a pending question
says it was interrupted and is never re-asked: paid work does not
repeat without the researcher's click.
"""

__all__ = [
    "S_STATUS_ARCHIVED",
    "S_STATUS_OPEN",
    "fbCampaignHoldsConversation",
    "fdictDescribeWithoutASession",
    "fdictReadOpenConversation",
    "flistDescribeArchived",
    "flistReadArchivedConversations",
    "flistMarkInterruptedQuestion",
    "fnSaveSession",
    "fnWriteConversation",
    "fsChatsDirectory",
]

import json
import logging
import os
import re
import time

from vaibify.config.durableFile import fnWriteFileDurably

from .agentCouncilStore import fjsonRedactCredentialsInRecord

logger = logging.getLogger("vaibify")

S_STATUS_OPEN = "open"
S_STATUS_ARCHIVED = "archived"
S_CHATS_DIRECTORY_NAME = "chats"
I_SCHEMA_VERSION = 1
_I_DIRECTORY_MODE_PRIVATE = 0o700
_REGEX_CONVERSATION_ID = re.compile(r"^chat-[0-9a-f]{12}$")


def fsChatsDirectory(sCampaignDirectory):
    """Return the directory a campaign's conversations live in."""
    return os.path.join(sCampaignDirectory, S_CHATS_DIRECTORY_NAME)


def _fsConversationPath(sCampaignDirectory, sConversationId):
    if not _REGEX_CONVERSATION_ID.match(sConversationId or ""):
        raise ValueError(f"not a conversation id: {sConversationId!r}")
    return os.path.join(
        fsChatsDirectory(sCampaignDirectory), sConversationId + ".json")


def fnWriteConversation(sCampaignDirectory, dictConversation):
    """Write one conversation atomically, redacted, at mode 600."""
    sPath = _fsConversationPath(
        sCampaignDirectory, dictConversation["sSessionId"])
    sDirectory = os.path.dirname(sPath)
    os.makedirs(sDirectory, mode=_I_DIRECTORY_MODE_PRIVATE, exist_ok=True)
    os.chmod(sDirectory, _I_DIRECTORY_MODE_PRIVATE)
    dictRecord = fjsonRedactCredentialsInRecord(
        dict(dictConversation, iSchemaVersion=I_SCHEMA_VERSION))
    fnWriteFileDurably(
        sPath, json.dumps(dictRecord, sort_keys=True).encode("utf-8"),
        iMode=0o600)


def _flistReadConversations(sCampaignDirectory):
    sDirectory = fsChatsDirectory(sCampaignDirectory)
    try:
        listNames = sorted(os.listdir(sDirectory))
    except OSError:
        return []
    listConversations = []
    for sName in listNames:
        if not sName.endswith(".json") or not _REGEX_CONVERSATION_ID.match(
                sName[:-len(".json")]):
            continue
        try:
            with open(os.path.join(sDirectory, sName), encoding="utf-8") as fileIn:
                dictRecord = json.load(fileIn)
        except (OSError, ValueError) as error:
            logger.warning("Chairbot conversation %s unreadable: %s",
                           sName, error)
            continue
        if isinstance(dictRecord, dict) and isinstance(
                dictRecord.get("listMessages"), list):
            listConversations.append(dictRecord)
    return listConversations


def fdictReadOpenConversation(sCampaignDirectory):
    """Return the campaign's open conversation (the newest), or None."""
    listOpen = [d for d in _flistReadConversations(sCampaignDirectory)
                if d.get("sStatus") == S_STATUS_OPEN]
    if not listOpen:
        return None
    return max(listOpen, key=lambda d: float(d.get("fUpdatedEpoch") or 0))


def flistReadArchivedConversations(sCampaignDirectory):
    """Return the campaign's closed conversations, newest first."""
    listArchived = [d for d in _flistReadConversations(sCampaignDirectory)
                    if d.get("sStatus") == S_STATUS_ARCHIVED]
    return sorted(listArchived,
                  key=lambda d: float(d.get("fClosedEpoch") or 0),
                  reverse=True)


def flistMarkInterruptedQuestion(dictConversation):
    """Return the messages with a never-answered question marked interrupted.

    The question still pending when the hub stopped is shown as
    "interrupted by shutdown -- not re-asked" and offered again only
    behind the researcher's click.
    """
    sPendingId = dictConversation.get("sPendingMessageId") or ""
    listMessages = []
    for dictMessage in dictConversation.get("listMessages") or []:
        dictCopy = dict(dictMessage)
        if sPendingId and dictCopy.get("sMessageId") == sPendingId:
            dictCopy["bInterrupted"] = True
        listMessages.append(dictCopy)
    return listMessages


def fbCampaignHoldsConversation(sCampaignDirectory):
    """Return True when any conversation, open or archived, is on disk."""
    return bool(_flistReadConversations(sCampaignDirectory))


def fnSaveSession(sCampaignDirectory, dictSession, sStatus):
    """Write one in-process session as its durable conversation record."""
    fNow = time.time()
    fnWriteConversation(sCampaignDirectory, {
        "sSessionId": dictSession["sSessionId"],
        "sCampaignId": dictSession["sCampaignId"],
        "sStatus": sStatus,
        "sState": dictSession.get("sState", ""),
        "listMessages": dictSession.get("listMessages", []),
        "sPendingMessageId": dictSession.get("sPendingMessageId", ""),
        "sParticipantId": dictSession.get("sParticipantId", ""),
        "dictModelIdentity": dictSession.get("dictModelIdentity", {}),
        "fUpdatedEpoch": fNow,
        "fClosedEpoch": fNow if sStatus == S_STATUS_ARCHIVED else 0,
    })


def flistDescribeArchived(sCampaignDirectory):
    """The closed conversations as the panel shows them, newest first."""
    if not sCampaignDirectory:
        return []
    return [{"sSessionId": dictRecord.get("sSessionId", ""),
             "fClosedEpoch": dictRecord.get("fClosedEpoch") or 0,
             "listMessages": dictRecord.get("listMessages") or []}
            for dictRecord in flistReadArchivedConversations(
                sCampaignDirectory)]


def fdictDescribeWithoutASession(sCampaignDirectory, iMaximumMessages):
    """Describe a campaign with no live session from what is on disk.

    A conversation the hub set aside at shutdown or release is OPEN on
    disk with no session in this process: ``bResumable`` says so, and
    its transcript is shown, a question that was pending marked
    interrupted and never re-asked. Opening it seeds the new session.
    """
    dictSaved = (fdictReadOpenConversation(sCampaignDirectory)
                 if sCampaignDirectory else None)
    listMessages = flistMarkInterruptedQuestion(dictSaved) if dictSaved else []
    return {"bOpen": False, "bResumable": dictSaved is not None,
            "sState": "", "listMessages": listMessages,
            "sPendingMessageId": "", "sFailureReason": "",
            "sChairbotParticipantId": "", "sResolvedModel": "",
            "iMessagesRemaining": max(0, iMaximumMessages - len(listMessages)),
            "iIdleSecondsRemaining": 0,
            "listArchived": flistDescribeArchived(sCampaignDirectory)}
