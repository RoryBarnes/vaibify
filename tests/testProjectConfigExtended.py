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
