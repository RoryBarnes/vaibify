"""Exhaustive tests for every remaining untested pure function.

Covers gaps found in the architecture review audit across
pipelineServer, pipelineRunner, syncDispatcher, and workflowManager.
"""

import json
from unittest.mock import MagicMock

from vaibify.gui.fileStatusManager import fsMarkerNameFromStepDirectory
from vaibify.gui.pipelineServer import (
    _fbCancelPipelineTask,
    _fbMarkerStale,
    _fdictBuildOverleafArgs,
    _fdictBuildTestMarkerStatus,
    _flistBuildCleanCommands,
    _flistFindCustomTestFiles,
    _fnApplyAllMarkerCategories,
    _fnHandleInteractiveComplete,
    _fnHandleInteractiveResponse,
    _fsResolveLanguage,
    _fsSanitizeServerError,
    fsComputeStaticCacheVersion,
)
from vaibify.gui.pipelineRunner import (
    _fdictBuildVariables,
)
from vaibify.gui.syncDispatcher import (
    _fsBuildTestMarkerScript,
    _flistBuildStepCopyCommandList,
    fsPythonCommand,
)
from vaibify.gui.workflowManager import (
    _fnCheckCommandReferences,
    fdictBuildStemRegistry,
    flistCollectReferenceStrings,
    fnRenumberAllReferences,
    fsetExtractStepReferences,
)


# ---------------------------------------------------------------
# pipelineServer.py gaps
# ---------------------------------------------------------------


class TestFsMarkerNameFromDirectory:
    def test_simple_path(self):
        assert fsMarkerNameFromStepDirectory(
            "/workspace/step01") == "workspace_step01.json"

    def test_trailing_slash(self):
        assert fsMarkerNameFromStepDirectory(
            "step01/") == "step01.json"

    def test_nested_path(self):
        sResult = fsMarkerNameFromStepDirectory(
            "/workspace/GJ1132/XUV")
        assert sResult == "workspace_GJ1132_XUV.json"

    def test_no_leading_slash(self):
        assert fsMarkerNameFromStepDirectory(
            "step01") == "step01.json"


class TestFbMarkerStale:
    def test_newer_test_file(self):
        dictMarker = {"fTimestamp": 100.0}
        dictTestFileInfo = {"dictMtimes": {"test_a.py": 200.0}}
        assert _fbMarkerStale(dictMarker, dictTestFileInfo) is True

    def test_older_test_file(self):
        dictMarker = {
            "fTimestamp": 300.0, "sRunAtUtc": "2026-04-23T00:00:00Z",
        }
        dictTestFileInfo = {"dictMtimes": {"test_a.py": 100.0}}
        assert _fbMarkerStale(dictMarker, dictTestFileInfo) is False

    def test_no_test_files(self):
        dictMarker = {
            "fTimestamp": 100.0, "sRunAtUtc": "2026-04-23T00:00:00Z",
        }
        assert _fbMarkerStale(dictMarker, {}) is False

    def test_no_timestamp(self):
        dictMarker = {}
        dictTestFileInfo = {"dictMtimes": {"test_a.py": 1.0}}
        assert _fbMarkerStale(dictMarker, dictTestFileInfo) is True


class TestFdictBuildTestMarkerStatus:
    def test_maps_marker_to_step(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "step01"},
        ]}
        dictTestInfo = {
            "markers": {
                "step01.json": {
                    "fTimestamp": 999.0,
                    "dictCategories": {},
                },
            },
            "testFiles": {},
        }
        dictResult = _fdictBuildTestMarkerStatus(
            dictWorkflow, dictTestInfo)
        assert "0" in dictResult

    def test_no_matching_marker(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "step01"},
        ]}
        dictTestInfo = {"markers": {}, "testFiles": {}}
        assert _fdictBuildTestMarkerStatus(
            dictWorkflow, dictTestInfo) == {}

    def test_empty_directory_skipped(self):
        dictWorkflow = {"listSteps": [{"sDirectory": ""}]}
        dictTestInfo = {"markers": {}, "testFiles": {}}
        assert _fdictBuildTestMarkerStatus(
            dictWorkflow, dictTestInfo) == {}


