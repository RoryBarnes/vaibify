"""Tests for vaibify.cli.commandBuild pure helpers."""

import os
import subprocess
import tempfile

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from vaibify.cli.commandBuild import (
    fnWriteSystemPackages,
    fnWritePythonPackages,
    fnWritePipInstallFlags,
    fnWriteBinariesEnv,
)


def _fConfigForBuild():
    """Return a minimal config for build context tests."""
    return SimpleNamespace(
        sProjectName="testproj",
        listSystemPackages=["gcc", "make", "git"],
        listPythonPackages=["numpy", "scipy"],
        sPipInstallFlags="--no-deps",
        listBinaries=[
            {"name": "solver", "path": "/workspace/bin/solver"},
        ],
    )


def test_fnWriteSystemPackages_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWriteSystemPackages(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "system-packages.txt")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "gcc" in sContent
        assert "make" in sContent


def test_fnWritePythonPackages_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePythonPackages(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "requirements.txt")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "numpy" in sContent
        assert "scipy" in sContent


def test_fnWritePipInstallFlags_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePipInstallFlags(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "pip-flags.txt")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "--no-deps" in sContent


def test_fnWriteBinariesEnv_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWriteBinariesEnv(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "binaries.env")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "solver=/workspace/bin/solver" in sContent


def test_fnWriteBinariesEnv_empty():
    config = SimpleNamespace(listBinaries=[])
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWriteBinariesEnv(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "binaries.env")
        with open(sPath) as fh:
            sContent = fh.read()
        assert sContent.strip() == ""


# -------------------------------------------------------------------
# Error handling tests for the build CLI command
# -------------------------------------------------------------------

@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.docker.fbDockerDaemonReachable",
       return_value=True)
def test_build_catches_runtime_error(
    mockDaemon, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = _fConfigForBuild()
    mockBuild.side_effect = RuntimeError("Docker command failed")
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code != 0
    assert "Docker build failed" in result.output
    assert "Traceback" not in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.docker.fbDockerDaemonReachable",
       return_value=True)
def test_build_catches_file_not_found(
    mockDaemon, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = _fConfigForBuild()
    mockBuild.side_effect = FileNotFoundError("director.py")
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code != 0
    assert "Build context preparation failed" in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.docker.fbDockerDaemonReachable",
       return_value=True)
def test_build_catches_value_error(
    mockDaemon, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = _fConfigForBuild()
    mockBuild.side_effect = ValueError("Unknown overlay name: 'bogus'")
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code != 0
    assert "Unknown overlay name" in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.docker.fbDockerDaemonReachable",
       return_value=False)
def test_build_exits_when_docker_unreachable(
    mockDaemon, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = _fConfigForBuild()
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code != 0
    assert "Docker daemon is not reachable" in result.output
    mockBuild.assert_not_called()


@patch("subprocess.run")
def test_fbDockerDaemonReachable_true(mockRun):
    from vaibify.docker import fbDockerDaemonReachable
    mockRun.return_value = SimpleNamespace(returncode=0)
    assert fbDockerDaemonReachable() is True


@patch("subprocess.run")
def test_fbDockerDaemonReachable_false(mockRun):
    from vaibify.docker import fbDockerDaemonReachable
    mockRun.return_value = SimpleNamespace(returncode=1)
    assert fbDockerDaemonReachable() is False


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_fbDockerDaemonReachable_no_docker(mockRun):
    from vaibify.docker import fbDockerDaemonReachable
    assert fbDockerDaemonReachable() is False


@patch("subprocess.run",
       side_effect=subprocess.TimeoutExpired("docker", 10))
def test_fbDockerDaemonReachable_timeout(mockRun):
    from vaibify.docker import fbDockerDaemonReachable
    assert fbDockerDaemonReachable() is False
