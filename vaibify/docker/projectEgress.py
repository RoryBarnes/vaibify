"""A project container's egress boundary in allowlist mode: its network and its proxy.

Each allowlist-mode project gets an INTERNAL network with the bridge's
gateway mode ``isolated`` (Engine 28 and later) and a proxy sidecar on
that network and on the default bridge. ``--internal`` alone is not the
seal: Docker gives an internal network's bridge an address, and a
container on it can then reach every host service that listens on
``0.0.0.0``, including the daemon host's. In ``isolated`` mode the
bridge gets no address and the container no route, so a direct dial to
the host, the VM, the LAN or another container fails before a packet
leaves the machine (measured on Engine 28.4.0: ``ENETUNREACH``). An
engine that does not honor the option, or that still assigns a gateway,
refuses allowlist mode by name; there is no fallback to a gatewayed
network.

The sidecar runs the shared proxy of :mod:`egressProxy` from a
read-only bind mount of the script, as an unprivileged user with every
capability dropped, a read-only root, memory, CPU and PID bounds, and
the host-gateway entry so it can serve the project's hub as the ONE
host destination (pinned to ``host.docker.internal:<hub port>``). The
project's published ports are published on the sidecar and relayed to
the container by name (ruling D3). Everything here is a ``docker``
command on the host; nothing execs into the project container.
"""

__all__ = [
    "EgressSetupError",
    "S_LABEL_KEY",
    "S_NETWORK_PREFIX",
    "S_PROXY_PREFIX",
    "S_PROXY_STATE_ABSENT",
    "S_PROXY_STATE_DOWN",
    "S_PROXY_STATE_RUNNING",
    "fdictDescribeProjectEgress",
    "fdictPrepareProjectEgress",
    "fdictReconcileProjectEgress",
    "flistDiscoverProjectEgressUuids",
    "flistNetworkCreateArguments",
    "flistProxyRunArguments",
    "flistReadRefusedDestinations",
    "fnReleaseProjectEgressQuietly",
    "fsNetworkName",
    "fsProxyName",
    "fsProxyState",
]

import json
import logging
import os
import re
import shutil
import subprocess
import time

from vaibify.config.durableFile import fnWriteFileDurably

from . import egressProxy
from .egressAllowlist import ftComputeAllowlist
from .egressProxy import EgressSetupError

logger = logging.getLogger(__name__)

S_LABEL_KEY = "vaibify-project-egress"
S_NETWORK_PREFIX = "vaibifyProjectEgress"
S_PROXY_PREFIX = "vaibifyProjectProxy"
S_HUB_HOST = "host.docker.internal"
S_ISOLATED_GATEWAY_OPTION = "com.docker.network.bridge.gateway_mode_ipv4=isolated"
S_ENGINE_REQUIREMENT = (
    "allowlist network mode needs a Docker Engine that honors the bridge "
    "option gateway_mode_ipv4=isolated (Engine 28.0.0 or later); this "
    "daemon did not. Update the engine, or set networkMode to open or "
    "isolated."
)
S_PROXY_STATE_RUNNING = "running"
S_PROXY_STATE_DOWN = "down"
S_PROXY_STATE_ABSENT = "absent"
I_PROXY_USER_ID = 65534
S_PROXY_MEMORY = "256m"
S_PROXY_CPUS = "0.5"
I_PROXY_PIDS_LIMIT = 64
F_DOCKER_TIMEOUT_SECONDS = 60.0
I_LOG_TAIL_LINES = 2000
_RE_PROJECT_UUID = re.compile(r"^[0-9a-f]{32}$")


def _fnValidateProjectUuid(sProjectUuid):
    if not _RE_PROJECT_UUID.match(sProjectUuid or ""):
        raise EgressSetupError(f"not a project identity: {sProjectUuid!r}")


def fsNetworkName(sProjectUuid):
    """Return the project's internal network name."""
    _fnValidateProjectUuid(sProjectUuid)
    return f"{S_NETWORK_PREFIX}-{sProjectUuid}"


def fsProxyName(sProjectUuid):
    """Return the project's proxy sidecar name."""
    _fnValidateProjectUuid(sProjectUuid)
    return f"{S_PROXY_PREFIX}-{sProjectUuid}"


