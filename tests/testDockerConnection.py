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


# -----------------------------------------------------------------------
# texecRunInContainerStreamedWithChunks
# -----------------------------------------------------------------------


def _fMockExecForStream(mockClient, listChunks, iExitCode=0):
    """Wire up the low-level docker-py exec mocks for streaming.

    ``listChunks`` is the sequence yielded by ``exec_start(stream=True,
    demux=True)``; each element is a ``(stdout_bytes, stderr_bytes)``
    tuple. ``iExitCode`` is what ``exec_inspect`` reports afterwards.
    """
    mockClient.api.exec_create.return_value = {"Id": "exec-1"}
    mockClient.api.exec_start.return_value = iter(listChunks)
    mockClient.api.exec_inspect.return_value = {"ExitCode": iExitCode}


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_with_chunks_emits_per_line(mockGetDocker):
    """Each complete line in the demuxed stream becomes one emit call."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockClient.containers.get.return_value = mockContainer
    _fMockExecForStream(mockClient, [
        (b"first\nsecond\n", None),
        (b"third\n", b"oops\n"),
    ])
    listEmits = []
    conn = DockerConnection()
    resultExec = conn.texecRunInContainerStreamedWithChunks(
        "abc123", "do-it",
        lambda sStream, sLine: listEmits.append((sStream, sLine)),
    )
    assert resultExec.iExitCode == 0
    assert listEmits == [
        ("stdout", "first"),
        ("stdout", "second"),
        ("stdout", "third"),
        ("stderr", "oops"),
    ]


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_with_chunks_buffers_partial_lines(mockGetDocker):
    """A line split across chunks is reassembled before emitting."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockClient.containers.get.return_value = mockContainer
    _fMockExecForStream(mockClient, [
        (b"part-", None),
        (b"ial\n", None),
    ])
    listEmits = []
    conn = DockerConnection()
    conn.texecRunInContainerStreamedWithChunks(
        "abc123", "cmd",
        lambda sStream, sLine: listEmits.append((sStream, sLine)),
    )
    assert listEmits == [("stdout", "part-ial")]


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_with_chunks_flushes_trailing_partial(mockGetDocker):
    """A trailing chunk without a newline is still emitted on exit."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockClient.containers.get.return_value = mockContainer
    _fMockExecForStream(mockClient, [
        (b"no-newline-here", None),
    ])
    listEmits = []
    conn = DockerConnection()
    conn.texecRunInContainerStreamedWithChunks(
        "abc123", "cmd",
        lambda sStream, sLine: listEmits.append((sStream, sLine)),
    )
    assert listEmits == [("stdout", "no-newline-here")]


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_with_chunks_propagates_exit_code(mockGetDocker):
    """exec_inspect's ExitCode is the iExitCode on the returned ExecResult."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockClient.containers.get.return_value = mockContainer
    _fMockExecForStream(mockClient, [(b"hi\n", None)], iExitCode=7)
    conn = DockerConnection()
    resultExec = conn.texecRunInContainerStreamedWithChunks(
        "abc123", "cmd", lambda sStream, sLine: None,
    )
    assert resultExec.iExitCode == 7


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_with_chunks_passes_workdir_and_user(mockGetDocker):
    """sWorkdir and sUser thread through to exec_create."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockClient.containers.get.return_value = mockContainer
    _fMockExecForStream(mockClient, [])
    conn = DockerConnection()
    conn.texecRunInContainerStreamedWithChunks(
        "abc123", "pwd", lambda sStream, sLine: None,
        sWorkdir="/workspace", sUser="root",
    )
    dictKwargs = mockClient.api.exec_create.call_args[1]
    assert dictKwargs["workdir"] == "/workspace"
    assert dictKwargs["user"] == "root"


# -----------------------------------------------------------------------
# Audit R1 — pool sizing, cache eviction, streaming RAM discipline
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_constructor_mounts_oversized_pool_adapters(mockGetDocker):
    """Pool ceiling is raised from docker-py default 10 to 32.

    The vanilla TCP HTTPAdapter exposes the pool size on
    ``_pool_maxsize``. docker-py's UnixHTTPAdapter exposes it on the
    constructor-time ``max_pool_size`` attribute (its internal
    PoolManager keeps the urllib3 default in ``_pool_maxsize``);
    check whichever attribute is present.
    """
    from vaibify.docker.dockerConnection import I_DOCKER_POOL_MAX_SIZE
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    DockerConnection()
    listMountCalls = mockClient.api.mount.call_args_list
    setPrefixes = {tCall[0][0] for tCall in listMountCalls}
    assert "http+docker://" in setPrefixes
    assert "http://" in setPrefixes
    assert "https://" in setPrefixes
    for tCall in listMountCalls:
        adapterHttp = tCall[0][1]
        iMaxPool = getattr(
            adapterHttp, "max_pool_size",
            getattr(adapterHttp, "_pool_maxsize", None),
        )
        assert iMaxPool == I_DOCKER_POOL_MAX_SIZE


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_flistGetRunningContainers_evicts_absent_ids(mockGetDocker):
    """Running-list refresh evicts cached entries that vanished."""
    from vaibify.docker.dockerConnection import _CACHED_CONTAINER_USER
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockClient.containers.list.return_value = []
    conn = DockerConnection()
    conn._dictContainers["gone"] = MagicMock()
    _CACHED_CONTAINER_USER["gone"] = "vplanet"
    conn.flistGetRunningContainers()
    assert "gone" not in conn._dictContainers
    assert "gone" not in _CACHED_CONTAINER_USER


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streaming_path_does_not_accumulate_lines(mockGetDocker):
    """A non-None fnEmitChunk opts out of in-memory accumulation."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainerWithUser("researcher")
    mockClient.containers.get.return_value = mockContainer
    _fMockExecForStream(mockClient, [
        (b"alpha\n", None),
        (b"beta\n", b"gamma\n"),
    ])
    listEmits = []
    conn = DockerConnection()
    resultExec = conn.texecRunInContainerStreamedWithChunks(
        "abc123", "spew",
        lambda sStream, sLine: listEmits.append((sStream, sLine)),
    )
    assert len(listEmits) == 3
    assert resultExec.sStdout == ""
    assert resultExec.sStderr == ""


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_non_streaming_callers_still_accumulate(mockGetDocker):
    """fnEmitChunk=None preserves the legacy return-string contract."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    conn = DockerConnection()
    _fMockExecForStream(mockClient, [
        (b"keep\nme\n", b"and\nme\n"),
    ])
    sStdout, sStderr = conn._ftStreamExecLines("exec-1", None)
    assert sStdout == "keep\nme"
    assert sStderr == "and\nme"


# -----------------------------------------------------------------------
# Streaming fetch via get_archive — large-file slice (perf)
# -----------------------------------------------------------------------


def _fnBuildTarStream(sFilename, baContent, iChunkSizeBytes=512):
    """Return a generator that yields the tar bytes for ``baContent``.

    Mirrors the shape of ``container.get_archive``'s first return
    value: an iterable producing arbitrary-sized chunks of raw tar
    bytes for a tar holding a single file ``sFilename`` with payload
    ``baContent``.
    """
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as tar:
        infoTar = tarfile.TarInfo(name=sFilename)
        infoTar.size = len(baContent)
        tar.addfile(infoTar, io.BytesIO(baContent))
    baAll = bufferTar.getvalue()
    return (
        baAll[iOffset:iOffset + iChunkSizeBytes]
        for iOffset in range(0, len(baAll), iChunkSizeBytes)
    )


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnIterStreamFile_yields_identical_bytes(mockGetDocker):
    """Streaming fetch reconstructs the same bytes the small path returns."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    baExpected = b"x" * (3 * 1024 + 17)
    mockContainer.get_archive.return_value = (
        _fnBuildTarStream("payload.bin", baExpected), {"size": len(baExpected)},
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    baReceived = b"".join(
        conn.fnIterStreamFile(
            "abc123", "/workspace/payload.bin", iChunkSizeBytes=1024,
        )
    )
    assert baReceived == baExpected


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnIterStreamFile_emits_bounded_chunks(mockGetDocker):
    """No yielded chunk exceeds the caller's requested chunk size."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    baExpected = b"y" * 10000
    mockContainer.get_archive.return_value = (
        _fnBuildTarStream("payload.bin", baExpected, iChunkSizeBytes=4096),
        {"size": len(baExpected)},
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    listChunks = list(
        conn.fnIterStreamFile(
            "abc123", "/workspace/payload.bin", iChunkSizeBytes=1024,
        )
    )
    assert all(len(b) <= 1024 for b in listChunks)
    assert b"".join(listChunks) == baExpected


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnIterStreamFile_raises_filenotfound_on_missing(mockGetDocker):
    """A missing path surfaces as FileNotFoundError, not a docker exception."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.get_archive.side_effect = RuntimeError("no such file")
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    with pytest.raises(FileNotFoundError):
        next(conn.fnIterStreamFile("abc123", "/nope"))


# -----------------------------------------------------------------------
# Small-file cap on fbaFetchFile
# -----------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fbaFetchFile_caps_oversize_payloads(mockGetDocker):
    """Files larger than ``iMaxBytes`` raise ValueError instead of returning."""
    import base64
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    baOversize = b"q" * 200
    sEncoded = base64.b64encode(baOversize).decode("ascii")
    mockContainer.exec_run.return_value = (0, sEncoded.encode("ascii"))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    with pytest.raises(ValueError, match="exceeds fbaFetchFile cap"):
        conn.fbaFetchFile("abc123", "/tmp/big.bin", iMaxBytes=100)


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fbaFetchFile_under_cap_returns_bytes(mockGetDocker):
    """Below-cap payloads are returned unchanged by the small-file path."""
    import base64
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    baSmall = b"under-cap"
    sEncoded = base64.b64encode(baSmall).decode("ascii")
    mockContainer.exec_run.return_value = (0, sEncoded.encode("ascii"))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    baResult = conn.fbaFetchFile(
        "abc123", "/tmp/small.bin", iMaxBytes=100,
    )
    assert baResult == baSmall


def test_module_init_filters_deprecation_warning():
    """A subprocess import shows the heartbeat-flood filter installed.

    pytest's per-test ``catch_warnings`` context saves and restores the
    filter list, so the filter installed at module-import is invisible
    inside an active pytest session. Verifying via subprocess sidesteps
    that without weakening the assertion.
    """
    import subprocess
    import sys
    sScript = (
        "import warnings, vaibify.docker.dockerConnection as m\n"
        "print(any("
        "t[0] == 'ignore' "
        "and t[2] is DeprecationWarning "
        "and t[1] is not None "
        "and 'ftResultExecuteCommand' in t[1].pattern "
        "for t in warnings.filters))\n"
    )
    resultProcess = subprocess.run(
        [sys.executable, "-c", sScript],
        capture_output=True, text=True, timeout=15,
    )
    assert resultProcess.stdout.strip() == "True", (
        resultProcess.stdout, resultProcess.stderr,
    )
