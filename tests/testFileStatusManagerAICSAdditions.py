"""Tests for the AICS-related additions inside fileStatusManager.

Covers ``_fnRefreshEnvelopeIfLevel1`` (the L3-envelope refresh hook
fired on the L1 promotion edge) and the previously-untested branches
inside ``fnMaybeAutoArchive``.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.fileStatusManager import (
    _fnRefreshEnvelopeIfLevel1,
    fnMaybeAutoArchive,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fdictBuildL1ReadyWorkflow():
    """Return a workflow with one all-green step + a project repo."""
    return {
        "sProjectRepoPath": "/repo",
        "bAutoArchive": False,
        "listSteps": [
            {
                "sName": "S0",
                "sDirectory": "S0",
                "saDataFiles": [],
                "saPlotFiles": [],
                "dictVerification": {
                    "sUser": "passed",
                    "sUnitTest": "passed",
                    "sIntegrity": "passed",
                    "sQualitative": "passed",
                    "sQuantitative": "passed",
                },
            },
        ],
        "dictSyncStatus": {},
    }


# ============================================================================
# _fnRefreshEnvelopeIfLevel1 — lines 1326, 1334-1335
# ============================================================================


def test_refresh_envelope_no_op_when_below_l1():
    """Line 1326: a sub-L1 workflow short-circuits without calling archiver."""
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "listSteps": [
            {
                "sName": "S",
                "dictVerification": {"sUser": "untested"},
            },
        ],
    }
    with patch(
        "vaibify.reproducibility.dataArchiver.fnGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert not mockGenerate.called


def test_refresh_envelope_calls_archiver_when_at_l1():
    """An L1-ready workflow triggers a single archiver call with the repo path."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.dataArchiver.fnGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert mockGenerate.called
    args, kwargs = mockGenerate.call_args
    assert args[0] == "/repo"
    assert kwargs.get("sContainerName") == "ctr"


def test_refresh_envelope_swallows_archiver_exception(caplog):
    """Lines 1334-1335: an exception from the archiver is logged and swallowed."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.dataArchiver.fnGenerateReproducibilityEnvelope",
        side_effect=RuntimeError("boom"),
    ):
        # Must not raise.
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")


def test_refresh_envelope_passes_host_binaries():
    """The archiver is called with the workflow's saHostBinaries list."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["saHostBinaries"] = ["/usr/bin/gcc"]
    with patch(
        "vaibify.reproducibility.dataArchiver.fnGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert mockGenerate.called
    _, kwargs = mockGenerate.call_args
    assert kwargs.get("listHostBinaries") == ["/usr/bin/gcc"]


# ============================================================================
# fnMaybeAutoArchive — line 1371 (invalid step index)
# ============================================================================


def test_auto_archive_returns_false_on_invalid_step_index():
    """Line 1371: an iStepIndex outside listSteps returns False."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["bAutoArchive"] = True
    # iAICSLevelBefore=0 → promoted; iStepIndex=999 is out of range.
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "ctr", dictWorkflow, 999, 0,
    ))
    assert bResult is False


def test_auto_archive_negative_step_index_returns_false():
    """A negative iStepIndex also returns False."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["bAutoArchive"] = True
    bResult = _fnRunAsync(fnMaybeAutoArchive(
        MagicMock(), "ctr", dictWorkflow, -1, 0,
    ))
    assert bResult is False


def test_auto_archive_promoted_runs_envelope_refresh():
    """On L1 promotion the envelope-refresh hook fires even with bAutoArchive False."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["bAutoArchive"] = False
    with patch(
        "vaibify.reproducibility.dataArchiver.fnGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRunAsync(fnMaybeAutoArchive(
            MagicMock(), "ctr", dictWorkflow, 0, 0,
        ))
    assert mockGenerate.called
