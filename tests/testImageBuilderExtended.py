"""Tests for untested pure functions in vaibify.docker.imageBuilder."""

from types import SimpleNamespace

from vaibify.docker.imageBuilder import (
    flistDetermineOverlays,
    _flistSortByCanonicalOrder,
    _fsResolveBaseImage,
    _flistBuildArgPairs,
    _fsResolveOverlayDockerfile,
    _flistOverlayCommand,
)

import pytest


def _fConfigWithFeatures(**kwargs):
    """Return a mock config with specified features."""
    dictDefaults = dict(
        bJupyter=False, bRLanguage=False, bJulia=False,
        bDatabase=False, bDvc=False, bLatex=True,
        bClaude=False, bGpu=False,
    )
    dictDefaults.update(kwargs)
    features = SimpleNamespace(**dictDefaults)
    return SimpleNamespace(
        sProjectName="proj",
        sBaseImage="ubuntu:24.04",
        sPythonVersion="3.12",
        sContainerUser="researcher",
        sWorkspaceRoot="/workspace",
        sPackageManager="pip",
        features=features,
    )


def test_flistDetermineOverlays_none():
    config = _fConfigWithFeatures()
    assert flistDetermineOverlays(config) == []


def test_flistDetermineOverlays_jupyter():
    config = _fConfigWithFeatures(bJupyter=True)
    listOverlays = flistDetermineOverlays(config)
    assert "jupyter" in listOverlays


def test_flistDetermineOverlays_order():
    config = _fConfigWithFeatures(bJupyter=True, bGpu=True)
    listOverlays = flistDetermineOverlays(config)
    assert listOverlays.index("gpu") < listOverlays.index("jupyter")


def test_flistSortByCanonicalOrder_filters():
    listResult = _flistSortByCanonicalOrder(["julia", "gpu"])
    assert listResult == ["gpu", "julia"]


def test_flistSortByCanonicalOrder_empty():
    assert _flistSortByCanonicalOrder([]) == []


def test_fsResolveBaseImage_no_gpu():
    config = _fConfigWithFeatures()
    assert _fsResolveBaseImage(config) == "ubuntu:24.04"


def test_fsResolveBaseImage_gpu():
    config = _fConfigWithFeatures(bGpu=True)
    sImage = _fsResolveBaseImage(config)
    assert "nvidia" in sImage


def test_flistBuildArgPairs_format():
    config = _fConfigWithFeatures()
    listPairs = _flistBuildArgPairs(config, "ubuntu:24.04")
    assert "--build-arg" in listPairs
    assert any("BASE_IMAGE=ubuntu:24.04" in s for s in listPairs)
    assert any("PYTHON_VERSION=3.12" in s for s in listPairs)


def test_fsResolveOverlayDockerfile_valid():
    sPath = _fsResolveOverlayDockerfile("jupyter", "/docker")
    assert sPath == "/docker/Dockerfile.jupyter"


def test_fsResolveOverlayDockerfile_invalid():
    with pytest.raises(ValueError):
        _fsResolveOverlayDockerfile("nonexistent", "/docker")


def test_flistOverlayCommand_structure():
    listCmd = _flistOverlayCommand(
        "/d/Dockerfile.jupyter", "proj:jupyter",
        "proj:base", "/d",
    )
    assert listCmd[0] == "docker"
    assert "build" in listCmd
    assert "-f" in listCmd
    assert "-t" in listCmd
    assert "proj:jupyter" in listCmd
