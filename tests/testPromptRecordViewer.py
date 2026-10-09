"""Reading a redacted Prompt Record session as a conversation.

The viewer is what a researcher reviews before approving the record,
so a turn it drops is a turn nobody approved. The records here have the
Claude Code transcript shape (``type``/``message``/``content`` blocks)
that capture collects.
"""

import json

import pytest

from vaibify.gui.promptRecordViewer import (
    I_TURN_TEXT_CHARACTERS,
    fdictSummarizeTurns,
    flistParseTranscriptTurns,
)


def _fsTranscript(listRecords, sRawTail=""):
    return "".join(json.dumps(d) + "\n" for d in listRecords) + sRawTail


def _flistKinds(listTurns):
    return [dictTurn["sKind"] for dictTurn in listTurns]


def test_each_kind_of_turn_is_read_from_its_block():
    sText = _fsTranscript([
        {"type": "user", "timestamp": "t1",
         "message": {"content": "plot the table"}},
        {"type": "assistant", "timestamp": "t2", "message": {"content": [
            {"type": "thinking", "thinking": "consider the axes"},
            {"type": "text", "text": "Plotting now."},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "python plot.py"}}]}},
        {"type": "user", "timestamp": "t3", "message": {"content": [
            {"type": "tool_result",
             "content": [{"type": "text", "text": "saved plot.pdf"}]}]}},
        {"type": "user", "isMeta": True, "timestamp": "t4",
         "message": {"content": "a system reminder"}},
    ])
    listTurns = flistParseTranscriptTurns(sText)
    assert _flistKinds(listTurns) == [
        "prompt", "thinking", "reply", "tool-call", "tool-result",
        "context",
    ]
    assert listTurns[3]["sToolName"] == "Bash"
    assert "python plot.py" in listTurns[3]["sText"]
    assert listTurns[4]["sText"] == "saved plot.pdf"


@pytest.mark.falsification
def test_an_unparseable_line_is_shown_not_dropped():
    """A line that is not JSON becomes an ``unparsed`` turn.

    A sanitizer once left records that were no longer valid JSON; a
    viewer that dropped them would ask the researcher to approve lines
    they were never shown.

    Kills: skipping a line that fails to parse.
    """
    sText = _fsTranscript(
        [{"type": "user", "message": {"content": "hello"}}],
        sRawTail="garbled {not json\n",
    )
    listTurns = flistParseTranscriptTurns(sText)
    assert _flistKinds(listTurns) == ["prompt", "unparsed"]
    assert listTurns[1]["sText"] == "garbled {not json"


def test_records_without_conversation_are_counted_not_hidden():
    sText = _fsTranscript([
        {"type": "mode", "mode": "plan"},
        {"type": "user", "timestamp": "2026-01-01T10:00:00Z",
         "message": {"content": "first"}},
        {"type": "file-history-snapshot"},
        {"type": "user", "timestamp": "2026-01-01T11:00:00Z",
         "message": {"content": "second"}},
    ])
    listTurns = flistParseTranscriptTurns(sText)
    dictSummary = fdictSummarizeTurns(sText, listTurns)
    assert dictSummary == {
        "iTurnCount": 2,
        "iPromptCount": 2,
        "sFirstTimestampUtc": "2026-01-01T10:00:00Z",
        "sLastTimestampUtc": "2026-01-01T11:00:00Z",
        "iRecordsWithoutConversation": 2,
    }


def test_a_long_turn_is_capped_and_says_so():
    sLong = "x" * (I_TURN_TEXT_CHARACTERS + 5)
    [dictTurn] = flistParseTranscriptTurns(_fsTranscript(
        [{"type": "user", "message": {"content": sLong}}],
    ))
    assert len(dictTurn["sText"]) == I_TURN_TEXT_CHARACTERS
    assert dictTurn["bTruncated"] is True
    assert dictTurn["iCharacters"] == len(sLong)


def test_a_redaction_marker_flags_its_turn():
    [dictTurn] = flistParseTranscriptTurns(_fsTranscript(
        [{"type": "user",
          "message": {"content": "key [REDACTED: vendor-token] here"}}],
    ))
    assert dictTurn["bRedacted"] is True


# --- Codex and Gemini ------------------------------------------------
# Synthetic, secret-free records in each CLI's own shape.


def test_codex_turns_come_from_response_items_only():
    """Codex repeats prompts and replies as event_msg records for its
    terminal; reading both would show every exchange twice."""
    sText = _fsTranscript([
        {"type": "session_meta", "payload": {"cwd": "/workspace/p"}},
        {"type": "response_item", "timestamp": "t0", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text",
                 "text": "<environment_context>cwd</environment_context>"}]}},
        {"type": "response_item", "timestamp": "t1", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "fit the curve"}]}},
        {"type": "event_msg", "timestamp": "t1",
         "payload": {"type": "user_message", "message": "fit the curve"}},
        {"type": "response_item", "timestamp": "t2", "payload": {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "least squares"}]}},
        {"type": "response_item", "timestamp": "t3", "payload": {
            "type": "function_call", "name": "shell",
            "arguments": "{\"command\": [\"python\", \"fit.py\"]}"}},
        {"type": "response_item", "timestamp": "t4", "payload": {
            "type": "function_call_output",
            "output": {"content": "chi2 = 1.02"}}},
        {"type": "response_item", "timestamp": "t5", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Fitted."}]}},
        {"type": "event_msg", "timestamp": "t5",
         "payload": {"type": "agent_message", "message": "Fitted."}},
    ])
    listTurns = flistParseTranscriptTurns(sText, "codex")
    assert _flistKinds(listTurns) == [
        "context", "prompt", "thinking", "tool-call", "tool-result", "reply"]
    assert listTurns[1]["sText"] == "fit the curve"
    assert listTurns[3]["sToolName"] == "shell"
    assert listTurns[4]["sText"] == "chi2 = 1.02"
    dictSummary = fdictSummarizeTurns(sText, listTurns)
    assert dictSummary["iPromptCount"] == 1
    assert dictSummary["iRecordsWithoutConversation"] == 3


