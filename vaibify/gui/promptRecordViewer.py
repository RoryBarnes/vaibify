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

Each agent writes its own shape, so the reader is chosen by the
provider the capture record names:

- **Claude Code**: one record per message, ``type`` user/assistant.
- **Codex**: ``response_item`` records carry the conversation; the
  ``event_msg`` records repeat the same prompts and replies for the
  terminal and are counted, not shown twice.
- **Gemini**: the file is append-only and REPLAYED. A record repeating
  an earlier message's ``id`` replaces it (mostly bookkeeping: token
  counts, tool calls), and ``{"$rewindTo": id}`` takes that message
  and every later one back. The session is shown as it finally stood;
  rewound turns are kept, flagged ``bRewound`` at the point they were
  taken back, and a message whose TEXT changed is flagged ``bEdited``
  with its earlier text in ``listEarlierTexts``.
"""

__all__ = [
    "I_TURN_TEXT_CHARACTERS",
    "fdictSummarizeTurns",
    "flistParseTranscriptTurns",
]

import json

I_TURN_TEXT_CHARACTERS = 20000
S_PROVIDER_CODEX = "codex"
S_PROVIDER_GEMINI = "gemini"
S_REDACTION_MARKER = "[REDACTED: "


def flistParseTranscriptTurns(sText, sProvider="claude"):
    """Return every turn in a redacted JSONL transcript, in order."""
    listLines = [sLine for sLine in sText.split("\n") if sLine.strip()]
    if sProvider == S_PROVIDER_GEMINI:
        return _flistGeminiTurns(listLines)
    fnTurnsFromRecord = (
        _flistCodexTurnsFromRecord if sProvider == S_PROVIDER_CODEX
        else _flistTurnsFromRecord
    )
    listTurns = []
    for iRecord, sLine in enumerate(listLines):
        try:
            jsonRecord = json.loads(sLine)
        except ValueError:
            listFromRecord = [_fdictTurn("unparsed", sLine, "")]
        else:
            listFromRecord = (
                fnTurnsFromRecord(jsonRecord)
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
    setRecordsWithTurns = set()
    for dictTurn in listTurns:
        setRecordsWithTurns.add(dictTurn["iRecord"])
        setRecordsWithTurns.update(dictTurn.get("listSupersededRecords", []))
    iRecordsWithTurns = len(setRecordsWithTurns)
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


# --- Codex -----------------------------------------------------------

_T_CODEX_CONTEXT_PREFIXES = ("<environment_context>", "<user_instructions>")


def _flistCodexTurnsFromRecord(jsonRecord):
    """Return the turns one Codex ``response_item`` contributes."""
    if jsonRecord.get("type") != "response_item":
        return []
    jsonPayload = jsonRecord.get("payload")
    if not isinstance(jsonPayload, dict):
        return []
    sTimestamp = str(jsonRecord.get("timestamp") or "")
    sItemType = jsonPayload.get("type")
    if sItemType == "message":
        return _flistCodexMessageTurns(jsonPayload, sTimestamp)
    if sItemType == "reasoning":
        sText = _fsJoinBlockTexts(jsonPayload.get("summary"))
        return [_fdictTurn("thinking", sText, sTimestamp)] if sText else []
    if sItemType in ("function_call", "custom_tool_call", "local_shell_call"):
        return [_fdictCodexToolCall(jsonPayload, sTimestamp)]
    if sItemType in ("function_call_output", "custom_tool_call_output"):
        return [_fdictTurn(
            "tool-result", _fsCodexOutputText(jsonPayload.get("output")),
            sTimestamp)]
    return []


def _flistCodexMessageTurns(jsonPayload, sTimestamp):
    sText = _fsJoinBlockTexts(jsonPayload.get("content"))
    sRole = jsonPayload.get("role")
    if sRole == "assistant":
        return [_fdictTurn("reply", sText, sTimestamp)]
    if sRole == "user" and not sText.lstrip().startswith(
            _T_CODEX_CONTEXT_PREFIXES):
        return [_fdictTurn("prompt", sText, sTimestamp)]
    return [_fdictTurn("context", sText, sTimestamp)]


def _fdictCodexToolCall(jsonPayload, sTimestamp):
    jsonArguments = jsonPayload.get(
        "arguments", jsonPayload.get("input", jsonPayload.get("action")))
    sArguments = (
        jsonArguments if isinstance(jsonArguments, str)
        else json.dumps(jsonArguments, ensure_ascii=False))
    dictTurn = _fdictTurn("tool-call", sArguments, sTimestamp)
    dictTurn["sToolName"] = str(
        jsonPayload.get("name") or jsonPayload.get("type") or "")
    return dictTurn


def _fsCodexOutputText(jsonOutput):
    if isinstance(jsonOutput, dict):
        jsonOutput = jsonOutput.get("content", jsonOutput.get("output"))
    if isinstance(jsonOutput, list):
        return _fsJoinBlockTexts(jsonOutput)
    return "" if jsonOutput is None else str(jsonOutput)


def _fsJoinBlockTexts(jsonBlocks):
    """Return the text of a string or a list of ``{text}`` blocks."""
    if isinstance(jsonBlocks, str):
        return jsonBlocks
    return "\n".join(
        str(jsonBlock.get("text") or "") for jsonBlock in (
            jsonBlocks if isinstance(jsonBlocks, list) else [])
        if isinstance(jsonBlock, dict) and jsonBlock.get("text")
    )


# --- Gemini ----------------------------------------------------------

def _flistGeminiTurns(listLines):
    """Replay a Gemini session and return its turns as it finally stood."""
    dictState = {"listTimeline": [], "dictMessages": {}}
    listUnparsed = []
    for iRecord, sLine in enumerate(listLines):
        try:
            jsonRecord = json.loads(sLine)
        except ValueError:
            listUnparsed.append(dict(
                _fdictTurn("unparsed", sLine, ""), iRecord=iRecord))
            continue
        if isinstance(jsonRecord, dict):
            _fnApplyGeminiRecord(dictState, jsonRecord, iRecord)
    return _flistAssembleGeminiTurns(dictState) + listUnparsed


def _fnApplyGeminiRecord(dictState, jsonRecord, iRecord):
    """Apply one record: a message (new or replacing) or a rewind."""
    if "$rewindTo" in jsonRecord:
        _fnRewindGemini(dictState, jsonRecord["$rewindTo"])
        return
    sId = jsonRecord.get("id")
    if not sId or jsonRecord.get("type") not in ("user", "gemini"):
        return
    dictMessage = dictState["dictMessages"].get(sId)
    if dictMessage is None:
        dictMessage = {"listVersions": [], "listRecords": []}
        dictState["dictMessages"][sId] = dictMessage
        dictState["listTimeline"].append({"sId": sId})
    dictMessage["listVersions"].append(jsonRecord)
    dictMessage["listRecords"].append(iRecord)


def _fnRewindGemini(dictState, sId):
    """Fold a message and everything after it into one rewound group.

    The timeline holds live messages and rewound groups in the order
    they happened. A rewind takes the live message it names and every
    entry after it -- live messages AND groups an earlier rewind left
    there -- into a single group in their place, so a second rewind
    to an earlier point can never drop what the first one kept.
    """
    listTimeline = dictState["listTimeline"]
    for iPosition, dictEntry in enumerate(listTimeline):
        if dictEntry.get("sId") == sId:
            break
    else:
        return
    listIds = []
    for dictEntry in listTimeline[iPosition:]:
        listIds.extend(dictEntry.get("listRewoundIds") or [dictEntry["sId"]])
    del listTimeline[iPosition:]
    listTimeline.append({"listRewoundIds": listIds})


def _flistAssembleGeminiTurns(dictState):
    """Return the timeline's turns, rewound groups numbered in place."""
    listTurns = []
    iGroup = 0
    for dictEntry in dictState["listTimeline"]:
        if "listRewoundIds" not in dictEntry:
            listTurns.extend(_flistGeminiMessageTurns(
                dictState["dictMessages"][dictEntry["sId"]], bRewound=False))
            continue
        iGroup += 1
        for sRewoundId in dictEntry["listRewoundIds"]:
            for dictTurn in _flistGeminiMessageTurns(
                    dictState["dictMessages"][sRewoundId], bRewound=True):
                dictTurn["iRewindGroup"] = iGroup
                listTurns.append(dictTurn)
    return listTurns


