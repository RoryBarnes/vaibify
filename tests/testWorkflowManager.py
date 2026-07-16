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
    fdictBuildStepVariables,
    fdictBuildDirectDependencies,
    fdictStepIdToIndex,
    DEFAULT_SEARCH_ROOT,
)


def _fdictTwoStepSymbolicWorkflow():
    """Refit (id 'refit') → Plot, wired with a symbolic token."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "Refit", "sStepId": "refit", "sDirectory": "Refit",
             "saOutputDataFiles": ["chains.npz"], "saPlotCommands": [],
             "saPlotFiles": []},
            {"sName": "Plot", "sStepId": "plot", "sDirectory": "Plot",
             "saOutputDataFiles": [],
             "saPlotCommands": ["plot {step:refit.chains}"],
             "saPlotFiles": ["out.pdf"]},
        ],
    }


def test_symbolic_token_resolves_to_output_path():
    dictWorkflow = _fdictTwoStepSymbolicWorkflow()
    dictVars = {"sRepoRoot": "/repo"}
    dictStepVars = fdictBuildStepVariables(dictWorkflow, dictVars)
    assert dictStepVars["step:refit.chains"] == "/repo/Refit/chains.npz"
    # The positional alias still resolves during the transition.
    assert dictStepVars["Step01.chains"] == "/repo/Refit/chains.npz"


def test_symbolic_dependency_edge_is_detected():
    dictWorkflow = _fdictTwoStepSymbolicWorkflow()
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    # Refit (index 0) is upstream of Plot (index 1).
    assert 1 in dictDirect.get(0, set())


def test_symbolic_edge_survives_reorder_positional_would_not():
    """The whole point of stable ids: insert a step ABOVE the producer
    and the symbolic reference still points at it. A positional
    {Step01.chains} would now silently name the inserted step."""
    dictWorkflow = _fdictTwoStepSymbolicWorkflow()
    fnInsertStep(dictWorkflow, 0, {
        "sName": "Prelude", "sStepId": "prelude", "sDirectory": "Prelude",
        "saOutputDataFiles": ["note.txt"], "saPlotCommands": [], "saPlotFiles": [],
    })
    # Refit is now at index 1, Plot at index 2. The command text is
    # unchanged (symbolic tokens are never renumbered).
    assert dictWorkflow["listSteps"][2]["saPlotCommands"] == [
        "plot {step:refit.chains}",
    ]
    dictIdToIndex = fdictStepIdToIndex(dictWorkflow)
    assert dictIdToIndex["refit"] == 1
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    # Edge still runs Refit(1) -> Plot(2), NOT Prelude(0) -> Plot.
    assert 2 in dictDirect.get(1, set())
    assert 2 not in dictDirect.get(0, set())


def test_positional_reference_earns_deprecation_warning():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "A", "sStepId": "a", "sDirectory": "A",
             "saOutputDataFiles": ["x.npz"], "saPlotCommands": [],
             "saPlotFiles": []},
            {"sName": "B", "sStepId": "b", "sDirectory": "B",
             "saPlotCommands": ["run {Step01.x}"], "saPlotFiles": []},
        ],
    }
    listWarnings = flistValidateReferences(dictWorkflow)
    assert any("deprecated" in s for s in listWarnings)


def test_symbolic_reference_to_unknown_id_warns():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "B", "sStepId": "b", "sDirectory": "B",
             "saPlotCommands": ["run {step:ghost.x}"], "saPlotFiles": []},
        ],
    }
    listWarnings = flistValidateReferences(dictWorkflow)
    assert any("names no step id" in s for s in listWarnings)


def test_resolve_workflow_commands_substitutes_symbolic_token():
    from vaibify.gui.workflowManager import fdictResolveWorkflowCommands
    dictWorkflow = _fdictTwoStepSymbolicWorkflow()
    dictReport = fdictResolveWorkflowCommands(
        dictWorkflow, {"sRepoRoot": "/repo"},
    )
    dictPlotCmd = dictReport["listSteps"][1]["listCommands"][0]
    assert dictPlotCmd["sResolved"] == "plot /repo/Refit/chains.npz"
    assert dictPlotCmd["listUnresolvedTokens"] == []


def test_resolve_workflow_commands_flags_dangling_token():
    """A reference to a nonexistent output stays unresolved and is
    reported — the whole point of the dry-run."""
    from vaibify.gui.workflowManager import fdictResolveWorkflowCommands
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "Refit", "sStepId": "refit", "sDirectory": "Refit",
             "saOutputDataFiles": ["chains.npz"], "saPlotCommands": [],
             "saPlotFiles": []},
            {"sName": "Plot", "sStepId": "plot", "sDirectory": "Plot",
             "saPlotCommands": ["plot {step:refit.ghost}"],
             "saPlotFiles": []},
        ],
    }
    dictReport = fdictResolveWorkflowCommands(
        dictWorkflow, {"sRepoRoot": "/repo"},
    )
    dictPlotCmd = dictReport["listSteps"][1]["listCommands"][0]
    assert dictPlotCmd["listUnresolvedTokens"] == ["{step:refit.ghost}"]


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
    assert dictStep["bRunEnabled"] is True
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
# fbDeriveUnnecessaryVerification
# ---------------------------------------------------------------------------


def _fdictBuildStepWithCategories(dictCommandsByCategory):
    """Return a step whose dictTests categories carry given commands."""
    dictTests = {
        "dictIntegrity": {
            "saCommands": list(
                dictCommandsByCategory.get("integrity", []),
            ),
            "sFilePath": "",
        },
        "dictQualitative": {
            "saCommands": list(
                dictCommandsByCategory.get("qualitative", []),
            ),
            "sFilePath": "",
        },
        "dictQuantitative": {
            "saCommands": list(
                dictCommandsByCategory.get("quantitative", []),
            ),
            "sFilePath": "",
            "sStandardsPath": "",
        },
    }
    return {
        "sName": "Step", "sDirectory": "step1",
        "saPlotCommands": [], "saPlotFiles": [],
        "dictTests": dictTests,
        "dictVerification": {
            "sUnitTest": "untested",
            "sIntegrity": "untested",
            "sQualitative": "untested",
            "sQuantitative": "untested",
        },
    }


def test_fbDeriveUnnecessaryVerification_flips_empty_categories():
    from vaibify.gui.workflowManager import (
        fbDeriveUnnecessaryVerification,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [_fdictBuildStepWithCategories({})],
    }
    bChanged = fbDeriveUnnecessaryVerification(dictWorkflow)
    assert bChanged is True
    dictV = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictV["sIntegrity"] == "unnecessary"
    assert dictV["sQualitative"] == "unnecessary"
    assert dictV["sQuantitative"] == "unnecessary"
    assert dictV["sUnitTest"] == "unnecessary"


def test_fbDeriveUnnecessaryVerification_leaves_with_commands_alone():
    from vaibify.gui.workflowManager import (
        fbDeriveUnnecessaryVerification,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [_fdictBuildStepWithCategories({
            "integrity": ["pytest test_integrity.py"],
        })],
    }
    fbDeriveUnnecessaryVerification(dictWorkflow)
    dictV = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictV["sIntegrity"] == "untested"
    assert dictV["sQualitative"] == "unnecessary"
    assert dictV["sQuantitative"] == "unnecessary"
    assert dictV["sUnitTest"] == "untested"


def test_fbDeriveUnnecessaryVerification_idempotent():
    from vaibify.gui.workflowManager import (
        fbDeriveUnnecessaryVerification,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [_fdictBuildStepWithCategories({})],
    }
    fbDeriveUnnecessaryVerification(dictWorkflow)
    bSecond = fbDeriveUnnecessaryVerification(dictWorkflow)
    assert bSecond is False


def test_fbDeriveUnnecessaryVerification_preserves_passed():
    from vaibify.gui.workflowManager import (
        fbDeriveUnnecessaryVerification,
    )
    dictStep = _fdictBuildStepWithCategories({})
    dictStep["dictVerification"]["sIntegrity"] = "passed"
    dictWorkflow = {
        "sPlotDirectory": "Plot", "listSteps": [dictStep],
    }
    fbDeriveUnnecessaryVerification(dictWorkflow)
    dictV = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictV["sIntegrity"] == "passed"
    assert dictV["sQualitative"] == "unnecessary"


def test_fbDeriveUnnecessaryVerification_missing_dictTests():
    from vaibify.gui.workflowManager import (
        fbDeriveUnnecessaryVerification,
    )
    dictStep = {
        "sName": "Step", "sDirectory": "step1",
        "saPlotCommands": [], "saPlotFiles": [],
        "dictVerification": {
            "sUnitTest": "untested",
            "sIntegrity": "untested",
            "sQualitative": "untested",
            "sQuantitative": "untested",
        },
    }
    dictWorkflow = {
        "sPlotDirectory": "Plot", "listSteps": [dictStep],
    }
    bChanged = fbDeriveUnnecessaryVerification(dictWorkflow)
    assert bChanged is True
    dictV = dictStep["dictVerification"]
    assert dictV["sIntegrity"] == "unnecessary"
    assert dictV["sUnitTest"] == "unnecessary"


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


def test_fnMigrateRunEnabledKey_renames_legacy():
    from vaibify.gui.workflowManager import fnMigrateRunEnabledKey
    dictWorkflow = {"listSteps": [
        {"sName": "A", "bEnabled": True},
        {"sName": "B", "bEnabled": False},
    ]}
    fnMigrateRunEnabledKey(dictWorkflow)
    assert dictWorkflow["listSteps"][0]["bRunEnabled"] is True
    assert dictWorkflow["listSteps"][1]["bRunEnabled"] is False
    assert "bEnabled" not in dictWorkflow["listSteps"][0]
    assert "bEnabled" not in dictWorkflow["listSteps"][1]


def test_fnMigrateRunEnabledKey_idempotent_on_new_format():
    from vaibify.gui.workflowManager import fnMigrateRunEnabledKey
    dictWorkflow = {"listSteps": [
        {"sName": "A", "bRunEnabled": True},
    ]}
    fnMigrateRunEnabledKey(dictWorkflow)
    assert dictWorkflow["listSteps"][0]["bRunEnabled"] is True
    assert "bEnabled" not in dictWorkflow["listSteps"][0]


def test_fnMigrateRunEnabledKey_prefers_existing_bRunEnabled():
    """If both keys are present, the new key wins; legacy is removed."""
    from vaibify.gui.workflowManager import fnMigrateRunEnabledKey
    dictWorkflow = {"listSteps": [
        {"sName": "A", "bEnabled": False, "bRunEnabled": True},
    ]}
    fnMigrateRunEnabledKey(dictWorkflow)
    assert dictWorkflow["listSteps"][0]["bRunEnabled"] is True
    assert "bEnabled" not in dictWorkflow["listSteps"][0]


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
            "saOutputDataFiles": ["figure.pdf", "data/result.csv"],
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
            "saOutputDataFiles": ["/tmp/leak.pdf"],
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
            "saOutputDataFiles": ["../../escape.pdf"],
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
            "saOutputDataFiles": ["{sPlotDirectory}/foo.pdf"],
            "saPlotFiles": ["{Step02.result}.png"],
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
                "saOutputDataFiles": ["/absolute.pdf"],
                "saPlotFiles": [],
            },
            {
                "sName": "Step 2",
                "sDirectory": "s2",
                "saOutputDataFiles": ["../../outside.csv"],
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


class TestOutputTokenStemCollisions:
    """Colliding basenames qualify by leading path segment (no renames)."""

    def test_fdictMapOutputTokenStems_plain_when_unique(self):
        from vaibify.gui.pipelineUtils import fdictMapOutputTokenStems
        dictStems = fdictMapOutputTokenStems(
            ["output/result.json", "Plot/figure.pdf"])
        assert dictStems == {
            "result": "output/result.json",
            "figure": "Plot/figure.pdf",
        }

    def test_fdictMapOutputTokenStems_qualifies_collisions(self):
        from vaibify.gui.pipelineUtils import fdictMapOutputTokenStems
        dictStems = fdictMapOutputTokenStems([
            "EngleBarnes/output/Converged_Param_Dictionary.json",
            "RibasBarnes/output/Converged_Param_Dictionary.json",
        ])
        assert dictStems == {
            "EngleBarnes_Converged_Param_Dictionary":
                "EngleBarnes/output/Converged_Param_Dictionary.json",
            "RibasBarnes_Converged_Param_Dictionary":
                "RibasBarnes/output/Converged_Param_Dictionary.json",
        }

    def test_fdictBuildStemRegistry_registers_qualified_tokens(self):
        from vaibify.gui.workflowManager import fdictBuildStemRegistry
        dictWorkflow = {"listSteps": [{
            "saOutputDataFiles": [
                "EngleBarnes/output/Converged_Param_Dictionary.json",
                "RibasBarnes/output/Converged_Param_Dictionary.json",
            ],
        }]}
        dictRegistry = fdictBuildStemRegistry(dictWorkflow)
        assert "Step01.EngleBarnes_Converged_Param_Dictionary" in dictRegistry
        assert "Step01.RibasBarnes_Converged_Param_Dictionary" in dictRegistry
        assert "Step01.Converged_Param_Dictionary" not in dictRegistry

    def test_fdictBuildStepVariables_resolves_qualified_tokens(self):
        from vaibify.gui.workflowManager import fdictBuildStepVariables
        dictWorkflow = {"listSteps": [{
            "sDirectory": "XuvEvolution",
            "saOutputDataFiles": [
                "EngleBarnes/output/Converged_Param_Dictionary.json",
                "RibasBarnes/output/Converged_Param_Dictionary.json",
            ],
        }]}
        dictVars = fdictBuildStepVariables(
            dictWorkflow, {"sRepoRoot": "/repo"})
        sKey = "Step01.EngleBarnes_Converged_Param_Dictionary"
        assert dictVars[sKey].endswith(
            "EngleBarnes/output/Converged_Param_Dictionary.json")


# ---------------------------------------------------------------
# audit MEDIUM #16: generic scratch-cleanup hook
# ---------------------------------------------------------------


def test_flistResolveStepScratchDirs_joins_to_step_workdir():
    from vaibify.gui.workflowManager import flistResolveStepScratchDirs
    dictStep = {"sDirectory": "stepA", "saScratchDirs": ["tmp", "cache"]}
    listAbs = flistResolveStepScratchDirs(
        dictStep, {"sRepoRoot": "/repo"},
    )
    assert listAbs == ["/repo/stepA/tmp", "/repo/stepA/cache"]


def test_flistResolveStepScratchDirs_skips_absolute_entries():
    from vaibify.gui.workflowManager import flistResolveStepScratchDirs
    dictStep = {"sDirectory": "stepA", "saScratchDirs": ["/etc", "ok"]}
    listAbs = flistResolveStepScratchDirs(
        dictStep, {"sRepoRoot": "/repo"},
    )
    assert listAbs == ["/repo/stepA/ok"]


def test_flistResolveStepScratchDirs_empty_when_absent():
    from vaibify.gui.workflowManager import flistResolveStepScratchDirs
    dictStep = {"sDirectory": "stepA"}
    assert flistResolveStepScratchDirs(dictStep, {}) == []


def test_fnCleanStepScratchDirs_invokes_rm_rf_per_path():
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnCleanStepScratchDirs
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictStep = {"sDirectory": "stepA", "saScratchDirs": ["tmp", "cache"]}
    listResults = fnCleanStepScratchDirs(
        mockDocker, "cid", dictStep, {"sRepoRoot": "/repo"},
    )
    assert listResults == [
        ("/repo/stepA/tmp", 0),
        ("/repo/stepA/cache", 0),
    ]
    listCommands = [
        tCall[0][1]
        for tCall in mockDocker.ftResultExecuteCommand.call_args_list
    ]
    assert all(sCmd.startswith("rm -rf -- ") for sCmd in listCommands)
    assert any("/repo/stepA/tmp" in sCmd for sCmd in listCommands)


def test_fnCleanStepScratchDirs_returns_empty_when_absent():
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnCleanStepScratchDirs
    mockDocker = MagicMock()
    dictStep = {"sDirectory": "stepA"}
    listResults = fnCleanStepScratchDirs(mockDocker, "cid", dictStep, {})
    assert listResults == []
    mockDocker.ftResultExecuteCommand.assert_not_called()


def test_fnCleanStepScratchDirs_reports_nonzero_exit():
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import fnCleanStepScratchDirs
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "missing")
    dictStep = {"sDirectory": "stepA", "saScratchDirs": ["tmp"]}
    listResults = fnCleanStepScratchDirs(
        mockDocker, "cid", dictStep, {"sRepoRoot": "/repo"},
    )
    assert listResults == [("/repo/stepA/tmp", 1)]


def test_validate_rejects_absolute_scratch_dir():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "A", "sDirectory": "stepA",
            "saScratchDirs": ["/etc/passwd"],
            "saPlotCommands": [], "saPlotFiles": [],
        }],
        "sPlotDirectory": "plots",
    }
    listWarnings = flistValidateOutputFilePaths(dictWorkflow)
    assert any("saScratchDirs" in sW for sW in listWarnings)


def test_validate_rejects_escaping_scratch_dir():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "A", "sDirectory": "stepA",
            "saScratchDirs": ["../../escape"],
            "saPlotCommands": [], "saPlotFiles": [],
        }],
        "sPlotDirectory": "plots",
    }
    listWarnings = flistValidateOutputFilePaths(dictWorkflow)
    assert any("saScratchDirs" in sW for sW in listWarnings)


def test_validate_accepts_repo_relative_scratch_dir():
    from vaibify.gui.workflowManager import flistValidateOutputFilePaths
    dictWorkflow = {
        "listSteps": [{
            "sName": "A", "sDirectory": "stepA",
            "saScratchDirs": ["tmp"],
            "saPlotCommands": [], "saPlotFiles": [],
        }],
        "sPlotDirectory": "plots",
    }
    listWarnings = flistValidateOutputFilePaths(dictWorkflow)
    assert not any("saScratchDirs" in sW for sW in listWarnings)
