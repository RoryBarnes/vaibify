"""Tests for untested functions in vaibify.config.projectConfig."""

import os
import tempfile

import pytest

from vaibify.config.projectConfig import (
    fdictLoadDefaults,
    fbValidateConfig,
    fconfigLoadFromFile,
    fnSaveToFile,
    ProjectConfig,
    FeaturesConfig,
)


def test_fdictLoadDefaults_has_keys():
    dictDefaults = fdictLoadDefaults()
    assert "projectName" in dictDefaults
    assert "features" in dictDefaults
    assert "pythonVersion" in dictDefaults


def test_fdictLoadDefaults_package_manager():
    dictDefaults = fdictLoadDefaults()
    assert dictDefaults["packageManager"] == "pip"


def test_fbValidateConfig_valid():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "testproj"
    assert fbValidateConfig(dictConfig) is True


def test_fbValidateConfig_missing_name():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = ""
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_rejects_metacharacter_names():
    dictConfig = fdictLoadDefaults()
    for sBadName in [
        "proj;rm -rf /",
        "../escape",
        "name with spaces",
        "name$(whoami)",
        "-leadingdash",
        ".leadingdot",
        "_leadingunder",
        "x" * 64,
    ]:
        dictConfig["projectName"] = sBadName
        assert fbValidateConfig(dictConfig) is False, sBadName


def test_fbValidateConfig_accepts_well_formed_names():
    dictConfig = fdictLoadDefaults()
    for sGoodName in [
        "myproj",
        "MyProj",
        "proj-1",
        "proj_1",
        "proj.1",
        "Project123",
        "x",
        "x" * 63,
    ]:
        dictConfig["projectName"] = sGoodName
        assert fbValidateConfig(dictConfig) is True, sGoodName


def test_fbValidateConfig_bad_manager():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["packageManager"] = "yarn"
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_not_dict():
    assert fbValidateConfig("not a dict") is False


def test_fbValidateConfig_bad_list():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["repositories"] = "not a list"
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_bad_features():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["features"] = {"jupyter": "yes"}
    assert fbValidateConfig(dictConfig) is False


def test_fnSaveToFile_roundtrip():
    config = ProjectConfig(sProjectName="roundtrip")
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        assert os.path.isfile(sPath)
        configLoaded = fconfigLoadFromFile(sPath)
        assert configLoaded.sProjectName == "roundtrip"


def test_fnSaveToFile_roundtrip_full():
    listRepositories = [{
        "name": "foo",
        "url": "https://github.com/example/foo.git",
        "branch": "main",
        "installMethod": "pip_editable",
    }]
    config = ProjectConfig(
        sProjectName="fullproj",
        listRepositories=listRepositories,
        bNeverSleep=True,
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.listRepositories == listRepositories
    assert configLoaded.bNeverSleep is True


def test_fconfigLoadFromFile_missing():
    with pytest.raises(FileNotFoundError):
        fconfigLoadFromFile("/nonexistent/vaibify.yml")


def test_fconfigLoadFromFile_features():
    config = ProjectConfig(
        sProjectName="feat",
        features=FeaturesConfig(bJupyter=True),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
        assert configLoaded.features.bJupyter is True
        assert configLoaded.features.bGpu is False


def test_claude_auto_update_default_true():
    config = ProjectConfig(sProjectName="claudedefault")
    assert config.features.bClaudeAutoUpdate is True


def test_claude_auto_update_yaml_roundtrip_true():
    config = ProjectConfig(
        sProjectName="claudeon",
        features=FeaturesConfig(
            bClaude=True, bClaudeAutoUpdate=True,
        ),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.features.bClaude is True
    assert configLoaded.features.bClaudeAutoUpdate is True


def test_claude_auto_update_yaml_roundtrip_false():
    config = ProjectConfig(
        sProjectName="claudeoff",
        features=FeaturesConfig(
            bClaude=True, bClaudeAutoUpdate=False,
        ),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.features.bClaudeAutoUpdate is False


def test_claude_auto_update_missing_key_defaults_true():
    import yaml
    dictConfig = {
        "projectName": "legacy",
        "features": {"claude": True},
    }
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        with open(sPath, "w") as fileHandle:
            yaml.safe_dump(dictConfig, fileHandle)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.features.bClaudeAutoUpdate is True