def _flistGeminiMessageTurns(dictMessage, bRewound):
    """Return the turns of one message's final version, with its history."""
    jsonFinal = dictMessage["listVersions"][-1]
    sTimestamp = str(jsonFinal.get("timestamp") or "")
    listTurns = []
    if jsonFinal.get("type") == "gemini":
        for jsonThought in jsonFinal.get("thoughts") or []:
            if isinstance(jsonThought, dict):
                listTurns.append(_fdictTurn("thinking", ": ".join(
                    str(jsonThought.get(sKey) or "")
                    for sKey in ("subject", "description")
                    if jsonThought.get(sKey)), sTimestamp))
    sText = _fsJoinBlockTexts(jsonFinal.get("content"))
    if sText:
        dictText = _fdictTurn(
            "prompt" if jsonFinal.get("type") == "user" else "reply",
            sText, sTimestamp)
        _fnMarkEarlierTexts(dictText, dictMessage, sText)
        listTurns.append(dictText)
    if jsonFinal.get("type") == "gemini":
        listTurns.extend(_flistGeminiToolTurns(jsonFinal, sTimestamp))
    for dictTurn in listTurns:
        dictTurn["iRecord"] = dictMessage["listRecords"][-1]
        dictTurn["listSupersededRecords"] = dictMessage["listRecords"][:-1]
        dictTurn["bRewound"] = bRewound
    return listTurns


def _fnMarkEarlierTexts(dictTurn, dictMessage, sFinalText):
    """Flag an edited message and keep the text it replaced.

    Only a change of TEXT is an edit; a version that added token counts
    or tool calls repeats the same text and leaves no mark.
    """
    listEarlier = []
    for jsonVersion in dictMessage["listVersions"][:-1]:
        sEarlier = _fsJoinBlockTexts(jsonVersion.get("content"))
        if sEarlier and sEarlier != sFinalText and (
                not listEarlier or listEarlier[-1] != sEarlier):
            listEarlier.append(sEarlier[:I_TURN_TEXT_CHARACTERS])
    dictTurn["bEdited"] = bool(listEarlier)
    dictTurn["listEarlierTexts"] = listEarlier


def _flistGeminiToolTurns(jsonFinal, sTimestamp):
    listTurns = []
    for jsonCall in jsonFinal.get("toolCalls") or []:
        if not isinstance(jsonCall, dict):
            continue
        dictCall = _fdictTurn("tool-call", json.dumps(
            jsonCall.get("args"), ensure_ascii=False), sTimestamp)
        dictCall["sToolName"] = str(jsonCall.get("name") or "")
        listTurns.append(dictCall)
        if jsonCall.get("result") is not None:
            listTurns.append(_fdictTurn(
                "tool-result", _fsGeminiResultText(jsonCall["result"]),
                sTimestamp))
    return listTurns


def _fsGeminiResultText(jsonResult):
    if isinstance(jsonResult, str):
        return jsonResult
    return json.dumps(jsonResult, ensure_ascii=False)
