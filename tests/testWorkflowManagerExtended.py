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
    assert dictVars["sPlotDirectory"] == "/workspace/Figures"
    assert dictVars["sRepoRoot"] == "/workspace"


def test_fdictBuildGlobalVariables_defaults():
    dictVars = fdictBuildGlobalVariables({}, "/a/b/c.json")
    assert dictVars["sPlotDirectory"] == "/a/b/Plot"
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


# -----------------------------------------------------------------------
# _fsExtractRepoName — container root case (line 55)
# -----------------------------------------------------------------------


def test_fsExtractRepoName_container_root():
    from vaibify.gui.workflowManager import _fsExtractRepoName
    sResult = _fsExtractRepoName(
        "/workspace/.vaibify/workflows/w.json", "/workspace",
    )
    assert sResult == "(container root)"


def test_fsExtractRepoName_repo_name():
    from vaibify.gui.workflowManager import _fsExtractRepoName
    sResult = _fsExtractRepoName(
        "/workspace/myrepo/.vaibify/workflows/w.json", "/workspace",
    )
    assert sResult == "myrepo"


# -----------------------------------------------------------------------
# _fsReadWorkflowName — exception fallback (lines 66-67)
# -----------------------------------------------------------------------


def test_fsReadWorkflowName_exception_returns_basename():
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import _fsReadWorkflowName
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = RuntimeError("fail")
    sResult = _fsReadWorkflowName(mockDocker, "cid", "/w/test.json")
    assert sResult == "test.json"


def test_fsReadWorkflowName_returns_workflow_name():
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import _fsReadWorkflowName
    dictWorkflow = {"sWorkflowName": "My Pipeline"}
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictWorkflow).encode("utf-8")
    )
    sResult = _fsReadWorkflowName(mockDocker, "cid", "/w/test.json")
    assert sResult == "My Pipeline"


# -----------------------------------------------------------------------
# fdictLoadWorkflowFromContainer (lines 75-82, 86)
# -----------------------------------------------------------------------


def test_fdictLoadWorkflowFromContainer_no_path_found():
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    with pytest.raises(FileNotFoundError, match="No workflow"):
        fdictLoadWorkflowFromContainer(mockDocker, "cid")


def test_fdictLoadWorkflowFromContainer_invalid_workflow():
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    mockDocker = MagicMock()
    dictBad = {"not_valid": True}
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictBad).encode("utf-8")
    )
    with pytest.raises(ValueError, match="Invalid"):
        fdictLoadWorkflowFromContainer(
            mockDocker, "cid", sWorkflowPath="/w.json",
        )


def test_fdictLoadWorkflowFromContainer_success():
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    mockDocker = MagicMock()
    dictValid = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "/d",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictValid).encode("utf-8")
    )
    dictResult = fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )
    assert dictResult["sPlotDirectory"] == "Plot"
    assert "dictTests" in dictResult["listSteps"][0]


# -----------------------------------------------------------------------
# fnSaveWorkflowToContainer null path (line 323)
# -----------------------------------------------------------------------


def test_fnSaveWorkflowToContainer_null_path_raises():
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    mockDocker = MagicMock()
    with pytest.raises(ValueError, match="required"):
        fnSaveWorkflowToContainer(mockDocker, "cid", {})


# -----------------------------------------------------------------------
# _fnValidateReorderIndices (lines 284, 286)
# -----------------------------------------------------------------------


def test_fnValidateReorderIndices_from_out_of_range():
    from vaibify.gui.workflowManager import _fnValidateReorderIndices
    with pytest.raises(IndexError, match="From index"):
        _fnValidateReorderIndices(-1, 0, 2)


def test_fnValidateReorderIndices_to_out_of_range():
    from vaibify.gui.workflowManager import _fnValidateReorderIndices
    with pytest.raises(IndexError, match="To index"):
        _fnValidateReorderIndices(0, 5, 2)


# -----------------------------------------------------------------------
# _fiRemapReorder (lines 293-299)
# -----------------------------------------------------------------------


def test_fiRemapReorder_forward_shift():
    from vaibify.gui.workflowManager import _fiRemapReorder
    iResult = _fiRemapReorder(2, 1, 0, 2)
    assert iResult == 1


