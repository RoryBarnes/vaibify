"""Tests for untested pure functions in vaibify.gui.pipelineRunner."""

import time

from vaibify.gui.pipelineRunner import (
    _fParseCpuTime,
    _fdictBuildWorkflowVars,
    _fiAggregateTestExitCode,
    _flistCollectCategoryLogs,
    _flistFilterUnexpectedFiles,
    _flistResolveTestCommands,
    _fnRecordRunStats,
    _fnToggleSelectedSteps,
    _fsExtractLogLine,
    _fsExtractScriptPath,
    _fsWrapWithTime,
    ffBuildLoggingCallback,
    fnClearOutputModifiedFlags,
    fsComputeStepLabel,
)


class TestFsComputeStepLabel:
    def test_automatic_step_first(self):
        dictWorkflow = {"listSteps": [{"sName": "A"}]}
        assert fsComputeStepLabel(dictWorkflow, 1) == "A01"

    def test_automatic_step_second(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A"}, {"sName": "B"},
        ]}
        assert fsComputeStepLabel(dictWorkflow, 2) == "A02"

    def test_interactive_step(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "bInteractive": True},
        ]}
        assert fsComputeStepLabel(dictWorkflow, 1) == "I01"

    def test_mixed_step_numbering(self):
        dictWorkflow = {"listSteps": [
            {"sName": "Auto1"},
            {"sName": "Inter1", "bInteractive": True},
            {"sName": "Auto2"},
            {"sName": "Inter2", "bInteractive": True},
        ]}
        assert fsComputeStepLabel(dictWorkflow, 1) == "A01"
        assert fsComputeStepLabel(dictWorkflow, 2) == "I01"
        assert fsComputeStepLabel(dictWorkflow, 3) == "A02"
        assert fsComputeStepLabel(dictWorkflow, 4) == "I02"

    def test_out_of_range_step(self):
        dictWorkflow = {"listSteps": []}
        assert fsComputeStepLabel(dictWorkflow, 5) == "05"

    def test_negative_step(self):
        dictWorkflow = {"listSteps": [{"sName": "A"}]}
        assert fsComputeStepLabel(dictWorkflow, 0) == "00"


class TestFnRecordRunStats:
    def test_records_timestamp(self):
        dictStep = {}
        fStartTime = time.time()
        _fnRecordRunStats(dictStep, "2026-04-07 12:00:00 UTC",
                          fStartTime, 1.5)
        assert dictStep["dictRunStats"]["sLastRun"] == (
            "2026-04-07 12:00:00 UTC")

    def test_records_cpu_time(self):
        dictStep = {}
        _fnRecordRunStats(dictStep, "2026-04-07", time.time(), 3.14)
        assert dictStep["dictRunStats"]["fCpuTime"] == 3.1

    def test_records_wall_clock(self):
        dictStep = {}
        fStart = time.time() - 2.0
        _fnRecordRunStats(dictStep, "now", fStart, 0.0)
        assert dictStep["dictRunStats"]["fWallClock"] >= 1.9

    def test_overwrites_existing_stats(self):
        dictStep = {"dictRunStats": {"sLastRun": "old"}}
        _fnRecordRunStats(dictStep, "new", time.time(), 0.0)
        assert dictStep["dictRunStats"]["sLastRun"] == "new"


class TestFParseCpuTime:
    def test_parses_valid_output(self):
        sOutput = "some output\n__VAIBIFY_CPU__ 1.23 0.45\ndone"
        assert _fParseCpuTime(sOutput) == 1.68

    def test_no_cpu_line_returns_zero(self):
        assert _fParseCpuTime("just output\nno cpu here") == 0.0

    def test_empty_output(self):
        assert _fParseCpuTime("") == 0.0

    def test_malformed_cpu_line(self):
        assert _fParseCpuTime("__VAIBIFY_CPU__ bad data") == 0.0

    def test_only_user_time(self):
        assert _fParseCpuTime("__VAIBIFY_CPU__ 2.5") == 0.0

    def test_first_cpu_line_wins(self):
        sOutput = (
            "__VAIBIFY_CPU__ 1.0 2.0\n"
            "__VAIBIFY_CPU__ 9.0 9.0\n"
        )
        assert _fParseCpuTime(sOutput) == 3.0


