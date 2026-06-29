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


_S_FRESH = "2026-04-20 00:00:00 UTC"


# ---------------------------------------------------------------
# _SET_PASSED_TEST_STATES must include 'passed-from-marker' so a
# marker-bootstrapped green badge demotes on data/upstream change.
# ---------------------------------------------------------------


@pytest.mark.parametrize("sPassedState", ["passed", "passed-from-marker"])
def test_fnInvalidateStepFiles_demotes_passed_states_on_data_change(
    sPassedState,
):
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
    iSignal = _fiMtimeStalenessSignal(
        1, {1: {0}}, {"0": "100", "1": "100"},
    )
    assert iSignal == 0


def test_fiMtimeStalenessSignal_older_downstream_is_stale():
    iSignal = _fiMtimeStalenessSignal(
        1, {1: {0}}, {"0": "200", "1": "100"},
    )
    assert iSignal == 1


# ---------------------------------------------------------------
# Same-second mtime boundaries: '>' not '>='.
# ---------------------------------------------------------------


def test_flistNewerPaths_excludes_equal_boundary():
    assert _flistNewerPaths(["/a"], {"/a": "100"}, iThreshold=100) == []


def test_flistNewerPaths_includes_strictly_newer():
    assert _flistNewerPaths(
        ["/a"], {"/a": "101"}, iThreshold=100,
    ) == ["/a"]


def test_fbAnyMtimeNewerThan_excludes_equal_boundary():
    assert _fbAnyMtimeNewerThan(
        ["/a"], {"/a": "50"}, iThreshold=50,
    ) is False


def test_fbAnyMtimeNewerThan_includes_strictly_newer():
    assert _fbAnyMtimeNewerThan(
        ["/a"], {"/a": "51"}, iThreshold=50,
    ) is True


# ---------------------------------------------------------------
# fbReconcileUserVerificationTimestamps must RETAIN the timestamp on
# a 'stale' step (the load-bearing "changed after you verified" state).
# ---------------------------------------------------------------


def test_fbReconcileUserVerificationTimestamps_retains_stale():
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
    dictResult = _fdictParseStatLines("/ws/Plot Output/fig.pdf 123\n")
    assert dictResult == {"/ws/Plot Output/fig.pdf": "123"}
