"""Tests for vaibify.docker.dockerConnection with mocked docker-py."""

import io
import tarfile

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from vaibify.docker.dockerConnection import DockerConnection


# -----------------------------------------------------------------------
# Helper: mock docker module
# -----------------------------------------------------------------------


def _fMockDockerModule():
    """Build a mock docker module with from_env returning a client."""
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    return mockDocker, mockClient


def _fMockContainer():
    """Build a mock container with exec_run and get_archive."""
    mockContainer = MagicMock()
    mockContainer.id = "abc123"
    mockContainer.short_id = "abc12"
    mockContainer.name = "test-container"
    mockImage = MagicMock()
    mockImage.tags = ["vaibify:latest"]
    mockImage.id = "sha256:deadbeef"
    mockContainer.image = mockImage
    return mockContainer


# -----------------------------------------------------------------------
# Constructor
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_constructor_calls_from_env(mockGetDocker):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    conn = DockerConnection()
    mockDocker.from_env.assert_called_once()


# -----------------------------------------------------------------------
# flistGetRunningContainers
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_flistGetRunningContainers_returns_info(
    mockGetDocker
):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockClient.containers.list.return_value = [mockContainer]
    conn = DockerConnection()
    listResult = conn.flistGetRunningContainers()
    assert len(listResult) == 1
    assert listResult[0]["sName"] == "test-container"
    assert listResult[0]["sContainerId"] == "abc123"
    assert listResult[0]["sImage"] == "vaibify:latest"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_flistGetRunningContainers_empty(mockGetDocker):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockClient.containers.list.return_value = []
    conn = DockerConnection()
    listResult = conn.flistGetRunningContainers()
    assert listResult == []


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_flistGetRunningContainers_no_image_tags(
    mockGetDocker
):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.image.tags = []
    mockContainer.image.id = "sha256:deadbeefcafe"
    mockClient.containers.list.return_value = [mockContainer]
    conn = DockerConnection()
    listResult = conn.flistGetRunningContainers()
    assert listResult[0]["sImage"] == "sha256:deadb"


# -----------------------------------------------------------------------
# ftResultExecuteCommand
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_ftResultExecuteCommand_returns_tuple(
    mockGetDocker
):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (
        0, b"hello world\n"
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    iExit, sOutput = conn.ftResultExecuteCommand(
        "abc123", "echo hello")
    assert iExit == 0
    assert "hello world" in sOutput


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_ftResultExecuteCommand_with_workdir(
    mockGetDocker
):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (0, b"ok")
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.ftResultExecuteCommand(
        "abc123", "pwd", sWorkdir="/workspace")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["workdir"] == "/workspace"


# -----------------------------------------------------------------------
# fbaFetchFile
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fbaFetchFile_returns_bytes(mockGetDocker):
    import base64
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    baContent = b"file content here"
    sEncoded = base64.b64encode(baContent).decode("ascii")
    mockContainer.exec_run.return_value = (
        0, sEncoded.encode("ascii"))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    baResult = conn.fbaFetchFile("abc123", "/tmp/test.txt")
    assert baResult == baContent


# -----------------------------------------------------------------------
# fnWriteFile
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnWriteFile_uses_exec(mockGetDocker):
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockClient.containers.get.return_value = mockContainer
    mockContainer.exec_run.return_value = (0, b"")
    conn = DockerConnection()
    conn.fnWriteFile(
        "abc123", "/tmp/output.txt", b"data")
    mockContainer.exec_run.assert_called_once()
    listCmd = mockContainer.exec_run.call_args.kwargs["cmd"]
    sShellCommand = listCmd[2]
    assert "base64" in sShellCommand
    assert "/tmp/output.txt" in sShellCommand
