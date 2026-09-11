"""The network diagnosis is a graph, and its classifications are pinned.

Two failures shaped this file. A container that could not resolve
hostnames reported ok in every check vaibify had, because nothing ran
inside it. And a linear "first failing rung" ladder, drafted for this
work, misclassified in both directions: a container-resolution failure
read as a container fault on a network whose DNS was simply down, and
a direct-dial failure read as broken egress on a network where
everything works through a proxy.

So the properties asserted here are about SHAPE, not plumbing: the
pair is classified together, isolation stops the walk, and the three
resolver configurations are told apart -- because only one of them can
be repaired by a restart.
"""

from unittest.mock import patch

import pytest

from vaibify.cli import doctorNetwork
from vaibify.cli.doctorNetwork import (
    S_DNS_BOTH_FAIL, S_DNS_BOTH_RESOLVE, S_DNS_CONTAINER_FAULT,
    S_DNS_HOST_FAULT_NOT_IMPAIRING, S_RESOLVER_DEFAULT_BRIDGE,
    S_RESOLVER_EMBEDDED_RESOLVER, S_RESOLVER_EXPLICIT_DNS,
    fdictResolveProbeTarget, fsClassifyDnsPair,
    fsClassifyResolverConfiguration, ftDescribeIsolation,
)


class _ConnectionStub:
    """Answers the two container reads the graph performs."""

    def __init__(self, sResolvConf="", dictResolution=None):
        self.sResolvConf = sResolvConf
        self.dictResolution = dictResolution or {
            "bAnswered": True, "listAddresses": ["93.184.216.34"],
        }
        self.listHandshakes = []

    def fbaFetchFile(self, sContainerId, sPath):
        return self.sResolvConf.encode("utf-8")

    def fdictResolveHostnameInContainer(self, *args, **kwargs):
        return self.dictResolution

    def fdictProbeTcpHandshakeInContainer(self, *args, **kwargs):
        self.listHandshakes.append(args)
        return {"bAnswered": True, "bConnected": True, "bTlsVerified": True,
                "sAddress": "93.184.216.34"}


class _ConfigStub:
    sProjectName = "networkProbe"
    listRepositories = [{"url": "https://git.example.test/team/thing.git"}]
    features = None


@pytest.mark.parametrize("bHost,bContainer,sExpected", [
    (True, True, S_DNS_BOTH_RESOLVE),
    (True, False, S_DNS_CONTAINER_FAULT),
    (False, True, S_DNS_HOST_FAULT_NOT_IMPAIRING),
    (False, False, S_DNS_BOTH_FAIL),
])
def test_the_pair_is_classified_together(bHost, bContainer, sExpected):
    """Either answer alone means different things; only the pair decides."""
    assert fsClassifyDnsPair(bHost, bContainer) == sExpected


def test_a_container_failure_on_a_dead_network_is_not_a_container_fault():
    """The misclassification a ladder makes, stated as its own case."""
    assert fsClassifyDnsPair(False, False) != S_DNS_CONTAINER_FAULT


def test_explicit_dns_is_told_apart_from_the_other_two():
    """It is the one configuration a restart provably cannot repair."""
    assert fsClassifyResolverConfiguration(
        {"HostConfig": {"Dns": ["10.0.0.1"], "NetworkMode": "bridge"}},
        "nameserver 10.0.0.1\n",
    ) == S_RESOLVER_EXPLICIT_DNS


def test_the_embedded_resolver_is_recognised_by_its_address():
    """127.0.0.11 is Docker's own forwarder on a user-defined network."""
    assert fsClassifyResolverConfiguration(
        {"HostConfig": {"NetworkMode": "projectnet"}},
        "nameserver 127.0.0.11\n",
    ) == S_RESOLVER_EMBEDDED_RESOLVER


def test_a_user_defined_network_is_embedded_even_without_the_file():
    """The container's own file may not show the stale upstream at all."""
    assert fsClassifyResolverConfiguration(
        {"HostConfig": {"NetworkMode": "projectnet"}}, "",
    ) == S_RESOLVER_EMBEDDED_RESOLVER


def test_the_default_bridge_is_recognised():
    """Its resolver list is host-derived and rewritten on every start."""
    assert fsClassifyResolverConfiguration(
        {"HostConfig": {"NetworkMode": "bridge"}},
        "nameserver 192.168.65.7\n",
    ) == S_RESOLVER_DEFAULT_BRIDGE


