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
    _flistResolveOutputPaths,
    fdictBuildImplicitDependencies,
    fdictBuildDirectDependencies,
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
    assert listNames[0]["bRunEnabled"] is True
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


def test_fnUpdateSyncStatus_normalizes_absolute_to_repo_relative():
    from vaibify.gui.workflowManager import fnUpdateOverleafDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateSyncStatus(
        dictWorkflow,
        ["/workspace/Proj/Plot/fig.pdf"],
        "Overleaf",
    )
    assert "Plot/fig.pdf" in dictWorkflow["dictSyncStatus"]
    assert "/workspace/Proj/Plot/fig.pdf" not in (
        dictWorkflow["dictSyncStatus"])


def test_fnUpdateOverleafDigests_keys_are_repo_relative():
    from vaibify.gui.workflowManager import fnUpdateOverleafDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateOverleafDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "abc123"},
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert dictEntry["sOverleafLastPushedDigest"] == "abc123"


def test_fnUpdateZenodoDigests_records_blob_sha():
    from vaibify.gui.workflowManager import fnUpdateZenodoDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "def456"},
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert dictEntry["sZenodoLastPushedDigest"] == "def456"
    assert dictEntry["sOverleafLastPushedDigest"] == ""


def test_fnUpdateZenodoDigests_skips_empty_digests():
    from vaibify.gui.workflowManager import fnUpdateZenodoDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": ""},
    )
    assert dictWorkflow.get("dictSyncStatus", {}) == {}


def test_fnUpdateZenodoDigests_records_explicit_endpoint():
    """An explicit sZenodoService stamps sZenodoLastPushedEndpoint."""
    from vaibify.gui.workflowManager import fnUpdateZenodoDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "def456"},
        sZenodoService="sandbox",
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert dictEntry["sZenodoLastPushedEndpoint"] == "sandbox"


def test_fnUpdateZenodoDigests_falls_back_to_workflow_service():
    """When sZenodoService is omitted, fall back to dictWorkflow value."""
    from vaibify.gui.workflowManager import fnUpdateZenodoDigests
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/Proj",
        "sZenodoService": "zenodo",
    }
    fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "def456"},
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert dictEntry["sZenodoLastPushedEndpoint"] == "zenodo"


def test_fnUpdateZenodoDigests_overwrites_endpoint_on_resync():
    """A subsequent push to a different endpoint updates the field."""
    from vaibify.gui.workflowManager import fnUpdateZenodoDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "abc123"},
        sZenodoService="sandbox",
    )
    fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "def456"},
        sZenodoService="zenodo",
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert dictEntry["sZenodoLastPushedDigest"] == "def456"
    assert dictEntry["sZenodoLastPushedEndpoint"] == "zenodo"


def test_fnUpdateOverleafDigests_does_not_write_endpoint_field():
    """Overleaf has no endpoint split, so the field must not appear."""
    from vaibify.gui.workflowManager import fnUpdateOverleafDigests
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnUpdateOverleafDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "abc123"},
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert "sOverleafLastPushedEndpoint" not in dictEntry


def test_fdictLookupSyncEntry_matches_repo_rel_first():
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    dictSync = {"Plot/fig.pdf": {"bGithub": True}}
    assert fdictLookupSyncEntry(
        dictSync, "Plot/fig.pdf", "/workspace/Proj",
    ) == {"bGithub": True}


def test_fdictLookupSyncEntry_falls_back_to_project_absolute():
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    dictSync = {
        "/workspace/Proj/Plot/fig.pdf": {"bOverleaf": True},
    }
    assert fdictLookupSyncEntry(
        dictSync, "Plot/fig.pdf", "/workspace/Proj",
    ) == {"bOverleaf": True}


def test_fdictLookupSyncEntry_returns_empty_when_missing():
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    assert fdictLookupSyncEntry({}, "Plot/fig.pdf", "") == {}


