"""Mutation-coverage tests for workflowManager path-boundary, dep-graph
cache-key, and cross-step reference classification.

Each test closes a specific coverage hole found by mutation testing.
The guarantees enforced here are load-bearing for the threat model
(no host write may escape the project repo) and for AICS Level 1 (the
declared dependency graph must be complete and honest). A silent drift
in any of these must fail the suite.
"""

import pytest

from vaibify.gui.workflowManager import (
    _fsCheckDatasetDestinationBoundary,
    _fsCheckOutputPathBoundary,
    _fsCheckPlotDirectoryBoundary,
    _fsCheckStepDirectoryBoundary,
    _flistValidateDatasetDestinations,
    fbValidateWorkflow,
    fdictBuildDirectDependencies,
    flistValidateReferences,
    fnClearDepGraphCache,
)

pytestmark = pytest.mark.falsification


def fdictMakeWorkflow(listSteps, **dictExtra):
    """Return a minimal valid workflow wrapping listSteps."""
    dictWorkflow = {"sPlotDirectory": "plots", "listSteps": listSteps}
    dictWorkflow.update(dictExtra)
    return dictWorkflow


def fdictMakeStep(sName, **dictExtra):
    """Return a minimal step satisfying T_REQUIRED_STEP_KEYS."""
    dictStep = {
        "sName": sName,
        "sDirectory": "",
        "saPlotCommands": [],
        "saPlotFiles": [],
    }
    dictStep.update(dictExtra)
    return dictStep


# ── _fsCheckOutputPathBoundary: exact '..' boundary (line 605) ───────


def test_output_entry_resolving_to_repo_parent_is_rejected():
    """An output entry that normalizes to EXACTLY the repo parent
    ('..') escapes the workspace boundary and must be flagged.

    Dropping the `sJoined == '..'` clause (leaving only
    startswith('../')) lets empty-dir + '..' and 'sub' + '../..'
    pass, so backend writes land one directory ABOVE the project
    repo. Both forms must produce an 'escapes the project repo'
    warning and fbValidateWorkflow must return False.

    Kills: Remove the `sJoined == '..'` clause in
    _fsCheckOutputPathBoundary (line 605); only startswith('../')
    remains."""
    sWarnEmpty = _fsCheckOutputPathBoundary(
        "..", "", "Step01", "saOutputFiles",
    )
    sWarnSub = _fsCheckOutputPathBoundary(
        "../..", "sub", "Step01", "saOutputFiles",
    )
    assert "escapes the project repo" in sWarnEmpty
    assert "escapes the project repo" in sWarnSub
    dictWorkflow = fdictMakeWorkflow(
        [fdictMakeStep("a", saOutputFiles=[".."])],
    )
    assert fbValidateWorkflow(dictWorkflow) is False


# ── _fsCheckStepDirectoryBoundary: exact '..' boundary (line 587) ────


def test_step_directory_equal_to_repo_parent_is_rejected():
    """sDirectory exactly '..' roots the whole step in the repo's
    parent, silently moving every host write outside the project
    repo. Dropping the `sNorm == '..'` clause accepts it; this
    asserts the warning fires and fbValidateWorkflow returns False.

    Kills: Remove the `sNorm == '..'` clause in
    _fsCheckStepDirectoryBoundary (line 587)."""
    sWarnDirect = _fsCheckStepDirectoryBoundary("..", "Step01")
    sWarnNested = _fsCheckStepDirectoryBoundary("a/../..", "Step01")
    assert "escapes the project repo" in sWarnDirect
    assert "escapes the project repo" in sWarnNested
    dictWorkflow = fdictMakeWorkflow(
        [fdictMakeStep("a", sDirectory="..")],
    )
    assert fbValidateWorkflow(dictWorkflow) is False


# ── _fsCheckPlotDirectoryBoundary: exact '..' boundary (line 513) ────


def test_plot_directory_equal_to_repo_parent_is_rejected():
    """sPlotDirectory exactly '..' resolves figures to the repo
    parent, breaking archive scoping. Dropping the `sNorm == '..'`
    clause accepts it; assert the boundary check warns and
    fbValidateWorkflow returns False.

    Kills: Remove the `sNorm == '..'` clause in
    _fsCheckPlotDirectoryBoundary (line 513)."""
    sWarning = _fsCheckPlotDirectoryBoundary("..")
    assert "escapes the project repo" in sWarning
    dictWorkflow = fdictMakeWorkflow(
        [fdictMakeStep("a")], sPlotDirectory="..",
    )
    assert fbValidateWorkflow(dictWorkflow) is False