def test_isolation_is_a_successful_assessment():
    """`--network none` is what the project asked for, not a fault."""
    bIsolated, sEvidence = ftDescribeIsolation({
        "HostConfig": {"NetworkMode": "none"},
        "Config": {"Env": ["VAIBIFY_NETWORK_ISOLATED=true"]},
    })
    assert bIsolated is True
    assert "vaibify created it" in sEvidence


def test_a_claimed_isolation_that_is_not_real_is_a_finding():
    """The flag set and the NetworkMode open is drift, not isolation."""
    bIsolated, sEvidence = ftDescribeIsolation({
        "HostConfig": {"NetworkMode": "bridge"},
        "Config": {"Env": ["VAIBIFY_NETWORK_ISOLATED=true"]},
    })
    assert bIsolated is False
    assert sEvidence


@pytest.mark.falsification
def test_an_isolated_container_gets_one_result_and_no_network_checks(
    monkeypatch,
):
    """Isolation ends the walk; downstream checks would say nothing.

    Kills: In doctorNetwork.flistDiagnoseContainerNetwork, continue
    the walk past isolation instead of returning, so a deliberately
    sealed container produces a wall of failures describing a working
    configuration.
    """
    monkeypatch.setattr(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {
            "HostConfig": {"NetworkMode": "none"},
            "Config": {"Env": ["VAIBIFY_NETWORK_ISOLATED=true"]},
        },
    )
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        _ConfigStub(), "networkProbe", _ConnectionStub(),
    )
    assert len(listResults) == 1
    assert listResults[0].sName == "network-isolation"
    assert listResults[0].sLevel == "info"


def test_the_probe_target_is_a_host_the_project_already_depends_on():
    """Doctor introduces no third party of its own."""
    dictTarget = fdictResolveProbeTarget(_ConfigStub())
    assert dictTarget["sHostname"] == "git.example.test"
    assert "repository" in dictTarget["sOrigin"]


def test_a_project_with_no_remote_and_no_agent_gets_no_target():
    """With nothing configured, doctor falls back to static inspection."""
    class _Bare:
        listRepositories = []
        features = None
    assert fdictResolveProbeTarget(_Bare())["sHostname"] == ""


def test_a_project_with_no_target_says_so_rather_than_inventing_one(
    monkeypatch,
):
    """The unassessed answer, never a name vaibify chose."""
    class _Bare:
        sProjectName = "networkProbe"
        listRepositories = []
        features = None
    monkeypatch.setattr(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {"HostConfig": {"NetworkMode": "bridge"},
                       "NetworkSettings": {"Networks": {
                           "bridge": {"Gateway": "172.17.0.1"}}}},
    )
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        _Bare(), "networkProbe", _ConnectionStub(),
    )
    listDns = [r for r in listResults if r.sName == "dns-resolution"]
    assert listDns and listDns[0].sLevel == "not-checked"


def test_no_advice_anywhere_names_a_public_resolver():
    """8.8.8.8 breaks split-horizon DNS on campus and corporate networks.

    Scans the strings the module can PRINT, not the file: the
    docstring names the address precisely to forbid it, and a
    whole-file scan would fail on the prohibition itself.
    """
    import ast
    treeModule = ast.parse(open("vaibify/cli/doctorNetwork.py").read())
    setDocstrings = {
        ast.get_docstring(nodeScope, clean=False)
        for nodeScope in ast.walk(treeModule)
        if isinstance(nodeScope, (
            ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
        ))
    }
    listSpoken = [
        nodeConstant.value for nodeConstant in ast.walk(treeModule)
        if isinstance(nodeConstant, ast.Constant)
        and isinstance(nodeConstant.value, str)
        and nodeConstant.value not in setDocstrings
    ]
    assert listSpoken, "the scan collected no strings at all"
    for sSpoken in listSpoken:
        assert "8.8.8.8" not in sSpoken
        assert "1.1.1.1" not in sSpoken


def test_the_transport_stage_does_not_run_without_online(monkeypatch):
    """A connection attempt is opt-in; a name lookup is the exception.

    Reported as INFO rather than "not checked": nobody asked for the
    probe, so it is not a check that could not be assessed, and the
    exit code for an explicitly requested scope must not fire on a
    policy the researcher set. The message still establishes nothing
    about transport.
    """
    monkeypatch.setattr(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {"HostConfig": {"NetworkMode": "bridge"},
                       "NetworkSettings": {"Networks": {
                           "bridge": {"Gateway": "172.17.0.1"}}}},
    )
    connectionStub = _ConnectionStub()
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        _ConfigStub(), "networkProbe", connectionStub, bOnline=False,
    )
    assert connectionStub.listHandshakes == []
    listTransport = [
        r for r in listResults if r.sName == "egress-transport"
    ]
    assert listTransport and listTransport[0].sLevel == "info"
    assert "nothing here says whether" in listTransport[0].sMessage


