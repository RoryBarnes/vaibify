"""Extended tests for async functions in vaibify.gui.pipelineRunner."""

import asyncio

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from vaibify.gui.pipelineRunner import (
    _fnEmitBanner,
    _fbShouldRunStep,
    fsGenerateLogFilename,
    ffBuildLoggingCallback,
    _fnToggleSelectedSteps,
    _fnEmitStepResult,
    _fnEmitCompletion,
    _fnEmitCommandHeader,
    fnWriteLogToContainer,
    fsShellQuote,
    _fsExtractScriptPath,
    _fnValidateStepDirectory,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coroutine)


# -----------------------------------------------------------------------
# _fnEmitBanner
# -----------------------------------------------------------------------


def test_fnEmitBanner_format():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(_fnEmitBanner(fnCapture, 3, "Analysis"))
    listLines = [d["sLine"] for d in listCaptured]
    assert "Step 03 - Analysis" in listLines


def test_fnEmitBanner_separator_length():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(_fnEmitBanner(fnCapture, 1, "Test"))
    listLines = [d["sLine"] for d in listCaptured]
    sBanner = "Step 01 - Test"
    assert "=" * len(sBanner) in listLines


# -----------------------------------------------------------------------
# _fbShouldRunStep — edge cases
# -----------------------------------------------------------------------


def test_fbShouldRunStep_equal_to_start():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 5, 5) is True


def test_fbShouldRunStep_above_start():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 10, 3) is True


def test_fbShouldRunStep_interactive_overrides():
    dictStep = {"bEnabled": True, "bInteractive": True}
    assert _fbShouldRunStep(dictStep, 5, 1) is False


def test_fbShouldRunStep_disabled_interactive():
    dictStep = {"bEnabled": False, "bInteractive": True}
    assert _fbShouldRunStep(dictStep, 1, 1) is False


# -----------------------------------------------------------------------
# fsGenerateLogFilename
# -----------------------------------------------------------------------


def test_fsGenerateLogFilename_format():
    sFilename = fsGenerateLogFilename("Pipeline Test")
    assert sFilename.startswith("Pipeline_Test_")
    assert sFilename.endswith(".log")


def test_fsGenerateLogFilename_sanitizes_slashes():
    sFilename = fsGenerateLogFilename("path/to/workflow")
    assert "/" not in sFilename


# -----------------------------------------------------------------------
# ffBuildLoggingCallback
# -----------------------------------------------------------------------


def test_ffBuildLoggingCallback_logs_output():
    listLogLines = []

    async def fnOriginal(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    _fnRunAsync(fnCallback({"sType": "output", "sLine": "hello"}))
    assert "hello" in listLogLines


def test_ffBuildLoggingCallback_logs_failure():
    listLogLines = []

    async def fnOriginal(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    _fnRunAsync(fnCallback({
        "sType": "commandFailed",
        "sCommand": "make",
        "iExitCode": 2,
    }))
    assert len(listLogLines) == 1
    assert "FAILED" in listLogLines[0]


def test_ffBuildLoggingCallback_ignores_other_types():
    listLogLines = []

    async def fnOriginal(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    _fnRunAsync(fnCallback({"sType": "started"}))
    assert len(listLogLines) == 0


def test_ffBuildLoggingCallback_forwards_to_original():
    listReceived = []

    async def fnOriginal(dictEvent):
        listReceived.append(dictEvent)

    listLogLines = []
    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    _fnRunAsync(fnCallback({"sType": "output", "sLine": "x"}))
    assert len(listReceived) == 1


# -----------------------------------------------------------------------
# _fnToggleSelectedSteps
# -----------------------------------------------------------------------


def test_fnToggleSelectedSteps_enables_selected():
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "bEnabled": True},
            {"sName": "B", "bEnabled": True},
            {"sName": "C", "bEnabled": True},
        ]
    }
    _fnToggleSelectedSteps(dictWorkflow, [0, 2])
    assert dictWorkflow["listSteps"][0]["bEnabled"] is True
    assert dictWorkflow["listSteps"][1]["bEnabled"] is False
    assert dictWorkflow["listSteps"][2]["bEnabled"] is True


