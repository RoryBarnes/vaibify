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
                "saOutputDataFiles": [],
                "saPlotFiles": [],
                "bNoInputData": True,
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


def test_fiAICSLevel_evaluates_L1_once_per_call():
    """Switch-time perf invariant: when L2 and L3 also call into L1
    via their internal short-circuits, the per-step iteration only
    runs once thanks to fnLevelComputationContext.
    """
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel1",
        wraps=levelGates._fbComputeLevel1,
    ) as mockCompute:
        levelGates.fiAICSLevel(dictWorkflow, "/workspace/repo")
    assert mockCompute.call_count == 1


def test_fiAICSLevel_evaluates_L2_at_most_once_per_call():
    """Same invariant for L2 — L3 calls L2 internally, but the memo
    ensures the heavy github/zenodo sync-status checks fire only once.
    """
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel2",
        wraps=levelGates._fbComputeLevel2,
    ) as mockCompute:
        levelGates.fiAICSLevel(dictWorkflow, "/workspace/repo")
    assert mockCompute.call_count <= 1


def test_fbAtLeastLevel1_uncached_outside_context():
    """Single-call sites (envelope-refresh hook, tests) do not get
    a stale-cache surprise — outside the context manager the gate
    falls through to the original uncached body every time."""
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel1",
        wraps=levelGates._fbComputeLevel1,
    ) as mockCompute:
        levelGates.fbAtLeastLevel1(dictWorkflow, "/workspace/repo")
        levelGates.fbAtLeastLevel1(dictWorkflow, "/workspace/repo")
    assert mockCompute.call_count == 2


def test_aics_memo_does_not_leak_across_invocations():
    """Two consecutive fiAICSLevel calls re-evaluate L1 cleanly so a
    state mutation between polls is picked up immediately."""
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel1",
        wraps=levelGates._fbComputeLevel1,
    ) as mockCompute:
        levelGates.fiAICSLevel(dictWorkflow, "/workspace/repo")
        levelGates.fiAICSLevel(dictWorkflow, "/workspace/repo")
    assert mockCompute.call_count == 2
