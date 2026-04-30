"""Tests for CLI run/test/register/start uncovered paths."""

import asyncio
import pathlib

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def fn_restore_event_loop():
    """Ensure a default event loop is available after asyncio.run.

    ``asyncio.run`` in _fiRunPipeline closes the default loop on Python 3.9,
    leaving subsequent tests that call ``asyncio.Event()`` without a loop.
    """
    yield
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _fMockDocker():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    return mockDocker


def _fMockConfig():
    return SimpleNamespace(sProjectName="proj")


def _fdictWorkflow(iSteps=2):
    return {
        "listSteps": [
            {"sName": f"Step {i + 1}", "sDirectory": f"/ws/s{i}"}
            for i in range(iSteps)
        ],
    }


# ---------------------------------------------------------------
# commandRun._fiRunPipeline / _fiRunSingleStep
# ---------------------------------------------------------------


def test_fiRunPipeline_calls_fnRunAllSteps_when_neither_step_nor_from():
    from vaibify.cli import commandRun
    mockRunAll = AsyncMock(return_value=0)
    with patch(
        "vaibify.gui.pipelineRunner.fnRunAllSteps", mockRunAll,
    ):
        iResult = commandRun._fiRunPipeline(
            _fMockDocker(), "ctn", None, None,
        )
    assert iResult == 0
    mockRunAll.assert_awaited_once()


def test_fiRunPipeline_calls_fnRunFromStep_when_iFrom():
    from vaibify.cli import commandRun
    mockFromStep = AsyncMock(return_value=0)
    with patch(
        "vaibify.gui.pipelineRunner.fnRunFromStep", mockFromStep,
    ):
        iResult = commandRun._fiRunPipeline(
            _fMockDocker(), "ctn", None, 3,
        )
    assert iResult == 0
    mockFromStep.assert_awaited_once()
    assert mockFromStep.call_args[0][2] == 3


def test_fiRunPipeline_dispatches_single_step_when_iStep_set():
    from vaibify.cli import commandRun
    with patch.object(
        commandRun, "_fiRunSingleStep", return_value=0
    ) as mockSingle:
        iResult = commandRun._fiRunPipeline(
            _fMockDocker(), "ctn", 2, None,
        )
    assert iResult == 0
    mockSingle.assert_called_once()
    assert mockSingle.call_args[0][2] == 2


def test_fiRunSingleStep_no_workflow_found_returns_two(capsys):
    from vaibify.cli import commandRun
    with patch(
        "vaibify.gui.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[],
    ):
        iResult = commandRun._fiRunSingleStep(
            _fMockDocker(), "ctn", 1, "/workspace",
        )
    assert iResult == 2
    assert "No workflow" in capsys.readouterr().out


def test_fiRunSingleStep_step_out_of_range_returns_two(capsys):
    from vaibify.cli import commandRun
    with patch(
        "vaibify.gui.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[{"sPath": "/workspace/wf.json"}],
    ), patch(
        "vaibify.gui.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=_fdictWorkflow(2),
    ):
        iResult = commandRun._fiRunSingleStep(
            _fMockDocker(), "ctn", 99, "/workspace",
        )
    assert iResult == 2
    assert "out of range" in capsys.readouterr().out


def test_fiRunSingleStep_step_zero_out_of_range(capsys):
    from vaibify.cli import commandRun
    with patch(
        "vaibify.gui.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[{"sPath": "/workspace/wf.json"}],
    ), patch(
        "vaibify.gui.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=_fdictWorkflow(2),
    ):
        iResult = commandRun._fiRunSingleStep(
            _fMockDocker(), "ctn", 0, "/workspace",
        )
    assert iResult == 2


def test_fiRunSingleStep_valid_step_runs_selected():
    from vaibify.cli import commandRun
    mockSelected = AsyncMock(return_value=0)
    with patch(
        "vaibify.gui.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[{"sPath": "/workspace/wf.json"}],
    ), patch(
        "vaibify.gui.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=_fdictWorkflow(2),
    ), patch(
        "vaibify.gui.pipelineRunner.fnRunSelectedSteps", mockSelected,
    ):
        iResult = commandRun._fiRunSingleStep(
            _fMockDocker(), "ctn", 2, "/workspace",
        )
    assert iResult == 0
    # Index passed should be 0-based (iStep - 1).
    assert mockSelected.call_args[0][2] == [1]


# ---------------------------------------------------------------
# commandRun.run CLI entry: non-zero exit propagation
# ---------------------------------------------------------------


@patch("vaibify.cli.commandRun.fsRequireRunningContainer",
       return_value="ctn")
