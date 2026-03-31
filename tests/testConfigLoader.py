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
    assert sDockerDir.endswith("docker")
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
