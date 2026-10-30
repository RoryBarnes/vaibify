"""The allowlisting egress proxy: one audited script for every sealed container.

A sealed container (an Agent Council runner today, an allowlist-mode
project container next) joins an INTERNAL Docker network, which the
kernel gives no route off the host, and reaches the outside world only
through a CONNECT proxy that sits on that network and on the default
bridge. The proxy serves ``CONNECT`` to hostnames on a fixed,
server-supplied allowlist and nothing else. This module is the pure
half: the proxy script, the allowlist validation, the argv and
environment composition, and the ``docker run`` arguments that seal a
container's network; every Docker-SDK call that realizes a boundary
lives with the authority that owns the containers
(``agentCouncilDockerGateway`` for the council).

What the script refuses, and why each refusal exists:

- a non-``CONNECT`` request, a target that is not ``host:port``, a port
  off the allowed set: the proxy is a tunnel to named services, never a
  general web proxy;
- a RAW ADDRESS target: a name is what the allowlist judges, and an
  address would bypass it;
- a host off the allowlist;
- an allowed name that RESOLVES to a non-public address (loopback,
  private, link-local, carrier-grade NAT, multicast, reserved), unless
  the server pinned that name to an address: a name an attacker
  controls could otherwise point at the host, the VM, the LAN or the
  cloud metadata service, and the proxy would dial it from its bridge
  side where those are reachable. A pinned address is a server
  decision and is dialed as given;
- a tunnel past the concurrency cap, so one client cannot exhaust the
  proxy's descriptors.

The script binds ONLY its address on the internal network when told
that network's subnet (``--bind-cidr``): bound to every interface, it
would also serve the default bridge, where any container on the daemon
could use it. Every ``CONNECT`` is logged as one line the hub can read
back (``vaibify-egress: CONNECT host:port allowed|refused reason``).

Name resolution happens in the proxy, on its bridge side. The sealed
container's own resolver is black-holed (``flistBuildBlackHoleDnsArguments``):
Docker's embedded resolver forwards external queries from the daemon's
namespace, and whether it refuses to on an internal network is
daemon-version behavior, not a contract (measured on Engine 28.4.0:
refused on an internal network, forwarded on an ordinary one). With
its only upstream the RFC 5737 TEST-NET-1 address, which is never
routed, an external lookup fails in about a second instead of
resolving or leaking the queried name. ``--dns 127.0.0.1`` would NOT
work: Docker substitutes public DNS for a localhost resolver.
"""

__all__ = [
    "EgressSetupError",
    "F_PROXY_READY_DEADLINE_SECONDS",
    "F_PROXY_READY_POLL_SECONDS",
    "I_PROXY_LISTEN_PORT",
    "I_PROXY_MAXIMUM_TUNNELS",
    "S_BLACK_HOLE_NAMESERVER",
    "S_CONNECT_LOG_PREFIX",
    "S_CONNECT_PROXY_SCRIPT",
    "S_PROXY_IMAGE",
    "S_PROXY_READY_LINE",
    "S_PROXY_SCRIPT_BASENAME",
    "S_PROXY_SCRIPT_CONTAINER_PATH",
    "S_PROXY_SCRIPT_DIRECTORY",
    "fdictBuildProxyEnvironment",
    "fdictParseConnectLogLine",
    "flistBuildBlackHoleDnsArguments",
    "flistBuildSealedNetworkArguments",
    "flistComposeProxyCommand",
    "fnValidateAllowlistOrRaise",
]

import ipaddress
import re


class EgressSetupError(RuntimeError):
    """A step of the egress-boundary build or teardown failed."""


# Digest-pinned so the proxy the boundary trusts is the exact reviewed
# image, not whatever a mutable tag points at today. The plain tag is
# python:3.10-slim.
S_PROXY_IMAGE = (
    "python:3.10-slim@sha256:"
    "a78e4529630cfe8c5199cafd6e0c28ee1579a13f86274396d8b6b2d80367aa3a"
)
I_PROXY_LISTEN_PORT = 8888
I_PROXY_MAXIMUM_TUNNELS = 256
S_PROXY_SCRIPT_DIRECTORY = "vaibifyEgress"
S_PROXY_SCRIPT_BASENAME = "vaibifyEgressProxy.py"
S_PROXY_SCRIPT_CONTAINER_PATH = (
    "/" + S_PROXY_SCRIPT_DIRECTORY + "/" + S_PROXY_SCRIPT_BASENAME)