def test_fnSetServiceTracking_enables_and_disables_overleaf():
    from vaibify.gui.workflowManager import fnSetServiceTracking
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    fnSetServiceTracking(
        dictWorkflow, "/workspace/Proj/Plot/fig.pdf", "Overleaf", True,
    )
    dictEntry = dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"]
    assert dictEntry["bOverleaf"] is True
    fnSetServiceTracking(
        dictWorkflow, "/workspace/Proj/Plot/fig.pdf", "Overleaf", False,
    )
    assert dictWorkflow["dictSyncStatus"]["Plot/fig.pdf"][
        "bOverleaf"] is False


def test_fnMigrateArchiveToTracking_seeds_flags_for_archive_files():
    from vaibify.gui.workflowManager import fnMigrateArchiveToTracking
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/Proj",
        "listSteps": [
            {
                "sDirectory": "StepA",
                "saPlotFiles": ["a.pdf"],
                "saDataFiles": ["a.h5"],
                "dictPlotFileCategories": {"a.pdf": "archive"},
                "dictDataFileCategories": {"a.h5": "supporting"},
            },
        ],
    }
    assert fnMigrateArchiveToTracking(dictWorkflow) is True
    dictSync = dictWorkflow["dictSyncStatus"]
    assert dictSync["StepA/a.pdf"]["bOverleaf"] is True
    assert dictSync["StepA/a.pdf"]["bZenodo"] is True
    assert "StepA/a.h5" not in dictSync


def test_fnMigrateArchiveToTracking_runs_only_once():
    from vaibify.gui.workflowManager import fnMigrateArchiveToTracking
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/Proj",
        "listSteps": [
            {"sDirectory": "X", "saPlotFiles": ["a.pdf"]},
        ],
    }
    assert fnMigrateArchiveToTracking(dictWorkflow) is True
    dictWorkflow["dictSyncStatus"]["X/a.pdf"]["bOverleaf"] = False
    assert fnMigrateArchiveToTracking(dictWorkflow) is False
    assert dictWorkflow["dictSyncStatus"]["X/a.pdf"][
        "bOverleaf"] is False


def test_flistResolveOutputFiles_resolves():
    dictStep = {"saPlotFiles": ["{sPlotDirectory}/fig.pdf"]}
    dictVars = {"sPlotDirectory": "Figures"}
    listResolved = flistResolveOutputFiles(dictStep, dictVars)
    assert listResolved == ["Figures/fig.pdf"]


def test_flistResolveOutputFiles_empty():
    assert flistResolveOutputFiles({}, {}) == []


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
            "sName": "S1", "sDirectory": "d",
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
    assert dictResult["listSteps"][0]["sLabel"] == "A01"


def test_fdictLoadWorkflowFromContainer_preserves_dictRandomnessLint():
    """Workflow load passes dictRandomnessLint through unchanged.

    dictRandomnessLint is an optional top-level field that the
    randomness lint reads. Loader must not strip or rename it; the
    schema validator should silently allow unknown top-level fields.
    """
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    mockDocker = MagicMock()
    dictValid = {
        "sPlotDirectory": "Plot",
        "dictRandomnessLint": {
            "sConfigGlob": "*.in",
            "sSeedRegex": r"^seed\s+\d+",
        },
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictValid).encode("utf-8")
    )
    dictResult = fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )
    assert "dictRandomnessLint" in dictResult
    assert dictResult["dictRandomnessLint"]["sConfigGlob"] == "*.in"
    assert (
        dictResult["dictRandomnessLint"]["sSeedRegex"]
        == r"^seed\s+\d+"
    )


def test_fnSaveWorkflowToContainer_attaches_slabel_in_memory():
    """sLabel is recomputed on the in-memory dict but not persisted.

    The merged in-memory dict carries sLabel for the dashboard's
    use; the persisted workflow.json does not (post-W3 the field is
    transient — see workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    >= 3).
    """
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    mockDocker = MagicMock()
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "Intro", "bInteractive": True},
            {"sName": "Run"},
        ],
    }
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow,
        sWorkflowPath="/w.json",
    )
    assert dictWorkflow["listSteps"][0]["sLabel"] == "I01"
    assert dictWorkflow["listSteps"][1]["sLabel"] == "A01"
    (_, _, baPayload), _ = mockDocker.fnWriteFile.call_args
    dictWritten = json.loads(baPayload.decode("utf-8"))
    assert "sLabel" not in dictWritten["listSteps"][0]
    assert "sLabel" not in dictWritten["listSteps"][1]


