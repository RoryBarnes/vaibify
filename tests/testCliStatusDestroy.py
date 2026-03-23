"""Tests for commandStatus and commandDestroy uncovered paths."""

import sys

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.commandStatus import (
    status,
    fnShowDaemonStatus,
    fnShowImageStatus,
    fnShowVolumeStatus,
    fnShowContainerStatus,
)
from vaibify.cli.commandDestroy import (
    destroy,
    fnRemoveVolume,
    fnRemoveImage,
)


def _fMockDockerModule():
    """Return a mock docker module with stable from_env."""
    mockModule = MagicMock()
    mockClient = MagicMock()
    mockModule.from_env.return_value = mockClient
    mockModule.errors.NotFound = type(
        "NotFound", (Exception,), {}
    )
    mockModule.errors.ImageNotFound = type(
        "ImageNotFound", (Exception,), {}
    )
    mockModule.errors.APIError = type(
        "APIError", (Exception,), {}
    )
    mockModule._mockClient = mockClient
    return mockModule


# -----------------------------------------------------------------------
# fnShowDaemonStatus
# -----------------------------------------------------------------------


def test_fnShowDaemonStatus_reachable(capsys):
    mockDocker = _fMockDockerModule()
    mockClient = mockDocker._mockClient
    fnShowDaemonStatus(mockClient)
    sCaptured = capsys.readouterr().out
    assert "running" in sCaptured


def test_fnShowDaemonStatus_unreachable(capsys):
    mockClient = MagicMock()
    mockClient.ping.side_effect = Exception("fail")
    fnShowDaemonStatus(mockClient)
    sCaptured = capsys.readouterr().out
    assert "unavailable" in sCaptured


# -----------------------------------------------------------------------
# fnShowImageStatus
# -----------------------------------------------------------------------


def test_fnShowImageStatus_found(capsys):
    mockClient = MagicMock()
    mockImage = MagicMock()
    mockImage.attrs = {"Created": "2024-01-01"}
    mockClient.images.get.return_value = mockImage
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowImageStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "testproj:latest" in sCaptured
    assert "built" in sCaptured


def test_fnShowImageStatus_not_found(capsys):
    mockClient = MagicMock()
    mockClient.images.get.side_effect = Exception("nope")
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowImageStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "not found" in sCaptured


# -----------------------------------------------------------------------
# fnShowVolumeStatus
# -----------------------------------------------------------------------


def test_fnShowVolumeStatus_exists(capsys):
    mockClient = MagicMock()
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowVolumeStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "exists" in sCaptured


def test_fnShowVolumeStatus_not_found(capsys):
    mockClient = MagicMock()
    mockClient.volumes.get.side_effect = Exception("nope")
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowVolumeStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "not found" in sCaptured


# -----------------------------------------------------------------------
# fnShowContainerStatus
# -----------------------------------------------------------------------


def test_fnShowContainerStatus_running(capsys):
    mockClient = MagicMock()
    mockContainer = MagicMock()
    mockContainer.name = "testproj"
    mockContainer.status = "running"
    mockClient.containers.list.return_value = [mockContainer]
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowContainerStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "running" in sCaptured


def test_fnShowContainerStatus_none(capsys):
    mockClient = MagicMock()
    mockClient.containers.list.return_value = []
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowContainerStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "none" in sCaptured


def test_fnShowContainerStatus_error(capsys):
    mockClient = MagicMock()
    mockClient.containers.list.side_effect = Exception("fail")
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowContainerStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "unable" in sCaptured


# -----------------------------------------------------------------------
# status CLI — full invocation
# -----------------------------------------------------------------------


@patch("vaibify.cli.commandStatus.fbDockerAvailable",
       return_value=False)
def test_status_no_docker(mockAvail):
    runner = CliRunner()
    result = runner.invoke(status, [])
    assert result.exit_code != 0
    assert "Docker" in result.output


@patch("vaibify.cli.commandStatus.fnShowContainerStatus")
@patch("vaibify.cli.commandStatus.fnShowVolumeStatus")
@patch("vaibify.cli.commandStatus.fnShowImageStatus")
@patch("vaibify.cli.commandStatus.fnShowDaemonStatus")
@patch("vaibify.cli.commandStatus.fconfigLoad")
@patch("vaibify.cli.commandStatus.fbDockerAvailable",
       return_value=True)