# RFC 5737 TEST-NET-1: reserved for documentation, never routed, so a
# resolver forwarding only to it can never receive an answer.
S_BLACK_HOLE_NAMESERVER = "192.0.2.1"

_RE_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

F_PROXY_READY_DEADLINE_SECONDS = 20.0
F_PROXY_READY_POLL_SECONDS = 0.25

# The line the proxy prints once its listening socket is bound; a launch
# blocks until it appears so a caller never wires a container to a proxy
# that is still importing. The listen port is above 1024, so the bind
# succeeds for an unprivileged proxy user.
S_PROXY_READY_LINE = "vaibify-egress: listening"
# Every CONNECT decision is one line beginning with this, for the hub.
S_CONNECT_LOG_PREFIX = "vaibify-egress: CONNECT "

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
import socket
import sys

I_HANDSHAKE_TIMEOUT_SECONDS = 10
I_UPSTREAM_DIAL_TIMEOUT_SECONDS = 10
I_RELAY_CHUNK_BYTES = 65536
I_MAXIMUM_HEADER_LINES = 100
I_MAXIMUM_CONCURRENT_TUNNELS = 256

SET_ALLOWED_HOSTNAMES = set()
SET_ALLOWED_PORTS = set()
DICT_HOSTNAME_ADDRESS_MAP = {}
DICT_STATE = {"iOpenTunnels": 0}
# The one (host, port) a sealed container may reach on the host: its hub.
# Resolved through this container's own hosts file (the host-gateway
# entry), exempt from the non-public refusal because the gateway IS
# non-public, and served for plain HTTP as well as CONNECT because the
# hub speaks http:// and a client given HTTP_PROXY sends it that way.
DICT_HUB_GATEWAY = {"sHost": "", "iPort": 0}
NETWORK_CARRIER_GRADE_NAT = ipaddress.ip_network("100.64.0.0/10")


class NonPublicResolution(Exception):
    """An allowed name resolved to an address the proxy must not dial."""


def fbLooksLikeRawAddress(sHost):
    sCandidate = sHost.strip("[]")
    try:
        ipaddress.ip_address(sCandidate)
        return True
    except ValueError:
        return False


def fbIsNonPublicAddress(sAddress):
    addressTarget = ipaddress.ip_address(sAddress)
    return (
        addressTarget.is_loopback or addressTarget.is_private
        or addressTarget.is_link_local or addressTarget.is_multicast
        or addressTarget.is_reserved or addressTarget.is_unspecified
        or (addressTarget.version == 4
            and addressTarget in NETWORK_CARRIER_GRADE_NAT)
    )


def fsSelectBindAddress(sBindCidr):
    if not sBindCidr:
        return "0.0.0.0"
    networkBind = ipaddress.ip_network(sBindCidr, strict=False)
    for tInfo in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        sCandidate = tInfo[4][0]
        if ipaddress.ip_address(sCandidate) in networkBind:
            return sCandidate
    raise SystemExit("vaibify-egress: no interface address inside " + sBindCidr)


def fbIsHubGateway(sHost, iPort):
    return bool(DICT_HUB_GATEWAY["sHost"]) and (sHost, iPort) == (
        DICT_HUB_GATEWAY["sHost"], DICT_HUB_GATEWAY["iPort"])


def fsSelectBridgeAddress(sBindCidr):
    if not sBindCidr:
        return "0.0.0.0"
    networkBind = ipaddress.ip_network(sBindCidr, strict=False)
    for tInfo in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        sCandidate = tInfo[4][0]
        if ipaddress.ip_address(sCandidate) not in networkBind:
            return sCandidate
    return "0.0.0.0"


def fnLogDecision(sHost, iPort, sDecision, sReason=""):
    print("vaibify-egress: CONNECT " + sHost + ":" + str(iPort) + " " + sDecision
          + (" " + sReason if sReason else ""), flush=True)


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
    if fbIsHubGateway(sHost, iPort):
        return listAnswers[0][4][0]
    for tAnswer in listAnswers:
        sAnswer = tAnswer[4][0]
        if fbIsNonPublicAddress(sAnswer):
            raise NonPublicResolution(sAnswer)
    return listAnswers[0][4][0]


async def fsJudgeTarget(sHostLower, iPort):
    if fbLooksLikeRawAddress(sHostLower):
        return "raw-address CONNECT target refused"
    if DICT_HUB_GATEWAY["sHost"] and sHostLower == DICT_HUB_GATEWAY["sHost"]:
        if iPort != DICT_HUB_GATEWAY["iPort"]:
            return "only the hub's port is served on the host"
    elif sHostLower not in SET_ALLOWED_HOSTNAMES:
        return "host not on allowlist"
    elif iPort not in SET_ALLOWED_PORTS:
        return "port not on allowlist"
    if DICT_STATE["iOpenTunnels"] >= I_MAXIMUM_CONCURRENT_TUNNELS:
        return "too many open tunnels"
    return ""


