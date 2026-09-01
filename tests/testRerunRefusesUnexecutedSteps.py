"""Tier 5 grades what it executed, and refuses when it executed nothing.

The unattended runner silently skips two classes of step, and they are
not the same thing.

A step DISABLED in the dashboard is a switch. Its pinned outputs sit
untouched, every hash trivially matches, and an attestation would
certify a "byte-identical rerun" that executed nothing — so a workflow
containing one is refused before any step runs. The degenerate case,
a workflow with no steps at all, refuses for the same reason: 0-of-0
execution exits zero and proves nothing.

An INTERACTIVE step is a declaration about the workflow: this part is
done by a human. Refusing those made Level 3 unreachable for every
project vaibify itself builds, because vaibify creates an interactive
AI Declaration step and Level 2 requires it (researcher-reported,
2026-08-31). Such a step's outputs are data a human produced, which
the steps below it consume as input; the shadow's repository copy
carries them verbatim, so the executable steps run against exactly the
bytes the original run used. They are dropped from the comparison and
named in ``listCarriedPaths``, because they were given, not
re-derived.

The exclusion is the dangerous half, and these tests are built around
its two failure modes: an exclusion set that silently matches nothing
(so given files are graded as reproduced), and an exclusion so broad
that nothing is left to compare (a vacuous 0-of-0 pass).
"""

import hashlib
from unittest.mock import patch

import pytest

from vaibify.reproducibility.rerunVerification import (
    S_DIVERGENCE_EVERY_ENTRY_GIVEN,
    S_DIVERGENCE_MANIFEST_EMPTY,
    S_DIVERGENCE_ROOT_MISMATCH,
    fsResolveRunnerRepoRoot,
    fdictRerunAndVerifyWorkflow,
    fdictVerifyRerunOutputs,
    flistCarriedOutputRepoPaths,
    flistNameStepsThatBlockARerun,
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


def _fsWorkflowPathIn(pathRepo):
    """Return where the workflow file sits inside a repository.

    A fixture cannot name an arbitrary workflow path any more, and
    should never have: the rerun refuses when the runner's repo root
    and the comparison's root differ, because steps would then write
    where the comparison never looks and every pinned artefact would
    match untouched. A fixture pairing an unrelated workflow path with
    a repo is exactly that false pass.
    """
    return str(pathRepo) + "/.vaibify/projects/project.json"


def _fnFailIfRerunStarts(*taArguments, **dictArguments):
    raise AssertionError(
        "the rerun must be refused before any step executes"
    )


def _fnWriteManifest(pathRepo, dictPathToBytes):
    """Pin each path at the hash of the bytes given, not of the file.

    Passing bytes that differ from what is on disk is how these tests
    make a path DIVERGE — which is the only way to tell an excluded
    entry from an included one that happened to match.
    """
    sLines = "".join(
        f"{hashlib.sha256(baBytes).hexdigest()}  {sPath}\n"
        for sPath, baBytes in sorted(dictPathToBytes.items())
    )
    (pathRepo / "MANIFEST.sha256").write_text(sLines)


@pytest.fixture
def fixturePinnedRepo(tmp_path):
    """A repo whose manifest pins one file whose bytes are unchanged."""
    pathOutput = tmp_path / "result.txt"
    pathOutput.write_text("answer = 42\n")
    _fnWriteManifest(tmp_path, {"result.txt": pathOutput.read_bytes()})
    return tmp_path


@pytest.fixture
def fixtureRepoWithAGivenFile(tmp_path):
    """A repo pinning a human's file that has since CHANGED, plus one that has not.

    ``Chains/samples.json`` is pinned at bytes it no longer holds. Any
    comparison that includes it fails; any comparison that correctly
    treats it as given passes on ``Figures/plot.txt`` alone. That gap
    is what makes an exclusion that matches nothing visible.
    """
    (tmp_path / "Chains").mkdir()
    (tmp_path / "Figures").mkdir()
    pathGiven = tmp_path / "Chains" / "samples.json"
    pathGiven.write_text('{"drawn": "by hand, later edited"}\n')
    pathDerived = tmp_path / "Figures" / "plot.txt"
    pathDerived.write_text("computed\n")
    _fnWriteManifest(tmp_path, {
        "Chains/samples.json": b'{"drawn": "by hand"}\n',
        "Figures/plot.txt": pathDerived.read_bytes(),
    })
    return tmp_path


def _fdictWorkflowWithAGivenStep():
    """An interactive step feeding an automated one, both declaring output."""
    return {"listSteps": [
        {
            "sName": "Collect Chains",
            "sDirectory": "Chains",
            "bInteractive": True,
            "saOutputDataFiles": ["samples.json"],
        },
        {
            "sName": "Plot",
            "sDirectory": "Figures",
            "bRunEnabled": True,
            "saOutputDataFiles": ["plot.txt"],
            "saCommands": ["true"],
        },
    ]}


@pytest.mark.falsification
def test_a_given_steps_output_is_carried_out_of_the_comparison(
    fixtureRepoWithAGivenFile,
):
    """A human step's output is not graded, and the rest of the run is.

    Kills: In flistCarriedOutputRepoPaths, return [] instead of
    sorted(setGiven - setExecuted) — the exclusion set matches nothing,
    the human's changed file is compared as though the rerun had
    produced it, and the attestation reports 1 of 2 with a divergence.
    """
    with patch(S_RUNNER_PATCH_TARGET, return_value=True):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", _fdictWorkflowWithAGivenStep(),
            _fsWorkflowPathIn(fixtureRepoWithAGivenFile), str(fixtureRepoWithAGivenFile),
        )
    assert dictOutcome["listCarriedPaths"] == ["Chains/samples.json"]
    assert dictOutcome["iOutputHashesTotal"] == 1, (
        "only the executed step's output is graded"
    )
    assert dictOutcome["listDivergedHashes"] == []
    assert dictOutcome["bPassed"] is True


