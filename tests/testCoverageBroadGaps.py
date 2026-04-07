"""Tests targeting the broadest coverage gaps across the codebase.

Targets:
  - shellSetup.py (21% -> high)
  - commandTest.py (32% -> higher)
  - commandWorkflow.py (41% -> higher)
  - commandLs.py (44% -> higher)
  - commandRun.py (51% -> higher)
  - commandCat.py (57% -> higher)
  - commandVerifyStep.py (61% -> higher)
  - pipelineServer.py pure helpers (78% -> higher)
  - pipelineRunner.py helper functions
  - containerManager.py uncovered branches
"""

import asyncio
import json
import os
import platform
import sys
import tempfile

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, mock_open, call


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a mock Docker connection."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (iExitCode, sOutput)
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"fake content"
    return mockDocker


def _fMockConfig(sProjectName="testproj"):
    """Return a mock project config."""
    return SimpleNamespace(sProjectName=sProjectName)


# =======================================================================
# 1. shellSetup.py — (21% coverage)
# =======================================================================


class TestShellSetupDetection:
    """Test shell detection functions."""

    def test_fsDetectShellName_from_env(self):
        from vaibify.install.shellSetup import _fsDetectShellName
        with patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            assert _fsDetectShellName() == "zsh"

    def test_fsDetectShellName_bash(self):
        from vaibify.install.shellSetup import _fsDetectShellName
        with patch.dict(os.environ, {"SHELL": "/usr/bin/bash"}):
            assert _fsDetectShellName() == "bash"

    def test_fsDetectShellName_default(self):
        from vaibify.install.shellSetup import _fsDetectShellName
        with patch.dict(os.environ, {}, clear=True):
            sResult = _fsDetectShellName()
            assert sResult == "sh"

    def test_fsDetectShellRcFile_zsh(self):
        from vaibify.install.shellSetup import _fsDetectShellRcFile
        sResult = _fsDetectShellRcFile("zsh")
        assert sResult.endswith(".zshrc")

    def test_fsDetectShellRcFile_bash_darwin(self):
        from vaibify.install.shellSetup import _fsDetectShellRcFile
        with patch("vaibify.install.shellSetup.platform") as mockPlatform:
            mockPlatform.system.return_value = "Darwin"
            sResult = _fsDetectShellRcFile("bash")
            assert ".bash_profile" in sResult

    def test_fsDetectShellRcFile_bash_linux(self):
        from vaibify.install.shellSetup import _fsDetectShellRcFile
        with patch("vaibify.install.shellSetup.platform") as mockPlatform:
            mockPlatform.system.return_value = "Linux"
            sResult = _fsDetectShellRcFile("bash")
            assert ".bashrc" in sResult

    def test_fsDetectShellRcFile_fish(self):
        from vaibify.install.shellSetup import _fsDetectShellRcFile
        sResult = _fsDetectShellRcFile("fish")
        assert "config.fish" in sResult

    def test_fsDetectShellRcFile_unknown(self):
        from vaibify.install.shellSetup import _fsDetectShellRcFile
        assert _fsDetectShellRcFile("csh") == ""


class TestShellSetupRcFile:
    """Test RC file reading and appending."""

    def test_fbRcFileContainsLine_found(self, tmp_path):
        from vaibify.install.shellSetup import _fbRcFileContainsLine
        sPath = str(tmp_path / "rc")
        with open(sPath, "w") as fileHandle:
            fileHandle.write("source /path/to/vaibify\n")
        assert _fbRcFileContainsLine(sPath, "vaibify") is True

    def test_fbRcFileContainsLine_not_found(self, tmp_path):
        from vaibify.install.shellSetup import _fbRcFileContainsLine
        sPath = str(tmp_path / "rc")
        with open(sPath, "w") as fileHandle:
            fileHandle.write("alias ls='ls -la'\n")
        assert _fbRcFileContainsLine(sPath, "vaibify") is False

    def test_fbRcFileContainsLine_missing_file(self):
        from vaibify.install.shellSetup import _fbRcFileContainsLine
        assert _fbRcFileContainsLine("/nonexistent/path", "x") is False

    def test_fnAppendToRcFile_writes_content(self, tmp_path):
        from vaibify.install.shellSetup import _fnAppendToRcFile
        sPath = str(tmp_path / "rc")
        with open(sPath, "w") as fileHandle:
            fileHandle.write("# existing\n")
        _fnAppendToRcFile(sPath, "alias foo='bar'")
        with open(sPath) as fileHandle:
            sContent = fileHandle.read()
        assert "Added by Vaibify" in sContent
        assert "alias foo='bar'" in sContent

    def test_fnAppendToRcFile_handles_permission_error(self):
        from vaibify.install.shellSetup import _fnAppendToRcFile
        _fnAppendToRcFile("/proc/nonexistent/bad", "test")


class TestShellSetupCompletions:
    """Test completion configuration."""

    def test_fsCompletionsDirectory_returns_path(self):
        from vaibify.install.shellSetup import _fsCompletionsDirectory
        sResult = _fsCompletionsDirectory()
        assert "completions" in sResult

    def test_fsCompletionPathForShell_unknown_shell(self):
        from vaibify.install.shellSetup import _fsCompletionPathForShell
        assert _fsCompletionPathForShell("fish") == ""

    def test_fsCompletionPathForShell_bash_missing_file(self):
        from vaibify.install.shellSetup import _fsCompletionPathForShell
        with patch(
            "vaibify.install.shellSetup._fsCompletionsDirectory",
            return_value="/nonexistent",
        ):
            assert _fsCompletionPathForShell("bash") == ""

    def test_fnConfigureCompletions_swallows_errors(self):
        from vaibify.install.shellSetup import fnConfigureCompletions
        with patch(
            "vaibify.install.shellSetup._fnConfigureCompletionsInner",
            side_effect=RuntimeError("boom"),
        ):
            fnConfigureCompletions()

    def test_fnConfigureCompletionsInner_no_file(self):
        from vaibify.install.shellSetup import _fnConfigureCompletionsInner
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="zsh",
        ), patch(
            "vaibify.install.shellSetup._fsCompletionPathForShell",
            return_value="",
        ):
            _fnConfigureCompletionsInner()

    def test_fnConfigureCompletionsInner_no_rc(self):
        from vaibify.install.shellSetup import _fnConfigureCompletionsInner
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="csh",
        ), patch(
            "vaibify.install.shellSetup._fsCompletionPathForShell",
            return_value="/some/file",
        ), patch(
            "vaibify.install.shellSetup._fsDetectShellRcFile",
            return_value="",
        ):
            _fnConfigureCompletionsInner()

    def test_fnConfigureCompletionsInner_already_present(self):
        from vaibify.install.shellSetup import _fnConfigureCompletionsInner
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="zsh",
        ), patch(
            "vaibify.install.shellSetup._fsCompletionPathForShell",
            return_value="/path/to/comp",
        ), patch(
            "vaibify.install.shellSetup._fsDetectShellRcFile",
            return_value="/home/user/.zshrc",
        ), patch(
            "vaibify.install.shellSetup._fbRcFileContainsLine",
            return_value=True,
        ):
            _fnConfigureCompletionsInner()

    def test_fnConfigureCompletionsInner_appends(self):
        from vaibify.install.shellSetup import _fnConfigureCompletionsInner
        mockAppend = MagicMock()
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="zsh",
        ), patch(
            "vaibify.install.shellSetup._fsCompletionPathForShell",
            return_value="/path/to/comp",
        ), patch(
            "vaibify.install.shellSetup._fsDetectShellRcFile",
            return_value="/home/user/.zshrc",
        ), patch(
            "vaibify.install.shellSetup._fbRcFileContainsLine",
            return_value=False,
        ), patch(
            "vaibify.install.shellSetup._fnAppendToRcFile",
            mockAppend,
        ):
            _fnConfigureCompletionsInner()
            mockAppend.assert_called_once()
            sBlock = mockAppend.call_args[0][1]
            assert "/path/to/comp" in sBlock


