"""Per-campaign egress boundary for Agent Council runners (design section 9.7).

A council runner may reach exactly one destination family: its own
provider's API endpoints. This module builds that boundary out of three
pieces, each created per campaign and destroyed with the campaign:

1. an INTERNAL Docker network (``docker network create --internal``),
   which the kernel gives no route off the host — every direct dial to
   a bridge, external, or IPv6 address fails immediately with
   ``ENETUNREACH`` (errno 101, verified live on Docker 28.4.0);
2. an allowlisting HTTP CONNECT proxy — a server-owned Python-stdlib
   script (``S_CONNECT_PROXY_SCRIPT``) run in a ``python:3.10-slim``
   container attached to BOTH the internal network and the default
   bridge. It serves only ``CONNECT`` to hostnames on a fixed,
   server-supplied allowlist, refuses raw-address targets, and performs
   name resolution ITSELF on its bridge side (or from a server-supplied
   hostname-to-address map, which is how the live tests pin their
   stand-in provider). Nothing model-controlled ever selects an
   endpoint: the allowlist, the port set and the address map enter only
   through the container's command line, composed here from validated
   server-owned values; and
3. runner wiring: the runner joins only the internal network, receives
   ``HTTPS_PROXY``/``HTTP_PROXY`` pointing at the proxy's NUMERIC
   internal address (so it never needs a resolver), and has its DNS
   black-holed as described below.

Phase 0 finding — the embedded-DNS exfiltration hazard
------------------------------------------------------
Docker's embedded resolver (127.0.0.11) answers on every user-defined
network, and it forwards external queries from the DAEMON'S network
namespace — so historically a container on an ``--internal`` network
could still resolve arbitrary external names, and the queried names
themselves leak bits to an attacker-observed nameserver. Falsified
empirically on Docker Engine 28.4.0 (aarch64 macOS, 2026-08-19):

- on an internal network, external lookups now FAIL fast with
  ``socket.gaierror`` EAI_AGAIN — this daemon generation refuses to
  forward for internal networks (container names on the same network
  still resolve, which stays on-host and leaks nothing off it);
- on an ordinary (non-internal) user-defined network the SAME daemon
  forwards happily — the hazard mechanism is alive in the engine, and
  its suppression on internal networks is daemon-version behaviour,
  not a documented contract.

Because the suppression is not a contract, runners do not rely on it.
Every runner is launched with ``--dns 192.0.2.1 --dns-option timeout:1
--dns-option attempts:1``: the embedded resolver's upstream set is then
ONLY the RFC 5737 TEST-NET-1 black-hole address (confirmed live — the
generated ``resolv.conf`` reports ``ExtServers: [192.0.2.1]``), and an
external lookup fails in about one second even on a network where
forwarding is live. ``192.0.2.1`` is reserved for documentation and is
never routed, so no real resolver can ever answer from it. Note that
``--dns 127.0.0.1`` would NOT work: Docker filters localhost resolver
addresses and silently substitutes public DNS, which would reopen the
channel while appearing to close it.

Teardown follows the prove-absence discipline of
``containerManager.fdictProbeContainerPresence``: after removal, the
daemon is asked to enumerate the resources, and a daemon that did not
answer yields an INDETERMINATE report, never a claim of absence.
"""

import ipaddress
import os
import re
import subprocess
import tempfile
import time

__all__ = [
    "EgressSetupError",
    "I_PROXY_LISTEN_PORT",
    "S_BLACK_HOLE_NAMESERVER",
    "S_CONNECT_PROXY_SCRIPT",
    "S_PROXY_IMAGE",
    "fdictBuildRunnerProxyEnvironment",
    "fdictRemoveCampaignEgressResources",
    "flistBuildRunnerNetworkArguments",
    "fnValidateCampaignIdOrRaise",
    "fsComposeNetworkName",
    "fsComposeProxyContainerName",
    "fsCreateCampaignInternalNetwork",
    "fsLaunchAllowlistProxy",
]


class EgressSetupError(RuntimeError):
    """A step of the egress-boundary build or teardown failed."""


