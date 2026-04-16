"""Tests covering functions critical to safe refactoring.

These tests target the untested pure functions identified in the
architecture review, organized by refactoring phase.
"""

from vaibify.gui.pipelineServer import (
    _fbCheckStaleUserVerification,
    _fbPlotNewerThanUserVerification,
    _fdictBuildFileStatusVars,
    _fdictBuildTestResponse,
    _fdictComputeMaxMtimeByStep,
    _fdictComputeMaxPlotMtimeByStep,
    _fdictInvalidateAffectedSteps,
    _flistCollectOutputPaths,
    _flistResolvePlotPaths,
    _flistResolveStepPaths,
    _flistStandardizedBasenames,
    _fnClearStepModificationState,
    _fnRegisterTestCommand,
    _fnUpdateModTimeBaseline,
    _fsBuildPytestCommand,
    _fsFindPlotPath,
    _fsFindStandardForFile,
    fdictCollectOutputPathsByStep,
)


# ---------------------------------------------------------------
# Phase 2: File-status polling extraction
# ---------------------------------------------------------------


class TestFdictComputeMaxMtimeByStep:
    def test_single_step_single_file(self):
        dictPaths = {0: ["/work/data.h5"]}
        dictMod = {"/work/data.h5": "1712500000"}
        dictResult = _fdictComputeMaxMtimeByStep(dictPaths, dictMod)
        assert dictResult == {"0": "1712500000"}

    def test_picks_max_mtime(self):
        dictPaths = {0: ["/work/a.h5", "/work/b.h5"]}
        dictMod = {"/work/a.h5": "100", "/work/b.h5": "200"}
        assert _fdictComputeMaxMtimeByStep(dictPaths, dictMod) == {
            "0": "200"}

    def test_missing_files_ignored(self):
        dictPaths = {0: ["/work/a.h5", "/work/missing.h5"]}
        dictMod = {"/work/a.h5": "100"}
        assert _fdictComputeMaxMtimeByStep(dictPaths, dictMod) == {
            "0": "100"}

    def test_all_files_missing(self):
        dictPaths = {0: ["/work/gone.h5"]}
        dictMod = {}
        assert _fdictComputeMaxMtimeByStep(dictPaths, dictMod) == {}

    def test_multiple_steps(self):
        dictPaths = {
            0: ["/work/a.h5"],
            1: ["/work/b.h5"],
        }
        dictMod = {"/work/a.h5": "100", "/work/b.h5": "200"}
        dictResult = _fdictComputeMaxMtimeByStep(dictPaths, dictMod)
        assert dictResult["0"] == "100"
        assert dictResult["1"] == "200"

    def test_empty_paths(self):
        assert _fdictComputeMaxMtimeByStep({}, {}) == {}

    def test_keys_are_strings(self):
        dictPaths = {3: ["/work/a.h5"]}
        dictMod = {"/work/a.h5": "100"}
        dictResult = _fdictComputeMaxMtimeByStep(dictPaths, dictMod)
        assert "3" in dictResult
        assert 3 not in dictResult


class TestFdictComputeMaxPlotMtimeByStep:
    def test_single_step_with_plot(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "listSteps": [
                {"sDirectory": "/work/step01",
                 "saPlotFiles": [
                     "{sPlotDirectory}/fig.{sFigureType}"]},
            ],
        }
        dictMod = {"/work/step01/Plot/fig.pdf": "500"}
        dictResult = _fdictComputeMaxPlotMtimeByStep(
            dictWorkflow, dictMod)
        assert dictResult == {"0": "500"}

    def test_no_plot_files(self):
        dictWorkflow = {
            "listSteps": [
                {"sDirectory": "/work", "saPlotFiles": []},
            ],
        }
        assert _fdictComputeMaxPlotMtimeByStep(
            dictWorkflow, {}) == {}

    def test_data_files_excluded(self):
        dictWorkflow = {
            "listSteps": [
                {"sDirectory": "/work",
                 "saDataFiles": ["data.h5"],
                 "saPlotFiles": []},
            ],
        }
        dictMod = {"/work/data.h5": "999"}
        assert _fdictComputeMaxPlotMtimeByStep(
            dictWorkflow, dictMod) == {}

    def test_empty_workflow(self):
        assert _fdictComputeMaxPlotMtimeByStep(
            {"listSteps": []}, {}) == {}


