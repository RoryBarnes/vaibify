"""Tests targeting uncovered lines in vaibify.gui.syncDispatcher.

Covers:
- Line 232: _flistBuildStepCopyCommandList skips steps with empty sCamelDir
- Lines 255-266: ftResultPushScriptsToGithub with actual commands
- Line 386: fdictCheckConnectivity unreachable "Unknown service" fallback
- Lines 645-667: ftResultExportDag (new function)
"""

import pytest
from unittest.mock import MagicMock, patch

from vaibify.gui.syncDispatcher import (
    DICT_DAG_MEDIA_TYPES,
    _flistBuildDagEdges,
    _flistBuildStepCopyCommandList,
    fdictCheckConnectivity,
    fsBuildDagDot,
    ftResultExportDag,
    ftResultPushScriptsToGithub,
)


# ── Helpers ──────────────────────────────────────────────────────


def _fmockDocker(iExitCode=0, sOutput="", baContent=b"<svg/>"):
    """Return a mock Docker connection with configurable results."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput)
    mockDocker.fbaFetchFile.return_value = baContent
    mockDocker.fnWriteFile.return_value = None
    return mockDocker


DICT_STEP_WITH_SCRIPTS = {
    "sName": "Run Simulation",
    "sDirectory": "/workspace/step01",
    "saDataCommands": ["python run.py"],
    "saPlotCommands": [],
    "saTestCommands": [],
    "saDependencies": [],
    "saDataFiles": [],
    "saPlotFiles": [],
}

DICT_STEP_EMPTY = {
    "sName": "Empty Step",
    "sDirectory": "/workspace/step02",
    "saDataCommands": [],
    "saPlotCommands": [],
    "saTestCommands": [],
    "saDependencies": [],
    "saDataFiles": [],
    "saPlotFiles": [],
}


# ── _flistBuildStepCopyCommandList: line 232 (empty sCamelDir) ───


class TestBuildStepCopyCommandListEmptyCamelDir:
    """When fdictBuildStepDirectoryMap returns '' for a step, skip it."""

    @patch("vaibify.gui.syncDispatcher.workflowManager")
    def test_empty_camel_dir_skipped(self, mockWorkflowMgr):
        mockWorkflowMgr.fdictBuildStepDirectoryMap.return_value = {
            0: "",
            1: "runSimulation",
        }
        mockWorkflowMgr.flistExtractStepScripts.return_value = [
            "run.py"]
        mockWorkflowMgr.fsGetPlotCategory.return_value = "display"
        dictWorkflow = {
            "listSteps": [DICT_STEP_WITH_SCRIPTS, DICT_STEP_WITH_SCRIPTS],
        }
        listCommands = _flistBuildStepCopyCommandList(dictWorkflow)
        assert len(listCommands) == 1
        assert "runSimulation" in listCommands[0]

    @patch("vaibify.gui.syncDispatcher.workflowManager")
    def test_all_empty_camel_dirs_returns_empty(self, mockWorkflowMgr):
        mockWorkflowMgr.fdictBuildStepDirectoryMap.return_value = {
            0: "",
        }
        dictWorkflow = {"listSteps": [DICT_STEP_WITH_SCRIPTS]}
        listCommands = _flistBuildStepCopyCommandList(dictWorkflow)
        assert listCommands == []


# ── ftResultPushScriptsToGithub: lines 255-266 ──────────────────


class TestPushScriptsToGithubWithCommands:
    """When scripts exist, builds git command and executes it."""

    @patch("vaibify.gui.syncDispatcher.workflowManager")
    def test_successful_push(self, mockWorkflowMgr):
        mockWorkflowMgr.fdictBuildStepDirectoryMap.return_value = {
            0: "runSimulation",
        }
        mockWorkflowMgr.flistExtractStepScripts.return_value = [
            "run.py"]
        mockWorkflowMgr.fsGetPlotCategory.return_value = "display"
        mockDocker = _fmockDocker(iExitCode=0, sOutput="abc1234")
        dictWorkflow = {
            "sWorkflowName": "Test",
            "listSteps": [DICT_STEP_WITH_SCRIPTS],
        }
        iExit, sOut = ftResultPushScriptsToGithub(
            mockDocker, "cid123", dictWorkflow,
            "commit message", "/workspace/repo",
        )
        assert iExit == 0
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "git add -A" in sCommand
        assert "git push" in sCommand
        assert ".gitignore" in sCommand
        assert "README.md" in sCommand
        assert "commit message" in sCommand

    @patch("vaibify.gui.syncDispatcher.workflowManager")
    def test_push_failure_propagated(self, mockWorkflowMgr):
        mockWorkflowMgr.fdictBuildStepDirectoryMap.return_value = {
            0: "runSimulation",
        }
        mockWorkflowMgr.flistExtractStepScripts.return_value = [
            "run.py"]
        mockWorkflowMgr.fsGetPlotCategory.return_value = "display"
        mockDocker = _fmockDocker(iExitCode=1, sOutput="push rejected")
        dictWorkflow = {
            "sWorkflowName": "Fail",
            "listSteps": [DICT_STEP_WITH_SCRIPTS],
        }
        iExit, sOut = ftResultPushScriptsToGithub(
            mockDocker, "cid123", dictWorkflow,
            "commit msg", "/workspace/repo",
        )
        assert iExit == 1
        assert sOut == "push rejected"


# ── fdictCheckConnectivity: line 386 (unreachable fallback) ──────


class TestCheckConnectivityUnknownService:
    """Line 386 is unreachable because fnValidateServiceName guards it.

    We bypass the guard by patching fnValidateServiceName to be a no-op.
    """

    @patch(
        "vaibify.gui.syncDispatcher.fnValidateServiceName",
        side_effect=lambda s: None,
    )
    def test_unknown_service_returns_not_connected(self, _mockValidate):
        mockDocker = _fmockDocker()
        dictResult = fdictCheckConnectivity(
            mockDocker, "cid123", "dropbox")
        assert dictResult["bConnected"] is False
        assert "Unknown" in dictResult["sMessage"]


# ── ftResultExportDag: lines 645-667 ────────────────────────────


class TestExportDag:
    """Tests for the new ftResultExportDag function."""

    def test_unsupported_format_returns_error(self):
        mockDocker = _fmockDocker()
        dictWorkflow = {"listSteps": []}
        iExit, sOut = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "bmp")
        assert iExit == 1
        assert "Unsupported" in sOut
        mockDocker.ftResultExecuteCommand.assert_not_called()

    def test_svg_format_success(self):
        baSvgContent = b"<svg>test</svg>"
        mockDocker = _fmockDocker(
            iExitCode=0, baContent=baSvgContent)
        dictWorkflow = {"listSteps": [
            {"sName": "Step One"},
        ]}
        iExit, baResult = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "svg")
        assert iExit == 0
        assert baResult == baSvgContent
        mockDocker.fnWriteFile.assert_called_once()
        sWrittenDot = mockDocker.fnWriteFile.call_args[0][2]
        assert b"digraph" in sWrittenDot
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "dot -Tsvg" in sCommand

    def test_png_format_success(self):
        baPngContent = b"\x89PNG"
        mockDocker = _fmockDocker(
            iExitCode=0, baContent=baPngContent)
        dictWorkflow = {"listSteps": []}
        iExit, baResult = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "png")
        assert iExit == 0
        assert baResult == baPngContent
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "dot -Tpng" in sCommand

    def test_pdf_format_success(self):
        baPdfContent = b"%PDF-1.4"
        mockDocker = _fmockDocker(
            iExitCode=0, baContent=baPdfContent)
        dictWorkflow = {"listSteps": []}
        iExit, baResult = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "pdf")
        assert iExit == 0
        assert baResult == baPdfContent

    def test_dot_conversion_failure(self):
        mockDocker = _fmockDocker(
            iExitCode=1, sOutput="dot: command not found")
        dictWorkflow = {"listSteps": []}
        iExit, sOut = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "svg")
        assert iExit == 1
        assert "dot: command not found" in sOut
        mockDocker.fbaFetchFile.assert_not_called()

    def test_format_normalized_with_leading_dot(self):
        mockDocker = _fmockDocker(iExitCode=0, baContent=b"<svg/>")
        dictWorkflow = {"listSteps": []}
        iExit, _ = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, ".SVG")
        assert iExit == 0
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "dot -Tsvg" in sCommand

    def test_persist_path_includes_format(self):
        mockDocker = _fmockDocker(iExitCode=0, baContent=b"data")
        dictWorkflow = {"listSteps": []}
        ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "png")
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "/workspace/.vaibify/dag.png" in sCommand


class TestDagMediaTypes:
    """Verify the DICT_DAG_MEDIA_TYPES constant."""

    def test_svg_media_type(self):
        assert DICT_DAG_MEDIA_TYPES["svg"] == "image/svg+xml"

    def test_png_media_type(self):
        assert DICT_DAG_MEDIA_TYPES["png"] == "image/png"

    def test_pdf_media_type(self):
        assert DICT_DAG_MEDIA_TYPES["pdf"] == "application/pdf"


# ── _flistBuildDagEdges: implicit deps from shared directories ────


class TestBuildDagEdgesIncludesImplicitDeps:
    """DAG edges should include implicit directory-overlap dependencies."""

    def test_flistBuildDagEdges_includes_implicit_deps(self):
        dictWorkflow = {
            "listSteps": [
                {
                    "sName": "Produce",
                    "sDirectory": "/workspace/shared/sub",
                    "saDataFiles": ["output.csv"],
                    "saPlotFiles": [],
                    "saDataCommands": [],
                    "saPlotCommands": [],
                },
                {
                    "sName": "Consume",
                    "sDirectory": "/workspace/shared",
                    "saDataFiles": [],
                    "saPlotFiles": [],
                    "saDataCommands": [],
                    "saPlotCommands": [],
                },
            ],
        }
        listEdges = _flistBuildDagEdges(dictWorkflow)
        assert any("step1 -> step2" in sEdge for sEdge in listEdges)
