"""Live falsification of the Agent Council egress boundary (design 15.6).

Every test here drives real Docker resources: a per-campaign internal
network, the allowlisting CONNECT proxy dual-homed onto the default
bridge, a provider stand-in and a forbidden stand-in on the bridge, and
runner containers sealed by ``flistBuildRunnerNetworkArguments``. The
proxy resolves the one allowlisted hostname from a server-supplied
address map (the stand-in has no public name), which is the same
launch-time parameter a production campaign may use.

Each escape attempt asserts its SPECIFIC failure mode — an HTTP status
plus the proxy's cause text, ``ENETUNREACH`` errno 101 for a routeless
dial, ``socket.gaierror`` for a denied lookup — never a blind "some
exception happened", so a passing test means the boundary refused for
the reason the design claims.
"""

import secrets
import subprocess

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui.agentCouncilEgress import (
    I_PROXY_LISTEN_PORT,
    S_BLACK_HOLE_NAMESERVER,
    fdictBuildRunnerProxyEnvironment,
    fdictRemoveCampaignEgressResources,
    flistBuildRunnerNetworkArguments,
    fsCreateCampaignInternalNetwork,
    fsLaunchAllowlistProxy,
)

pytestmark = pytest.mark.docker_live

S_RUNNER_IMAGE = "python:3.10-slim"
S_PROVIDER_HOSTNAME = "provider-stand-in"
S_FORBIDDEN_HOSTNAME = "forbidden-stand-in"
I_STAND_IN_PORT = 8000
S_PROVIDER_MARKER = "vaibify-provider-marker"
F_CONTAINER_RUN_TIMEOUT_SECONDS = 120.0

# Test-owned snippets executed inside runner containers. Parameterized
# only through argv values this file composes; assertions match the
# structured lines they print.
S_SNIPPET_CONNECT_THROUGH_PROXY = r"""
import os, socket, sys
sProxyAuthority = os.environ["HTTPS_PROXY"].split("//", 1)[1]
sProxyHost, sProxyPort = sProxyAuthority.rsplit(":", 1)
socketProxy = socket.create_connection((sProxyHost, int(sProxyPort)), timeout=10)
socketProxy.sendall(("CONNECT %s HTTP/1.1\r\n\r\n" % sys.argv[1]).encode())
sReply = socketProxy.recv(65536).decode("latin-1")
print("STATUS:" + sReply.split("\r\n")[0])
print("BODY:" + sReply.split("\r\n\r\n", 1)[-1].strip())
if " 200 " in sReply.split("\r\n")[0]:
    socketProxy.sendall(b"GET /marker.txt HTTP/1.1\r\nHost: target\r\nConnection: close\r\n\r\n")
    baTunneled = b""
    while True:
        baChunk = socketProxy.recv(65536)
        if not baChunk:
            break
        baTunneled += baChunk
    print("TUNNELED:" + baTunneled.decode("latin-1").replace("\r\n", "|"))
socketProxy.close()
"""

S_SNIPPET_DIAL_ADDRESS = r"""
import socket, sys
socketDial = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
socketDial.settimeout(5)
try:
    socketDial.connect((sys.argv[1], int(sys.argv[2])))
    print("DIAL-CONNECTED")
except OSError as errorDial:
    print("DIAL-%s:%s" % (type(errorDial).__name__, errorDial.errno))
finally:
    socketDial.close()
"""

S_SNIPPET_DIAL_IPV6 = r"""
import socket, sys
socketDial = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
socketDial.settimeout(5)
try:
    socketDial.connect((sys.argv[1], int(sys.argv[2]), 0, 0))
    print("DIAL-CONNECTED")
except OSError as errorDial:
    print("DIAL-%s:%s" % (type(errorDial).__name__, errorDial.errno))
finally:
    socketDial.close()
"""

S_SNIPPET_RESOLVE_NAME = r"""
import socket, sys
try:
    saAnswer = socket.getaddrinfo(sys.argv[1], 443, socket.AF_INET)
    print("RESOLVE-OK:" + saAnswer[0][4][0])
except socket.gaierror as errorResolve:
    print("RESOLVE-gaierror:%s" % errorResolve.errno)
"""


