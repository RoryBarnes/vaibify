"""Tests for vaibify.cli.configLoader path helpers."""

import os

from vaibify.cli.configLoader import fsConfigPath, fsDockerDir, fnSetConfigPath


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
