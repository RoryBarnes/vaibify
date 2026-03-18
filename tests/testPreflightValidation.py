"""Tests for pre-flight validation and shell quoting in pipelineRunner."""

import pytest

from vaibify.gui.pipelineRunner import (
    _flistPreflightValidate,
    _fsExtractScriptPath,
    fsShellQuote,
)


class MockDockerConnection:
    """Mock that simulates docker exec results for validation tests."""

    def __init__(self, dictResponses=None):
        self._dictResponses = dictResponses or {}
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        for sPattern, iExitCode in self._dictResponses.items():
            if sPattern in sCommand:
                return (iExitCode, "")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sFilePath):
        return b"{}"


def _fdictBuildTestWorkflow(listSteps):
    """Build a minimal workflow dict for testing."""
    return {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": -1,
        "listSteps": listSteps,
    }


def _fdictBuildTestStep(sName, sDirectory, listCommands=None):
    """Build a minimal step dict."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "bEnabled": True,
        "bPlotOnly": True,
        "saDataCommands": [],
        "saPlotCommands": listCommands or [],
        "saPlotFiles": [],
    }


# --- _fsExtractScriptPath tests ---


def test_fsExtractScriptPath_python_command():
    assert _fsExtractScriptPath("python makePlot.py arg1") == "makePlot.py"


def test_fsExtractScriptPath_bare_executable():
    assert _fsExtractScriptPath("vplanet vpl.in") == "vplanet"


def test_fsExtractScriptPath_builtin_returns_none():
    assert _fsExtractScriptPath("cd /workspace") is None
    assert _fsExtractScriptPath("cp file1 file2") is None
    assert _fsExtractScriptPath("echo hello") is None


def test_fsExtractScriptPath_empty_returns_none():
    assert _fsExtractScriptPath("") is None


def test_fsExtractScriptPath_python3_command():
    assert _fsExtractScriptPath("python3 script.py") == "script.py"


# --- Pre-flight validation tests ---


@pytest.mark.asyncio
async def test_preflight_missing_directory():
    dictWorkflow = _fdictBuildTestWorkflow([
        _fdictBuildTestStep("Step1", "/nonexistent/dir",
                            ["python test.py"]),
    ])
    mockConnection = MockDockerConnection({
        "test -d '/nonexistent/dir'": 1,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables
    )

    assert len(listErrors) >= 1
    assert "does not exist" in listErrors[0]


@pytest.mark.asyncio
async def test_preflight_not_writable_directory():
    dictWorkflow = _fdictBuildTestWorkflow([
        _fdictBuildTestStep("Step1", "/readonly/dir",
                            ["python test.py"]),
    ])
    mockConnection = MockDockerConnection({
        "test -d '/readonly/dir'": 0,
        "test -w '/readonly/dir'": 1,
        "test -f 'test.py'": 0,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables
    )

    assert len(listErrors) >= 1
    assert "not writable" in listErrors[0]


@pytest.mark.asyncio
async def test_preflight_missing_script():
    dictWorkflow = _fdictBuildTestWorkflow([
        _fdictBuildTestStep("Step1", "/workspace/project",
                            ["python missing_script.py"]),
    ])
    mockConnection = MockDockerConnection({
        "test -d '/workspace/project'": 0,
        "test -w '/workspace/project'": 0,
        "test -f 'missing_script.py'": 1,
        "which 'missing_script.py'": 1,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables
    )

    assert len(listErrors) >= 1
    assert "command not found" in listErrors[0]
    assert "missing_script.py" in listErrors[0]


@pytest.mark.asyncio
async def test_preflight_valid_step_passes():
    dictWorkflow = _fdictBuildTestWorkflow([
        _fdictBuildTestStep("Step1", "/workspace/project",
                            ["python plot.py"]),
    ])
    mockConnection = MockDockerConnection({
        "test -d": 0,
        "test -w": 0,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables
    )

    assert len(listErrors) == 0


@pytest.mark.asyncio
async def test_preflight_skips_disabled_steps():
    dictStep = _fdictBuildTestStep("Disabled", "/nonexistent",
                                   ["python nope.py"])
    dictStep["bEnabled"] = False
    dictWorkflow = _fdictBuildTestWorkflow([dictStep])
    mockConnection = MockDockerConnection({
        "test -d /nonexistent": 1,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables
    )

    assert len(listErrors) == 0


@pytest.mark.asyncio
async def test_preflight_multiple_errors_collected():
    dictWorkflow = _fdictBuildTestWorkflow([
        _fdictBuildTestStep("Step1", "/missing1", ["python a.py"]),
        _fdictBuildTestStep("Step2", "/missing2", ["python b.py"]),
    ])
    mockConnection = MockDockerConnection({
        "test -d": 1,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables
    )

    assert len(listErrors) == 2
    assert "Step 1" in listErrors[0]
    assert "Step 2" in listErrors[1]


@pytest.mark.asyncio
async def test_preflight_respects_start_step():
    dictWorkflow = _fdictBuildTestWorkflow([
        _fdictBuildTestStep("Step1", "/missing", ["python a.py"]),
        _fdictBuildTestStep("Step2", "/also_missing", ["python b.py"]),
    ])
    mockConnection = MockDockerConnection({
        "test -d": 1,
    })
    dictVariables = {"sPlotDirectory": "Plot", "sFigureType": "pdf"}

    listErrors = await _flistPreflightValidate(
        mockConnection, "container123", dictWorkflow, dictVariables,
        iStartStep=2,
    )

    assert len(listErrors) == 1
    assert "Step 2" in listErrors[0]


# --- fsShellQuote tests ---


def test_fsShellQuote_plain_path():
    assert fsShellQuote("/workspace/project") == "'/workspace/project'"


def test_fsShellQuote_path_with_spaces():
    assert fsShellQuote("/path/with spaces") == "'/path/with spaces'"


def test_fsShellQuote_injection_semicolon():
    sInput = "/dir; rm -rf /"
    sQuoted = fsShellQuote(sInput)
    assert sQuoted == "'/dir; rm -rf /'"
    assert ";" not in sQuoted.strip("'")  or "'" in sQuoted


def test_fsShellQuote_injection_backtick():
    sInput = "/dir$(whoami)"
    sQuoted = fsShellQuote(sInput)
    assert sQuoted == "'/dir$(whoami)'"


def test_fsShellQuote_embedded_single_quote():
    sInput = "/dir/it's"
    sQuoted = fsShellQuote(sInput)
    assert sQuoted == "'/dir/it'\\''s'"


def test_fsShellQuote_empty_string():
    assert fsShellQuote("") == "''"


def test_fsShellQuote_prevents_injection_in_commands():
    """Verify quoting is applied in actual command construction."""
    mockConnection = MockDockerConnection({})
    from vaibify.gui.pipelineRunner import _fnValidateStepDirectory
    listErrors = []
    _fnValidateStepDirectory(
        mockConnection, "c1", "/workspace; rm -rf /",
        1, "Malicious", listErrors,
    )
    for sCommand in mockConnection.listCommands:
        assert "'/workspace; rm -rf /'" in sCommand
