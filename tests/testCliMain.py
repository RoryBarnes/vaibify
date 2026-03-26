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


@patch("vaibify.cli.main.fconfigLoad")
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


@patch("vaibify.cli.main.fconfigLoad")
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


@patch("vaibify.cli.main.fconfigLoad")
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


# -----------------------------------------------------------------------
# verify
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigLoad")
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


@patch("vaibify.cli.main.fconfigLoad")
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


@patch("vaibify.cli.main.fconfigLoad")
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


def test_pull_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["pull", "--help"])
    assert result.exit_code == 0
    assert "Pull" in result.output


# -----------------------------------------------------------------------
# --config option
# -----------------------------------------------------------------------


def test_main_config_option_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "--config" in result.output
