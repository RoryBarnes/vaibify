"""Tests for vaibify.cli.main — main group, stop, connect, verify, etc."""

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.main import main


# -----------------------------------------------------------------------
# main group
# -----------------------------------------------------------------------


def test_main_group_help_lists_all_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for sCommand in (
        "init", "build", "start", "status",
        "destroy", "config", "publish", "stop",
        "connect", "verify", "setup", "gui",
        "push", "pull",
    ):
        assert sCommand in result.output


def test_main_version_option():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0


# -----------------------------------------------------------------------
# stop
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
def test_stop_success(mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
    )
    with patch(
        "vaibify.docker.containerManager.fnStopContainer",
    ) as mockStop:
        runner = CliRunner()
        result = runner.invoke(main, ["stop"])
        assert result.exit_code == 0
        assert "Stopped" in result.output


@patch("vaibify.cli.main.fconfigResolveProject")
def test_stop_not_running_exits(mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
    )
    with patch(
        "vaibify.docker.containerManager.fnStopContainer",
        side_effect=RuntimeError("not running"),
    ):
        runner = CliRunner()
        result = runner.invoke(main, ["stop"])
        assert result.exit_code != 0
        assert "not active" in result.output.lower()


# -----------------------------------------------------------------------
# connect
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_connect_calls_docker_exec(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["connect"])
    assert mockRun.called
    listArgs = mockRun.call_args[0][0]
    assert "docker" in listArgs
    assert "exec" in listArgs
    assert "researcher" in listArgs


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_connect_with_project_option(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="myproj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["connect", "-p", "myproj"])
    mockConfig.assert_called_once_with("myproj")
    assert mockRun.called


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPushToContainer")
def test_push_with_project_option(mockPush, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="myproj",
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["push", "-p", "myproj", "/src", "/dst"],
    )
    assert result.exit_code == 0
    mockConfig.assert_called_once_with("myproj")
    mockPush.assert_called_once_with("myproj", "/src", "/dst")


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPullFromContainer")
def test_pull_with_project_option(mockPull, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="myproj",
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["pull", "-p", "myproj", "/src", "/dst"],
    )
    assert result.exit_code == 0
    mockConfig.assert_called_once_with("myproj")
    mockPull.assert_called_once_with("myproj", "/src", "/dst")


# -----------------------------------------------------------------------
# verify
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_verify_calls_check_isolation(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["verify"])
    assert mockRun.called
    listArgs = mockRun.call_args[0][0]
    assert "checkIsolation" in listArgs[-1]


# -----------------------------------------------------------------------
# setup help
# -----------------------------------------------------------------------


def test_setup_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--help"])
    assert result.exit_code == 0
    assert "setup" in result.output.lower()


# -----------------------------------------------------------------------
# gui help
# -----------------------------------------------------------------------


def test_gui_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["gui", "--help"])
    assert result.exit_code == 0
    assert "pipeline" in result.output.lower()


def test_gui_help_no_user_option():
    runner = CliRunner()
    result = runner.invoke(main, ["gui", "--help"])
    assert "--user" not in result.output


# -----------------------------------------------------------------------
# push
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPushToContainer")
def test_push_calls_transfer(mockPush, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["push", "/src", "/dst"])
    assert result.exit_code == 0
    assert "Pushed" in result.output
    mockPush.assert_called_once_with("proj", "/src", "/dst")


# -----------------------------------------------------------------------
# pull
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPullFromContainer")
def test_pull_calls_transfer(mockPull, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["pull", "/src", "/dst"])
    assert result.exit_code == 0
    assert "Pulled" in result.output
    mockPull.assert_called_once_with("proj", "/src", "/dst")


# -----------------------------------------------------------------------
# push / pull help
# -----------------------------------------------------------------------


def test_push_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["push", "--help"])
    assert result.exit_code == 0
    assert "Push" in result.output
    assert "--project" in result.output


def test_pull_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["pull", "--help"])
    assert result.exit_code == 0
    assert "Pull" in result.output
    assert "--project" in result.output


