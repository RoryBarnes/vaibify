"""The Prompt Record viewer shows what capture actually landed.

The review gate asks the researcher to confirm what the sanitizer
produced; before this viewer the dialog showed the first 40 raw JSON
lines of one session, and Approve signed off every session. The record
here is planted through the REAL capture pipeline (sanitize, then land
with its hash chain), so the viewer reads a genuine record: a prompt, a
reply, a tool call and its result, a token the sanitizer must redact,
and a line that is not JSON, which must be shown rather than dropped.
"""

import json
import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

S_PROMPT = "Please summarize the results table"
S_TOKEN = "ghp_" + "aaaaaaaaaaaaaaaaaaaa1111111111111111"
S_TRANSCRIPT_PATH = "/home/user/.claude/projects/-project/session-one.jsonl"


class _StubTranscriptSource:
    """Serve one transcript to the capture pipeline's fetch."""

    def __init__(self, baTranscript):
        self.baTranscript = baTranscript

    def fbaFetchFile(self, sContainerId, sFilePath):
        return self.baTranscript


def _fbaTranscript():
    listRecords = [
        {"type": "user", "timestamp": "2026-01-01T10:00:00Z",
         "message": {"role": "user", "content": S_PROMPT}},
        {"type": "assistant", "timestamp": "2026-01-01T10:00:05Z",
         "message": {"content": [
             {"type": "text", "text": "Reading the table now."},
             {"type": "tool_use", "name": "Bash",
              "input": {"command": "cat results.csv"}}]}},
        {"type": "user", "timestamp": "2026-01-01T10:00:06Z",
         "message": {"content": [{"type": "tool_result",
                                  "content": "token " + S_TOKEN}]}},
    ]
    sText = "".join(json.dumps(d) + "\n" for d in listRecords)
    return (sText + "garbled {not json\n").encode("utf-8")


def _fnPlantRecord(serverHub):
    from vaibify.gui import promptRecordManager
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    sProject = os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)
    filesRepo = ffilesEnsureRepoFiles(sProject)
    baTranscript = _fbaTranscript()
    dictSanitized = promptRecordManager.fdictSanitizeNewTranscriptLines(
        _StubTranscriptSource(baTranscript), "cid", filesRepo,
        {S_TRANSCRIPT_PATH: {"iSizeBytes": len(baTranscript),
                             "sLaunchDirectory": sProject}},
        sProject, [],
    )
    promptRecordManager.fdictLandSanitizedSessions(filesRepo, dictSanitized)
    sWorkflowPath = os.path.join(
        sProject, ".vaibify", "projects", S_HOST_WORKFLOW_NAME + ".json",
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    dictWorkflow["dictAiProvenance"] = {"dictPromptRecord": {
        "bEnabled": True, "bFirstCaptureReviewed": False,
    }}
    with open(sWorkflowPath, "w") as fileWorkflow:
        json.dump(dictWorkflow, fileWorkflow)
    return sWorkflowPath


def _fnInterceptTheCapturePoll(pageDashboard):
    """This lane's Docker adapter is fail-closed; no pass may reach it."""
    pageDashboard.route(
        "**/prompt-record/capture*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bPaused": True, "sPausedBy": "lane"}),
        ),
    )


@pytest.mark.falsification
def test_the_review_shows_the_redacted_conversation(pageDashboard, serverHub):
    """Kills: dropping the highlight on redaction markers."""
    sWorkflowPath = _fnPlantRecord(serverHub)
    _fnInterceptTheCapturePoll(pageDashboard)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    pageDashboard.click('.requirement-group-header[data-group="ai"]')
    pageDashboard.click('.requirement-row-header[data-req="promptRecord"]')
    pageDashboard.click(".wf-review-prompt-record")
    sTurns = "#promptRecordViewerTurns"
    pageDashboard.wait_for_selector(
        sTurns + " .prompt-record-turn-prompt", timeout=15000,
    )
    sText = pageDashboard.locator(sTurns).text_content()
    assert S_PROMPT in sText
    assert S_TOKEN not in sText
    elCall = pageDashboard.locator(
        sTurns + " details.prompt-record-turn-tool-call",
    )
    assert elCall.count() == 1 and elCall.get_attribute("open") is None
    assert "Bash" in elCall.locator("summary").text_content()
    assert pageDashboard.locator(
        sTurns + " mark.prompt-record-redaction").count() >= 1
    assert pageDashboard.locator(
        sTurns + " .prompt-record-turn-unparsed").count() == 1

    pageDashboard.check("#checkPromptRecordRedactionsOnly")
    listShown = pageDashboard.locator(sTurns + " .prompt-record-turn")
    assert listShown.count() == 1
    assert "has-redaction" in listShown.first.get_attribute("class")

    pageDashboard.click('[data-viewer-action="approve"]')
    pageDashboard.wait_for_selector(
        "#promptRecordViewerApprove :text('reviewed and approved')",
        timeout=10000,
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictRecord = json.load(fileWorkflow)["dictAiProvenance"][
            "dictPromptRecord"]
    assert dictRecord["bFirstCaptureReviewed"] is True
    assert pageDashboard.listPageErrors == []
