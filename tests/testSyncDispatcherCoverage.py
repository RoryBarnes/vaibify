"""Tests for untested pure functions in vaibify.gui.syncDispatcher."""

import json

from vaibify.gui.syncDispatcher import (
    _fbSafeDirectoryName,
    _fdictParsePorcelainLine,
    _flistArchivePlotPaths,
    _flistBuildDagEdges,
    _fsBuildStepCopyCommands,
    _fsGenerateGitIgnore,
    _fsGenerateReadme,
    _fsNormalizePath,
    fdictParseTestMarkerOutput,
    flistCollectOutputFiles,
    flistExtractAllScriptPaths,
    flistGetDirtyFiles,
    fsBuildTestMarkerCheckCommand,
    ftResultPushStagedToGithub,
)


class _FakeDockerConnection:
    """Mock connectionDocker capturing commands and returning canned results."""

    def __init__(self, tResult=(0, "")):
        self._tResult = tResult
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return self._tResult


class TestFsNormalizePath:
    def test_absolute_path_unchanged(self):
        assert _fsNormalizePath("/work", "/usr/bin/python") == (
            "/usr/bin/python")

    def test_relative_path_joined(self):
        sResult = _fsNormalizePath("/work/step01", "run.py")
        assert sResult == "/work/step01/run.py"

    def test_dot_dot_normalized(self):
        sResult = _fsNormalizePath("/work/step01", "../shared/lib.py")
        assert sResult == "/work/shared/lib.py"

    def test_empty_directory(self):
        sResult = _fsNormalizePath("", "run.py")
        assert sResult == "run.py"


class TestFlistBuildDagEdges:
    def test_no_dependencies(self):
        listSteps = [
            {"sName": "A", "saDataCommands": ["python run.py"],
             "saPlotCommands": [], "saTestCommands": []},
        ]
        assert _flistBuildDagEdges({"listSteps": listSteps}) == []

    def test_simple_dependency(self):
        listSteps = [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": []},
            {"sName": "B",
             "saDataCommands": ["python run.py {Step1.sOutput}"],
             "saPlotCommands": [], "saTestCommands": []},
        ]
        listEdges = _flistBuildDagEdges({"listSteps": listSteps})
        assert len(listEdges) == 1
        assert "step1 -> step2" in listEdges[0]

    def test_multiple_dependencies(self):
        listSteps = [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": []},
            {"sName": "B", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": []},
            {"sName": "C",
             "saDataCommands": [
                 "python {Step1.sData} {Step2.sPlot}"],
             "saPlotCommands": [], "saTestCommands": []},
        ]
        listEdges = _flistBuildDagEdges({"listSteps": listSteps})
        assert len(listEdges) == 2

    def test_no_duplicate_edges(self):
        listSteps = [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": []},
            {"sName": "B",
             "saDataCommands": [
                 "cmd {Step1.sA} {Step1.sB}"],
             "saPlotCommands": [], "saTestCommands": []},
        ]
        listEdges = _flistBuildDagEdges({"listSteps": listSteps})
        assert len(listEdges) == 1

    def test_dependency_in_plot_commands(self):
        listSteps = [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": []},
            {"sName": "B", "saDataCommands": [],
             "saPlotCommands": ["plot {Step1.sFile}"],
             "saTestCommands": []},
        ]
        assert len(_flistBuildDagEdges({"listSteps": listSteps})) == 1

    def test_dependency_in_sa_dependencies(self):
        listSteps = [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": [],
             "saDependencies": []},
            {"sName": "B", "saDataCommands": [],
             "saPlotCommands": [], "saTestCommands": [],
             "saDependencies": ["{Step1.sOutput}"]},
        ]
        assert len(_flistBuildDagEdges({"listSteps": listSteps})) == 1


class TestFlistExtractAllScriptPaths:
    def test_empty_workflow(self):
        assert flistExtractAllScriptPaths({"listSteps": []}) == []

    def test_no_commands(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work", "saDataCommands": [],
             "saPlotCommands": []},
        ]}
        assert flistExtractAllScriptPaths(dictWorkflow) == []

    def test_deduplication(self):
        dictWorkflow = {"listSteps": [
            {"sDirectory": "/work",
             "saDataCommands": ["python run.py"],
             "saPlotCommands": ["python run.py"]},
        ]}
        listPaths = flistExtractAllScriptPaths(dictWorkflow)
        assert len(listPaths) == len(set(listPaths))


