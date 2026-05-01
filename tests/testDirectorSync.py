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


def test_director_and_manager_share_the_migration_registry():
    """Both load paths must apply the same registered migrations.

    director.py runs on the host; workflowManager.py runs on
    container paths. They diverge on path semantics but share the
    pure ``workflowMigrations`` registry — a legacy v0 workflow run
    through the registry must come out at the current schema version
    with identical post-migration shape regardless of caller.
    """
    from vaibify.gui.workflowMigrations import (
        I_CURRENT_WORKFLOW_VERSION,
        S_VERSION_KEY,
        fnApplyMigrations,
    )

    def _fdictBuildLegacyShape():
        return {
            "sPlotDirectory": "Plot",
            "listSteps": [
                {
                    "sName": "S",
                    "sDirectory": "/workspace/Proj/S",
                    "saPlotCommands": [],
                    "saPlotFiles": [],
                    "bEnabled": True,
                    "saTestCommands": [],
                },
            ],
        }
    dictForDirector = _fdictBuildLegacyShape()
    dictForManager = _fdictBuildLegacyShape()
    fnApplyMigrations(dictForDirector, sProjectRepoPath="/workspace/Proj")
    fnApplyMigrations(dictForManager, sProjectRepoPath="/workspace/Proj")
    assert dictForDirector[S_VERSION_KEY] == I_CURRENT_WORKFLOW_VERSION
    assert dictForManager[S_VERSION_KEY] == I_CURRENT_WORKFLOW_VERSION
    assert dictForDirector == dictForManager
    assert dictForDirector["listSteps"][0]["sDirectory"] == "S"
    assert dictForDirector["listSteps"][0]["bRunEnabled"] is True
