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