def fsRunContainerSnippet(saNetworkArguments, dictEnvironment, sSnippet,
                          saArguments):
    """Run a snippet in a throwaway runner container; return its output."""
    saCommand = ["docker", "run", "--rm"]
    saCommand.extend(saNetworkArguments)
    for sKey, sValue in dictEnvironment.items():
        saCommand.extend(["-e", f"{sKey}={sValue}"])
    saCommand.extend([S_RUNNER_IMAGE, "python", "-c", sSnippet])
    saCommand.extend(saArguments)
    processResult = subprocess.run(
        saCommand, capture_output=True, text=True,
        timeout=F_CONTAINER_RUN_TIMEOUT_SECONDS, check=False,
    )
    assert processResult.returncode == 0, (
        f"runner container failed: {processResult.stderr}")
    return processResult.stdout


def fsStartBridgeStandIn(sContainerName):
    """Start an HTTP stand-in on the default bridge; return its address."""
    sServeCommand = (
        "mkdir -p /srv && echo " + S_PROVIDER_MARKER
        + " > /srv/marker.txt && cd /srv && exec python -m http.server "
        + str(I_STAND_IN_PORT)
    )
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", sContainerName,
         S_RUNNER_IMAGE, "sh", "-c", sServeCommand],
        capture_output=True, text=True, timeout=60, check=True,
    )
    processResult = subprocess.run(
        ["docker", "inspect", "-f", "{{.NetworkSettings.IPAddress}}",
         sContainerName],
        capture_output=True, text=True, timeout=30, check=True,
    )
    sAddress = processResult.stdout.strip()
    assert sAddress, f"{sContainerName} has no bridge address"
    return sAddress


def fnRemoveContainerQuietly(sContainerName):
    """Best-effort removal of a test container."""
    subprocess.run(
        ["docker", "rm", "-f", sContainerName],
        capture_output=True, text=True, timeout=30, check=False,
    )


@pytest.fixture(scope="module")
def dictEgressTopology():
    """Stand up the full egress topology once for this module.

    Internal network + proxy allowlisting ONLY the provider stand-in's
    hostname (resolved through the launch-time address map), with the
    provider and forbidden stand-ins on the default bridge.
    """
    fnRequireDaemonReachable()
    sCampaignId = "livetest-" + secrets.token_hex(4)
    sProviderContainer = f"vaibifyEgressLiveProvider-{sCampaignId}"
    sForbiddenContainer = f"vaibifyEgressLiveForbidden-{sCampaignId}"
    fsCreateCampaignInternalNetwork(sCampaignId)
    try:
        sProviderAddress = fsStartBridgeStandIn(sProviderContainer)
        sForbiddenAddress = fsStartBridgeStandIn(sForbiddenContainer)
        sProxyAddress = fsLaunchAllowlistProxy(
            sCampaignId,
            [S_PROVIDER_HOSTNAME],
            iaAllowedPorts=[I_STAND_IN_PORT],
            dictHostnameAddressMap={S_PROVIDER_HOSTNAME: sProviderAddress},
        )
        yield {
            "sCampaignId": sCampaignId,
            "sProxyAddress": sProxyAddress,
            "sProviderAddress": sProviderAddress,
            "sForbiddenAddress": sForbiddenAddress,
            "saRunnerNetworkArguments":
                flistBuildRunnerNetworkArguments(sCampaignId),
            "dictRunnerEnvironment":
                fdictBuildRunnerProxyEnvironment(sProxyAddress),
        }
    finally:
        fnRemoveContainerQuietly(sProviderContainer)
        fnRemoveContainerQuietly(sForbiddenContainer)
        dictRemoval = fdictRemoveCampaignEgressResources(sCampaignId)
        assert dictRemoval["saIndeterminateResources"] == [], (
            "teardown could not prove absence of: "
            f"{dictRemoval['saIndeterminateResources']}"
        )


def fsRunRunnerSnippet(dictEgressTopology, sSnippet, saArguments):
    """Run a snippet inside a sealed runner on the campaign network."""
    return fsRunContainerSnippet(
        dictEgressTopology["saRunnerNetworkArguments"],
        dictEgressTopology["dictRunnerEnvironment"],
        sSnippet, saArguments,
    )