class TestFsWrapWithTime:
    def test_contains_original_command(self):
        sWrapped = _fsWrapWithTime("python run.py")
        assert "python run.py" in sWrapped

    def test_contains_time_binary(self):
        sWrapped = _fsWrapWithTime("echo hello")
        assert "/usr/bin/time" in sWrapped

    def test_contains_vaibify_cpu_marker(self):
        sWrapped = _fsWrapWithTime("ls")
        assert "__VAIBIFY_CPU__" in sWrapped

    def test_fallback_when_time_missing(self):
        sWrapped = _fsWrapWithTime("ls")
        assert "else ls; fi" in sWrapped


class TestFlistFilterUnexpectedFiles:
    def test_no_expected_returns_all(self):
        dictStep = {"saDataFiles": [], "saPlotFiles": []}
        setNew = {"/work/output.csv"}
        listResult = _flistFilterUnexpectedFiles(
            setNew, "/work", dictStep)
        assert len(listResult) == 1
        assert listResult[0]["sFilePath"] == "output.csv"

    def test_expected_files_excluded(self):
        dictStep = {
            "saDataFiles": ["data.h5"],
            "saPlotFiles": ["plot.pdf"],
        }
        setNew = {"/work/data.h5", "/work/surprise.txt"}
        listResult = _flistFilterUnexpectedFiles(
            setNew, "/work", dictStep)
        assert len(listResult) == 1
        assert listResult[0]["sFilePath"] == "surprise.txt"

    def test_empty_new_files(self):
        dictStep = {"saDataFiles": [], "saPlotFiles": []}
        assert _flistFilterUnexpectedFiles(
            set(), "/work", dictStep) == []

    def test_absolute_path_matching(self):
        dictStep = {
            "saDataFiles": ["/work/abs.npy"],
            "saPlotFiles": [],
        }
        setNew = {"/work/abs.npy"}
        assert _flistFilterUnexpectedFiles(
            setNew, "/work", dictStep) == []


class TestFlistResolveTestCommands:
    def test_legacy_commands(self):
        dictStep = {"saTestCommands": ["pytest tests/"]}
        assert _flistResolveTestCommands(dictStep) == ["pytest tests/"]

    def test_structured_tests(self):
        dictStep = {
            "dictTests": {
                "dictIntegrity": {
                    "saCommands": ["pytest tests/test_integrity.py"],
                },
                "dictQuantitative": {
                    "saCommands": ["pytest tests/test_quant.py"],
                },
            },
        }
        listResult = _flistResolveTestCommands(dictStep)
        assert len(listResult) == 2

    def test_no_tests_returns_empty(self):
        dictStep = {}
        assert _flistResolveTestCommands(dictStep) == []

    def test_structured_overrides_legacy(self):
        dictStep = {
            "saTestCommands": ["old command"],
            "dictTests": {
                "dictIntegrity": {"saCommands": ["new cmd"]},
            },
        }
        listResult = _flistResolveTestCommands(dictStep)
        assert "old command" not in listResult
        assert "new cmd" in listResult


class TestFlistCollectCategoryLogs:
    def test_collects_from_multiple_categories(self):
        dictResults = {
            "dictIntegrity": {"sOutput": "line1\nline2"},
            "dictQuantitative": {"sOutput": "line3"},
        }
        listLines = _flistCollectCategoryLogs(dictResults)
        assert listLines == ["line1", "line2", "line3"]

    def test_empty_output_ignored(self):
        dictResults = {
            "dictIntegrity": {"sOutput": ""},
            "dictQuantitative": {"sOutput": "data"},
        }
        listLines = _flistCollectCategoryLogs(dictResults)
        assert listLines == ["data"]

    def test_no_categories(self):
        assert _flistCollectCategoryLogs({}) == []


class TestFiAggregateTestExitCode:
    def test_all_passed(self):
        dictResults = {
            "a": {"iExitCode": 0},
            "b": {"iExitCode": 0},
        }
        assert _fiAggregateTestExitCode(dictResults) == 0

    def test_one_failed(self):
        dictResults = {
            "a": {"iExitCode": 0},
            "b": {"iExitCode": 1},
        }
        assert _fiAggregateTestExitCode(dictResults) == 1

    def test_all_failed(self):
        dictResults = {
            "a": {"iExitCode": 2},
            "b": {"iExitCode": 1},
        }
        assert _fiAggregateTestExitCode(dictResults) == 1

    def test_empty_dict_passes(self):
        assert _fiAggregateTestExitCode({}) == 0

    def test_missing_exit_code_treated_as_failure(self):
        dictResults = {"a": {}}
        assert _fiAggregateTestExitCode(dictResults) == 1