class TestShellSetupHelperCommands:
    """Test helper command alias configuration."""

    def test_fsHelperAliasBlock_fish(self):
        from vaibify.install.shellSetup import _fsHelperAliasBlock
        sResult = _fsHelperAliasBlock("fish")
        assert "alias vaibify_connect 'vaibify connect'" in sResult

    def test_fsHelperAliasBlock_bash(self):
        from vaibify.install.shellSetup import _fsHelperAliasBlock
        sResult = _fsHelperAliasBlock("bash")
        assert "alias vaibify_connect='vaibify connect'" in sResult

    def test_fnConfigureHelperCommands_swallows_errors(self):
        from vaibify.install.shellSetup import fnConfigureHelperCommands
        with patch(
            "vaibify.install.shellSetup._fnConfigureHelperCommandsInner",
            side_effect=RuntimeError("boom"),
        ):
            fnConfigureHelperCommands()

    def test_fnConfigureHelperCommandsInner_no_rc(self):
        from vaibify.install.shellSetup import (
            _fnConfigureHelperCommandsInner,
        )
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="csh",
        ), patch(
            "vaibify.install.shellSetup._fsDetectShellRcFile",
            return_value="",
        ):
            _fnConfigureHelperCommandsInner()

    def test_fnConfigureHelperCommandsInner_already_present(self):
        from vaibify.install.shellSetup import (
            _fnConfigureHelperCommandsInner,
        )
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="zsh",
        ), patch(
            "vaibify.install.shellSetup._fsDetectShellRcFile",
            return_value="/home/user/.zshrc",
        ), patch(
            "vaibify.install.shellSetup._fbRcFileContainsLine",
            return_value=True,
        ):
            _fnConfigureHelperCommandsInner()

    def test_fnConfigureHelperCommandsInner_appends(self):
        from vaibify.install.shellSetup import (
            _fnConfigureHelperCommandsInner,
        )
        mockAppend = MagicMock()
        with patch(
            "vaibify.install.shellSetup._fsDetectShellName",
            return_value="bash",
        ), patch(
            "vaibify.install.shellSetup._fsDetectShellRcFile",
            return_value="/home/user/.bashrc",
        ), patch(
            "vaibify.install.shellSetup._fbRcFileContainsLine",
            return_value=False,
        ), patch(
            "vaibify.install.shellSetup._fnAppendToRcFile",
            mockAppend,
        ):
            _fnConfigureHelperCommandsInner()
            mockAppend.assert_called_once()


class TestShellSetupColima:
    """Test Colima socket linking."""

    def test_fnLinkColimaSocket_swallows_errors(self):
        from vaibify.install.shellSetup import fnLinkColimaSocket
        with patch(
            "vaibify.install.shellSetup._fnLinkColimaSocketInner",
            side_effect=RuntimeError("boom"),
        ):
            fnLinkColimaSocket()

    def test_fnLinkColimaSocketInner_not_darwin(self):
        from vaibify.install.shellSetup import _fnLinkColimaSocketInner
        with patch(
            "vaibify.install.shellSetup.platform"
        ) as mockPlatform:
            mockPlatform.system.return_value = "Linux"
            _fnLinkColimaSocketInner()

    def test_fnLinkColimaSocketInner_socket_exists(self):
        from vaibify.install.shellSetup import _fnLinkColimaSocketInner
        with patch(
            "vaibify.install.shellSetup.platform"
        ) as mockPlatform, patch(
            "vaibify.install.shellSetup.os.path.exists",
            return_value=True,
        ):
            mockPlatform.system.return_value = "Darwin"
            _fnLinkColimaSocketInner()

    def test_fnLinkColimaSocketInner_no_write_access(self):
        from vaibify.install.shellSetup import _fnLinkColimaSocketInner
        with patch(
            "vaibify.install.shellSetup.platform"
        ) as mockPlatform, patch(
            "vaibify.install.shellSetup.os.path.exists",
            return_value=False,
        ), patch(
            "vaibify.install.shellSetup.os.access",
            return_value=False,
        ):
            mockPlatform.system.return_value = "Darwin"
            _fnLinkColimaSocketInner()

    def test_fnLinkColimaSocketInner_no_colima_socket(self):
        from vaibify.install.shellSetup import _fnLinkColimaSocketInner

        def fnSideEffectExists(sPath):
            if "docker.sock" in sPath and "/var/run" in sPath:
                return False
            if ".colima" in sPath:
                return False
            return True

        with patch(
            "vaibify.install.shellSetup.platform"
        ) as mockPlatform, patch(
            "vaibify.install.shellSetup.os.path.exists",
            side_effect=fnSideEffectExists,
        ), patch(
            "vaibify.install.shellSetup.os.access",
            return_value=True,
        ):
            mockPlatform.system.return_value = "Darwin"
            _fnLinkColimaSocketInner()