def test_fnSaveWorkflowToContainer_clears_stale_slabel_from_disk():
    """Stale sLabel from a prior write is dropped on next save."""
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    mockDocker = MagicMock()
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "Intro", "bInteractive": True, "sLabel": "STALE"},
            {"sName": "Run", "sLabel": "ALSO_STALE"},
        ],
    }
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow,
        sWorkflowPath="/w.json",
    )
    (_, _, baPayload), _ = mockDocker.fnWriteFile.call_args
    dictWritten = json.loads(baPayload.decode("utf-8"))
    for dictStep in dictWritten["listSteps"]:
        assert "sLabel" not in dictStep


# -----------------------------------------------------------------------
# fnAttachComputedTrackedPaths — script + standards arrays for badges
# -----------------------------------------------------------------------


def test_fnAttachComputedTrackedPaths_extracts_scripts_and_standards():
    """saStepScripts and saTestStandards carry canonical repo-relative paths."""
    from vaibify.gui.workflowManager import fnAttachComputedTrackedPaths
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "S1", "sDirectory": "d",
                "saDataCommands": ["python data.py --flag"],
                "saPlotCommands": ["python3 plot.py"],
                "saPlotFiles": ["f.pdf"],
                "dictTests": {
                    "dictQualitative": {
                        "sStandardsPath": "d/tests/qual.json",
                    },
                    "dictQuantitative": {
                        "sStandardsPath": "d/tests/quant.json",
                    },
                },
            },
        ],
    }
    fnAttachComputedTrackedPaths(dictWorkflow)
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["saStepScripts"] == ["d/data.py", "d/plot.py"]
    assert dictStep["saTestStandards"] == [
        "d/tests/qual.json", "d/tests/quant.json",
    ]


def test_fnAttachComputedTrackedPaths_matches_state_contract_canonical():
    """Computed paths match stateContract — single source of truth."""
    from vaibify.gui.workflowManager import fnAttachComputedTrackedPaths
    from vaibify.gui import stateContract
    dictStep = {
        "sName": "S1", "sDirectory": "analysis",
        "saDataCommands": ["python compute.py"],
        "saPlotCommands": ["python3 plot.py --opt"],
        "saPlotFiles": ["f.pdf"],
        "dictTests": {
            "dictQuantitative": {
                "sStandardsPath": "analysis/tests/quant.json",
            },
        },
    }
    dictWorkflow = {"listSteps": [dictStep]}
    fnAttachComputedTrackedPaths(dictWorkflow)
    dictAttached = dictWorkflow["listSteps"][0]
    assert dictAttached["saStepScripts"] == (
        stateContract._flistStepScriptRepoPaths(dictStep)
    )
    assert dictAttached["saTestStandards"] == (
        stateContract._flistStepStandardsRepoPaths(dictStep)
    )


def test_fnAttachComputedTrackedPaths_handles_empty_step():
    """Steps with no commands or tests still get empty arrays attached."""
    from vaibify.gui.workflowManager import fnAttachComputedTrackedPaths
    dictWorkflow = {
        "listSteps": [
            {"sName": "Empty", "sDirectory": "d"},
        ],
    }
    fnAttachComputedTrackedPaths(dictWorkflow)
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["saStepScripts"] == []
    assert dictStep["saTestStandards"] == []


def test_fdictLoadWorkflowFromContainer_attaches_computed_tracked_paths():
    """Workflow load attaches saStepScripts and saTestStandards to each step."""
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fdictLoadWorkflowFromContainer
    mockDocker = MagicMock()
    dictValid = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saDataCommands": ["python compute.py"],
            "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["f.pdf"],
            "dictTests": {
                "dictQuantitative": {
                    "sStandardsPath": "d/tests/quant.json",
                },
            },
        }],
    }
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictValid).encode("utf-8")
    )
    dictResult = fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )
    dictStep = dictResult["listSteps"][0]
    assert dictStep["saStepScripts"] == ["d/compute.py", "d/plot.py"]
    assert dictStep["saTestStandards"] == ["d/tests/quant.json"]


