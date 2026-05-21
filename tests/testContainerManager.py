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
    features = SimpleNamespace(
        bGpu=False, bClaude=False, bClaudeAutoUpdate=True,
    )
    return SimpleNamespace(
        sProjectName="testproj",
        sWorkspaceRoot="/workspace",
        sContainerUser="researcher",
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
    assert "--rm" not in saArgs
    assert "-d" in saArgs
    assert "-it" not in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_no_claude_env_when_disabled(mockX11):
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    assert not any(
        "VAIBIFY_CLAUDE_AUTO_UPDATE" in s for s in saArgs
    )


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_claude_auto_update_true(mockX11):
    config = _fConfigMinimal()
    config.features.bClaude = True
    config.features.bClaudeAutoUpdate = True
    saArgs = flistBuildRunArgs(config)
    assert "VAIBIFY_CLAUDE_AUTO_UPDATE=true" in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_claude_auto_update_false(mockX11):
    config = _fConfigMinimal()
    config.features.bClaude = True
    config.features.bClaudeAutoUpdate = False
    saArgs = flistBuildRunArgs(config)
    assert "VAIBIFY_CLAUDE_AUTO_UPDATE=false" in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_mounts_credentials_volume(mockX11):
    """The credentials volume must mount the container keyring data
    dir so Zenodo/GitHub tokens survive ``docker rm`` + ``docker run``
    (which is what the GUI's Restart and Rebuild actions both do)."""
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    sExpectedMount = (
        f"{config.sProjectName}-credentials"
        f":/home/{config.sContainerUser}/.local/share/python_keyring"
    )
    assert sExpectedMount in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_credentials_volume_honours_container_user(mockX11):
    """A non-default sContainerUser routes the mount to that user's home."""
    config = _fConfigMinimal()
    config.sContainerUser = "scientist"
    saArgs = flistBuildRunArgs(config)
    assert any(
        "/home/scientist/.local/share/python_keyring" in sArg
        for sArg in saArgs
    )


# -----------------------------------------------------------------------
# Agent-bridge --add-host wiring
# -----------------------------------------------------------------------


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_adds_host_gateway_when_agent_enabled(mockX11):
    """``--add-host host.docker.internal:host-gateway`` appears only when
    the in-container ``vaibify-do`` agent is actually wired up
    (``features.bClaude`` true and network isolation off).
    """
    config = _fConfigMinimal()
    config.features.bClaude = True
    saArgs = flistBuildRunArgs(config)
    assert "--add-host" in saArgs
    iFlag = saArgs.index("--add-host")
    assert saArgs[iFlag + 1] == "host.docker.internal:host-gateway"


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_omits_host_gateway_by_default(mockX11):
    """Projects without the agent feature get no host-gateway entry."""
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    assert "--add-host" not in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_omits_host_gateway_under_network_isolation(
    mockX11,
):
    """A sealed container does not need the host-gateway entry."""
    config = _fConfigMinimal()
    config.features.bClaude = True
    config.bNetworkIsolation = True
    saArgs = flistBuildRunArgs(config)
    assert "--add-host" not in saArgs


def test_fnAddAgentHostBridge_appends_when_agent_enabled():
    """The helper appends the flag + value pair when bClaude is on."""
    from vaibify.docker.containerManager import _fnAddAgentHostBridge
    config = _fConfigMinimal()
    config.features.bClaude = True
    saArgs = ["--rm"]
    _fnAddAgentHostBridge(config, saArgs)
    assert saArgs == [
        "--rm", "--add-host", "host.docker.internal:host-gateway",
    ]


def test_fnAddAgentHostBridge_no_op_when_agent_disabled():
    """The helper is a no-op when the agent feature is off."""
    from vaibify.docker.containerManager import _fnAddAgentHostBridge
    config = _fConfigMinimal()
    saArgs = ["--rm"]
    _fnAddAgentHostBridge(config, saArgs)
    assert saArgs == ["--rm"]


# -----------------------------------------------------------------------
# Entrypoint user override (security: container default identity is unpriv)
# -----------------------------------------------------------------------


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_runs_entrypoint_as_root(mockX11):
    """``docker run`` must override the image USER so the entrypoint
    can chown the workspace and then drop privileges via gosu."""
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    assert "--user" in saArgs
    iFlag = saArgs.index("--user")
    assert saArgs[iFlag + 1] == "0"