class TestShellSetupMarker:
    """Test marker file and orchestration."""

    def test_fbIsSetupComplete_false(self):
        from vaibify.install.shellSetup import fbIsSetupComplete
        with patch(
            "vaibify.install.shellSetup.os.path.isfile",
            return_value=False,
        ):
            assert fbIsSetupComplete() is False

    def test_fbIsSetupComplete_true(self):
        from vaibify.install.shellSetup import fbIsSetupComplete
        with patch(
            "vaibify.install.shellSetup.os.path.isfile",
            return_value=True,
        ):
            assert fbIsSetupComplete() is True

    def test_fnWriteMarkerFile_writes(self, tmp_path):
        from vaibify.install import shellSetup
        sOldMarker = shellSetup._MARKER_PATH
        sTestMarker = str(tmp_path / ".setup_done")
        shellSetup._MARKER_PATH = sTestMarker
        try:
            shellSetup._fnWriteMarkerFile()
            assert os.path.isfile(sTestMarker)
            with open(sTestMarker) as fileHandle:
                assert "setup complete" in fileHandle.read()
        finally:
            shellSetup._MARKER_PATH = sOldMarker

    def test_fnWriteMarkerFile_handles_error(self):
        from vaibify.install import shellSetup
        sOldMarker = shellSetup._MARKER_PATH
        shellSetup._MARKER_PATH = "/proc/nonexistent/marker"
        try:
            shellSetup._fnWriteMarkerFile()
        finally:
            shellSetup._MARKER_PATH = sOldMarker

    def test_fnRunFirstTimeSetup(self, tmp_path):
        from vaibify.install import shellSetup
        sOldDir = shellSetup._MARKER_DIR
        sOldPath = shellSetup._MARKER_PATH
        shellSetup._MARKER_DIR = str(tmp_path / "vaibify")
        shellSetup._MARKER_PATH = str(tmp_path / "vaibify" / ".setup_done")
        try:
            with patch(
                "vaibify.install.shellSetup.fnConfigureCompletions",
            ) as mockComp, patch(
                "vaibify.install.shellSetup.fnConfigureHelperCommands",
            ) as mockHelp, patch(
                "vaibify.install.shellSetup.fnLinkColimaSocket",
            ) as mockColima:
                shellSetup.fnRunFirstTimeSetup()
                mockComp.assert_called_once()
                mockHelp.assert_called_once()
                mockColima.assert_called_once()
                assert os.path.isdir(str(tmp_path / "vaibify"))
        finally:
            shellSetup._MARKER_DIR = sOldDir
            shellSetup._MARKER_PATH = sOldPath


# =======================================================================
# 2. commandTest.py — CLI integration paths (32% coverage)
# =======================================================================


class TestCommandTestHelpers:
    """Test internal helpers of commandTest."""

    def test_fdictBuildStepResult_all_fields(self):
        from vaibify.cli.commandTest import _fdictBuildStepResult
        dictResult = _fdictBuildStepResult(
            0, {"sName": "Build"}, "passed", 0, ""
        )
        assert dictResult["iNumber"] == 1
        assert dictResult["sName"] == "Build"
        assert dictResult["sStatus"] == "passed"
        assert dictResult["iExitCode"] == 0

    def test_fdictRunStepTests_no_commands(self):
        from vaibify.cli.commandTest import _fdictRunStepTests
        mockDocker = _fMockDocker()
        dictStep = {"sDirectory": "/workspace", "saTestCommands": []}
        dictResult = _fdictRunStepTests(mockDocker, "ctn1", dictStep, 0)
        assert dictResult["sStatus"] == "skipped"

    def test_fdictRunStepTests_with_commands_pass(self):
        from vaibify.cli.commandTest import _fdictRunStepTests
        mockDocker = _fMockDocker(iExitCode=0, sOutput="ok\n")
        dictStep = {
            "sDirectory": "/workspace",
            "saTestCommands": ["pytest"],
            "dictTests": {},
        }
        dictResult = _fdictRunStepTests(mockDocker, "ctn1", dictStep, 0)
        assert dictResult["sStatus"] == "passed"

    def test_fdictRunStepTests_with_commands_fail(self):
        from vaibify.cli.commandTest import _fdictRunStepTests
        mockDocker = _fMockDocker(iExitCode=1, sOutput="FAILED\n")
        dictStep = {
            "sDirectory": "/workspace",
            "saTestCommands": ["pytest"],
            "dictTests": {},
        }
        dictResult = _fdictRunStepTests(mockDocker, "ctn1", dictStep, 0)
        assert dictResult["sStatus"] == "failed"

    def test_fiRunTestCommandList_stops_on_failure(self):
        from vaibify.cli.commandTest import _fiRunTestCommandList
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.side_effect = [
            (0, "ok\n"),
            (1, "fail\n"),
            (0, "never reached\n"),
        ]
        iResult = _fiRunTestCommandList(
            mockDocker, "ctn1", ["cmd1", "cmd2", "cmd3"], "/workspace"
        )
        assert iResult == 1
        assert mockDocker.ftResultExecuteCommand.call_count == 2

    def test_fnPrintTestResults_table(self, capsys):
        from vaibify.cli.commandTest import _fnPrintTestResults
        listResults = [
            {"iNumber": 1, "sName": "Build", "sStatus": "passed",
             "iExitCode": 0},
            {"iNumber": 2, "sName": "Plot", "sStatus": "failed",
             "iExitCode": 1},
        ]
        _fnPrintTestResults(listResults)
        sOutput = capsys.readouterr().out
        assert "Build" in sOutput
        assert "passed" in sOutput
        assert "failed" in sOutput

    def test_flistRunAllTests_runs_specified_indices(self):
        from vaibify.cli.commandTest import _flistRunAllTests
        mockDocker = _fMockDocker(iExitCode=0, sOutput="ok\n")
        listSteps = [
            {"sName": "Step1", "sDirectory": "/workspace",
             "saTestCommands": ["pytest"], "dictTests": {}},
            {"sName": "Step2", "sDirectory": "/workspace",
             "saTestCommands": [], "dictTests": {}},
        ]
        listResults = _flistRunAllTests(mockDocker, "ctn1", listSteps, [0, 1])
        assert len(listResults) == 2
        assert listResults[0]["sStatus"] == "passed"
        assert listResults[1]["sStatus"] == "skipped"


# =======================================================================
# 3. commandWorkflow.py — CLI integration (41% coverage)
# =======================================================================


