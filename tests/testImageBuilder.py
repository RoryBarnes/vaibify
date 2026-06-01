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


def test_dockerfile_claude_is_unpinned():
    """Ensure Dockerfile.claude installs @latest and not the old pin."""
    import os
    sPath = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "docker",
            "Dockerfile.claude",
        )
    )
    with open(sPath) as fileHandle:
        sContent = fileHandle.read()
    assert "@anthropic-ai/claude-code@latest" in sContent
    assert "2.1.104" not in sContent


def _fsReadDockerfileClaude():
    import os
    sPath = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "docker",
            "Dockerfile.claude",
        )
    )
    with open(sPath) as fileHandle:
        return fileHandle.read()


def test_dockerfile_claude_separates_nodejs_install_from_setup():
    """The nodejs install must be its own RUN so an apt failure
    cannot wear the NodeSource-download failure diagnostic. The
    previous shape bundled both in one RUN and a disk-full apt error
    surfaced the network-trouble message, sending the user hunting
    the wrong root cause."""
    sContent = _fsReadDockerfileClaude()
    iCurlPos = sContent.find("deb.nodesource.com/setup_20.x")
    iInstallPos = sContent.find("apt-get install -y --no-install-recommends nodejs")
    assert iCurlPos != -1, "NodeSource setup script reference missing"
    assert iInstallPos != -1, "nodejs install line missing"
    sBetween = sContent[iCurlPos:iInstallPos]
    assert "\nRUN " in sBetween, (
        "nodejs install must live in its own RUN so its failure "
        "diagnostic is not masked by the NodeSource curl-failure "
        "message above it."
    )


def test_dockerfile_claude_nodejs_install_mentions_disk_space():
    """The nodejs install's failure diagnostic must mention disk
    space as the primary cause and name the prune commands. In
    practice the apt failure here is almost always the Docker VM
    running out of disk; without this hint the user is left guessing."""
    sContent = _fsReadDockerfileClaude()
    iInstallPos = sContent.find("apt-get install -y --no-install-recommends nodejs")
    sBlock = sContent[iInstallPos:iInstallPos + 2000]
    assert "out of disk" in sBlock or "disk" in sBlock
    assert "docker builder prune" in sBlock
    assert "docker image prune" in sBlock


def test_fnBuildImage_prunes_dangling_layers_post_build(monkeypatch):
    """A successful build must trigger ``docker image prune -f`` so
    the now-orphaned layers from the prior tag of the same image do
    not accumulate. ``-f`` only (no ``-a``) keeps tagged images,
    volumes, and running containers safe."""
    import vaibify.docker.imageBuilder as imageBuilder
    listCalls = []

    def _fnFakeRun(saCommand, **kwargs):
        listCalls.append(list(saCommand))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr(imageBuilder, "fnBuildBase", lambda *a, **k: None)
    monkeypatch.setattr(imageBuilder, "fnApplyOverlay", lambda *a, **k: None)
    monkeypatch.setattr(imageBuilder, "_fnTagFinalImage", lambda *a, **k: None)
    monkeypatch.setattr(imageBuilder.subprocess, "run", _fnFakeRun)

    imageBuilder.fnBuildImage(MockConfig(), "/dockerdir", bNoCache=False)

    listPruneCalls = [
        cmd for cmd in listCalls
        if cmd[:4] == ["docker", "image", "prune", "-f"]
    ]
    assert len(listPruneCalls) == 1, (
        "post-build prune did not run; orphaned layers will "
        "accumulate every rebuild and eventually fill the VM disk"
    )


def test_fnBuildImage_prune_failure_is_swallowed(monkeypatch):
    """A prune failure (timeout, docker daemon stop) must not cause
    the build call to raise. The build itself already succeeded;
    prune is best-effort."""
    import subprocess as subprocessModule
    import vaibify.docker.imageBuilder as imageBuilder

    def _fnRaiseTimeout(saCommand, **kwargs):
        raise subprocessModule.TimeoutExpired(saCommand, 30)

    monkeypatch.setattr(imageBuilder, "fnBuildBase", lambda *a, **k: None)
    monkeypatch.setattr(imageBuilder, "fnApplyOverlay", lambda *a, **k: None)
    monkeypatch.setattr(imageBuilder, "_fnTagFinalImage", lambda *a, **k: None)
    monkeypatch.setattr(imageBuilder.subprocess, "run", _fnRaiseTimeout)

    imageBuilder.fnBuildImage(MockConfig(), "/dockerdir", bNoCache=False)