async def fnRelayTunnel(readerClient, writerClient, readerUpstream, writerUpstream):
    DICT_STATE["iOpenTunnels"] += 1
    try:
        writerClient.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writerClient.drain()
        await asyncio.gather(
            fnPumpOneDirection(readerClient, writerUpstream),
            fnPumpOneDirection(readerUpstream, writerClient),
        )
    finally:
        DICT_STATE["iOpenTunnels"] -= 1


def ftParseHubRequestTarget(sUrl):
    """Return ``(sHost, iPort, sOriginForm)`` of an absolute http URL, or None."""
    if not sUrl.lower().startswith("http://"):
        return None
    sAuthority, sSeparator, sRest = sUrl[len("http://"):].partition("/")
    sHost, sColon, sPort = sAuthority.rpartition(":")
    if not sColon:
        sHost, sPort = sAuthority, "80"
    if not sPort.isdigit():
        return None
    return sHost.lower(), int(sPort), "/" + sRest


async def fnForwardHubRequest(readerClient, writerClient, saParts, listHeaderLines):
    """Serve one plain-HTTP request addressed to the hub, and only the hub."""
    tTarget = ftParseHubRequestTarget(saParts[1])
    if tTarget is None or not fbIsHubGateway(tTarget[0], tTarget[1]):
        await fnRefuse(writerClient, "HTTP/1.1 405 Method Not Allowed",
                       "only CONNECT is served")
        return
    sHost, iPort, sOriginForm = tTarget
    try:
        sAddress = await fsResolveAllowlistedHost(sHost, iPort)
        readerUpstream, writerUpstream = await asyncio.wait_for(
            asyncio.open_connection(sAddress, iPort),
            I_UPSTREAM_DIAL_TIMEOUT_SECONDS,
        )
    except (OSError, asyncio.TimeoutError, NonPublicResolution):
        fnLogDecision(sHost, iPort, "refused", "upstream dial failed")
        await fnRefuse(writerClient, "HTTP/1.1 502 Bad Gateway",
                       "upstream dial failed")
        return
    fnLogDecision(sHost, iPort, "allowed", "hub http")
    sVersion = saParts[2] if len(saParts) > 2 else "HTTP/1.1"
    writerUpstream.write((saParts[0] + " " + sOriginForm + " " + sVersion
                          + "\r\n").encode("latin-1"))
    for baHeaderLine in listHeaderLines:
        if not baHeaderLine.lower().startswith(b"proxy-"):
            writerUpstream.write(baHeaderLine)
    writerUpstream.write(b"\r\n")
    await writerUpstream.drain()
    DICT_STATE["iOpenTunnels"] += 1
    try:
        await asyncio.gather(
            fnPumpOneDirection(readerClient, writerUpstream),
            fnPumpOneDirection(readerUpstream, writerClient),
        )
    finally:
        DICT_STATE["iOpenTunnels"] -= 1


