"""Read a redacted Prompt Record session as a conversation.

A captured session is the agent CLI's JSONL transcript after
sanitization. This module turns it into the turns a researcher reviews:
their prompts and the agent's replies as text, and the tool calls,
tool results and reasoning that sit between them as foldable one-liners.

It reads only what capture already redacted, and it hides nothing it
cannot place: a line that is not JSON -- a sanitizer once left some
behind -- is shown raw as ``unparsed``, and records that carry no
conversation (mode switches, file-history snapshots, titles) are
counted in ``iRecordsWithoutConversation`` rather than silently dropped.
Only the Claude Code transcript shape is understood, because it is the
only one capture collects today.
"""

__all__ = [
    "I_TURN_TEXT_CHARACTERS",
    "fdictSummarizeTurns",
    "flistParseTranscriptTurns",
]

import json

I_TURN_TEXT_CHARACTERS = 20000
S_REDACTION_MARKER = "[REDACTED: "


def flistParseTranscriptTurns(sText):
    """Return every turn in a redacted JSONL transcript, in order."""
    listTurns = []
    listLines = [sLine for sLine in sText.split("\n") if sLine.strip()]
    for iRecord, sLine in enumerate(listLines):
        try:
            jsonRecord = json.loads(sLine)
        except ValueError:
            listFromRecord = [_fdictTurn("unparsed", sLine, "")]
        else:
            listFromRecord = (
                _flistTurnsFromRecord(jsonRecord)
                if isinstance(jsonRecord, dict) else []
            )
        for dictTurn in listFromRecord:
            dictTurn["iRecord"] = iRecord
        listTurns.extend(listFromRecord)
    return listTurns


def fdictSummarizeTurns(sText, listTurns):
    """Return the session's prompt count, time span and skipped records."""
    listTimestamps = [
        dictTurn["sTimestampUtc"] for dictTurn in listTurns
        if dictTurn["sTimestampUtc"]
    ]
    iRecords = sum(1 for sLine in sText.split("\n") if sLine.strip())
    iRecordsWithTurns = len({
        dictTurn["iRecord"] for dictTurn in listTurns
    })
    return {
        "iTurnCount": len(listTurns),
        "iPromptCount": sum(
            1 for dictTurn in listTurns if dictTurn["sKind"] == "prompt"
        ),
        "sFirstTimestampUtc": min(listTimestamps) if listTimestamps else "",
        "sLastTimestampUtc": max(listTimestamps) if listTimestamps else "",
        "iRecordsWithoutConversation": iRecords - iRecordsWithTurns,
    }


def _flistTurnsFromRecord(jsonRecord):
    """Return the turns one transcript record contributes."""
    sType = jsonRecord.get("type")
    if sType not in ("user", "assistant"):
        return []
    sTimestamp = str(jsonRecord.get("timestamp") or "")
    jsonContent = (jsonRecord.get("message") or {}).get("content")
    if sType == "user":
        sPromptKind = "context" if jsonRecord.get("isMeta") else "prompt"
        return _flistUserTurns(jsonContent, sPromptKind, sTimestamp)
    return _flistAssistantTurns(jsonContent, sTimestamp)


def _flistUserTurns(jsonContent, sPromptKind, sTimestamp):
    """Return a user record's prompt text and any tool results."""
    if isinstance(jsonContent, str):
        return [_fdictTurn(sPromptKind, jsonContent, sTimestamp)]
    listTurns = []
    for jsonBlock in jsonContent if isinstance(jsonContent, list) else []:
        if not isinstance(jsonBlock, dict):
            continue
        if jsonBlock.get("type") == "text":
            listTurns.append(_fdictTurn(
                sPromptKind, str(jsonBlock.get("text") or ""), sTimestamp,
            ))
        elif jsonBlock.get("type") == "tool_result":
            listTurns.append(_fdictTurn(
                "tool-result", _fsToolResultText(jsonBlock.get("content")),
                sTimestamp,
            ))
    return listTurns


def _flistAssistantTurns(jsonContent, sTimestamp):
    """Return an assistant record's replies, tool calls and reasoning."""
    listTurns = []
    for jsonBlock in jsonContent if isinstance(jsonContent, list) else []:
        if not isinstance(jsonBlock, dict):
            continue
        sBlockType = jsonBlock.get("type")
        if sBlockType == "text":
            listTurns.append(_fdictTurn(
                "reply", str(jsonBlock.get("text") or ""), sTimestamp,
            ))
        elif sBlockType == "tool_use":
            dictTurn = _fdictTurn(
                "tool-call",
                json.dumps(jsonBlock.get("input"), ensure_ascii=False),
                sTimestamp,
            )
            dictTurn["sToolName"] = str(jsonBlock.get("name") or "")
            listTurns.append(dictTurn)
        elif sBlockType == "thinking":
            listTurns.append(_fdictTurn(
                "thinking", str(jsonBlock.get("thinking") or ""), sTimestamp,
            ))
    return listTurns


def _fsToolResultText(jsonContent):
    """Return a tool result's text, whether a string or a list of blocks."""
    if isinstance(jsonContent, str):
        return jsonContent
    if isinstance(jsonContent, list):
        return "\n".join(
            str(jsonBlock.get("text") or "") for jsonBlock in jsonContent
            if isinstance(jsonBlock, dict) and jsonBlock.get("type") == "text"
        )
    return ""


def _fdictTurn(sKind, sText, sTimestamp):
    """Return one turn, its text capped and its redactions flagged."""
    return {
        "sKind": sKind,
        "sText": sText[:I_TURN_TEXT_CHARACTERS],
        "bTruncated": len(sText) > I_TURN_TEXT_CHARACTERS,
        "iCharacters": len(sText),
        "bRedacted": S_REDACTION_MARKER in sText,
        "sTimestampUtc": sTimestamp,
        "sToolName": "",
    }