def test_fnToggleSelectedSteps_empty_disables_all():
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "bEnabled": True},
        ]
    }
    _fnToggleSelectedSteps(dictWorkflow, [])
    assert dictWorkflow["listSteps"][0]["bEnabled"] is False


# -----------------------------------------------------------------------
# _fnEmitStepResult
# -----------------------------------------------------------------------


def test_fnEmitStepResult_pass():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(_fnEmitStepResult(fnCapture, 1, 0))
    assert listCaptured[0]["sType"] == "stepPass"


def test_fnEmitStepResult_fail():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(_fnEmitStepResult(fnCapture, 2, 1))
    assert listCaptured[0]["sType"] == "stepFail"


# -----------------------------------------------------------------------
# _fnEmitCompletion
# -----------------------------------------------------------------------


def test_fnEmitCompletion_success():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(_fnEmitCompletion(fnCapture, 0))
    assert listCaptured[0]["sType"] == "completed"


def test_fnEmitCompletion_failure():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(_fnEmitCompletion(fnCapture, 1))
    assert listCaptured[0]["sType"] == "failed"


# -----------------------------------------------------------------------
# _fnEmitCommandHeader
# -----------------------------------------------------------------------


def test_fnEmitCommandHeader_same_command():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(
        _fnEmitCommandHeader(fnCapture, "make", "make")
    )
    assert len(listCaptured) == 1
    assert "$ make" in listCaptured[0]["sLine"]


def test_fnEmitCommandHeader_resolved_differs():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    _fnRunAsync(
        _fnEmitCommandHeader(fnCapture, "{sCmd}", "make")
    )
    assert len(listCaptured) == 2
    assert "=>" in listCaptured[1]["sLine"]


# -----------------------------------------------------------------------
# fnWriteLogToContainer
# -----------------------------------------------------------------------


def test_fnWriteLogToContainer_calls_write():
    mockConnection = MagicMock()
    listLines = ["line1", "line2"]
    _fnRunAsync(fnWriteLogToContainer(
        mockConnection, "cid", "/log.txt", listLines,
    ))
    mockConnection.fnWriteFile.assert_called_once()
    baData = mockConnection.fnWriteFile.call_args[0][2]
    assert b"line1" in baData
    assert b"line2" in baData


# -----------------------------------------------------------------------
# _fsExtractScriptPath
# -----------------------------------------------------------------------


def test_fsExtractScriptPath_python_command():
    sResult = _fsExtractScriptPath("python3 script.py")
    assert sResult == "script.py"


def test_fsExtractScriptPath_builtin_returns_none():
    sResult = _fsExtractScriptPath("echo hello")
    assert sResult is None


def test_fsExtractScriptPath_bare_command():
    sResult = _fsExtractScriptPath("vplanet input.in")
    assert sResult == "vplanet"


# -----------------------------------------------------------------------
# _fnValidateStepDirectory
# -----------------------------------------------------------------------


def test_fnValidateStepDirectory_missing_dir():
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.return_value = (1, "")
    listErrors = []
    _fnValidateStepDirectory(
        mockConnection, "cid", "/missing",
        1, "Step1", listErrors,
    )
    assert len(listErrors) == 1
    assert "does not exist" in listErrors[0]


def test_fnValidateStepDirectory_not_writable():
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.side_effect = [
        (0, ""),
        (1, ""),
    ]
    listErrors = []
    _fnValidateStepDirectory(
        mockConnection, "cid", "/readonly",
        1, "Step1", listErrors,
    )
    assert len(listErrors) == 1
    assert "not writable" in listErrors[0]


def test_fnValidateStepDirectory_ok():
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.side_effect = [
        (0, ""),
        (0, ""),
    ]
    listErrors = []
    _fnValidateStepDirectory(
        mockConnection, "cid", "/workspace",
        1, "Step1", listErrors,
    )
    assert len(listErrors) == 0
