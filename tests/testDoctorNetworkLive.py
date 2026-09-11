"""The network diagnosis, driven against containers that are really broken.

The unit lane can only prove the classifiers agree with themselves.
What it cannot see is whether a container built the way the finding
describes actually behaves that way -- and this diagnosis exists
because a researcher's container really did stop resolving names while
every check vaibify had reported ok.

So each case here BREAKS a real container in the specific way the
finding names, and asserts the diagnosis from the daemon's and the
container's own answers.

Skipped automatically when no daemon is reachable, unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one -- a lane advertised as
live coverage that reports success without a daemon is a false green.
"""

import subprocess
import uuid

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.cli.doctorNetwork import (
    S_RESOLVER_DEFAULT_BRIDGE, S_RESOLVER_EXPLICIT_DNS,
    flistDiagnoseContainerNetwork, fsClassifyResolverConfiguration,
    fsReadContainerResolvConf,
)
from vaibify.docker.containerManager import fjsonInspectContainer
from vaibify.docker.dockerConnection import DockerConnection


pytestmark = pytest.mark.docker_live

# The probe image DECLARES a user, and must. `DockerConnection`
# resolves every exec's user from the image's ``Config.User`` and falls
# back to ``researcher``; a stock base image declares neither, so every
# typed read against one is refused by the daemon with "unable to find
# user researcher" (measured here). A vaibify image always carries the
# directive, so this is a constraint on what a PROBE can be built from,
# not a defect -- and building the probe is the honest way to meet it
# rather than making the exec numeric, which would change the
# project-container lane that relies on the name.
S_PROBE_IMAGE = "vaibifynetworkprobe:test"
S_PROBE_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "RUN useradd --create-home --uid 1000 researcher\n"
    "USER researcher\n"
)


@pytest.fixture(scope="module", autouse=True)
def fnBuildProbeImage():
    """Build the probe image once for this module."""
    fnRequireDaemonReachable()
    processBuild = subprocess.run(
        ["docker", "build", "-t", S_PROBE_IMAGE, "-"],
        input=S_PROBE_DOCKERFILE, capture_output=True, text=True,
    )
    if processBuild.returncode != 0:
        pytest.skip(f"could not build the probe image: {processBuild.stderr}")
    yield


class _ConfigForProbe:
    """A project whose one dependency is a name that really resolves."""

    sProjectName = ""
    listRepositories = [{"url": "https://github.com/torvalds/linux.git"}]
    features = None


def _fsStartProbeContainer(saExtraArgs):
    """Start a probe container and return its name."""
    sName = "vaibifyNetProbe" + uuid.uuid4().hex[:8]
    processRun = subprocess.run(
        ["docker", "run", "-d", "--name", sName] + list(saExtraArgs)
        + [S_PROBE_IMAGE, "sleep", "300"],
        capture_output=True, text=True,
    )
    if processRun.returncode != 0:
        pytest.skip(f"could not start a probe container: {processRun.stderr}")
    return sName


def _fnRemoveProbeContainer(sName):
    """Remove the probe container on every path."""
    subprocess.run(
        ["docker", "rm", "-f", sName], capture_output=True, text=True,
    )


def _flistDiagnose(sName, bOnline=False):
    """Run the diagnosis against one live container."""
    configProbe = _ConfigForProbe()
    configProbe.sProjectName = sName
    return flistDiagnoseContainerNetwork(
        configProbe, sName, DockerConnection(), bOnline,
    )


def _fresultNamed(listResults, sName):
    """Return the one result with this name, or None."""
    for resultPreflight in listResults:
        if resultPreflight.sName == sName:
            return resultPreflight
    return None


def test_an_isolated_container_reports_once_and_probes_nothing():
    """`--network none` is a successful assessment, not a wall of failures."""
    fnRequireDaemonReachable()
    sName = _fsStartProbeContainer([
        "--network", "none", "-e", "VAIBIFY_NETWORK_ISOLATED=true",
    ])
    try:
        listResults = _flistDiagnose(sName)
        assert len(listResults) == 1
        assert listResults[0].sName == "network-isolation"
        assert listResults[0].sLevel == "info"
    finally:
        _fnRemoveProbeContainer(sName)


