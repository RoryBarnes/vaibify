"""Tests for vaibify.gui.workflowManager step CRUD and references."""

import pytest

from vaibify.gui.workflowManager import (
    fbValidateWorkflow,
    fsResolveStepWorkdir,
    fsResolveVariables,
    fdictCreateStep,
    fbStepRequiresTests,
    fnInsertStep,
    fnDeleteStep,
    fnReorderStep,
    flistValidateReferences,
    flistFindWorkflowsInContainer,
    fsCamelCaseDirectory,
    flistExtractStepScripts,
    fdictBuildStepDirectoryMap,
    DEFAULT_SEARCH_ROOT,
)


def _fdictBuildMinimalWorkflow(iStepCount=2):
    """Return a valid workflow dict with iStepCount simple steps."""
    listSteps = []
    for iIndex in range(iStepCount):
        listSteps.append({
            "sName": f"Step {iIndex + 1}",
            "sDirectory": f"step{iIndex + 1}",
            "saPlotCommands": [f"python run{iIndex + 1}.py"],
            "saPlotFiles": ["output.pdf"],
        })
    return {
        "sPlotDirectory": "Plot",
        "listSteps": listSteps,
    }


def test_fbValidateWorkflow_valid():
    dictWorkflow = _fdictBuildMinimalWorkflow()
    assert fbValidateWorkflow(dictWorkflow) is True


def test_fbValidateWorkflow_missing_keys():
    dictMissingPlotDir = {"listSteps": []}
    assert fbValidateWorkflow(dictMissingPlotDir) is False

    dictMissingSteps = {"sPlotDirectory": "Plot"}
    assert fbValidateWorkflow(dictMissingSteps) is False

    dictMissingStepField = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "/tmp",
                "saPlotCommands": ["echo hi"],
            }
        ],
    }
    assert fbValidateWorkflow(dictMissingStepField) is False


def test_fsResolveVariables_replaces_tokens():
    sTemplate = "cp {sPlotDirectory}/{sFileName} /output/"
    dictVariables = {
        "sPlotDirectory": "Plot",
        "sFileName": "figure.pdf",
    }

    sResolved = fsResolveVariables(sTemplate, dictVariables)

    assert sResolved == "cp Plot/figure.pdf /output/"


def test_fsResolveVariables_leaves_unknown_tokens():
    sTemplate = "cp {sKnown}/{sUnknown} /out/"
    dictVariables = {"sKnown": "data"}

    sResolved = fsResolveVariables(sTemplate, dictVariables)

    assert sResolved == "cp data/{sUnknown} /out/"


def test_fsResolveStepWorkdir_joins_relative_onto_repo_root():
    sResolved = fsResolveStepWorkdir(
        "StepDir", {"sRepoRoot": "/workspace/Proj"},
    )
    assert sResolved == "/workspace/Proj/StepDir"


def test_fsResolveStepWorkdir_preserves_absolute():
    sResolved = fsResolveStepWorkdir(
        "/workspace/Proj/StepDir", {"sRepoRoot": "/workspace/Other"},
    )
    assert sResolved == "/workspace/Proj/StepDir"


def test_fsResolveStepWorkdir_empty_inputs():
    assert fsResolveStepWorkdir("", {"sRepoRoot": "/workspace/Proj"}) == ""
    assert fsResolveStepWorkdir("StepDir", {}) == "StepDir"
    assert fsResolveStepWorkdir("StepDir", None) == "StepDir"


def test_fdictCreateStep_returns_valid_dict():
    dictStep = fdictCreateStep(
        sName="TestStep",
        sDirectory="/workspace/test",
        bPlotOnly=False,
        saDataCommands=["make"],
        saPlotCommands=["python plot.py"],
        saPlotFiles=["output.pdf"],
    )

    assert dictStep["sName"] == "TestStep"
    assert dictStep["sDirectory"] == "/workspace/test"
    assert dictStep["bEnabled"] is True
    assert dictStep["bPlotOnly"] is False
    assert dictStep["saDataCommands"] == ["make"]
    assert dictStep["saPlotCommands"] == ["python plot.py"]
    assert dictStep["saPlotFiles"] == ["output.pdf"]


