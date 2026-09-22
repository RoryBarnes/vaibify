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
# `new=` rather than a plain patch: supplying the replacement
# explicitly stops mock injecting an extra positional argument,
# so the build command's second resolver is stubbed without
# rewriting the signature of every test below.
@patch("vaibify.cli.commandBuild.fsResolveProjectConfigPath",
       new=lambda sProjectName=None: "/projects/thisProject/vaibify.yml")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.cli.commandBuild.flistRunBuildPreflight",
       return_value=[])
def test_build_catches_runtime_error(
    mockPreflight, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import fnBuildCommand
    mockConfig.return_value = _fConfigForBuild()
    mockBuild.side_effect = RuntimeError("Docker command failed")
    runner = CliRunner()
    result = runner.invoke(fnBuildCommand)
    assert result.exit_code != 0
    assert "Docker build failed" in result.output
    assert "Traceback" not in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
# `new=` rather than a plain patch: supplying the replacement
# explicitly stops mock injecting an extra positional argument,
# so the build command's second resolver is stubbed without
# rewriting the signature of every test below.
@patch("vaibify.cli.commandBuild.fsResolveProjectConfigPath",
       new=lambda sProjectName=None: "/projects/thisProject/vaibify.yml")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.cli.commandBuild.flistRunBuildPreflight",
       return_value=[])
def test_build_catches_file_not_found(
    mockPreflight, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import fnBuildCommand
    mockConfig.return_value = _fConfigForBuild()
    mockBuild.side_effect = FileNotFoundError("director.py")
    runner = CliRunner()
    result = runner.invoke(fnBuildCommand)
    assert result.exit_code != 0
    assert "Build context preparation failed" in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
# `new=` rather than a plain patch: supplying the replacement
# explicitly stops mock injecting an extra positional argument,
# so the build command's second resolver is stubbed without
# rewriting the signature of every test below.
@patch("vaibify.cli.commandBuild.fsResolveProjectConfigPath",
       new=lambda sProjectName=None: "/projects/thisProject/vaibify.yml")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.cli.commandBuild.flistRunBuildPreflight",
       return_value=[])
def test_build_catches_value_error(
    mockPreflight, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import fnBuildCommand
    mockConfig.return_value = _fConfigForBuild()
    mockBuild.side_effect = ValueError("Unknown overlay name: 'bogus'")
    runner = CliRunner()
    result = runner.invoke(fnBuildCommand)
    assert result.exit_code != 0
    assert "Unknown overlay name" in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
# `new=` rather than a plain patch: supplying the replacement
# explicitly stops mock injecting an extra positional argument,
# so the build command's second resolver is stubbed without
# rewriting the signature of every test below.
@patch("vaibify.cli.commandBuild.fsResolveProjectConfigPath",
       new=lambda sProjectName=None: "/projects/thisProject/vaibify.yml")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.cli.preflightChecks._ftDockerInfoProbe",
       return_value=(1, "Cannot connect to the Docker daemon"))
@patch("vaibify.docker.dockerContext.fsActiveDockerContext",
       return_value="desktop-linux")
def test_build_exits_when_docker_unreachable(
    mockContext, mockProbe, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import fnBuildCommand
    mockConfig.return_value = _fConfigForBuild()
    runner = CliRunner()
    result = runner.invoke(fnBuildCommand)
    assert result.exit_code != 0
    assert "Docker daemon not reachable" in result.output
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


def testAFailedBuildRetainsItsContextAndPrunesTheOlderOnes(
    tmp_path, monkeypatch,
):
    """The retention runs on the real failure path, not just in isolation.

    Driving ``fnBuildFromConfig`` rather than calling the prune directly
    is the point: a collector that is never reached on the path that
    creates the litter collects nothing, and no test of the collector
    alone can see that.
    """
    import pytest
    from vaibify.cli import commandBuild
    from vaibify.config import hostResidue, registryManager

    pathBuild = tmp_path / "build"
    pathBuild.mkdir()
    monkeypatch.setattr(
        commandBuild, "_S_BUILD_STAGING_DIRECTORY", str(pathBuild))
    monkeypatch.setattr(
        hostResidue, "S_BUILD_CONTEXT_ROOT", str(pathBuild))
    monkeypatch.setattr(
        registryManager, "flistGetAllProjects",
        lambda: [{"sName": "testproj"}])

    listOlder = [f"testproj-oooooo{iIndex:02d}" for iIndex in range(4)]
    for iIndex, sName in enumerate(listOlder):
        (pathBuild / sName).mkdir()
        os.utime(pathBuild / sName, (1_600_000_000 - iIndex * 60,) * 2)

    monkeypatch.setattr(
        commandBuild, "fnCopyPackagedTree",
        lambda pathSource, pathTarget: pathTarget.mkdir(parents=True))
    monkeypatch.setattr(
        commandBuild, "fnPrepareBuildContext",
        lambda config, sStagedDir, sProjectDirectory=None: None)
    monkeypatch.setattr(
        commandBuild, "_ftReadPinnedEnvironment", lambda: ())
    monkeypatch.setattr(
        commandBuild, "_fbResolveNoCache", lambda config, bNoCache: False)
    monkeypatch.setattr(
        commandBuild, "fnWarnIfBaseImageFloating", lambda config: None)

    def _fnFailTheBuild(config, sStagedDir, bNoCache=False):
        raise RuntimeError("the daemon refused the build")

    monkeypatch.setattr(
        commandBuild, "_ffnImportBuildOrExit", lambda: _fnFailTheBuild)

    with pytest.raises(RuntimeError):
        commandBuild.fnBuildFromConfig(
            _fConfigForBuild(), "/docker", False)

    listRemaining = sorted(
        sPath.name for sPath in pathBuild.iterdir() if sPath.is_dir())
    assert len(listRemaining) == 3, listRemaining
    # The failure just seen is retained, and it is the newest.
    listRetainedOlder = [
        sName for sName in listRemaining if sName in listOlder]
    assert listRetainedOlder == listOlder[:2], (
        "the two newest older contexts are kept beside the new one")