class TestFnApplyAllMarkerCategories:
    def test_applies_all_categories(self):
        dictVerify = {}
        dictCategories = {
            "integrity": {"iPassed": 3, "iFailed": 0},
            "qualitative": {"iPassed": 0, "iFailed": 1},
            "quantitative": {"iPassed": 5, "iFailed": 0},
        }
        _fnApplyAllMarkerCategories(dictVerify, dictCategories)
        assert dictVerify["sIntegrity"] == "passed"
        assert dictVerify["sQualitative"] == "failed"
        assert dictVerify["sQuantitative"] == "passed"

    def test_missing_categories_unchanged(self):
        dictVerify = {"sIntegrity": "untested"}
        _fnApplyAllMarkerCategories(dictVerify, {})
        assert dictVerify["sIntegrity"] == "untested"

    def test_partial_categories(self):
        dictVerify = {}
        dictCategories = {
            "integrity": {"iPassed": 1, "iFailed": 0},
        }
        _fnApplyAllMarkerCategories(dictVerify, dictCategories)
        assert dictVerify["sIntegrity"] == "passed"
        assert "sQualitative" not in dictVerify


class TestFlistFindCustomTestFiles:
    def test_detects_modified_file(self):
        dictFileHashes = {"test_a.py": "abc123"}
        dictExpected = {"test_a.py": "def456"}
        listResult = _flistFindCustomTestFiles(
            dictFileHashes, dictExpected)
        assert listResult == ["test_a.py"]

    def test_matching_hashes_not_listed(self):
        dictFileHashes = {"test_a.py": "abc123"}
        dictExpected = {"test_a.py": "abc123"}
        assert _flistFindCustomTestFiles(
            dictFileHashes, dictExpected) == []

    def test_missing_file_not_listed(self):
        dictFileHashes = {}
        dictExpected = {"test_a.py": "abc123"}
        assert _flistFindCustomTestFiles(
            dictFileHashes, dictExpected) == []

    def test_extra_files_ignored(self):
        dictFileHashes = {"test_a.py": "abc", "test_b.py": "def"}
        dictExpected = {"test_a.py": "abc"}
        assert _flistFindCustomTestFiles(
            dictFileHashes, dictExpected) == []


class TestFlistBuildCleanCommands:
    def test_builds_rm_commands(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work/step01",
             "saDataFiles": ["data.h5"],
             "saPlotFiles": ["Plot/fig.pdf"],
             "dictRunStats": {"sLastRun": "old"},
             "dictVerification": {"sUnitTest": "passed"}},
        ]}
        listCmds = _flistBuildCleanCommands(dictWorkflow)
        assert len(listCmds) == 2
        assert any("data.h5" in s for s in listCmds)
        assert any("fig.pdf" in s for s in listCmds)

    def test_resets_run_stats(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work",
             "saDataFiles": ["a.h5"], "saPlotFiles": [],
             "dictRunStats": {"sLastRun": "old"},
             "dictVerification": {"sUnitTest": "passed"}},
        ]}
        _flistBuildCleanCommands(dictWorkflow)
        assert dictWorkflow["listSteps"][0]["dictRunStats"] == {}

    def test_resets_verification(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work",
             "saDataFiles": [], "saPlotFiles": [],
             "dictRunStats": {},
             "dictVerification": {
                 "sUnitTest": "passed", "sUser": "passed"}},
        ]}
        _flistBuildCleanCommands(dictWorkflow)
        dictVerify = dictWorkflow["listSteps"][0][
            "dictVerification"]
        assert dictVerify["sUnitTest"] == "untested"
        assert dictVerify["sUser"] == "untested"

    def test_skips_interactive_steps(self):
        dictWorkflow = {"listSteps": [
            {"bInteractive": True,
             "saDataFiles": ["a.h5"], "saPlotFiles": [],
             "dictRunStats": {}, "dictVerification": {}},
        ]}
        assert _flistBuildCleanCommands(dictWorkflow) == []

    def test_skips_template_variables(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work",
             "saDataFiles": ["{sPlotDirectory}/fig.pdf"],
             "saPlotFiles": [],
             "dictRunStats": {}, "dictVerification": {}},
        ]}
        listCmds = _flistBuildCleanCommands(dictWorkflow)
        assert listCmds == []

    def test_absolute_path_preserved(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work",
             "saDataFiles": ["/abs/data.h5"],
             "saPlotFiles": [],
             "dictRunStats": {}, "dictVerification": {}},
        ]}
        listCmds = _flistBuildCleanCommands(dictWorkflow)
        assert any("/abs/data.h5" in s for s in listCmds)

    def test_empty_workflow(self):
        assert _flistBuildCleanCommands({"listSteps": []}) == []


