"""A project container's egress boundary in a sealed mode: its network and its proxy.

Each project in a sealed mode (web or allowlist) gets an INTERNAL
network with the bridge's
gateway mode ``isolated`` (Engine 28 and later) and a proxy sidecar on
that network and on the default bridge. ``--internal`` alone is not the
seal: Docker gives an internal network's bridge an address, and a
container on it can then reach every host service that listens on
``0.0.0.0``, including the daemon host's. In ``isolated`` mode the
bridge gets no address and the container no route, so a direct dial to
the host, the VM, the LAN or another container fails before a packet
leaves the machine (measured on Engine 28.4.0: ``ENETUNREACH``). An
engine that does not honor the option, or that still assigns a gateway,
refuses both sealed modes by name; there is no fallback to a
gatewayed network.

The two modes differ only in the proxy's host-name check: allowlist
mode tunnels to the computed allowlist, web mode (``--any-public-host``)
to any host whose name resolves to a public address. Raw-address
targets and non-public resolutions are refused in both.

The sidecar runs the shared proxy of :mod:`egressProxy` from a
read-only bind mount of the script, as an unprivileged user with every
capability dropped, a read-only root, memory, CPU and PID bounds, and
the host-gateway entry so it can serve the project's hub as the ONE
host destination (``host.docker.internal:<hub port>``). The hub port
is not fixed at launch: the sidecar starts serving no hub, and the
hub that connects to the container pins its own port
(:func:`fsPinProjectEgressHub`), just before it tells the agent which
URL to dial, by rewriting a file the sidecar mounts read-only. A
container a different hub later takes over is then pinned to that
hub, with no restart and no new proxy address. A sidecar started by
a vaibify older than this pins the launch-time port in its argv and
cannot be re-pinned; :func:`fdictReadHubPin` reports it as such. The
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
    "S_PIN_ABSENT",
    "S_PIN_FIXED",
    "S_PIN_PINNED",
    "fbEgressIsInUse",
    "fdictDescribeProjectEgress",
    "fdictPrepareProjectEgress",
    "fdictReadHubPin",
    "fdictReconcileProjectEgress",
    "flistDiscoverProjectEgressUuids",
    "flistNetworkCreateArguments",
    "flistProxyRunArguments",
    "flistReadRefusedDestinations",
    "fnReleaseProjectEgressIfUnused",
    "fnReleaseProjectEgressQuietly",
    "fsNetworkName",
    "fsPinProjectEgressHub",
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

from vaibify.config.projectConfig import S_NETWORK_MODE_WEB

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
    "the web and allowlist network modes need a Docker Engine that honors "
    "the bridge option gateway_mode_ipv4=isolated (Engine 28.0.0 or "
    "later); this daemon did not. Update the engine, or set networkMode: "
    "open in vaibify.yml."
)
S_PROXY_STATE_RUNNING = "running"
S_PROXY_STATE_DOWN = "down"
S_PROXY_STATE_ABSENT = "absent"
S_PIN_PINNED = "pinned"
S_PIN_FIXED = "fixed"
S_PIN_ABSENT = "absent"
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


def _fsHubPortDirectory(sProjectUuid):
    _fnValidateProjectUuid(sProjectUuid)
    return os.path.join(_fsEgressRoot(), sProjectUuid, egressProxy.S_HUB_PORT_DIRECTORY)


def _fsHubPortPath(sProjectUuid):
    return os.path.join(_fsHubPortDirectory(sProjectUuid), egressProxy.S_HUB_PORT_BASENAME)


def _fnWriteHubPort(sProjectUuid, iHubPort):
    """Write the hub port the sidecar serves; 0 writes "no hub".

    The directory is world-readable because the sidecar's unprivileged
    user reads it through a bind mount; the port is no secret, and the
    project directory above it stays private to the host user.
    """
    sDirectory = _fsHubPortDirectory(sProjectUuid)
    os.makedirs(os.path.dirname(sDirectory), mode=0o700, exist_ok=True)
    os.makedirs(sDirectory, mode=0o755, exist_ok=True)
    os.chmod(sDirectory, 0o755)
    fnWriteFileDurably(
        _fsHubPortPath(sProjectUuid),
        (str(iHubPort) if iHubPort else "").encode("ascii"), iMode=0o644,
    )


def _fiReadHubPortFile(sProjectUuid):
    """Return the port the sidecar's file names, or 0."""
    try:
        with open(_fsHubPortPath(sProjectUuid), "r", encoding="ascii") as fileHandle:
            return egressProxy.fiParseHubPortText(fileHandle.read(16))
    except (OSError, UnicodeDecodeError):
        return 0


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
    config, sProjectUuid, sNetworkName, sScriptPath, sSubnet,
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
        "--mount",
        f"type=bind,source={_fsHubPortDirectory(sProjectUuid)},"
        f"target=/{egressProxy.S_HUB_PORT_DIRECTORY},readonly",
    ]
    for iListen, _sTarget, _iTarget in listForwards:
        listArguments.extend(["-p", f"127.0.0.1:{iListen}:{iListen}"])
    listArguments.append(egressProxy.S_PROXY_IMAGE)
    listArguments.extend(egressProxy.flistComposeProxyCommand(
        listHosts, listPorts, {}, sSubnet, "", listForwards,
        bAnyPublicHost=getattr(config, "sNetworkMode", "") == S_NETWORK_MODE_WEB,
        sRepinnableHubHost=S_HUB_HOST,
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


def fdictPrepareProjectEgress(config, sProjectUuid):
    """Make the project's sealed network and proxy; return what the launch needs.

    Returns ``{"sNetworkName", "sProxyName", "sProxyAddress"}``. A stale
    sidecar from an earlier start is replaced, because its allowlist
    may be stale too. The new sidecar serves no hub until one connects
    and pins its port: the launch does not know which hub the agent
    will be told to dial, and a guess is how a container came to be
    pinned to a hub other than the one that owned it.
    """
    sNetworkName = fsNetworkName(sProjectUuid)
    sProxyName = fsProxyName(sProjectUuid)
    _fnEnsureSealedNetwork(sNetworkName, sProjectUuid)
    _fnRemoveProxyLogged(sProxyName, "replaced by a fresh launch")
    sScriptPath = _fsWriteProxyScript(sProjectUuid)
    _fnWriteHubPort(sProjectUuid, 0)
    sSubnet = _fsReadNetworkSubnet(sNetworkName)
    _fsDockerOrRaise(
        flistProxyRunArguments(
            config, sProjectUuid, sNetworkName, sScriptPath, sSubnet),
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
        _fnRemoveProxyLogged(sProxyName, "it did not come up")
        raise


def _fnRemoveProxyLogged(sProxyName, sReason):
    """Remove a proxy container, logging the removal when one existed."""
    processResult = _fprocessDocker(["rm", "-f", sProxyName])
    if processResult.returncode == 0 and processResult.stdout.strip():
        logger.info("network proxy %s removed: %s", sProxyName, sReason)


def fnReleaseProjectEgressQuietly(sProjectUuid, sReason):
    """Remove the project's proxy, network and script; a failure is logged.

    Every release is logged with its reason. One that left no trace
    took a running project's proxy and took a day to attribute.
    """
    if not _RE_PROJECT_UUID.match(sProjectUuid or ""):
        return
    logger.info("project egress released for %s: %s", sProjectUuid, sReason)
    try:
        _fprocessDocker(["rm", "-f", fsProxyName(sProjectUuid)])
        _fprocessDocker(["network", "rm", fsNetworkName(sProjectUuid)])
        shutil.rmtree(os.path.join(_fsEgressRoot(), sProjectUuid), ignore_errors=True)
    except EgressSetupError:
        logger.warning("project egress release failed for %s", sProjectUuid,
                       exc_info=True)


def flistDiscoverProjectEgressUuids():
    """Return every project identity a labeled proxy or network on the daemon names.

    ``.Label`` rather than ``index .Labels``: ``docker ps`` formats
    ``.Labels`` as one comma-joined string, which ``index`` refuses, so
    that spelling failed every discovery on every daemon.
    """
    setUuids = set()
    for listCommand in (
        ["ps", "-a", "--filter", f"label={S_LABEL_KEY}",
         "--format", f'{{{{.Label "{S_LABEL_KEY}"}}}}'],
        ["network", "ls", "--filter", f"label={S_LABEL_KEY}",
         "--format", f'{{{{.Label "{S_LABEL_KEY}"}}}}'],
    ):
        processResult = _fprocessDocker(listCommand)
        if processResult.returncode != 0:
            raise EgressSetupError(f"docker {listCommand[0]} failed: {processResult.stderr}")
        setUuids.update(
            sLine.strip() for sLine in processResult.stdout.splitlines()
            if _RE_PROJECT_UUID.match(sLine.strip()))
    return sorted(setUuids)


def fbEgressIsInUse(sProjectUuid):
    """Return True when the daemon reports a container besides the proxy on the network.

    The daemon's answer is the only evidence. A project registry is
    not: a hub whose registry lacked a project once released the proxy
    of that project's RUNNING container, because absence from a
    registry was read as absence from the daemon. ``--filter network=``
    matches a stopped container too, and still matches by name once the
    network itself is gone. A daemon that does not answer raises, so
    nothing is released on silence.
    """
    processResult = _fprocessDocker(
        ["ps", "-a", "--filter", f"network={fsNetworkName(sProjectUuid)}",
         "--format", "{{.Names}}"])
    if processResult.returncode != 0:
        raise EgressSetupError(f"docker ps failed: {processResult.stderr}")
    sProxyName = fsProxyName(sProjectUuid)
    return any(sName.strip() and sName.strip() != sProxyName
               for sName in processResult.stdout.splitlines())


def fnReleaseProjectEgressIfUnused(sProjectUuid, sReason):
    """Release the identity's egress only once the daemon says nothing uses it.

    A removal that failed, or a second container on the network, keeps
    the proxy; a daemon that does not answer keeps it too, logged.
    """
    if not _RE_PROJECT_UUID.match(sProjectUuid or ""):
        return
    try:
        bInUse = fbEgressIsInUse(sProjectUuid)
    except EgressSetupError:
        logger.warning("project egress kept for %s: the daemon did not "
                       "say whether a container uses it", sProjectUuid,
                       exc_info=True)
        return
    if bInUse:
        logger.info("project egress kept for %s: a container is still "
                    "attached to its network", sProjectUuid)
        return
    fnReleaseProjectEgressQuietly(sProjectUuid, sReason)


def fdictReconcileProjectEgress():
    """Release the egress of every identity no container uses; return the outcomes."""
    dictOutcomes = {}
    for sProjectUuid in flistDiscoverProjectEgressUuids():
        if fbEgressIsInUse(sProjectUuid):
            dictOutcomes[sProjectUuid] = "kept"
            continue
        fnReleaseProjectEgressQuietly(
            sProjectUuid, "no container is attached to its network")
        dictOutcomes[sProjectUuid] = "released"
    return dictOutcomes


def _flistReadProxyCommand(sProjectUuid):
    """Return the sidecar's argv, or None when there is no sidecar."""
    processResult = _fprocessDocker(
        ["inspect", "--format", "{{json .Config.Cmd}}", fsProxyName(sProjectUuid)])
    if processResult.returncode != 0:
        return None
    try:
        listCommand = json.loads(processResult.stdout.strip() or "null")
    except ValueError:
        raise EgressSetupError(
            f"could not read the network proxy's command: {processResult.stdout!r}")
    return [str(sArgument) for sArgument in (listCommand or [])]


def _fiFixedHubPortOf(listCommand):
    """Return the port an older sidecar's ``--hub-gateway`` names, or 0."""
    if "--hub-gateway" not in listCommand:
        return 0
    iIndex = listCommand.index("--hub-gateway") + 1
    sGateway = listCommand[iIndex] if iIndex < len(listCommand) else ""
    return egressProxy.fiParseHubPortText(sGateway.rpartition(":")[2])


def fdictReadHubPin(sProjectUuid):
    """Return ``{sPin, iPinnedHubPort}``: which hub port the sidecar serves.

    ``sPin`` is ``pinned`` for a sidecar that reads its port from the
    file, ``fixed`` for an older one whose argv fixed it at launch, and
    ``absent`` when there is no sidecar (``iPinnedHubPort`` 0). A port
    of 0 means the sidecar serves no hub.
    """
    listCommand = _flistReadProxyCommand(sProjectUuid)
    if listCommand is None:
        return {"sPin": S_PIN_ABSENT, "iPinnedHubPort": 0}
    if "--hub-port-file" in listCommand:
        return {"sPin": S_PIN_PINNED, "iPinnedHubPort": _fiReadHubPortFile(sProjectUuid)}
    return {"sPin": S_PIN_FIXED, "iPinnedHubPort": _fiFixedHubPortOf(listCommand)}


def fsPinProjectEgressHub(sProjectUuid, iHubPort):
    """Point the project's sidecar at hub ``iHubPort``; return the pin kind.

    Called by the hub that connects to the container, BEFORE it writes
    the agent's session file, so the agent is never told a hub the
    proxy refuses. An unchanged pin is not rewritten: some daemons'
    file sharing shows a replaced file as missing for a moment. A
    sidecar that cannot be re-pinned answers ``fixed`` and is left
    alone; the dashboard's Network row says what it serves.
    """
    if not isinstance(iHubPort, int) or not (
            egressProxy.I_MINIMUM_HUB_PORT <= iHubPort <= egressProxy.I_MAXIMUM_HUB_PORT):
        raise EgressSetupError(f"hub port {iHubPort!r} is not a port a hub listens on")
    dictPin = fdictReadHubPin(sProjectUuid)
    if dictPin["sPin"] == S_PIN_PINNED and dictPin["iPinnedHubPort"] != iHubPort:
        _fnWriteHubPort(sProjectUuid, iHubPort)
        logger.info("network proxy of %s now serves hub port %d (was %d)",
                    sProjectUuid, iHubPort, dictPin["iPinnedHubPort"])
    return dictPin["sPin"]


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
    """Return the Network row's proxy facts: state, hub pin, refusals.

    ``{sProxyState, sPin, iPinnedHubPort, listRefused}``. A refusal of
    the hub's host carries ``bAllowable`` false: the host is never
    judged by the allowlist, so allowing it would change nothing. A
    refusal of the port the sidecar serves NOW is dropped, because it
    was refused under an earlier pin and would be allowed today.
    """
    sState = fsProxyState(sProjectUuid)
    if sState == S_PROXY_STATE_ABSENT:
        return {"sProxyState": sState, "sPin": S_PIN_ABSENT,
                "iPinnedHubPort": 0, "listRefused": []}
    dictPin = fdictReadHubPin(sProjectUuid)
    listRefused = []
    for dictEntry in flistReadRefusedDestinations(sProjectUuid):
        bHubHost = dictEntry["sHost"] == S_HUB_HOST
        if bHubHost and dictEntry["iPort"] == dictPin["iPinnedHubPort"]:
            continue
        listRefused.append({**dictEntry, "bAllowable": not bHubHost})
    return {"sProxyState": sState, **dictPin, "listRefused": listRefused}
