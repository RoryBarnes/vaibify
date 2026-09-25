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


def _fsSupervisionFunction(sFunctionName):
    return _fsExtractFunctionBlock(
        _fsReadStaticFile("scriptWorkflowRequirements.js"), sFunctionName,
    )


def testThePermanentFlagsRenderWhateverTheSwitchesSay():
    """Supervised mode's row renders its chip with no early return.

    The flags used to live inside the Prompt Record block, after an
    early return taken whenever the record was off. They now have their
    own row, whose detail has one return, and the chip is in it.
    Re-pointed 2026-09-25 when the AI block became rows.
    """
    sDetail = _fsSupervisionFunction("_fsRenderSupervisionDetail")
    # Two returns: the flag list's per-flag callback, and the one that
    # builds the detail. A third is an early exit -- how the chip was
    # hidden before -- whether or not it sits inside an ``if``.
    assert sDetail.count("return ") == 2, (
        "Supervised mode's detail gained an early return; that is how "
        "the chip was hidden before"
    )
    sFinal = sDetail[sDetail.rindex("return "):]
    assert "_fsRenderSupervisionChip(dictDetail)" in sFinal, (
        "disabling a switch must not hide permanent supervision flags"
    )


def testTheRowWarningNamesFlagsEvenWithSupervisionOff():
    """The collapsed row's ⚠ counts flags before asking if it is on.

    A row whose ⚠ answered "" whenever supervision was off hid the
    flags a supervised period left behind from the collapsed block.
    """
    sWarning = _fsSupervisionFunction("_fsSupervisionWarning")
    iFlags = sWarning.index("iFlagCount")
    iEnabled = sWarning.find("bEnabled")
    assert iEnabled == -1 or iFlags < iEnabled, (
        "the ⚠ must name permanent flags before it consults the switch"
    )
    assert 'bEnabled !== true) return ""' not in sWarning


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
