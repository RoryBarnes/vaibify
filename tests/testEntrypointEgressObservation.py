"""A sealed container's startup network check asks its proxy, cause by cause.

A container in web or allowlist mode resolves no name itself: its proxy
resolves names for it. The startup check used to run a name lookup
anyway, so it warned "could not resolve github.com" on every start of
every sealed project while the start's own repository updates went
through the proxy without trouble. It now sends the proxy the CONNECT
the container's tools send and reads the proxy's refusal back, because
the proxy already tells apart a host off the allowlist, a name that
resolves to a non-public address, an upstream it could not dial and a
full tunnel table, and each of those has a different remedy.

These source the REAL ``entrypoint.sh`` and run the real function
against a stand-in proxy on loopback that answers in the proxy's own
format (``egressProxy.fnRefuse``: a status line and a
``vaibify-egress: <reason>`` body).
"""

import os
import shutil
import socket
import ssl
import subprocess
import threading

import pytest


_S_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "entrypoint.sh",
))


def _fsRefusal(sStatusLine, sReason):
    """Return a refusal exactly as ``egressProxy.fnRefuse`` writes one."""
    sBody = "vaibify-egress: " + sReason + "\n"
    return (sStatusLine + "\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(len(sBody)) + "\r\nConnection: close\r\n\r\n" + sBody)


class _StandInProxy:
    """Answers one CONNECT with a scripted reply, then optionally speaks TLS."""

    def __init__(self, sReply, contextTls=None, bGarbageAfter=False):
        self.socketListening = socket.socket()
        self.socketListening.bind(("127.0.0.1", 0))
        self.socketListening.listen(1)
        self.iPort = self.socketListening.getsockname()[1]
        self.sReply, self.contextTls = sReply, contextTls
        self.bGarbageAfter = bGarbageAfter
        self.threadServe = threading.Thread(target=self._fnServe, daemon=True)
        self.threadServe.start()

    def _fnServe(self):
        socketClient, _ = self.socketListening.accept()
        socketClient.settimeout(5)
        baRequest = b""
        while b"\r\n\r\n" not in baRequest:
            baRequest += socketClient.recv(4096)
        socketClient.sendall(self.sReply.encode("ascii"))
        try:
            if self.contextTls is not None:
                self.contextTls.wrap_socket(socketClient, server_side=True).close()
            elif self.bGarbageAfter:
                socketClient.recv(4096)
                socketClient.sendall(b"this is not a TLS server hello\r\n" * 4)
        except (OSError, ssl.SSLError):
            pass
        socketClient.close()
        self.socketListening.close()


def _flistRunTheCheck(sProxyUrl, sMode="web", dictExtraEnvironment=None):
    """Source the entrypoint and run the startup check; return warnings and codes."""
    sScript = (
        "set +e\n"
        f"source {_S_ENTRYPOINT}\n"
        'fnObserveNameResolution "github.com"\n'
        'printf "WARNING-LINE:%s\\n" "${saStartupWarnings[@]}"\n'
        'printf "OBSERVATION:%s\\n" "${saStartupObservations[@]}"\n'
    )
    dictEnvironment = dict(os.environ, VAIBIFY_NETWORK_MODE=sMode,
                           HTTPS_PROXY=sProxyUrl, https_proxy=sProxyUrl)
    dictEnvironment.update(dictExtraEnvironment or {})
    sOutput = subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True,
        env=dictEnvironment, timeout=60,
    ).stdout
    listWarnings = [sLine.split(":", 1)[1] for sLine in sOutput.splitlines()
                    if sLine.startswith("WARNING-LINE:") and sLine != "WARNING-LINE:"]
    return listWarnings, sOutput


def _fsProxyUrl(proxyStandIn):
    return f"http://127.0.0.1:{proxyStandIn.iPort}"


def _fiUnusedPort():
    with socket.socket() as socketProbe:
        socketProbe.bind(("127.0.0.1", 0))
        return socketProbe.getsockname()[1]


def testAProxyThatDoesNotAnswerSaysNothingReachesTheNetwork():
    listWarnings, _ = _flistRunTheCheck(f"http://127.0.0.1:{_fiUnusedPort()}")
    assert len(listWarnings) == 1
    assert "github.com: egress-proxy-unreachable:" in listWarnings[0]
    assert "nothing in this container reaches the network" in listWarnings[0]


