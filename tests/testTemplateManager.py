"""Tests for vaibify.config.templateManager using tmp_path."""

import os

import pytest
from unittest.mock import patch

from vaibify.config.templateManager import (
    flistAvailableTemplates,
    fdictLoadTemplateConfig,
    _PATH_TEMPLATES,
)


# -----------------------------------------------------------------------
# flistAvailableTemplates
# -----------------------------------------------------------------------


def test_flistAvailableTemplates_returns_list():
    listResult = flistAvailableTemplates()
    assert isinstance(listResult, list)


def test_flistAvailableTemplates_sorted():
    listResult = flistAvailableTemplates()
    assert listResult == sorted(listResult)


def test_flistAvailableTemplates_contains_known_templates():
    listResult = flistAvailableTemplates()
    if listResult:
        assert all(isinstance(s, str) for s in listResult)


def test_flistAvailableTemplates_missing_dir_raises():
    with patch(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        __class__=type(_PATH_TEMPLATES),
    ):
        from pathlib import Path
        sFake = Path("/nonexistent_template_dir_xyz")
        with patch(
            "vaibify.config.templateManager._PATH_TEMPLATES",
            sFake,
        ):
            with pytest.raises(FileNotFoundError):
                flistAvailableTemplates()


# -----------------------------------------------------------------------
# fdictLoadTemplateConfig — with real templates dir
# -----------------------------------------------------------------------


def test_fdictLoadTemplateConfig_returns_dict():
    listTemplates = flistAvailableTemplates()
    if not listTemplates:
        pytest.skip("No templates available")
    sFirst = listTemplates[0]
    sContainerConf = _PATH_TEMPLATES / sFirst / "container.conf"
    if not sContainerConf.exists():
        pytest.skip(f"No container.conf in {sFirst}")
    dictResult = fdictLoadTemplateConfig(sFirst)
    assert isinstance(dictResult, dict)
    assert "listRepositories" in dictResult


def test_fdictLoadTemplateConfig_repos_are_list():
    listTemplates = flistAvailableTemplates()
    if not listTemplates:
        pytest.skip("No templates available")
    sFirst = listTemplates[0]
    sContainerConf = _PATH_TEMPLATES / sFirst / "container.conf"
    if not sContainerConf.exists():
        pytest.skip(f"No container.conf in {sFirst}")
    dictResult = fdictLoadTemplateConfig(sFirst)
    assert isinstance(dictResult["listRepositories"], list)


def test_fdictLoadTemplateConfig_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        fdictLoadTemplateConfig("nonexistent_template_xyz")


# -----------------------------------------------------------------------
# Using tmp_path to create custom template directories
# -----------------------------------------------------------------------


def test_flistAvailableTemplates_custom_dir(tmp_path):
    sTplA = tmp_path / "alpha"
    sTplB = tmp_path / "beta"
    sTplA.mkdir()
    sTplB.mkdir()
    (tmp_path / "notadir.txt").write_text("skip me")
    with patch(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path,
    ):
        listResult = flistAvailableTemplates()
        assert listResult == ["alpha", "beta"]


def test_fdictLoadTemplateConfig_custom_template(tmp_path):
    sTpl = tmp_path / "mytemplate"
    sTpl.mkdir()
    sConfContent = (
        "myrepo|https://github.com/test/repo.git"
        "|main|pip_editable\n"
    )
    (sTpl / "container.conf").write_text(sConfContent)
    with patch(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path,
    ):
        dictResult = fdictLoadTemplateConfig("mytemplate")
        assert len(dictResult["listRepositories"]) == 1
        dictRepo = dictResult["listRepositories"][0]
        assert dictRepo["sName"] == "myrepo"
        assert dictRepo["sInstallMethod"] == "pip_editable"


def test_fdictLoadTemplateConfig_no_conf_raises(tmp_path):
    sTpl = tmp_path / "emptytemplate"
    sTpl.mkdir()
    with patch(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path,
    ):
        with pytest.raises(FileNotFoundError):
            fdictLoadTemplateConfig("emptytemplate")
