"""Tests for vaibify.docker.containerManager helper functions."""

from types import SimpleNamespace
from unittest.mock import patch

from vaibify.docker.containerManager import (
    _fnAddCpuAllocation,
    _fdictParseContainerState,
    flistBuildRunArgs,
)


def test_fnAddCpuAllocation_adds():
    saRunArgs = []
    _fnAddCpuAllocation(saRunArgs)
    assert "--cpus" in saRunArgs
    iCpuIndex = saRunArgs.index("--cpus")
    iCpuCount = int(saRunArgs[iCpuIndex + 1])
    assert iCpuCount >= 1


def test_fdictParseContainerState_running():
    dictResult = _fdictParseContainerState("running")
    assert dictResult["bExists"] is True
    assert dictResult["bRunning"] is True
    assert dictResult["sStatus"] == "running"


def test_fdictParseContainerState_exited():
    dictResult = _fdictParseContainerState("exited")
    assert dictResult["bExists"] is True
    assert dictResult["bRunning"] is False


def test_fdictParseContainerState_empty():
    dictResult = _fdictParseContainerState("")
    assert dictResult["bExists"] is False
    assert dictResult["bRunning"] is False
    assert dictResult["sStatus"] == "not found"


def _fConfigMinimal():
    """Return a minimal mock config for flistBuildRunArgs."""
    features = SimpleNamespace(bGpu=False)
    return SimpleNamespace(
        sProjectName="testproj",
        sWorkspaceRoot="/workspace",
        listPorts=[],
        listBindMounts=[],
        listSecrets=[],
        features=features,
        bNetworkIsolation=False,
    )


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_contains_basics(mockX11):
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    assert "--rm" in saArgs
    assert "-it" in saArgs
    assert "--name" in saArgs
    assert "testproj" in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_network_isolation(mockX11):
    config = _fConfigMinimal()
    config.bNetworkIsolation = True
    saArgs = flistBuildRunArgs(config)
    assert "--network" in saArgs
    assert "none" in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_gpu(mockX11):
    config = _fConfigMinimal()
    config.features.bGpu = True
    saArgs = flistBuildRunArgs(config)
    assert "--gpus" in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_detached(mockX11):
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config, bDetached=True)
    assert "--rm" in saArgs
    assert "-d" in saArgs
    assert "-it" not in saArgs
