"""The Prompt Record dialog shows what it left out; its poll says it is automatic.

A capture pass records only the agent sessions launched inside the
project's folder, because a container can hold several projects and
capturing every session once published one project's transcripts into
another's public record. The sessions left out are a real gap in the
record, so the dialog must show it -- a researcher who launched the
agent from the workspace root would otherwise read an empty record as
"nothing happened".

The dashboard's own 30-second capture poll marks itself automatic, and
the backend stands it down behind a capture already in flight instead
of queuing a second full pass. Nothing else in the browser lane loads
this dialog or this poller, so without these tests both JavaScript
paths are unexecuted.

The count comes from a REAL index file read by the REAL status route.
Only the capture POST is intercepted: this lane's Docker adapter is
fail-closed, and a capture reaching it would poison the host project's
journal for every later test in the module.
"""

import json
import os
import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

I_SESSIONS_OUTSIDE_PROJECT = 3
I_POLL_FAST_FORWARD_MILLISECONDS = 31000
F_POLL_WAIT_SECONDS = 10.0


def _fnEnableTheRecordWithAGap(serverHub):
    """Enable the record and plant an index that left sessions out."""
    sProjectDirectory = os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)
    sWorkflowPath = os.path.join(
        sProjectDirectory, ".vaibify", "projects",
        S_HOST_WORKFLOW_NAME + ".json",
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    dictWorkflow["dictAiProvenance"] = {
        "dictPromptRecord": {
            "bEnabled": True, "bFirstCaptureReviewed": False,
        },
    }
    with open(sWorkflowPath, "w") as fileWorkflow:
        json.dump(dictWorkflow, fileWorkflow)
    sRecordDirectory = os.path.join(
        sProjectDirectory, ".vaibify", "promptRecord",
    )
    os.makedirs(sRecordDirectory, exist_ok=True)
    with open(os.path.join(sRecordDirectory, "index.json"), "w") as fileIndex:
        json.dump({
            "listCaptures": [],
            "listCoverageIntervals": [],
            "dictSessionBytes": {},
            "dictSessionRawSha256": {},
            "iSessionsOutsideProject": I_SESSIONS_OUTSIDE_PROJECT,
        }, fileIndex)


def _flistInterceptTheCapturePoll(pageDashboard):
    """Answer every capture POST as paused, recording its URL."""
    listCaptureUrls = []

    def fnAnswerPaused(route):
        listCaptureUrls.append(route.request.url)
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bPaused": True, "sPausedBy": "lane"}),
        )

    pageDashboard.route("**/prompt-record/capture*", fnAnswerPaused)
    return listCaptureUrls


@pytest.mark.falsification
def test_the_dialog_names_the_sessions_it_left_out(
    pageDashboard, serverHub,
):
    """The left-out count reaches the researcher, and the poll says automatic.

    Both halves share one page on purpose. Each open of the host
    project waits for the previous test's claim to be released, and the
    poll fires on a 30-second interval: as two tests they cost the
    Firefox lane ninety seconds it did not have. The page clock is
    fast-forwarded to fire the poll instead of waiting for it.

    The poll half is registered through
    ``tests/testPromptRecordPollFrontendContract.py``; a registry entry
    names one test.

    Kills: removing the out-of-project paragraph from the dialog's
    status render.
    """
    _fnEnableTheRecordWithAGap(serverHub)
    listCaptureUrls = _flistInterceptTheCapturePoll(pageDashboard)
    pageDashboard.clock.install()
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    pageDashboard.click('.requirement-group-header[data-group="ai"]')
    pageDashboard.click('.requirement-row-header[data-req="aiModelPrompts"]')
    pageDashboard.click(".wf-open-prompt-record")
    pageDashboard.wait_for_selector(
        "#promptRecordBody :text('started outside this project')",
        timeout=15000,
    )
    sBody = pageDashboard.locator("#promptRecordBody").text_content()
    assert (
        f"{I_SESSIONS_OUTSIDE_PROJECT} agent session(s) were started "
        "outside this project" in sBody
    ), sBody
    assert "change into the project folder" in sBody, sBody
    pageDashboard.clock.fast_forward(I_POLL_FAST_FORWARD_MILLISECONDS)
    fDeadline = time.monotonic() + F_POLL_WAIT_SECONDS
    while not listCaptureUrls and time.monotonic() < fDeadline:
        pageDashboard.wait_for_timeout(250)
    assert listCaptureUrls, (
        "the dashboard never polled the Prompt Record capture while "
        "the record was enabled"
    )
    assert all("bAutomatic=true" in sUrl for sUrl in listCaptureUrls), (
        listCaptureUrls
    )
    assert pageDashboard.listPageErrors == []