def test_a_container_with_a_dead_resolver_is_the_container_s_fault():
    """The motivating incident, reproduced: the host resolves, it does not.

    ``--dns`` at a reserved, unroutable address is a resolver that
    answers nothing -- the same observable behaviour as a resolv.conf
    left over from a network the laptop has since left.
    """
    fnRequireDaemonReachable()
    sName = _fsStartProbeContainer(["--dns", "192.0.2.1"])
    try:
        listResults = _flistDiagnose(sName)
        resultDns = _fresultNamed(listResults, "dns-resolution")
        assert resultDns is not None
        assert resultDns.sLevel == "fail"
        assert "the container does not" in resultDns.sMessage
    finally:
        _fnRemoveProbeContainer(sName)


def test_an_explicit_dns_container_is_reported_as_outside_drift():
    """Vaibify sets no DNS servers, so finding some means something else did."""
    fnRequireDaemonReachable()
    sName = _fsStartProbeContainer(["--dns", "192.0.2.1"])
    try:
        sResolvConf = fsReadContainerResolvConf(DockerConnection(), sName)
        assert fsClassifyResolverConfiguration(
            fjsonInspectContainer(sName), sResolvConf,
        ) == S_RESOLVER_EXPLICIT_DNS
        resultResolver = _fresultNamed(
            _flistDiagnose(sName), "resolver-configuration",
        )
        assert resultResolver.sLevel == "warn"
        assert "outside vaibify" in resultResolver.sMessage
        assert "restart cannot clear it" in resultResolver.sRemediation
    finally:
        _fnRemoveProbeContainer(sName)


def test_a_repair_refuses_to_restart_an_explicit_dns_container():
    """A restart would succeed and change nothing, which reads as failure."""
    fnRequireDaemonReachable()
    from vaibify.cli import commandRepair
    sName = _fsStartProbeContainer(["--dns", "192.0.2.1"])
    configProbe = _ConfigForProbe()
    configProbe.sProjectName = sName
    try:
        iExit = commandRepair._fiRepairDnsForProject(
            configProbe, False, True,
        )
        assert iExit == 1
        assert fjsonInspectContainer(sName)["State"]["Running"] is True
    finally:
        _fnRemoveProbeContainer(sName)


def test_a_healthy_container_resolves_and_classifies_as_bridge():
    """The pass, so the failing cases are not the only thing exercised."""
    fnRequireDaemonReachable()
    sName = _fsStartProbeContainer([])
    try:
        assert fsClassifyResolverConfiguration(
            fjsonInspectContainer(sName),
            fsReadContainerResolvConf(DockerConnection(), sName),
        ) == S_RESOLVER_DEFAULT_BRIDGE
        resultDns = _fresultNamed(_flistDiagnose(sName), "dns-resolution")
        assert resultDns is not None
        if resultDns.sLevel != "ok":
            pytest.skip(
                "this machine cannot resolve the probe name either, so "
                "the pair says nothing about the container: "
                + resultDns.sMessage
            )
        assert "both this host and the container resolve" in (
            resultDns.sMessage
        )
    finally:
        _fnRemoveProbeContainer(sName)


def test_dns_healthy_but_egress_dead_is_reported_at_the_transport_stage():
    """A dead route is not a DNS fault, and must not be reported as one."""
    fnRequireDaemonReachable()
    sName = _fsStartProbeContainer([
        "--add-host", "github.com:192.0.2.9",
    ])
    try:
        listResults = _flistDiagnose(sName, bOnline=True)
        resultDns = _fresultNamed(listResults, "dns-resolution")
        resultTransport = _fresultNamed(listResults, "egress-transport")
        assert resultDns is not None and resultDns.sLevel == "ok"
        assert resultTransport is not None
        assert resultTransport.sLevel == "fail"
        assert "this is the transport, not" in resultTransport.sRemediation
    finally:
        _fnRemoveProbeContainer(sName)
