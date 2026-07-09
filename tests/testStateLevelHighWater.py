"""Unit tests for the AICS level high-water ratchet in stateManager.

The high-water record answers "when did this step (or the workflow
header) first attain each level?". The ratchet is ADD-ONLY: regression
to any non-attained state (``none`` / ``partial`` / ``unknown`` /
``not-started``) never erases a recorded timestamp — regression memory
is the feature, and the dashboard renders it truthfully. The ratchet
consumes the independent-level cell dicts
(``{"sState", "iSatisfied", "iTotal", "bRegression"}``) and ONLY
``attained`` stamps. Schema v2 carries the new fields through the same
tuple-generic split/merge that handles every other stateful field, so
v1 ``state.json`` files load with no migration.
"""

import copy

from vaibify.gui import stateManager
from vaibify.gui.stateManager import (
    I_CURRENT_STATE_SCHEMA_VERSION,
    T_STATEFUL_STEP_FIELDS,
    T_STATEFUL_TOP_FIELDS,
    fbRatchetLevelHighWater,
    fnMergeStateIntoWorkflow,
    ftSplitMergedDict,
)


def _fdictWorkflowWithOneStep():
    """Return a minimal merged workflow dict with one named step."""
    return {
        "listSteps": [
            {"sName": "stepOne", "sDirectory": "stepOne"},
        ],
    }


def _fdictCell(sState):
    """Return one independent-level cell with the given state."""
    return {
        "sState": sState, "iSatisfied": 0, "iTotal": 1,
        "bRegression": False,
    }


def _fdictAllInState(sState):
    """Return a level-state dict with every level cell in one state."""
    return {
        "s1": _fdictCell(sState),
        "s2": _fdictCell(sState),
        "s3": _fdictCell(sState),
    }


def _fdictAllAttained():
    """Return a level-state dict with every level attained."""
    return _fdictAllInState("attained")


def _fdictAllRegressed():
    """Return a level-state dict with every level back to none."""
    return _fdictAllInState("none")


def testSchemaVersionBumpedToTwo():
    assert I_CURRENT_STATE_SCHEMA_VERSION == 2


def testStatefulTuplesCarryHighWaterFields():
    assert "dictLevelHighWater" in T_STATEFUL_STEP_FIELDS
    assert "dictWorkflowLevelHighWater" in T_STATEFUL_TOP_FIELDS


def testAttainStampsStepAndWorkflowTimestamps():
    dictWorkflow = _fdictWorkflowWithOneStep()
    bChanged = fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllAttained()}, _fdictAllAttained(),
    )
    assert bChanged is True
    dictStepHighWater = dictWorkflow["listSteps"][0]["dictLevelHighWater"]
    assert set(dictStepHighWater.keys()) == {"1", "2", "3"}
    assert all(dictStepHighWater.values())
    dictHeaderHighWater = dictWorkflow["dictWorkflowLevelHighWater"]
    assert set(dictHeaderHighWater.keys()) == {"1", "2", "3"}


def testPartialAttainmentStampsOnlyAttainedLevels():
    dictWorkflow = _fdictWorkflowWithOneStep()
    dictStates = {
        "s1": _fdictCell("attained"),
        "s2": _fdictCell("partial"),
        "s3": _fdictCell("none"),
    }
    bChanged = fbRatchetLevelHighWater(
        dictWorkflow, {0: dictStates}, dictStates,
    )
    assert bChanged is True
    dictStepHighWater = dictWorkflow["listSteps"][0]["dictLevelHighWater"]
    assert set(dictStepHighWater.keys()) == {"1"}


def testRegressionSurvivesAndReturnsFalse():
    dictWorkflow = _fdictWorkflowWithOneStep()
    fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllAttained()}, _fdictAllAttained(),
    )
    dictBefore = copy.deepcopy(dictWorkflow)
    bChanged = fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllRegressed()}, _fdictAllRegressed(),
    )
    assert bChanged is False
    assert dictWorkflow == dictBefore