def test_fdictCreateStep_defaults():
    dictStep = fdictCreateStep(
        sName="MinimalStep",
        sDirectory="/workspace/min",
    )

    assert dictStep["bPlotOnly"] is True
    assert dictStep["saDataCommands"] == []
    assert dictStep["saPlotCommands"] == []
    assert dictStep["saPlotFiles"] == []


def test_fnInsertStep_renumbers_references():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "/workspace/s1",
                "saPlotCommands": ["python s1.py"],
                "saPlotFiles": ["/workspace/s1/out.pdf"],
            },
            {
                "sName": "Step 2",
                "sDirectory": "/workspace/s2",
                "saPlotCommands": [
                    "cp {Step01.out} /workspace/s2/input.pdf"
                ],
                "saPlotFiles": ["/workspace/s2/result.pdf"],
            },
        ],
    }

    dictNewStep = fdictCreateStep(
        sName="Inserted",
        sDirectory="/workspace/inserted",
        saPlotCommands=["echo inserted"],
        saPlotFiles=["/workspace/inserted/new.pdf"],
    )

    fnInsertStep(dictWorkflow, 1, dictNewStep)

    assert len(dictWorkflow["listSteps"]) == 3
    assert dictWorkflow["listSteps"][1]["sName"] == "Inserted"

    sUpdatedCommand = dictWorkflow["listSteps"][2]["saPlotCommands"][0]
    assert "Step01" in sUpdatedCommand


def test_fnDeleteStep_renumbers_references():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "/workspace/s1",
                "saPlotCommands": ["echo s1"],
                "saPlotFiles": ["/workspace/s1/out.pdf"],
            },
            {
                "sName": "Step 2",
                "sDirectory": "/workspace/s2",
                "saPlotCommands": ["echo s2"],
                "saPlotFiles": ["/workspace/s2/out.pdf"],
            },
            {
                "sName": "Step 3",
                "sDirectory": "/workspace/s3",
                "saPlotCommands": [
                    "cp {Step02.out} /workspace/s3/"
                ],
                "saPlotFiles": ["/workspace/s3/result.pdf"],
            },
        ],
    }

    fnDeleteStep(dictWorkflow, 0)

    assert len(dictWorkflow["listSteps"]) == 2
    assert dictWorkflow["listSteps"][0]["sName"] == "Step 2"

    sUpdatedCommand = dictWorkflow["listSteps"][1]["saPlotCommands"][0]
    assert "Step01" in sUpdatedCommand


def test_fnReorderStep_updates_references():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "/workspace/a",
                "saPlotCommands": ["echo a"],
                "saPlotFiles": ["/workspace/a/a.pdf"],
            },
            {
                "sName": "B",
                "sDirectory": "/workspace/b",
                "saPlotCommands": ["echo b"],
                "saPlotFiles": ["/workspace/b/b.pdf"],
            },
            {
                "sName": "C",
                "sDirectory": "/workspace/c",
                "saPlotCommands": [
                    "cp {Step01.a} /workspace/c/"
                ],
                "saPlotFiles": ["/workspace/c/c.pdf"],
            },
        ],
    }

    fnReorderStep(dictWorkflow, 0, 2)

    assert dictWorkflow["listSteps"][2]["sName"] == "A"
    assert dictWorkflow["listSteps"][0]["sName"] == "B"


def test_flistValidateReferences_detects_broken_refs():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "/workspace/s1",
                "saPlotCommands": [
                    "cp {Step05.nonexistent} /tmp/"
                ],
                "saPlotFiles": ["/workspace/s1/out.pdf"],
            },
        ],
    }

    listWarnings = flistValidateReferences(dictWorkflow)

    assert len(listWarnings) > 0
    bFoundBrokenRef = any(
        "Step05" in sWarning for sWarning in listWarnings
    )
    assert bFoundBrokenRef


def test_flistValidateReferences_clean_workflow():
    dictWorkflow = _fdictBuildMinimalWorkflow(iStepCount=1)

    listWarnings = flistValidateReferences(dictWorkflow)

    assert listWarnings == []


