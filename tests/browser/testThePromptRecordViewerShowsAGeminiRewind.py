"""A Gemini session is reviewed as it finally stood, rewinds kept.

Gemini's transcript is append-only and replayed: a rewind takes turns
back, and a later record can replace a message's text. The ruling is to
show the final conversation, fold what was rewound into one collapsed
group where it was taken back, and mark a reply whose text changed with
its earlier text folded beneath it. The record is planted through the
REAL capture pipeline, so the viewer reads what capture landed, under
the provider-named session file capture chose.
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

S_TRANSCRIPT_PATH = (
    "/home/user/.gemini/tmp/project/chats/session-2026-10-08T10-00-a1b2c3d4"
    ".jsonl")


class _StubTranscriptSource:
    def __init__(self, baTranscript):
        self.baTranscript = baTranscript

    def fbaFetchFile(self, sContainerId, sFilePath):
        return self.baTranscript


def _fbaGeminiTranscript():
    listRecords = [
        {"sessionId": "a1b2c3d4", "kind": "main", "startTime": "t0"},
        {"id": "u1", "timestamp": "2026-10-08T10:00:00Z", "type": "user",
         "content": [{"text": "use a log axis"}]},
        {"id": "g1", "timestamp": "2026-10-08T10:00:04Z", "type": "gemini",
         "content": "Kept the linear axis."},
        {"$rewindTo": "u1"},
        {"id": "u2", "timestamp": "2026-10-08T10:01:00Z", "type": "user",
         "content": [{"text": "use a log y axis"}]},
        {"id": "g2", "timestamp": "2026-10-08T10:01:03Z", "type": "gemini",
         "content": "Draft reply."},
        {"id": "g2", "timestamp": "2026-10-08T10:01:05Z", "type": "gemini",
         "content": "Replotted with a log y axis."},
    ]
    return "".join(json.dumps(d) + "\n" for d in listRecords).encode()


def _fnPlantRecord(serverHub):
    from vaibify.gui import promptRecordManager
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    sProject = os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)
    filesRepo = ffilesEnsureRepoFiles(sProject)
    baTranscript = _fbaGeminiTranscript()
    dictSanitized = promptRecordManager.fdictSanitizeNewTranscriptLines(
        _StubTranscriptSource(baTranscript), "cid", filesRepo,
        {S_TRANSCRIPT_PATH: {
            "iSizeBytes": len(baTranscript), "listStatKey": [1, 2, 3, 4],
            "sLaunchDirectory": sProject, "sProvider": "gemini"}},
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


@pytest.mark.falsification
def test_a_gemini_rewind_is_folded_and_an_edit_is_marked(
    pageDashboard, serverHub,
):
    """Kills: rendering every turn flat, so a rewound turn reads as part
    of the conversation the researcher is approving.
    """
    _fnPlantRecord(serverHub)
    pageDashboard.route(
        "**/prompt-record/capture*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bPaused": True, "sPausedBy": "lane"}),
        ),
    )
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    pageDashboard.click('.requirement-group-header[data-group="ai"]')
    pageDashboard.click('.requirement-row-header[data-req="promptRecord"]')
    pageDashboard.click(".wf-review-prompt-record")
    elSession = pageDashboard.locator(".prompt-record-session").first
    elSession.wait_for(state="visible", timeout=15000)
    assert "Gemini session" in elSession.text_content()
    sTurns = "#promptRecordViewerTurns"
    pageDashboard.wait_for_selector(
        sTurns + " > .prompt-record-turn-prompt", timeout=15000)

    elRewound = pageDashboard.locator(sTurns + " details.prompt-record-rewound")
    assert elRewound.count() == 1
    assert elRewound.get_attribute("open") is None
    assert "Rewound · 2 turn(s)" in elRewound.locator(
        "summary").text_content()
    assert not elRewound.locator(".prompt-record-turn-text").first.is_visible()

    listVisiblePrompts = [
        el.text_content() for el in pageDashboard.locator(
            sTurns + " > .prompt-record-turn-prompt").all()]
    assert listVisiblePrompts and "use a log y axis" in listVisiblePrompts[0]

    elReply = pageDashboard.locator(
        sTurns + " > .prompt-record-turn-reply").last
    assert "edited" in elReply.locator(
        ".prompt-record-turn-label").text_content()
    elEarlier = elReply.locator("details.prompt-record-earlier-text")
    assert elEarlier.get_attribute("open") is None
    elEarlier.locator("summary").click()
    assert "Draft reply." in elEarlier.text_content()
    assert pageDashboard.listPageErrors == []
