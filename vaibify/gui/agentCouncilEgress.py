"""Per-campaign egress boundary for Agent Council runners (design section 9.7).

A council runner may reach exactly one destination family: its own
provider's API endpoints. The boundary is built out of three pieces,
each created per campaign and destroyed with the campaign. Since
remediation R4 this module holds ONLY the pure half — the server-owned
proxy script, the allowlist and campaign-id validation, the resource
NAME composition, the proxy argv/environment composition, and the
runner's DNS wiring; every Docker-SDK call that realizes the boundary
(network create/remove, proxy create/start/inspect, script copy-in,
bridge attach, absence-proving teardown) lives in
``agentCouncilDockerGateway``, the single council SDK authority
(``tests/testCouncilGatewayAuthority.py`` fails the build if SDK reach
reappears here). The three pieces:

1. an INTERNAL Docker network (``networks.create(..., internal=True)``),
   which the kernel gives no route off the host — every direct dial to
   a bridge, external, or IPv6 address fails immediately with
   ``ENETUNREACH`` (errno 101, verified live on Docker 28.4.0);
2. an allowlisting HTTP CONNECT proxy — a server-owned Python-stdlib
   script (``S_CONNECT_PROXY_SCRIPT``) run in a digest-pinned
   ``S_PROXY_IMAGE`` container attached to BOTH the internal network
   and the default bridge, created by the gateway with the runner's own
   hardened posture (unprivileged user, all capabilities dropped,
   no-new-privileges, memory/CPU/PID bounds). It serves only
   ``CONNECT`` to hostnames on a fixed,
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

Teardown follows the prove-absence discipline of the gateway's
container destruction: after removal, the daemon is asked to inspect
the resources by name, and a daemon that did not answer with a positive
``NotFound`` yields an INDETERMINATE report, never a claim of absence
(``agentCouncilDockerGateway.fdictRemoveCampaignEgressResources``).
"""

import ipaddress
import re

from vaibify.gui import agentCouncilRunner

__all__ = [
    "EgressSetupError",
    "I_PROXY_LISTEN_PORT",
    "S_BLACK_HOLE_NAMESERVER",
    "S_CONNECT_PROXY_SCRIPT",
    "S_PROXY_IMAGE",
    "S_NETWORK_NAME_PREFIX",
    "S_PROXY_NAME_PREFIX",
    "S_PROXY_READY_LINE",
    "fbaBuildProxyScriptTarball",
    "fdictBuildRunnerProxyEnvironment",
    "flistBuildRunnerNetworkArguments",
    "flistComposeProxyCommand",
    "fnValidateAllowlistOrRaise",
    "fnValidateCampaignIdOrRaise",
    "fsComposeNetworkName",
    "fsComposeProxyContainerName",
]


class EgressSetupError(RuntimeError):
    """A step of the egress-boundary build or teardown failed."""


# Digest-pinned (remediation R4) so the proxy the boundary trusts is the
# exact reviewed image, not whatever a mutable tag points at today. The
# plain tag is python:3.10-slim.
S_PROXY_IMAGE = (
    "python:3.10-slim@sha256:"
    "a78e4529630cfe8c5199cafd6e0c28ee1579a13f86274396d8b6b2d80367aa3a"
)
I_PROXY_LISTEN_PORT = 8888
S_PROXY_SCRIPT_DIRECTORY = "vaibifyEgress"
S_PROXY_SCRIPT_BASENAME = "vaibifyEgressProxy.py"
S_PROXY_SCRIPT_CONTAINER_PATH = (
    "/" + S_PROXY_SCRIPT_DIRECTORY + "/" + S_PROXY_SCRIPT_BASENAME)

# RFC 5737 TEST-NET-1: reserved for documentation, never routed, so a
# resolver forwarding only to it can never receive an answer.
S_BLACK_HOLE_NAMESERVER = "192.0.2.1"

S_NETWORK_NAME_PREFIX = "vaibifyCouncilEgress"
S_PROXY_NAME_PREFIX = "vaibifyCouncilProxy"

# A campaign id is server-minted. Validating it before it reaches a
# resource name keeps a crafted value from smuggling a stray token.
_RE_CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_RE_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

F_PROXY_READY_DEADLINE_SECONDS = 20.0
F_PROXY_READY_POLL_SECONDS = 0.25

# The line the proxy prints once its listening socket is bound; the
# gateway's launch blocks until it appears so a caller never wires a
# runner to a proxy that is still importing. The listen port is above
# 1024, so the bind succeeds for the unprivileged proxy user.
S_PROXY_READY_LINE = "vaibify-egress: listening"

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
    """Refuse a campaign id that could smuggle a token into a resource name."""
    if not isinstance(sCampaignId, str) or not _RE_CAMPAIGN_ID.match(
            sCampaignId):
        raise EgressSetupError(
            "campaign id must be 1-64 characters of letters, digits and "
            f"hyphens, starting with a letter or digit; got {sCampaignId!r}"
        )


def fsComposeNetworkName(sCampaignId):
    """Return the per-campaign internal network name."""
    fnValidateCampaignIdOrRaise(sCampaignId)
    return f"{S_NETWORK_NAME_PREFIX}-{sCampaignId}"


def fsComposeProxyContainerName(sCampaignId):
    """Return the per-campaign proxy container name."""
    fnValidateCampaignIdOrRaise(sCampaignId)
    return f"{S_PROXY_NAME_PREFIX}-{sCampaignId}"


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


def fbaBuildProxyScriptTarball():
    """Build the ownership-stamped tarball carrying the proxy script.

    Reuses the runner's ownership-stamping one-file tarball builder, so
    the single tar-entry discipline in the package stays in one place
    and ``tarfile.TarInfo``'s native uid/gid default of 0 can never
    leak — the 1000-stamped entries are what let the NON-ROOT proxy
    read its own script. The gateway extracts the archive at ``/``, so
    the script lands at ``S_PROXY_SCRIPT_CONTAINER_PATH``.
    """
    return agentCouncilRunner.fbaBuildStampedFileTarball(
        S_PROXY_SCRIPT_DIRECTORY, S_PROXY_SCRIPT_BASENAME,
        S_CONNECT_PROXY_SCRIPT.encode("utf-8"),
        iFileMode=0o644, iDirectoryMode=0o755,
    )


def flistComposeProxyCommand(saAllowedHostnames, iaAllowedPorts,
                             dictHostnameAddressMap):
    """Compose the argv the proxy container runs, from server-owned values."""
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
    return saCommand


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