def test_fnSaveWorkflowToContainer_strips_computed_tracked_paths():
    """saStepScripts and saTestStandards never persist to workflow.json."""
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    mockDocker = MagicMock()
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["f.pdf"],
            "saStepScripts": ["plot.py"],
            "saTestStandards": ["tests/quant.json"],
        }],
    }
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow,
        sWorkflowPath="/w.json",
    )
    (_, _, baPayload), _ = mockDocker.fnWriteFile.call_args
    dictWritten = json.loads(baPayload.decode("utf-8"))
    dictStepOut = dictWritten["listSteps"][0]
    assert "saStepScripts" not in dictStepOut
    assert "saTestStandards" not in dictStepOut


def test_load_save_reload_save_round_trip_is_idempotent():
    """Two full load-save cycles produce byte-identical workflow.json.

    Pins the contract that computed badge caches (saStepScripts,
    saTestStandards) attach on load and strip on save without
    accumulating, duplicating, or drifting across cycles. Also
    re-attaches after a between-cycle command edit and confirms the
    derived list tracks the edit.
    """
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer, fnSaveWorkflowToContainer,
    )
    dictPersisted = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saDataCommands": ["python compute.py"],
            "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["f.pdf"],
            "dictTests": {
                "dictQuantitative": {
                    "sStandardsPath": "d/tests/quant.json",
                },
            },
        }],
    }
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictPersisted).encode("utf-8")
    )
    dictLoaded = fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )
    assert dictLoaded["listSteps"][0]["saStepScripts"] == [
        "d/compute.py", "d/plot.py",
    ]
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictLoaded, sWorkflowPath="/w.json",
    )
    (_, _, baFirst), _ = mockDocker.fnWriteFile.call_args
    mockDocker.fbaFetchFile.return_value = baFirst
    dictReloaded = fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictReloaded, sWorkflowPath="/w.json",
    )
    (_, _, baSecond), _ = mockDocker.fnWriteFile.call_args
    assert baFirst == baSecond
    dictReloaded["listSteps"][0]["saDataCommands"] = [
        "python newCompute.py",
    ]
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictReloaded, sWorkflowPath="/w.json",
    )
    (_, _, baThird), _ = mockDocker.fnWriteFile.call_args
    mockDocker.fbaFetchFile.return_value = baThird
    dictAfterEdit = fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )
    assert dictAfterEdit["listSteps"][0]["saStepScripts"] == [
        "d/newCompute.py", "d/plot.py",
    ]


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
                "sName": "A", "sDirectory": "a",
                "saPlotCommands": ["echo"],
                "saPlotFiles": ["a.pdf"],
            },
            {
                "sName": "B", "sDirectory": "b",
                "saPlotCommands": ["{Step01.a}"],
                "saPlotFiles": ["b.pdf"],
            },
            {
                "sName": "C", "sDirectory": "c",
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
# _flistResolveOutputPaths
# -----------------------------------------------------------------------


def test_flistResolveOutputPaths_basic():
    dictStep = {
        "sDirectory": "step01",
        "saDataFiles": ["results.csv"],
        "saPlotFiles": ["fig.pdf"],
    }
    listPaths = _flistResolveOutputPaths(dictStep)
    assert "step01/results.csv" in listPaths
    assert "step01/fig.pdf" in listPaths


def test_flistResolveOutputPaths_skips_templates():
    dictStep = {
        "sDirectory": "step01",
        "saPlotFiles": ["{sPlotDirectory}/fig.pdf", "local.png"],
    }
    listPaths = _flistResolveOutputPaths(dictStep)
    assert len(listPaths) == 1
    assert "local.png" in listPaths[0]


def test_flistResolveOutputPaths_empty_directory():
    dictStep = {"sDirectory": "", "saDataFiles": ["data.csv"]}
    assert _flistResolveOutputPaths(dictStep) == []


# -----------------------------------------------------------------------
# fdictBuildImplicitDependencies
# -----------------------------------------------------------------------


def test_fdictBuildImplicitDependencies_shared_directory():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Produce",
                "sDirectory": "/workspace/analysis/sub",
                "saDataFiles": ["output.csv"],
            },
            {
                "sName": "Consume",
                "sDirectory": "/workspace/analysis",
                "saDataFiles": [],
            },
        ],
    }
    dictImplicit = fdictBuildImplicitDependencies(dictWorkflow)
    assert 0 in dictImplicit
    assert 1 in dictImplicit[0]


