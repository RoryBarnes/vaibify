"""Tests for the Auto Archive helpers in fileStatusManager."""

import asyncio
from unittest.mock import MagicMock, patch

from vaibify.gui.fileStatusManager import (
    fbIsStepFullyVerified,
    flistStepRemoteFiles,
    fnMaybeAutoArchive,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


# ---------------------------------------------------------------------------
# fbIsStepFullyVerified
# ---------------------------------------------------------------------------


def test_fbIsStepFullyVerified_user_not_passed_returns_false():
    dictStep = {"dictVerification": {"sUser": "untested"}}
    assert fbIsStepFullyVerified(dictStep) is False


def test_fbIsStepFullyVerified_user_failed_returns_false():
    dictStep = {"dictVerification": {"sUser": "failed"}}
    assert fbIsStepFullyVerified(dictStep) is False


def test_fbIsStepFullyVerified_user_passed_no_tests_returns_true():
    dictStep = {"dictVerification": {"sUser": "passed"}}
    assert fbIsStepFullyVerified(dictStep) is True


def test_fbIsStepFullyVerified_all_tests_passed_returns_true():
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed",
        "sIntegrity": "passed",
        "sQualitative": "passed",
        "sQuantitative": "passed",
    }}
    assert fbIsStepFullyVerified(dictStep) is True


def test_fbIsStepFullyVerified_one_test_untested_returns_false():
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "passed",
        "sIntegrity": "untested",
    }}
    assert fbIsStepFullyVerified(dictStep) is False


def test_fbIsStepFullyVerified_one_test_failed_returns_false():
    dictStep = {"dictVerification": {
        "sUser": "passed",
        "sUnitTest": "failed",
    }}
    assert fbIsStepFullyVerified(dictStep) is False


def test_fbIsStepFullyVerified_no_dictVerification_returns_false():
    dictStep = {}
    assert fbIsStepFullyVerified(dictStep) is False


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
                "saDataFiles": ["data.out"],
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
# fnMaybeAutoArchive
# ---------------------------------------------------------------------------


def test_fnMaybeAutoArchive_noop_when_setting_off():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = False
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "cid", dictWorkflow, 0, False,
    ))
    assert bResult is False


def test_fnMaybeAutoArchive_noop_when_already_verified():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "passed"}
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "cid", dictWorkflow, 0,
        bWasFullyVerifiedBefore=True,
    ))
    assert bResult is False


def test_fnMaybeAutoArchive_noop_when_step_not_now_verified():
    dictWorkflow = _fdictBuildWorkflow()
    dictWorkflow["bAutoArchive"] = True
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": "untested"}
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "cid", dictWorkflow, 0, False,
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
            MagicMock(), "cid", dictWorkflow, 0, False,
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
            MagicMock(), "cid", dictWorkflow, 0, False,
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
            MagicMock(), "cid", dictWorkflow, 0, False,
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
            MagicMock(), "cid", dictWorkflow, 0, False,
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
            MagicMock(), "cid", dictWorkflow, 0, False,
        ))
    assert bResult is False
    assert not mockPush.called