def test_fiRemapReorder_backward_shift():
    from vaibify.gui.workflowManager import _fiRemapReorder
    iResult = _fiRemapReorder(2, 3, 2, 0)
    assert iResult == 3


def test_fiRemapReorder_moved_step():
    from vaibify.gui.workflowManager import _fiRemapReorder
    iResult = _fiRemapReorder(1, 1, 0, 2)
    assert iResult == 3


# -----------------------------------------------------------------------
# fsResolveCommand (line 425)
# -----------------------------------------------------------------------


def test_fsResolveCommand_resolves_variables():
    from vaibify.gui.workflowManager import fsResolveCommand
    sResult = fsResolveCommand(
        "python {sRepoRoot}/script.py",
        {"sRepoRoot": "/workspace"},
    )
    assert sResult == "python /workspace/script.py"


# -----------------------------------------------------------------------
# flistExtractOutputFiles (line 449)
# -----------------------------------------------------------------------


def test_flistExtractOutputFiles_returns_plot_files():
    from vaibify.gui.workflowManager import flistExtractOutputFiles
    dictStep = {"saPlotFiles": ["a.pdf", "b.png"]}
    assert flistExtractOutputFiles(dictStep) == ["a.pdf", "b.png"]


def test_flistExtractOutputFiles_empty():
    from vaibify.gui.workflowManager import flistExtractOutputFiles
    assert flistExtractOutputFiles({}) == []


# -----------------------------------------------------------------------
# fdictAutoDetectScripts (lines 537, 542)
# -----------------------------------------------------------------------


def test_fdictAutoDetectScripts_classifies_correctly():
    from vaibify.gui.workflowManager import fdictAutoDetectScripts
    dictResult = fdictAutoDetectScripts([
        "dataGenerate.py", "plotFigure.py", "README.md",
        "helper.py", "dataClean.py",
    ])
    assert "dataGenerate.py" in dictResult["listDataScripts"]
    assert "dataClean.py" in dictResult["listDataScripts"]
    assert "plotFigure.py" in dictResult["listPlotScripts"]
    assert "helper.py" not in dictResult["listDataScripts"]
    assert "helper.py" not in dictResult["listPlotScripts"]


# -----------------------------------------------------------------------
# fdictBuildDownstreamMap (line 628)
# -----------------------------------------------------------------------


def test_fdictBuildDownstreamMap_chain():
    from vaibify.gui.workflowManager import fdictBuildDownstreamMap
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A", "sDirectory": "/a",
                "saPlotCommands": ["echo"],
                "saPlotFiles": ["a.pdf"],
            },
            {
                "sName": "B", "sDirectory": "/b",
                "saPlotCommands": ["{Step01.a}"],
                "saPlotFiles": ["b.pdf"],
            },
            {
                "sName": "C", "sDirectory": "/c",
                "saPlotCommands": ["{Step02.b}"],
                "saPlotFiles": ["c.pdf"],
            },
        ],
    }
    dictDown = fdictBuildDownstreamMap(dictWorkflow)
    assert 1 in dictDown[0]
    assert 2 in dictDown[0]
    assert 2 in dictDown[1]


# -----------------------------------------------------------------------
# File categorization helpers
# -----------------------------------------------------------------------


def test_fsGetFileCategory_archive_default():
    from vaibify.gui.workflowManager import fsGetFileCategory
    dictStep = {"saPlotFiles": ["fig.pdf"]}
    assert fsGetFileCategory(dictStep, "fig.pdf") == "archive"


def test_fsGetFileCategory_from_plot_categories():
    from vaibify.gui.workflowManager import fsGetFileCategory
    dictStep = {
        "dictPlotFileCategories": {"fig.pdf": "supporting"},
    }
    assert fsGetFileCategory(dictStep, "fig.pdf") == "supporting"


def test_fsGetFileCategory_from_data_categories():
    from vaibify.gui.workflowManager import fsGetFileCategory
    dictStep = {
        "dictDataFileCategories": {"data.csv": "supporting"},
    }
    assert fsGetFileCategory(dictStep, "data.csv") == "supporting"


# -----------------------------------------------------------------------
# _fsResolveStepOutputPath — absolute path (line 425)
# -----------------------------------------------------------------------


