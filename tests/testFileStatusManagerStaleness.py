"""Mutation-closing tests for staleness/invalidation in fileStatusManager.

Each test targets a specific surviving mutant: a marker-bootstrapped
green badge that must still demote, a plot-standards badge that must go
stale, the running-pipeline invalidation guard, the upstream-flag
reconciliation, and the same-second mtime boundary comparisons that
distinguish "fresh" from "changed after verify".
"""

from unittest.mock import patch

import pytest

from vaibify.gui.fileStatusManager import (
    _fbAnyMtimeNewerThan,
    _fdictDetectChangedFiles,
    _fdictParseStatLines,
    _fiMtimeStalenessSignal,
    _flistNewerPaths,
    _fnInvalidateDownstreamStep,
    _fnInvalidateStepFiles,
    fbReconcileUpstreamFlags,
    fbReconcileUserVerificationTimestamps,
)


pytestmark = pytest.mark.falsification


_S_FRESH = "2026-04-20 00:00:00 UTC"


# ---------------------------------------------------------------
# _SET_PASSED_TEST_STATES must include 'passed-from-marker' so a
# marker-bootstrapped green badge demotes on data/upstream change.
# ---------------------------------------------------------------


@pytest.mark.parametrize("sPassedState", ["passed", "passed-from-marker"])
def test_fnInvalidateStepFiles_demotes_passed_states_on_data_change(
    sPassedState,
):
    """A passed/passed-from-marker badge demotes on a data-file change.

    Kills: Line 698: _SET_PASSED_TEST_STATES drops 'passed-from-marker'
    (frozenset shrinks to {'passed'})
    """
    dictStep = {
        "saDataFiles": ["data.out"],
        "saPlotFiles": [],
        "dictVerification": {
            "sUnitTest": sPassedState,
            "sIntegrity": sPassedState,
        },
    }
    _fnInvalidateStepFiles(
        dictStep, ["/ws/step0/data.out"], dictModTimes={},
    )
    dictV = dictStep["dictVerification"]
    assert dictV["sUnitTest"] == "untested"
    assert dictV["sIntegrity"] == "untested"


@pytest.mark.parametrize("sPassedState", ["passed", "passed-from-marker"])
def test_fnInvalidateDownstreamStep_demotes_passed_states(sPassedState):
    """A downstream step's passed-from-marker badge demotes on invalidate.

    Kills: Line 698: _SET_PASSED_TEST_STATES drops 'passed-from-marker'
    (frozenset shrinks to {'passed'})
    """
    dictStep = {
        "dictVerification": {
            "sUnitTest": sPassedState,
            "sQuantitative": sPassedState,
        },
    }
    _fnInvalidateDownstreamStep(dictStep)
    dictV = dictStep["dictVerification"]
    assert dictV["sUnitTest"] == "untested"
    assert dictV["sQuantitative"] == "untested"
    assert dictV["bUpstreamModified"] is True


# ---------------------------------------------------------------
# Plot-standards badge must flip 'passed' -> 'stale' on a plot change.
# ---------------------------------------------------------------


def test_fnInvalidateStepFiles_plot_standards_goes_stale_on_plot_change():
    """A plot-file change flips the plot-standards badge to 'stale'.

    Kills: Line 771: _fnInvalidateStepFiles sets sPlotStandards='passed'
    instead of 'stale' on a plot-file change
    """
    dictStep = {
        "saDataFiles": [],
        "saPlotFiles": ["fig.pdf"],
        "dictVerification": {"sPlotStandards": "passed"},
    }
    _fnInvalidateStepFiles(
        dictStep, ["/ws/step0/fig.pdf"],
        dictModTimes={"/ws/step0/fig.pdf": "1900000000"},
    )
    assert dictStep["dictVerification"]["sPlotStandards"] == "stale"


def test_fnInvalidateStepFiles_plot_standards_kept_on_non_plot_change():
    """A non-plot data change must leave the plot-standards badge 'passed'.

    Kills: a mutation that demotes sPlotStandards to 'stale' even when
    only a non-plot data file changed (spurious over-invalidation of the
    plot badge).
    """
    dictStep = {
        "saDataFiles": ["data.out"],
        "saPlotFiles": ["fig.pdf"],
        "dictVerification": {"sPlotStandards": "passed"},
    }
    _fnInvalidateStepFiles(
        dictStep, ["/ws/step0/data.out"],
        dictModTimes={"/ws/step0/data.out": "1900000000"},
    )
    assert dictStep["dictVerification"]["sPlotStandards"] == "passed"


