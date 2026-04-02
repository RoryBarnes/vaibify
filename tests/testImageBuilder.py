"""Tests for vaibify.docker.imageBuilder overlay ordering and build logic."""

from unittest.mock import patch, MagicMock

import pytest

from vaibify.docker.imageBuilder import (
    flistDetermineOverlays,
    fbImageExists,
    fbBuildxAvailable,
    _flistBuildPrefix,
    _LIST_OVERLAY_ORDER,
)
from vaibify.config.projectConfig import FeaturesConfig


class MockFeatures:
    """Minimal stand-in for FeaturesConfig with all flags off."""

    def __init__(self, **kwargs):
        self.bJupyter = kwargs.get("bJupyter", False)
        self.bRLanguage = kwargs.get("bRLanguage", False)
        self.bJulia = kwargs.get("bJulia", False)
        self.bDatabase = kwargs.get("bDatabase", False)
        self.bDvc = kwargs.get("bDvc", False)
        self.bGpu = kwargs.get("bGpu", False)
        self.bClaude = kwargs.get("bClaude", False)
        self.bLatex = kwargs.get("bLatex", False)


class MockConfig:
    """Minimal stand-in for ProjectConfig."""

    def __init__(self, features=None):
        self.features = features if features else MockFeatures()
        self.sProjectName = "testproject"
        self.sBaseImage = "ubuntu:24.04"
        self.sPythonVersion = "3.12"
        self.sContainerUser = "researcher"
        self.sWorkspaceRoot = "/workspace"
        self.sPackageManager = "pip"


def test_flistDetermineOverlays_empty_features():
    configMock = MockConfig(features=MockFeatures())

    listOverlays = flistDetermineOverlays(configMock)

    assert listOverlays == []


def test_flistDetermineOverlays_all_features():
    featuresAll = MockFeatures(
        bGpu=True,
        bJupyter=True,
        bRLanguage=True,
        bJulia=True,
        bDatabase=True,
        bDvc=True,
        bClaude=True,
    )
    configMock = MockConfig(features=featuresAll)

    listOverlays = flistDetermineOverlays(configMock)

    listExpected = [
        "gpu", "jupyter", "rlang", "julia",
        "database", "dvc", "claude",
    ]
    assert listOverlays == listExpected
    assert listOverlays == _LIST_OVERLAY_ORDER


def test_flistDetermineOverlays_partial_features():
    featuresPartial = MockFeatures(
        bJupyter=True,
        bDvc=True,
    )
    configMock = MockConfig(features=featuresPartial)

    listOverlays = flistDetermineOverlays(configMock)

    assert listOverlays == ["jupyter", "dvc"]
    iJupyterIndex = _LIST_OVERLAY_ORDER.index("jupyter")
    iDvcIndex = _LIST_OVERLAY_ORDER.index("dvc")
    assert iJupyterIndex < iDvcIndex


def test_flistDetermineOverlays_gpu_comes_first():
    featuresGpuAndClaude = MockFeatures(
        bGpu=True,
        bClaude=True,
    )
    configMock = MockConfig(features=featuresGpuAndClaude)

    listOverlays = flistDetermineOverlays(configMock)

    assert listOverlays == ["gpu", "claude"]
    assert listOverlays[0] == "gpu"


def test_fbImageExists_returns_false_when_subprocess_fails():
    with patch(
        "vaibify.config.secretManager.subprocess.run"
    ):
        with patch(
            "vaibify.docker.imageBuilder.subprocess.run"
        ) as mockRun:
            mockResult = MagicMock()
            mockResult.returncode = 1
            mockRun.return_value = mockResult

            bExists = fbImageExists("nonexistent:latest")

    assert bExists is False
    mockRun.assert_called_once()
    listCallArgs = mockRun.call_args[0][0]
    assert "docker" in listCallArgs
    assert "image" in listCallArgs
    assert "inspect" in listCallArgs
    assert "nonexistent:latest" in listCallArgs


def test_fbBuildxAvailable_true():
    with patch(
        "vaibify.docker.imageBuilder.subprocess.run"
    ) as mockRun:
        mockRun.return_value = MagicMock(returncode=0)
        assert fbBuildxAvailable() is True


def test_fbBuildxAvailable_false():
    with patch(
        "vaibify.docker.imageBuilder.subprocess.run"
    ) as mockRun:
        mockRun.return_value = MagicMock(returncode=1)
        assert fbBuildxAvailable() is False


def test_flistBuildPrefix_uses_buildx_when_available():
    with patch(
        "vaibify.docker.imageBuilder.fbBuildxAvailable",
        return_value=True,
    ):
        assert _flistBuildPrefix() == [
            "docker", "buildx", "build"]


def test_flistBuildPrefix_falls_back_to_legacy():
    with patch(
        "vaibify.docker.imageBuilder.fbBuildxAvailable",
        return_value=False,
    ):
        assert _flistBuildPrefix() == ["docker", "build"]


def test_fbImageExists_returns_true_when_subprocess_succeeds():
    with patch(
        "vaibify.docker.imageBuilder.subprocess.run"
    ) as mockRun:
        mockResult = MagicMock()
        mockResult.returncode = 0
        mockRun.return_value = mockResult

        bExists = fbImageExists("myproject:latest")

    assert bExists is True