def test_a_proxy_url_is_redacted_in_the_report(monkeypatch):
    """A proxy URL routinely carries user:password@."""
    monkeypatch.setattr(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {
            "HostConfig": {"NetworkMode": "bridge"},
            "NetworkSettings": {"Networks": {
                "bridge": {"Gateway": "172.17.0.1"}}},
            "Config": {"Env": [
                "HTTPS_PROXY=http://alice:sekret@proxy.example.test:8080",
            ]},
        },
    )
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        _ConfigStub(), "networkProbe", _ConnectionStub(),
    )
    sReport = " ".join(r.sMessage for r in listResults)
    assert "sekret" not in sReport
    assert "proxy.example.test" in sReport


# -----------------------------------------------------------------------
# Stage 4 dials the door the PROJECT uses, along the path it would use.
#
# A probe hard-coded to 443 and a direct dial reported a working
# network as broken twice over: a git server reached over ssh answers
# on 22 and serves nothing on 443, and a container behind a corporate
# proxy reaches nothing directly whether it is healthy or not.
# -----------------------------------------------------------------------


@pytest.mark.parametrize("sUrl,tExpected", [
    ("https://git.example.test/o/r.git", ("git.example.test", 443, True)),
    ("http://git.example.test:8080/o/r.git",
     ("git.example.test", 8080, False)),
    ("ssh://git@git.example.test:2222/o/r.git",
     ("git.example.test", 2222, False)),
    ("git@git.example.test:o/r.git", ("git.example.test", 22, False)),
    ("git://git.example.test/o/r.git", ("git.example.test", 9418, False)),
])
def test_the_probe_target_carries_the_transport_the_project_uses(
    sUrl, tExpected,
):
    """Port and TLS travel with the name, or the probe dials a wall."""
    class _Configured:
        listRepositories = [{"url": sUrl}]
        features = None
    dictTarget = fdictResolveProbeTarget(_Configured())
    assert (
        dictTarget["sHostname"], dictTarget["iPort"],
        dictTarget["bUsesTls"],
    ) == tExpected


def test_an_scp_like_remote_is_a_target_at_all():
    """vaibify accepts `user@host:path`; urlsplit finds no host in it."""
    class _Configured:
        listRepositories = [{"url": "git@git.example.test:o/r.git"}]
        features = None
    assert fdictResolveProbeTarget(_Configured())["sHostname"]


@pytest.mark.falsification
def test_the_transport_probe_dials_the_project_s_own_port():
    """Kills: In doctorNetwork, probe port 443 rather than the target's.

    A git remote reached over ssh answers on 22 and serves nothing on
    443, so a fixed 443 reports a perfectly healthy container as
    unable to reach its own repository.
    """
    class _SshRemote:
        sProjectName = "networkProbe"
        listRepositories = [{"url": "ssh://git@git.example.test/o/r.git"}]
        features = None

    connectionStub = _ConnectionStub()
    with patch.object(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {"HostConfig": {"NetworkMode": "bridge"},
                       "NetworkSettings": {"Networks": {
                           "bridge": {"Gateway": "172.17.0.1"}}}},
    ):
        doctorNetwork.flistDiagnoseContainerNetwork(
            _SshRemote(), "networkProbe", connectionStub, bOnline=True,
        )
    assert connectionStub.listHandshakes, "no transport probe was made"
    tArguments = connectionStub.listHandshakes[0]
    assert tArguments[1] == "git.example.test"
    assert tArguments[2] == 22


def test_a_plain_transport_is_not_graded_on_a_tls_handshake():
    """ssh carries no TLS, so "TLS did not verify" is not a finding."""
    class _PlainProbe(_ConnectionStub):
        def fdictProbeTcpHandshakeInContainer(self, *args, **kwargs):
            self.listHandshakes.append(args)
            return {"bAnswered": True, "bConnected": True,
                    "bTlsVerified": False, "sAddress": "10.0.0.9"}

    class _SshRemote:
        sProjectName = "networkProbe"
        listRepositories = [{"url": "ssh://git@git.example.test/o/r.git"}]
        features = None

    with patch.object(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {"HostConfig": {"NetworkMode": "bridge"},
                       "NetworkSettings": {"Networks": {
                           "bridge": {"Gateway": "172.17.0.1"}}}},
    ):
        listResults = doctorNetwork.flistDiagnoseContainerNetwork(
            _SshRemote(), "networkProbe", _PlainProbe(), bOnline=True,
        )
    resultTransport = [
        r for r in listResults if r.sName == "egress-transport"
    ][0]
    assert resultTransport.sLevel == "ok"