class TestFlistResolveStepPaths:
    def test_resolves_data_and_plot(self):
        dictStep = {
            "sDirectory": "/work/step01",
            "saDataFiles": ["data.h5"],
            "saPlotFiles": ["Plot/fig.pdf"],
        }
        dictVars = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}
        listPaths = _flistResolveStepPaths(dictStep, dictVars)
        assert len(listPaths) == 2
        assert "/work/step01/data.h5" in listPaths
        assert "/work/step01/Plot/fig.pdf" in listPaths

    def test_absolute_paths_unchanged(self):
        dictStep = {
            "sDirectory": "/work",
            "saDataFiles": ["/abs/data.h5"],
            "saPlotFiles": [],
        }
        listPaths = _flistResolveStepPaths(dictStep, {})
        assert listPaths == ["/abs/data.h5"]

    def test_template_variables_resolved(self):
        dictStep = {
            "sDirectory": "/work",
            "saDataFiles": [],
            "saPlotFiles": [
                "{sPlotDirectory}/fig.{sFigureType}"],
        }
        dictVars = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}
        listPaths = _flistResolveStepPaths(dictStep, dictVars)
        assert listPaths == ["/work/Plot/fig.pdf"]

    def test_empty_files(self):
        dictStep = {
            "sDirectory": "/work",
            "saDataFiles": [],
            "saPlotFiles": [],
        }
        assert _flistResolveStepPaths(dictStep, {}) == []


class TestFdictCollectOutputPathsByStep:
    def test_multiple_steps(self):
        dictWorkflow = {
            "listSteps": [
                {"sDirectory": "/work/s1",
                 "saDataFiles": ["a.h5"], "saPlotFiles": []},
                {"sDirectory": "/work/s2",
                 "saDataFiles": [], "saPlotFiles": ["p.pdf"]},
            ],
        }
        dictResult = fdictCollectOutputPathsByStep(dictWorkflow)
        assert 0 in dictResult
        assert 1 in dictResult
        assert "/work/s1/a.h5" in dictResult[0]
        assert "/work/s2/p.pdf" in dictResult[1]

    def test_empty_workflow(self):
        assert fdictCollectOutputPathsByStep(
            {"listSteps": []}) == {}


class TestFlistCollectOutputPaths:
    def test_flat_list(self):
        dictWorkflow = {
            "listSteps": [
                {"sDirectory": "/work",
                 "saDataFiles": ["a.h5"],
                 "saPlotFiles": ["b.pdf"]},
            ],
        }
        listPaths = _flistCollectOutputPaths(dictWorkflow)
        assert len(listPaths) == 2


class TestFdictInvalidateAffectedSteps:
    def test_direct_change_invalidates(self):
        dictWorkflow = {
            "listSteps": [
                {"sName": "A",
                 "saDataFiles": ["data.h5"],
                 "saPlotFiles": [],
                 "saDataCommands": [],
                 "saPlotCommands": [],
                 "dictVerification": {"sUnitTest": "passed"}},
            ],
        }
        dictChanged = {0: ["/work/data.h5"]}
        dictResult = _fdictInvalidateAffectedSteps(
            dictWorkflow, dictChanged)
        assert 0 in dictResult

    def test_downstream_step_gets_upstream_flag(self):
        dictWorkflow = {
            "listSteps": [
                {"sName": "A",
                 "saDataFiles": ["data.h5"],
                 "saPlotFiles": [],
                 "saDataCommands": [],
                 "saPlotCommands": [],
                 "dictVerification": {}},
                {"sName": "B",
                 "saDataFiles": [],
                 "saPlotFiles": [],
                 "saDataCommands": ["{Step1.data}"],
                 "saPlotCommands": [],
                 "dictVerification": {"sUnitTest": "passed"}},
            ],
        }
        dictChanged = {0: ["/work/data.h5"]}
        dictResult = _fdictInvalidateAffectedSteps(
            dictWorkflow, dictChanged)
        assert 1 in dictResult
        bUpstream = dictResult[1].get("bUpstreamModified", False)
        assert bUpstream is True

    def test_empty_changes(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": [],
             "dictVerification": {"sUnitTest": "passed"}},
        ]}
        assert _fdictInvalidateAffectedSteps(
            dictWorkflow, {}) == {}

    def test_out_of_range_index_ignored(self):
        dictWorkflow = {"listSteps": []}
        dictChanged = {99: ["/work/phantom.h5"]}
        assert _fdictInvalidateAffectedSteps(
            dictWorkflow, dictChanged) == {}


# ---------------------------------------------------------------
# Phase 2: Verification state machine gaps
# ---------------------------------------------------------------


