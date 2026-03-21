"""Tests for untested functions in vaibify.gui.workflowManager."""

import pytest

from vaibify.gui.workflowManager import (
    fdictBuildGlobalVariables,
    fdictBuildStepVariables,
    flistExtractStepNames,
    fdictGetStep,
    fsRemapStepReferences,
    fnUpdateStep,
    fsetExtractStepReferences,
    fdictBuildStemRegistry,
    flistFilterFigureFiles,
    fdictGetSyncStatus,
    fnUpdateSyncStatus,
    flistResolveOutputFiles,
)


def _fdictBuildWorkflow():
    """Return a minimal workflow for testing."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Compute",
                "sDirectory": "compute",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["output.pdf"],
                "saDataFiles": ["data.csv"],
                "saDataCommands": [],
            },
            {
                "sName": "Visualize",
                "sDirectory": "viz",
                "saPlotCommands": ["python viz.py"],
                "saPlotFiles": ["fig.png"],
                "saDataFiles": [],
                "saDataCommands": [],
            },
        ],
    }


def test_fdictBuildGlobalVariables_keys():
    dictWorkflow = {"sPlotDirectory": "Figures"}
    sPath = "/workspace/.vaibify/workflows/w.json"
    dictVars = fdictBuildGlobalVariables(dictWorkflow, sPath)
    assert dictVars["sPlotDirectory"] == "Figures"
    assert dictVars["sRepoRoot"] == "/workspace"


def test_fdictBuildGlobalVariables_defaults():
    dictVars = fdictBuildGlobalVariables({}, "/a/b/c.json")
    assert dictVars["sPlotDirectory"] == "Plot"
    assert dictVars["iNumberOfCores"] == -1
    assert dictVars["sFigureType"] == "pdf"


def test_fdictBuildStepVariables_maps_stems():
    dictWorkflow = _fdictBuildWorkflow()
    dictGlobal = {"sRepoRoot": "/workspace"}
    dictVars = fdictBuildStepVariables(dictWorkflow, dictGlobal)
    assert "Step01.output" in dictVars
    assert "Step01.data" in dictVars
    assert "Step02.fig" in dictVars


def test_flistExtractStepNames_structure():
    dictWorkflow = _fdictBuildWorkflow()
    listNames = flistExtractStepNames(dictWorkflow)
    assert len(listNames) == 2
    assert listNames[0]["iIndex"] == 0
    assert listNames[0]["iNumber"] == 1
    assert listNames[0]["sName"] == "Compute"
    assert listNames[1]["sName"] == "Visualize"


def test_flistExtractStepNames_defaults():
    dictWorkflow = _fdictBuildWorkflow()
    listNames = flistExtractStepNames(dictWorkflow)
    assert listNames[0]["bEnabled"] is True
    assert listNames[0]["bPlotOnly"] is True


def test_fdictGetStep_valid_index():
    dictWorkflow = _fdictBuildWorkflow()
    dictStep = fdictGetStep(dictWorkflow, 0)
    assert dictStep["sName"] == "Compute"


def test_fdictGetStep_returns_copy():
    dictWorkflow = _fdictBuildWorkflow()
    dictStep = fdictGetStep(dictWorkflow, 0)
    dictStep["sName"] = "Changed"
    assert dictWorkflow["listSteps"][0]["sName"] == "Compute"


def test_fdictGetStep_invalid_index():
    dictWorkflow = _fdictBuildWorkflow()
    with pytest.raises(IndexError):
        fdictGetStep(dictWorkflow, 5)
    with pytest.raises(IndexError):
        fdictGetStep(dictWorkflow, -1)


def test_fsRemapStepReferences_remaps():
    sText = "use {Step01.output} and {Step02.fig}"
    sResult = fsRemapStepReferences(sText, lambda i: i + 1)
    assert "{Step02.output}" in sResult
    assert "{Step03.fig}" in sResult


def test_fsRemapStepReferences_no_change():
    sText = "use {Step01.output}"
    sResult = fsRemapStepReferences(sText, lambda i: i)
    assert sResult == sText


def test_fnUpdateStep_modifies():
    dictWorkflow = _fdictBuildWorkflow()
    fnUpdateStep(dictWorkflow, 0, {"sName": "NewName"})
    assert dictWorkflow["listSteps"][0]["sName"] == "NewName"


def test_fnUpdateStep_invalid_index():
    dictWorkflow = _fdictBuildWorkflow()
    with pytest.raises(IndexError):
        fnUpdateStep(dictWorkflow, 10, {"sName": "X"})


def test_fsetExtractStepReferences_finds():
    sText = "run {Step01.data} then {Step03.fig}"
    setRefs = fsetExtractStepReferences(sText)
    assert ("01", "data") in setRefs
    assert ("03", "fig") in setRefs


def test_fsetExtractStepReferences_empty():
    assert fsetExtractStepReferences("no refs") == set()


def test_fdictBuildStemRegistry_maps():
    dictWorkflow = _fdictBuildWorkflow()
    dictRegistry = fdictBuildStemRegistry(dictWorkflow)
    assert dictRegistry["Step01.output"] == 1
    assert dictRegistry["Step01.data"] == 1
    assert dictRegistry["Step02.fig"] == 2


def test_flistFilterFigureFiles_filters():
    listPaths = ["a.pdf", "b.png", "c.txt", "d.svg", "e.jpg"]
    listFigures = flistFilterFigureFiles(listPaths)
    assert "a.pdf" in listFigures
    assert "b.png" in listFigures
    assert "c.txt" not in listFigures
    assert "d.svg" in listFigures
    assert "e.jpg" in listFigures


def test_flistFilterFigureFiles_empty():
    assert flistFilterFigureFiles([]) == []


def test_fdictGetSyncStatus_default():
    dictWorkflow = {}
    assert fdictGetSyncStatus(dictWorkflow) == {}


def test_fdictGetSyncStatus_existing():
    dictWorkflow = {"dictSyncStatus": {"a.pdf": {}}}
    assert "a.pdf" in fdictGetSyncStatus(dictWorkflow)


def test_fnUpdateSyncStatus_creates():
    dictWorkflow = {}
    fnUpdateSyncStatus(dictWorkflow, ["fig.pdf"], "Overleaf")
    dictSync = dictWorkflow["dictSyncStatus"]
    assert "fig.pdf" in dictSync
    assert dictSync["fig.pdf"]["bOverleaf"] is True
    assert len(dictSync["fig.pdf"]["sOverleafTimestamp"]) > 0


def test_fnUpdateSyncStatus_multiple():
    dictWorkflow = {}
    fnUpdateSyncStatus(dictWorkflow, ["a.pdf", "b.pdf"], "Github")
    assert dictWorkflow["dictSyncStatus"]["a.pdf"]["bGithub"] is True
    assert dictWorkflow["dictSyncStatus"]["b.pdf"]["bGithub"] is True


def test_flistResolveOutputFiles_resolves():
    dictStep = {"saPlotFiles": ["{sPlotDirectory}/fig.pdf"]}
    dictVars = {"sPlotDirectory": "Figures"}
    listResolved = flistResolveOutputFiles(dictStep, dictVars)
    assert listResolved == ["Figures/fig.pdf"]


def test_flistResolveOutputFiles_empty():
    assert flistResolveOutputFiles({}, {}) == []
