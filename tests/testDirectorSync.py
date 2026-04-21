"""Verify director.py and workflowManager.py shared logic stays in sync."""

from vaibify.gui.director import (
    fsResolveVariables as fsDirectorResolve,
    fbValidateWorkflow as fbDirectorValidate,
)
from vaibify.gui.workflowManager import (
    fsResolveVariables as fsManagerResolve,
    fbValidateWorkflow as fbManagerValidate,
)


def test_fsResolveVariables_produces_same_output():
    """Both resolve functions should handle known tokens identically."""
    dictVars = {"sPlotDirectory": "/workspace/Plot", "sFigureType": "pdf"}
    sTemplate = "{sPlotDirectory}/Corner.{sFigureType}"
    assert fsDirectorResolve(sTemplate, dictVars) == \
        fsManagerResolve(sTemplate, dictVars)


def test_fsResolveVariables_no_variables():
    """Both should return the original string when no variables."""
    sTemplate = "plain_string.txt"
    assert fsDirectorResolve(sTemplate, {}) == \
        fsManagerResolve(sTemplate, {})


def test_fsResolveVariables_unknown_token_divergence():
    """Director raises on unknowns (strict); manager preserves them."""
    import pytest
    sTemplate = "{unknown_var}"
    with pytest.raises(KeyError):
        fsDirectorResolve(sTemplate, {})
    assert fsManagerResolve(sTemplate, {}) == sTemplate


def test_fbValidateWorkflow_valid():
    """Both validators should accept a well-formed workflow."""
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Test",
            "sDirectory": "Test",
            "saDataCommands": ["python data.py"],
            "saDataFiles": ["output.npy"],
            "saPlotCommands": ["python plot.py out.pdf"],
            "saPlotFiles": ["out.pdf"],
            "saTestCommands": [],
        }]
    }
    assert fbDirectorValidate(dictWorkflow) is True
    assert fbManagerValidate(dictWorkflow) is True


def test_fbValidateWorkflow_missing_steps():
    """Both validators should reject a workflow without listSteps."""
    assert fbDirectorValidate({}) is False
    assert fbManagerValidate({}) is False