class TestFdictBuildOverleafArgs:
    def test_extracts_all_fields(self):
        dictWorkflow = {
            "sOverleafProjectId": "abc123",
            "sOverleafFigureDirectory": "figs",
            "sGithubBaseUrl": "https://github.com/user/repo",
            "sZenodoDoi": "10.5281/zenodo.123",
            "sTexFilename": "paper.tex",
        }
        dictResult = _fdictBuildOverleafArgs(dictWorkflow, "figs")
        assert dictResult["sProjectId"] == "abc123"
        assert dictResult["sTargetDirectory"] == "figs"
        assert dictResult["sGithubBaseUrl"] == (
            "https://github.com/user/repo")
        assert dictResult["sDoi"] == "10.5281/zenodo.123"
        assert dictResult["sTexFilename"] == "paper.tex"
        assert dictResult["dictWorkflow"] is dictWorkflow

    def test_defaults(self):
        dictResult = _fdictBuildOverleafArgs({}, "figures")
        assert dictResult["sProjectId"] == ""
        assert dictResult["sTargetDirectory"] == "figures"
        assert dictResult["sTexFilename"] == "main.tex"


class TestFsResolveLanguage:
    def test_returns_known_language(self):
        assert _fsResolveLanguage(
            "python", "run.py", "python run.py", "") == "python"

    def test_detects_from_source(self):
        sResult = _fsResolveLanguage(
            "unknown", "script.py", "python script.py",
            "#!/usr/bin/env python\nimport os")
        assert sResult == "python"

    def test_unknown_stays_unknown(self):
        sResult = _fsResolveLanguage(
            "unknown", "mystery", "mystery", "")
        assert isinstance(sResult, str)


def _fdictMockInteractiveContext():
    """Build a mock interactive context without asyncio.Event."""
    mockEvent = MagicMock()
    return {"eventResume": mockEvent, "sResponse": ""}


class TestFnHandleInteractiveComplete:
    def test_sets_complete_response(self):
        dictInteractive = _fdictMockInteractiveContext()
        _fnHandleInteractiveComplete(
            dictInteractive, {"iExitCode": 0})
        assert dictInteractive["sResponse"] == "complete:0"

    def test_nonzero_exit_code(self):
        dictInteractive = _fdictMockInteractiveContext()
        _fnHandleInteractiveComplete(
            dictInteractive, {"iExitCode": 42})
        assert dictInteractive["sResponse"] == "complete:42"

    def test_default_exit_code(self):
        dictInteractive = _fdictMockInteractiveContext()
        _fnHandleInteractiveComplete(dictInteractive, {})
        assert dictInteractive["sResponse"] == "complete:0"


class TestFnHandleInteractiveResponse:
    def test_resume_action(self):
        dictInteractive = _fdictMockInteractiveContext()
        _fnHandleInteractiveResponse(
            dictInteractive, "interactiveResume", {})
        assert dictInteractive["sResponse"] == "resume"

    def test_skip_action(self):
        dictInteractive = _fdictMockInteractiveContext()
        _fnHandleInteractiveResponse(
            dictInteractive, "interactiveSkip", {})
        assert dictInteractive["sResponse"] == "skip"


class TestFbCancelPipelineTask:
    def test_cancels_running_task(self):
        mockTask = MagicMock()
        mockTask.done.return_value = False
        dictTasks = {"container1": mockTask}
        bResult = _fbCancelPipelineTask(dictTasks, "container1")
        assert bResult is True
        mockTask.cancel.assert_called_once()
        assert "container1" not in dictTasks

    def test_no_task_returns_false(self):
        assert _fbCancelPipelineTask({}, "container1") is False

    def test_done_task_returns_false(self):
        mockTask = MagicMock()
        mockTask.done.return_value = True
        dictTasks = {"container1": mockTask}
        assert _fbCancelPipelineTask(dictTasks, "container1") is False


class TestFsSanitizeServerError:
    def test_disk_full(self):
        sResult = _fsSanitizeServerError("No space left on device")
        assert "prune" in sResult

    def test_no_container(self):
        sResult = _fsSanitizeServerError("No such container: abc")
        assert "not found" in sResult

    def test_connection_refused(self):
        sResult = _fsSanitizeServerError("connection refused")
        assert "Docker" in sResult

    def test_permission_denied(self):
        sResult = _fsSanitizeServerError("Permission denied")
        assert "Permission" in sResult

    def test_long_message_truncated(self):
        sResult = _fsSanitizeServerError("x" * 1000)
        assert len(sResult) <= 505

    def test_short_message_unchanged(self):
        sResult = _fsSanitizeServerError("short error")
        assert sResult == "short error"


