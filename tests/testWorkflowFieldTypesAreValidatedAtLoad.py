"""Numeric and state fields of project.json are typed at the load boundary.

``project.json`` and ``state.json`` are writable from inside the
container, and the dashboard writes a handful of their fields into HTML
attributes and form values: the verification state into a CSS class, the
core count, tolerance and runtime limits into ``value=`` attributes. A
string where a number belongs is therefore markup the page would
interpolate. The loader refuses a wrong type by name; the renderer
escapes again (``tests/browser/testWorkflowFieldsCannotInjectMarkup.py``).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui import workflowFieldTypes, workflowManager

S_HOSTILE = 'x" onfocus="window.pwned=1" autofocus="'


def _fdictWorkflow(**dictTopLevel):
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Simulate", "sDirectory": "sim",
            "saPlotCommands": ["python plot.py"], "saPlotFiles": ["f.pdf"],
        }],
    }
    dictWorkflow.update(dictTopLevel)
    return dictWorkflow


def _fdictLoad(dictWorkflow):
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps(
        dictWorkflow).encode("utf-8")
    return workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "containerId",
        "/workspace/repo/.vaibify/projects/project.json",
    )


@pytest.mark.falsification
def testAHostileNumericFieldIsRefusedByNameAtLoad():
    """Kills: dropping the typed-field check from the load validation."""
    for sField, objValue in (
        ("iNumberOfCores", S_HOSTILE),
        ("fTolerance", S_HOSTILE),
        ("fDefaultWallClockBudgetSeconds", S_HOSTILE),
    ):
        with pytest.raises(ValueError) as errorRaised:
            _fdictLoad(_fdictWorkflow(**{sField: objValue}))
        assert sField in str(errorRaised.value)
        assert "Invalid project.json" in str(errorRaised.value), (
            "the project file's own check must refuse it, before any "
            "state is merged in"
        )


@pytest.mark.parametrize("sField,objValue", [
    ("iNumberOfCores", "8"), ("iNumberOfCores", True),
    ("iNumberOfCores", 2.5), ("iNumberOfCores", [4]),
    ("fTolerance", "1e-6"), ("fTolerance", 0), ("fTolerance", -1.0),
    ("fTolerance", float("nan")), ("fTolerance", float("inf")),
    ("fDefaultWallClockBudgetSeconds", "60"),
    ("fDefaultWallClockBudgetSeconds", -5),
    ("fDefaultWallClockBudgetSeconds", True),
])
def testWrongTypedWorkflowFieldsAreNamed(sField, objValue):
    sFailure = workflowFieldTypes.fsDescribeUntypedField(
        _fdictWorkflow(**{sField: objValue}))
    assert sField in sFailure


@pytest.mark.parametrize("dictFields", [
    {}, {"iNumberOfCores": -1}, {"iNumberOfCores": 8},
    {"fTolerance": 1e-6}, {"fTolerance": 0.5},
    {"fDefaultWallClockBudgetSeconds": 0},
    {"fDefaultWallClockBudgetSeconds": 12.5},
    {"iNumberOfCores": None, "fTolerance": None},
])
def testOrdinaryValuesAndAbsenceAreAccepted(dictFields):
    assert workflowFieldTypes.fsDescribeUntypedField(
        _fdictWorkflow(**dictFields)) == ""
    assert _fdictLoad(_fdictWorkflow(**dictFields))["listSteps"]


def testAStepRuntimeBudgetMustBeANonNegativeNumber():
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"][0]["fWallClockBudgetSeconds"] = S_HOSTILE
    sFailure = workflowFieldTypes.fsDescribeUntypedField(dictWorkflow)
    assert "fWallClockBudgetSeconds" in sFailure and "Step01" in sFailure
    dictWorkflow["listSteps"][0]["fWallClockBudgetSeconds"] = 30
    assert workflowFieldTypes.fsDescribeUntypedField(dictWorkflow) == ""


@pytest.mark.parametrize("objState", [S_HOSTILE, "Passed", 5, "", "a b"])
def testAVerificationStateMustBeALowercaseWord(objState):
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"][0]["dictVerification"] = {"sUser": objState}
    sFailure = workflowFieldTypes.fsDescribeUntypedField(dictWorkflow)
    assert "dictVerification.sUser" in sFailure


@pytest.mark.parametrize("sState", [
    "untested", "passed", "failed", "outputs-changed", "passed-from-marker",
])
def testTheVocabularyWordsAreAccepted(sState):
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "sUser": sState, "sUnitTest": sState,
    }
    assert workflowFieldTypes.fsDescribeUntypedField(dictWorkflow) == ""


@pytest.mark.falsification
def testAHostileStateMergedFromStateJsonIsRefusedAfterTheMerge():
    """state.json is merged in after the first validation; check again.

    Kills: removing the post-merge check, which leaves state.json as the
    unvalidated route into the verification badge's class attribute.
    """
    def fnMergeHostileState(connectionDocker, sContainerId, dictWorkflow,
                            sRepoPath, sWorkflowPath):
        dictWorkflow["listSteps"][0]["dictVerification"] = {
            "sUser": S_HOSTILE}

    with patch.object(
        workflowManager, "_fnLoadAndMergeState", fnMergeHostileState,
    ):
        with pytest.raises(ValueError) as errorRaised:
            _fdictLoad(_fdictWorkflow())
    assert "Invalid state" in str(errorRaised.value)
    assert "dictVerification.sUser" in str(errorRaised.value)