S_PROXY_IMAGE = "python:3.10-slim"
I_PROXY_LISTEN_PORT = 8888
S_PROXY_SCRIPT_CONTAINER_PATH = "/vaibifyEgressProxy.py"

# RFC 5737 TEST-NET-1: reserved for documentation, never routed, so a
# resolver forwarding only to it can never receive an answer.
S_BLACK_HOLE_NAMESERVER = "192.0.2.1"

_S_NETWORK_NAME_PREFIX = "vaibifyCouncilEgress"
_S_PROXY_NAME_PREFIX = "vaibifyCouncilProxy"

# A campaign id is server-minted. Validating it before it reaches a
# command line keeps a crafted value from smuggling an extra flag.
_RE_CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_RE_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

_F_DOCKER_COMMAND_TIMEOUT_SECONDS = 30.0
_F_PROXY_READY_DEADLINE_SECONDS = 20.0
_F_PROXY_READY_POLL_SECONDS = 0.25

# The line the proxy prints once its listening socket is bound; launch
# blocks until it appears so a caller never wires a runner to a proxy
# that is still importing.
_S_PROXY_READY_LINE = "vaibify-egress: listening"

# Server-owned constant text, executed inside the proxy container.
# Parameterized EXCLUSIVELY through argv composed by this module from
# validated server-owned values; no model- or user-controlled text may
# ever enter this script or its arguments. Raw string: the \r\n escape
# sequences must reach the container as escape sequences in Python
# source, not as literal line breaks.
S_CONNECT_PROXY_SCRIPT = r'''"""Allowlisting HTTP CONNECT proxy. Stdlib only. Server-owned text."""
import argparse
import asyncio
import ipaddress
import sys

I_HANDSHAKE_TIMEOUT_SECONDS = 10
I_UPSTREAM_DIAL_TIMEOUT_SECONDS = 10
I_RELAY_CHUNK_BYTES = 65536
I_MAXIMUM_HEADER_LINES = 100

SET_ALLOWED_HOSTNAMES = set()
SET_ALLOWED_PORTS = set()
DICT_HOSTNAME_ADDRESS_MAP = {}


def fbLooksLikeRawAddress(sHost):
    sCandidate = sHost.strip("[]")
    try:
        ipaddress.ip_address(sCandidate)
        return True
    except ValueError:
        return False


async def fnRefuse(writerClient, sStatusLine, sReason):
    sBody = "vaibify-egress: " + sReason + "\n"
    sResponse = (
        sStatusLine + "\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: " + str(len(sBody)) + "\r\n"
        "Connection: close\r\n\r\n" + sBody
    )
    writerClient.write(sResponse.encode("ascii"))
    await writerClient.drain()


async def fnPumpOneDirection(readerSource, writerSink):
    try:
        while True:
            baChunk = await readerSource.read(I_RELAY_CHUNK_BYTES)
            if not baChunk:
                break
            writerSink.write(baChunk)
            await writerSink.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writerSink.close()
        except Exception:
            pass


async def fsResolveAllowlistedHost(sHost, iPort):
    if sHost in DICT_HOSTNAME_ADDRESS_MAP:
        return DICT_HOSTNAME_ADDRESS_MAP[sHost]
    loopRunning = asyncio.get_running_loop()
    listAnswers = await asyncio.wait_for(
        loopRunning.getaddrinfo(sHost, iPort),
        I_UPSTREAM_DIAL_TIMEOUT_SECONDS,
    )
    return listAnswers[0][4][0]


async def fnServeOneClient(readerClient, writerClient):
    try:
        baRequestLine = await asyncio.wait_for(
            readerClient.readline(), I_HANDSHAKE_TIMEOUT_SECONDS)
        saParts = baRequestLine.decode("latin-1").split()
        for _ in range(I_MAXIMUM_HEADER_LINES):
            baHeaderLine = await asyncio.wait_for(
                readerClient.readline(), I_HANDSHAKE_TIMEOUT_SECONDS)
            if baHeaderLine in (b"\r\n", b"\n", b""):
                break
        if len(saParts) < 2 or saParts[0] != "CONNECT":
            await fnRefuse(writerClient, "HTTP/1.1 405 Method Not Allowed",
                           "only CONNECT is served")
            return
        sHost, sSeparator, sPort = saParts[1].rpartition(":")
        if not sSeparator or not sPort.isdigit():
            await fnRefuse(writerClient, "HTTP/1.1 400 Bad Request",
                           "CONNECT target must be host:port")
            return
        iPort = int(sPort)
        sHostLower = sHost.lower()
        if fbLooksLikeRawAddress(sHostLower):
            await fnRefuse(writerClient, "HTTP/1.1 403 Forbidden",
                           "raw-address CONNECT target refused")
            return
        if sHostLower not in SET_ALLOWED_HOSTNAMES:
            await fnRefuse(writerClient, "HTTP/1.1 403 Forbidden",
                           "host not on allowlist")
            return
        if iPort not in SET_ALLOWED_PORTS:
            await fnRefuse(writerClient, "HTTP/1.1 403 Forbidden",
                           "port not on allowlist")
            return
        try:
            sAddress = await fsResolveAllowlistedHost(sHostLower, iPort)
            readerUpstream, writerUpstream = await asyncio.wait_for(
                asyncio.open_connection(sAddress, iPort),
                I_UPSTREAM_DIAL_TIMEOUT_SECONDS,
            )
        except (OSError, asyncio.TimeoutError):
            await fnRefuse(writerClient, "HTTP/1.1 502 Bad Gateway",
                           "upstream dial failed")
            return
        writerClient.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writerClient.drain()
        await asyncio.gather(
            fnPumpOneDirection(readerClient, writerUpstream),
            fnPumpOneDirection(readerUpstream, writerClient),
        )
    except (asyncio.TimeoutError, ConnectionError, UnicodeDecodeError):
        pass
    finally:
        try:
            writerClient.close()
        except Exception:
            pass


async def fnRunProxyForever(iListenPort):
    serverProxy = await asyncio.start_server(
        fnServeOneClient, host="0.0.0.0", port=iListenPort)
    print("vaibify-egress: listening on", iListenPort, flush=True)
    async with serverProxy:
        await serverProxy.serve_forever()


def main():
    parserArguments = argparse.ArgumentParser()
    parserArguments.add_argument("--listen-port", type=int, required=True)
    parserArguments.add_argument("--allowlist", required=True)
    parserArguments.add_argument("--allowed-ports", default="443")
    parserArguments.add_argument("--address-map", default="")
    namespaceArguments = parserArguments.parse_args()
    SET_ALLOWED_HOSTNAMES.update(
        sHost.strip().lower()
        for sHost in namespaceArguments.allowlist.split(",") if sHost.strip())
    SET_ALLOWED_PORTS.update(
        int(sPort) for sPort in namespaceArguments.allowed_ports.split(",")
        if sPort.strip())
    for sPair in namespaceArguments.address_map.split(","):
        if "=" in sPair:
            sHost, sAddress = sPair.split("=", 1)
            DICT_HOSTNAME_ADDRESS_MAP[sHost.strip().lower()] = sAddress.strip()
    try:
        asyncio.run(fnRunProxyForever(namespaceArguments.listen_port))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
'''