def testReattainmentKeepsOriginalTimestamp(monkeypatch):
    dictWorkflow = _fdictWorkflowWithOneStep()
    monkeypatch.setattr(
        stateManager, "_fsCurrentUtcIso",
        lambda: "2026-06-01T00:00:00Z",
    )
    fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllAttained()}, _fdictAllAttained(),
    )
    monkeypatch.setattr(
        stateManager, "_fsCurrentUtcIso",
        lambda: "2026-06-09T00:00:00Z",
    )
    bChanged = fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllAttained()}, _fdictAllAttained(),
    )
    assert bChanged is False
    dictStepHighWater = dictWorkflow["listSteps"][0]["dictLevelHighWater"]
    assert dictStepHighWater["1"] == "2026-06-01T00:00:00Z"


def testOnlyAttainedEverStamps():
    """Every non-attained cell state — including unknown, partial,
    not-started, and unassessed — must stamp nothing."""
    for sState in (
        "unknown", "partial", "none", "not-started", "unassessed",
    ):
        dictWorkflow = _fdictWorkflowWithOneStep()
        dictStates = _fdictAllInState(sState)
        bChanged = fbRatchetLevelHighWater(
            dictWorkflow, {0: dictStates}, dictStates,
        )
        assert bChanged is False, sState
        assert "dictLevelHighWater" not in dictWorkflow["listSteps"][0]
        assert "dictWorkflowLevelHighWater" not in dictWorkflow


def testRatchetReturnsFalseWithEmptyStates():
    dictWorkflow = _fdictWorkflowWithOneStep()
    assert fbRatchetLevelHighWater(dictWorkflow, {}, {}) is False
    assert fbRatchetLevelHighWater(dictWorkflow, None, None) is False


def testSplitMovesHighWaterIntoStateFile():
    dictWorkflow = _fdictWorkflowWithOneStep()
    fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllAttained()}, _fdictAllAttained(),
    )
    dictDeclarative, dictState = ftSplitMergedDict(dictWorkflow)
    assert "dictLevelHighWater" not in dictDeclarative["listSteps"][0]
    assert "dictWorkflowLevelHighWater" not in dictDeclarative
    dictStepState = dictState["dictStepState"]["stepOne"]
    assert set(dictStepState["dictLevelHighWater"].keys()) == {
        "1", "2", "3",
    }
    assert set(dictState["dictWorkflowLevelHighWater"].keys()) == {
        "1", "2", "3",
    }


def testSplitThenMergeRoundTripPreservesHighWater():
    dictWorkflow = _fdictWorkflowWithOneStep()
    fbRatchetLevelHighWater(
        dictWorkflow, {0: _fdictAllAttained()}, _fdictAllAttained(),
    )
    dictExpectedStep = copy.deepcopy(
        dictWorkflow["listSteps"][0]["dictLevelHighWater"],
    )
    dictExpectedHeader = copy.deepcopy(
        dictWorkflow["dictWorkflowLevelHighWater"],
    )
    dictDeclarative, dictState = ftSplitMergedDict(dictWorkflow)
    fnMergeStateIntoWorkflow(dictDeclarative, dictState)
    assert dictDeclarative["listSteps"][0]["dictLevelHighWater"] == (
        dictExpectedStep
    )
    assert dictDeclarative["dictWorkflowLevelHighWater"] == (
        dictExpectedHeader
    )


def testVersionOneStateLoadsWithAbsentHighWaterFields():
    """A v1 state.json — no high-water keys anywhere — merges cleanly:
    absent keys mean the levels were never attained, and the merge
    must not invent empty fields."""
    dictWorkflow = _fdictWorkflowWithOneStep()
    dictStateVersionOne = {
        "iStateSchemaVersion": 1,
        "sLastUpdated": "2026-01-01T00:00:00Z",
        "dictStepState": {
            "stepOne": {
                "dictVerification": {"sUser": "passed"},
                "dictRunStats": {},
            },
        },
    }
    fnMergeStateIntoWorkflow(dictWorkflow, dictStateVersionOne)
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["dictVerification"] == {"sUser": "passed"}
    assert "dictLevelHighWater" not in dictStep
    assert "dictWorkflowLevelHighWater" not in dictWorkflow