def test_flistFindWorkflowsInContainer_custom_search_root():
    class MockDockerConnection:
        def __init__(self):
            self.listCommands = []

        def ftResultExecuteCommand(self, sContainerId, sCommand):
            self.listCommands.append(sCommand)
            return (0, "")

    mockConnection = MockDockerConnection()

    flistFindWorkflowsInContainer(
        mockConnection, "abc123", sSearchRoot="/custom/root"
    )

    bFoundCustomRoot = any(
        "/custom/root" in s for s in mockConnection.listCommands
    )
    assert bFoundCustomRoot


# --- Unit test field tests ---


def test_fdictCreateStep_includes_test_fields():
    dictStep = fdictCreateStep(sName="Test", sDirectory="/workspace")
    assert "saTestCommands" in dictStep
    assert dictStep["saTestCommands"] == []
    assert "dictRunStats" in dictStep


def test_fbStepRequiresTests_true_when_data_no_tests():
    dictStep = fdictCreateStep(
        sName="Test", sDirectory="/workspace",
        saDataCommands=["python analyze.py"],
    )
    assert fbStepRequiresTests(dictStep) is True


def test_fbStepRequiresTests_false_when_tests_present():
    dictStep = fdictCreateStep(
        sName="Test", sDirectory="/workspace",
        saDataCommands=["python analyze.py"],
        saTestCommands=["pytest test_analyze.py"],
    )
    assert fbStepRequiresTests(dictStep) is False


def test_fbStepRequiresTests_false_when_no_data():
    dictStep = fdictCreateStep(
        sName="Test", sDirectory="/workspace",
    )
    assert fbStepRequiresTests(dictStep) is False


# ---------------------------------------------------------------------------
# CamelCase directory mapping
# ---------------------------------------------------------------------------


def test_fsCamelCaseDirectory_multiple_words():
    assert fsCamelCaseDirectory("Flare MCMC Analysis") == (
        "FlareMcmcAnalysis"
    )


def test_fsCamelCaseDirectory_single_word():
    assert fsCamelCaseDirectory("Overview") == "Overview"


def test_fsCamelCaseDirectory_with_punctuation():
    assert fsCamelCaseDirectory("Step-01: Intro!") == "Step01Intro"


def test_fsCamelCaseDirectory_empty():
    assert fsCamelCaseDirectory("") == ""


def test_flistExtractStepScripts_python_commands():
    dictStep = {
        "saDataCommands": ["python kepler_ffd.py"],
        "saPlotCommands": ["python plotCorner.py Plot/out.pdf"],
    }
    listScripts = flistExtractStepScripts(dictStep)
    assert "kepler_ffd.py" in listScripts
    assert "plotCorner.py" in listScripts


def test_flistExtractStepScripts_no_scripts():
    dictStep = {
        "saDataCommands": ["maxlev config.json"],
        "saPlotCommands": [],
    }
    assert flistExtractStepScripts(dictStep) == []


def test_fdictBuildStepDirectoryMap():
    dictWorkflow = {
        "listSteps": [
            {"sName": "Kepler FFD Corner"},
            {"sName": "FFD Age Comparison"},
        ],
    }
    dictMap = fdictBuildStepDirectoryMap(dictWorkflow)
    assert dictMap[0] == "KeplerFfdCorner"
    assert dictMap[1] == "FfdAgeComparison"


# ---------------------------------------------------------------------------
# Three-category test infrastructure: migration and aggregation
# ---------------------------------------------------------------------------


def test_fdictMigrateTestFormat_old_format():
    from vaibify.gui.workflowManager import fdictMigrateTestFormat
    dictStep = {"saTestCommands": ["pytest test_step01.py"]}
    fdictMigrateTestFormat(dictStep)
    assert "dictTests" in dictStep
    assert dictStep["dictTests"]["dictIntegrity"]["saCommands"] == [
        "pytest test_step01.py"
    ]
    assert dictStep["dictTests"]["dictQualitative"]["saCommands"] == []


def test_fdictMigrateTestFormat_already_migrated():
    from vaibify.gui.workflowManager import fdictMigrateTestFormat
    dictTests = {
        "dictQualitative": {
            "saCommands": ["pytest tests/test_qualitative.py"],
            "sFilePath": "",
        },
        "dictQuantitative": {
            "saCommands": [], "sFilePath": "", "sStandardsPath": "",
        },
        "dictIntegrity": {"saCommands": [], "sFilePath": ""},
        "listUserTests": [],
    }
    dictStep = {"dictTests": dictTests, "saTestCommands": []}
    fdictMigrateTestFormat(dictStep)
    assert dictStep["dictTests"]["dictQualitative"]["saCommands"] == [
        "pytest tests/test_qualitative.py"
    ]


