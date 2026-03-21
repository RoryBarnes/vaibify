"""Tests for CLI subcommands: config, destroy, init, start, status, publish."""

import os
import tempfile

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.commandConfig import (
    config,
    fnWriteYaml,
    fdictLoadYamlFile,
)
from vaibify.cli.commandDestroy import (
    destroy,
    fnRequireDocker,
    fnRemoveVolume,
    fnRemoveImage,
)
from vaibify.cli.commandInit import (
    init,
    flistAvailableTemplates,
    fnPrintAvailableTemplates,
    fnCopyDirectoryContents,
    fbConfigExists,
)
from vaibify.cli.commandStart import start
from vaibify.cli.commandStatus import status
from vaibify.cli.commandPublish import publish


# -----------------------------------------------------------------------
# commandConfig: fnWriteYaml
# -----------------------------------------------------------------------


def test_fnWriteYaml_creates_file(tmp_path):
    sPath = str(tmp_path / "out.yml")
    fnWriteYaml({"sKey": "value"}, sPath)
    assert os.path.isfile(sPath)


def test_fnWriteYaml_content_readable(tmp_path):
    sPath = str(tmp_path / "out.yml")
    fnWriteYaml({"iCount": 42}, sPath)
    dictLoaded = fdictLoadYamlFile(sPath)
    assert dictLoaded["iCount"] == 42


def test_fnWriteYaml_bad_path_exits():
    runner = CliRunner()
    with pytest.raises(SystemExit):
        fnWriteYaml({"a": 1}, "/nonexistent_dir_xyz/out.yml")


# -----------------------------------------------------------------------
# commandConfig: fdictLoadYamlFile
# -----------------------------------------------------------------------


def test_fdictLoadYamlFile_missing_file_exits():
    with pytest.raises(SystemExit):
        fdictLoadYamlFile("/nonexistent_xyz.yml")


def test_fdictLoadYamlFile_empty_returns_dict(tmp_path):
    sPath = str(tmp_path / "empty.yml")
    with open(sPath, "w") as fh:
        fh.write("")
    dictResult = fdictLoadYamlFile(sPath)
    assert dictResult == {}


def test_fdictLoadYamlFile_valid(tmp_path):
    sPath = str(tmp_path / "valid.yml")
    fnWriteYaml({"sName": "test"}, sPath)
    dictResult = fdictLoadYamlFile(sPath)
    assert dictResult["sName"] == "test"


# -----------------------------------------------------------------------
# commandConfig: help text
# -----------------------------------------------------------------------


def test_config_help_shows_subcommands():
    runner = CliRunner()
    result = runner.invoke(config, ["--help"])
    assert result.exit_code == 0
    assert "export" in result.output
    assert "edit" in result.output


# -----------------------------------------------------------------------
# commandConfig: export
# -----------------------------------------------------------------------


@patch("vaibify.cli.commandConfig.fconfigLoad")
@patch("vaibify.config.projectConfig.fnSaveToFile")
def test_config_export_calls_save(mockSave, mockLoad):
    mockLoad.return_value = SimpleNamespace(sProjectName="proj")
    runner = CliRunner()
    result = runner.invoke(config, ["export", "/tmp/out.yml"])
    assert result.exit_code == 0
    assert "exported" in result.output.lower()


# -----------------------------------------------------------------------
# commandConfig: import
# -----------------------------------------------------------------------


def test_config_import_with_valid_file(tmp_path):
    sInputPath = str(tmp_path / "input.yml")
    fnWriteYaml({"sKey": "val"}, sInputPath)
    sConfigPath = str(tmp_path / "vaibify.yml")
    with patch(
        "vaibify.cli.commandConfig.fsConfigPath",
        return_value=sConfigPath,
    ):
        runner = CliRunner()
        result = runner.invoke(
            config, ["import", sInputPath], input="y\n")
        assert result.exit_code == 0


# -----------------------------------------------------------------------
# commandConfig: edit
# -----------------------------------------------------------------------


def test_config_edit_missing_file_exits():
    with patch(
        "vaibify.cli.commandConfig.fsConfigPath",
        return_value="/nonexistent_xyz.yml",
    ):
        runner = CliRunner()
        result = runner.invoke(config, ["edit"])
        assert result.exit_code != 0


@patch("click.edit")
def test_config_edit_opens_editor(mockEdit, tmp_path):
    sConfigPath = str(tmp_path / "vaibify.yml")
    fnWriteYaml({"sName": "test"}, sConfigPath)
    with patch(
        "vaibify.cli.commandConfig.fsConfigPath",
        return_value=sConfigPath,
    ):
        runner = CliRunner()
        result = runner.invoke(config, ["edit"])
        assert result.exit_code == 0
        mockEdit.assert_called_once()


# -----------------------------------------------------------------------
# commandDestroy: fnRequireDocker
# -----------------------------------------------------------------------