# ---------------------------------------------------------------
# Running-pipeline guard must suppress invalidation; the suppression
# comes from the guard, not from an empty workflow.
# ---------------------------------------------------------------


def _fdictBuildPollContext():
    return {"dictPreviousModTimes": {"cid": {"/ws/step0/out.dat": "100"}}}


def _dictWorkflowSingleStep():
    return {
        "listSteps": [{
            "sDirectory": "/ws/step0",
            "saDataFiles": ["out.dat"],
            "saPlotFiles": [],
            "dictVerification": {},
        }],
    }


_DICT_POLL_VARS = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}


def test_fdictDetectChangedFiles_suppressed_while_running():
    """While the pipeline runs, change detection is suppressed.

    Kills: Line 1178: _fdictDetectChangedFiles running-pipeline guard
    neutralized (applied as `if False and bPipelineRunning:`)
    """
    dictCtx = _fdictBuildPollContext()
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        dictResult = _fdictDetectChangedFiles(
            dictCtx, "cid", _dictWorkflowSingleStep(),
            {"/ws/step0/out.dat": "200"}, dictVars=_DICT_POLL_VARS,
        )
    assert dictResult == {}


def test_fdictDetectChangedFiles_detects_change_when_not_running():
    """When the pipeline is idle, a changed output is reported.

    Kills: a mutation that suppresses change detection even when the
    pipeline is not running (guard stuck always-on), which would hide
    genuine output changes from the dashboard.
    """
    dictCtx = _fdictBuildPollContext()
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        dictResult = _fdictDetectChangedFiles(
            dictCtx, "cid", _dictWorkflowSingleStep(),
            {"/ws/step0/out.dat": "200"}, dictVars=_DICT_POLL_VARS,
        )
    assert dictResult == {0: ["/ws/step0/out.dat"]}


# ---------------------------------------------------------------
# fbReconcileUpstreamFlags clear-branch must fire when iSignal == 0.
# ---------------------------------------------------------------


def _dictWorkflowWithUpstreamEdge(dictDownVerification):
    """Step 1 references {Step01.x}, so step 1's upstream is step 0."""
    return {
        "listSteps": [
            {
                "sDirectory": "/ws/step0",
                "saDataFiles": ["a.out"],
                "dictVerification": {},
            },
            {
                "sDirectory": "/ws/step1",
                "saDataCommands": ["python run.py {Step01.x}"],
                "saDataFiles": ["b.out"],
                "dictVerification": dictDownVerification,
            },
        ],
    }


def test_fbReconcileUpstreamFlags_clears_flag_when_downstream_fresh():
    """A fresh downstream clears its bUpstreamModified flag (iSignal == 0).

    Kills: Line 847: fbReconcileUpstreamFlags clear-branch
    'iSignal == 0' -> 'iSignal == 2'
    """
    dictWorkflow = _dictWorkflowWithUpstreamEdge(
        {"bUpstreamModified": True},
    )
    # Downstream (step 1) mtime >= upstream (step 0) mtime -> fresh.
    dictMaxMtimeByStep = {"0": "100", "1": "100"}
    bChanged = fbReconcileUpstreamFlags(dictWorkflow, dictMaxMtimeByStep)
    assert bChanged is True
    dictV = dictWorkflow["listSteps"][1]["dictVerification"]
    assert dictV.get("bUpstreamModified") is None


def test_fbReconcileUpstreamFlags_sets_flag_when_downstream_stale():
    """A stale downstream gets bUpstreamModified set when upstream is newer.

    Kills: a mutation that fails to set bUpstreamModified when the
    upstream mtime exceeds the downstream mtime, hiding a real
    upstream-changed condition.
    """
    dictWorkflow = _dictWorkflowWithUpstreamEdge({})
    # Upstream (step 0) newer than downstream (step 1) -> stale.
    dictMaxMtimeByStep = {"0": "200", "1": "100"}
    bChanged = fbReconcileUpstreamFlags(dictWorkflow, dictMaxMtimeByStep)
    assert bChanged is True
    dictV = dictWorkflow["listSteps"][1]["dictVerification"]
    assert dictV.get("bUpstreamModified") is True


