"""Tests for vaibify.docker.containerManager helper functions."""

from types import SimpleNamespace
from unittest.mock import patch

import os

from vaibify.docker.containerManager import (
    _fnAddCpuAllocation,
    _fnAddMemoryAllocation,
    _fdictParseContainerState,
    flistBuildRunArgs,
)


def test_fnAddCpuAllocation_adds():
    saRunArgs = []
    _fnAddCpuAllocation(_fConfigMinimal(), saRunArgs)
    assert "--cpus" in saRunArgs
    iCpuIndex = saRunArgs.index("--cpus")
    iCpuCount = int(saRunArgs[iCpuIndex + 1])
    assert iCpuCount >= 1


@patch(
    "vaibify.docker.containerManager.os.cpu_count",
    return_value=4,
)
def test_fnAddCpuAllocation_default_is_host_cores_minus_one(
    mockCpuCount,
):
    """The unlimited default must be exactly cores minus one — not
    cores, not half, not a shifted bit pattern."""
    saRunArgs = []
    _fnAddCpuAllocation(_fConfigMinimal(), saRunArgs)
    assert saRunArgs == ["--cpus", "3"]


@patch(
    "vaibify.docker.containerManager.os.cpu_count",
    return_value=2,
)
def test_fnAddCpuAllocation_two_core_host_defaults_to_one(
    mockCpuCount,
):
    saRunArgs = []
    _fnAddCpuAllocation(_fConfigMinimal(), saRunArgs)
    assert saRunArgs == ["--cpus", "1"]


@patch(
    "vaibify.docker.containerManager.os.cpu_count",
    return_value=1,
)
def test_fnAddCpuAllocation_single_core_host_never_asks_for_zero(
    mockCpuCount,
):
    saRunArgs = []
    _fnAddCpuAllocation(_fConfigMinimal(), saRunArgs)
    assert saRunArgs == ["--cpus", "1"]


@patch(
    "vaibify.docker.containerManager.os.cpu_count",
    return_value=None,
)
def test_fnAddCpuAllocation_unknown_host_assumes_two_cores(
    mockCpuCount,
):
    saRunArgs = []
    _fnAddCpuAllocation(_fConfigMinimal(), saRunArgs)
    assert saRunArgs == ["--cpus", "1"]


@patch(
    "vaibify.docker.containerManager.os.cpu_count",
    return_value=None,
)
def test_fnAddCpuAllocation_unknown_host_still_clamps_config_limit(
    mockCpuCount,
):
    """With an unknown core count the assumed host is 2 cores, and a
    configured limit must clamp against that assumption too."""
    config = _fConfigMinimal()
    config.iCpuLimit = 3
    saRunArgs = []
    _fnAddCpuAllocation(config, saRunArgs)
    assert saRunArgs == ["--cpus", "2"]


@patch(
    "vaibify.docker.containerManager.os.cpu_count",
    return_value=4,
)
def test_fnAddCpuAllocation_negative_config_falls_back_to_default(
    mockCpuCount,
):
    """A negative iCpuLimit (possible via a hand-edited config object)
    must select the default path, never reach docker as --cpus -3."""
    config = _fConfigMinimal()
    config.iCpuLimit = -3
    saRunArgs = []
    _fnAddCpuAllocation(config, saRunArgs)
    assert saRunArgs == ["--cpus", "3"]


def test_fnAddMemoryAllocation_negative_config_adds_no_flag():
    config = _fConfigMinimal()
    config.fMemoryLimitGigabytes = -2.0
    saRunArgs = []
    _fnAddMemoryAllocation(config, saRunArgs)
    assert saRunArgs == []


def test_fnAddCpuAllocation_honours_config_limit():
    config = _fConfigMinimal()
    config.iCpuLimit = 1
    saRunArgs = []
    _fnAddCpuAllocation(config, saRunArgs)
    iCpuIndex = saRunArgs.index("--cpus")
    assert saRunArgs[iCpuIndex + 1] == "1"


def test_fnAddCpuAllocation_clamps_limit_to_host_cores():
    config = _fConfigMinimal()
    config.iCpuLimit = 100000
    saRunArgs = []
    _fnAddCpuAllocation(config, saRunArgs)
    iCpuIndex = saRunArgs.index("--cpus")
    assert int(saRunArgs[iCpuIndex + 1]) <= (os.cpu_count() or 2)


def test_fnAddMemoryAllocation_omitted_by_default():
    saRunArgs = []
    _fnAddMemoryAllocation(_fConfigMinimal(), saRunArgs)
    assert saRunArgs == []


def test_fnAddMemoryAllocation_formats_whole_and_fractional():
    config = _fConfigMinimal()
    config.fMemoryLimitGigabytes = 1.0
    saRunArgs = []
    _fnAddMemoryAllocation(config, saRunArgs)
    assert saRunArgs == ["--memory", "1g"]
    config.fMemoryLimitGigabytes = 1.5
    saRunArgs = []
    _fnAddMemoryAllocation(config, saRunArgs)
    assert saRunArgs == ["--memory", "1.5g"]


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
def test_flistBuildRunArgs_drops_all_capabilities(mockX11):
    """Audit M3: --cap-drop=ALL is the default for every workflow."""
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    assert "--cap-drop" in saArgs
    iFlag = saArgs.index("--cap-drop")
    assert saArgs[iFlag + 1] == "ALL"


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_disables_privilege_escalation(mockX11):
    """Audit M3: --security-opt=no-new-privileges is the default."""
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    assert "--security-opt" in saArgs
    iFlag = saArgs.index("--security-opt")
    assert saArgs[iFlag + 1] == "no-new-privileges"


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_readds_minimum_entrypoint_capabilities(mockX11):
    """Audit M3 follow-up: --cap-drop=ALL must be paired with the five
    capabilities the entrypoint needs to chown + gosu, otherwise the
    container fails to start before any agent code runs."""
    config = _fConfigMinimal()
    saArgs = flistBuildRunArgs(config)
    setReAdded = set()
    for iIndex, sArg in enumerate(saArgs):
        if sArg == "--cap-add" and iIndex + 1 < len(saArgs):
            setReAdded.add(saArgs[iIndex + 1])
    assert {"CHOWN", "FOWNER", "DAC_OVERRIDE", "SETUID", "SETGID"} <= setReAdded


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