# ── _fsCheckDatasetDestinationBoundary: exact '..' (line 549) ────────


def test_dataset_destination_equal_to_repo_parent_is_rejected():
    """A dataset sDestination of exactly '..' stages host data one
    level outside the workspace. Dropping the `sNorm == '..'` clause
    accepts it; assert both the per-destination check and the
    workflow-level validation flag it.

    Kills: Remove the `sNorm == '..'` clause in
    _fsCheckDatasetDestinationBoundary (line 549)."""
    sWarning = _fsCheckDatasetDestinationBoundary("..", "Dataset01")
    assert "escapes the project repo" in sWarning
    dictWorkflow = fdictMakeWorkflow(
        [fdictMakeStep("a")],
        listDatasets=[{"sDestination": ".."}],
    )
    listWarnings = _flistValidateDatasetDestinations(dictWorkflow)
    assert any("escapes the project repo" in s for s in listWarnings)
    assert fbValidateWorkflow(dictWorkflow) is False


# ── _fsWorkflowDepCacheKey: saDependencies participates (line 1698) ──


def test_dep_cache_key_tracks_sadependencies_edits():
    """An saDependencies-only edit (adding a manual {StepNN.*} edge)
    must bust the dep-graph cache so the recomputed graph contains
    the new edge. Dropping saDependencies from the cache key returns
    the stale pre-edit graph from the LRU, so a declared dependency
    edge silently vanishes and bUpstreamModified cannot fire.

    Kills: Drop saDependencies from the dep-graph cache key
    dictRelevant in _fsWorkflowDepCacheKey (line 1698)."""
    fnClearDepGraphCache()
    dictWorkflow = fdictMakeWorkflow([
        fdictMakeStep("a"),
        fdictMakeStep("b", saDependencies=[]),
    ])
    dictBefore = fdictBuildDirectDependencies(dictWorkflow)
    assert dictBefore.get(0, set()) == set()
    dictWorkflow["listSteps"][1]["saDependencies"] = ["{Step01.out}"]
    dictAfter = fdictBuildDirectDependencies(dictWorkflow)
    assert 1 in dictAfter.get(0, set())
    fnClearDepGraphCache()


# ── _fsClassifyReference: self-reference is circular (line 1096) ─────


def test_self_referencing_step_flagged_as_circular():
    """A step that references its OWN output token (iRefNumber ==
    iNumber) is an impossible dependency and must be reported as a
    circular dependency. Weakening `iRefNumber >= iNumber` to `>`
    drops the self-loop case, returning no warning at all.

    Kills: Weaken `iRefNumber >= iNumber` to `iRefNumber > iNumber`
    in _fsClassifyReference (line 1096)."""
    dictWorkflow = fdictMakeWorkflow([
        fdictMakeStep(
            "a",
            saOutputFiles=["result.csv"],
            saCommands=["run {Step01.result}"],
        ),
        fdictMakeStep("b"),
    ])
    listWarnings = flistValidateReferences(dictWorkflow)
    assert any("circular dependency" in s for s in listWarnings)


# ── _fsClassifyReference: last-step ref off-by-one (line 1092) ───────


def test_reference_to_last_step_is_circular_not_beyond():
    """A reference to the LAST existing step (iRefNumber ==
    iStepCount) from an earlier step is a forward/circular edge, not
    a beyond-the-end reference. The off-by-one `>` -> `>=` mislabels
    it 'points beyond the last step'; assert the diagnostic is
    circular and NOT beyond-the-last-step.

    Kills: Off-by-one `iRefNumber > iStepCount` to `>= iStepCount`
    in _fsClassifyReference (line 1092)."""
    dictWorkflow = fdictMakeWorkflow([
        fdictMakeStep("a", saCommands=["run {Step02.result}"]),
        fdictMakeStep("b", saOutputFiles=["result.csv"]),
    ])
    listWarnings = flistValidateReferences(dictWorkflow)
    assert any("circular dependency" in s for s in listWarnings)
    assert not any("beyond the last step" in s for s in listWarnings)
