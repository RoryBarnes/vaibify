"""Tests for the AICS-related additions inside fileStatusManager.

Covers ``_fnRefreshEnvelopeIfLevel1`` (the L3-envelope refresh hook
fired on the L1 promotion edge) and the previously-untested branches
inside ``fbMaybeAutoArchive``.
"""

from unittest.mock import MagicMock, patch

from tests.dockerConnectionDoubles import (
    fconnectionDoubleWithNoContainerPaths,
)

import pytest

from vaibify.gui.fileStatusManager import (
    _fnRefreshEnvelopeIfLevel1,
    fbMaybeAutoArchive,
)


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
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert not mockGenerate.called


def _fsRealRepositoryWithAManifest(tmp_path, sCommitterEmail, sOwnEmail):
    """A git repository whose MANIFEST.sha256 ``sCommitterEmail`` committed.

    A real repository because the refresh now asks git whose manifest
    it is before writing; a path git cannot read is UNDETERMINED and
    the refresh does not write over it.
    """
    import os
    import subprocess
    sRepo = str(tmp_path / "repo")
    os.makedirs(sRepo)
    dictEnvironment = dict(
        os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL=sCommitterEmail,
        GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL=sCommitterEmail,
    )
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileHandle:
        fileHandle.write("# vaibify manifest\n")
    for listArguments in (
        ["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "author"],
        ["config", "user.email", sOwnEmail],
    ):
        subprocess.run(
            ["git", *listArguments], cwd=sRepo, env=dictEnvironment,
            check=True, capture_output=True,
        )
    return sRepo


def test_refresh_envelope_calls_archiver_when_at_l1(tmp_path):
    """An L1-ready workflow whose manifest is its own triggers one archiver call."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["sProjectRepoPath"] = _fsRealRepositoryWithAManifest(
        tmp_path, "author@example.invalid", "author@example.invalid",
    )
    with patch(
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert mockGenerate.called
    args, kwargs = mockGenerate.call_args
    assert args[0] == dictWorkflow["sProjectRepoPath"]
    assert kwargs.get("sContainerName") == "ctr"


@pytest.mark.falsification
def test_refresh_envelope_never_writes_over_a_foreign_manifest(tmp_path):
    """A clone crosses Level 1 when its reader approves the steps; the
    refresh then replaced the AUTHOR's manifest, lock and envelope with
    the reader's own and the manifest check passed against a
    self-comparison (researcher-reported, 2026-09-13).

    Kills: regenerating regardless of whose manifest HEAD tracks.
    """
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["sProjectRepoPath"] = _fsRealRepositoryWithAManifest(
        tmp_path, "author@example.invalid", "reader@example.invalid",
    )
    with patch(
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert not mockGenerate.called


@pytest.mark.falsification
def test_refresh_envelope_does_not_write_when_git_cannot_say_whose(tmp_path):
    """Kills: treating an unanswerable ownership question as the reader's own."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["sProjectRepoPath"] = str(tmp_path / "not-a-repo")
    with patch(
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert not mockGenerate.called


def test_refresh_envelope_swallows_archiver_exception(caplog):
    """Lines 1334-1335: an exception from the archiver is logged and swallowed."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
        side_effect=RuntimeError("boom"),
    ):
        # Must not raise.
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")


def test_refresh_envelope_passes_host_binaries(tmp_path):
    """The archiver is called with the workflow's saHostBinaries list."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["sProjectRepoPath"] = _fsRealRepositoryWithAManifest(
        tmp_path, "author@example.invalid", "author@example.invalid",
    )
    dictWorkflow["saHostBinaries"] = ["/usr/bin/gcc"]
    with patch(
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
    ) as mockGenerate:
        _fnRefreshEnvelopeIfLevel1(dictWorkflow, sContainerId="ctr")
    assert mockGenerate.called
    _, kwargs = mockGenerate.call_args
    assert kwargs.get("listHostBinaries") == ["/usr/bin/gcc"]


# ============================================================================
# fbMaybeAutoArchive — line 1371 (invalid step index)
# ============================================================================


def test_auto_archive_returns_false_on_invalid_step_index():
    """Line 1371: an iStepIndex outside listSteps returns False."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["bAutoArchive"] = True
    # iProofLevelBefore=0 → promoted; iStepIndex=999 is out of range.
    bResult = fbMaybeAutoArchive(
        fconnectionDoubleWithNoContainerPaths(), "ctr", dictWorkflow, 999, 0,
    )
    assert bResult is False


def test_auto_archive_negative_step_index_returns_false():
    """A negative iStepIndex also returns False."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["bAutoArchive"] = True
    bResult = fbMaybeAutoArchive(
        fconnectionDoubleWithNoContainerPaths(), "ctr", dictWorkflow, -1, 0,
    )
    assert bResult is False


def test_auto_archive_promoted_runs_envelope_refresh(tmp_path):
    """On L1 promotion the envelope-refresh hook fires even with bAutoArchive False."""
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    dictWorkflow["sProjectRepoPath"] = _fsRealRepositoryWithAManifest(
        tmp_path, "author@example.invalid", "author@example.invalid",
    )
    dictWorkflow["bAutoArchive"] = False
    # The connection double cannot run git in a container, so the
    # ownership question is answered here: this test pins the DISPATCH
    # (the refresh fires without bAutoArchive), not the ownership rule,
    # which the refresh tests above drive through a real git.
    with patch(
        "vaibify.reproducibility.dataArchiver.fdictGenerateReproducibilityEnvelope",
    ) as mockGenerate, patch(
        "vaibify.reproducibility.gitEvidence.fsManifestOwnershipForRepoFiles",
        return_value="own",
    ):
        fbMaybeAutoArchive(
            fconnectionDoubleWithNoContainerPaths(), "ctr", dictWorkflow, 0, 0,
        )
    assert mockGenerate.called


def test_fiProofLevel_evaluates_L1_once_per_call():
    """Switch-time perf invariant: when L2 and L3 also call into L1
    via their internal short-circuits, the per-step iteration only
    runs once thanks to fcontextLevelComputation.
    """
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel1",
        wraps=levelGates._fbComputeLevel1,
    ) as mockCompute:
        levelGates.fiProofLevel(
            dictWorkflow, "/workspace/repo", bHostProject=False,
        )
    assert mockCompute.call_count == 1


def test_fiProofLevel_evaluates_L2_at_most_once_per_call():
    """Same invariant for L2 — L3 calls L2 internally, but the memo
    ensures the heavy github/zenodo sync-status checks fire only once.
    """
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel2",
        wraps=levelGates._fbComputeLevel2,
    ) as mockCompute:
        levelGates.fiProofLevel(
            dictWorkflow, "/workspace/repo", bHostProject=False,
        )
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
    """Two consecutive fiProofLevel calls re-evaluate L1 cleanly so a
    state mutation between polls is picked up immediately."""
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildL1ReadyWorkflow()
    with patch(
        "vaibify.reproducibility.levelGates._fbComputeLevel1",
        wraps=levelGates._fbComputeLevel1,
    ) as mockCompute:
        levelGates.fiProofLevel(
            dictWorkflow, "/workspace/repo", bHostProject=False,
        )
        levelGates.fiProofLevel(
            dictWorkflow, "/workspace/repo", bHostProject=False,
        )
    assert mockCompute.call_count == 2