@patch("vaibify.cli.commandRun.fconnectionRequireDocker")
@patch("vaibify.cli.commandRun.fconfigResolveProject",
       return_value=_fMockConfig())
@patch("vaibify.cli.commandRun._fiRunPipeline", return_value=1)
def test_run_exits_one_on_pipeline_failure(
    mockPipeline, mockConfig, mockDocker, mockContainer,
):
    from vaibify.cli.commandRun import run
    runner = CliRunner()
    result = runner.invoke(run, [])
    assert result.exit_code == 1


@patch("vaibify.cli.commandRun.fsRequireRunningContainer",
       return_value="ctn")
@patch("vaibify.cli.commandRun.fconnectionRequireDocker")
@patch("vaibify.cli.commandRun.fconfigResolveProject",
       return_value=_fMockConfig())
@patch("vaibify.cli.commandRun._fiRunPipeline", return_value=2)
def test_run_exits_two_on_out_of_range(
    mockPipeline, mockConfig, mockDocker, mockContainer,
):
    from vaibify.cli.commandRun import run
    runner = CliRunner()
    result = runner.invoke(run, [])
    assert result.exit_code == 2


@patch("vaibify.cli.commandRun.fsRequireRunningContainer",
       return_value="ctn")
@patch("vaibify.cli.commandRun.fconnectionRequireDocker")
@patch("vaibify.cli.commandRun.fconfigResolveProject",
       return_value=_fMockConfig())
@patch("vaibify.cli.commandRun._fiRunPipeline", return_value=0)
def test_run_exits_zero_on_success(
    mockPipeline, mockConfig, mockDocker, mockContainer,
):
    from vaibify.cli.commandRun import run
    runner = CliRunner()
    result = runner.invoke(run, [])
    assert result.exit_code == 0


# ---------------------------------------------------------------
# commandStart: runtime error handling
# ---------------------------------------------------------------


def test_fnHandleDockerRuntimeError_sigkill_exits_zero(capsys):
    from vaibify.cli.commandStart import _fnHandleDockerRuntimeError
    with pytest.raises(SystemExit) as exc:
        _fnHandleDockerRuntimeError(
            RuntimeError("container exited: exit 137"), "proj",
        )
    assert exc.value.code == 0
    assert "stopped externally" in capsys.readouterr().out


def test_fnHandleDockerRuntimeError_sigint_exits_zero(capsys):
    from vaibify.cli.commandStart import _fnHandleDockerRuntimeError
    with pytest.raises(SystemExit) as exc:
        _fnHandleDockerRuntimeError(
            RuntimeError("container exited: exit 130"), "proj",
        )
    assert exc.value.code == 0
    assert "exited cleanly" in capsys.readouterr().out


def test_fnHandleDockerRuntimeError_sigterm_exits_zero(capsys):
    from vaibify.cli.commandStart import _fnHandleDockerRuntimeError
    with pytest.raises(SystemExit) as exc:
        _fnHandleDockerRuntimeError(
            RuntimeError("container exited: exit 143"), "proj",
        )
    assert exc.value.code == 0


def test_fnHandleDockerRuntimeError_unknown_exits_one(capsys):
    from vaibify.cli.commandStart import _fnHandleDockerRuntimeError
    with pytest.raises(SystemExit) as exc:
        _fnHandleDockerRuntimeError(
            RuntimeError("image not found"), "proj",
        )
    assert exc.value.code == 1
    sOutput = (capsys.readouterr().err
               + capsys.readouterr().out)
    assert "Error" in sOutput or "image not found" in sOutput


def test_fnStartContainer_raises_runtime_error_is_handled(capsys):
    from vaibify.cli.commandStart import _fnStartContainer
    with patch(
        "vaibify.docker.containerManager.fnStartContainer",
        side_effect=RuntimeError("exit 137"),
    ), pytest.raises(SystemExit) as exc:
        _fnStartContainer(_fConfigMinimal(), "/dk", None)
    assert exc.value.code == 0


def _fConfigMinimal():
    return SimpleNamespace(sProjectName="proj")


# ---------------------------------------------------------------
# commandTest: CLI entry covers lines 106-134
# ---------------------------------------------------------------


def test_test_cli_step_out_of_range_exits_two():
    from vaibify.cli.commandTest import test as testCmd
    runner = CliRunner()
    with patch(
        "vaibify.cli.commandTest.fconfigResolveProject",
        return_value=_fMockConfig(),
    ), patch(
        "vaibify.cli.commandTest.fconnectionRequireDocker",
        return_value=_fMockDocker(),
    ), patch(
        "vaibify.cli.commandTest.fsRequireRunningContainer",
        return_value="ctn",
    ), patch(
        "vaibify.cli.commandTest.fdictRequireWorkflow",
        return_value={"dictWorkflow": _fdictWorkflow(2)},
    ):
        result = runner.invoke(testCmd, ["--step", "99"])
    assert result.exit_code == 2
    assert "out of range" in result.output