def testAHostOffTheAllowlistNamesTheAllowlist():
    proxyStandIn = _StandInProxy(_fsRefusal("HTTP/1.1 403 Forbidden", "host not on allowlist"))
    listWarnings, _ = _flistRunTheCheck(_fsProxyUrl(proxyStandIn), sMode="allowlist")
    assert listWarnings == [
        "github.com: egress-not-allowlisted: the network proxy refused it "
        "(host not on allowlist); add it to allowedHosts in vaibify.yml"]


@pytest.mark.falsification
def testANonPublicDestinationIsNotCalledAnAllowlistMatter():
    """Kills: folding the non-public refusal into a generic one.

    A name that resolves to a private address is refused in BOTH sealed
    modes, so advice to edit the allowlist would send a researcher to a
    setting that cannot help. Mutation: the probe's ``elif "non-public"
    in sReason:`` becomes ``elif False:``.
    """
    proxyStandIn = _StandInProxy(_fsRefusal(
        "HTTP/1.1 403 Forbidden", "host resolves to a non-public address"))
    listWarnings, _ = _flistRunTheCheck(_fsProxyUrl(proxyStandIn))
    assert len(listWarnings) == 1
    assert "egress-non-public-destination" in listWarnings[0]
    assert "allowedHosts" not in listWarnings[0]


def testAnUpstreamTheProxyCannotDialIsSaidSo():
    proxyStandIn = _StandInProxy(_fsRefusal("HTTP/1.1 502 Bad Gateway", "upstream dial failed"))
    listWarnings, _ = _flistRunTheCheck(_fsProxyUrl(proxyStandIn))
    assert listWarnings == [
        "github.com: egress-upstream-unreachable: the network proxy could "
        "not reach it (upstream dial failed)"]


def testAFullTunnelTableIsSaidSo():
    proxyStandIn = _StandInProxy(_fsRefusal(
        "HTTP/1.1 503 Service Unavailable", "too many open tunnels"))
    listWarnings, _ = _flistRunTheCheck(_fsProxyUrl(proxyStandIn))
    assert listWarnings and "egress-proxy-saturated" in listWarnings[0]


def testAnyOtherRefusalQuotesTheProxysReason():
    proxyStandIn = _StandInProxy(_fsRefusal(
        "HTTP/1.1 403 Forbidden", "raw-address CONNECT target refused"))
    listWarnings, _ = _flistRunTheCheck(_fsProxyUrl(proxyStandIn))
    assert listWarnings == [
        "github.com: egress-refused: the network proxy refused it "
        "(403 raw-address CONNECT target refused)"]


def testAnOpenedTunnelWhoseTlsFailsIsSaidSo():
    proxyStandIn = _StandInProxy(
        "HTTP/1.1 200 Connection Established\r\n\r\n", bGarbageAfter=True)
    listWarnings, _ = _flistRunTheCheck(_fsProxyUrl(proxyStandIn))
    assert len(listWarnings) == 1
    assert "egress-tls-failed" in listWarnings[0]


@pytest.mark.skipif(shutil.which("openssl") is None,
                    reason="needs openssl to mint the stand-in host's certificate")
@pytest.mark.falsification
def testAReachableHostRaisesNoWarningInASealedMode(tmp_path):
    """Kills: running the name lookup in a sealed mode again.

    The lookup cannot succeed in a sealed container, so it warned on
    every start. Here the proxy tunnels and the far end completes TLS
    with a certificate this check trusts, so nothing may be warned.
    Mutation: the mode test ``= "web" ]`` becomes ``= "never" ]``, which
    sends a web-mode container back to the name lookup.
    """
    sKey, sCertificate = str(tmp_path / "key.pem"), str(tmp_path / "cert.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", sKey, "-out", sCertificate, "-days", "1",
         "-subj", "/CN=github.com", "-addext", "subjectAltName=DNS:github.com"],
        capture_output=True, check=True)
    contextServer = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contextServer.load_cert_chain(sCertificate, sKey)
    proxyStandIn = _StandInProxy(
        "HTTP/1.1 200 Connection Established\r\n\r\n", contextTls=contextServer)
    listWarnings, sOutput = _flistRunTheCheck(
        _fsProxyUrl(proxyStandIn), sMode="web",
        dictExtraEnvironment={"SSL_CERT_FILE": sCertificate})
    assert listWarnings == []
    assert '"sCode": "egress-reachable"' in sOutput


def testAnOpenProjectStillResolvesNamesItself():
    """Open mode has its own resolver, so the name lookup still applies."""
    listWarnings, sOutput = _flistRunTheCheck(
        f"http://127.0.0.1:{_fiUnusedPort()}", sMode="open")
    assert "egress-" not in sOutput
    assert all("egress-" not in sWarning for sWarning in listWarnings)