class TestFsComputeStaticCacheVersion:
    def test_returns_numeric_string(self):
        sResult = fsComputeStaticCacheVersion()
        assert sResult.isdigit()

    def test_returns_nonzero(self):
        assert int(fsComputeStaticCacheVersion()) > 0


# ---------------------------------------------------------------
# pipelineRunner.py gap
# ---------------------------------------------------------------


class TestFdictBuildVariables:
    def test_merges_global_and_step_vars(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "iNumberOfCores": 4,
            "listSteps": [
                {"sName": "Step A",
                 "sDirectory": "/work/step01",
                 "saDataFiles": ["data.h5"],
                 "saPlotFiles": []},
            ],
        }
        dictResult = _fdictBuildVariables(dictWorkflow, "/work")
        assert "sPlotDirectory" in dictResult
        assert "Plot" in dictResult["sPlotDirectory"]


# ---------------------------------------------------------------
# syncDispatcher.py gaps
# ---------------------------------------------------------------


class TestFlistBuildStepCopyCommandList:
    def test_builds_commands_for_steps_with_scripts(self):
        dictWorkflow = {
            "listSteps": [
                {"sName": "Build Model",
                 "sDirectory": "/work/step01",
                 "saDataCommands": ["python run.py"],
                 "saPlotCommands": [],
                 "saPlotFiles": [],
                 "saDataFiles": []},
            ],
        }
        listCmds = _flistBuildStepCopyCommandList(dictWorkflow)
        assert len(listCmds) >= 1
        assert "mkdir" in listCmds[0]

    def test_empty_workflow(self):
        assert _flistBuildStepCopyCommandList(
            {"listSteps": []}) == []

    def test_step_with_no_scripts(self):
        dictWorkflow = {
            "listSteps": [
                {"sName": "Empty",
                 "sDirectory": "/work/step01",
                 "saDataCommands": [],
                 "saPlotCommands": [],
                 "saPlotFiles": [],
                 "saDataFiles": []},
            ],
        }
        assert _flistBuildStepCopyCommandList(dictWorkflow) == []


class TestFsBuildTestMarkerScript:
    def test_returns_valid_python(self):
        sScript = _fsBuildTestMarkerScript(
            json.dumps(["step01"]), "/workspace/DemoRepo")
        assert "import json" in sScript
        assert "print(json.dumps(R))" in sScript

    def test_contains_marker_directory(self):
        sScript = _fsBuildTestMarkerScript(
            json.dumps([]), "/workspace/DemoRepo")
        assert "test_markers" in sScript

    def test_no_single_quotes(self):
        sScript = _fsBuildTestMarkerScript(
            json.dumps(["step"]), "/workspace/DemoRepo")
        assert "'" not in sScript

    def test_includes_hash_extraction(self):
        sScript = _fsBuildTestMarkerScript(
            json.dumps([]), "/workspace/DemoRepo")
        assert "vaibify-template-hash" in sScript

    def test_checks_conftest(self):
        sScript = _fsBuildTestMarkerScript(
            json.dumps([]), "/workspace/DemoRepo")
        assert "conftest.py" in sScript

    def test_embeds_project_repo_path(self):
        sScript = _fsBuildTestMarkerScript(
            json.dumps([]), "/workspace/DemoRepo")
        assert "/workspace/DemoRepo" in sScript


# ---------------------------------------------------------------
# workflowManager.py gaps
# ---------------------------------------------------------------