def _fsEgressRoot():
    return os.path.join(os.path.expanduser("~"), ".vaibify", "egress")


def _fsWriteProxyScript(sProjectUuid):
    """Write the server-owned proxy script where the sidecar mounts it read-only."""
    sDirectory = os.path.join(_fsEgressRoot(), sProjectUuid)
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    sPath = os.path.join(sDirectory, egressProxy.S_PROXY_SCRIPT_BASENAME)
    fnWriteFileDurably(
        sPath, egressProxy.S_CONNECT_PROXY_SCRIPT.encode("utf-8"), iMode=0o644,
    )
    return sPath


def _fprocessDocker(listArguments, fTimeoutSeconds=F_DOCKER_TIMEOUT_SECONDS):
    """Run one docker command; return its CompletedProcess, never raise on exit code."""
    try:
        return subprocess.run(
            ["docker"] + list(listArguments), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=fTimeoutSeconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EgressSetupError(f"docker {listArguments[0]} did not answer: {error}")


def _fsDockerOrRaise(listArguments, sWhat):
    processResult = _fprocessDocker(listArguments)
    if processResult.returncode != 0:
        raise EgressSetupError(f"{sWhat}: {processResult.stderr.strip()}")
    return processResult.stdout.strip()


def flistNetworkCreateArguments(sNetworkName, sProjectUuid):
    """Return the ``docker network create`` arguments for the sealed network."""
    return [
        "network", "create", "--internal", "--ipv6=false",
        "--opt", S_ISOLATED_GATEWAY_OPTION,
        "--label", f"{S_LABEL_KEY}={sProjectUuid}", sNetworkName,
    ]


def _fbNetworkExists(sNetworkName):
    return _fprocessDocker(["network", "inspect", sNetworkName]).returncode == 0


def _fsReadNetworkFormat(sNetworkName, sFormat):
    return _fsDockerOrRaise(
        ["network", "inspect", "--format", sFormat, sNetworkName],
        f"could not inspect the network {sNetworkName}",
    )


def _flistReadNetworkIpamConfig(sNetworkName):
    """Return the network's IPAM config entries, as the daemon reports them."""
    sJson = _fsReadNetworkFormat(sNetworkName, "{{json .IPAM.Config}}")
    try:
        listConfig = json.loads(sJson or "null")
    except ValueError:
        raise EgressSetupError(f"could not read the IPAM config of {sNetworkName}: {sJson!r}")
    return [dictEntry for dictEntry in (listConfig or []) if isinstance(dictEntry, dict)]


def _fnEnsureSealedNetwork(sNetworkName, sProjectUuid):
    """Create the network if absent; refuse an engine that leaves it a gateway.

    The IPAM config is read as JSON: a Go template's ``.Gateway`` prints
    ``<no value>`` for the key an isolated network lacks, which is the
    very absence this check wants to see.
    """
    if not _fbNetworkExists(sNetworkName):
        processResult = _fprocessDocker(
            flistNetworkCreateArguments(sNetworkName, sProjectUuid))
        if processResult.returncode != 0:
            raise EgressSetupError(
                S_ENGINE_REQUIREMENT + f" (docker said: {processResult.stderr.strip()})")
    bGateway = any(dictEntry.get("Gateway") for dictEntry in _flistReadNetworkIpamConfig(sNetworkName))
    sOption = _fsReadNetworkFormat(
        sNetworkName, '{{index .Options "com.docker.network.bridge.gateway_mode_ipv4"}}')
    if bGateway or sOption.strip() != "isolated":
        _fprocessDocker(["network", "rm", sNetworkName])
        raise EgressSetupError(S_ENGINE_REQUIREMENT)


def _fsReadNetworkSubnet(sNetworkName):
    for dictEntry in _flistReadNetworkIpamConfig(sNetworkName):
        sSubnet = str(dictEntry.get("Subnet") or "")
        if sSubnet and ":" not in sSubnet:
            return sSubnet
    raise EgressSetupError(f"the network {sNetworkName} reports no IPv4 subnet")


def _flistForwards(config):
    """Return ``(iListen, sTargetHost, iTargetPort)`` for every published port."""
    listForwards = []
    for dictPort in getattr(config, "listPorts", []) or []:
        iContainer = int(dictPort.get("container"))
        listForwards.append((
            int(dictPort.get("host", iContainer)), config.sProjectName, iContainer,
        ))
    return listForwards


def flistProxyRunArguments(
    config, sProjectUuid, sNetworkName, sScriptPath, sSubnet, iHubPort,
):
    """Return the ``docker run`` arguments that create the hardened sidecar."""
    listHosts, listPorts = ftComputeAllowlist(config)
    listForwards = _flistForwards(config)
    listArguments = [
        "run", "-d", "--name", fsProxyName(sProjectUuid),
        "--label", f"{S_LABEL_KEY}={sProjectUuid}",
        "--network", sNetworkName,
        "--user", f"{I_PROXY_USER_ID}:{I_PROXY_USER_ID}",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--read-only", "--memory", S_PROXY_MEMORY, "--cpus", S_PROXY_CPUS,
        "--pids-limit", str(I_PROXY_PIDS_LIMIT),
        "--add-host", f"{S_HUB_HOST}:host-gateway",
        "--mount",
        f"type=bind,source={sScriptPath},"
        f"target={egressProxy.S_PROXY_SCRIPT_CONTAINER_PATH},readonly",
    ]
    for iListen, _sTarget, _iTarget in listForwards:
        listArguments.extend(["-p", f"127.0.0.1:{iListen}:{iListen}"])
    listArguments.append(egressProxy.S_PROXY_IMAGE)
    listArguments.extend(egressProxy.flistComposeProxyCommand(
        listHosts, listPorts, {}, sSubnet,
        f"{S_HUB_HOST}:{iHubPort}" if iHubPort else "", listForwards,
    ))
    return listArguments


def _fnAwaitProxyReady(sProxyName):
    fDeadline = time.monotonic() + egressProxy.F_PROXY_READY_DEADLINE_SECONDS
    while time.monotonic() < fDeadline:
        processLogs = _fprocessDocker(["logs", sProxyName])
        if egressProxy.S_PROXY_READY_LINE in (processLogs.stdout + processLogs.stderr):
            return
        sRunning = _fprocessDocker(
            ["inspect", "--format", "{{.State.Running}}", sProxyName]).stdout.strip()
        if sRunning == "false":
            raise EgressSetupError(
                "the network proxy exited on startup: "
                + (processLogs.stderr or processLogs.stdout).strip()[-400:])
        time.sleep(egressProxy.F_PROXY_READY_POLL_SECONDS)
    raise EgressSetupError(
        f"the network proxy never reported {egressProxy.S_PROXY_READY_LINE!r} "
        f"within {egressProxy.F_PROXY_READY_DEADLINE_SECONDS:g} seconds")


def _fsReadProxyAddress(sProxyName, sNetworkName):
    sAddress = _fsDockerOrRaise(
        ["inspect", "--format",
         f'{{{{(index .NetworkSettings.Networks "{sNetworkName}").IPAddress}}}}',
         sProxyName],
        "could not read the network proxy's address",
    )
    if not sAddress:
        raise EgressSetupError(f"the network proxy reports no address on {sNetworkName}")
    return sAddress


def fdictPrepareProjectEgress(config, sProjectUuid, iHubPort):
    """Make the project's sealed network and proxy; return what the launch needs.

    Returns ``{"sNetworkName", "sProxyName", "sProxyAddress"}``. A stale
    sidecar from an earlier start is replaced, because its allowlist
    and hub port may be stale too.
    """
    sNetworkName = fsNetworkName(sProjectUuid)
    sProxyName = fsProxyName(sProjectUuid)
    _fnEnsureSealedNetwork(sNetworkName, sProjectUuid)
    _fprocessDocker(["rm", "-f", sProxyName])
    sScriptPath = _fsWriteProxyScript(sProjectUuid)
    sSubnet = _fsReadNetworkSubnet(sNetworkName)
    _fsDockerOrRaise(
        flistProxyRunArguments(
            config, sProjectUuid, sNetworkName, sScriptPath, sSubnet, iHubPort),
        "could not start the network proxy",
    )
    try:
        _fsDockerOrRaise(["network", "connect", "bridge", sProxyName],
                         "could not attach the network proxy to the bridge")
        _fnAwaitProxyReady(sProxyName)
        return {
            "sNetworkName": sNetworkName, "sProxyName": sProxyName,
            "sProxyAddress": _fsReadProxyAddress(sProxyName, sNetworkName),
        }
    except EgressSetupError:
        _fprocessDocker(["rm", "-f", sProxyName])
        raise


def fnReleaseProjectEgressQuietly(sProjectUuid):
    """Remove the project's proxy, network and script; a failure is logged."""
    if not _RE_PROJECT_UUID.match(sProjectUuid or ""):
        return
    try:
        _fprocessDocker(["rm", "-f", fsProxyName(sProjectUuid)])
        _fprocessDocker(["network", "rm", fsNetworkName(sProjectUuid)])
        shutil.rmtree(os.path.join(_fsEgressRoot(), sProjectUuid), ignore_errors=True)
    except EgressSetupError:
        logger.warning("project egress release failed for %s", sProjectUuid,
                       exc_info=True)


def flistDiscoverProjectEgressUuids():
    """Return every project identity a labeled proxy or network on the daemon names."""
    setUuids = set()
    for listCommand in (
        ["ps", "-a", "--filter", f"label={S_LABEL_KEY}",
         "--format", f'{{{{index .Labels "{S_LABEL_KEY}"}}}}'],
        ["network", "ls", "--filter", f"label={S_LABEL_KEY}",
         "--format", f'{{{{index .Labels "{S_LABEL_KEY}"}}}}'],
    ):
        processResult = _fprocessDocker(listCommand)
        if processResult.returncode != 0:
            raise EgressSetupError(f"docker {listCommand[0]} failed: {processResult.stderr}")
        setUuids.update(
            sLine.strip() for sLine in processResult.stdout.splitlines()
            if _RE_PROJECT_UUID.match(sLine.strip()))
    return sorted(setUuids)


def fdictReconcileProjectEgress(fbProjectContainerExists):
    """Release the egress of every project whose container the daemon says is gone."""
    dictOutcomes = {}
    for sProjectUuid in flistDiscoverProjectEgressUuids():
        if fbProjectContainerExists(sProjectUuid):
            dictOutcomes[sProjectUuid] = "kept"
            continue
        fnReleaseProjectEgressQuietly(sProjectUuid)
        dictOutcomes[sProjectUuid] = "released"
    return dictOutcomes


def fsProxyState(sProjectUuid):
    """Return running, down or absent for the project's proxy sidecar."""
    processResult = _fprocessDocker(
        ["inspect", "--format", "{{.State.Running}}", fsProxyName(sProjectUuid)])
    if processResult.returncode != 0:
        return S_PROXY_STATE_ABSENT
    return (S_PROXY_STATE_RUNNING if processResult.stdout.strip() == "true"
            else S_PROXY_STATE_DOWN)


def flistReadRefusedDestinations(sProjectUuid, iLimit=20):
    """Return the recently refused destinations, most refused first."""
    processLogs = _fprocessDocker(
        ["logs", "--tail", str(I_LOG_TAIL_LINES), fsProxyName(sProjectUuid)])
    dictCounts = {}
    for sLine in (processLogs.stdout + processLogs.stderr).splitlines():
        dictDecision = egressProxy.fdictParseConnectLogLine(sLine)
        if dictDecision is None or dictDecision["bAllowed"]:
            continue
        tKey = (dictDecision["sHost"], dictDecision["iPort"])
        dictEntry = dictCounts.setdefault(
            tKey, {"sHost": tKey[0], "iPort": tKey[1], "iCount": 0,
                   "sReason": dictDecision["sReason"]})
        dictEntry["iCount"] += 1
    return sorted(dictCounts.values(), key=lambda dictEntry: -dictEntry["iCount"])[:iLimit]


def fdictDescribeProjectEgress(sProjectUuid):
    """Return ``{sProxyState, listRefused}`` for the dashboard's Network row."""
    sState = fsProxyState(sProjectUuid)
    return {
        "sProxyState": sState,
        "listRefused": (flistReadRefusedDestinations(sProjectUuid)
                        if sState != S_PROXY_STATE_ABSENT else []),
    }