def test_a_container_behind_a_proxy_is_probed_through_it():
    """A direct dial from behind a proxy fails whatever the truth is."""
    dictTarget = {"sHostname": "git.example.test", "iPort": 443,
                  "bUsesTls": True}
    assert doctorNetwork.ftSelectEffectiveProxy(
        {"HTTPS_PROXY": "http://proxy.example.test:3128"}, dictTarget,
    ) == ("proxy.example.test", 3128)


def test_a_no_proxy_host_is_probed_directly():
    """Probing a NO_PROXY host through the proxy invents a failure."""
    dictTarget = {"sHostname": "git.internal.example", "iPort": 443,
                  "bUsesTls": True}
    assert doctorNetwork.ftSelectEffectiveProxy(
        {
            "HTTPS_PROXY": "http://proxy.example.test:3128",
            "NO_PROXY": ".internal.example,localhost",
        },
        dictTarget,
    ) == ("", 0)


def test_a_proxy_refusal_is_reported_as_the_proxy_s_answer():
    """A 407 is the proxy talking, not the destination being unreachable."""
    class _ProxyRefusing(_ConnectionStub):
        def fdictProbeTcpHandshakeInContainer(self, *args, **kwargs):
            self.listHandshakes.append(args)
            return {
                "bAnswered": True, "bConnected": False,
                "bThroughProxy": True,
                "sProxyError": "HTTP/1.1 407 Proxy Authentication Required",
            }

    with patch.object(
        doctorNetwork, "fjsonInspectContainer",
        lambda sName: {
            "HostConfig": {"NetworkMode": "bridge"},
            "NetworkSettings": {"Networks": {
                "bridge": {"Gateway": "172.17.0.1"}}},
            "Config": {"Env": ["HTTPS_PROXY=http://proxy.example.test:3128"]},
        },
    ):
        listResults = doctorNetwork.flistDiagnoseContainerNetwork(
            _ConfigStub(), "networkProbe", _ProxyRefusing(), bOnline=True,
        )
    resultTransport = [
        r for r in listResults if r.sName == "egress-transport"
    ][0]
    assert resultTransport.sLevel == "fail"
    assert "proxy refused" in resultTransport.sMessage
    assert "407" in resultTransport.sMessage
    assert "sends none by design" in resultTransport.sRemediation


def test_the_proxy_probe_program_really_speaks_connect():
    """The probe text is run against a REAL socket, not asserted about.

    The program is module source text executed inside a container, so
    a mistake in it is invisible to every test that only checks what
    vaibify composed. This runs it against a loopback proxy that
    answers 407 -- the exact evidence the transport stage reports as a
    proxy refusal -- and reads the answer back.
    """
    import json
    import socketserver
    import subprocess
    import sys
    import threading

    from vaibify.docker.dockerConnection import (
        _DICT_TYPED_READ_PROGRAMS, _S_TYPED_READ_PATH_SLOT,
        S_TYPED_READ_TCP_HANDSHAKE, _fsTypedReadPathLiteral,
    )

    class _RefusingProxy(socketserver.BaseRequestHandler):
        def handle(self):
            baRequest = self.request.recv(1024)
            assert baRequest.startswith(b"CONNECT git.example.test:443")
            assert b"Proxy-Authorization" not in baRequest
            self.request.sendall(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n",
            )

    serverProxy = socketserver.TCPServer(("127.0.0.1", 0), _RefusingProxy)
    iProxyPort = serverProxy.server_address[1]
    threading.Thread(target=serverProxy.handle_request, daemon=True).start()
    try:
        sProgram = _DICT_TYPED_READ_PROGRAMS[
            S_TYPED_READ_TCP_HANDSHAKE
        ].replace(
            _S_TYPED_READ_PATH_SLOT,
            _fsTypedReadPathLiteral([
                "git.example.test", "443", "5", "tls",
                "127.0.0.1", str(iProxyPort),
            ]),
        )
        processProbe = subprocess.run(
            [sys.executable, "-c", sProgram],
            capture_output=True, text=True,
        )
        dictAnswer = json.loads(processProbe.stdout)
    finally:
        serverProxy.server_close()
    assert dictAnswer["bThroughProxy"] is True
    assert dictAnswer["bConnected"] is False
    assert "407" in dictAnswer["sProxyError"]