def test_test_cli_all_steps_human_format():
    from vaibify.cli.commandTest import test as testCmd
    runner = CliRunner()
    with patch(
        "vaibify.cli.commandTest.fconfigResolveProject",
        return_value=_fMockConfig(),
    ), patch(
        "vaibify.cli.commandTest.fconnectionRequireDocker",
        return_value=_fMockDocker(),
    ), patch(
        "vaibify.cli.commandTest.fsRequireRunningContainer",
        return_value="ctn",
    ), patch(
        "vaibify.cli.commandTest.fdictRequireWorkflow",
        return_value={"dictWorkflow": _fdictWorkflow(2)},
    ), patch(
        "vaibify.cli.commandTest._flistRunAllTests",
        return_value=[
            {"iNumber": 1, "sName": "Step 1",
             "sStatus": "passed", "iExitCode": 0, "sMessage": ""},
            {"iNumber": 2, "sName": "Step 2",
             "sStatus": "passed", "iExitCode": 0, "sMessage": ""},
        ],
    ):
        result = runner.invoke(testCmd, [])
    assert result.exit_code == 0
    assert "Step 1" in result.output
    assert "passed" in result.output


def test_test_cli_failing_step_exits_one():
    from vaibify.cli.commandTest import test as testCmd
    runner = CliRunner()
    with patch(
        "vaibify.cli.commandTest.fconfigResolveProject",
        return_value=_fMockConfig(),
    ), patch(
        "vaibify.cli.commandTest.fconnectionRequireDocker",
        return_value=_fMockDocker(),
    ), patch(
        "vaibify.cli.commandTest.fsRequireRunningContainer",
        return_value="ctn",
    ), patch(
        "vaibify.cli.commandTest.fdictRequireWorkflow",
        return_value={"dictWorkflow": _fdictWorkflow(1)},
    ), patch(
        "vaibify.cli.commandTest._flistRunAllTests",
        return_value=[
            {"iNumber": 1, "sName": "Step 1",
             "sStatus": "failed", "iExitCode": 1, "sMessage": "bad"},
        ],
    ):
        result = runner.invoke(testCmd, [])
    assert result.exit_code == 1


def test_test_cli_json_output():
    from vaibify.cli.commandTest import test as testCmd
    import json
    runner = CliRunner()
    with patch(
        "vaibify.cli.commandTest.fconfigResolveProject",
        return_value=_fMockConfig(),
    ), patch(
        "vaibify.cli.commandTest.fconnectionRequireDocker",
        return_value=_fMockDocker(),
    ), patch(
        "vaibify.cli.commandTest.fsRequireRunningContainer",
        return_value="ctn",
    ), patch(
        "vaibify.cli.commandTest.fdictRequireWorkflow",
        return_value={"dictWorkflow": _fdictWorkflow(1)},
    ), patch(
        "vaibify.cli.commandTest._flistRunAllTests",
        return_value=[
            {"iNumber": 1, "sName": "Step 1",
             "sStatus": "passed", "iExitCode": 0, "sMessage": ""},
        ],
    ):
        result = runner.invoke(testCmd, ["--json"])
    assert result.exit_code == 0
    dictParsed = json.loads(result.output)
    assert dictParsed["bAllPassed"] is True


def test_register_missing_yml_exits_one(tmp_path, capsys):
    from vaibify.cli import commandRegister
    with pytest.raises(SystemExit) as exc:
        commandRegister.register.callback(sdirectory=str(tmp_path))
    assert exc.value.code == 1
    assert "No vaibify.yml found" in capsys.readouterr().out


def test_register_success(tmp_path, capsys):
    from vaibify.cli import commandRegister
    (tmp_path / "vaibify.yml").write_text("project: {name: demo}\n")
    with patch(
        "vaibify.cli.commandRegister.fnAddProject"
    ) as mockAdd:
        commandRegister.register.callback(sdirectory=str(tmp_path))
    mockAdd.assert_called_once()
    assert "Registered project at" in capsys.readouterr().out


def test_register_already_registered_returns_cleanly(tmp_path, capsys):
    from vaibify.cli import commandRegister
    (tmp_path / "vaibify.yml").write_text("project: {name: demo}\n")
    with patch(
        "vaibify.cli.commandRegister.fnAddProject",
        side_effect=ValueError("already registered"),
    ):
        commandRegister.register.callback(sdirectory=str(tmp_path))
    assert "Already registered" in capsys.readouterr().out