def test_status_full_run(
    mockAvail, mockLoad, mockDaemon,
    mockImage, mockVolume, mockContainer,
):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(status, [])
    assert result.exit_code == 0
    mockDaemon.assert_called_once()
    mockImage.assert_called_once()
    mockVolume.assert_called_once()
    mockContainer.assert_called_once()


# -----------------------------------------------------------------------
# fnRemoveVolume
# -----------------------------------------------------------------------


def test_fnRemoveVolume_success(capsys):
    mockDocker = _fMockDockerModule()
    with patch.dict("sys.modules", {"docker": mockDocker}):
        fnRemoveVolume("test-workspace")
    sCaptured = capsys.readouterr().out
    assert "Removed" in sCaptured


def test_fnRemoveVolume_not_found(capsys):
    mockDocker = _fMockDockerModule()
    mockDocker.from_env().volumes.get.side_effect = (
        mockDocker.errors.NotFound("nope")
    )
    with patch.dict("sys.modules", {"docker": mockDocker}):
        fnRemoveVolume("test-workspace")
    sCaptured = capsys.readouterr().out
    assert "does not exist" in sCaptured


def test_fnRemoveVolume_api_error():
    mockDocker = _fMockDockerModule()
    mockVolume = MagicMock()
    mockVolume.remove.side_effect = (
        mockDocker.errors.APIError("busy")
    )
    mockDocker.from_env().volumes.get.return_value = mockVolume
    with patch.dict("sys.modules", {"docker": mockDocker}):
        with pytest.raises(SystemExit):
            fnRemoveVolume("test-workspace")


# -----------------------------------------------------------------------
# fnRemoveImage
# -----------------------------------------------------------------------


def test_fnRemoveImage_success(capsys):
    mockDocker = _fMockDockerModule()
    with patch.dict("sys.modules", {"docker": mockDocker}):
        fnRemoveImage("testproj:latest")
    sCaptured = capsys.readouterr().out
    assert "Removed" in sCaptured


def test_fnRemoveImage_not_found(capsys):
    mockDocker = _fMockDockerModule()
    mockDocker.from_env().images.remove.side_effect = (
        mockDocker.errors.ImageNotFound("nope")
    )
    with patch.dict("sys.modules", {"docker": mockDocker}):
        fnRemoveImage("testproj:latest")
    sCaptured = capsys.readouterr().out
    assert "does not exist" in sCaptured


def test_fnRemoveImage_api_error():
    mockDocker = _fMockDockerModule()
    mockDocker.from_env().images.remove.side_effect = (
        mockDocker.errors.APIError("problem")
    )
    with patch.dict("sys.modules", {"docker": mockDocker}):
        with pytest.raises(SystemExit):
            fnRemoveImage("testproj:latest")


# -----------------------------------------------------------------------
# destroy CLI — full invocation
# -----------------------------------------------------------------------


@patch("vaibify.cli.commandDestroy.fbDockerAvailable",
       return_value=True)
@patch("vaibify.cli.commandDestroy.fconfigLoad")
@patch("vaibify.cli.commandDestroy.fnRemoveVolume")
def test_destroy_confirm_yes(
    mockRemove, mockLoad, mockAvail,
):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(destroy, [], input="y\nn\n")
    assert result.exit_code == 0
    mockRemove.assert_called_once()
    assert "Destroy complete" in result.output


@patch("vaibify.cli.commandDestroy.fbDockerAvailable",
       return_value=True)
@patch("vaibify.cli.commandDestroy.fconfigLoad")
def test_destroy_confirm_no(mockLoad, mockAvail):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(destroy, [], input="n\n")
    assert "Aborted" in result.output


@patch("vaibify.cli.commandDestroy.fbDockerAvailable",
       return_value=True)
@patch("vaibify.cli.commandDestroy.fconfigLoad")
@patch("vaibify.cli.commandDestroy.fnRemoveVolume")
@patch("vaibify.cli.commandDestroy.fnRemoveImage")
def test_destroy_also_image(
    mockRemoveImg, mockRemoveVol, mockLoad, mockAvail,
):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(destroy, [], input="y\ny\n")
    assert result.exit_code == 0
    mockRemoveVol.assert_called_once()
    mockRemoveImg.assert_called_once()