class TestFbPlotNewerThanUserVerification:
    def test_plot_changed_no_user_update(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig.pdf"],
            "dictVerification": {},
        }
        listChanged = ["/work/Plot/fig.pdf"]
        assert _fbPlotNewerThanUserVerification(
            dictStep, listChanged, {}) is True

    def test_plot_changed_user_update_older(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig.pdf"],
            "dictVerification": {
                "sLastUserUpdate": "2026-01-01 00:00:00 UTC",
            },
        }
        listChanged = ["/work/Plot/fig.pdf"]
        dictMod = {"/work/Plot/fig.pdf": "1800000000"}
        assert _fbPlotNewerThanUserVerification(
            dictStep, listChanged, dictMod) is True

    def test_no_plot_change(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig.pdf"],
            "dictVerification": {},
        }
        listChanged = ["/work/data.h5"]
        assert _fbPlotNewerThanUserVerification(
            dictStep, listChanged, {}) is False

    def test_plot_changed_user_update_newer(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig.pdf"],
            "dictVerification": {
                "sLastUserUpdate": "2099-01-01 00:00:00 UTC",
            },
        }
        listChanged = ["/work/Plot/fig.pdf"]
        dictMod = {"/work/Plot/fig.pdf": "1712500000"}
        assert _fbPlotNewerThanUserVerification(
            dictStep, listChanged, dictMod) is False

    def test_invalid_timestamp_returns_true(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig.pdf"],
            "dictVerification": {
                "sLastUserUpdate": "not a date",
            },
        }
        listChanged = ["/work/Plot/fig.pdf"]
        assert _fbPlotNewerThanUserVerification(
            dictStep, listChanged, {}) is True


class TestFbCheckStaleUserVerification:
    def test_resets_stale_verification(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "listSteps": [
                {"sDirectory": "/work",
                 "saPlotFiles": [
                     "{sPlotDirectory}/fig.{sFigureType}"],
                 "dictVerification": {
                     "sUser": "passed",
                     "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
                 }},
            ],
        }
        dictMod = {"/work/Plot/fig.pdf": "1900000000"}
        bChanged = _fbCheckStaleUserVerification(
            dictWorkflow, dictMod)
        assert bChanged is True
        assert dictWorkflow["listSteps"][0][
            "dictVerification"]["sUser"] == "untested"

    def test_preserves_fresh_verification(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "listSteps": [
                {"sDirectory": "/work",
                 "saPlotFiles": [
                     "{sPlotDirectory}/fig.{sFigureType}"],
                 "dictVerification": {
                     "sUser": "passed",
                     "sLastUserUpdate": "2099-01-01 00:00:00 UTC",
                 }},
            ],
        }
        dictMod = {"/work/Plot/fig.pdf": "1712500000"}
        bChanged = _fbCheckStaleUserVerification(
            dictWorkflow, dictMod)
        assert dictWorkflow["listSteps"][0][
            "dictVerification"]["sUser"] == "passed"

    def test_skips_non_passed_steps(self):
        dictWorkflow = {
            "listSteps": [
                {"saPlotFiles": [],
                 "dictVerification": {
                     "sUser": "untested",
                     "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
                 }},
            ],
        }
        bChanged = _fbCheckStaleUserVerification(
            dictWorkflow, {})
        assert bChanged is False

    def test_skips_no_last_user_update(self):
        dictWorkflow = {
            "listSteps": [
                {"saPlotFiles": [],
                 "dictVerification": {"sUser": "passed"}},
            ],
        }
        bChanged = _fbCheckStaleUserVerification(
            dictWorkflow, {})
        assert bChanged is False

    def test_empty_workflow(self):
        assert _fbCheckStaleUserVerification(
            {"listSteps": []}, {}) is False

    def test_clears_modification_flags_when_fresh(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "listSteps": [
                {"sDirectory": "/work",
                 "saPlotFiles": [
                     "{sPlotDirectory}/fig.{sFigureType}"],
                 "dictVerification": {
                     "sUser": "passed",
                     "sLastUserUpdate": "2099-01-01 00:00:00 UTC",
                     "listModifiedFiles": ["/work/old.txt"],
                     "bOutputModified": True,
                 }},
            ],
        }
        dictMod = {"/work/Plot/fig.pdf": "1712500000"}
        bChanged = _fbCheckStaleUserVerification(
            dictWorkflow, dictMod)
        assert bChanged is True
        dictVerify = dictWorkflow["listSteps"][0][
            "dictVerification"]
        assert "listModifiedFiles" not in dictVerify
        assert "bOutputModified" not in dictVerify
        assert dictVerify["sUser"] == "passed"


