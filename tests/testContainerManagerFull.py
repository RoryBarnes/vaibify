"""Tests for remaining functions in containerManager."""

import subprocess

import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.docker.containerManager import (
    fnStartContainer,
    fnStopContainer,
    fnRemoveStopped,
    fbContainerIsRunning,
    fdictGetContainerStatus,
    _flistAssembleRunCommand,
    _fnCleanupTempFiles,
    _fnAddPortForwarding,
    _fnAddBindMounts,
    _fnMountSingleSecret,
    fnMountSecrets,
    _fsInspectContainerState,
)


def _fConfigMinimal():
    """Return a minimal mock config."""
    features = SimpleNamespace(bGpu=False)
    return SimpleNamespace(
        sProjectName="testproj",
        sWorkspaceRoot="/workspace",
        listPorts=[],
        listBindMounts=[],
        listSecrets=[],
        features=features,
        bNetworkIsolation=False,
    )


# -----------------------------------------------------------------------
# fnStopContainer
# -----------------------------------------------------------------------


@patch("vaibify.docker.containerManager._fnRunDockerCommand")
@patch("vaibify.docker.containerManager.fnRemoveStopped")
def test_fnStopContainer_calls_stop(
    mockRemove, mockRun,
):
    fnStopContainer("testproj")
    mockRun.assert_called_once()
    saArgs = mockRun.call_args[0][0]
    assert "stop" in saArgs
    assert "testproj" in saArgs
    mockRemove.assert_called_once_with("testproj")


# -----------------------------------------------------------------------
# fnRemoveStopped
# -----------------------------------------------------------------------


@patch("subprocess.run")
def test_fnRemoveStopped_calls_rm(mockRun):
    fnRemoveStopped("testproj")
    saArgs = mockRun.call_args[0][0]
    assert "rm" in saArgs
    assert "testproj" in saArgs


@patch("subprocess.run", side_effect=Exception("fail"))
def test_fnRemoveStopped_ignores_errors(mockRun):
    fnRemoveStopped("testproj")


# -----------------------------------------------------------------------
# fbContainerIsRunning
# -----------------------------------------------------------------------


@patch("subprocess.run")
def test_fbContainerIsRunning_true(mockRun):
    mockResult = MagicMock()
    mockResult.stdout = "true\n"
    mockRun.return_value = mockResult
    assert fbContainerIsRunning("testproj") is True


@patch("subprocess.run")
def test_fbContainerIsRunning_false(mockRun):
    mockResult = MagicMock()
    mockResult.stdout = "false\n"
    mockRun.return_value = mockResult
    assert fbContainerIsRunning("testproj") is False


# -----------------------------------------------------------------------
# fdictGetContainerStatus
# -----------------------------------------------------------------------


@patch("subprocess.run")
def test_fdictGetContainerStatus_running(mockRun):
    mockResult = MagicMock()
    mockResult.returncode = 0
    mockResult.stdout = "running\n"
    mockRun.return_value = mockResult
    dictResult = fdictGetContainerStatus("testproj")
    assert dictResult["bExists"] is True
    assert dictResult["bRunning"] is True
    assert dictResult["sStatus"] == "running"


@patch("subprocess.run")
def test_fdictGetContainerStatus_not_found(mockRun):
    mockResult = MagicMock()
    mockResult.returncode = 1
    mockResult.stdout = ""
    mockRun.return_value = mockResult
    dictResult = fdictGetContainerStatus("testproj")
    assert dictResult["bExists"] is False
    assert dictResult["bRunning"] is False


@patch("subprocess.run")
def test_fdictGetContainerStatus_exited(mockRun):
    mockResult = MagicMock()
    mockResult.returncode = 0
    mockResult.stdout = "exited\n"
    mockRun.return_value = mockResult
    dictResult = fdictGetContainerStatus("testproj")
    assert dictResult["bExists"] is True
    assert dictResult["bRunning"] is False


# -----------------------------------------------------------------------
# _flistAssembleRunCommand
# -----------------------------------------------------------------------


def test_flistAssembleRunCommand_no_user_cmd():
    config = _fConfigMinimal()
    saResult = _flistAssembleRunCommand(
        config, ["--rm"], None
    )
    assert "docker" == saResult[0]
    assert "run" == saResult[1]
    assert "testproj:latest" in saResult


def test_flistAssembleRunCommand_with_cmd():
    config = _fConfigMinimal()
    saResult = _flistAssembleRunCommand(
        config, ["--rm"], ["bash"]
    )
    assert "bash" in saResult


# -----------------------------------------------------------------------
# _fnCleanupTempFiles
# -----------------------------------------------------------------------


