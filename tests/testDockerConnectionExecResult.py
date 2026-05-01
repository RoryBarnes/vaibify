"""Tests for the split-stream docker exec contract (audit F-R-01).

These cover the new ``ExecResult`` dataclass, the new
``texecRunInContainerStreamed`` entry point, and the legacy
``ftResultExecuteCommand`` backward-compat wrapper that now emits a
``DeprecationWarning`` while preserving the historical
``(iExitCode, sOutput)`` shape.
"""

import warnings

import pytest
from unittest.mock import MagicMock, patch

from vaibify.docker.dockerConnection import (
    DockerConnection,
    ExecResult,
)


def _fMockDockerModule():
    """Build a mock docker module exposing ``from_env``."""
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    return mockDocker, mockClient


def _fMockContainer():
    """Build a mock container compatible with DockerConnection."""
    mockContainer = MagicMock()
    mockContainer.id = "abc123"
    mockContainer.short_id = "abc12"
    mockContainer.name = "test-container"
    mockImage = MagicMock()
    mockImage.tags = ["vaibify:latest"]
    mockImage.id = "sha256:deadbeef"
    mockContainer.image = mockImage
    return mockContainer


# ---------------------------------------------------------------------
# ExecResult dataclass
# ---------------------------------------------------------------------


def test_ExecResult_carries_three_fields():
    """ExecResult exposes iExitCode, sStdout, sStderr."""
    resultExec = ExecResult(
        iExitCode=2, sStdout="hello\n", sStderr="oops\n",
    )
    assert resultExec.iExitCode == 2
    assert resultExec.sStdout == "hello\n"
    assert resultExec.sStderr == "oops\n"


def test_ExecResult_equality_is_field_based():
    """Two ExecResult instances with identical fields compare equal."""
    resultExecA = ExecResult(
        iExitCode=0, sStdout="x", sStderr="",
    )
    resultExecB = ExecResult(
        iExitCode=0, sStdout="x", sStderr="",
    )
    assert resultExecA == resultExecB


# ---------------------------------------------------------------------
# texecRunInContainerStreamed
# ---------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_separates_stdout_and_stderr(mockGetDocker):
    """The streamed entry point preserves stream separation."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (
        0, (b"stdout-bytes\n", b"stderr-bytes\n"),
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    resultExec = conn.texecRunInContainerStreamed(
        "abc123", "echo split",
    )
    assert isinstance(resultExec, ExecResult)
    assert resultExec.sStdout == "stdout-bytes\n"
    assert resultExec.sStderr == "stderr-bytes\n"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_returns_exit_code(mockGetDocker):
    """The streamed entry point returns docker-py's exit code unchanged."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (
        7, (b"", b"boom\n"),
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    resultExec = conn.texecRunInContainerStreamed(
        "abc123", "false",
    )
    assert resultExec.iExitCode == 7
    assert resultExec.sStderr == "boom\n"
    assert resultExec.sStdout == ""


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_handles_none_streams(mockGetDocker):
    """A demuxed call with no stderr returns empty string, not None."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (
        0, (b"only stdout\n", None),
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    resultExec = conn.texecRunInContainerStreamed(
        "abc123", "echo x",
    )
    assert resultExec.sStdout == "only stdout\n"
    assert resultExec.sStderr == ""


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_passes_demux_to_exec_run(mockGetDocker):
    """``exec_run`` is invoked with ``demux=True``."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (0, (b"", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.texecRunInContainerStreamed("abc123", "true")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["demux"] is True


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_streamed_forwards_workdir_and_user(mockGetDocker):
    """Optional sWorkdir / sUser propagate to ``exec_run`` kwargs."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (0, (b"", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    conn.texecRunInContainerStreamed(
        "abc123", "pwd",
        sWorkdir="/workspace", sUser="astro",
    )
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["workdir"] == "/workspace"
    assert dictKwargs["user"] == "astro"


# ---------------------------------------------------------------------
# Legacy wrapper backward compatibility + deprecation
# ---------------------------------------------------------------------


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_legacy_wrapper_emits_deprecation_warning(mockGetDocker):
    """``ftResultExecuteCommand`` warns callers to migrate."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (0, (b"hi", b""))
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    with warnings.catch_warnings(record=True) as listCaught:
        warnings.simplefilter("always")
        conn.ftResultExecuteCommand("abc123", "echo hi")
    listDeprecations = [
        w for w in listCaught
        if issubclass(w.category, DeprecationWarning)
    ]
    assert len(listDeprecations) == 1
    assert "texecRunInContainerStreamed" in str(
        listDeprecations[0].message)


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_legacy_wrapper_returns_merged_tuple(mockGetDocker):
    """Legacy wrapper merges streams into the historical 2-tuple."""
    mockDocker, mockClient = _fMockDockerModule()
    mockGetDocker.return_value = mockDocker
    mockContainer = _fMockContainer()
    mockContainer.exec_run.return_value = (
        3, (b"out-text\n", b"err-text\n"),
    )
    mockClient.containers.get.return_value = mockContainer
    conn = DockerConnection()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        tResult = conn.ftResultExecuteCommand(
            "abc123", "echo merged",
        )
    assert isinstance(tResult, tuple)
    assert tResult[0] == 3
    assert "out-text" in tResult[1]
    assert "err-text" in tResult[1]


# ---------------------------------------------------------------------
# Migrated GUI route surfaces stderr distinctly
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_run_test_route_exposes_stderr_separately():
    """The migrated save-and-run-test handler surfaces sStderr distinctly."""
    from vaibify.gui.routes import testRoutes

    mockDocker = MagicMock()
    mockDocker.texecRunInContainerStreamed = MagicMock(
        return_value=ExecResult(
            iExitCode=1,
            sStdout="collected 1 item\n",
            sStderr="E   AssertionError: foo != bar\n",
        ),
    )
    dictWorkflow = {
        "listSteps": [{
            "sDirectory": "step01",
            "dictTests": {},
            "dictVerification": {},
        }],
        "sProjectRepoPath": "/workspace/proj",
    }
    dictCtx = {
        "require": MagicMock(),
        "docker": mockDocker,
        "save": MagicMock(),
        "workflows": {"cid-1": dictWorkflow},
    }
    listHandlers = {}

    def fnCapturePost(sPath):
        def fnDecorator(fnHandler):
            listHandlers[sPath] = fnHandler
            return fnHandler
        return fnDecorator
    app = MagicMock()
    app.post = fnCapturePost
    testRoutes._fnRegisterTestSaveAndRun(app, dictCtx)
    fnHandler = listHandlers[
        "/api/steps/{sContainerId}/{iStepIndex}/save-and-run-test"
    ]

    mockRequest = MagicMock()
    mockRequest.sFilePath = "tests/test_one.py"
    mockRequest.sContent = "def test_x(): assert 1 == 2"

    with patch(
        "vaibify.gui.routes.testRoutes.fdictRequireWorkflow",
        return_value=dictWorkflow,
    ), patch(
        "vaibify.gui.routes.testRoutes._fnRecordTestResult",
    ), patch(
        "vaibify.gui.routes.testRoutes._fnRegisterTestCommand",
    ):
        dictResult = await fnHandler("cid-1", 0, mockRequest)

    assert dictResult["bPassed"] is False
    assert dictResult["iExitCode"] == 1
    assert dictResult["sStdout"] == "collected 1 item\n"
    assert dictResult["sStderr"] == (
        "E   AssertionError: foo != bar\n"
    )
    assert "AssertionError" in dictResult["sOutput"]