def test_fdictBuildImplicitDependencies_no_overlap():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "/workspace/alpha",
                "saDataFiles": ["a.csv"],
            },
            {
                "sName": "B",
                "sDirectory": "/workspace/beta",
                "saDataFiles": [],
            },
        ],
    }
    dictImplicit = fdictBuildImplicitDependencies(dictWorkflow)
    assert dictImplicit == {}


def test_fdictBuildImplicitDependencies_template_excluded():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "step01",
                "saPlotFiles": ["{sPlotDirectory}/fig.pdf"],
            },
            {
                "sName": "B",
                "sDirectory": ".",
                "saDataFiles": [],
            },
        ],
    }
    dictImplicit = fdictBuildImplicitDependencies(dictWorkflow)
    assert dictImplicit == {}


# -----------------------------------------------------------------------
# fdictBuildDirectDependencies (integration: explicit + implicit)
# -----------------------------------------------------------------------


def test_fdictBuildDirectDependencies_includes_implicit():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Upstream",
                "sDirectory": "/workspace/shared/sub",
                "saDataFiles": ["data.csv"],
                "saPlotFiles": [],
                "saPlotCommands": [],
                "saDataCommands": [],
            },
            {
                "sName": "Explicit",
                "sDirectory": "/workspace/other",
                "saDataCommands": ["{Step01.data}"],
                "saPlotCommands": [],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
            {
                "sName": "Implicit",
                "sDirectory": "/workspace/shared",
                "saDataCommands": [],
                "saPlotCommands": [],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
        ],
    }
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    assert 1 in dictDirect.get(0, set())
    assert 2 in dictDirect.get(0, set())


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
                "sName": "Root", "sDirectory": "r",
                "saPlotCommands": ["echo"],
                "saPlotFiles": ["root.pdf"],
            },
            {
                "sName": "Left", "sDirectory": "l",
                "saPlotCommands": ["{Step01.root}"],
                "saPlotFiles": ["left.pdf"],
            },
            {
                "sName": "Right", "sDirectory": "/r2",
                "saPlotCommands": ["{Step01.root}"],
                "saPlotFiles": ["right.pdf"],
            },
            {
                "sName": "Join", "sDirectory": "j",
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
    """Auto-discovers first workflow when path is None."""
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    dictValid = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }
    sWorkflowPath = "/workspace/repo/.vaibify/workflows/w.json"
    sProbeOutput = json.dumps({sWorkflowPath: "/workspace/repo"}) + "\n"

    def _fExecuteCommand(sContainerId, sCommand, **_kwargs):
        if sCommand.startswith("find "):
            return (0, sWorkflowPath + "\n")
        if sCommand.startswith("python3 -c "):
            return (0, sProbeOutput)
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExecuteCommand
    sJsonContent = json.dumps(dictValid).encode("utf-8")
    sNameJson = json.dumps({"sWorkflowName": "Auto"}).encode("utf-8")

    def _fFetchFile(sContainerId, sPath):
        if sPath == sWorkflowPath:
            return sJsonContent
        if sPath.endswith(".vaibify/workflows/w.json"):
            return sNameJson
        raise FileNotFoundError(sPath)

    mockDocker.fbaFetchFile.side_effect = _fFetchFile
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
                "sName": "A", "sDirectory": "a",
                "saPlotCommands": ["echo"],
                "saPlotFiles": ["a.pdf"],
            },
            {
                "sName": "B", "sDirectory": "b",
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


# -----------------------------------------------------------------------
# Manual step dependency token: {StepNN.manual}
# -----------------------------------------------------------------------


def test_fsetExtractUpstreamIndices_manual_token():
    """Manual dep token {Step01.manual} is detected as an upstream dep."""
    from vaibify.gui.workflowManager import fsetExtractUpstreamIndices
    setResult = fsetExtractUpstreamIndices("{Step01.manual}")
    assert setResult == {0}


def test_fdictBuildDirectDependencies_manual_token():
    """saDependencies with {Step01.manual} creates a dependency edge."""
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "A", "sDirectory": "dirA",
             "saPlotCommands": [], "saPlotFiles": []},
            {"sName": "B", "sDirectory": "dirB",
             "saPlotCommands": [], "saPlotFiles": [],
             "saDependencies": ["{Step01.manual}"]},
        ],
    }
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    assert 1 in dictDirect.get(0, set())