def test_connect_to_allowlisted_host_tunnels_bytes_end_to_end(
        dictEgressTopology):
    """CONNECT to the one allowlisted hostname succeeds and relays."""
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, S_SNIPPET_CONNECT_THROUGH_PROXY,
        [f"{S_PROVIDER_HOSTNAME}:{I_STAND_IN_PORT}"])
    assert "STATUS:HTTP/1.1 200 Connection Established" in sOutput
    assert S_PROVIDER_MARKER in sOutput, (
        f"no provider bytes came back through the tunnel: {sOutput}")


def test_connect_to_forbidden_host_is_refused_by_name(dictEgressTopology):
    """CONNECT to a hostname off the allowlist is refused with the cause."""
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, S_SNIPPET_CONNECT_THROUGH_PROXY,
        [f"{S_FORBIDDEN_HOSTNAME}:{I_STAND_IN_PORT}"])
    assert "STATUS:HTTP/1.1 403 Forbidden" in sOutput
    assert "host not on allowlist" in sOutput
    assert S_PROVIDER_MARKER not in sOutput


def test_connect_to_raw_address_target_is_refused(dictEgressTopology):
    """CONNECT to a numeric target is refused even for the provider's own
    address — a raw-address dial is indistinguishable from an escape."""
    sForbiddenTarget = (
        f"{dictEgressTopology['sForbiddenAddress']}:{I_STAND_IN_PORT}")
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, S_SNIPPET_CONNECT_THROUGH_PROXY,
        [sForbiddenTarget])
    assert "STATUS:HTTP/1.1 403 Forbidden" in sOutput
    assert "raw-address CONNECT target refused" in sOutput
    sProviderTarget = (
        f"{dictEgressTopology['sProviderAddress']}:{I_STAND_IN_PORT}")
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, S_SNIPPET_CONNECT_THROUGH_PROXY,
        [sProviderTarget])
    assert "raw-address CONNECT target refused" in sOutput


def test_non_connect_method_is_refused(dictEgressTopology):
    """A plain proxied GET is answered 405; only CONNECT is served."""
    sSnippet = S_SNIPPET_CONNECT_THROUGH_PROXY.replace(
        "CONNECT %s", "GET http://%s/")
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, sSnippet,
        [f"{S_PROVIDER_HOSTNAME}:{I_STAND_IN_PORT}"])
    assert "STATUS:HTTP/1.1 405 Method Not Allowed" in sOutput
    assert "only CONNECT is served" in sOutput


def test_direct_dial_around_the_proxy_has_no_route(dictEgressTopology):
    """Skipping the proxy dies at the kernel: ENETUNREACH, errno 101.

    Both bridge stand-ins and a public address are dialed directly
    from the sealed runner; the internal network gives the packet
    nowhere to go, so the refusal is immediate and specific — not a
    timeout, not a connection refusal.
    """
    for sAddress in (
            dictEgressTopology["sProviderAddress"],
            dictEgressTopology["sForbiddenAddress"],
            "203.0.113.1",
    ):
        sOutput = fsRunRunnerSnippet(
            dictEgressTopology, S_SNIPPET_DIAL_ADDRESS,
            [sAddress, str(I_STAND_IN_PORT)])
        assert "DIAL-OSError:101" in sOutput, (
            f"direct dial to {sAddress} did not die with ENETUNREACH: "
            f"{sOutput}")
        assert "DIAL-CONNECTED" not in sOutput


def test_external_name_resolution_is_denied_in_the_runner(
        dictEgressTopology):
    """The exfiltration-by-lookup channel is closed: gaierror, never an
    address. This is the Phase 0 hazard falsification — the runner's
    resolver upstream is the TEST-NET-1 black-hole, so even a daemon
    generation that forwards external queries for internal networks
    would forward them to an address that can never answer."""
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, S_SNIPPET_RESOLVE_NAME, ["example.com"])
    assert "RESOLVE-gaierror:" in sOutput, (
        f"external name resolution did not fail as gaierror: {sOutput}")
    assert "RESOLVE-OK" not in sOutput