# ---------------------------------------------------------------
# Phase 2: Helper functions
# ---------------------------------------------------------------


class TestFdictBuildFileStatusVars:
    def test_defaults(self):
        dictResult = _fdictBuildFileStatusVars({})
        assert dictResult["sPlotDirectory"] == "Plot"
        assert dictResult["sFigureType"] == "pdf"

    def test_custom_values(self):
        dictWorkflow = {
            "sPlotDirectory": "Figures",
            "sFigureType": "png",
        }
        dictResult = _fdictBuildFileStatusVars(dictWorkflow)
        assert dictResult["sPlotDirectory"] == "Figures"
        assert dictResult["sFigureType"] == "png"


class TestFnClearStepModificationState:
    def test_clears_flags(self):
        dictWorkflow = {"listSteps": [
            {"dictVerification": {
                "listModifiedFiles": ["a.txt"],
                "bOutputModified": True,
                "sUnitTest": "passed",
            }},
        ]}
        _fnClearStepModificationState(dictWorkflow, 0)
        dictVerify = dictWorkflow["listSteps"][0][
            "dictVerification"]
        assert "listModifiedFiles" not in dictVerify
        assert "bOutputModified" not in dictVerify
        assert dictVerify["sUnitTest"] == "passed"

    def test_out_of_range_no_crash(self):
        _fnClearStepModificationState({"listSteps": []}, 5)

    def test_missing_verification_no_crash(self):
        dictWorkflow = {"listSteps": [{}]}
        _fnClearStepModificationState(dictWorkflow, 0)


class TestFnUpdateModTimeBaseline:
    def test_sets_baseline(self):
        dictCtx = {}
        dictMod = {"/work/a.h5": "100"}
        _fnUpdateModTimeBaseline(dictCtx, "container1", dictMod)
        assert dictCtx["dictPreviousModTimes"]["container1"] == {
            "/work/a.h5": "100"}

    def test_overwrites_existing(self):
        dictCtx = {"dictPreviousModTimes": {
            "c1": {"/work/old.h5": "50"}}}
        _fnUpdateModTimeBaseline(dictCtx, "c1", {"/work/new.h5": "99"})
        assert "/work/old.h5" not in (
            dictCtx["dictPreviousModTimes"]["c1"])

    def test_copies_not_references(self):
        dictCtx = {}
        dictMod = {"/work/a.h5": "100"}
        _fnUpdateModTimeBaseline(dictCtx, "c1", dictMod)
        dictMod["/work/a.h5"] = "999"
        assert dictCtx["dictPreviousModTimes"]["c1"][
            "/work/a.h5"] == "100"


# ---------------------------------------------------------------
# Phase 3: Test routes/logic extraction
# ---------------------------------------------------------------


class TestFsBuildPytestCommand:
    def test_contains_cd_and_pytest(self):
        sCmd = _fsBuildPytestCommand("/work/step01", "tests/test_a.py")
        assert "cd" in sCmd
        assert "pytest" in sCmd
        assert "tests/test_a.py" in sCmd

    def test_shell_quotes_directory(self):
        sCmd = _fsBuildPytestCommand("/work/my step", "test.py")
        assert "'" in sCmd

    def test_verbose_flag(self):
        sCmd = _fsBuildPytestCommand("/work", "test.py")
        assert "-v" in sCmd


class TestFnRegisterTestCommand:
    def test_registers_on_pass(self):
        dictStep = {}
        _fnRegisterTestCommand(dictStep, True, "tests/test_a.py")
        assert len(dictStep["saTestCommands"]) == 1
        assert "test_a.py" in dictStep["saTestCommands"][0]

    def test_skips_on_fail(self):
        dictStep = {}
        _fnRegisterTestCommand(dictStep, False, "tests/test_a.py")
        assert "saTestCommands" not in dictStep

    def test_no_duplicate_registration(self):
        dictStep = {"saTestCommands": [
            "python -m pytest tests/test_a.py -v"]}
        _fnRegisterTestCommand(dictStep, True, "tests/test_a.py")
        assert len(dictStep["saTestCommands"]) == 1

    def test_appends_to_existing(self):
        dictStep = {"saTestCommands": [
            "python -m pytest tests/test_a.py -v"]}
        _fnRegisterTestCommand(dictStep, True, "tests/test_b.py")
        assert len(dictStep["saTestCommands"]) == 2