async def fnServeOneClient(readerClient, writerClient):
    try:
        baRequestLine = await asyncio.wait_for(
            readerClient.readline(), I_HANDSHAKE_TIMEOUT_SECONDS)
        saParts = baRequestLine.decode("latin-1").split()
        listHeaderLines = []
        for _ in range(I_MAXIMUM_HEADER_LINES):
            baHeaderLine = await asyncio.wait_for(
                readerClient.readline(), I_HANDSHAKE_TIMEOUT_SECONDS)
            if baHeaderLine in (b"\r\n", b"\n", b""):
                break
            listHeaderLines.append(baHeaderLine)
        if len(saParts) < 2:
            await fnRefuse(writerClient, "HTTP/1.1 400 Bad Request",
                           "no request line")
            return
        if saParts[0] != "CONNECT":
            await fnForwardHubRequest(readerClient, writerClient, saParts,
                                      listHeaderLines)
            return
        sHost, sSeparator, sPort = saParts[1].rpartition(":")
        if not sSeparator or not sPort.isdigit():
            await fnRefuse(writerClient, "HTTP/1.1 400 Bad Request",
                           "CONNECT target must be host:port")
            return
        iPort = int(sPort)
        sHostLower = sHost.lower()
        sRefusal = await fsJudgeTarget(sHostLower, iPort)
        if sRefusal:
            fnLogDecision(sHostLower, iPort, "refused", sRefusal)
            sStatus = ("HTTP/1.1 503 Service Unavailable"
                       if sRefusal == "too many open tunnels"
                       else "HTTP/1.1 403 Forbidden")
            await fnRefuse(writerClient, sStatus, sRefusal)
            return
        try:
            sAddress = await fsResolveAllowlistedHost(sHostLower, iPort)
        except NonPublicResolution as error:
            fnLogDecision(sHostLower, iPort, "refused",
                          "resolves to a non-public address " + str(error))
            await fnRefuse(writerClient, "HTTP/1.1 403 Forbidden",
                           "host resolves to a non-public address")
            return
        try:
            readerUpstream, writerUpstream = await asyncio.wait_for(
                asyncio.open_connection(sAddress, iPort),
                I_UPSTREAM_DIAL_TIMEOUT_SECONDS,
            )
        except (OSError, asyncio.TimeoutError):
            fnLogDecision(sHostLower, iPort, "refused", "upstream dial failed")
            await fnRefuse(writerClient, "HTTP/1.1 502 Bad Gateway",
                           "upstream dial failed")
            return
        fnLogDecision(sHostLower, iPort, "allowed")
        await fnRelayTunnel(readerClient, writerClient, readerUpstream, writerUpstream)
    except (asyncio.TimeoutError, ConnectionError, UnicodeDecodeError):
        pass
    finally:
        try:
            writerClient.close()
        except Exception:
            pass


def ffnForwardTo(sTargetHost, iTargetPort):
    """Return a client handler relaying raw TCP to one target, resolved per connection."""
    async def fnForwardOneClient(readerClient, writerClient):
        try:
            loopRunning = asyncio.get_running_loop()
            listAnswers = await asyncio.wait_for(
                loopRunning.getaddrinfo(sTargetHost, iTargetPort),
                I_UPSTREAM_DIAL_TIMEOUT_SECONDS)
            readerUpstream, writerUpstream = await asyncio.wait_for(
                asyncio.open_connection(listAnswers[0][4][0], iTargetPort),
                I_UPSTREAM_DIAL_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError):
            writerClient.close()
            return
        await asyncio.gather(
            fnPumpOneDirection(readerClient, writerUpstream),
            fnPumpOneDirection(readerUpstream, writerClient),
        )

    return fnForwardOneClient


async def fnRunProxyForever(iListenPort, sBindCidr, listForwards):
    sBindAddress = fsSelectBindAddress(sBindCidr)
    sBridgeAddress = fsSelectBridgeAddress(sBindCidr)
    serverProxy = await asyncio.start_server(
        fnServeOneClient, host=sBindAddress, port=iListenPort)
    # Published ports: the forwards listen on the bridge side, where
    # Docker's port publishing lands, and relay to the sealed container.
    listForwardServers = [
        await asyncio.start_server(
            ffnForwardTo(sTargetHost, iTargetPort), host=sBridgeAddress,
            port=iListenHere)
        for iListenHere, sTargetHost, iTargetPort in listForwards
    ]
    print("vaibify-egress: listening on", sBindAddress + ":" + str(iListenPort),
          flush=True)
    async with serverProxy:
        for serverForward in listForwardServers:
            await serverForward.start_serving()
        await serverProxy.serve_forever()


def flistParseForwards(sForwards):
    """Parse ``PORT=HOST:PORT,...`` into ``(iListen, sHost, iPort)`` triples."""
    listForwards = []
    for sEntry in sForwards.split(","):
        if "=" not in sEntry:
            continue
        sListen, sTarget = sEntry.split("=", 1)
        sTargetHost, sColon, sTargetPort = sTarget.rpartition(":")
        if sColon and sListen.strip().isdigit() and sTargetPort.isdigit():
            listForwards.append((int(sListen), sTargetHost.strip().lower(),
                                 int(sTargetPort)))
    return listForwards