class TestFlistCollectOutputFiles:
    def test_collects_data_and_plot_files(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A",
             "saDataFiles": ["data.h5"],
             "saPlotFiles": ["plot.pdf"]},
        ]}
        listResult = flistCollectOutputFiles(dictWorkflow, {})
        assert len(listResult) == 2
        sPaths = {d["sPath"] for d in listResult}
        assert "data.h5" in sPaths
        assert "plot.pdf" in sPaths

    def test_sync_status_attached(self):
        dictWorkflow = {"listSteps": [
            {"sName": "A", "saDataFiles": ["data.h5"],
             "saPlotFiles": []},
        ]}
        dictSync = {"data.h5": {"bPushed": True}}
        listResult = flistCollectOutputFiles(dictWorkflow, dictSync)
        assert listResult[0]["dictSync"]["bPushed"] is True

    def test_empty_workflow(self):
        assert flistCollectOutputFiles({"listSteps": []}, {}) == []

    def test_missing_file_keys(self):
        dictWorkflow = {"listSteps": [{"sName": "A"}]}
        assert flistCollectOutputFiles(dictWorkflow, {}) == []


class TestFlistArchivePlotPaths:
    def test_archive_category_included(self):
        dictStep = {"saPlotFiles": ["fig.pdf"]}

        def fsMockGetCategory(dictStep, sFile):
            return "archive"

        listResult = _flistArchivePlotPaths(
            dictStep, "/work/step01", fsMockGetCategory)
        assert len(listResult) == 1
        assert "/work/step01/fig.pdf" in listResult[0]

    def test_non_archive_excluded(self):
        dictStep = {"saPlotFiles": ["fig.pdf"]}

        def fsMockGetCategory(dictStep, sFile):
            return "display"

        listResult = _flistArchivePlotPaths(
            dictStep, "/work", fsMockGetCategory)
        assert listResult == []

    def test_absolute_path_preserved(self):
        dictStep = {"saPlotFiles": ["/abs/fig.pdf"]}

        def fsMockGetCategory(dictStep, sFile):
            return "archive"

        listResult = _flistArchivePlotPaths(
            dictStep, "/work", fsMockGetCategory)
        assert listResult[0] == "/abs/fig.pdf"

    def test_empty_plot_files(self):
        dictStep = {"saPlotFiles": []}
        assert _flistArchivePlotPaths(
            dictStep, "/work", lambda d, f: "archive") == []


class TestFsBuildStepCopyCommands:
    def test_builds_mkdir_and_copy(self):
        sResult = _fsBuildStepCopyCommands(
            "/work/step01", "stepOne",
            ["run.py"], [])
        assert "mkdir -p" in sResult
        assert "cp" in sResult
        assert "stepOne" in sResult

    def test_archive_plots_use_pdftoppm(self):
        sResult = _fsBuildStepCopyCommands(
            "/work/step01", "stepOne",
            [], ["/work/step01/fig.pdf"])
        assert "pdftoppm" in sResult

    def test_no_scripts_no_plots(self):
        sResult = _fsBuildStepCopyCommands(
            "/work", "stepDir", [], [])
        assert "mkdir -p" in sResult


class TestFsGenerateGitIgnore:
    def test_contains_common_patterns(self):
        sResult = _fsGenerateGitIgnore()
        assert "*.npy" in sResult
        assert "*.h5" in sResult
        assert ".vaibify/logs/" in sResult

    def test_contains_plot_pdf(self):
        sResult = _fsGenerateGitIgnore()
        assert "Plot/*.pdf" in sResult


class TestFsGenerateReadme:
    def test_contains_workflow_name(self):
        dictWorkflow = {
            "sProjectTitle": "GJ 1132 XUV",
            "listSteps": [],
        }
        sResult = _fsGenerateReadme(dictWorkflow)
        assert "GJ 1132 XUV" in sResult

    def test_lists_step_names(self):
        dictWorkflow = {
            "sProjectTitle": "Test",
            "listSteps": [
                {"sName": "Build Model"},
                {"sName": "Plot Results"},
            ],
        }
        sResult = _fsGenerateReadme(dictWorkflow)
        assert "Build Model" in sResult
        assert "Plot Results" in sResult

    def test_contains_vaibify_link(self):
        dictWorkflow = {"listSteps": []}
        sResult = _fsGenerateReadme(dictWorkflow)
        assert "Vaibify" in sResult