def fnValidateCampaignIdOrRaise(sCampaignId):
    """Refuse a campaign id that could smuggle flags into a command line."""
    if not isinstance(sCampaignId, str) or not _RE_CAMPAIGN_ID.match(
            sCampaignId):
        raise EgressSetupError(
            "campaign id must be 1-64 characters of letters, digits and "
            f"hyphens, starting with a letter or digit; got {sCampaignId!r}"
        )


def fsComposeNetworkName(sCampaignId):
    """Return the per-campaign internal network name."""
    fnValidateCampaignIdOrRaise(sCampaignId)
    return f"{_S_NETWORK_NAME_PREFIX}-{sCampaignId}"


def fsComposeProxyContainerName(sCampaignId):
    """Return the per-campaign proxy container name."""
    fnValidateCampaignIdOrRaise(sCampaignId)
    return f"{_S_PROXY_NAME_PREFIX}-{sCampaignId}"


def _ftRunDockerCommand(saCommand, fTimeoutSeconds=None):
    """Return ``(bAnswered, processResult)`` for a bounded docker call.

    ``bAnswered`` False means the daemon did not answer at all (absent
    CLI, timeout) — which is NOT the same as a nonzero exit and must
    never be read as one.
    """
    fBound = fTimeoutSeconds or _F_DOCKER_COMMAND_TIMEOUT_SECONDS
    try:
        processResult = subprocess.run(
            saCommand, capture_output=True, text=True, timeout=fBound,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (False, None)
    return (True, processResult)


def _fnRunDockerCommandOrRaise(saCommand, sPurpose):
    """Run a docker command that must succeed, or raise with its stderr."""
    bAnswered, processResult = _ftRunDockerCommand(saCommand)
    if not bAnswered:
        raise EgressSetupError(
            f"docker did not answer while trying to {sPurpose}; is the "
            "daemon running?"
        )
    if processResult.returncode != 0:
        raise EgressSetupError(
            f"failed to {sPurpose}: {processResult.stderr.strip()}"
        )


def fsCreateCampaignInternalNetwork(sCampaignId):
    """Create the campaign's internal network and return its name.

    ``--internal`` is the boundary itself: the daemon attaches no
    gateway off the host, so a runner's direct dial to any bridge,
    external or IPv6 address fails with ``ENETUNREACH`` before a
    single packet leaves the machine.
    """
    sNetworkName = fsComposeNetworkName(sCampaignId)
    _fnRunDockerCommandOrRaise(
        ["docker", "network", "create", "--internal", sNetworkName],
        f"create internal network {sNetworkName}",
    )
    return sNetworkName


def _fnValidateAllowlistOrRaise(saAllowedHostnames, iaAllowedPorts,
                                dictHostnameAddressMap):
    """Refuse allowlist material that is not plainly server-shaped."""
    if not saAllowedHostnames:
        raise EgressSetupError("the egress allowlist must not be empty")
    for sHostname in saAllowedHostnames:
        if not _RE_HOSTNAME.match(sHostname):
            raise EgressSetupError(
                f"allowlist entry {sHostname!r} is not a valid hostname")
        try:
            ipaddress.ip_address(sHostname)
        except ValueError:
            pass
        else:
            raise EgressSetupError(
                f"allowlist entry {sHostname!r} is a raw address; the "
                "proxy refuses raw-address targets — allowlist a "
                "hostname and pin its address in dictHostnameAddressMap"
            )
    for iPort in iaAllowedPorts:
        if not isinstance(iPort, int) or not 0 < iPort < 65536:
            raise EgressSetupError(f"allowed port {iPort!r} is not valid")
    for sHostname, sAddress in dictHostnameAddressMap.items():
        if not _RE_HOSTNAME.match(sHostname):
            raise EgressSetupError(
                f"address-map key {sHostname!r} is not a valid hostname")
        try:
            ipaddress.ip_address(sAddress)
        except ValueError:
            raise EgressSetupError(
                f"address-map value {sAddress!r} for {sHostname!r} is "
                "not a valid address"
            )


def _fnCopyProxyScriptIntoContainer(sProxyName):
    """Stage the server-owned proxy script into the created container."""
    iFileDescriptor, sScriptPath = tempfile.mkstemp(
        prefix="vaibifyEgressProxy", suffix=".py")
    try:
        with os.fdopen(iFileDescriptor, "w", encoding="utf-8") as fileScript:
            fileScript.write(S_CONNECT_PROXY_SCRIPT)
        _fnRunDockerCommandOrRaise(
            ["docker", "cp", sScriptPath,
             f"{sProxyName}:{S_PROXY_SCRIPT_CONTAINER_PATH}"],
            f"copy the proxy script into {sProxyName}",
        )
    finally:
        try:
            os.unlink(sScriptPath)
        except OSError:
            pass


def _fnAwaitProxyListening(sProxyName):
    """Block until the proxy logs its ready line, or raise."""
    fDeadline = time.monotonic() + _F_PROXY_READY_DEADLINE_SECONDS
    while time.monotonic() < fDeadline:
        bAnswered, processResult = _ftRunDockerCommand(
            ["docker", "logs", sProxyName])
        if bAnswered and processResult.returncode == 0:
            sCombined = processResult.stdout + processResult.stderr
            if _S_PROXY_READY_LINE in sCombined:
                return
            if "Traceback" in sCombined:
                raise EgressSetupError(
                    f"the egress proxy crashed on startup: {sCombined}")
        time.sleep(_F_PROXY_READY_POLL_SECONDS)
    raise EgressSetupError(
        f"the egress proxy {sProxyName} never reported "
        f"{_S_PROXY_READY_LINE!r} within "
        f"{_F_PROXY_READY_DEADLINE_SECONDS} seconds"
    )


def _fsReadProxyInternalAddress(sProxyName, sNetworkName):
    """Return the proxy's numeric address on the internal network."""
    sFormat = (
        '{{with index .NetworkSettings.Networks "'
        + sNetworkName + '"}}{{.IPAddress}}{{end}}'
    )
    bAnswered, processResult = _ftRunDockerCommand(
        ["docker", "inspect", "-f", sFormat, sProxyName])
    if not bAnswered or processResult.returncode != 0:
        raise EgressSetupError(
            f"could not read the internal-network address of {sProxyName}")
    sAddress = processResult.stdout.strip()
    if not sAddress:
        raise EgressSetupError(
            f"{sProxyName} reports no address on {sNetworkName}")
    return sAddress


def _flistComposeProxyCreateCommand(sProxyName, sNetworkName,
                                    saAllowedHostnames, iaAllowedPorts,
                                    dictHostnameAddressMap):
    """Compose the ``docker create`` command for the proxy container."""
    saCommand = [
        "docker", "create", "--name", sProxyName,
        "--network", sNetworkName, "--restart", "no",
        S_PROXY_IMAGE, "python", S_PROXY_SCRIPT_CONTAINER_PATH,
        "--listen-port", str(I_PROXY_LISTEN_PORT),
        "--allowlist", ",".join(saAllowedHostnames),
        "--allowed-ports", ",".join(str(iPort) for iPort in iaAllowedPorts),
    ]
    if dictHostnameAddressMap:
        saCommand.extend([
            "--address-map",
            ",".join(
                f"{sHostname}={sAddress}"
                for sHostname, sAddress in dictHostnameAddressMap.items()
            ),
        ])
    return saCommand


def fsLaunchAllowlistProxy(sCampaignId, saAllowedHostnames,
                           iaAllowedPorts=None,
                           dictHostnameAddressMap=None):
    """Launch the campaign's CONNECT proxy; return its internal address.

    The container is created on the internal network, given the
    server-owned script by ``docker cp``, attached to the default
    bridge (its resolving and dialing side), and only then started —
    so it is dual-homed before the first byte is served. The returned
    address is the NUMERIC internal-network address the runner's proxy
    environment points at, which is why a runner needs no resolver.
    """
    iaPorts = list(iaAllowedPorts) if iaAllowedPorts else [443]
    dictAddressMap = dict(dictHostnameAddressMap or {})
    saHostnames = [sHostname.lower() for sHostname in saAllowedHostnames]
    _fnValidateAllowlistOrRaise(saHostnames, iaPorts, dictAddressMap)
    sNetworkName = fsComposeNetworkName(sCampaignId)
    sProxyName = fsComposeProxyContainerName(sCampaignId)
    try:
        _fnRunDockerCommandOrRaise(
            _flistComposeProxyCreateCommand(
                sProxyName, sNetworkName, saHostnames, iaPorts,
                dictAddressMap),
            f"create the egress proxy container {sProxyName}",
        )
        _fnCopyProxyScriptIntoContainer(sProxyName)
        _fnRunDockerCommandOrRaise(
            ["docker", "network", "connect", "bridge", sProxyName],
            f"attach {sProxyName} to the default bridge",
        )
        _fnRunDockerCommandOrRaise(
            ["docker", "start", sProxyName],
            f"start the egress proxy {sProxyName}",
        )
        _fnAwaitProxyListening(sProxyName)
        return _fsReadProxyInternalAddress(sProxyName, sNetworkName)
    except Exception:
        _ftRunDockerCommand(["docker", "rm", "-f", sProxyName])
        raise


def fdictBuildRunnerProxyEnvironment(sProxyInternalAddress,
                                     iProxyPort=I_PROXY_LISTEN_PORT):
    """Return the proxy environment a runner container receives.

    Both spellings are provided because HTTP clients disagree about
    case. The value uses the proxy's numeric internal address, so the
    runner can use it without any working name resolution.
    """
    sProxyUniformResourceLocator = (
        f"http://{sProxyInternalAddress}:{iProxyPort}")
    return {
        "HTTPS_PROXY": sProxyUniformResourceLocator,
        "HTTP_PROXY": sProxyUniformResourceLocator,
        "https_proxy": sProxyUniformResourceLocator,
        "http_proxy": sProxyUniformResourceLocator,
    }


def flistBuildRunnerNetworkArguments(sCampaignId):
    """Return the ``docker run`` arguments that seal a runner's network.

    ``--network``: the internal network only — no external route, so
    direct and IPv6 dials die with ``ENETUNREACH``. ``--dns`` plus the
    two ``--dns-option`` values black-hole the embedded resolver's
    upstream (module docstring, Phase 0 finding), so an external
    lookup fails in about a second instead of resolving or hanging.
    """
    sNetworkName = fsComposeNetworkName(sCampaignId)
    return [
        "--network", sNetworkName,
        "--dns", S_BLACK_HOLE_NAMESERVER,
        "--dns-option", "timeout:1",
        "--dns-option", "attempts:1",
    ]


def _ftProbeListingCommand(saCommand):
    """Return ``(bAnswered, bPresent)`` for a docker listing probe."""
    bAnswered, processResult = _ftRunDockerCommand(saCommand)
    if not bAnswered or processResult.returncode != 0:
        return (False, False)
    return (True, bool(processResult.stdout.strip()))


def fdictRemoveCampaignEgressResources(sCampaignId):
    """Remove the campaign's proxy and network, proving absence.

    Returns ``{bProxyAbsenceProven, bNetworkAbsenceProven,
    saIndeterminateResources}``. A proven flag is True only when the
    daemon positively enumerated nothing by that name AFTER the
    removal attempt. A daemon that did not answer lands the resource
    in ``saIndeterminateResources`` with its flag False — reported,
    never swallowed, exactly as an unanswered container probe makes a
    settlement inconclusive rather than clean.
    """
    sProxyName = fsComposeProxyContainerName(sCampaignId)
    sNetworkName = fsComposeNetworkName(sCampaignId)
    saIndeterminateResources = []
    _ftRunDockerCommand(["docker", "rm", "-f", sProxyName])
    bProxyAnswered, bProxyPresent = _ftProbeListingCommand(
        ["docker", "ps", "-a", "-q", "--filter", f"name=^{sProxyName}$"])
    if not bProxyAnswered:
        saIndeterminateResources.append(sProxyName)
    _ftRunDockerCommand(["docker", "network", "rm", sNetworkName])
    bNetworkAnswered, bNetworkPresent = _ftProbeListingCommand(
        ["docker", "network", "ls", "-q", "--filter",
         f"name=^{sNetworkName}$"])
    if not bNetworkAnswered:
        saIndeterminateResources.append(sNetworkName)
    return {
        "bProxyAbsenceProven": bProxyAnswered and not bProxyPresent,
        "bNetworkAbsenceProven": bNetworkAnswered and not bNetworkPresent,
        "saIndeterminateResources": saIndeterminateResources,
    }