# ----------------------------------------------------------------------
# Zenodo metadata helpers (Phase 2)
# ----------------------------------------------------------------------


def test_fdictInitializeZenodoMetadata_shape():
    from vaibify.gui.workflowManager import (
        fdictInitializeZenodoMetadata,
    )
    dictMeta = fdictInitializeZenodoMetadata()
    assert dictMeta["sTitle"] == ""
    assert dictMeta["sLicense"] == "CC-BY-4.0"
    assert dictMeta["listCreators"] == [
        {"sName": "", "sAffiliation": "", "sOrcid": ""},
    ]
    assert dictMeta["listKeywords"] == []


def test_fdictGetZenodoMetadata_returns_default_when_absent():
    from vaibify.gui.workflowManager import fdictGetZenodoMetadata
    dictMeta = fdictGetZenodoMetadata({})
    assert dictMeta["sTitle"] == ""
    assert dictMeta["sLicense"] == "CC-BY-4.0"


def test_fdictGetZenodoMetadata_returns_stored_value():
    from vaibify.gui.workflowManager import fdictGetZenodoMetadata
    dictStored = {
        "sTitle": "X", "sDescription": "",
        "listCreators": [{"sName": "N"}],
        "sLicense": "MIT",
        "listKeywords": [], "sRelatedGithubUrl": "",
    }
    dictMeta = fdictGetZenodoMetadata(
        {"dictZenodoMetadata": dictStored})
    assert dictMeta["sTitle"] == "X"
    assert dictMeta["sLicense"] == "MIT"


def test_fnSetZenodoMetadata_writes_normalized():
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    dictWf = {}
    fnSetZenodoMetadata(dictWf, {
        "sTitle": "  Title  ",
        "sDescription": "  desc  ",
        "listCreators": [
            {"sName": "  Jane  ", "sAffiliation": " UW "},
            {"sName": ""},  # dropped
        ],
        "sLicense": "MIT",
        "listKeywords": ["  a  ", "", "b"],
        "sRelatedGithubUrl": "  https://github.com/u/r  ",
    })
    dictStored = dictWf["dictZenodoMetadata"]
    assert dictStored["sTitle"] == "Title"
    assert dictStored["sDescription"] == "desc"
    assert dictStored["listCreators"] == [
        {"sName": "Jane", "sAffiliation": "UW", "sOrcid": ""},
    ]
    assert dictStored["listKeywords"] == ["a", "b"]
    assert dictStored["sRelatedGithubUrl"] == (
        "https://github.com/u/r"
    )


def test_fnSetZenodoMetadata_requires_title():
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    with pytest.raises(ValueError, match="Title"):
        fnSetZenodoMetadata({}, {
            "sTitle": "",
            "listCreators": [{"sName": "Jane"}],
            "sLicense": "MIT",
        })


def test_fnSetZenodoMetadata_requires_at_least_one_creator():
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    with pytest.raises(ValueError, match="creator"):
        fnSetZenodoMetadata({}, {
            "sTitle": "X",
            "listCreators": [{"sName": ""}, {"sName": "   "}],
            "sLicense": "MIT",
        })


def test_fnSetZenodoMetadata_requires_license():
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    with pytest.raises(ValueError, match="License"):
        fnSetZenodoMetadata({}, {
            "sTitle": "X",
            "listCreators": [{"sName": "Jane"}],
            "sLicense": "",
        })


