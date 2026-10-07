"""A pass whose freshness could not be checked is never an attained Level 1.

When the poll cannot compare a step's recorded test digests with its
files (the snapshot failed, a read tore, the container omitted a path)
nothing is invalidated -- silence is not a deletion -- but the step must
not be shown as green either. The verdict is per poll and never
persisted, and it reaches Level 1 by the route the script-status verdict
already takes: the gate, the cell, the Step Viewer's requirement rows
and the badge all read the same answer.
"""

import pytest

from tests.markerPollHarness import MarkerPollProject
from vaibify.reproducibility import levelGates

S_REPO = "/workspace/proj-levelgate"
S_PATH_A = "StepA/out.dat"


def _fdictWorkflowOneStep(dictVerificationOverrides=None):
    dictVerification = {
        "sUnitTest": "passed", "sIntegrity": "passed",
        "sQualitative": "passed", "sQuantitative": "passed",
        "sUser": "passed"}
    dictVerification.update(dictVerificationOverrides or {})
    return {
        "sProjectRepoPath": S_REPO,
        "listSteps": [{
            "sName": "Step A", "sLabel": "A01", "sDirectory": "StepA",
            "bNoInputData": True, "saOutputDataFiles": ["out.dat"],
            "saPlotFiles": [], "dictVerification": dictVerification,
        }],
    }


@pytest.mark.falsification
def test_an_unchecked_pass_never_reads_attained_anywhere():
    """The poll's whole answer, end to end, for a failed snapshot.

    No step cell reads ``attained``, the scalar level is 0, the step's
    ratchet stamps nothing, and the blocker names the files whose
    answer is missing. (The Project row's own L1 cell is, by contract,
    "project repo present" and not an aggregate of the steps; the
    scalar level is what carries the all-steps verdict.)

    Kills: mapping unknown onto a met requirement, dropping the verdict
    from the gate, and stamping a high-water mark for an unchecked pass.
    """
    project = MarkerPollProject(iSnapshotExitCode=1)
    dictAnswer = project.fdictRunPoll()
    assert dictAnswer["iProofLevel"] == 0
    for sIndex, dictLevels in dictAnswer["dictStepLevels"].items():
        assert dictLevels["s1"]["sState"] != "attained", sIndex
    for dictStep in project.dictWorkflow["listSteps"]:
        assert "1" not in (dictStep.get("dictLevelHighWater") or {})
    dictBlocker = dictAnswer["listBlockers"][0]
    assert dictBlocker["sCriterion"] == "test-freshness-unchecked"
    assert dictBlocker["listOffendingFiles"]
    assert "not a failure" in dictBlocker["sRemediationHint"]
    assert dictBlocker["sRemediationHint"].endswith(
        "Click to run a diagnosis")


@pytest.mark.falsification
def test_the_scalar_level_and_the_gate_withhold_level_one():
    """The same inputs with and without the verdict.

    Kills: threading the verdict into the blocker list but not into the
    boolean gate that ``fiProofLevel`` reads.
    """
    dictWorkflow = _fdictWorkflowOneStep()
    assert levelGates.fiProofLevel(
        dictWorkflow, S_REPO, bHostProject=False) == 1
    assert levelGates.fiProofLevel(
        dictWorkflow, S_REPO, bHostProject=False,
        dictUnknownFreshnessByStep={0: [S_PATH_A]}) == 0


@pytest.mark.falsification
def test_the_verdict_is_part_of_the_blocker_cache_key():
    """Identical Level 1 inputs except the verdict: different answers.

    The blocker lists are cached on a key of the workflow, the mtimes,
    the repo and the script status. The verdict is an input of the same
    gate, so a key that omitted it would serve the answer computed
    without it.

    Kills: dropping the verdict fingerprint from the cache key.
    """
    dictWorkflow = _fdictWorkflowOneStep()
    listChecked = levelGates.flistLevel1Blockers(
        dictWorkflow, {}, S_REPO, None, None)
    listUnchecked = levelGates.flistLevel1Blockers(
        dictWorkflow, {}, S_REPO, None, {0: [S_PATH_A]})
    listCheckedAgain = levelGates.flistLevel1Blockers(
        dictWorkflow, {}, S_REPO, None, None)
    assert listChecked == [] == listCheckedAgain
    assert [d["sCriterion"] for d in listUnchecked] == [
        "test-freshness-unchecked"]
    assert levelGates._fsFreshnessVerdictFingerprint(None) == "none"
    assert levelGates._fsFreshnessVerdictFingerprint(
        {0: ["b", "a"]}) == levelGates._fsFreshnessVerdictFingerprint(
        {0: ["a", "b"]})
    assert levelGates._fsFreshnessVerdictFingerprint(
        {0: ["a"]}) != levelGates._fsFreshnessVerdictFingerprint(
        {1: ["a"]})