def test_connect_help_shows_project_option():
    runner = CliRunner()
    result = runner.invoke(main, ["connect", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.output


# -----------------------------------------------------------------------
# --config option
# -----------------------------------------------------------------------


def test_main_config_option_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "--config" in result.output


# -----------------------------------------------------------------------
# --config option sets path (lines 51-52)
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fnLaunchHub")
def test_main_config_option_calls_set_path(mockLaunch, tmp_path):
    """Lines 51-52: --config option invokes fnSetConfigPath."""
    sConfigPath = str(tmp_path / "vaibify.yml")
    with open(sConfigPath, "w") as fh:
        fh.write("projectName: test\n")
    with patch(
        "vaibify.cli.main.fnSetConfigPath",
        create=True,
    ) as mockSetPath:
        with patch(
            "vaibify.cli.configLoader.fnSetConfigPath",
        ) as mockSetPathReal:
            runner = CliRunner()
            result = runner.invoke(
                main, ["--config", sConfigPath],
            )
            mockSetPathReal.assert_called_once_with(sConfigPath)


# -----------------------------------------------------------------------
# main invoked without subcommand (line 54)
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fnLaunchHub")
def test_main_no_subcommand_calls_launch_hub(mockLaunch):
    """Line 54: no subcommand invokes fnLaunchHub with no explicit port."""
    runner = CliRunner()
    result = runner.invoke(main, [])
    mockLaunch.assert_called_once_with(None)


@patch("vaibify.cli.main.fnLaunchHub")
def test_main_custom_port_passed_to_launch_hub(mockLaunch):
    """Lines 54: --port forwarded to fnLaunchHub."""
    runner = CliRunner()
    result = runner.invoke(main, ["--port", "9999"])
    mockLaunch.assert_called_once_with(9999)


# -----------------------------------------------------------------------
# fnLaunchHub (lines 59-72)
# -----------------------------------------------------------------------


def test_fnLaunchHub_starts_server():
    """Lines 59-72: fnLaunchHub creates app and runs uvicorn."""
    import sys
    mockUvicorn = MagicMock()
    mockWebbrowser = MagicMock()
    with patch.dict(sys.modules, {
        "uvicorn": mockUvicorn, "webbrowser": mockWebbrowser,
    }):
        with patch(
            "vaibify.gui.pipelineServer.fappCreateHubApplication",
        ) as mockApp:
            mockApp.return_value = MagicMock()
            from vaibify.cli.main import fnLaunchHub
            fnLaunchHub(8050)
            mockApp.assert_called_once()
            mockUvicorn.run.assert_called_once()
            args = mockUvicorn.run.call_args
            assert args[1]["port"] == 8050


# -----------------------------------------------------------------------
# setup command (lines 126-138)
# -----------------------------------------------------------------------


def test_setup_launches_wizard():
    """Lines 126-138: setup command starts wizard server."""
    import sys
    mockUvicorn = MagicMock()
    mockWebbrowser = MagicMock()
    with patch.dict(sys.modules, {
        "uvicorn": mockUvicorn, "webbrowser": mockWebbrowser,
    }):
        with patch(
            "vaibify.install.setupServer.fappCreateSetupWizard",
        ) as mockWizard:
            mockWizard.return_value = MagicMock()
            runner = CliRunner()
            result = runner.invoke(main, ["setup"])
            assert "setup wizard" in result.output.lower()
            mockUvicorn.run.assert_called_once()


# -----------------------------------------------------------------------
# gui command (lines 144-163)
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
def test_gui_launches_pipeline_viewer(mockConfig):
    """Lines 144-163: gui command starts pipeline viewer."""
    import sys
    mockConfig.return_value = SimpleNamespace(
        sWorkspaceRoot="/workspace",
        sContainerUser="researcher",
    )
    mockUvicorn = MagicMock()
    mockWebbrowser = MagicMock()
    mockCreateApp = MagicMock(return_value=MagicMock())
    with patch.dict(sys.modules, {
        "uvicorn": mockUvicorn, "webbrowser": mockWebbrowser,
    }):
        with patch(
            "vaibify.gui.pipelineServer.fappCreateApplication",
            mockCreateApp,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["gui"])
            assert "pipeline viewer" in result.output.lower()
            mockUvicorn.run.assert_called_once()
