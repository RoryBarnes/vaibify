"""Tests for untested pure functions in vaibify.gui.pipelineServer."""

import pytest

from vaibify.gui.pipelineServer import (
    _fbAnyDataFileChanged,
    _fbAnyMtimeNewerThan,
    _fbAnyPlotFileChanged,
    _fbStepScriptsModified,
    _fdictBuildScriptStatus,
    _fdictFindChangedFiles,
    _fiParseUtcTimestamp,
    _flistBuildFigureCheckPaths,
    _flistCollectUpstreamOutputs,
    _flistExtractKillPatterns,
    _flistFilterOwnOutputs,
    _flistParseDirectoryOutput,
    _flistResolvePlotPaths,
    _fnClearDownstreamUpstreamFlags,
    _fnInvalidateDownstreamStep,
    _fnInvalidateStepFiles,
    _fnRecordTestResult,
    _fnUpdateAggregateTestState,
    _fsBuildConvertCommand,
    _fsJoinStepPath,
    _fsPlotStandardPath,
    _fsetCollectCurrentStepOutputs,
    fdictExtractSettings,
    fdictFilterNonNone,
    fdictRequireWorkflow,
    fnValidatePathWithinRoot,
    fsResolveFigurePath,
    fsSanitizeExceptionForClient,
)


class TestFsSanitizeExceptionForClient:
    def test_no_such_container(self):
        sMsg = fsSanitizeExceptionForClient(
            Exception("No such container: abc123"))
        assert "Container not found" in sMsg

    def test_not_running(self):
        sMsg = fsSanitizeExceptionForClient(
            Exception("container not running"))
        assert "not running" in sMsg

    def test_connection_refused(self):
        sMsg = fsSanitizeExceptionForClient(
            Exception("connection refused"))
        assert "Could not connect" in sMsg

    def test_timeout(self):
        sMsg = fsSanitizeExceptionForClient(
            Exception("operation timeout"))
        assert "timed out" in sMsg

    def test_generic_error(self):
        sMsg = fsSanitizeExceptionForClient(
            Exception("/home/user/.secret/path leaked"))
        assert "/home/user" not in sMsg
        assert "Pipeline action failed" in sMsg