# ---------------------------------------------------------------
# _fiMtimeStalenessSignal: equal same-second mtimes are NOT stale.
# ---------------------------------------------------------------


def test_fiMtimeStalenessSignal_equal_mtimes_is_fresh():
    """Equal same-second upstream/downstream mtimes are fresh, not stale.

    Kills: Line 878: _fiMtimeStalenessSignal upstream comparison
    '>' -> '>='
    """
    iSignal = _fiMtimeStalenessSignal(
        1, {1: {0}}, {"0": "100", "1": "100"},
    )
    assert iSignal == 0


def test_fiMtimeStalenessSignal_older_downstream_is_stale():
    """A strictly newer upstream marks the downstream stale (signal 1).

    Kills: a mutation that fails to flag staleness when the upstream
    mtime strictly exceeds the downstream mtime.
    """
    iSignal = _fiMtimeStalenessSignal(
        1, {1: {0}}, {"0": "200", "1": "100"},
    )
    assert iSignal == 1


# ---------------------------------------------------------------
# Same-second mtime boundaries: '>' not '>='.
# ---------------------------------------------------------------


def test_flistNewerPaths_excludes_equal_boundary():
    """A path whose mtime equals the threshold is not newer.

    Kills: Line 894: _flistNewerPaths threshold 'iMtime > iThreshold'
    -> '>='
    """
    assert _flistNewerPaths(["/a"], {"/a": "100"}, iThreshold=100) == []


def test_flistNewerPaths_includes_strictly_newer():
    """A path whose mtime strictly exceeds the threshold is reported.

    Kills: a mutation that breaks the newer-than comparison so a
    strictly newer path is no longer returned by _flistNewerPaths.
    """
    assert _flistNewerPaths(
        ["/a"], {"/a": "101"}, iThreshold=100,
    ) == ["/a"]


def test_fbAnyMtimeNewerThan_excludes_equal_boundary():
    """An mtime equal to the threshold does not count as newer.

    Kills: Line 549: _fbAnyMtimeNewerThan 'int(sMtime) > iThreshold'
    -> '>='
    """
    assert _fbAnyMtimeNewerThan(
        ["/a"], {"/a": "50"}, iThreshold=50,
    ) is False


def test_fbAnyMtimeNewerThan_includes_strictly_newer():
    """A strictly newer mtime is detected as newer-than the threshold.

    Kills: a mutation that breaks the newer-than test so a strictly
    newer mtime is not detected by _fbAnyMtimeNewerThan.
    """
    assert _fbAnyMtimeNewerThan(
        ["/a"], {"/a": "51"}, iThreshold=50,
    ) is True


# ---------------------------------------------------------------
# fbReconcileUserVerificationTimestamps must RETAIN the timestamp on
# a 'stale' step (the load-bearing "changed after you verified" state).
# ---------------------------------------------------------------


def test_fbReconcileUserVerificationTimestamps_retains_stale():
    """A 'stale' step retains its user-verification timestamp.

    Kills: Line 814: fbReconcileUserVerificationTimestamps retain set
    ('passed','stale') -> ('passed',)
    """
    dictWorkflow = {
        "listSteps": [{
            "dictVerification": {
                "sUser": "stale",
                "sLastUserUpdate": _S_FRESH,
            },
        }],
    }
    bChanged = fbReconcileUserVerificationTimestamps(dictWorkflow)
    assert bChanged is False
    dictV = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictV["sLastUserUpdate"] == _S_FRESH


# ---------------------------------------------------------------
# _fdictParseStatLines must split on the LAST space so paths with
# embedded spaces keep their mtime intact.
# ---------------------------------------------------------------


def test_fdictParseStatLines_handles_path_with_space():
    """A stat line for a path with spaces keeps the path and mtime intact.

    Kills: Line 1457: _fdictParseStatLines 'sLine.rsplit(" ",1)' ->
    'sLine.split(" ",1)'
    """
    dictResult = _fdictParseStatLines("/ws/Plot Output/fig.pdf 123\n")
    assert dictResult == {"/ws/Plot Output/fig.pdf": "123"}