def test_fnCleanupTempFiles_removes_files(tmp_path):
    sPath = str(tmp_path / "secret.txt")
    with open(sPath, "w") as fh:
        fh.write("secret")
    _fnCleanupTempFiles([sPath])
    import os
    assert not os.path.exists(sPath)


def test_fnCleanupTempFiles_ignores_missing():
    _fnCleanupTempFiles(["/nonexistent_xyz_test"])


# -----------------------------------------------------------------------
# _fnAddPortForwarding
# -----------------------------------------------------------------------


def test_fnAddPortForwarding_adds_ports():
    saRunArgs = []
    config = _fConfigMinimal()
    config.listPorts = [
        {"host": 8080, "container": 80},
        {"container": 443},
    ]
    _fnAddPortForwarding(config, saRunArgs)
    assert "-p" in saRunArgs
    assert "8080:80" in saRunArgs
    assert "443:443" in saRunArgs


# -----------------------------------------------------------------------
# _fnAddBindMounts
# -----------------------------------------------------------------------


def test_fnAddBindMounts_regular():
    saRunArgs = []
    config = _fConfigMinimal()
    config.listBindMounts = [
        {"host": "/data", "container": "/mnt/data"},
    ]
    _fnAddBindMounts(config, saRunArgs)
    assert "-v" in saRunArgs
    assert "/data:/mnt/data" in saRunArgs


def test_fnAddBindMounts_readonly():
    saRunArgs = []
    config = _fConfigMinimal()
    config.listBindMounts = [
        {
            "host": "/data",
            "container": "/mnt/data",
            "readOnly": True,
        },
    ]
    _fnAddBindMounts(config, saRunArgs)
    assert "/data:/mnt/data:ro" in saRunArgs


# -----------------------------------------------------------------------
# _fnMountSingleSecret
# -----------------------------------------------------------------------


def test_fnMountSingleSecret_adds_mount():
    saRunArgs = []
    listCleanup = []

    def fnMockMount(sName, sMethod):
        return "/tmp/mock_secret"

    dictSecret = {"name": "gh_token", "method": "keyring"}
    _fnMountSingleSecret(
        dictSecret, saRunArgs, listCleanup, fnMockMount,
    )
    assert "/tmp/mock_secret" in listCleanup
    assert "-v" in saRunArgs
    assert any("/run/secrets/gh_token" in s for s in saRunArgs)


# -----------------------------------------------------------------------
# fnMountSecrets
# -----------------------------------------------------------------------


@patch("vaibify.config.secretManager.fsMountSecret",
       return_value="/tmp/sec")
def test_fnMountSecrets_iterates(mockMount):
    config = _fConfigMinimal()
    config.listSecrets = [
        {"name": "token", "method": "keyring"},
    ]
    saRunArgs = []
    listCleanup = []
    fnMountSecrets(config, saRunArgs, listCleanup)
    assert len(listCleanup) == 1


# -----------------------------------------------------------------------
# fnStartContainer
# -----------------------------------------------------------------------


@patch("vaibify.docker.containerManager._fnCleanupTempFiles")
@patch("vaibify.docker.containerManager._fnRunDockerCommand")
@patch("vaibify.docker.containerManager.fnMountSecrets")
@patch("vaibify.docker.containerManager.flistBuildRunArgs",
       return_value=["--rm"])
def test_fnStartContainer_success(
    mockBuild, mockSecrets, mockRun, mockCleanup,
):
    config = _fConfigMinimal()
    fnStartContainer(config, "/docker")
    mockRun.assert_called_once()


@patch("vaibify.docker.containerManager._fnCleanupTempFiles")
@patch("vaibify.docker.containerManager._fnRunDockerCommand",
       side_effect=RuntimeError("fail"))
@patch("vaibify.docker.containerManager.fnMountSecrets")
@patch("vaibify.docker.containerManager.flistBuildRunArgs",
       return_value=["--rm"])
def test_fnStartContainer_cleanup_on_error(
    mockBuild, mockSecrets, mockRun, mockCleanup,
):
    config = _fConfigMinimal()
    with pytest.raises(RuntimeError):
        fnStartContainer(config, "/docker")
    mockCleanup.assert_called_once()


# -----------------------------------------------------------------------
# _fsInspectContainerState
# -----------------------------------------------------------------------


@patch("subprocess.run")
def test_fsInspectContainerState_success(mockRun):
    mockResult = MagicMock()
    mockResult.returncode = 0
    mockResult.stdout = "running\n"
    mockRun.return_value = mockResult
    sResult = _fsInspectContainerState("testproj")
    assert sResult == "running"


@patch("subprocess.run")
def test_fsInspectContainerState_failure(mockRun):
    mockResult = MagicMock()
    mockResult.returncode = 1
    mockResult.stdout = ""
    mockRun.return_value = mockResult
    sResult = _fsInspectContainerState("testproj")
    assert sResult == ""