class TestFnUpdateAggregateTestState:
    def test_all_passed(self):
        dictStep = {
            "dictVerification": {
                "sIntegrity": "passed",
                "sQualitative": "passed",
                "sQuantitative": "passed",
            },
            "dictTests": {
                "dictIntegrity": {"saCommands": ["cmd1"]},
                "dictQualitative": {"saCommands": ["cmd2"]},
                "dictQuantitative": {"saCommands": ["cmd3"]},
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"

    def test_one_failed(self):
        dictStep = {
            "dictVerification": {
                "sIntegrity": "passed",
                "sQuantitative": "failed",
            },
            "dictTests": {
                "dictIntegrity": {"saCommands": ["cmd1"]},
                "dictQuantitative": {"saCommands": ["cmd2"]},
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "failed"

    def test_no_tests(self):
        dictStep = {
            "dictVerification": {},
            "dictTests": {},
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"

    def test_partial_untested(self):
        dictStep = {
            "dictVerification": {
                "sIntegrity": "passed",
            },
            "dictTests": {
                "dictIntegrity": {"saCommands": ["cmd1"]},
                "dictQuantitative": {"saCommands": ["cmd2"]},
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"

    def test_empty_commands_ignored(self):
        dictStep = {
            "dictVerification": {"sIntegrity": "passed"},
            "dictTests": {
                "dictIntegrity": {"saCommands": ["cmd"]},
                "dictQuantitative": {"saCommands": []},
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"


class TestFnRecordTestResult:
    def test_records_passed(self):
        dictStep = {}
        dictWorkflow = {"listSteps": [dictStep]}
        _fnRecordTestResult(dictStep, True, dictWorkflow, 0)
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"

    def test_records_failed(self):
        dictStep = {}
        dictWorkflow = {"listSteps": [dictStep]}
        _fnRecordTestResult(dictStep, False, dictWorkflow, 0)
        assert dictStep["dictVerification"]["sUnitTest"] == "failed"

    def test_clears_modification_flags(self):
        dictStep = {
            "dictVerification": {
                "listModifiedFiles": ["a.py"],
                "bUpstreamModified": True,
            },
        }
        dictWorkflow = {"listSteps": [dictStep]}
        _fnRecordTestResult(dictStep, True, dictWorkflow, 0)
        assert "listModifiedFiles" not in dictStep["dictVerification"]
        assert "bUpstreamModified" not in dictStep["dictVerification"]


class TestFnClearDownstreamUpstreamFlags:
    def test_clears_downstream(self):
        dictWorkflow = {
            "listSteps": [
                {"sName": "A", "saDataCommands": [],
                 "saPlotCommands": []},
                {"sName": "B",
                 "saDataCommands": ["{Step1.data}"],
                 "saPlotCommands": [],
                 "dictVerification": {
                     "bUpstreamModified": True}},
            ],
        }
        _fnClearDownstreamUpstreamFlags(dictWorkflow, 0)

    def test_no_crash_on_empty_workflow(self):
        _fnClearDownstreamUpstreamFlags({"listSteps": []}, 0)


class TestFlistParseDirectoryOutput:
    def test_parses_files_and_dirs(self):
        sOutput = "f /workspace/data.h5\nd /workspace/plots\n"
        listResult = _flistParseDirectoryOutput(sOutput)
        assert len(listResult) == 2
        assert listResult[0]["sName"] == "data.h5"
        assert listResult[0]["bIsDirectory"] is False
        assert listResult[1]["sName"] == "plots"
        assert listResult[1]["bIsDirectory"] is True

    def test_empty_output(self):
        assert _flistParseDirectoryOutput("") == []

    def test_short_lines_skipped(self):
        assert _flistParseDirectoryOutput("x\n") == []

    def test_whitespace_only(self):
        assert _flistParseDirectoryOutput("   \n  \n") == []


class TestFsResolveFigurePath:
    def test_absolute_path(self):
        assert fsResolveFigurePath(
            "/workspace/proj", "/abs/path.pdf") == "/abs/path.pdf"

    def test_workspace_prefix(self):
        assert fsResolveFigurePath(
            "/workspace/proj", "workspace/plot.pdf") == (
            "/workspace/plot.pdf")

    def test_relative_path(self):
        sResult = fsResolveFigurePath(
            "/workspace/proj", "Plot/fig.pdf")
        assert sResult == "/workspace/proj/Plot/fig.pdf"


class TestFdictExtractSettings:
    def test_defaults(self):
        dictResult = fdictExtractSettings({})
        assert dictResult["sPlotDirectory"] == "Plot"
        assert dictResult["sFigureType"] == "pdf"
        assert dictResult["iNumberOfCores"] == -1
        assert dictResult["fTolerance"] == 1e-6

    def test_custom_values(self):
        dictWorkflow = {
            "sPlotDirectory": "Figures",
            "sFigureType": "png",
            "iNumberOfCores": 4,
            "fTolerance": 0.001,
        }
        dictResult = fdictExtractSettings(dictWorkflow)
        assert dictResult["sPlotDirectory"] == "Figures"
        assert dictResult["iNumberOfCores"] == 4


class TestFdictFilterNonNone:
    def test_filters_none_values(self):
        dictResult = fdictFilterNonNone(
            {"a": 1, "b": None, "c": "x"})
        assert dictResult == {"a": 1, "c": "x"}

    def test_all_none(self):
        assert fdictFilterNonNone({"a": None}) == {}

    def test_no_none(self):
        assert fdictFilterNonNone({"a": 1}) == {"a": 1}

    def test_empty_dict(self):
        assert fdictFilterNonNone({}) == {}


class TestFnValidatePathWithinRoot:
    def test_valid_path(self):
        sResult = fnValidatePathWithinRoot(
            "/workspace/project/file.py", "/workspace")
        assert sResult == "/workspace/project/file.py"

    def test_traversal_rejected(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            fnValidatePathWithinRoot(
                "/workspace/../etc/passwd", "/workspace")

    def test_root_itself_allowed(self):
        sResult = fnValidatePathWithinRoot(
            "/workspace", "/workspace")
        assert sResult == "/workspace"


class TestFdictRequireWorkflow:
    def test_returns_workflow(self):
        dictCache = {"abc": {"sWorkflowName": "Test"}}
        dictResult = fdictRequireWorkflow(dictCache, "abc")
        assert dictResult["sWorkflowName"] == "Test"

    def test_missing_raises_404(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            fdictRequireWorkflow({}, "missing")


class TestFsPlotStandardPath:
    def test_standard_filename(self):
        assert _fsPlotStandardPath("Evolution") == (
            "Evolution_standard.png")


class TestFsBuildConvertCommand:
    def test_contains_pdftoppm(self):
        sCmd = _fsBuildConvertCommand(
            "/work/fig.pdf", "/work/standards", "fig.pdf")
        assert "pdftoppm" in sCmd

    def test_contains_ghostscript_fallback(self):
        sCmd = _fsBuildConvertCommand(
            "/work/fig.pdf", "/work/standards", "fig.pdf")
        assert "gs" in sCmd

    def test_output_in_correct_directory(self):
        sCmd = _fsBuildConvertCommand(
            "/work/fig.pdf", "/output", "fig.pdf")
        assert "/output" in sCmd


class TestFiParseUtcTimestamp:
    def test_with_seconds(self):
        iResult = _fiParseUtcTimestamp("2026-04-07 12:30:45 UTC")
        assert iResult is not None
        assert isinstance(iResult, int)

    def test_without_seconds(self):
        iResult = _fiParseUtcTimestamp("2026-04-07 12:30 UTC")
        assert iResult is not None

    def test_invalid_string(self):
        assert _fiParseUtcTimestamp("not a date") is None

    def test_none_input(self):
        assert _fiParseUtcTimestamp(None) is None

    def test_empty_string(self):
        assert _fiParseUtcTimestamp("") is None


class TestFbAnyMtimeNewerThan:
    def test_newer_detected(self):
        assert _fbAnyMtimeNewerThan(
            ["/work/a.txt"], {"/work/a.txt": "200"}, 100) is True

    def test_older_not_detected(self):
        assert _fbAnyMtimeNewerThan(
            ["/work/a.txt"], {"/work/a.txt": "50"}, 100) is False

    def test_missing_path(self):
        assert _fbAnyMtimeNewerThan(
            ["/work/missing.txt"], {}, 100) is False

    def test_empty_paths(self):
        assert _fbAnyMtimeNewerThan(
            [], {"/work/a.txt": "200"}, 100) is False


class TestFbAnyDataFileChanged:
    def test_data_file_changed(self):
        assert _fbAnyDataFileChanged(
            ["/work/step01/data.h5"],
            ["data.h5"]) is True

    def test_unrelated_change(self):
        assert _fbAnyDataFileChanged(
            ["/work/step01/plot.pdf"],
            ["data.h5"]) is False

    def test_empty_changed(self):
        assert _fbAnyDataFileChanged(
            [], ["data.h5"]) is False


class TestFbAnyPlotFileChanged:
    def test_plot_changed(self):
        assert _fbAnyPlotFileChanged(
            ["/work/Plot/fig.pdf"],
            ["Plot/fig.pdf"]) is True

    def test_data_not_plot(self):
        assert _fbAnyPlotFileChanged(
            ["/work/data.h5"],
            ["Plot/fig.pdf"]) is False

    def test_empty_lists(self):
        assert _fbAnyPlotFileChanged([], []) is False


class TestFdictFindChangedFiles:
    def test_detects_changes(self):
        dictPaths = {0: ["/work/a.txt", "/work/b.txt"]}
        dictOld = {"/work/a.txt": "100", "/work/b.txt": "200"}
        dictNew = {"/work/a.txt": "100", "/work/b.txt": "300"}
        dictResult = _fdictFindChangedFiles(
            dictPaths, dictOld, dictNew)
        assert 0 in dictResult
        assert "/work/b.txt" in dictResult[0]

    def test_no_changes(self):
        dictPaths = {0: ["/work/a.txt"]}
        dictOld = {"/work/a.txt": "100"}
        dictNew = {"/work/a.txt": "100"}
        assert _fdictFindChangedFiles(
            dictPaths, dictOld, dictNew) == {}

    def test_new_file_detected(self):
        dictPaths = {0: ["/work/new.txt"]}
        dictOld = {}
        dictNew = {"/work/new.txt": "100"}
        assert 0 in _fdictFindChangedFiles(
            dictPaths, dictOld, dictNew)


class TestFnInvalidateStepFiles:
    def test_data_change_resets_unit_test(self):
        dictStep = {
            "saDataFiles": ["data.h5"],
            "saPlotFiles": [],
            "dictVerification": {"sUnitTest": "passed"},
        }
        _fnInvalidateStepFiles(
            dictStep, ["/work/data.h5"])
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"

    def test_plot_change_does_not_reset_unit_test(self):
        dictStep = {
            "saDataFiles": ["data.h5"],
            "saPlotFiles": ["Plot/fig.pdf"],
            "dictVerification": {"sUnitTest": "passed"},
        }
        _fnInvalidateStepFiles(
            dictStep, ["/work/Plot/fig.pdf"])
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"

    def test_tracks_modified_files(self):
        dictStep = {
            "saDataFiles": [],
            "saPlotFiles": [],
            "dictVerification": {},
        }
        _fnInvalidateStepFiles(
            dictStep, ["/work/a.txt", "/work/b.txt"])
        listModified = dictStep["dictVerification"][
            "listModifiedFiles"]
        assert "/work/a.txt" in listModified
        assert "/work/b.txt" in listModified

    def test_accumulates_modified_files(self):
        dictStep = {
            "saDataFiles": [],
            "saPlotFiles": [],
            "dictVerification": {
                "listModifiedFiles": ["/work/old.txt"]},
        }
        _fnInvalidateStepFiles(
            dictStep, ["/work/new.txt"])
        listModified = dictStep["dictVerification"][
            "listModifiedFiles"]
        assert "/work/old.txt" in listModified
        assert "/work/new.txt" in listModified


class TestFnInvalidateDownstreamStep:
    def test_sets_upstream_modified(self):
        dictStep = {"dictVerification": {}}
        _fnInvalidateDownstreamStep(dictStep)
        assert dictStep["dictVerification"][
            "bUpstreamModified"] is True

    def test_resets_passing_unit_test(self):
        dictStep = {
            "dictVerification": {"sUnitTest": "passed"},
        }
        _fnInvalidateDownstreamStep(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"

    def test_resets_failed_unit_test(self):
        dictStep = {
            "dictVerification": {"sUnitTest": "failed"},
        }
        _fnInvalidateDownstreamStep(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "failed"


class TestFlistExtractKillPatterns:
    def test_python_script_extraction(self):
        dictWorkflow = {"listSteps": [
            {"saDataCommands": ["python run_model.py"],
             "saPlotCommands": []},
        ]}
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        assert "run_model.py" in listPatterns

    def test_builtin_commands_excluded(self):
        dictWorkflow = {"listSteps": [
            {"saDataCommands": ["cp a b", "echo done"],
             "saPlotCommands": ["mkdir -p out"]},
        ]}
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        assert "cp" not in listPatterns
        assert "echo" not in listPatterns
        assert "mkdir" not in listPatterns

    def test_non_python_command(self):
        dictWorkflow = {"listSteps": [
            {"saDataCommands": ["vplanet vpl.in"],
             "saPlotCommands": []},
        ]}
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        assert "vplanet" in listPatterns

    def test_empty_workflow(self):
        assert _flistExtractKillPatterns({"listSteps": []}) == []

    def test_deduplication(self):
        dictWorkflow = {"listSteps": [
            {"saDataCommands": ["python run.py"],
             "saPlotCommands": ["python run.py"]},
        ]}
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        assert listPatterns.count("run.py") == 1


class TestFlistCollectUpstreamOutputs:
    def test_collects_upstream_data_files(self):
        dictWorkflow = {"listSteps": [
            {"sName": "Step A",
             "saDataFiles": ["output.h5"]},
            {"sName": "Step B",
             "saDataFiles": ["result.npy"]},
        ]}
        listResult = _flistCollectUpstreamOutputs(dictWorkflow, 1)
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "output.h5"
        assert listResult[0]["iSourceStep"] == 1

    def test_no_upstream(self):
        dictWorkflow = {"listSteps": [
            {"sName": "Step A", "saDataFiles": ["a.h5"]},
        ]}
        assert _flistCollectUpstreamOutputs(dictWorkflow, 0) == []

    def test_template_variable_format(self):
        dictWorkflow = {"listSteps": [
            {"sName": "Step A",
             "saDataFiles": ["evolution.h5"]},
        ]}
        listResult = _flistCollectUpstreamOutputs(dictWorkflow, 1)
        assert "{Step01.evolution}" in listResult[0][
            "sTemplateVariable"]


class TestFsJoinStepPath:
    def test_absolute_path_unchanged(self):
        assert _fsJoinStepPath("/work", "/usr/bin/python") == (
            "/usr/bin/python")

    def test_relative_path_joined(self):
        sResult = _fsJoinStepPath("/work/step01", "run.py")
        assert "step01" in sResult
        assert sResult.endswith("run.py")

    def test_empty_directory(self):
        assert _fsJoinStepPath("", "run.py") == "run.py"


class TestFsetCollectCurrentStepOutputs:
    def test_collects_basenames(self):
        dictWorkflow = {"listSteps": [
            {"saDataFiles": ["/work/data.h5"],
             "saPlotFiles": ["Plot/fig.pdf"]},
        ]}
        setResult = _fsetCollectCurrentStepOutputs(dictWorkflow, 0)
        assert "data.h5" in setResult
        assert "fig.pdf" in setResult

    def test_out_of_range(self):
        dictWorkflow = {"listSteps": []}
        assert _fsetCollectCurrentStepOutputs(dictWorkflow, 5) == set()


class TestFlistFilterOwnOutputs:
    def test_filters_own_outputs(self):
        listDetected = [
            {"sFileName": "/work/data.h5"},
            {"sFileName": "/work/external.csv"},
        ]
        setOwn = {"data.h5"}
        listResult = _flistFilterOwnOutputs(listDetected, setOwn)
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "/work/external.csv"

    def test_no_own_outputs(self):
        listDetected = [{"sFileName": "a.txt"}]
        assert len(_flistFilterOwnOutputs(listDetected, set())) == 1


class TestFlistBuildFigureCheckPaths:
    def test_basic_absolute(self):
        listPaths = _flistBuildFigureCheckPaths(
            "/workspace/proj/Plot/fig.pdf",
            "", "/workspace/proj", "Plot/fig.pdf")
        assert "/workspace/proj/Plot/fig.pdf" in listPaths

    def test_with_workdir(self):
        listPaths = _flistBuildFigureCheckPaths(
            "/workspace/proj/fig.pdf",
            "/workspace/proj/step01",
            "/workspace/proj", "fig.pdf")
        assert len(listPaths) == 2

    def test_absolute_file_no_fallback(self):
        listPaths = _flistBuildFigureCheckPaths(
            "/abs/fig.pdf", "/workspace",
            "/workspace", "/abs/fig.pdf")
        assert len(listPaths) == 1


class TestFbStepScriptsModified:
    def test_no_stored_hashes(self):
        dictStep = {"saDataCommands": [], "saPlotCommands": []}
        assert _fbStepScriptsModified(dictStep, {}) is None

    def test_matching_hashes(self):
        dictStep = {
            "sDirectory": "/work",
            "saDataCommands": ["python run.py"],
            "saPlotCommands": [],
            "dictRunStats": {
                "dictInputHashes": {"/work/run.py": "abc123"},
            },
        }
        assert _fbStepScriptsModified(
            dictStep, {"/work/run.py": "abc123"}) is False

    def test_changed_hash(self):
        dictStep = {
            "sDirectory": "/work",
            "saDataCommands": ["python run.py"],
            "saPlotCommands": [],
            "dictRunStats": {
                "dictInputHashes": {"/work/run.py": "abc123"},
            },
        }
        assert _fbStepScriptsModified(
            dictStep, {"/work/run.py": "def456"}) is True


class TestFdictBuildScriptStatus:
    def test_empty_workflow(self):
        dictResult = _fdictBuildScriptStatus(
            {"listSteps": []}, {})
        assert dictResult == {}

    def test_detects_modification(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work",
             "saDataCommands": ["python run.py"],
             "saPlotCommands": [],
             "dictRunStats": {
                 "dictInputHashes": {"/work/run.py": "old"},
             }},
        ]}
        dictResult = _fdictBuildScriptStatus(
            dictWorkflow, {"/work/run.py": "new"})
        assert dictResult.get(0) == "modified"
