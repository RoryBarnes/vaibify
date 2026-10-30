"""Allowlist mode against a real daemon: the seal holds and the proxy judges.

A real sealed network and proxy sidecar are made through the production
module, with a tiny HTTP server on this host standing in for the hub. A
throwaway client container on the sealed network then proves, with raw
sockets and no proxy variables, that it cannot reach the daemon host's
bridge address at all (``ENETUNREACH``: the isolated gateway mode gives
it no route), and, through the proxy, that an allowed host tunnels, an
unlisted host and a raw address are refused, the hub's port is served
for plain HTTP and CONNECT alike, and another port on the host is
refused. The negative control shows an ordinary internal network DOES
get a gateway, so the isolated option is what the seal rests on.

Live-daemon convention (``testDockerConnectionLive``): skipped without
a daemon unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one. Needs
Engine 28 (the isolated gateway mode); an older engine skips with the
module's own refusal as the reason.
"""

import http.server
import secrets
import socket
import threading
from types import SimpleNamespace

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker import egressProxy, projectEgress

pytestmark = pytest.mark.docker_live

S_CLIENT_IMAGE = "python:3.10-slim"
S_PROBE = r"""
import os, socket, sys
sTarget = sys.argv[1]
def fsConnect(sHost, iPort, bViaProxy):
    if bViaProxy:
        sProxy = os.environ["HTTPS_PROXY"].split("//", 1)[1]
        s = socket.create_connection((sProxy.split(":")[0], int(sProxy.split(":")[1])), timeout=15)
        s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % (sHost, iPort)).encode())
        return s.recv(65536).decode("latin-1").split("\r\n")[0]
    try:
        socket.create_connection((sHost, iPort), timeout=5).close()
        return "CONNECTED"
    except OSError as error:
        return "OSERROR " + str(error.errno) + " " + type(error).__name__
if sTarget == "hub-http":
    import urllib.request
    print(urllib.request.urlopen(sys.argv[2], timeout=15).read().decode())
else:
    sHost, sPort, sVia = sTarget.rsplit(":", 2)
    print(fsConnect(sHost, int(sPort), sVia == "proxy"))
"""


class _HubStandIn(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- http.server's method name
        baBody = b"hub-stand-in " + self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(baBody)))
        self.end_headers()
        self.wfile.write(baBody)

    def log_message(self, sFormat, *listArguments):
        return


@pytest.fixture(scope="module")
def dictLive():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    serverHub = http.server.HTTPServer(("0.0.0.0", 0), _HubStandIn)
    threadHub = threading.Thread(target=serverHub.serve_forever, daemon=True)
    threadHub.start()
    iHubPort = serverHub.server_address[1]
    sUuid = secrets.token_hex(16)
    config = SimpleNamespace(
        sProjectName=f"vaibifyEgressLive{sUuid[:8]}", listPorts=[],
        listRepositories=[], listAllowedHosts=["pypi.org"], sPackageManager="pip",
        features=SimpleNamespace(bClaude=False, bCodex=False, bGemini=False,
                                 bAntigravity=False, bOpenCode=False, bCline=False,
                                 bOpenHands=False, bPi=False),
    )
    try:
        dictEgress = projectEgress.fdictPrepareProjectEgress(config, sUuid, iHubPort)
    except egressProxy.EgressSetupError as error:
        serverHub.shutdown()
        if "28.0.0" in str(error):
            pytest.skip(str(error))
        raise
    try:
        clientDocker.images.get(S_CLIENT_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_CLIENT_IMAGE)
    containerClient = clientDocker.containers.run(
        S_CLIENT_IMAGE, ["sleep", "600"], detach=True, labels=fdictLabels(),
        name=config.sProjectName, network=dictEgress["sNetworkName"],
        dns=[egressProxy.S_BLACK_HOLE_NAMESERVER],
        environment=egressProxy.fdictBuildProxyEnvironment(dictEgress["sProxyAddress"]),
    )
    try:
        yield {"container": containerClient, "iHubPort": iHubPort, "sUuid": sUuid,
               "clientDocker": clientDocker, "dictEgress": dictEgress}
    finally:
        containerClient.remove(force=True)
        projectEgress.fnReleaseProjectEgressQuietly(sUuid)
        serverHub.shutdown()


def _fsProbe(dictLive, *listArguments, bWithProxyEnvironment=True):
    dictEnvironment = {} if bWithProxyEnvironment else {
        sKey: "" for sKey in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
    iExit, baOutput = dictLive["container"].exec_run(
        ["python3", "-c", S_PROBE] + list(listArguments), environment=dictEnvironment)
    return baOutput.decode("utf-8", errors="replace").strip()


@pytest.mark.falsification
def testTheSealedContainerHasNoRouteToTheDaemonHost(dictLive):
    """Kills: creating the network without the isolated gateway mode (a gateway appears)."""
    sNetworkName = dictLive["dictEgress"]["sNetworkName"]
    dictConfig = dictLive["clientDocker"].api.inspect_network(sNetworkName)["IPAM"]["Config"][0]
    assert "Gateway" not in dictConfig or not dictConfig.get("Gateway")
    assert _fsProbe(dictLive, "172.17.0.1:22:direct", bWithProxyEnvironment=False).startswith(
        "OSERROR 101")
    assert _fsProbe(dictLive, "host.docker.internal:%d:direct" % dictLive["iHubPort"],
                    bWithProxyEnvironment=False).startswith("OSERROR")


def testAnOrdinaryInternalNetworkDoesGetAGateway(dictLive):
    """The negative control: without the isolated option the bridge has an address."""
    clientDocker = dictLive["clientDocker"]
    networkPlain = clientDocker.networks.create(
        f"vaibifyEgressControl{secrets.token_hex(4)}", driver="bridge", internal=True,
        labels=fdictLabels())
    try:
        assert networkPlain.attrs["IPAM"]["Config"][0].get("Gateway")
    finally:
        networkPlain.remove()


def testTheProxyTunnelsAnAllowedHostAndRefusesTheRest(dictLive):
    assert _fsProbe(dictLive, "pypi.org:443:proxy").startswith("HTTP/1.1 200")
    assert _fsProbe(dictLive, "example.com:443:proxy").startswith("HTTP/1.1 403")
    assert _fsProbe(dictLive, "93.184.216.34:443:proxy").startswith("HTTP/1.1 403")
    assert _fsProbe(dictLive, "pypi.org:80:proxy").startswith("HTTP/1.1 403")


@pytest.mark.falsification
def testTheHubIsReachedOnItsPortAloneForHttpAndConnect(dictLive):
    """Kills: pinning the hub's host name without its port, which opens every host service."""
    iHubPort = dictLive["iHubPort"]
    assert _fsProbe(dictLive, "host.docker.internal:%d:proxy" % iHubPort).startswith(
        "HTTP/1.1 200")
    assert _fsProbe(dictLive, "host.docker.internal:%d:proxy" % (iHubPort + 1)).startswith(
        "HTTP/1.1 403")
    assert _fsProbe(dictLive, "hub-http", "http://host.docker.internal:%d/api/ping" % iHubPort
                    ) == "hub-stand-in /api/ping"


def testTheRefusalsAreReadBackForTheDashboard(dictLive):
    listRefused = projectEgress.flistReadRefusedDestinations(dictLive["sUuid"])
    assert any(dictEntry["sHost"] == "example.com" for dictEntry in listRefused)
    assert projectEgress.fsProxyState(dictLive["sUuid"]) == "running"