def test_fnSetZenodoMetadata_rejects_non_http_related_url():
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    with pytest.raises(ValueError, match="Related URL"):
        fnSetZenodoMetadata({}, {
            "sTitle": "X",
            "listCreators": [{"sName": "Jane"}],
            "sLicense": "MIT",
            "sRelatedGithubUrl": "ftp://example.com/repo",
        })


def test_fnSetZenodoMetadata_accepts_http_and_https():
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    for sUrl in (
        "http://example.com/repo",
        "https://github.com/u/r",
    ):
        dictWf = {}
        fnSetZenodoMetadata(dictWf, {
            "sTitle": "X",
            "listCreators": [{"sName": "Jane"}],
            "sLicense": "MIT",
            "sRelatedGithubUrl": sUrl,
        })
        assert dictWf["dictZenodoMetadata"]["sRelatedGithubUrl"] == sUrl


# ----------------------------------------------------------------------
# Non-Zenodo coverage gaps
# ----------------------------------------------------------------------


def test_fdictDetectReposForCandidates_non_zero_exit_returns_empty():
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        _fdictDetectReposForCandidates,
    )
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        1, "permission denied",
    )
    dictResult = _fdictDetectReposForCandidates(
        mockDocker, "cid", ["/workspace/a.json"],
    )
    assert dictResult == {}


def test_fbValidateWorkflow_rejects_absolute_output_paths():
    """flistValidateOutputFilePaths returning warnings => False."""
    from vaibify.gui.workflowManager import fbValidateWorkflow
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "A", "sDirectory": "step01",
            "saPlotCommands": [], "saPlotFiles": [],
            "saDataFiles": ["/etc/passwd"],
        }],
    }
    assert fbValidateWorkflow(dictWorkflow) is False


def test_fbValidateWorkflow_rejects_traversal_step_directories():
    """flistValidateStepDirectories returning warnings => False."""
    from vaibify.gui.workflowManager import fbValidateWorkflow
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "A", "sDirectory": "../escape",
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    assert fbValidateWorkflow(dictWorkflow) is False


def test_fnInsertStep_renumbers_downstream_references():
    """References to Step02 become Step03 when inserting at position 1."""
    from vaibify.gui.workflowManager import fnInsertStep
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A", "sDirectory": "a",
                "saPlotCommands": [], "saPlotFiles": [],
                "saDataFiles": [], "saTestCommands": [],
                "saDataCommands": [],
            },
            {
                "sName": "B", "sDirectory": "b",
                "saPlotCommands": [], "saPlotFiles": [],
                "saDataFiles": [], "saTestCommands": [],
                "saDataCommands": ["use {Step2.saDataFiles[0]}"],
            },
        ],
    }
    fnInsertStep(dictWorkflow, 1, {
        "sName": "NEW", "sDirectory": "new",
        "saPlotCommands": [], "saPlotFiles": [],
        "saDataFiles": [], "saTestCommands": [],
        "saDataCommands": [],
    })
    sCmd = dictWorkflow["listSteps"][2]["saDataCommands"][0]
    # Remap normalizes to zero-padded format (Step03)
    assert "{Step03.saDataFiles[0]}" in sCmd


def test_fnDeleteStep_renumbers_downstream_references():
    """References split across the deleted step: upstream stays, downstream shifts."""
    from vaibify.gui.workflowManager import fnDeleteStep
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A", "sDirectory": "a",
                "saPlotCommands": [], "saPlotFiles": [],
                "saDataFiles": ["a.dat"], "saTestCommands": [],
                "saDataCommands": [],
            },
            {
                "sName": "B", "sDirectory": "b",
                "saPlotCommands": [], "saPlotFiles": [],
                "saDataFiles": [], "saTestCommands": [],
                "saDataCommands": [],
            },
            {
                "sName": "C", "sDirectory": "c",
                "saPlotCommands": [], "saPlotFiles": [],
                "saDataFiles": [], "saTestCommands": [],
                "saDataCommands": [
                    "use {Step1.saDataFiles[0]} + {Step3.sName}",
                ],
            },
        ],
    }
    fnDeleteStep(dictWorkflow, 1)
    sCmd = dictWorkflow["listSteps"][1]["saDataCommands"][0]
    # Downstream ref (Step3) shifts to Step02; upstream ref (Step1)
    # stays as-is (remap returned the original number untouched).
    assert "{Step02.sName}" in sCmd
    assert "{Step1.saDataFiles[0]}" in sCmd