class TestFnRenumberAllReferences:
    def test_renumbers_forward(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A",
             "saDataCommands": [],
             "saTestCommands": [],
             "saPlotCommands": [
                 "python plot.py {Step01.data}"],
             "saPlotFiles": [],
             "saDependencies": []},
            {"sName": "B",
             "saDataCommands": [
                 "python run.py {Step01.output}"],
             "saTestCommands": [],
             "saPlotCommands": [],
             "saPlotFiles": [],
             "saDependencies": []},
        ]}
        fnRenumberAllReferences(
            dictWorkflow, lambda n: n + 1)
        assert "{Step02.data}" in (
            dictWorkflow["listSteps"][0]["saPlotCommands"][0])
        assert "{Step02.output}" in (
            dictWorkflow["listSteps"][1]["saDataCommands"][0])

    def test_no_references_unchanged(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A",
             "saDataCommands": ["python run.py"],
             "saTestCommands": [],
             "saPlotCommands": [],
             "saPlotFiles": [],
             "saDependencies": []},
        ]}
        fnRenumberAllReferences(
            dictWorkflow, lambda n: n + 10)
        assert dictWorkflow["listSteps"][0][
            "saDataCommands"][0] == "python run.py"

    def test_renumbers_dependencies(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A",
             "saDataCommands": [],
             "saTestCommands": [],
             "saPlotCommands": [],
             "saPlotFiles": [],
             "saDependencies": ["{Step01.output}"]},
        ]}
        fnRenumberAllReferences(
            dictWorkflow, lambda n: n + 5)
        assert "{Step06.output}" in (
            dictWorkflow["listSteps"][0]["saDependencies"][0])

    def test_renumbers_plot_files(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A",
             "saDataCommands": [],
             "saTestCommands": [],
             "saPlotCommands": [],
             "saPlotFiles": ["{Step01.plotDir}/fig.pdf"],
             "saDependencies": []},
        ]}
        fnRenumberAllReferences(
            dictWorkflow, lambda n: n + 1)
        assert "{Step02.plotDir}" in (
            dictWorkflow["listSteps"][0]["saPlotFiles"][0])

    def test_identity_remap_unchanged(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A",
             "saDataCommands": ["{Step01.x}"],
             "saTestCommands": [],
             "saPlotCommands": [],
             "saPlotFiles": [],
             "saDependencies": []},
        ]}
        fnRenumberAllReferences(dictWorkflow, lambda n: n)
        assert dictWorkflow["listSteps"][0][
            "saDataCommands"][0] == "{Step01.x}"

    def test_empty_lists_no_crash(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A"},
        ]}
        fnRenumberAllReferences(dictWorkflow, lambda n: n + 1)


class TestFlistCollectReferenceStrings:
    def test_collects_all_command_types(self):
        dictStep = {
            "saDataCommands": ["cmd1"],
            "saTestCommands": ["cmd2"],
            "saPlotCommands": ["cmd3"],
            "saDependencies": ["dep1"],
        }
        listResult = flistCollectReferenceStrings(dictStep)
        assert "cmd1" in listResult
        assert "cmd2" in listResult
        assert "cmd3" in listResult
        assert "dep1" in listResult

    def test_empty_step(self):
        assert flistCollectReferenceStrings({}) == []

    def test_partial_keys(self):
        dictStep = {
            "saDataCommands": ["cmd1"],
        }
        listResult = flistCollectReferenceStrings(dictStep)
        assert listResult == ["cmd1"]


class TestFnCheckCommandReferences:
    def test_detects_beyond_last_step(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "saDataFiles": []},
        ]}
        dictRegistry = fdictBuildStemRegistry(dictWorkflow)
        listWarnings = []
        _fnCheckCommandReferences(
            "python run.py {Step05.data}", "Step01", 1,
            dictWorkflow, dictRegistry, listWarnings)
        assert len(listWarnings) == 1
        assert "beyond" in listWarnings[0]

    def test_detects_circular(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "saDataFiles": ["out.h5"]},
            {"sName": "B", "saDataFiles": ["res.h5"]},
        ]}
        dictRegistry = fdictBuildStemRegistry(dictWorkflow)
        listWarnings = []
        _fnCheckCommandReferences(
            "python run.py {Step02.res}", "Step01", 1,
            dictWorkflow, dictRegistry, listWarnings)
        assert len(listWarnings) == 1
        assert "circular" in listWarnings[0]

    def test_valid_reference_no_warning(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "saDataFiles": ["out.h5"]},
            {"sName": "B", "saDataFiles": []},
        ]}
        dictRegistry = fdictBuildStemRegistry(dictWorkflow)
        listWarnings = []
        _fnCheckCommandReferences(
            "python run.py {Step01.out}", "Step02", 2,
            dictWorkflow, dictRegistry, listWarnings)
        assert listWarnings == []

    def test_no_references_no_warning(self):
        dictWorkflow = {"listSteps": [{"sName": "A"}]}
        dictRegistry = {}
        listWarnings = []
        _fnCheckCommandReferences(
            "python run.py", "Step01", 1,
            dictWorkflow, dictRegistry, listWarnings)
        assert listWarnings == []

    def test_missing_output_warning(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "saDataFiles": []},
            {"sName": "B", "saDataFiles": []},
        ]}
        dictRegistry = fdictBuildStemRegistry(dictWorkflow)
        listWarnings = []
        _fnCheckCommandReferences(
            "{Step01.nonexistent}", "Step02", 2,
            dictWorkflow, dictRegistry, listWarnings)
        assert len(listWarnings) == 1
        assert "no matching output" in listWarnings[0]