def test_a_given_step_does_not_refuse_the_rerun(fixtureRepoWithAGivenFile):
    """The rerun is attempted, which is the whole point of the carve-out."""
    with patch(S_RUNNER_PATCH_TARGET, return_value=True) as mockRunner:
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", _fdictWorkflowWithAGivenStep(),
            _fsWorkflowPathIn(fixtureRepoWithAGivenFile), str(fixtureRepoWithAGivenFile),
        )
    assert mockRunner.called, "the executable steps must actually run"
    assert dictOutcome.get("bRerunAttempted", True) is True


@pytest.mark.falsification
def test_a_workflow_of_only_given_steps_is_not_a_pass(tmp_path):
    """Carrying every pinned entry leaves nothing reproduced.

    Kills: In fdictVerifyRerunOutputs, drop the "if not listCompared"
    branch — every entry is excluded, the comparison finds 0 of 0
    mismatches, and a workflow whose outputs are entirely human-made
    is attested as byte-identically reproduced.
    """
    (tmp_path / "Chains").mkdir()
    pathGiven = tmp_path / "Chains" / "samples.json"
    pathGiven.write_text("by hand\n")
    _fnWriteManifest(tmp_path, {
        "Chains/samples.json": pathGiven.read_bytes(),
    })
    dictWorkflow = {"listSteps": [{
        "sName": "Collect Chains",
        "sDirectory": "Chains",
        "bInteractive": True,
        "saOutputDataFiles": ["samples.json"],
    }]}
    with patch(S_RUNNER_PATCH_TARGET, return_value=True):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow,
            _fsWorkflowPathIn(tmp_path), str(tmp_path),
        )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["iOutputHashesTotal"] == 0
    assert S_DIVERGENCE_EVERY_ENTRY_GIVEN in dictOutcome[
        "listDivergedHashes"
    ]


def test_an_executed_step_keeps_a_path_a_given_step_also_declares():
    """Where both claim a path, the one backed by execution wins.

    Otherwise a stray declaration on a human step could quietly lift
    a genuinely computed artefact out of the comparison.
    """
    listCarried = flistCarriedOutputRepoPaths({"listSteps": [
        {
            "sName": "Collect Chains",
            "sDirectory": "Shared",
            "bInteractive": True,
            "saOutputDataFiles": ["both.json", "given.json"],
        },
        {
            "sName": "Compute",
            "sDirectory": "Shared",
            "saOutputDataFiles": ["both.json"],
        },
    ]})
    assert listCarried == ["Shared/given.json"]


def test_the_ai_declaration_is_carried_by_the_general_rule():
    """It needs no case of its own: it is an interactive step.

    Its declaration file rides along with its outputs because the
    manifest pins it as a publication artefact and a human wrote it —
    the same reason its step is given.
    """
    listCarried = flistCarriedOutputRepoPaths({"listSteps": [
        {
            "sName": "AI Declaration",
            "sDirectory": "AIDeclaration",
            "sStepKind": "ai-declaration",
            "sDeclarationFile": "AIDeclaration/declaration.md",
            "bInteractive": True,
            "saOutputDataFiles": [],
        },
        _fdictAutomatedStep("GenerateSamples"),
    ]})
    assert listCarried == ["AIDeclaration/declaration.md"]