def test_flistBuildTestCommands():
    from vaibify.gui.workflowManager import flistBuildTestCommands
    dictStep = {
        "dictTests": {
            "dictQualitative": {
                "saCommands": ["pytest tests/test_qualitative.py"],
                "sFilePath": "",
            },
            "dictQuantitative": {
                "saCommands": ["pytest tests/test_quantitative.py"],
                "sFilePath": "", "sStandardsPath": "",
            },
            "dictIntegrity": {
                "saCommands": ["pytest tests/test_integrity.py"],
                "sFilePath": "",
            },
            "listUserTests": [],
        },
    }
    listCommands = flistBuildTestCommands(dictStep)
    assert len(listCommands) == 3
    assert "test_qualitative.py" in listCommands[0]
    assert "test_quantitative.py" in listCommands[1]
    assert "test_integrity.py" in listCommands[2]


def test_flistBuildTestCommands_empty():
    from vaibify.gui.workflowManager import flistBuildTestCommands
    dictStep = {"dictTests": {
        "dictQualitative": {"saCommands": [], "sFilePath": ""},
        "dictQuantitative": {
            "saCommands": [], "sFilePath": "", "sStandardsPath": "",
        },
        "dictIntegrity": {"saCommands": [], "sFilePath": ""},
        "listUserTests": [],
    }}
    assert flistBuildTestCommands(dictStep) == []


def test_fsTestsDirectory():
    from vaibify.gui.workflowManager import fsTestsDirectory
    assert fsTestsDirectory("/work/step01") == "/work/step01/tests"


# ----------------------------------------------------------------------
# flistValidateOutputFilePaths
# ----------------------------------------------------------------------


def test_flistValidateOutputFilePaths_accepts_repo_relative():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": "analysis",
            "saOutputFiles": ["figure.pdf", "data/result.csv"],
            "saDataFiles": [],
            "saPlotFiles": [],
        }],
    }
    assert flistValidateOutputFilePaths(dictWorkflow) == []


def test_flistValidateOutputFilePaths_rejects_absolute_path():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": "analysis",
            "saOutputFiles": ["/tmp/leak.pdf"],
            "saDataFiles": [],
            "saPlotFiles": [],
        }],
    }
    listWarnings = flistValidateOutputFilePaths(dictWorkflow)
    assert len(listWarnings) == 1
    assert "repo-relative" in listWarnings[0]
    assert "Step01" in listWarnings[0]


def test_flistValidateOutputFilePaths_rejects_escaping_parent():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": "analysis",
            "saOutputFiles": ["../../escape.pdf"],
            "saDataFiles": [],
            "saPlotFiles": [],
        }],
    }
    listWarnings = flistValidateOutputFilePaths(dictWorkflow)
    assert len(listWarnings) == 1
    assert "escapes" in listWarnings[0]


def test_flistValidateOutputFilePaths_skips_template_paths():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": "analysis",
            "saOutputFiles": ["{sPlotDirectory}/foo.pdf"],
            "saPlotFiles": ["{Step02.result}.png"],
            "saDataFiles": [],
        }],
    }
    assert flistValidateOutputFilePaths(dictWorkflow) == []


def test_flistValidateOutputFilePaths_reports_all_violations():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "s1",
                "saOutputFiles": ["/absolute.pdf"],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
            {
                "sName": "Step 2",
                "sDirectory": "s2",
                "saOutputFiles": [],
                "saDataFiles": ["../../outside.csv"],
                "saPlotFiles": [],
            },
        ],
    }
    listWarnings = flistValidateOutputFilePaths(dictWorkflow)
    assert len(listWarnings) == 2
    assert "Step01" in listWarnings[0]
    assert "Step02" in listWarnings[1]


def test_flistValidateOutputFilePaths_empty_workflow_returns_empty():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    assert flistValidateOutputFilePaths({}) == []
    assert flistValidateOutputFilePaths({"listSteps": []}) == []
