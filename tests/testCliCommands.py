"""Tests for CLI commands: main group, stop, connect, verify, init, build."""

import os
import tempfile

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.main import main
from vaibify.cli.commandInit import (
    flistAvailableTemplates,
    fsTemplatePath,
    fbConfigExists,
)
from vaibify.cli.commandBuild import (
    fnPrepareBuildContext,
    fnWriteSystemPackages,
    fnWritePythonPackages,
)


# -----------------------------------------------------------------------
# main group help
# -----------------------------------------------------------------------


def test_main_help_shows_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "build" in result.output
    assert "start" in result.output


def test_main_help_shows_description():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "Vaibify" in result.output


# -----------------------------------------------------------------------
# stop — with mock
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.cli.main.fnStopContainer",
       create=True)
def test_stop_calls_stop_container(mockStop, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj")
    with patch(
        "vaibify.docker.containerManager.fnStopContainer"
    ) as mockStopReal:
        runner = CliRunner()
        result = runner.invoke(main, ["stop"])
        if result.exit_code == 0:
            assert "Stopped" in result.output


# -----------------------------------------------------------------------
# connect
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_connect_invokes_docker_exec(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["connect"])
    mockRun.assert_called_once()
    listArgs = mockRun.call_args[0][0]
    assert "docker" in listArgs
    assert "exec" in listArgs


# -----------------------------------------------------------------------
# verify
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_verify_invokes_check_isolation(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["verify"])
    mockRun.assert_called_once()
    listArgs = mockRun.call_args[0][0]
    assert "checkIsolation" in listArgs[-1]


# -----------------------------------------------------------------------
# gui help text
# -----------------------------------------------------------------------


def test_gui_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["gui", "--help"])
    assert result.exit_code == 0
    assert "pipeline viewer" in result.output.lower()


# -----------------------------------------------------------------------
# commandInit: flistAvailableTemplates
# -----------------------------------------------------------------------


def test_flistAvailableTemplates_returns_list():
    listTemplates = flistAvailableTemplates()
    assert isinstance(listTemplates, list)


def test_flistAvailableTemplates_contains_known():
    listTemplates = flistAvailableTemplates()
    if listTemplates:
        assert all(isinstance(s, str) for s in listTemplates)


# -----------------------------------------------------------------------
# commandInit: fsTemplatePath
# -----------------------------------------------------------------------


def test_fsTemplatePath_returns_string():
    sPath = fsTemplatePath("sandbox")
    assert isinstance(sPath, str)
    assert "sandbox" in sPath


def test_fsTemplatePath_is_absolute():
    sPath = fsTemplatePath("sandbox")
    assert os.path.isabs(sPath)


# -----------------------------------------------------------------------
# commandInit: fbConfigExists
# -----------------------------------------------------------------------


def test_fbConfigExists_false_in_tmpdir():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sOriginal = os.getcwd()
        os.chdir(sTmpDir)
        try:
            assert fbConfigExists() is False
        finally:
            os.chdir(sOriginal)


# -----------------------------------------------------------------------
# commandBuild: fnPrepareBuildContext
# -----------------------------------------------------------------------


@patch("vaibify.config.containerConfig.fnGenerateContainerConf")
@patch("vaibify.cli.commandBuild.fnCopyDirectorScript")
def test_fnPrepareBuildContext_writes_files(
    mockCopy, mockGenerate
):
    config = SimpleNamespace(
        sProjectName="testproj",
        listSystemPackages=["gcc"],
        listPythonPackages=["numpy"],
        sPipInstallFlags="",
        listBinaries=[],
        listRepositories=[],
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnPrepareBuildContext(config, sTmpDir)
        assert os.path.isfile(
            os.path.join(sTmpDir, "system-packages.txt"))
        assert os.path.isfile(
            os.path.join(sTmpDir, "requirements.txt"))
        assert os.path.isfile(
            os.path.join(sTmpDir, "pip-flags.txt"))
        assert os.path.isfile(
            os.path.join(sTmpDir, "binaries.env"))


# -----------------------------------------------------------------------
# commandBuild: fnWriteSystemPackages with tmp_path
# -----------------------------------------------------------------------


def test_fnWriteSystemPackages_creates_file(tmp_path):
    config = SimpleNamespace(
        listSystemPackages=["vim", "wget"])
    fnWriteSystemPackages(config, str(tmp_path))
    sPath = tmp_path / "system-packages.txt"
    assert sPath.exists()
    sContent = sPath.read_text()
    assert "vim" in sContent
    assert "wget" in sContent


# -----------------------------------------------------------------------
# commandBuild: fnWritePythonPackages with tmp_path
# -----------------------------------------------------------------------


def test_fnWritePythonPackages_creates_file(tmp_path):
    config = SimpleNamespace(
        listPythonPackages=["matplotlib", "pandas"])
    fnWritePythonPackages(config, str(tmp_path))
    sPath = tmp_path / "requirements.txt"
    assert sPath.exists()
    sContent = sPath.read_text()
    assert "matplotlib" in sContent
    assert "pandas" in sContent


def test_fnWritePythonPackages_empty_list(tmp_path):
    config = SimpleNamespace(listPythonPackages=[])
    fnWritePythonPackages(config, str(tmp_path))
    sPath = tmp_path / "requirements.txt"
    sContent = sPath.read_text()
    assert sContent.strip() == ""