@pytest.mark.falsification
def test_disabled_step_refuses_rerun_before_any_execution(
    fixturePinnedRepo,
):
    """A dashboard-disabled step would be skipped, so the rerun refuses.

    Kills: In flistNameStepsThatBlockARerun, return [] instead of the
    disabled-step comprehension — the scanner reports every workflow
    executable, the refusal never fires, and the patched runner raises.
    """
    dictWorkflow = {"listSteps": [
        _fdictAutomatedStep("GenerateSamples"),
        _fdictAutomatedStep("PlotHistogram", bRunEnabled=False),
    ]}
    with patch(S_RUNNER_PATCH_TARGET, side_effect=_fnFailIfRerunStarts):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow, _fsWorkflowPathIn(fixturePinnedRepo),
            str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["listDivergedHashes"] == [
        "step 'PlotHistogram' is disabled and would not execute"
    ]
    assert dictOutcome["sManifestDigest"], (
        "the refusal must still name the manifest it refused against"
    )


def test_workflow_with_no_steps_refuses_rerun(fixturePinnedRepo):
    """Zero steps executing zero commands is not a reproduction."""
    with patch(S_RUNNER_PATCH_TARGET, side_effect=_fnFailIfRerunStarts):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", {"listSteps": []},
            _fsWorkflowPathIn(fixturePinnedRepo), str(fixturePinnedRepo),
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
            None, "container", dictWorkflow, _fsWorkflowPathIn(fixturePinnedRepo),
            str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is True
    assert dictOutcome["iOutputHashesMatched"] == 1
    assert dictOutcome["iOutputHashesTotal"] == 1
    assert dictOutcome["listCarriedPaths"] == []


def test_scanner_reports_every_blocking_step_not_just_the_first():
    """The researcher sees the full repair list in one refusal."""
    listReasons = flistNameStepsThatBlockARerun({"listSteps": [
        _fdictAutomatedStep("PlotHistogram", bRunEnabled=False),
        _fdictAutomatedStep("WriteReport", bRunEnabled=False),
        _fdictAutomatedStep("GenerateSamples"),
    ]})
    assert len(listReasons) == 2
    assert "PlotHistogram" in listReasons[0]
    assert "WriteReport" in listReasons[1]


def test_an_interactive_step_is_never_named_as_blocking():
    """The refusal list is the repair list; a human step needs no repair."""
    listReasons = flistNameStepsThatBlockARerun({"listSteps": [
        _fdictInteractiveStep("InspectChains"),
        _fdictAutomatedStep("GenerateSamples"),
    ]})
    assert listReasons == []


@pytest.mark.falsification
def test_a_run_root_that_differs_from_the_comparison_root_is_refused(
    fixturePinnedRepo,
):
    """Running one directory and grading another must never be attempted.

    Kills: in _fsRefuseAMismatchedRunRoot, return "" unconditionally —
    the rerun proceeds with the two roots apart and the patched runner
    raises instead of the refusal firing.

    This is the false-pass class, not a path bug. Steps execute under
    the runner's resolved root; the comparison re-hashes under
    filesRepo. Where those differ the steps write somewhere the
    comparison never reads, so every pinned artefact is found exactly
    as the archive left it and a workflow that reproduced nothing
    attests byte-identical. The shadow lane shipped with those roots
    one level apart; it was caught only because the wrong directory
    happened not to exist.
    """
    dictWorkflow = {"listSteps": [_fdictAutomatedStep("GenerateSamples")]}
    with patch(S_RUNNER_PATCH_TARGET, side_effect=_fnFailIfRerunStarts):
        dictOutcome = fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow,
            "/somewhere/else/.vaibify/projects/project.json",
            str(fixturePinnedRepo),
        )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["bRerunAttempted"] is False
    assert S_DIVERGENCE_ROOT_MISMATCH in dictOutcome[
        "listDivergedHashes"][0]


def test_the_shadow_layout_resolves_to_the_shadow_repository(tmp_path):
    """The runner's root must be the repository, not its parent.

    Pinned as a RELATIONSHIP rather than a spelling. The runner takes a
    workflow directory and peels a level before cutting at
    ``.vaibify``, so handing it a repository root yields that root's
    PARENT — which is what put the shadow rerun in ``/shadow`` while
    its comparison read ``/shadow/<repo>``.
    """
    sRepoRoot = "/shadow/aigreenhouse"
    sWorkflowPath = sRepoRoot + "/.vaibify/projects/project.json"
    assert fsResolveRunnerRepoRoot(
        {"listSteps": []}, sWorkflowPath,
    ) == sRepoRoot


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
