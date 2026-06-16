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


def test_dockerfile_claude_uses_native_installer():
    """Dockerfile.claude must use Anthropic's native installer, not
    the deprecated npm install path. The native installer drops a
    self-contained binary under the container user's home directory,
    sidestepping the npm-prefix / root-owned-/usr/lib/node_modules
    bug that prevented in-container auto-updates."""
    sContent = _fsReadDockerfileClaude()
    assert "claude.ai/install.sh" in sContent, (
        "Dockerfile.claude must download the native installer from "
        "claude.ai/install.sh"
    )
    assert "@anthropic-ai/claude-code" not in sContent, (
        "Dockerfile.claude must not install Claude via npm; the npm "
        "path produces an install whose auto-update fails because "
        "npm's runtime prefix isn't user-writable. Use the native "
        "installer instead."
    )
    assert "nodesource" not in sContent.lower(), (
        "Dockerfile.claude must not pull in Node.js — the native "
        "Claude installer is self-contained and removes the Node "
        "dependency from the overlay."
    )


def test_dockerfile_claude_installs_as_container_user():
    """The native installer must run as ${CONTAINER_USER} so the
    install ends up under their home directory and they own every
    file. Running the installer as root would put it under /root
    and break auto-update for the unprivileged runtime user — the
    exact failure mode the rewrite fixes."""
    sContent = _fsReadDockerfileClaude()
    iInstallerPos = sContent.find("claude.ai/install.sh")
    assert iInstallerPos != -1
    sPreceding = sContent[:iInstallerPos]
    iLastUserRoot = sPreceding.rfind("USER root")
    iLastUserContainer = sPreceding.rfind("USER ${CONTAINER_USER}")
    assert iLastUserContainer > iLastUserRoot, (
        "The most recent USER directive before the installer must be "
        "USER ${CONTAINER_USER}; otherwise the install runs as root "
        "and lands outside the container user's home."
    )


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