class TestFdictBuildTestResponse:
    def test_all_passed(self):
        dictCats = {
            "dictIntegrity": {"bPassed": True, "iExitCode": 0},
            "dictQuantitative": {"bPassed": True, "iExitCode": 0},
        }
        dictResult = _fdictBuildTestResponse(True, dictCats)
        assert dictResult["bPassed"] is True
        assert dictResult["iExitCode"] == 0
        assert "dictCategoryResults" in dictResult

    def test_one_failed(self):
        dictCats = {
            "dictIntegrity": {"bPassed": True, "iExitCode": 0},
            "dictQuantitative": {"bPassed": False, "iExitCode": 1},
        }
        dictResult = _fdictBuildTestResponse(False, dictCats)
        assert dictResult["bPassed"] is False
        assert dictResult["iExitCode"] == 1

    def test_max_exit_code(self):
        dictCats = {
            "a": {"bPassed": False, "iExitCode": 2},
            "b": {"bPassed": False, "iExitCode": 5},
        }
        dictResult = _fdictBuildTestResponse(False, dictCats)
        assert dictResult["iExitCode"] == 5

    def test_empty_categories(self):
        dictResult = _fdictBuildTestResponse(True, {})
        assert dictResult["bPassed"] is True
        assert dictResult["iExitCode"] == 0


# ---------------------------------------------------------------
# Phase 3: Plot standardization helpers
# ---------------------------------------------------------------


class TestFlistResolvePlotPaths:
    def test_resolves_template_variables(self):
        dictStep = {
            "sDirectory": "/work",
            "saPlotFiles": [
                "{sPlotDirectory}/fig.{sFigureType}"],
        }
        dictVars = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}
        listResult = _flistResolvePlotPaths(dictStep, dictVars)
        assert len(listResult) == 1
        sPath, sBasename = listResult[0]
        assert sPath == "/work/Plot/fig.pdf"
        assert sBasename == "fig.pdf"

    def test_absolute_path(self):
        dictStep = {
            "sDirectory": "/work",
            "saPlotFiles": ["/abs/fig.pdf"],
        }
        listResult = _flistResolvePlotPaths(dictStep, {})
        assert listResult[0][0] == "/abs/fig.pdf"

    def test_empty_plot_files(self):
        dictStep = {"sDirectory": "/work", "saPlotFiles": []}
        assert _flistResolvePlotPaths(dictStep, {}) == []


class TestFlistStandardizedBasenames:
    def test_all_plots(self):
        listPlots = [("/work/a.pdf", "a.pdf"), ("/work/b.pdf", "b.pdf")]
        listResult = _flistStandardizedBasenames(listPlots, "")
        assert listResult == ["a.pdf", "b.pdf"]

    def test_single_target(self):
        listPlots = [("/work/a.pdf", "a.pdf"), ("/work/b.pdf", "b.pdf")]
        listResult = _flistStandardizedBasenames(listPlots, "a.pdf")
        assert listResult == ["a.pdf"]

    def test_no_match(self):
        listPlots = [("/work/a.pdf", "a.pdf")]
        listResult = _flistStandardizedBasenames(listPlots, "missing.pdf")
        assert listResult == []


class TestFsFindPlotPath:
    def test_finds_by_basename(self):
        listPlots = [
            ("/work/Plot/fig.pdf", "fig.pdf"),
            ("/work/Plot/other.pdf", "other.pdf"),
        ]
        assert _fsFindPlotPath(listPlots, "fig.pdf") == (
            "/work/Plot/fig.pdf")

    def test_finds_by_suffix(self):
        listPlots = [("/work/Plot/fig.pdf", "fig.pdf")]
        assert _fsFindPlotPath(listPlots, "Plot/fig.pdf") == (
            "/work/Plot/fig.pdf")

    def test_not_found(self):
        listPlots = [("/work/Plot/fig.pdf", "fig.pdf")]
        assert _fsFindPlotPath(listPlots, "missing.pdf") == ""


class TestFsFindStandardForFile:
    def test_finds_standard_path(self):
        listPlots = [("/work/Plot/fig.pdf", "fig.pdf")]
        sResult = _fsFindStandardForFile(listPlots, "fig.pdf")
        assert "fig_standard.png" in sResult
        assert "/work/Plot/" in sResult

    def test_not_found(self):
        listPlots = [("/work/Plot/fig.pdf", "fig.pdf")]
        assert _fsFindStandardForFile(listPlots, "nope.pdf") == ""
