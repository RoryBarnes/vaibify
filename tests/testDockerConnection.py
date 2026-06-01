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
    mockContainer.put_archive = MagicMock(return_value=True)
    conn = DockerConnection()
    conn.fnWriteFile(
        "abc123", "/tmp/output.txt", b"data")
    mockContainer.put_archive.assert_called_once()
    sDirectory = mockContainer.put_archive.call_args[0][0]
    assert sDirectory == "/tmp"


# -----------------------------------------------------------------------
# Default unprivileged user (security: docker exec must not default to root)
# -----------------------------------------------------------------------


def _fMockContainerWithUser(sUser):
    """Mock container whose image USER directive is ``sUser``.

    The resolver reads ``container.image.attrs["Config"]["User"]``,
    which is the immutable USER directive baked into the image at
    build time — not ``container.attrs["Config"]["User"]``, which is
    the runtime user (overridable by ``docker run --user``).
    """
    mockContainer = _fMockContainer()
    mockContainer.image.attrs = {"Config": {"User": sUser}}
    return mockContainer


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_texecRunInContainerStreamed_defaults_to_image_user(mockGetDocker):
    """``docker exec`` defaults to the image's unprivileged user."""
    from vaibify.docker.dockerConnection import _CACHED_CONTAINER_USER
    _CACHED_CONTAINER_USER.clear()
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockContainer.exec_run.return_value = (0, (b"ok", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.texecRunInContainerStreamed("abc123", "id")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "researcher"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_texecRunInContainerStreamed_explicit_root_overrides(mockGetDocker):
    """Callers that genuinely need root can opt in via ``sUser="root"``."""
    from vaibify.docker.dockerConnection import _CACHED_CONTAINER_USER
    _CACHED_CONTAINER_USER.clear()
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockContainer.exec_run.return_value = (0, (b"ok", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.texecRunInContainerStreamed("abc123", "id", sUser="root")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "root"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fsExecCreate_defaults_to_image_user(mockGetDocker):
    """Interactive terminals open as the unprivileged image user."""
    from vaibify.docker.dockerConnection import _CACHED_CONTAINER_USER
    _CACHED_CONTAINER_USER.clear()
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("scientist")
    mockClient.containers.get.return_value = mockContainer
    mockClient.api.exec_create.return_value = {"Id": "exec_id"}
    conn = DockerConnection()
    conn.fsExecCreate("abc123")
    dictKwargs = mockClient.api.exec_create.call_args[1]
    assert dictKwargs["user"] == "scientist"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_resolve_user_falls_back_to_researcher_when_attrs_missing(
    mockGetDocker,
):
    """Older images without USER pinned still get an unprivileged default."""
    from vaibify.docker.dockerConnection import _CACHED_CONTAINER_USER
    _CACHED_CONTAINER_USER.clear()
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("")
    mockContainer.exec_run.return_value = (0, (b"ok", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.texecRunInContainerStreamed("abc123", "id")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "researcher"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_resolve_user_ignores_run_user_zero_override(mockGetDocker):
    """``docker run --user 0`` must not propagate into dispatched exec.

    Regression for the L1-gate bug: vaibify starts containers with
    ``--user 0`` so the entrypoint's root phase can chown the
    workspace, then ``gosu``-drops to the unprivileged user for PID 1.
    The resolver must follow the image's install identity (USER
    directive), not the runtime override.
    """
    from vaibify.docker.dockerConnection import _CACHED_CONTAINER_USER
    _CACHED_CONTAINER_USER.clear()
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockContainer.attrs = {"Config": {"User": "0"}}
    mockContainer.exec_run.return_value = (0, (b"ok", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.texecRunInContainerStreamed("abc123", "id")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "researcher"


# -----------------------------------------------------------------------
# Secret-bearing writes (audit M1: TOCTOU window between put_archive and chmod)
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnWriteFileViaTar_stamps_mode_uid_gid(mockGetDocker):
    """Secret-bearing writes can stamp mode/uid/gid into the tarball entry."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockClient.containers.get.return_value = mockContainer
    mockContainer.put_archive = MagicMock(return_value=True)
    conn = DockerConnection()
    conn.fnWriteFileViaTar(
        "abc123", "/tmp/secret.env", b"token=abc",
        iMode=0o600, iUid=1000, iGid=1000,
    )
    bufferTar = mockContainer.put_archive.call_args[0][1]
    bufferTar.seek(0)
    with tarfile.open(fileobj=bufferTar, mode="r") as tar:
        listMembers = tar.getmembers()
    assert len(listMembers) == 1
    assert listMembers[0].mode == 0o600
    assert listMembers[0].uid == 1000
    assert listMembers[0].gid == 1000


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnWriteFile_forwards_mode_uid_gid(mockGetDocker):
    """``fnWriteFile`` forwards mode/uid/gid to the underlying tar write."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockClient.containers.get.return_value = mockContainer
    mockContainer.put_archive = MagicMock(return_value=True)
    conn = DockerConnection()
    conn.fnWriteFile(
        "abc123", "/tmp/x", b"data",
        iMode=0o600, iUid=1000, iGid=1000,
    )
    bufferTar = mockContainer.put_archive.call_args[0][1]
    bufferTar.seek(0)
    with tarfile.open(fileobj=bufferTar, mode="r") as tar:
        listMembers = tar.getmembers()
    assert listMembers[0].mode == 0o600
    assert listMembers[0].uid == 1000


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnWriteFileViaTar_sets_mtime_to_current_time(mockGetDocker):
    """Files written via put_archive must carry a real mtime.

    tarfile.TarInfo defaults mtime to 0; callers of fnWriteFileViaTar
    do not normally set it. Without an explicit assignment, every
    file vaibify writes lands in the container with epoch-0 mtime,
    which corrupts test-source-mtime lineage checks and surfaces as
    "1970-01-01" in the dashboard.
    """
    import io
    import tarfile
    import time

    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockClient.containers.get.return_value = mockContainer
    mockContainer.put_archive = MagicMock(return_value=True)
    iBefore = int(time.time())
    conn = DockerConnection()
    conn.fnWriteFileViaTar(
        "abc123", "/tmp/output.txt", b"contents")
    iAfter = int(time.time())
    bufferTar = mockContainer.put_archive.call_args[0][1]
    bufferTar.seek(0)
    with tarfile.open(fileobj=bufferTar, mode="r") as tar:
        listMembers = tar.getmembers()
    assert len(listMembers) == 1
    assert iBefore <= listMembers[0].mtime <= iAfter
