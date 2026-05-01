"""Tests for vaibify.docker.dockerContext active-context helpers."""

import subprocess
from unittest.mock import patch, MagicMock

from vaibify.docker.dockerContext import (
    fsActiveDockerContext,
    fbColimaActive,
)


def _fmockCompletedProcess(iReturnCode, sStdout):
    """Build a MagicMock mimicking subprocess.CompletedProcess."""
    mockResult = MagicMock()
    mockResult.returncode = iReturnCode
    mockResult.stdout = sStdout
    return mockResult


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fsActiveDockerContext_returns_stripped_stdout(mockRun):
    mockRun.return_value = _fmockCompletedProcess(0, "  colima\n")
    assert fsActiveDockerContext() == "colima"


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fsActiveDockerContext_returns_empty_on_missing_binary(mockRun):
    mockRun.side_effect = FileNotFoundError("no docker")
    assert fsActiveDockerContext() == ""


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fsActiveDockerContext_returns_empty_on_timeout(mockRun):
    mockRun.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=5)
    assert fsActiveDockerContext() == ""


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fsActiveDockerContext_returns_empty_on_nonzero_exit(mockRun):
    mockRun.return_value = _fmockCompletedProcess(1, "boom\n")
    assert fsActiveDockerContext() == ""


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fbColimaActive_true_when_context_is_colima(mockRun):
    mockRun.return_value = _fmockCompletedProcess(0, "colima\n")
    assert fbColimaActive() is True


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fbColimaActive_false_for_desktop_linux(mockRun):
    mockRun.return_value = _fmockCompletedProcess(0, "desktop-linux\n")
    assert fbColimaActive() is False


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fbColimaActive_false_when_context_empty(mockRun):
    mockRun.return_value = _fmockCompletedProcess(0, "\n")
    assert fbColimaActive() is False


@patch("vaibify.docker.dockerContext.subprocess.run")
def test_fbColimaActive_false_on_error(mockRun):
    mockRun.side_effect = FileNotFoundError("no docker")
    assert fbColimaActive() is False
