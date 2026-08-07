"""Tests for vaibify.cli.configLoader path helpers."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.cli.configLoader import (
    fsConfigPath,
    fsDockerDir,
    fnSetConfigPath,
    fconfigResolveProject,
)


def test_fsConfigPath_default():
    fnSetConfigPath(None)
    sPath = fsConfigPath()
    assert sPath.endswith("vaibify.yml")
    assert os.path.isabs(sPath)


def test_fsConfigPath_override():
    fnSetConfigPath("/tmp/custom.yml")
    sPath = fsConfigPath()
    assert sPath.endswith("/custom.yml")
    fnSetConfigPath(None)


def test_fsDockerDir_exists():
    sDockerDir = fsDockerDir()
    assert sDockerDir.endswith("containerImage")
    assert os.path.isabs(sDockerDir)


def test_fsDockerDir_is_directory():
    sDockerDir = fsDockerDir()
    assert os.path.isdir(sDockerDir)


# -----------------------------------------------------------------------
# fconfigResolveProject
# -----------------------------------------------------------------------


_MOCK_CONFIG = SimpleNamespace(
    sProjectName="alpha",
    sContainerUser="researcher",
)


@patch("vaibify.cli.configLoader._fconfigParse")
@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_by_name_found(mockRegistry, mockParse):
    mockRegistry.return_value = {
        "listProjects": [
            {"sName": "alpha", "sConfigPath": "/a/vaibify.yml"},
        ],
    }
    mockParse.return_value = _MOCK_CONFIG
    configResult = fconfigResolveProject("alpha")
    assert configResult.sProjectName == "alpha"
    mockParse.assert_called_once_with("/a/vaibify.yml")


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_by_name_not_found_exits(mockRegistry):
    mockRegistry.return_value = {
        "listProjects": [
            {"sName": "beta", "sConfigPath": "/b/vaibify.yml"},
        ],
    }
    with pytest.raises(SystemExit):
        fconfigResolveProject("missing")


@patch("vaibify.cli.configLoader._fconfigParse")
def test_resolve_local_vaibify_yml(mockParse, tmp_path, monkeypatch):
    sConfigFile = tmp_path / "vaibify.yml"
    sConfigFile.write_text("projectName: local\n")
    monkeypatch.chdir(tmp_path)
    mockParse.return_value = _MOCK_CONFIG
    configResult = fconfigResolveProject(None)
    assert configResult.sProjectName == "alpha"
    mockParse.assert_called_once_with(str(sConfigFile))


@patch("vaibify.cli.configLoader._fconfigParse")
@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_single_registry_entry(
    mockRegistry, mockParse, tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    mockRegistry.return_value = {
        "listProjects": [
            {"sName": "only", "sConfigPath": "/o/vaibify.yml"},
        ],
    }
    mockParse.return_value = _MOCK_CONFIG
    configResult = fconfigResolveProject(None)
    mockParse.assert_called_once_with("/o/vaibify.yml")


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_no_projects_exits(
    mockRegistry, tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    mockRegistry.return_value = {"listProjects": []}
    with pytest.raises(SystemExit):
        fconfigResolveProject(None)


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_multiple_projects_exits(
    mockRegistry, tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    mockRegistry.return_value = {
        "listProjects": [
            {"sName": "a", "sConfigPath": "/a/vaibify.yml"},
            {"sName": "b", "sConfigPath": "/b/vaibify.yml"},
        ],
    }
    with pytest.raises(SystemExit):
        fconfigResolveProject(None)


# --- fsResolveProjectConfigPath (mirrors fconfigResolveProject) ---

@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_config_path_by_name_found(mockRegistry):
    from vaibify.cli.configLoader import fsResolveProjectConfigPath
    mockRegistry.return_value = {"listProjects": [
        {"sName": "proj", "sConfigPath": "/p/proj/vaibify.yml"},
    ]}
    assert fsResolveProjectConfigPath("proj") == "/p/proj/vaibify.yml"


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_config_path_by_name_missing_exits(mockRegistry):
    from vaibify.cli.configLoader import fsResolveProjectConfigPath
    mockRegistry.return_value = {"listProjects": [
        {"sName": "other", "sConfigPath": "/p/other/vaibify.yml"},
    ]}
    with pytest.raises(SystemExit):
        fsResolveProjectConfigPath("ghost")


def test_resolve_config_path_prefers_local_file(tmp_path, monkeypatch):
    from vaibify.cli.configLoader import fsResolveProjectConfigPath
    (tmp_path / "vaibify.yml").write_text("projectName: local\n")
    monkeypatch.chdir(tmp_path)
    sPath = fsResolveProjectConfigPath()
    assert sPath.endswith("vaibify.yml")
    assert str(tmp_path) in sPath


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_config_path_single_project(mockRegistry, tmp_path,
                                            monkeypatch):
    from vaibify.cli.configLoader import fsResolveProjectConfigPath
    monkeypatch.chdir(tmp_path)  # no local vaibify.yml
    mockRegistry.return_value = {"listProjects": [
        {"sName": "only", "sConfigPath": "/p/only/vaibify.yml"},
    ]}
    assert fsResolveProjectConfigPath() == "/p/only/vaibify.yml"


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_config_path_zero_projects_exits(mockRegistry, tmp_path,
                                                 monkeypatch):
    from vaibify.cli.configLoader import fsResolveProjectConfigPath
    monkeypatch.chdir(tmp_path)
    mockRegistry.return_value = {"listProjects": []}
    with pytest.raises(SystemExit):
        fsResolveProjectConfigPath()


@patch("vaibify.config.registryManager.fdictLoadRegistry")
def test_resolve_config_path_multiple_projects_exits(mockRegistry,
                                                     tmp_path, monkeypatch):
    from vaibify.cli.configLoader import fsResolveProjectConfigPath
    monkeypatch.chdir(tmp_path)
    mockRegistry.return_value = {"listProjects": [
        {"sName": "a", "sConfigPath": "/p/a/vaibify.yml"},
        {"sName": "b", "sConfigPath": "/p/b/vaibify.yml"},
    ]}
    with pytest.raises(SystemExit):
        fsResolveProjectConfigPath()


# ---------------------------------------------------------------------
# ``--config PATH`` must SELECT the project, not merely be accepted.
# ---------------------------------------------------------------------

def test_an_explicit_config_path_outranks_the_working_directory(
    tmp_path, monkeypatch,
):
    """The top-level ``--config`` option decides which project is acted on.

    It used to be accepted, recorded, and then ignored: every project
    command resolved through the working directory, so
    ``vaibify --config /elsewhere/vaibify.yml start`` started whichever
    project the CURRENT directory found -- and ``stop`` stopped it.
    Naming one container and acting on another is the worst answer
    available, because nothing on screen says so. Found while running
    the container-acceptance lane, which could not start its own
    project on a machine that had any other.
    """
    from vaibify.cli import configLoader

    sDirectoryElsewhere = tmp_path / "elsewhere"
    sDirectoryElsewhere.mkdir()
    (sDirectoryElsewhere / "vaibify.yml").write_text(
        "projectName: the-explicit-one\n",
    )
    sDirectoryHere = tmp_path / "here"
    sDirectoryHere.mkdir()
    (sDirectoryHere / "vaibify.yml").write_text(
        "projectName: the-discovered-one\n",
    )
    monkeypatch.chdir(sDirectoryHere)

    configDiscovered = configLoader.fconfigResolveProject()
    assert configDiscovered.sProjectName == "the-discovered-one"

    try:
        configLoader.fnSetConfigPath(
            str(sDirectoryElsewhere / "vaibify.yml"),
        )
        configExplicit = configLoader.fconfigResolveProject()
        assert configExplicit.sProjectName == "the-explicit-one", (
            "--config was ignored: the command would act on the "
            "working directory's project instead of the named one"
        )
        assert configLoader.fsResolveProjectConfigPath() == str(
            sDirectoryElsewhere / "vaibify.yml",
        ), (
            "the path a command writes back to must be the one it read"
        )
    finally:
        configLoader.fnSetConfigPath(None)


def test_an_explicit_project_name_still_outranks_the_config_path(
    tmp_path, monkeypatch,
):
    """``--project`` is the more specific instruction and keeps priority."""
    from vaibify.cli import configLoader

    sDirectoryElsewhere = tmp_path / "elsewhere"
    sDirectoryElsewhere.mkdir()
    (sDirectoryElsewhere / "vaibify.yml").write_text(
        "projectName: the-explicit-one\n",
    )
    monkeypatch.chdir(tmp_path)
    listLookups = []

    def _fconfigFromRegistry(sProjectName):
        listLookups.append(sProjectName)
        return SimpleNamespace(sProjectName=sProjectName)

    monkeypatch.setattr(
        configLoader, "_fconfigLoadFromRegistry", _fconfigFromRegistry,
    )
    try:
        configLoader.fnSetConfigPath(
            str(sDirectoryElsewhere / "vaibify.yml"),
        )
        configNamed = configLoader.fconfigResolveProject("named-project")
        assert configNamed.sProjectName == "named-project"
        assert listLookups == ["named-project"]
    finally:
        configLoader.fnSetConfigPath(None)