def _flistGeminiSession():
    return [
        {"sessionId": "s1", "projectHash": "h", "startTime": "t0",
         "lastUpdated": "t0", "kind": "main"},
        {"id": "u1", "timestamp": "t1", "type": "user",
         "content": [{"text": "plot the spectrum"}]},
        {"id": "g1", "timestamp": "t2", "type": "gemini", "content": "",
         "toolCalls": [{"name": "run_shell_command",
                        "args": {"command": "python plot.py"},
                        "result": "saved spectrum.pdf"}]},
        {"id": "g1", "timestamp": "t2", "type": "gemini", "content": "",
         "toolCalls": [{"name": "run_shell_command",
                        "args": {"command": "python plot.py"},
                        "result": "saved spectrum.pdf"}],
         "tokens": {"total": 50}},
        {"$set": {"lastUpdated": "t2"}},
        {"id": "u2", "timestamp": "t3", "type": "user",
         "content": [{"text": "use a log axis"}]},
        {"id": "g2", "timestamp": "t4", "type": "gemini",
         "content": "Done with a linear axis."},
        {"$rewindTo": "u2"},
        {"id": "u3", "timestamp": "t5", "type": "user",
         "content": [{"text": "use a log y axis"}]},
        {"id": "g3", "timestamp": "t6", "type": "gemini",
         "content": "Draft reply."},
        {"id": "g3", "timestamp": "t6", "type": "gemini",
         "content": "Replotted with a log y axis.",
         "thoughts": [{"subject": "Axis", "description": "log scale"}]},
    ]


@pytest.mark.falsification
def test_a_gemini_session_shows_its_final_state_and_keeps_what_was_rewound():
    """The ruling: the conversation as it finally stood, rewound turns
    kept where they were taken back, an edited text marked with the
    text it replaced, and a bookkeeping-only update leaving no mark.

    Kills: replaying a rewind by dropping the rewound messages.
    """
    listTurns = flistParseTranscriptTurns(
        _fsTranscript(_flistGeminiSession()), "gemini")
    listShown = [
        (d["sKind"], d["sText"], d["bRewound"]) for d in listTurns]
    assert listShown == [
        ("prompt", "plot the spectrum", False),
        ("tool-call", '{"command": "python plot.py"}', False),
        ("tool-result", "saved spectrum.pdf", False),
        ("prompt", "use a log axis", True),
        ("reply", "Done with a linear axis.", True),
        ("prompt", "use a log y axis", False),
        ("thinking", "Axis: log scale", False),
        ("reply", "Replotted with a log y axis.", False),
    ]
    dictEdited = listTurns[-1]
    assert dictEdited["bEdited"] is True
    assert dictEdited["listEarlierTexts"] == ["Draft reply."]
    assert not any(
        d.get("bEdited") for d in listTurns[:-1]), (
        "a version that only added token counts is not an edit")


def test_a_gemini_session_counts_only_records_without_conversation():
    sText = _fsTranscript(_flistGeminiSession())
    listTurns = flistParseTranscriptTurns(sText, "gemini")
    dictSummary = fdictSummarizeTurns(sText, listTurns)
    assert dictSummary["iPromptCount"] == 3
    assert dictSummary["iRecordsWithoutConversation"] == 3, (
        "the metadata line, the $set and the $rewindTo carry no "
        "conversation; a superseded version of a message does")


def test_an_unreadable_gemini_line_is_shown_not_dropped():
    sText = _fsTranscript(_flistGeminiSession()[:2], "not json\n")
    listTurns = flistParseTranscriptTurns(sText, "gemini")
    assert listTurns[-1]["sKind"] == "unparsed"
    assert listTurns[-1]["sText"] == "not json"


@pytest.mark.falsification
def test_a_second_gemini_rewind_to_an_earlier_point_loses_nothing():
    """Messages A..E; rewind to D, then to B. The final conversation is A,
    and B..E -- including D and E, which the FIRST rewind took back --
    are all still shown, as one rewound group.

    Kills: placing a rewound group by its position in the live list,
    which a later, earlier rewind shortens past it.
    """
    listRecords = [
        {"id": sId, "timestamp": "t" + sId, "type": "user",
         "content": [{"text": "message " + sId}]}
        for sId in "ABCDE"
    ]
    listRecords.insert(5, {"$rewindTo": "D"})
    listRecords.append({"$rewindTo": "B"})
    listTurns = flistParseTranscriptTurns(
        _fsTranscript(listRecords), "gemini")
    assert [(d["sText"], d["bRewound"]) for d in listTurns] == [
        ("message A", False),
        ("message B", True), ("message C", True),
        ("message D", True), ("message E", True),
    ]
    assert {d.get("iRewindGroup") for d in listTurns[1:]} == {1}