class TestFdictParseTestMarkerOutput:
    def test_valid_json(self):
        dictExpected = {
            "markers": {"test.json": {"bPassed": True}},
            "testFiles": {},
            "missingConftest": [],
        }
        sOutput = json.dumps(dictExpected)
        assert fdictParseTestMarkerOutput(sOutput) == dictExpected

    def test_empty_string(self):
        dictResult = fdictParseTestMarkerOutput("")
        assert dictResult["markers"] == {}
        assert dictResult["testFiles"] == {}

    def test_none_input(self):
        dictResult = fdictParseTestMarkerOutput(None)
        assert dictResult["markers"] == {}

    def test_invalid_json(self):
        dictResult = fdictParseTestMarkerOutput("{bad json")
        assert dictResult["markers"] == {}

    def test_whitespace_only(self):
        dictResult = fdictParseTestMarkerOutput("   \n  ")
        assert dictResult["markers"] == {}


class TestFbSafeDirectoryName:
    def test_simple_path(self):
        assert _fbSafeDirectoryName("/workspace/step01") is True

    def test_path_with_spaces(self):
        assert _fbSafeDirectoryName("/work/My Step") is True

    def test_path_with_dots(self):
        assert _fbSafeDirectoryName("/work/.vaibify") is True

    def test_shell_injection_rejected(self):
        assert _fbSafeDirectoryName("/work; rm -rf /") is False

    def test_backtick_rejected(self):
        assert _fbSafeDirectoryName("/work/`whoami`") is False

    def test_dollar_sign_rejected(self):
        assert _fbSafeDirectoryName("/work/$HOME") is False

    def test_empty_string(self):
        assert _fbSafeDirectoryName("") is False


class TestFsBuildTestMarkerCheckCommand:
    def test_produces_python_command(self):
        sCmd = fsBuildTestMarkerCheckCommand(["/workspace/step01"])
        assert sCmd.startswith("python3 -c ")

    def test_unsafe_dirs_filtered(self):
        sCmd = fsBuildTestMarkerCheckCommand(
            ["/workspace/step01", "/bad;rm -rf /"])
        assert "bad" not in sCmd
        assert "step01" in sCmd

    def test_empty_dirs(self):
        sCmd = fsBuildTestMarkerCheckCommand([])
        assert "python3 -c" in sCmd

    def test_multiple_safe_dirs(self):
        sCmd = fsBuildTestMarkerCheckCommand(
            ["/workspace/A01", "/workspace/A02"])
        assert "A01" in sCmd
        assert "A02" in sCmd


class TestFtResultPushStagedToGithub:
    def test_success_returns_zero(self):
        fake = _FakeDockerConnection((0, "abc1234\n"))
        iExit, sOut = ftResultPushStagedToGithub(
            fake, "cid", "Fix bug", "/workspace/proj")
        assert iExit == 0
        assert "abc1234" in sOut

    def test_does_not_run_git_add(self):
        fake = _FakeDockerConnection((0, ""))
        ftResultPushStagedToGithub(
            fake, "cid", "msg", "/workspace/proj")
        sCommand = fake.listCommands[0][1]
        assert "git add" not in sCommand

    def test_command_contains_commit_push_revparse(self):
        fake = _FakeDockerConnection((0, ""))
        ftResultPushStagedToGithub(
            fake, "cid", "msg", "/workspace/proj")
        sCommand = fake.listCommands[0][1]
        assert "git commit -m" in sCommand
        assert "git push" in sCommand
        assert "git rev-parse --short HEAD" in sCommand
        assert "cd '/workspace/proj'" in sCommand

    def test_chained_with_and_operator(self):
        fake = _FakeDockerConnection((0, ""))
        ftResultPushStagedToGithub(
            fake, "cid", "msg", "/workspace/proj")
        sCommand = fake.listCommands[0][1]
        assert " && " in sCommand

    def test_commit_failure_surfaced(self):
        fake = _FakeDockerConnection(
            (1, "nothing to commit, working tree clean"))
        iExit, sOut = ftResultPushStagedToGithub(
            fake, "cid", "msg", "/workspace/proj")
        assert iExit == 1
        assert "nothing to commit" in sOut

    def test_push_failure_surfaced(self):
        fake = _FakeDockerConnection((128, "fatal: unable to access"))
        iExit, sOut = ftResultPushStagedToGithub(
            fake, "cid", "msg", "/workspace/proj")
        assert iExit == 128
        assert "fatal" in sOut

    def test_shell_quotes_single_quote_message(self):
        fake = _FakeDockerConnection((0, ""))
        ftResultPushStagedToGithub(
            fake, "cid", "it's a fix", "/workspace/proj")
        sCommand = fake.listCommands[0][1]
        assert "'it'\\''s a fix'" in sCommand

    def test_shell_quotes_workdir_with_spaces(self):
        fake = _FakeDockerConnection((0, ""))
        ftResultPushStagedToGithub(
            fake, "cid", "msg", "/work space/proj")
        sCommand = fake.listCommands[0][1]
        assert "'/work space/proj'" in sCommand


