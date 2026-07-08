"""Tests for the schema versioning + migration registry."""

from vaibify.gui import workflowMigrations
from vaibify.gui.workflowMigrations import (
    I_CURRENT_WORKFLOW_VERSION,
    S_VERSION_KEY,
    fbWorkflowNeedsMigration,
    fiGetSchemaVersion,
    fnApplyMigrations,
    fnMigrateAbsoluteContainerPaths,
    fnStampCurrentVersion,
)


def _fdictBuildLegacyV0Fixture():
    """Workflow shaped like a pre-2026-04-20 dump, no version field."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Flares",
                "sDirectory": "/workspace/SampleProject/Flares",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["fig1.pdf"],
                "saOutputFiles": [
                    "/workspace/SampleProject/Flares/out.npz",
                ],
                "bEnabled": True,
                "saTestCommands": ["pytest"],
            },
        ],
    }


def _fdictBuildModernV2Fixture():
    """A clean workflow already at the current schema version."""
    return {
        S_VERSION_KEY: I_CURRENT_WORKFLOW_VERSION,
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Flares",
                "sDirectory": "Flares",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["fig1.pdf"],
                "saOutputFiles": ["out.npz"],
                "bRunEnabled": True,
                "dictTests": {
                    "dictQualitative": {"saCommands": [], "sFilePath": ""},
                    "dictQuantitative": {
                        "saCommands": [], "sFilePath": "",
                        "sStandardsPath": "",
                    },
                    "dictIntegrity": {
                        "saCommands": ["pytest"], "sFilePath": "",
                    },
                    "listUserTests": [],
                },
            },
        ],
    }


def test_fiGetSchemaVersion_defaults_to_zero_when_missing():
    assert fiGetSchemaVersion({}) == 0
    assert fiGetSchemaVersion({"listSteps": []}) == 0


def test_fiGetSchemaVersion_returns_int_when_present():
    assert fiGetSchemaVersion({S_VERSION_KEY: 1}) == 1
    assert fiGetSchemaVersion({S_VERSION_KEY: 2}) == 2


def test_fiGetSchemaVersion_tolerates_string_value():
    assert fiGetSchemaVersion({S_VERSION_KEY: "garbage"}) == 0


def test_fbWorkflowNeedsMigration_true_for_v0():
    assert fbWorkflowNeedsMigration(_fdictBuildLegacyV0Fixture()) is True


def test_fbWorkflowNeedsMigration_false_for_current():
    assert (
        fbWorkflowNeedsMigration(_fdictBuildModernV2Fixture()) is False
    )


def test_fnStampCurrentVersion_sets_field():
    dictWorkflow = {}
    fnStampCurrentVersion(dictWorkflow)
    assert dictWorkflow[S_VERSION_KEY] == I_CURRENT_WORKFLOW_VERSION


def test_fnApplyMigrations_brings_legacy_to_current():
    dictWorkflow = _fdictBuildLegacyV0Fixture()
    iVersion = fnApplyMigrations(
        dictWorkflow, sProjectRepoPath="/workspace/SampleProject",
    )
    assert iVersion == I_CURRENT_WORKFLOW_VERSION
    assert dictWorkflow[S_VERSION_KEY] == I_CURRENT_WORKFLOW_VERSION


def test_v0_to_v1_renames_bEnabled_and_creates_dictTests():
    dictWorkflow = _fdictBuildLegacyV0Fixture()
    fnApplyMigrations(dictWorkflow)
    dictStep = dictWorkflow["listSteps"][0]
    assert "bEnabled" not in dictStep
    assert dictStep["bRunEnabled"] is True
    assert "dictTests" in dictStep


def test_v1_to_v2_strips_absolute_workspace_prefix_from_step_dir():
    dictWorkflow = _fdictBuildLegacyV0Fixture()
    fnApplyMigrations(
        dictWorkflow, sProjectRepoPath="/workspace/SampleProject",
    )
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["sDirectory"] == "Flares"
    assert dictStep["saOutputFiles"] == ["out.npz"]


def test_v1_to_v2_infers_repo_root_when_context_missing():
    """Migrator strips legacy /workspace/<repo>/ prefix even without sRepoPath."""
    dictWorkflow = _fdictBuildLegacyV0Fixture()
    fnApplyMigrations(dictWorkflow, sProjectRepoPath="")
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["sDirectory"] == "Flares"
    assert dictStep["saOutputFiles"] == ["out.npz"]


def test_v0_to_v1_archive_tracking_uses_supplied_repo_path():
    """Sync-status keys must be repo-relative even when the dict's
    own ``sProjectRepoPath`` is unset at load time. The registry
    threads the path through; the archive-tracking migrator should
    pick it up and strip the prefix from generated keys.
    """
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Flares",
                "sDirectory": "/workspace/SampleProject/Flares",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["fig1.pdf"],
                "saDataFiles": [],
                "saOutputFiles": [
                    "/workspace/SampleProject/Flares/out.npz",
                ],
                "saTestCommands": [],
                "dictPlotFileCategories": {"fig1.pdf": "archive"},
            },
        ],
    }
    fnApplyMigrations(
        dictWorkflow, sProjectRepoPath="/workspace/SampleProject",
    )
    listKeys = list(dictWorkflow.get("dictSyncStatus", {}).keys())
    assert listKeys == ["Flares/fig1.pdf"]
    assert "sProjectRepoPath" not in dictWorkflow


def test_fnMigrateAbsoluteContainerPaths_skips_template_paths():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "{sStepDir}",
                "saOutputFiles": ["{sOut}/file.npz"],
            },
        ],
    }
    fnMigrateAbsoluteContainerPaths(
        dictWorkflow, sProjectRepoPath="/workspace/Anything",
    )
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["sDirectory"] == "{sStepDir}"
    assert dictStep["saOutputFiles"] == ["{sOut}/file.npz"]


def test_v4_to_v5_strips_absolute_prefix_from_test_paths():
    """Container-absolute dictTests paths become repo-relative."""
    dictWorkflow = {
        S_VERSION_KEY: 4,
        "listSteps": [
            {
                "sDirectory": "Flares",
                "dictTests": {
                    "dictQuantitative": {
                        "sFilePath": "/workspace/SampleProject/Flares"
                                     "/tests/test_quantitative.py",
                        "sStandardsPath": "/workspace/SampleProject"
                                          "/Flares/tests"
                                          "/quantitative_standards.json",
                    },
                    "dictIntegrity": {
                        "sFilePath": "Flares/tests/test_integrity.py",
                    },
                    "listUserTests": [],
                },
            },
        ],
    }
    fnApplyMigrations(
        dictWorkflow, sProjectRepoPath="/workspace/SampleProject",
    )
    dictTests = dictWorkflow["listSteps"][0]["dictTests"]
    assert dictTests["dictQuantitative"]["sFilePath"] == (
        "Flares/tests/test_quantitative.py"
    )
    assert dictTests["dictQuantitative"]["sStandardsPath"] == (
        "Flares/tests/quantitative_standards.json"
    )
    assert dictTests["dictIntegrity"]["sFilePath"] == (
        "Flares/tests/test_integrity.py"
    )


def test_fnApplyMigrations_is_no_op_on_current_version():
    dictWorkflow = _fdictBuildModernV2Fixture()
    dictBefore = dict(dictWorkflow)
    listStepsBefore = [dict(s) for s in dictWorkflow["listSteps"]]
    fnApplyMigrations(dictWorkflow, sProjectRepoPath="/workspace/X")
    assert dictWorkflow[S_VERSION_KEY] == I_CURRENT_WORKFLOW_VERSION
    assert dictWorkflow["sPlotDirectory"] == dictBefore["sPlotDirectory"]
    assert dictWorkflow["listSteps"][0] == listStepsBefore[0]


def test_T_MIGRATORS_starts_at_zero_and_is_contiguous():
    """Registry must enumerate every version from 0 to current-1."""
    listFromVersions = [iFrom for iFrom, _ in workflowMigrations.T_MIGRATORS]
    assert listFromVersions == list(range(I_CURRENT_WORKFLOW_VERSION))


def test_load_invalid_workflow_returns_named_diagnostic():
    from vaibify.gui.workflowManager import (
        fsDescribeValidationFailure,
    )
    dictMissingPlotDir = {"listSteps": []}
    sFailure = fsDescribeValidationFailure(dictMissingPlotDir)
    assert "sPlotDirectory" in sFailure


def test_load_invalid_step_field_names_step_label():
    from vaibify.gui.workflowManager import (
        fsDescribeValidationFailure,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {"sName": "A", "sDirectory": "a", "saPlotCommands": []},
        ],
    }
    sFailure = fsDescribeValidationFailure(dictWorkflow)
    assert "Step01" in sFailure
    assert "saPlotFiles" in sFailure


def test_load_invalid_absolute_path_names_field_in_diagnostic():
    from vaibify.gui.workflowManager import (
        fsDescribeValidationFailure,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "/workspace/Project/A",
                "saPlotCommands": [],
                "saPlotFiles": [],
            },
        ],
    }
    sFailure = fsDescribeValidationFailure(dictWorkflow)
    assert "sDirectory" in sFailure
    assert "repo-relative" in sFailure


def test_fsDeriveProjectRepoPathFromWorkflow_strips_vaibify_suffix():
    from vaibify.gui.workflowManager import (
        fsDeriveProjectRepoPathFromWorkflow,
    )
    sResult = fsDeriveProjectRepoPathFromWorkflow(
        "/workspace/MyProj/.vaibify/workflows/main.json",
    )
    assert sResult == "/workspace/MyProj"


def test_fsDeriveProjectRepoPathFromWorkflow_returns_empty_for_non_matching():
    from vaibify.gui.workflowManager import (
        fsDeriveProjectRepoPathFromWorkflow,
    )
    assert fsDeriveProjectRepoPathFromWorkflow("") == ""
    assert fsDeriveProjectRepoPathFromWorkflow("/some/random/path.json") == ""


def test_step_without_sStepKind_defaults_to_data_kind():
    """Phase 2 backward-compat: legacy steps lack ``sStepKind``.

    The ai-declaration predicate must treat a missing kind as ``data``,
    so legacy workflows do not accidentally satisfy the "has AI
    Declaration step" L2 criterion just because their schema predates
    the field.
    """
    from vaibify.reproducibility.aiDeclarationStep import (
        fbStepIsAiDeclaration,
    )
    dictLegacyStep = {
        "sName": "Flares",
        "sDirectory": "Flares",
        "saPlotCommands": ["python plot.py"],
    }
    assert "sStepKind" not in dictLegacyStep
    assert fbStepIsAiDeclaration(dictLegacyStep) is False


def test_ai_declaration_step_kind_survives_migration_run():
    """Migrators don't touch ``sStepKind`` or ``sDeclarationFile``.

    A user-added ai-declaration step must round-trip through
    ``fnApplyMigrations`` unchanged; otherwise the L2 gate would drop
    every time the schema version bumps.
    """
    dictWorkflow = {
        S_VERSION_KEY: I_CURRENT_WORKFLOW_VERSION,
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "AI Declaration",
                "sDirectory": "",
                "sStepKind": "ai-declaration",
                "sDeclarationFile": "AI_USAGE.md",
                "saPlotCommands": [],
                "saPlotFiles": [],
            },
        ],
    }
    fnApplyMigrations(dictWorkflow, sProjectRepoPath="/workspace/X")
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["sStepKind"] == "ai-declaration"
    assert dictStep["sDeclarationFile"] == "AI_USAGE.md"


def test_load_derives_unnecessary_for_empty_command_categories():
    """Loading a workflow rewrites stale "untested" to "unnecessary".

    Simulates a state.json saved by an older vaibify (or freshly
    initialized step) where every category default is "untested".
    After the load-time derivation hook fires, categories whose
    ``saCommands`` list is empty must read "unnecessary" so the
    all-green gate accepts them.
    """
    from vaibify.gui.workflowManager import (
        fbDeriveUnnecessaryVerification,
    )
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "PlotOnly",
                "sDirectory": "plotOnly",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["fig.pdf"],
                "dictTests": {
                    "dictIntegrity": {
                        "saCommands": [], "sFilePath": "",
                    },
                    "dictQualitative": {
                        "saCommands": [], "sFilePath": "",
                    },
                    "dictQuantitative": {
                        "saCommands": [], "sFilePath": "",
                        "sStandardsPath": "",
                    },
                },
                "dictVerification": {
                    "sUnitTest": "untested",
                    "sIntegrity": "untested",
                    "sQualitative": "untested",
                    "sQuantitative": "untested",
                },
            },
        ],
    }
    fbDeriveUnnecessaryVerification(dictWorkflow)
    dictV = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictV["sIntegrity"] == "unnecessary"
    assert dictV["sQualitative"] == "unnecessary"
    assert dictV["sQuantitative"] == "unnecessary"
    assert dictV["sUnitTest"] == "unnecessary"