def test_fdictBuildStepVariables_absolute_output():
    """Line 425: absolute output file path returned directly."""
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1",
            "sDirectory": "compute",
            "saPlotCommands": ["echo"],
            "saPlotFiles": ["/absolute/path/fig.pdf"],
        }],
    }
    dictGlobal = fdictBuildGlobalVariables(
        dictWorkflow, "/workspace/.vaibify/workflows/w.json",
    )
    dictStepVars = fdictBuildStepVariables(dictWorkflow, dictGlobal)
    assert dictStepVars["Step01.fig"] == "/absolute/path/fig.pdf"


# -----------------------------------------------------------------------
# flistExtractStepScripts — empty command and .py direct (lines 537, 542)
# -----------------------------------------------------------------------


def test_flistExtractStepScripts_empty_command():
    """Line 537: empty command string is skipped."""
    from vaibify.gui.workflowManager import flistExtractStepScripts
    dictStep = {"saDataCommands": ["", "python run.py"]}
    listResult = flistExtractStepScripts(dictStep)
    assert listResult == ["run.py"]


def test_flistExtractStepScripts_direct_py_script():
    """Line 542: direct .py script name extracted."""
    from vaibify.gui.workflowManager import flistExtractStepScripts
    dictStep = {"saPlotCommands": ["./plotFigure.py --arg"]}
    listResult = flistExtractStepScripts(dictStep)
    assert listResult == ["./plotFigure.py"]


# -----------------------------------------------------------------------
# fdictBuildDownstreamMap — cycle/revisit (line 628)
# -----------------------------------------------------------------------


def test_fdictBuildDownstreamMap_diamond():
    """Line 628: BFS revisits handled (diamond dependency)."""
    from vaibify.gui.workflowManager import fdictBuildDownstreamMap
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Root", "sDirectory": "/r",
                "saPlotCommands": ["echo"],
                "saPlotFiles": ["root.pdf"],
            },
            {
                "sName": "Left", "sDirectory": "/l",
                "saPlotCommands": ["{Step01.root}"],
                "saPlotFiles": ["left.pdf"],
            },
            {
                "sName": "Right", "sDirectory": "/r2",
                "saPlotCommands": ["{Step01.root}"],
                "saPlotFiles": ["right.pdf"],
            },
            {
                "sName": "Join", "sDirectory": "/j",
                "saPlotCommands": [
                    "{Step02.left} {Step03.right}",
                ],
                "saPlotFiles": ["join.pdf"],
            },
        ],
    }
    dictDown = fdictBuildDownstreamMap(dictWorkflow)
    assert 3 in dictDown[0]
    assert 3 in dictDown[1]
    assert 3 in dictDown[2]


# -----------------------------------------------------------------------
# fdictLoadWorkflowFromContainer — auto-discover path (line 82)
# -----------------------------------------------------------------------


def test_fdictLoadWorkflowFromContainer_auto_discover():
    """Line 82: auto-discovers first workflow when path is None."""
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    dictValid = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "/d",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0,
        "/workspace/repo/.vaibify/workflows/w.json\n",
    )
    sJsonContent = json.dumps(dictValid).encode("utf-8")
    sNameJson = json.dumps({"sWorkflowName": "Auto"}).encode("utf-8")
    mockDocker.fbaFetchFile.side_effect = [sNameJson, sJsonContent]
    dictResult = fdictLoadWorkflowFromContainer(mockDocker, "cid")
    assert dictResult["sPlotDirectory"] == "Plot"


# -----------------------------------------------------------------------
# fnInsertStep renumbering (line 250)
# -----------------------------------------------------------------------


def test_fnInsertStep_renumbers():
    """Line 250: downstream references incremented on insert."""
    from vaibify.gui.workflowManager import (
        fnInsertStep, fdictCreateStep,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A", "sDirectory": "/a",
                "saPlotCommands": ["echo"],
                "saPlotFiles": ["a.pdf"],
            },
            {
                "sName": "B", "sDirectory": "/b",
                "saPlotCommands": ["{Step01.a}"],
                "saPlotFiles": ["b.pdf"],
            },
        ],
    }
    dictNewStep = fdictCreateStep("New", "/new")
    fnInsertStep(dictWorkflow, 1, dictNewStep)
    assert len(dictWorkflow["listSteps"]) == 3
    sBCommand = dictWorkflow["listSteps"][2]["saPlotCommands"][0]
    assert "Step01" in sBCommand
