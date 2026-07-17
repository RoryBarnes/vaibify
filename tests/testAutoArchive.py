"""Tests for the Auto Archive helpers in fileStatusManager."""

import asyncio
from unittest.mock import MagicMock, patch

from vaibify.gui.fileStatusManager import (
    flistStepRemoteFiles,
    fnMaybeAutoArchive,
)
from vaibify.reproducibility.levelGates import fbStepIsAtLeastLevel1


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


# ---------------------------------------------------------------------------
# fbStepIsAtLeastLevel1 (was fbIsStepFullyVerified; the predicate is now
# composed in vaibify.reproducibility.levelGates from three orthogonal
# per-step predicates in fileStatusManager). Names changed; semantics
# unchanged: user attested + every defined test green + timing clean.
# ---------------------------------------------------------------------------


def test_fbStepIsAtLeastLevel1_user_not_passed_returns_false():
    dictStep = {"dictVerification": {"sUser": "untested"}}
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_user_failed_returns_false():
    dictStep = {"dictVerification": {"sUser": "failed"}}
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_user_passed_no_tests_returns_true():
    dictStep = {"bNoInputData": True,
                "dictVerification": {"sUser": "passed"}}
    assert fbStepIsAtLeastLevel1(dictStep) is True


def test_fbStepIsAtLeastLevel1_all_tests_passed_returns_true():
    dictStep = {"bNoInputData": True,
                "dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed",
        "sIntegrity": "passed",
        "sQualitative": "passed",
        "sQuantitative": "passed",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is True


def test_fbStepIsAtLeastLevel1_one_test_untested_returns_false():
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed",
        "sIntegrity": "untested",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_one_test_failed_returns_false():
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "failed",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_no_dictVerification_returns_false():
    dictStep = {}
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbStepIsAtLeastLevel1_unnecessary_counts_as_green():
    dictStep = {"bNoInputData": True,
                "dictVerification": {
        "sUser": "passed",
        "sUnitTest": "unnecessary",
        "sIntegrity": "unnecessary",
        "sQualitative": "unnecessary",
        "sQuantitative": "unnecessary",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is True


def test_fbStepIsAtLeastLevel1_mixed_passed_and_unnecessary_is_green():
    dictStep = {"bNoInputData": True,
                "dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed",
        "sIntegrity": "passed",
        "sQualitative": "unnecessary",
        "sQuantitative": "passed",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is True


def test_fbStepIsAtLeastLevel1_undeclared_input_blocks_despite_all_green():
    """An all-green step with no input declaration is NOT L1 — the
    per-step predicate must agree with the blocker and the cell, or
    the step banner shows a check while the input contract is unmet."""
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed",
        "sIntegrity": "passed",
        "sQualitative": "passed",
        "sQuantitative": "passed",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is False
    dictStep["saInputDataFiles"] = ["data/raw.csv"]
    assert fbStepIsAtLeastLevel1(dictStep) is True


def test_fbStepIsAtLeastLevel1_untested_still_blocks_with_unnecessary():
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "untested",
        "sIntegrity": "unnecessary",
        "sQualitative": "untested",
        "sQuantitative": "unnecessary",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is False


def test_fbAtLeastLevel1_all_unnecessary_steps_are_green():
    """L1 gate composes per-step predicates across the whole workflow."""
    from vaibify.reproducibility.levelGates import fbAtLeastLevel1
    dictV = {
        "sUser": "passed",
        "sUnitTest": "unnecessary",
        "sIntegrity": "unnecessary",
        "sQualitative": "unnecessary",
        "sQuantitative": "unnecessary",
    }
    dictWorkflow = {"listSteps": [
        {"bNoInputData": True, "dictVerification": dict(dictV)},
        {"bNoInputData": True, "dictVerification": dict(dictV)},
    ]}
    assert fbAtLeastLevel1(dictWorkflow, "/repo") is True


def test_fbStepIsAtLeastLevel1_passed_from_marker_counts_as_green():
    """``sUnitTest=passed-from-marker`` is the bootstrap-from-disk
    equivalent of ``passed`` (see stateManager._fdictVerificationFromMarker).
    The gate must treat both equivalently or workflows whose state was
    rebuilt from markers on a fresh checkout never reach L1.
    """
    dictStep = {"bNoInputData": True,
                "dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed-from-marker",
        "sIntegrity": "passed",
        "sQualitative": "passed",
        "sQuantitative": "passed",
    }}
    assert fbStepIsAtLeastLevel1(dictStep) is True


# ---------------------------------------------------------------------------
# flistStepRemoteFiles
# ---------------------------------------------------------------------------


def _fdictBuildWorkflow():
    return {
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [
            {
                "sDirectory": "/workspace/repo/stepA",
                "saPlotFiles": ["Plot/figure.pdf"],
                "saOutputDataFiles": ["data.out"],
                "bNoInputData": True,
            },
        ],
        "dictSyncStatus": {
            "stepA/Plot/figure.pdf": {
                "bOverleaf": True, "bZenodo": False,
            },
            "stepA/data.out": {
                "bOverleaf": False, "bZenodo": True,
            },
        },
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
    }


def test_flistStepRemoteFiles_returns_overleaf_files():
    dictWorkflow = _fdictBuildWorkflow()
    listFiles = flistStepRemoteFiles(dictWorkflow, 0, "Overleaf")
    assert listFiles == ["stepA/Plot/figure.pdf"]


def test_flistStepRemoteFiles_returns_zenodo_files():
    dictWorkflow = _fdictBuildWorkflow()
    listFiles = flistStepRemoteFiles(dictWorkflow, 0, "Zenodo")
    assert listFiles == ["stepA/data.out"]


def test_flistStepRemoteFiles_invalid_index_returns_empty():
    dictWorkflow = _fdictBuildWorkflow()
    assert flistStepRemoteFiles(dictWorkflow, 99, "Overleaf") == []


def test_flistStepRemoteFiles_no_sync_status_returns_empty():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["dictSyncStatus"] = {}
    listFiles = flistStepRemoteFiles(dictWorkflow, 0, "Overleaf")
    assert listFiles == []


# ---------------------------------------------------------------------------
# fnMaybeAutoArchive — final positional arg renamed from
# ``bWasFullyVerifiedBefore`` (boolean) to ``iAICSLevelBefore``
# (integer 0..3). Promotion is now defined as
# ``iAICSLevelBefore < 1 <= fiAICSLevel(...)``.
# ---------------------------------------------------------------------------


def test_fnMaybeAutoArchive_noop_when_setting_off():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = False
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "cid", dictWorkflow, 0, 0,
    ))
    assert bResult is False


def test_fnMaybeAutoArchive_noop_when_already_verified():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "cid", dictWorkflow, 0,
        iAICSLevelBefore=1,
    ))
    assert bResult is False


