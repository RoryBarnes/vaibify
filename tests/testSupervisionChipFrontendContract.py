"""Frontend contract: the supervision chip is never conditional.

Permanent supervision flags used to render only inside the Prompt
Record block, after an early return taken whenever the record was
disabled. One toggle on ``prompt-record/configure`` therefore removed
the red chip from the dashboard while the flags file still held every
finding — the dashboard showing something that is not true, which is
this repository's hardest rule.

The backend now refuses to disable the record while supervision is on
(``testReplayRoutes.py``); this file pins the render side, so the chip
survives even for a workflow whose record was disabled before that
refusal existed.

JavaScript is not executed by the repository test suite; these are
string-presence + structural assertions in the established
frontend-contract pattern.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsExtractFunctionBlock(sSource, sFunctionName):
    iStart = sSource.find("function " + sFunctionName)
    assert iStart != -1, sFunctionName + " missing from source"
    iNext = sSource.find("\n    function ", iStart + 1)
    return sSource[iStart:iNext if iNext != -1 else len(sSource)]


def _fsPromptRecordBlock():
    return _fsExtractFunctionBlock(
        _fsReadStaticFile("scriptWorkflowRequirements.js"),
        "_fsRenderPromptRecordBlock",
    )


def testDisabledPromptRecordStillRendersTheSupervisionChip():
    """The record-off branch must call the chip renderer too."""
    sBlock = _fsPromptRecordBlock()
    iEarlyReturn = sBlock.find("dictRecord.bEnabled !== true")
    assert iEarlyReturn != -1, (
        "the record-disabled branch is gone; re-point this contract"
    )
    iNextBranch = sBlock.find("var sState", iEarlyReturn)
    assert iNextBranch != -1
    assert "_fsRenderSupervisionChip(dictDetail)" in sBlock[
        iEarlyReturn:iNextBranch
    ], (
        "disabling the Prompt Record must not hide permanent "
        "supervision flags"
    )


def testSupervisionChipRendersEveryTamperSignal():
    """Every honesty signal the poll carries has a rendered chip."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    sChip = _fsExtractFunctionBlock(sSource, "_fsRenderSupervisionChip")
    for sSignal in (
        "bFlagChainIntact",
        "bEventChainIntact",
        "bPersistedFlagCountMatches",
        "bClockSkewSuspected",
        "iFlagCount",
    ):
        assert sSignal in sChip, (
            sSignal + " has no rendered chip; a supervision signal "
            "the backend reports must never be dropped on the floor"
        )


def testSupervisionChipDoesNotConsultPromptRecordState():
    """The chip's verdict depends on supervision evidence alone.

    Reading the Prompt Record's state here would reintroduce the
    coupling from the other direction: a chip that hides itself when
    a neighbouring panel is switched off.
    """
    sChip = _fsExtractFunctionBlock(
        _fsReadStaticFile("scriptWorkflowRequirements.js"),
        "_fsRenderSupervisionChip",
    )
    assert "dictPromptRecord" not in sChip
    assert "bEnabled" not in sChip