def test_fiRemapReorder_unrelated_number_unchanged():
    """Fall-through: a step number untouched by either reorder endpoint."""
    from vaibify.gui.workflowManager import _fiRemapReorder
    # Move step from index 2 (number 3) to index 4 (target 5).
    # Step number 1 is before the swap range and shouldn't shift.
    iResult = _fiRemapReorder(1, 3, 2, 4)
    assert iResult == 1


def test_fiRemapReorder_same_position_unchanged():
    from vaibify.gui.workflowManager import _fiRemapReorder
    # iFromIndex == iToIndex: fall through, return unchanged.
    iResult = _fiRemapReorder(2, 1, 0, 0)
    assert iResult == 2


def test_fsToSyncStatusKey_empty_path_returns_empty():
    from vaibify.gui.workflowManager import fsToSyncStatusKey
    assert fsToSyncStatusKey("", "/workspace/repo") == ""


def test_fsToSyncStatusKey_no_project_repo_returns_path():
    from vaibify.gui.workflowManager import fsToSyncStatusKey
    assert fsToSyncStatusKey("step01/out.dat", "") == (
        "step01/out.dat"
    )


def test_fsToSyncStatusKey_path_without_prefix_returns_unchanged():
    from vaibify.gui.workflowManager import fsToSyncStatusKey
    assert fsToSyncStatusKey(
        "/other/place/out.dat", "/workspace/repo",
    ) == "/other/place/out.dat"


def test_fdictLookupSyncEntry_container_absolute_key_hit():
    """Legacy key shape '/workspace/<rel>' still resolves."""
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    dictSync = {"/workspace/step01/out.dat": {"bZenodo": True}}
    dictEntry = fdictLookupSyncEntry(dictSync, "step01/out.dat")
    assert dictEntry == {"bZenodo": True}


def test_fdictLookupSyncEntry_leading_slash_key_hit():
    """Legacy key shape '/<rel>' also resolves."""
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    dictSync = {"/step01/out.dat": {"bGithub": True}}
    dictEntry = fdictLookupSyncEntry(dictSync, "step01/out.dat")
    assert dictEntry == {"bGithub": True}


def test_fdictLookupSyncEntry_project_absolute_key_hit():
    """Project-absolute key resolves when sProjectRepoPath is given."""
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    dictSync = {"/workspace/repo/step01/out.dat": {"bOverleaf": True}}
    dictEntry = fdictLookupSyncEntry(
        dictSync, "step01/out.dat", "/workspace/repo",
    )
    assert dictEntry == {"bOverleaf": True}


def test_fdictLookupSyncEntry_miss_returns_empty_dict():
    from vaibify.gui.workflowManager import fdictLookupSyncEntry
    dictEntry = fdictLookupSyncEntry(
        {"other/path": {"x": 1}}, "step01/out.dat",
    )
    assert dictEntry == {}


def test_fsJoinRepoRelPath_empty_step_dir_returns_file():
    from vaibify.gui.workflowMigrations import _fsJoinRepoRelPath
    assert _fsJoinRepoRelPath("", "out.dat") == "out.dat"


def test_fsJoinRepoRelPath_absolute_file_returns_file():
    """Absolute files ignore the step directory."""
    from vaibify.gui.workflowMigrations import _fsJoinRepoRelPath
    assert _fsJoinRepoRelPath(
        "step01", "/absolute/path.dat",
    ) == "/absolute/path.dat"


def test_fsJoinRepoRelPath_joins_relative_file_to_step_dir():
    from vaibify.gui.workflowMigrations import _fsJoinRepoRelPath
    assert _fsJoinRepoRelPath(
        "step01", "out.dat",
    ) == "step01/out.dat"
