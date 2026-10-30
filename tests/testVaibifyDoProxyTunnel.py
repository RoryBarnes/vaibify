"""In allowlist mode the in-container CLI reaches its hub only through the proxy.

The container has no route to the host; the proxy sidecar pins the
hub's one port. ``urllib`` tunnels from the proxy variables on its own;
the hand-rolled WebSocket dial must do so explicitly, with a CONNECT
whose 200 is checked before the handshake begins. Driven with a fake
socket so no network is touched.
"""

import importlib.util
from pathlib import Path

import pytest

_S_VAIBIFY_DO_PATH = (
    Path(__file__).resolve().parent.parent
    / "vaibify" / "containerImage" / "vaibifyDo.py"
)


@pytest.fixture
def modCli():
    spec = importlib.util.spec_from_file_location("vaibifyDoTunnel", _S_VAIBIFY_DO_PATH)
    moduleCli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleCli)
    return moduleCli


class _FakeSocket:
    def __init__(self, baReply):
        self.baReply = baReply
        self.listSent = []

    def sendall(self, baData):
        self.listSent.append(baData)

    def recv(self, iMaximumBytes):
        baChunk, self.baReply = self.baReply, b""
        return baChunk


def _fnFakeConnections(monkeypatch, moduleCli, baReply):
    listDialed = []

    def fsocketConnect(tAddress, timeout=None):
        listDialed.append(tAddress)
        return _FakeSocket(baReply)

    monkeypatch.setattr(moduleCli.socket, "create_connection", fsocketConnect)
    return listDialed


@pytest.mark.falsification
def testWithAProxyTheHubIsReachedThroughAConnectTunnel(modCli, monkeypatch):
    """Kills: dialing the hub directly although a proxy is set, which cannot reach it."""
    monkeypatch.setenv("HTTPS_PROXY", "http://172.30.0.2:8888")
    listDialed = _fnFakeConnections(
        monkeypatch, modCli, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    socketHub = modCli.fsocketConnectToHub("host.docker.internal", 8050)
    assert listDialed == [("172.30.0.2", 8888)]
    assert socketHub.listSent[0].startswith(b"CONNECT host.docker.internal:8050 HTTP/1.1\r\n")


def testWithoutAProxyTheHubIsDialedDirectly(modCli, monkeypatch):
    for sName in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(sName, raising=False)
    listDialed = _fnFakeConnections(monkeypatch, modCli, b"")
    socketHub = modCli.fsocketConnectToHub("host.docker.internal", 8050)
    assert listDialed == [("host.docker.internal", 8050)]
    assert socketHub.listSent == []


def testARefusedTunnelIsAnErrorNamingTheProxysAnswer(modCli, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://172.30.0.2:8888")
    _fnFakeConnections(monkeypatch, modCli, b"HTTP/1.1 403 Forbidden\r\n\r\nrefused\n")
    with pytest.raises(OSError, match="403"):
        modCli.fsocketConnectToHub("host.docker.internal", 8050)


def testAProxyThatHangsUpIsAnError(modCli, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://172.30.0.2:8888")
    _fnFakeConnections(monkeypatch, modCli, b"")
    with pytest.raises(OSError, match="closed"):
        modCli.fsocketConnectToHub("host.docker.internal", 8050)


def testTheProxyAuthorityIsReadFromEitherSpelling(modCli, monkeypatch):
    for sName in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(sName, raising=False)
    assert modCli.ftProxyAuthority() is None
    monkeypatch.setenv("http_proxy", "http://10.9.8.7:9999")
    assert modCli.ftProxyAuthority() == ("10.9.8.7", 9999)