def test_black_hole_nameserver_denies_resolution_where_forwarding_is_live():
    """Prove the DNS-denial MECHANISM, not just today's daemon default.

    On an ordinary (non-internal) user-defined network the embedded
    resolver forwards external queries — the live hazard. The same
    network plus the black-hole dns arguments must turn that success
    into gaierror. Skips honestly when the host itself has no working
    external DNS, because then the hazard cannot be demonstrated.
    """
    fnRequireDaemonReachable()
    sNetworkName = "vaibifyEgressLiveOpenNet-" + secrets.token_hex(4)
    subprocess.run(
        ["docker", "network", "create", sNetworkName],
        capture_output=True, text=True, timeout=30, check=True,
    )
    try:
        sOutputForwarding = fsRunContainerSnippet(
            ["--network", sNetworkName], {}, S_SNIPPET_RESOLVE_NAME,
            ["example.com"])
        if "RESOLVE-OK" not in sOutputForwarding:
            pytest.skip(
                "this host resolves no external names at all, so the "
                "forwarding hazard cannot be demonstrated here: "
                + sOutputForwarding.strip()
            )
        sOutputDenied = fsRunContainerSnippet(
            ["--network", sNetworkName,
             "--dns", S_BLACK_HOLE_NAMESERVER,
             "--dns-option", "timeout:1", "--dns-option", "attempts:1"],
            {}, S_SNIPPET_RESOLVE_NAME, ["example.com"])
        assert "RESOLVE-gaierror:" in sOutputDenied, (
            "the black-hole nameserver did not deny resolution on a "
            f"forwarding network: {sOutputDenied}")
        assert "RESOLVE-OK" not in sOutputDenied
    finally:
        subprocess.run(
            ["docker", "network", "rm", sNetworkName],
            capture_output=True, text=True, timeout=30, check=False,
        )


def test_ipv6_dial_has_no_route(dictEgressTopology):
    """A v6 dial from the sealed runner dies with ENETUNREACH."""
    sOutput = fsRunRunnerSnippet(
        dictEgressTopology, S_SNIPPET_DIAL_IPV6,
        ["2001:db8::1", "443"])
    assert "DIAL-OSError:101" in sOutput, (
        f"IPv6 dial did not die with ENETUNREACH: {sOutput}")
    assert "DIAL-CONNECTED" not in sOutput


def test_sandbox_network_none_reaches_nothing(dictEgressTopology):
    """A network-none sandbox cannot reach even the proxy, and resolves
    nothing — every dial ENETUNREACH, every lookup gaierror."""
    sOutput = fsRunContainerSnippet(
        ["--network", "none"], {}, S_SNIPPET_DIAL_ADDRESS,
        [dictEgressTopology["sProxyAddress"], str(I_PROXY_LISTEN_PORT)])
    assert "DIAL-OSError:101" in sOutput
    assert "DIAL-CONNECTED" not in sOutput
    sOutput = fsRunContainerSnippet(
        ["--network", "none"], {}, S_SNIPPET_RESOLVE_NAME, ["example.com"])
    assert "RESOLVE-gaierror:" in sOutput
    assert "RESOLVE-OK" not in sOutput


def test_teardown_proves_absence_and_is_idempotent():
    """Removal reports PROVEN absence, and a second pass still proves it.

    A dedicated campaign is built and torn down so the assertion covers
    the removal path itself, not the shared fixture's afterglow. Both
    proven flags must be True with nothing indeterminate; running the
    removal again must re-prove absence from a positive daemon answer,
    not from a swallowed error.
    """
    fnRequireDaemonReachable()
    sCampaignId = "livetear-" + secrets.token_hex(4)
    fsCreateCampaignInternalNetwork(sCampaignId)
    fsLaunchAllowlistProxy(
        sCampaignId, [S_PROVIDER_HOSTNAME],
        iaAllowedPorts=[I_STAND_IN_PORT],
        dictHostnameAddressMap={S_PROVIDER_HOSTNAME: "203.0.113.7"},
    )
    dictFirstRemoval = fdictRemoveCampaignEgressResources(sCampaignId)
    assert dictFirstRemoval == {
        "bProxyAbsenceProven": True,
        "bNetworkAbsenceProven": True,
        "saIndeterminateResources": [],
    }
    dictSecondRemoval = fdictRemoveCampaignEgressResources(sCampaignId)
    assert dictSecondRemoval["bProxyAbsenceProven"] is True
    assert dictSecondRemoval["bNetworkAbsenceProven"] is True
    assert dictSecondRemoval["saIndeterminateResources"] == []
