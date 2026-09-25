"""The AI block's optional rows are shown, never counted.

The block has two Level 2 declarations (AI models, Personal AI
Configuration) and three optional records (Project instructions, the
Prompt Record, Supervised mode). An optional row must never move the
block's Level 2 cell: a level cell and the rows beneath it fail on the
same set. It says what needs the researcher through the ⚠ beside its
title instead, and that ⚠ is echoed on the collapsed heading, because a
"first capture awaiting your review" hidden under a closed heading was
the complaint that started this redesign.

The project is set up so counting would show: both Level 2 rows unmet
(nothing declared, the personal question unanswered) while an optional
row is met (the project instructions file exists). The block's L2 cell
must read "none"; counting the optional row would lift it to "partial".
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


def _fsProjectDirectory(serverHub):
    return os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)


def _fnPlantAnUnreviewedRecordAndInstructions(serverHub):
    """Record on with one capture unreviewed; instructions file present."""
    sProject = _fsProjectDirectory(serverHub)
    sWorkflowPath = os.path.join(
        sProject, ".vaibify", "projects", S_HOST_WORKFLOW_NAME + ".json",
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
    sRecord = os.path.join(sProject, ".vaibify", "promptRecord")
    os.makedirs(sRecord, exist_ok=True)
    with open(os.path.join(sRecord, "index.json"), "w") as fileIndex:
        json.dump({
            "listCaptures": [{
                "sSessionFileName": "session.jsonl",
                "sCaptureKind": "whole", "iBytesCaptured": 10,
                "sSha256": "0" * 64, "sPreviousRecordSha256": "",
                "sCapturedAtUtc": "2026-01-01T00:00:00+00:00",
                "iRedactionCount": 2, "dictRedactionsByCategory": {},
            }],
            "listCoverageIntervals": [],
            "dictSessionBytes": {}, "dictSessionRawSha256": {},
            "iSessionsOutsideProject": 0,
        }, fileIndex)
    with open(os.path.join(sProject, ".vaibify", "AGENTS.md"), "w") as f:
        f.write("# Standing instructions\n")


def _fsLevelTwoCellClass(pageDashboard, sHeaderSelector):
    return pageDashboard.locator(
        sHeaderSelector + " .step-level-cell",
    ).nth(1).get_attribute("class")


@pytest.mark.falsification
def test_an_optional_row_never_lifts_the_level_cell(pageDashboard, serverHub):
    """Kills: counting optional rows in the group's level summary."""
    _fnPlantAnUnreviewedRecordAndInstructions(serverHub)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    sAiHeader = '.requirement-group-header[data-group="ai"]'
    pageDashboard.wait_for_selector(
        sAiHeader + " .requirement-group-warning", timeout=15000,
    )
    pageDashboard.click(sAiHeader)
    pageDashboard.wait_for_selector(
        '.requirement-row-header[data-req="promptRecord"]', timeout=10000,
    )
    sInstructionsCell = _fsLevelTwoCellClass(
        pageDashboard, '.requirement-row-header[data-req="projectInstructions"]',
    )
    assert "level-cell-attained" in sInstructionsCell, sInstructionsCell
    sGroupCell = _fsLevelTwoCellClass(pageDashboard, sAiHeader)
    assert "level-cell-none" in sGroupCell, (
        "both Level 2 rows are unmet, so the block's L2 cell must read "
        "none; an optional row lifted it: " + sGroupCell
    )
    sRecordRow = '.requirement-row-header[data-req="promptRecord"]'
    assert "level-cell-not-applicable" in _fsLevelTwoCellClass(
        pageDashboard, sRecordRow,
    )
    sWarning = pageDashboard.locator(
        sRecordRow + " .requirement-row-warning",
    ).get_attribute("title")
    assert "awaiting your review" in sWarning, sWarning
    pageDashboard.click(sRecordRow)
    elReview = pageDashboard.locator(".wf-review-prompt-record")
    elReview.wait_for(timeout=10000)
    assert "wf-action-caution" in elReview.get_attribute("class")
    assert pageDashboard.listPageErrors == []
