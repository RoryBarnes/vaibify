"""Tier 5 must refuse a rerun whose steps cannot all execute.

The unattended runner silently skips two classes of step: interactive
steps (its interactive handler returns success immediately when no
researcher is attached) and steps disabled in the dashboard. A skipped
step leaves its manifest-pinned outputs untouched, so every hash
trivially matches and the attestation would certify a "byte-identical
rerun" that executed nothing — the exact false-pass class the rerun
lane exists to prevent. The degenerate case is a workflow with no steps
at all, where 0-of-0 execution still exits zero.

The honest verdict is a refusal *before any step executes*, naming each
unexecutable step, recorded with ``bRerunAttempted`` False so reporters
never describe the exit status of a run that did not happen. These
tests drive :func:`fdictRerunAndVerifyWorkflow` with the runner patched
to fail loudly if invoked, so a regression that starts the rerun anyway
cannot pass them. A sibling class — a readable manifest that pins no
files — fails closed the same way: zero of zero is a vacuous match,
not a reproduction.
"""

import hashlib
from unittest.mock import patch

import pytest

from vaibify.reproducibility.rerunVerification import (
    S_DIVERGENCE_MANIFEST_EMPTY,
    fdictRerunAndVerifyWorkflow,
    fdictVerifyRerunOutputs,
    flistNameUnexecutableSteps,
)


S_RUNNER_PATCH_TARGET = (
    "vaibify.reproducibility.rerunVerification.fbRunWorkflowInContainer"
)


def _fdictAutomatedStep(sName, bRunEnabled=True):
    return {
        "sName": sName,
        "bRunEnabled": bRunEnabled,
        "saCommands": ["true"],
    }


def _fdictInteractiveStep(sName):
    return {"sName": sName, "bInteractive": True}


def _fnFailIfRerunStarts(*taArguments, **dictArguments):
    raise AssertionError(
        "the rerun must be refused before any step executes"
    )


@pytest.fixture
def fixturePinnedRepo(tmp_path):
    """A repo whose manifest pins one file whose bytes are unchanged."""
    pathOutput = tmp_path / "result.txt"
    pathOutput.write_text("answer = 42\n")
    (tmp_path / "MANIFEST.sha256").write_text(
        f"{hashlib.sha256(pathOutput.read_bytes()).hexdigest()}"
        f"  result.txt\n"
    )
    return tmp_path


@pytest.mark.falsification
def test_interactive_step_refuses_rerun_before_any_execution(
    fixturePinnedRepo,
):
    """An interactive step means no unattended rerun can be attested.

    Kills: In flistNameUnexecutableSteps, return [] instead of
    listReasons — the scanner reports every workflow executable, the
    refusal never fires, and the patched runner raises.
    """
    dictWorkflow = {"listSteps": [
        _fdictAutomatedStep("GenerateSamples"),
        _fdictInteractiveStep("InspectChains"),
    ]}
    with patch(S_RUNNER_PATCH_TARGET, side_effect=_fnFailIfRerunStarts):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow, "/workspace/repo/wf.json",
            str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["iOutputHashesTotal"] == 0
    assert dictOutcome["listDivergedHashes"] == [
        "step 'InspectChains' is interactive and cannot execute "
        "unattended"
    ]
    assert dictOutcome["sManifestDigest"], (
        "the refusal must still name the manifest it refused against"
    )


def test_disabled_step_refuses_rerun_before_any_execution(
    fixturePinnedRepo,
):
    """A dashboard-disabled step would be skipped, so the rerun refuses."""
    dictWorkflow = {"listSteps": [
        _fdictAutomatedStep("GenerateSamples"),
        _fdictAutomatedStep("PlotHistogram", bRunEnabled=False),
    ]}
    with patch(S_RUNNER_PATCH_TARGET, side_effect=_fnFailIfRerunStarts):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow, "/workspace/repo/wf.json",
            str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["listDivergedHashes"] == [
        "step 'PlotHistogram' is disabled and would not execute"
    ]


def test_workflow_with_no_steps_refuses_rerun(fixturePinnedRepo):
    """Zero steps executing zero commands is not a reproduction."""
    with patch(S_RUNNER_PATCH_TARGET, side_effect=_fnFailIfRerunStarts):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", {"listSteps": []},
            "/workspace/repo/wf.json", str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["listDivergedHashes"] == [
        "workflow contains no steps to execute"
    ]


def test_fully_executable_workflow_still_attempts_and_passes(
    fixturePinnedRepo,
):
    """The refusal is not a veto: enabled automated steps rerun and pass."""
    dictWorkflow = {"listSteps": [
        _fdictAutomatedStep("GenerateSamples"),
        _fdictAutomatedStep("PlotHistogram"),
    ]}
    with patch(S_RUNNER_PATCH_TARGET, return_value=True):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow, "/workspace/repo/wf.json",
            str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is True
    assert dictOutcome["iOutputHashesMatched"] == 1
    assert dictOutcome["iOutputHashesTotal"] == 1


def test_scanner_reports_every_unexecutable_step_not_just_the_first():
    """The researcher sees the full repair list in one refusal."""
    listReasons = flistNameUnexecutableSteps({"listSteps": [
        _fdictInteractiveStep("InspectChains"),
        _fdictAutomatedStep("PlotHistogram", bRunEnabled=False),
        _fdictAutomatedStep("GenerateSamples"),
    ]})
    assert len(listReasons) == 2
    assert "InspectChains" in listReasons[0]
    assert "PlotHistogram" in listReasons[1]


@pytest.mark.falsification
def test_manifest_pinning_no_files_fails_closed(tmp_path):
    """A readable manifest with zero entries must never attest a pass.

    Kills: In fdictVerifyRerunOutputs, guard the empty-manifest branch
    with "if False and not listEntries" — zero pinned files falls
    through to the vacuous 0-of-0 pass.
    """
    (tmp_path / "MANIFEST.sha256").write_text("")
    dictOutcome = fdictVerifyRerunOutputs(str(tmp_path), True)
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["iOutputHashesTotal"] == 0
    assert S_DIVERGENCE_MANIFEST_EMPTY in dictOutcome[
        "listDivergedHashes"
    ]