class TestCommandWorkflowCli:
    """Test the workflow CLI subcommand through Click runner."""

    def test_fnPrintStepTable(self, capsys):
        from vaibify.cli.commandWorkflow import _fnPrintStepTable
        dictWorkflow = {
            "sWorkflowName": "TestFlow",
            "listSteps": [
                {"sName": "Init", "dictVerification": {"sUser": "passed"},
                 "dictRunStats": {"sLastRun": "2025-01-01"}},
            ],
        }
        _fnPrintStepTable(dictWorkflow)
        sOutput = capsys.readouterr().out
        assert "TestFlow" in sOutput
        assert "Init" in sOutput

    def test_fnPrintStepDetail(self, capsys):
        from vaibify.cli.commandWorkflow import _fnPrintStepDetail
        dictStep = {
            "sName": "Analyze",
            "sDirectory": "/workspace/step",
            "bEnabled": True,
            "bPlotOnly": False,
            "bInteractive": True,
            "dictVerification": {"sUser": "untested"},
            "dictRunStats": {"sLastRun": "2025-06-01"},
            "saDataCommands": [],
            "saPlotCommands": [],
            "saTestCommands": [],
        }
        _fnPrintStepDetail(0, dictStep)
        sOutput = capsys.readouterr().out
        assert "Analyze" in sOutput
        assert "Interactive: True" in sOutput

    @patch("vaibify.cli.commandWorkflow.fconfigResolveProject")
    @patch("vaibify.cli.commandWorkflow.fconnectionRequireDocker")
    @patch("vaibify.cli.commandWorkflow.fsRequireRunningContainer")
    @patch("vaibify.cli.commandWorkflow.fdictRequireWorkflow")
    def test_workflow_table_output(
        self, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "sWorkflowName": "TestFlow",
                "listSteps": [
                    {"sName": "Build", "dictVerification": {},
                     "dictRunStats": {}},
                ],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandWorkflow import workflow
        runner = CliRunner()
        result = runner.invoke(workflow, [])
        assert result.exit_code == 0
        assert "TestFlow" in result.output

    @patch("vaibify.cli.commandWorkflow.fconfigResolveProject")
    @patch("vaibify.cli.commandWorkflow.fconnectionRequireDocker")
    @patch("vaibify.cli.commandWorkflow.fsRequireRunningContainer")
    @patch("vaibify.cli.commandWorkflow.fdictRequireWorkflow")
    def test_workflow_json_output(
        self, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "sWorkflowName": "TestFlow",
                "listSteps": [
                    {"sName": "Build", "sDirectory": "/workspace",
                     "bEnabled": True, "bPlotOnly": False,
                     "bInteractive": False,
                     "dictVerification": {}, "dictRunStats": {},
                     "saDataCommands": [], "saPlotCommands": [],
                     "saTestCommands": []},
                ],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandWorkflow import workflow
        runner = CliRunner()
        result = runner.invoke(workflow, ["--json"])
        assert result.exit_code == 0
        dictOut = json.loads(result.output)
        assert dictOut["iStepCount"] == 1

    @patch("vaibify.cli.commandWorkflow.fconfigResolveProject")
    @patch("vaibify.cli.commandWorkflow.fconnectionRequireDocker")
    @patch("vaibify.cli.commandWorkflow.fsRequireRunningContainer")
    @patch("vaibify.cli.commandWorkflow.fdictRequireWorkflow")
    def test_workflow_step_detail(
        self, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "sWorkflowName": "TestFlow",
                "listSteps": [
                    {"sName": "Build", "sDirectory": "/workspace",
                     "bEnabled": True, "bPlotOnly": False,
                     "bInteractive": False,
                     "dictVerification": {"sUser": "passed"},
                     "dictRunStats": {"sLastRun": "2025-01-01"},
                     "saDataCommands": [], "saPlotCommands": [],
                     "saTestCommands": []},
                ],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandWorkflow import workflow
        runner = CliRunner()
        result = runner.invoke(workflow, ["--step", "1"])
        assert result.exit_code == 0
        assert "Build" in result.output

    @patch("vaibify.cli.commandWorkflow.fconfigResolveProject")
    @patch("vaibify.cli.commandWorkflow.fconnectionRequireDocker")
    @patch("vaibify.cli.commandWorkflow.fsRequireRunningContainer")
    @patch("vaibify.cli.commandWorkflow.fdictRequireWorkflow")
    def test_workflow_step_out_of_range(
        self, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "sWorkflowName": "TestFlow",
                "listSteps": [{"sName": "Build"}],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandWorkflow import workflow
        runner = CliRunner()
        result = runner.invoke(workflow, ["--step", "5"])
        assert result.exit_code != 0

    @patch("vaibify.cli.commandWorkflow.fconfigResolveProject")
    @patch("vaibify.cli.commandWorkflow.fconnectionRequireDocker")
    @patch("vaibify.cli.commandWorkflow.fsRequireRunningContainer")
    @patch("vaibify.cli.commandWorkflow.fdictRequireWorkflow")
    def test_workflow_step_detail_json(
        self, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "sWorkflowName": "TestFlow",
                "listSteps": [
                    {"sName": "Build", "sDirectory": "/workspace",
                     "bEnabled": True, "bPlotOnly": True,
                     "bInteractive": False,
                     "dictVerification": {}, "dictRunStats": {},
                     "saDataCommands": [], "saPlotCommands": [],
                     "saTestCommands": []},
                ],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandWorkflow import workflow
        runner = CliRunner()
        result = runner.invoke(workflow, ["--step", "1", "--json"])
        assert result.exit_code == 0
        dictOut = json.loads(result.output)
        assert dictOut["sName"] == "Build"


# =======================================================================
# 4. commandLs.py — (44% coverage)
# =======================================================================


class TestCommandLsCli:
    """Test ls CLI subcommand."""

    def test_flistParseDirectoryListing(self):
        from vaibify.cli.commandLs import _flistParseDirectoryListing
        sOutput = "file1.txt\nfile2.py\n\n  dir1  \n"
        listFiles = _flistParseDirectoryListing(sOutput)
        assert listFiles == ["file1.txt", "file2.py", "dir1"]

    def test_flistParseDirectoryListing_empty(self):
        from vaibify.cli.commandLs import _flistParseDirectoryListing
        assert _flistParseDirectoryListing("") == []

    @patch("vaibify.cli.commandLs.fconfigResolveProject")
    @patch("vaibify.cli.commandLs.fconnectionRequireDocker")
    @patch("vaibify.cli.commandLs.fsRequireRunningContainer")
    def test_ls_success(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(sOutput="file1\nfile2\n")
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandLs import ls
        runner = CliRunner()
        result = runner.invoke(ls, [])
        assert result.exit_code == 0
        assert "file1" in result.output

    @patch("vaibify.cli.commandLs.fconfigResolveProject")
    @patch("vaibify.cli.commandLs.fconnectionRequireDocker")
    @patch("vaibify.cli.commandLs.fsRequireRunningContainer")
    def test_ls_json_output(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(sOutput="file1\nfile2\n")
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandLs import ls
        runner = CliRunner()
        result = runner.invoke(ls, ["--json"])
        assert result.exit_code == 0
        dictOut = json.loads(result.output)
        assert "listFiles" in dictOut

    @patch("vaibify.cli.commandLs.fconfigResolveProject")
    @patch("vaibify.cli.commandLs.fconnectionRequireDocker")
    @patch("vaibify.cli.commandLs.fsRequireRunningContainer")
    def test_ls_error(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(
            iExitCode=2, sOutput="No such directory"
        )
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandLs import ls
        runner = CliRunner()
        result = runner.invoke(ls, ["/nonexistent"])
        assert result.exit_code != 0

    @patch("vaibify.cli.commandLs.fconfigResolveProject")
    @patch("vaibify.cli.commandLs.fconnectionRequireDocker")
    @patch("vaibify.cli.commandLs.fsRequireRunningContainer")
    def test_ls_relative_path(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(sOutput="data.csv\n")
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandLs import ls
        runner = CliRunner()
        result = runner.invoke(ls, ["src/data"])
        assert result.exit_code == 0


# =======================================================================
# 5. commandCat.py — (57% coverage)
# =======================================================================


class TestCommandCatCli:
    """Test cat CLI subcommand."""

    @patch("vaibify.cli.commandCat.fconfigResolveProject")
    @patch("vaibify.cli.commandCat.fconnectionRequireDocker")
    @patch("vaibify.cli.commandCat.fsRequireRunningContainer")
    def test_cat_success(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(sOutput="line1\nline2\n")
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandCat import cat
        runner = CliRunner()
        result = runner.invoke(cat, ["README.md"])
        assert result.exit_code == 0
        assert "line1" in result.output

    @patch("vaibify.cli.commandCat.fconfigResolveProject")
    @patch("vaibify.cli.commandCat.fconnectionRequireDocker")
    @patch("vaibify.cli.commandCat.fsRequireRunningContainer")
    def test_cat_error(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(iExitCode=1, sOutput="No such file")
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandCat import cat
        runner = CliRunner()
        result = runner.invoke(cat, ["missing.txt"])
        assert result.exit_code != 0
        assert "Error" in result.output

    @patch("vaibify.cli.commandCat.fconfigResolveProject")
    @patch("vaibify.cli.commandCat.fconnectionRequireDocker")
    @patch("vaibify.cli.commandCat.fsRequireRunningContainer")
    def test_cat_absolute_path(self, mockContainer, mockDocker, mockConfig):
        mockConfig.return_value = _fMockConfig()
        mockDockerConn = _fMockDocker(sOutput="content\n")
        mockDocker.return_value = mockDockerConn
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandCat import cat
        runner = CliRunner()
        result = runner.invoke(cat, ["/etc/hosts"])
        assert result.exit_code == 0


# =======================================================================
# 6. commandRun.py — (51% coverage)
# =======================================================================


class TestCommandRunHelpers:
    """Test commandRun helper functions."""

    def test_fnCliStatusCallback_skipped(self, capsys):
        from vaibify.cli.commandRun import fnCliStatusCallback
        fnCliStatusCallback({"sType": "stepSkipped", "iStepNumber": 1})
        assert "skipped" in capsys.readouterr().out

    def test_fnCliStatusCallback_error(self, capsys):
        from vaibify.cli.commandRun import fnCliStatusCallback
        fnCliStatusCallback(
            {"sType": "error", "sMessage": "boom"}
        )
        assert "boom" in capsys.readouterr().out

    def test_fnCliStatusCallback_unknown_type(self, capsys):
        from vaibify.cli.commandRun import fnCliStatusCallback
        fnCliStatusCallback({"sType": "unknownEvent"})
        sOutput = capsys.readouterr().out
        assert sOutput == ""

    @patch("vaibify.cli.commandRun.fconfigResolveProject")
    @patch("vaibify.cli.commandRun.fconnectionRequireDocker")
    @patch("vaibify.cli.commandRun.fsRequireRunningContainer")
    def test_run_mutually_exclusive(
        self, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        from vaibify.cli.commandRun import run
        runner = CliRunner()
        result = runner.invoke(run, ["--step", "1", "--from", "2"])
        assert result.exit_code != 0


# =======================================================================
# 7. commandVerifyStep.py — (61% coverage)
# =======================================================================


class TestCommandVerifyStepCli:
    """Test verify-step CLI integration."""

    @patch("vaibify.cli.commandVerifyStep.fconfigResolveProject")
    @patch("vaibify.cli.commandVerifyStep.fconnectionRequireDocker")
    @patch("vaibify.cli.commandVerifyStep.fsRequireRunningContainer")
    @patch("vaibify.cli.commandVerifyStep.fdictRequireWorkflow")
    @patch("vaibify.cli.commandVerifyStep._fnSaveWorkflow")
    def test_verify_step_success(
        self, mockSave, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "listSteps": [
                    {"sName": "Build", "dictVerification": {}},
                ],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandVerifyStep import verify_step
        runner = CliRunner()
        result = runner.invoke(
            verify_step, ["--step", "1", "--status", "passed"]
        )
        assert result.exit_code == 0
        assert "passed" in result.output
        mockSave.assert_called_once()

    @patch("vaibify.cli.commandVerifyStep.fconfigResolveProject")
    @patch("vaibify.cli.commandVerifyStep.fconnectionRequireDocker")
    @patch("vaibify.cli.commandVerifyStep.fsRequireRunningContainer")
    @patch("vaibify.cli.commandVerifyStep.fdictRequireWorkflow")
    def test_verify_step_out_of_range(
        self, mockWorkflow, mockContainer, mockDocker, mockConfig
    ):
        mockConfig.return_value = _fMockConfig()
        mockDocker.return_value = _fMockDocker()
        mockContainer.return_value = "ctn1"
        mockWorkflow.return_value = {
            "dictWorkflow": {
                "listSteps": [{"sName": "Build"}],
            },
            "sWorkflowPath": "/w.yaml",
        }
        from vaibify.cli.commandVerifyStep import verify_step
        runner = CliRunner()
        result = runner.invoke(
            verify_step, ["--step", "5", "--status", "passed"]
        )
        assert result.exit_code != 0


# =======================================================================
# 8. pipelineServer.py — Pure helper functions (78% coverage)
# =======================================================================


class TestPipelineServerHelpers:
    """Test pure helper functions in pipelineServer."""

    def test_flistExtractKillPatterns(self):
        from vaibify.gui.pipelineServer import _flistExtractKillPatterns
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataCommands": [
                        "python build_data.py",
                        "python3 analyze.py",
                        "cp file1 file2",
                        "vplanet vpl.in",
                    ],
                    "saPlotCommands": [
                        "python plot_results.py",
                        "echo done",
                    ],
                },
                {
                    "saDataCommands": [""],
                    "saPlotCommands": [],
                },
            ],
        }
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        assert "build_data.py" in listPatterns
        assert "analyze.py" in listPatterns
        assert "plot_results.py" in listPatterns
        assert "vplanet" in listPatterns
        assert "cp" not in listPatterns
        assert "echo" not in listPatterns

    def test_flistBuildFigureCheckPaths_absolute(self):
        from vaibify.gui.pipelineServer import _flistBuildFigureCheckPaths
        listPaths = _flistBuildFigureCheckPaths(
            "/workspace/plot.pdf", "", "/workspace", "plot.pdf"
        )
        assert listPaths == ["/workspace/plot.pdf"]

    def test_flistBuildFigureCheckPaths_with_workdir(self):
        from vaibify.gui.pipelineServer import _flistBuildFigureCheckPaths
        listPaths = _flistBuildFigureCheckPaths(
            "/workspace/step01/plot.pdf",
            "/workspace/step01",
            "/workspace",
            "plot.pdf",
        )
        assert len(listPaths) == 2
        assert "/workspace/step01/plot.pdf" in listPaths

    def test_flistBuildFigureCheckPaths_relative_workdir(self):
        from vaibify.gui.pipelineServer import _flistBuildFigureCheckPaths
        listPaths = _flistBuildFigureCheckPaths(
            "/workspace/step01/plot.pdf",
            "output",
            "/workspace/step01",
            "plot.pdf",
        )
        assert len(listPaths) == 2

    def test_fnUpdateAggregateTestState_all_passed(self):
        from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
        dictStep = {
            "dictTests": {
                "dictIntegrity": {"saCommands": ["pytest -k int"]},
                "dictQualitative": {"saCommands": ["pytest -k qual"]},
            },
            "dictVerification": {
                "sIntegrity": "passed",
                "sQualitative": "passed",
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"

    def test_fnUpdateAggregateTestState_one_failed(self):
        from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
        dictStep = {
            "dictTests": {
                "dictIntegrity": {"saCommands": ["pytest -k int"]},
                "dictQualitative": {"saCommands": ["pytest -k qual"]},
            },
            "dictVerification": {
                "sIntegrity": "passed",
                "sQualitative": "failed",
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "failed"

    def test_fnUpdateAggregateTestState_no_commands(self):
        from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
        dictStep = {
            "dictTests": {
                "dictIntegrity": {"saCommands": []},
            },
            "dictVerification": {},
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"

    def test_fnUpdateAggregateTestState_mixed_untested(self):
        from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
        dictStep = {
            "dictTests": {
                "dictIntegrity": {"saCommands": ["pytest"]},
                "dictQualitative": {"saCommands": ["pytest"]},
            },
            "dictVerification": {
                "sIntegrity": "passed",
            },
        }
        _fnUpdateAggregateTestState(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"

    def test_fsPlotStandardPath(self):
        from vaibify.gui.pipelineServer import _fsPlotStandardPath
        assert _fsPlotStandardPath("figure1") == "figure1_standard.png"

    def test_fsBuildConvertCommand(self):
        from vaibify.gui.pipelineServer import _fsBuildConvertCommand
        sCmd = _fsBuildConvertCommand(
            "/workspace/plot.pdf", "/workspace", "plot.pdf"
        )
        assert "pdftoppm" in sCmd
        assert "plot_standard" in sCmd
        assert "gs" in sCmd

    def test_flistResolvePlotPaths_empty(self):
        from vaibify.gui.pipelineServer import _flistResolvePlotPaths
        dictStep = {"sDirectory": "/workspace", "saPlotFiles": []}
        assert _flistResolvePlotPaths(dictStep, {}) == []

    def test_flistResolvePlotPaths_absolute_file(self):
        from vaibify.gui.pipelineServer import _flistResolvePlotPaths
        dictStep = {
            "sDirectory": "/workspace/step01",
            "saPlotFiles": ["/absolute/plot.pdf"],
        }
        listResult = _flistResolvePlotPaths(dictStep, {})
        assert len(listResult) == 1
        assert listResult[0][0] == "/absolute/plot.pdf"
        assert listResult[0][1] == "plot.pdf"

    def test_flistResolvePlotPaths_relative_file(self):
        from vaibify.gui.pipelineServer import _flistResolvePlotPaths
        dictStep = {
            "sDirectory": "/workspace/step01",
            "saPlotFiles": ["output/plot.pdf"],
        }
        listResult = _flistResolvePlotPaths(dictStep, {})
        assert len(listResult) == 1
        assert listResult[0][0] == "/workspace/step01/output/plot.pdf"

    def test_flistStandardizedBasenames_all(self):
        from vaibify.gui.pipelineServer import _flistStandardizedBasenames
        listPlots = [
            ("/workspace/a.pdf", "a.pdf"),
            ("/workspace/b.pdf", "b.pdf"),
        ]
        listResult = _flistStandardizedBasenames(listPlots, "")
        assert len(listResult) == 2

    def test_flistStandardizedBasenames_filtered(self):
        from vaibify.gui.pipelineServer import _flistStandardizedBasenames
        listPlots = [
            ("/workspace/a.pdf", "a.pdf"),
            ("/workspace/b.pdf", "b.pdf"),
        ]
        listResult = _flistStandardizedBasenames(listPlots, "a.pdf")
        assert listResult == ["a.pdf"]

    def test_fsFindPlotPath_found(self):
        from vaibify.gui.pipelineServer import _fsFindPlotPath
        listPlots = [
            ("/workspace/step/plot.pdf", "plot.pdf"),
        ]
        assert _fsFindPlotPath(listPlots, "plot.pdf") == (
            "/workspace/step/plot.pdf"
        )

    def test_fsFindPlotPath_not_found(self):
        from vaibify.gui.pipelineServer import _fsFindPlotPath
        listPlots = [("/workspace/step/plot.pdf", "plot.pdf")]
        assert _fsFindPlotPath(listPlots, "missing.pdf") == ""

    def test_fsFindStandardForFile_found(self):
        from vaibify.gui.pipelineServer import _fsFindStandardForFile
        listPlots = [("/workspace/step/plot.pdf", "plot.pdf")]
        sResult = _fsFindStandardForFile(listPlots, "plot.pdf")
        assert "plot_standard.png" in sResult

    def test_fsFindStandardForFile_not_found(self):
        from vaibify.gui.pipelineServer import _fsFindStandardForFile
        listPlots = [("/workspace/step/plot.pdf", "plot.pdf")]
        assert _fsFindStandardForFile(listPlots, "missing.pdf") == ""

    def test_fnRecordTestResult_passed_clears(self):
        from vaibify.gui.pipelineServer import _fnRecordTestResult
        dictStep = {
            "dictVerification": {
                "listModifiedFiles": ["a.py"],
                "bUpstreamModified": True,
            },
        }
        dictWorkflow = {"listSteps": [dictStep]}
        with patch(
            "vaibify.gui.pipelineServer._fnClearDownstreamUpstreamFlags"
        ):
            _fnRecordTestResult(dictStep, True, dictWorkflow, 0)
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"
        assert "listModifiedFiles" not in dictStep["dictVerification"]
        assert "bUpstreamModified" not in dictStep["dictVerification"]

    def test_fnRecordTestResult_failed(self):
        from vaibify.gui.pipelineServer import _fnRecordTestResult
        dictStep = {"dictVerification": {}}
        dictWorkflow = {"listSteps": [dictStep]}
        _fnRecordTestResult(dictStep, False, dictWorkflow, 0)
        assert dictStep["dictVerification"]["sUnitTest"] == "failed"


class TestPipelineServerFileTracking:
    """Test file change tracking helpers in pipelineServer."""

    def test_fdictFindChangedFiles_detects_changes(self):
        from vaibify.gui.pipelineServer import _fdictFindChangedFiles
        dictPathsByStep = {0: ["/workspace/a.py", "/workspace/b.py"]}
        dictOldTimes = {"/workspace/a.py": "100", "/workspace/b.py": "200"}
        dictNewTimes = {"/workspace/a.py": "100", "/workspace/b.py": "300"}
        dictChanged = _fdictFindChangedFiles(
            dictPathsByStep, dictOldTimes, dictNewTimes
        )
        assert 0 in dictChanged
        assert "/workspace/b.py" in dictChanged[0]

    def test_fdictFindChangedFiles_no_changes(self):
        from vaibify.gui.pipelineServer import _fdictFindChangedFiles
        dictPathsByStep = {0: ["/workspace/a.py"]}
        dictOldTimes = {"/workspace/a.py": "100"}
        dictNewTimes = {"/workspace/a.py": "100"}
        dictChanged = _fdictFindChangedFiles(
            dictPathsByStep, dictOldTimes, dictNewTimes
        )
        assert dictChanged == {}

    def test_fnInvalidateStepFiles_marks_modified(self):
        from vaibify.gui.pipelineServer import _fnInvalidateStepFiles
        dictStep = {
            "dictVerification": {"sUnitTest": "passed"},
            "saPlotFiles": [],
            "saDataFiles": ["data.py"],
        }
        _fnInvalidateStepFiles(dictStep, ["/workspace/data.py"])
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"
        assert "/workspace/data.py" in (
            dictStep["dictVerification"]["listModifiedFiles"]
        )

    def test_fnInvalidateStepFiles_plot_stale(self):
        from vaibify.gui.pipelineServer import _fnInvalidateStepFiles
        dictStep = {
            "dictVerification": {
                "sUnitTest": "passed",
                "sPlotStandards": "passed",
            },
            "saPlotFiles": ["plot.pdf"],
        }
        _fnInvalidateStepFiles(dictStep, ["/workspace/plot.pdf"])
        assert dictStep["dictVerification"]["sPlotStandards"] == "stale"

    def test_fbAnyPlotFileChanged_true(self):
        from vaibify.gui.pipelineServer import _fbAnyPlotFileChanged
        assert _fbAnyPlotFileChanged(
            ["/workspace/output/plot.pdf"],
            ["plot.pdf"],
        ) is True

    def test_fbAnyPlotFileChanged_false(self):
        from vaibify.gui.pipelineServer import _fbAnyPlotFileChanged
        assert _fbAnyPlotFileChanged(
            ["/workspace/data.csv"],
            ["plot.pdf"],
        ) is False

    def test_fnInvalidateDownstreamStep(self):
        from vaibify.gui.pipelineServer import _fnInvalidateDownstreamStep
        dictStep = {
            "dictVerification": {"sUnitTest": "passed"},
        }
        _fnInvalidateDownstreamStep(dictStep)
        assert dictStep["dictVerification"]["sUnitTest"] == "untested"
        assert dictStep["dictVerification"]["bUpstreamModified"] is True

    def test_fnStoreCommitHash(self):
        from vaibify.gui.pipelineServer import _fnStoreCommitHash
        dictWorkflow = {
            "dictSyncStatus": {
                "plot.pdf": {"sService": "github"},
                "data.csv": {"sService": "github"},
            },
        }
        _fnStoreCommitHash(
            dictWorkflow, ["plot.pdf"], "abc123"
        )
        assert (
            dictWorkflow["dictSyncStatus"]["plot.pdf"]["sGithubCommit"]
            == "abc123"
        )
        assert "sGithubCommit" not in (
            dictWorkflow["dictSyncStatus"]["data.csv"]
        )

    def test_fdictFindStemMatch_found(self):
        from vaibify.gui.pipelineServer import _fdictFindStemMatch
        dictStemRegistry = {"Step1.output": 1}
        dictWorkflow = {
            "listSteps": [{"sName": "DataBuild"}],
        }
        dictMatch = _fdictFindStemMatch(
            "output", dictStemRegistry, dictWorkflow, 1
        )
        assert dictMatch is not None
        assert dictMatch["iSourceStep"] == 1
        assert dictMatch["sSourceStepName"] == "DataBuild"

    def test_fdictFindStemMatch_not_found(self):
        from vaibify.gui.pipelineServer import _fdictFindStemMatch
        dictStemRegistry = {"Step1.output": 1}
        dictWorkflow = {"listSteps": [{"sName": "DataBuild"}]}
        assert _fdictFindStemMatch(
            "missing", dictStemRegistry, dictWorkflow, 1
        ) is None

    def test_fdictFindStemMatch_skips_current_and_downstream(self):
        from vaibify.gui.pipelineServer import _fdictFindStemMatch
        dictStemRegistry = {"Step2.output": 2}
        dictWorkflow = {
            "listSteps": [
                {"sName": "Step1"},
                {"sName": "Step2"},
            ],
        }
        assert _fdictFindStemMatch(
            "output", dictStemRegistry, dictWorkflow, 1
        ) is None


class TestPipelineServerBuildGenerateResponse:
    """Test test generation response builders."""

    def test_fdictBuildGenerateResponse(self):
        from vaibify.gui.pipelineServer import _fdictBuildGenerateResponse
        dictResult = {
            "dictIntegrity": {"saCommands": ["cmd1"]},
            "dictQualitative": {},
        }
        dictResponse = _fdictBuildGenerateResponse(dictResult)
        assert dictResponse["bGenerated"] is True
        assert dictResponse["dictIntegrity"]["saCommands"] == ["cmd1"]
        assert dictResponse["dictQuantitative"] == {}

    def test_fnApplyGeneratedTests(self):
        from vaibify.gui.pipelineServer import _fnApplyGeneratedTests
        dictWorkflow = {
            "listSteps": [
                {"sName": "Build", "saTestCommands": []},
            ],
        }
        dictResult = {
            "dictIntegrity": {"saCommands": ["pytest -k int"]},
            "dictQualitative": {"saCommands": []},
        }
        mockSave = MagicMock()
        dictCtx = {"save": mockSave}
        _fnApplyGeneratedTests(
            dictCtx, "ctn1", dictWorkflow, 0, dictResult
        )
        mockSave.assert_called_once()
        dictStep = dictWorkflow["listSteps"][0]
        assert "dictTests" in dictStep


# =======================================================================
# 9. pipelineRunner.py — additional helper coverage
# =======================================================================


class TestPipelineRunnerHelpers:
    """Test uncovered helper functions in pipelineRunner."""

    def test_fbShouldRunStep_before_start(self):
        from vaibify.gui.pipelineRunner import _fbShouldRunStep
        dictStep = {"bEnabled": True}
        assert _fbShouldRunStep(dictStep, 1, 3) is False

    def test_fbShouldRunStep_at_start(self):
        from vaibify.gui.pipelineRunner import _fbShouldRunStep
        dictStep = {"bEnabled": True}
        assert _fbShouldRunStep(dictStep, 3, 3) is True

    def test_fbShouldRunStep_disabled(self):
        from vaibify.gui.pipelineRunner import _fbShouldRunStep
        dictStep = {"bEnabled": False}
        assert _fbShouldRunStep(dictStep, 3, 1) is False

    def test_fnSetInteractiveResponse_sets_and_triggers(self):
        from vaibify.gui.pipelineRunner import (
            fdictCreateInteractiveContext,
            fnSetInteractiveResponse,
        )
        dictContext = fdictCreateInteractiveContext()
        assert not dictContext["eventResume"].is_set()
        fnSetInteractiveResponse(dictContext, "resume")
        assert dictContext["sResponse"] == "resume"
        assert dictContext["eventResume"].is_set()

    def test_fnSaveWorkflowStats_handles_error(self):
        from vaibify.gui.pipelineRunner import _fnSaveWorkflowStats
        mockDocker = MagicMock()
        mockDocker.fnWriteFile.side_effect = RuntimeError("write error")
        _fnSaveWorkflowStats(
            mockDocker, "ctn1", {"listSteps": []}, "/w.yaml"
        )

    def test_fnSaveWorkflowStats_writes_json(self):
        from vaibify.gui.pipelineRunner import _fnSaveWorkflowStats
        mockDocker = MagicMock()
        dictWorkflow = {"listSteps": [{"sName": "Build"}]}
        _fnSaveWorkflowStats(
            mockDocker, "ctn1", dictWorkflow, "/w.yaml"
        )
        mockDocker.fnWriteFile.assert_called_once()
        baWritten = mockDocker.fnWriteFile.call_args[0][2]
        dictParsed = json.loads(baWritten.decode("utf-8"))
        assert dictParsed["listSteps"][0]["sName"] == "Build"


class TestPipelineServerDirectoryParsing:
    """Test directory listing parsing."""

    def test_flistParseDirectoryOutput_mixed_types(self):
        from vaibify.gui.pipelineServer import _flistParseDirectoryOutput
        sOutput = "f /workspace/data.csv\nd /workspace/output\nf /workspace/plot.pdf\n"
        listEntries = _flistParseDirectoryOutput(sOutput)
        assert len(listEntries) == 3
        assert listEntries[0]["sName"] == "data.csv"
        assert listEntries[0]["bIsDirectory"] is False
        assert listEntries[1]["sName"] == "output"
        assert listEntries[1]["bIsDirectory"] is True

    def test_flistParseDirectoryOutput_empty(self):
        from vaibify.gui.pipelineServer import _flistParseDirectoryOutput
        assert _flistParseDirectoryOutput("") == []

    def test_flistParseDirectoryOutput_short_lines(self):
        from vaibify.gui.pipelineServer import _flistParseDirectoryOutput
        assert _flistParseDirectoryOutput("f\n\n  \n") == []


class TestPipelineServerFigureFetch:
    """Test figure fetching logic."""

    def test_fbaFetchFallback_absolute_workdir(self):
        from vaibify.gui.pipelineServer import _fbaFetchFallback
        mockDocker = _fMockDocker()
        mockDocker.fbaFetchFile.return_value = b"png content"
        baResult = _fbaFetchFallback(
            mockDocker, "ctn1",
            "/workspace/step01", "/workspace/step01", "plot.png"
        )
        assert baResult == b"png content"

    def test_fbaFetchFallback_relative_workdir(self):
        from vaibify.gui.pipelineServer import _fbaFetchFallback
        mockDocker = _fMockDocker()
        mockDocker.fbaFetchFile.return_value = b"png content"
        baResult = _fbaFetchFallback(
            mockDocker, "ctn1",
            "/workspace/step01", "output", "plot.png"
        )
        assert baResult == b"png content"

    def test_fbaFetchFallback_not_found(self):
        from vaibify.gui.pipelineServer import _fbaFetchFallback
        from fastapi import HTTPException
        mockDocker = _fMockDocker()
        mockDocker.fbaFetchFile.side_effect = FileNotFoundError("missing")
        with pytest.raises(HTTPException) as excInfo:
            _fbaFetchFallback(
                mockDocker, "ctn1",
                "/workspace", "/workspace", "missing.png"
            )
        assert excInfo.value.status_code == 404


# =======================================================================
# 10. containerManager.py — uncovered branches
# =======================================================================


class TestContainerManagerBranches:
    """Test uncovered branches in containerManager."""

    def test_fsRunDetachedCommand_success(self):
        from vaibify.docker.containerManager import _fsRunDetachedCommand
        with patch("vaibify.docker.containerManager.subprocess.run") as mockRun:
            mockRun.return_value = SimpleNamespace(
                returncode=0, stdout="abc123\n", stderr=""
            )
            sResult = _fsRunDetachedCommand(["docker", "run", "-d", "img"])
            assert sResult == "abc123"

    def test_fsRunDetachedCommand_failure(self):
        from vaibify.docker.containerManager import _fsRunDetachedCommand
        with patch("vaibify.docker.containerManager.subprocess.run") as mockRun:
            mockRun.return_value = SimpleNamespace(
                returncode=1, stdout="", stderr="image not found"
            )
            with pytest.raises(RuntimeError, match="image not found"):
                _fsRunDetachedCommand(["docker", "run", "-d", "img"])

    def test_flistAssembleRunCommand_with_command(self):
        from vaibify.docker.containerManager import _flistAssembleRunCommand
        mockConfig = SimpleNamespace(sProjectName="test")
        saResult = _flistAssembleRunCommand(
            mockConfig, ["-d", "--rm"], ["bash", "-c", "echo hi"]
        )
        assert "test:latest" in saResult
        assert "bash" in saResult
        assert "echo hi" in saResult

    def test_flistAssembleRunCommand_without_command(self):
        from vaibify.docker.containerManager import _flistAssembleRunCommand
        mockConfig = SimpleNamespace(sProjectName="test")
        saResult = _flistAssembleRunCommand(
            mockConfig, ["-d"], None
        )
        assert saResult == ["docker", "run", "-d", "test:latest"]