class TestFnClearOutputModifiedFlags:
    def test_clears_modification_flags(self):
        dictWorkflow = {"listSteps": [
            {"dictVerification": {
                "bOutputModified": True,
                "listModifiedFiles": ["a.txt"],
                "bUpstreamModified": True,
                "sUnitTest": "passed",
            }},
        ]}
        fnClearOutputModifiedFlags(dictWorkflow)
        dictVerification = dictWorkflow["listSteps"][0][
            "dictVerification"]
        assert "bOutputModified" not in dictVerification
        assert "listModifiedFiles" not in dictVerification
        assert "bUpstreamModified" not in dictVerification
        assert dictVerification["sUnitTest"] == "passed"

    def test_handles_missing_verification(self):
        dictWorkflow = {"listSteps": [{}]}
        fnClearOutputModifiedFlags(dictWorkflow)

    def test_handles_empty_steps(self):
        fnClearOutputModifiedFlags({"listSteps": []})


class TestFdictBuildWorkflowVars:
    def test_default_values(self):
        dictResult = _fdictBuildWorkflowVars({})
        assert dictResult["sPlotDirectory"] == "Plot"
        assert dictResult["sFigureType"] == "pdf"

    def test_custom_values(self):
        dictWorkflow = {
            "sPlotDirectory": "Figures",
            "sFigureType": "png",
        }
        dictResult = _fdictBuildWorkflowVars(dictWorkflow)
        assert dictResult["sPlotDirectory"] == "Figures"
        assert dictResult["sFigureType"] == "png"


class TestFnToggleSelectedSteps:
    def test_enables_selected_only(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "bEnabled": True},
            {"sName": "B", "bEnabled": True},
            {"sName": "C", "bEnabled": True},
        ]}
        _fnToggleSelectedSteps(dictWorkflow, [0, 2])
        assert dictWorkflow["listSteps"][0]["bEnabled"] is True
        assert dictWorkflow["listSteps"][1]["bEnabled"] is False
        assert dictWorkflow["listSteps"][2]["bEnabled"] is True

    def test_empty_selection_disables_all(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "bEnabled": True},
        ]}
        _fnToggleSelectedSteps(dictWorkflow, [])
        assert dictWorkflow["listSteps"][0]["bEnabled"] is False


class TestFsExtractLogLine:
    def test_output_event(self):
        dictEvent = {"sType": "output", "sLine": "hello world"}
        assert _fsExtractLogLine(dictEvent) == "hello world"

    def test_command_failed_event(self):
        dictEvent = {
            "sType": "commandFailed",
            "sCommand": "python run.py",
            "iExitCode": 1,
        }
        sResult = _fsExtractLogLine(dictEvent)
        assert "FAILED" in sResult
        assert "python run.py" in sResult

    def test_unknown_event_returns_none(self):
        assert _fsExtractLogLine({"sType": "stepStarted"}) is None

    def test_empty_dict(self):
        assert _fsExtractLogLine({}) is None


class TestFsExtractScriptPath:
    def test_builtin_commands_return_none(self):
        for sCmd in ("cd /tmp", "echo hello", "mkdir -p foo",
                     "cp a b", "rm file", "bash -c 'test'"):
            assert _fsExtractScriptPath(sCmd) is None

    def test_empty_command(self):
        assert _fsExtractScriptPath("") is None

    def test_simple_script(self):
        sResult = _fsExtractScriptPath("python run.py")
        assert sResult is not None


class TestFfBuildLoggingCallback:
    def test_max_log_lines_enforced(self):
        listLogLines = list(range(10000))
        import asyncio

        async def fnDummy(dictEvent):
            pass

        async def fnRunTest():
            fnCallback = ffBuildLoggingCallback(fnDummy, listLogLines)
            await fnCallback(
                {"sType": "output", "sLine": "overflow"})
            assert len(listLogLines) <= 10000
            assert listLogLines[-1] == "overflow"

        asyncio.run(fnRunTest())