@pytest.mark.falsification
def test_the_criterion_ranks_below_red_axes_and_above_attestation():
    """Priority: ``axis-not-green`` > ``test-freshness-unchecked`` > the
    two attestation criteria.

    Kills: ranking the unchecked criterion above a failed test (hiding
    the failure).
    """
    for dictOverride, sExpectedCriterion in (
        ({"sUnitTest": "failed"}, "axis-not-green"),
        ({"sUser": "untested"}, "test-freshness-unchecked"),
        ({"sUser": "stale", "sLastUserUpdate": "2026-01-01T00:00:00Z"},
         "test-freshness-unchecked"),
    ):
        dictWorkflow = _fdictWorkflowOneStep(dictOverride)
        listBlockers = levelGates.flistLevel1Blockers(
            dictWorkflow, {}, S_REPO, None, {0: [S_PATH_A]})
        assert listBlockers[0]["sCriterion"] == sExpectedCriterion, (
            dictOverride)


@pytest.mark.falsification
def test_test_rows_read_unknown_even_when_script_stale_dominates():
    """The requirement rows read the verdict directly, not the blocker.

    A step that is also ``script-stale`` carries only that dominant
    blocker, so a row derived from the blocker list would show its
    tests as met. The rows must still say they could not be checked.

    Kills: reading the freshness requirement from the dominant-criterion
    set instead of from the verdict.
    """
    dictWorkflow = _fdictWorkflowOneStep()
    dictScriptStatus = {0: {"sStatus": "modified"}}
    dictUnknown = {0: [S_PATH_A]}
    listBlockers = levelGates.flistLevel1Blockers(
        dictWorkflow, {}, S_REPO, dictScriptStatus, dictUnknown)
    assert [d["sCriterion"] for d in listBlockers] == ["script-stale"]
    dictStates = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, listBlockers, [], [], None, dictUnknown)
    dictRows = {
        dictRow["sName"]: dictRow["bMet"]
        for dictRow in dictStates[0]["s1"]["listRequirements"]}
    for sAxis in ("sUnitTest", "sIntegrity", "sQualitative",
                  "sQuantitative"):
        assert dictRows[sAxis] is None, sAxis
    assert dictRows["user-attestation"] is True
    assert dictStates[0]["s1"]["sState"] != "attained"


@pytest.mark.falsification
def test_a_red_axis_stays_unmet_and_a_checked_pass_stays_met():
    """Unknown only softens a green axis; it never rescues a red one.

    Kills: mapping every axis to unknown when the step has an unchecked
    verdict.
    """
    dictWorkflow = _fdictWorkflowOneStep({"sIntegrity": "failed"})
    dictStates = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], [], [], None, {0: [S_PATH_A]})
    dictRows = {
        dictRow["sName"]: dictRow["bMet"]
        for dictRow in dictStates[0]["s1"]["listRequirements"]}
    assert dictRows["sIntegrity"] is False
    assert dictRows["sUnitTest"] is None
    dictChecked = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], [], [], None, None)
    assert {
        dictRow["sName"]: dictRow["bMet"]
        for dictRow in dictChecked[0]["s1"]["listRequirements"]
    }["sUnitTest"] is True


def test_an_unchecked_cell_counts_the_unknowns_and_is_not_attained():
    dictWorkflow = _fdictWorkflowOneStep()
    dictStates = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], [], [], None, {0: [S_PATH_A]})
    dictCell = dictStates[0]["s1"]
    assert dictCell["sState"] == "partial"
    assert dictCell["iSatisfied"] < dictCell["iTotal"]


def test_the_ratchet_stamps_nothing_for_an_unchecked_step():
    """Only ``attained`` stamps, and an unchecked cell is not attained."""
    from vaibify.gui import stateManager
    dictWorkflow = _fdictWorkflowOneStep()
    dictStates = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], [], [], None, {0: [S_PATH_A]})
    stateManager.fbRatchetLevelHighWater(dictWorkflow, dictStates, {})
    assert "1" not in (
        dictWorkflow["listSteps"][0].get("dictLevelHighWater") or {})