def main():
    parserArguments = argparse.ArgumentParser()
    parserArguments.add_argument("--listen-port", type=int, required=True)
    parserArguments.add_argument("--allowlist", required=True)
    parserArguments.add_argument("--allowed-ports", default="443")
    parserArguments.add_argument("--address-map", default="")
    parserArguments.add_argument("--bind-cidr", default="")
    parserArguments.add_argument("--hub-gateway", default="")
    parserArguments.add_argument("--forward", default="")
    namespaceArguments = parserArguments.parse_args()
    if namespaceArguments.hub_gateway:
        sHubHost, _sColon, sHubPort = namespaceArguments.hub_gateway.rpartition(":")
        DICT_HUB_GATEWAY["sHost"] = sHubHost.strip().lower()
        DICT_HUB_GATEWAY["iPort"] = int(sHubPort)
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
        asyncio.run(fnRunProxyForever(
            namespaceArguments.listen_port, namespaceArguments.bind_cidr,
            flistParseForwards(namespaceArguments.forward)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
'''


def fnValidateAllowlistOrRaise(saAllowedHostnames, iaAllowedPorts,
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


def flistComposeProxyCommand(saAllowedHostnames, iaAllowedPorts,
                             dictHostnameAddressMap, sBindCidr="",
                             sHubGateway="", listForwards=()):
    """Compose the argv the proxy container runs, from server-owned values.

    ``sBindCidr`` is the internal network's subnet; given, the proxy
    binds only its address on that network. Validated as a network
    here, so a value that is not one never reaches the script's argv.
    ``sHubGateway`` is the one ``host:port`` on the host a sealed
    container may reach (its hub); ``listForwards`` are
    ``(iListen, sTargetHost, iTargetPort)`` triples the proxy relays
    from its bridge side, for the container's published ports.
    """
    saCommand = [
        "python", S_PROXY_SCRIPT_CONTAINER_PATH,
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
    if sBindCidr:
        saCommand.extend([
            "--bind-cidr", str(ipaddress.ip_network(sBindCidr, strict=False)),
        ])
    if sHubGateway:
        sHubHost, _sColon, sHubPort = sHubGateway.rpartition(":")
        if not _RE_HOSTNAME.match(sHubHost) or not sHubPort.isdigit():
            raise EgressSetupError(f"hub gateway {sHubGateway!r} is not host:port")
        saCommand.extend(["--hub-gateway", f"{sHubHost.lower()}:{int(sHubPort)}"])
    if listForwards:
        for iListen, sTargetHost, iTargetPort in listForwards:
            if not _RE_HOSTNAME.match(sTargetHost):
                raise EgressSetupError(f"forward target {sTargetHost!r} is not a hostname")
        saCommand.extend(["--forward", ",".join(
            f"{int(iListen)}={sTargetHost}:{int(iTargetPort)}"
            for iListen, sTargetHost, iTargetPort in listForwards)])
    return saCommand


def fdictBuildProxyEnvironment(sProxyInternalAddress,
                               iProxyPort=I_PROXY_LISTEN_PORT):
    """Return the proxy environment a sealed container receives.

    Both spellings are provided because HTTP clients disagree about
    case. The value uses the proxy's numeric internal address, so the
    container can use it without any working name resolution.
    """
    sProxyUniformResourceLocator = (
        f"http://{sProxyInternalAddress}:{iProxyPort}")
    return {
        "HTTPS_PROXY": sProxyUniformResourceLocator,
        "HTTP_PROXY": sProxyUniformResourceLocator,
        "https_proxy": sProxyUniformResourceLocator,
        "http_proxy": sProxyUniformResourceLocator,
    }


def flistBuildBlackHoleDnsArguments():
    """Return the ``docker run`` arguments that black-hole a container's resolver."""
    return [
        "--dns", S_BLACK_HOLE_NAMESERVER,
        "--dns-option", "timeout:1",
        "--dns-option", "attempts:1",
    ]


def flistBuildSealedNetworkArguments(sNetworkName):
    """Return the ``docker run`` arguments that seal a container's network.

    ``--network``: the internal network only, so direct and IPv6 dials
    die with ``ENETUNREACH``; then the black-holed resolver.
    """
    return ["--network", sNetworkName] + flistBuildBlackHoleDnsArguments()


def fdictParseConnectLogLine(sLine):
    """Parse one proxy CONNECT log line; ``None`` for any other line.

    Returns ``{"sHost", "iPort", "bAllowed", "sReason"}``. The hub reads
    the proxy's log for the dashboard's list of refused destinations.
    """
    if not sLine.startswith(S_CONNECT_LOG_PREFIX):
        return None
    listParts = sLine[len(S_CONNECT_LOG_PREFIX):].strip().split(" ", 2)
    if len(listParts) < 2 or listParts[1] not in ("allowed", "refused"):
        return None
    sHost, sSeparator, sPort = listParts[0].rpartition(":")
    if not sSeparator or not sPort.isdigit():
        return None
    return {
        "sHost": sHost, "iPort": int(sPort),
        "bAllowed": listParts[1] == "allowed",
        "sReason": listParts[2] if len(listParts) == 3 else "",
    }