def test_fnMaybeAutoArchive_noop_when_step_not_now_verified():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "untested"}
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "cid", dictWorkflow, 0, 0,
    ))
    assert bResult is False


def test_fnMaybeAutoArchive_pushes_overleaf_on_transition():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["sOverleafProjectId"] = "abc123"
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToOverleaf",
        return_value=(0, "ok"),
    ) as mockPush:
        bResult = _fnRunAsync(fnMaybeAutoArchive(
            MagicMock(), "cid", dictWorkflow, 0, 0,
        ))
    assert bResult is True
    assert mockPush.called
    assert mockPush.call_args[0][3] == "abc123"


def test_fnMaybeAutoArchive_pushes_zenodo_on_transition():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["sZenodoService"] = "sandbox"
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    with patch(
        "vaibify.gui.syncDispatcher.ftResultArchiveToZenodo",
        return_value=(0, "ok"),
    ) as mockArchive:
        bResult = _fnRunAsync(fnMaybeAutoArchive(
            MagicMock(), "cid", dictWorkflow, 0, 0,
        ))
    assert bResult is True
    assert mockArchive.called


def test_fnMaybeAutoArchive_pushes_both_remotes():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["sOverleafProjectId"] = "abc123"
    dictWorkflow["sZenodoService"] = "sandbox"
    dictWorkflow["dictSyncStatus"]["stepA/Plot/figure.pdf"] = {
        "bOverleaf": True, "bZenodo": True,
    }
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToOverleaf",
        return_value=(0, "ok"),
    ) as mockOverleaf, patch(
        "vaibify.gui.syncDispatcher.ftResultArchiveToZenodo",
        return_value=(0, "ok"),
    ) as mockZenodo:
        bResult = _fnRunAsync(fnMaybeAutoArchive(
            MagicMock(), "cid", dictWorkflow, 0, 0,
        ))
    assert bResult is True
    assert mockOverleaf.called
    assert mockZenodo.called


def test_fnMaybeAutoArchive_swallows_overleaf_failure():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["sOverleafProjectId"] = "abc123"
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToOverleaf",
        side_effect=RuntimeError("network down"),
    ):
        bResult = _fnRunAsync(fnMaybeAutoArchive(
            MagicMock(), "cid", dictWorkflow, 0, 0,
        ))
    assert bResult is False


def test_fnMaybeAutoArchive_no_remotes_configured_returns_false():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["dictSyncStatus"] = {}
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToOverleaf",
    ) as mockPush:
        bResult = _fnRunAsync(fnMaybeAutoArchive(
            MagicMock(), "cid", dictWorkflow, 0, 0,
        ))
    assert bResult is False
    assert not mockPush.called