@patch(
    "vaibify.cli.commandDestroy.fbDockerAvailable",
    return_value=False,
)
def test_fnRequireDocker_exits_when_unavailable(mockAvail):
    with pytest.raises(SystemExit):
        fnRequireDocker()


@patch(
    "vaibify.cli.commandDestroy.fbDockerAvailable",
    return_value=True,
)
def test_fnRequireDocker_passes_when_available(mockAvail):
    fnRequireDocker()


# -----------------------------------------------------------------------
# commandDestroy: help text
# -----------------------------------------------------------------------


def test_destroy_help_text():
    runner = CliRunner()
    result = runner.invoke(destroy, ["--help"])
    assert result.exit_code == 0
    assert "Remove" in result.output


# -----------------------------------------------------------------------
# commandInit: fnPrintAvailableTemplates
# -----------------------------------------------------------------------


def test_fnPrintAvailableTemplates_no_crash(capsys):
    fnPrintAvailableTemplates()
    sCaptured = capsys.readouterr().out
    assert isinstance(sCaptured, str)


# -----------------------------------------------------------------------
# commandInit: fnCopyDirectoryContents
# -----------------------------------------------------------------------


def test_fnCopyDirectoryContents_copies_files(tmp_path):
    sSource = str(tmp_path / "source")
    sDest = str(tmp_path / "dest")
    os.makedirs(sSource)
    os.makedirs(sDest)
    with open(os.path.join(sSource, "file.txt"), "w") as fh:
        fh.write("content")
    fnCopyDirectoryContents(sSource, sDest)
    assert os.path.isfile(os.path.join(sDest, "file.txt"))


def test_fnCopyDirectoryContents_copies_subdirs(tmp_path):
    sSource = str(tmp_path / "source")
    sDest = str(tmp_path / "dest")
    os.makedirs(os.path.join(sSource, "sub"))
    os.makedirs(sDest)
    with open(os.path.join(sSource, "sub", "a.txt"), "w") as fh:
        fh.write("hello")
    fnCopyDirectoryContents(sSource, sDest)
    assert os.path.isfile(os.path.join(sDest, "sub", "a.txt"))


# -----------------------------------------------------------------------
# commandInit: init help and --template=None
# -----------------------------------------------------------------------


def test_init_help_text():
    runner = CliRunner()
    result = runner.invoke(init, ["--help"])
    assert result.exit_code == 0
    assert "Initialize" in result.output


def test_init_no_template_lists_available():
    runner = CliRunner()
    result = runner.invoke(init, [])
    assert result.exit_code == 0


# -----------------------------------------------------------------------
# commandInit: init with --force
# -----------------------------------------------------------------------


@patch("vaibify.cli.commandInit.fnCopyTemplate")
@patch("vaibify.cli.commandInit.fnWriteDefaultConfig")
@patch("vaibify.cli.commandInit.fbConfigExists", return_value=True)
def test_init_existing_config_no_force_exits(
    mockExists, mockWrite, mockCopy,
):
    runner = CliRunner()
    result = runner.invoke(init, ["--template", "general"])
    assert result.exit_code != 0


@patch("vaibify.cli.commandInit.fnCopyTemplate")
@patch("vaibify.cli.commandInit.fnWriteDefaultConfig")
@patch("vaibify.cli.commandInit.fbConfigExists", return_value=True)
def test_init_existing_config_force_succeeds(
    mockExists, mockWrite, mockCopy,
):
    runner = CliRunner()
    result = runner.invoke(
        init, ["--template", "general", "--force"])
    assert result.exit_code == 0
    assert "Initialized" in result.output


# -----------------------------------------------------------------------
# commandStart: help text
# -----------------------------------------------------------------------


def test_start_help_text():
    runner = CliRunner()
    result = runner.invoke(start, ["--help"])
    assert result.exit_code == 0
    assert "Start" in result.output


# -----------------------------------------------------------------------
# commandStatus: help text
# -----------------------------------------------------------------------


def test_status_help_text():
    runner = CliRunner()
    result = runner.invoke(status, ["--help"])
    assert result.exit_code == 0
    assert "status" in result.output.lower()


# -----------------------------------------------------------------------
# commandPublish: help text and subcommands
# -----------------------------------------------------------------------


def test_publish_help_text():
    runner = CliRunner()
    result = runner.invoke(publish, ["--help"])
    assert result.exit_code == 0
    assert "archive" in result.output
    assert "workflow" in result.output


def test_publish_archive_stub():
    runner = CliRunner()
    result = runner.invoke(publish, ["archive"])
    assert result.exit_code == 0
    assert "Not yet implemented" in result.output


def test_publish_workflow_stub():
    runner = CliRunner()
    result = runner.invoke(publish, ["workflow"])
    assert result.exit_code == 0
    assert "Not yet implemented" in result.output
