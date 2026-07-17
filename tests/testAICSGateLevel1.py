"""Unit tests for the AICS Level 1 (Self-Consistent) gate.

Phase 1 ships ``fiAICSLevel``, ``fbAtLeastLevel1``, and three orthogonal
per-step predicates lifted out of the old monolithic
``fbIsStepFullyVerified``. This module exercises each predicate
individually and then the composed gate, plus the L0 short-circuit
when ``sProjectRepoPath`` is empty.
"""

from vaibify.reproducibility.levelGates import (
    fbAtLeastLevel1,
    fbAtLeastLevel2,
    fbAtLeastLevel3,
    fbStepIsAtLeastLevel1,
    fbWorkflowHasProjectRepo,
    fiAICSLevel,
)
from vaibify.gui.fileStatusManager import (
    fbStepTestsPassing,
    fbStepTimingClean,
    fbStepUserApproved,
)


def _fdictAllGreenStep():
    """Return a single dict step that satisfies every L1 criterion."""
    return {
        "sName": "A", "sDirectory": "A",
        "bNoInputData": True,
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }


def _fdictAllGreenWorkflow():
    """Return a workflow whose single step satisfies L1."""
    return {"listSteps": [_fdictAllGreenStep()]}


# ------------------------------------------------------------------------
# fbStepUserApproved
# ------------------------------------------------------------------------


def test_fbStepUserApproved_passed_returns_true():
    assert fbStepUserApproved(
        {"dictVerification": {"sUser": "passed"}}
    ) is True


def test_fbStepUserApproved_untested_returns_false():
    assert fbStepUserApproved(
        {"dictVerification": {"sUser": "untested"}}
    ) is False


def test_fbStepUserApproved_failed_returns_false():
    assert fbStepUserApproved(
        {"dictVerification": {"sUser": "failed"}}
    ) is False


def test_fbStepUserApproved_handles_corrupt_step():
    assert fbStepUserApproved(None) is False
    assert fbStepUserApproved({"dictVerification": None}) is False


# ------------------------------------------------------------------------
# fbStepTimingClean
# ------------------------------------------------------------------------


def test_fbStepTimingClean_no_flags_returns_true():
    assert fbStepTimingClean({"dictVerification": {}}) is True


def test_fbStepTimingClean_upstream_modified_blocks():
    dictStep = {"dictVerification": {"bUpstreamModified": True}}
    assert fbStepTimingClean(dictStep) is False


def test_fbStepTimingClean_modified_files_blocks():
    dictStep = {"dictVerification": {
        "listModifiedFiles": ["a.csv"],
    }}
    assert fbStepTimingClean(dictStep) is False


def test_fbStepTimingClean_handles_corrupt_step():
    assert fbStepTimingClean("not a dict") is False


# ------------------------------------------------------------------------
# fbStepTestsPassing
# ------------------------------------------------------------------------


def test_fbStepTestsPassing_all_unnecessary_is_green():
    dictStep = {"dictVerification": {
        "sUnitTest": "unnecessary",
        "sIntegrity": "unnecessary",
        "sQualitative": "unnecessary",
        "sQuantitative": "unnecessary",
    }}
    assert fbStepTestsPassing(dictStep) is True


def test_fbStepTestsPassing_passed_from_marker_is_green():
    dictStep = {"dictVerification": {
        "sUnitTest": "passed-from-marker",
    }}
    assert fbStepTestsPassing(dictStep) is True


def test_fbStepTestsPassing_failed_blocks():
    dictStep = {"dictVerification": {"sUnitTest": "failed"}}
    assert fbStepTestsPassing(dictStep) is False


def test_fbStepTestsPassing_untested_blocks():
    dictStep = {"dictVerification": {"sIntegrity": "untested"}}
    assert fbStepTestsPassing(dictStep) is False


# ------------------------------------------------------------------------
# fbWorkflowHasProjectRepo
# ------------------------------------------------------------------------


def test_fbWorkflowHasProjectRepo_empty_returns_false():
    assert fbWorkflowHasProjectRepo("") is False


def test_fbWorkflowHasProjectRepo_none_returns_false():
    assert fbWorkflowHasProjectRepo(None) is False


def test_fbWorkflowHasProjectRepo_path_returns_true():
    assert fbWorkflowHasProjectRepo("/workspace/repo") is True


# ------------------------------------------------------------------------
# fbStepIsAtLeastLevel1 composition
# ------------------------------------------------------------------------


def test_fbStepIsAtLeastLevel1_all_three_predicates_green():
    assert fbStepIsAtLeastLevel1(_fdictAllGreenStep()) is True


def test_fbStepIsAtLeastLevel1_user_block_fails():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "untested"
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_timing_block_fails():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["bUpstreamModified"] = True
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_tests_block_fails():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    assert fbStepIsAtLeastLevel1(dictStep) is False


# ------------------------------------------------------------------------
# fbAtLeastLevel1
# ------------------------------------------------------------------------


def test_fbAtLeastLevel1_all_green_returns_true():
    assert fbAtLeastLevel1(_fdictAllGreenWorkflow(), "/repo") is True


def test_fbAtLeastLevel1_empty_repo_returns_false():
    assert fbAtLeastLevel1(_fdictAllGreenWorkflow(), "") is False


def test_fbAtLeastLevel1_no_steps_returns_false():
    assert fbAtLeastLevel1({"listSteps": []}, "/repo") is False


def test_fbAtLeastLevel1_one_step_blocks_workflow():
    dictWorkflow = _fdictAllGreenWorkflow()
    dictWorkflow["listSteps"].append({
        "sName": "B", "sDirectory": "B",
        "dictVerification": {"sUser": "untested"},
    })
    assert fbAtLeastLevel1(dictWorkflow, "/repo") is False


# ------------------------------------------------------------------------
# fiAICSLevel — short-circuit ladder
# ------------------------------------------------------------------------


def test_fiAICSLevel_returns_zero_when_repo_missing():
    assert fiAICSLevel(_fdictAllGreenWorkflow(), "") == 0


def test_fiAICSLevel_returns_one_when_at_L1_only():
    """L2 and L3 are stub-False in Phase 1 → L1 ceiling."""
    assert fiAICSLevel(_fdictAllGreenWorkflow(), "/repo") == 1


def test_fiAICSLevel_returns_zero_when_step_blocks():
    dictWorkflow = _fdictAllGreenWorkflow()
    dictWorkflow["listSteps"][0]["dictVerification"]["sUser"] = "failed"
    assert fiAICSLevel(dictWorkflow, "/repo") == 0


def test_fbAtLeastLevel2_returns_false_without_sync_state():
    """Phase 2 contract: an L1 workflow without GitHub/Zenodo sync
    state and without an AI Declaration step is L1 only, not L2."""
    assert fbAtLeastLevel2(_fdictAllGreenWorkflow(), "/repo") is False


def test_fbAtLeastLevel3_stub_returns_false_until_phase_three():
    assert fbAtLeastLevel3(_fdictAllGreenWorkflow(), "/repo") is False