class TestFdictParsePorcelainLine:
    def test_modified(self):
        dictEntry = _fdictParsePorcelainLine(" M path/to/file.py")
        assert dictEntry == {
            "sPath": "path/to/file.py", "sStatus": "modified"}

    def test_added(self):
        dictEntry = _fdictParsePorcelainLine("A  new.py")
        assert dictEntry["sStatus"] == "added"
        assert dictEntry["sPath"] == "new.py"

    def test_deleted(self):
        dictEntry = _fdictParsePorcelainLine(" D gone.py")
        assert dictEntry["sStatus"] == "deleted"

    def test_untracked(self):
        dictEntry = _fdictParsePorcelainLine("?? unknown.py")
        assert dictEntry["sStatus"] == "untracked"

    def test_renamed(self):
        dictEntry = _fdictParsePorcelainLine("R  old -> new")
        assert dictEntry["sStatus"] == "renamed"

    def test_unknown_code(self):
        dictEntry = _fdictParsePorcelainLine("ZZ foo.py")
        assert dictEntry["sStatus"] == "unknown"

    def test_too_short(self):
        assert _fdictParsePorcelainLine("M") is None


class TestFlistGetDirtyFiles:
    def test_empty_workdir_returns_empty_list(self):
        fake = _FakeDockerConnection((0, ""))
        listResult = flistGetDirtyFiles(fake, "cid", "/workspace/proj")
        assert listResult == []

    def test_uses_git_c_workdir(self):
        fake = _FakeDockerConnection((0, ""))
        flistGetDirtyFiles(fake, "cid", "/workspace/proj")
        sCommand = fake.listCommands[0][1]
        assert "git -C '/workspace/proj' status --porcelain" in sCommand

    def test_parses_mixed_statuses(self):
        sOutput = (
            " M scripts/foo.py\n"
            "A  scripts/new.py\n"
            " D old.py\n"
            "?? notes.txt\n"
            "R  a.py -> b.py\n"
        )
        fake = _FakeDockerConnection((0, sOutput))
        listResult = flistGetDirtyFiles(fake, "cid", "/workspace/proj")
        assert len(listResult) == 5
        listStatuses = [d["sStatus"] for d in listResult]
        assert "modified" in listStatuses
        assert "added" in listStatuses
        assert "deleted" in listStatuses
        assert "untracked" in listStatuses
        assert "renamed" in listStatuses

    def test_non_zero_exit_returns_empty(self):
        fake = _FakeDockerConnection((128, "not a git repository"))
        listResult = flistGetDirtyFiles(fake, "cid", "/tmp/nowhere")
        assert listResult == []

    def test_path_with_spaces(self):
        fake = _FakeDockerConnection((0, " M my script.py\n"))
        listResult = flistGetDirtyFiles(fake, "cid", "/workspace/proj")
        assert len(listResult) == 1
        assert listResult[0]["sPath"] == "my script.py"
        assert listResult[0]["sStatus"] == "modified"

    def test_blank_lines_skipped(self):
        fake = _FakeDockerConnection((0, "\n M foo.py\n\n"))
        listResult = flistGetDirtyFiles(fake, "cid", "/workspace/proj")
        assert len(listResult) == 1

    def test_shell_quotes_workdir(self):
        fake = _FakeDockerConnection((0, ""))
        flistGetDirtyFiles(fake, "cid", "/work space/proj")
        sCommand = fake.listCommands[0][1]
        assert "'/work space/proj'" in sCommand


class TestSyncDispatcherExports:
    def test_new_symbols_in_all(self):
        from vaibify.gui import syncDispatcher
        assert "ftResultPushStagedToGithub" in syncDispatcher.__all__
        assert "flistGetDirtyFiles" in syncDispatcher.__all__
