"""Branch coverage for ``vaibify doctor`` and its network decision graph.

The daemon is replaced at its two narrowest boundaries -- the
``docker inspect`` of container state and the Docker connection's
typed reads -- and name resolution on this host is replaced at
``socket.getaddrinfo``, so no test resolves a real name or dials a
real host. Every other decision (scope routing, the exit code, the
JSON shape, the explain lookup, the DNS pair classification) runs for
real.
"""

import json
import os
import socket
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from vaibify.cli import commandBuild, commandDoctor, doctorHostChecks
from vaibify.cli import doctorNetwork
from vaibify.cli.commandDoctor import fnDoctorCommand
from vaibify.cli.preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_INFO, S_LEVEL_NOT_CHECKED, S_LEVEL_OK,
    S_LEVEL_WARN, S_SCOPE_CONTAINER, PreflightResult,
)
from vaibify.config import hostResidue, registryManager
from vaibify.docker import containerManager

S_CONTAINER_NAME = "projectAlpha"
S_REMOTE_HOST = "git.example.test"


def fconfigForDoctor(**dictOverrides):
    """Return the config attributes the doctor scopes read."""
    dictValues = {
        "sProjectName": S_CONTAINER_NAME, "sWorkspaceRoot": "/workspace",
        "listPorts": [], "listBindMounts": [], "listRepositories": [],
        "features": SimpleNamespace(bGpu=False, bClaude=False),
    }
    dictValues.update(dictOverrides)
    return SimpleNamespace(**dictValues)


def fnWriteRegistry(tmp_path, monkeypatch, listProjects):
    """Point the registry at a tmp file holding listProjects."""
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({"listProjects": listProjects}))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )


def fnPinContainerState(monkeypatch, sState):
    """Answer ``docker inspect -f {{.State.Status}}`` with sState."""
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        lambda sProjectName: containerManager._fdictParseContainerState(
            sState,
        ),
    )


def fnPinDockerConnection(monkeypatch, objConnection):
    """Make the doctor's Docker connection open as objConnection."""
    monkeypatch.setattr(
        commandDoctor, "_fconnectionOpenDockerQuietly", lambda: objConnection,
    )


def fnPinHostResolution(monkeypatch, listAddresses):
    """Answer getaddrinfo on this host; an empty list means NXDOMAIN."""
    def flistResolve(sHostname, *args, **kwargs):
        assert sHostname == S_REMOTE_HOST
        if not listAddresses:
            raise socket.gaierror(8, "nodename nor servname provided")
        return [(0, 0, 0, "", (sAddress, 0)) for sAddress in listAddresses]

    monkeypatch.setattr(doctorNetwork.socket, "getaddrinfo", flistResolve)


class _ContainerConnection:
    """The Docker connection's typed reads, scripted per test."""

    def __init__(
        self, sResolvConf="nameserver 192.168.5.1\n", dictResolution=None,
        dictProbe=None, bResolvConfRaises=False, dictFiles=None,
        listEntries=None, listExists=None, dictOwnership=None,
    ):
        self.sResolvConf = sResolvConf
        self.dictResolution = dictResolution or {
            "bAnswered": True, "listAddresses": ["192.0.2.10"],
        }
        self.dictProbe = dictProbe or {}
        self.bResolvConfRaises = bResolvConfRaises
        self.dictFiles = dictFiles or {}
        self.listEntries = listEntries or []
        self.listExists = listExists or []
        self.dictOwnership = dictOwnership or {"bAnswered": False}
        self.listProbeCalls = []

    def fbaFetchFile(self, sContainerName, sPath):
        if sPath == "/etc/resolv.conf":
            if self.bResolvConfRaises:
                raise RuntimeError("exec failed")
            return self.sResolvConf.encode("utf-8")
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def fdictResolveHostnameInContainer(self, sContainerName, sHost, *args):
        return self.dictResolution

    def fdictProbeTcpHandshakeInContainer(self, *args):
        self.listProbeCalls.append(args)
        return self.dictProbe

    def flistDirectoryEntries(self, sContainerName, sPath):
        return self.listEntries

    def flistContainerPathsExist(self, sContainerName, listPaths):
        return self.listExists

    def fdictFindForeignOwnedPaths(self, sContainerName, sRepoPath, iUid):
        return self.dictOwnership


def fjsonBridgeInspect(**dictOverrides):
    """Return a docker-inspect payload for a default-bridge container."""
    jsonInspect = {
        "HostConfig": {"NetworkMode": "bridge"},
        "Config": {"Env": []},
        "NetworkSettings": {"Networks": {"bridge": {"Gateway": "172.17.0.1"}}},
    }
    jsonInspect.update(dictOverrides)
    return jsonInspect


def fnPinInspect(monkeypatch, jsonInspect):
    """Answer the container inspect the network graph reads."""
    monkeypatch.setattr(
        doctorNetwork, "fjsonInspectContainer",
        lambda sContainerName: jsonInspect,
    )


# ---------------------------------------------------------------------
# Scope assembly
# ---------------------------------------------------------------------


def fpreflightNamed(sName, sLevel=S_LEVEL_OK):
    """Return a result whose name records where it came from."""
    return PreflightResult(sName=sName, sLevel=sLevel, sMessage=sName)


def testBuildScopeRunsTheBuildProbesThenTheDaemonSizedChecks(monkeypatch):
    for sHelper in (
        "_flistPreflightArch", "_flistPreflightDisk", "_flistPreflightMemory",
    ):
        monkeypatch.setattr(
            commandBuild, sHelper,
            lambda *args, sName=sHelper: [fpreflightNamed(sName)],
        )
    monkeypatch.setattr(
        doctorHostChecks, "flistCheckResourceAllocation",
        lambda config: [fpreflightNamed("resources")],
    )
    monkeypatch.setattr(
        doctorHostChecks, "flistCheckDepositScratchSpace",
        lambda config: [fpreflightNamed("deposit")],
    )
    listResults = commandDoctor._flistBuildOnlyChecks(fconfigForDoctor())
    assert [r.sName for r in listResults] == [
        "_flistPreflightArch", "_flistPreflightDisk",
        "_flistPreflightMemory", "resources", "deposit",
    ]


# ---------------------------------------------------------------------
# Orphaned build residue
# ---------------------------------------------------------------------


def fnRedirectResidueRoots(tmp_path, monkeypatch):
    """Point the residue roots at tmp directories; return (hash, context)."""
    pathHash = tmp_path / "cache"
    pathContext = tmp_path / "build"
    pathHash.mkdir()
    pathContext.mkdir()
    monkeypatch.setattr(hostResidue, "S_BUILD_HASH_ROOT", str(pathHash))
    monkeypatch.setattr(hostResidue, "S_BUILD_CONTEXT_ROOT", str(pathContext))
    return pathHash, pathContext


def testNoOrphanedResidueIsAPass(tmp_path, monkeypatch):
    fnRedirectResidueRoots(tmp_path, monkeypatch)
    fnWriteRegistry(tmp_path, monkeypatch, [{"sName": S_CONTAINER_NAME}])
    preflightResult = commandDoctor.fpreflightOrphanedHostResidue()
    assert preflightResult.sLevel == S_LEVEL_OK


def testOrphanedResidueIsNamedWithARemovalCommandButNotRemoved(
    tmp_path, monkeypatch,
):
    pathHash, pathContext = fnRedirectResidueRoots(tmp_path, monkeypatch)
    (pathHash / "projectGone-arg-hash").write_text("abc\n")
    (pathContext / "projectGone-abcd1234").mkdir()
    (pathHash / f"{S_CONTAINER_NAME}-arg-hash").write_text("def\n")
    fnWriteRegistry(tmp_path, monkeypatch, [{"sName": S_CONTAINER_NAME}])
    preflightResult = commandDoctor.fpreflightOrphanedHostResidue()
    assert preflightResult.sLevel == S_LEVEL_INFO
    assert "2 build leftover(s) belong to 1 project(s)" in (
        preflightResult.sMessage
    )
    assert preflightResult.sMessage.endswith("projectGone")
    assert preflightResult.sCommand == "rm -rf " + " ".join(sorted([
        str(pathHash / "projectGone-arg-hash"),
        str(pathContext / "projectGone-abcd1234"),
    ]))
    assert (pathHash / "projectGone-arg-hash").exists()
    assert (pathContext / "projectGone-abcd1234").exists()


def testCorruptRegistryEntryLeavesResidueUnassessed(tmp_path, monkeypatch):
    fnRedirectResidueRoots(tmp_path, monkeypatch)
    fnWriteRegistry(tmp_path, monkeypatch, [{"sMode": "host"}])
    preflightResult = commandDoctor.fpreflightOrphanedHostResidue()
    assert preflightResult.sLevel == S_LEVEL_NOT_CHECKED
    assert "registry could not be read" in preflightResult.sMessage


# ---------------------------------------------------------------------
# Host projects without git or python3
# ---------------------------------------------------------------------


def testHostProjectWithoutGitFailsAndWithoutPythonWarns(
    tmp_path, monkeypatch,
):
    sDirectory = str(tmp_path / "hostProject")
    os.makedirs(sDirectory)
    fnWriteRegistry(tmp_path, monkeypatch, [{
        "sName": "hostProjectBeta", "sMode": "host", "sDirectory": sDirectory,
    }])
    monkeypatch.setattr(commandDoctor.shutil, "which", lambda sName: None)
    listResults = commandDoctor.flistRunDoctorChecks(
        fconfigForDoctor(sProjectName="hostProjectBeta"), False, False,
    )
    dictByName = {r.sName: r for r in listResults}
    assert dictByName["host-directory"].sLevel == S_LEVEL_OK
    assert dictByName["host-git"].sLevel == S_LEVEL_FAIL
    assert "shell out to git" in dictByName["host-git"].sRemediation
    assert dictByName["host-python3"].sLevel == S_LEVEL_WARN
    assert "docker-daemon" not in dictByName


# ---------------------------------------------------------------------
# Container scope
# ---------------------------------------------------------------------


def testDockerConnectionThatCannotOpenIsNone(monkeypatch):
    from vaibify.docker import dockerConnection

    def fnRefuse():
        raise RuntimeError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(dockerConnection, "DockerConnection", fnRefuse)
    assert commandDoctor._fconnectionOpenDockerQuietly() is None


def testStoppedContainerMakesEveryContainerCheckUnassessed(monkeypatch):
    fnPinContainerState(monkeypatch, "exited")
    listResults = commandDoctor._flistContainerScopeChecks(
        fconfigForDoctor(), False,
    )
    assert [r.sName for r in listResults] == [
        "network-attachment", "resolver-configuration", "dns-resolution",
        "x11-container-display",
    ]
    assert {r.sLevel for r in listResults} == {S_LEVEL_NOT_CHECKED}
    assert "exists but is exited" in listResults[0].sMessage


def testUnopenableConnectionMakesEveryContainerCheckUnassessed(monkeypatch):
    fnPinContainerState(monkeypatch, "running")
    fnPinDockerConnection(monkeypatch, None)
    listResults = commandDoctor._flistContainerScopeChecks(
        fconfigForDoctor(), False,
    )
    assert {r.sLevel for r in listResults} == {S_LEVEL_NOT_CHECKED}
    assert "could not be opened" in listResults[0].sMessage


def testRunningContainerIsWalkedThroughTheNetworkGraph(monkeypatch):
    fnPinContainerState(monkeypatch, "running")
    fnPinDockerConnection(monkeypatch, _ContainerConnection())
    fnPinInspect(monkeypatch, fjsonBridgeInspect())
    listResults = commandDoctor._flistContainerScopeChecks(
        fconfigForDoctor(), False,
    )
    dictByName = {r.sName: r for r in listResults}
    assert dictByName["network-attachment"].sLevel == S_LEVEL_OK
    assert "172.17.0.1" in dictByName["network-attachment"].sMessage
    assert "192.168.5.1" in dictByName["resolver-configuration"].sMessage
    assert dictByName["dns-resolution"].sLevel == S_LEVEL_NOT_CHECKED


def testRunningContainerWithoutADisplayIsFlaggedWhenX11IsRequested(
    monkeypatch,
):
    fnPinContainerState(monkeypatch, "running")
    fnPinDockerConnection(monkeypatch, _ContainerConnection())
    fnPinInspect(monkeypatch, fjsonBridgeInspect())
    monkeypatch.setattr(
        containerManager, "fjsonInspectContainer",
        lambda sContainerName: fjsonBridgeInspect(),
    )
    listResults = commandDoctor._flistContainerScopeChecks(
        fconfigForDoctor(bX11Forwarding=True), False,
    )
    dictByName = {r.sName: r for r in listResults}
    assert dictByName["x11-container-display"].sLevel == S_LEVEL_WARN
    assert dictByName["x11-container-display"].sCommand == (
        "vaibify stop && vaibify start")


# ---------------------------------------------------------------------
# Project scope
# ---------------------------------------------------------------------


def testProjectScopeWithoutARepositoryNamesWhatItCouldNotRun(monkeypatch):
    fnPinContainerState(monkeypatch, "running")
    fnPinDockerConnection(monkeypatch, _ContainerConnection())
    listResults = commandDoctor._flistProjectScopeChecks(fconfigForDoctor())
    dictByName = {r.sName: r for r in listResults}
    assert dictByName["journal-quarantine"].sLevel == S_LEVEL_OK
    assert dictByName["startup-observations"].sLevel == S_LEVEL_NOT_CHECKED
    for sName in ("envelope-image-currency", "workspace-ownership"):
        assert dictByName[sName].sLevel == S_LEVEL_NOT_CHECKED
        assert "no git repository was found" in dictByName[sName].sMessage


def testProjectScopeWithARepositoryRunsEnvelopeAndOwnership(monkeypatch):
    from vaibify.reproducibility import environmentSnapshot
    fnPinContainerState(monkeypatch, "running")
    fnPinDockerConnection(monkeypatch, _ContainerConnection(
        listEntries=[S_CONTAINER_NAME], listExists=[True],
        dictOwnership={
            "bAnswered": True, "listRootOwned": [], "listOtherOwned": [],
            "bTruncated": False,
        },
    ))
    monkeypatch.setattr(
        environmentSnapshot, "fdictCaptureLiveImageIdentity",
        lambda sContainerName: {"sImageDigest": "", "sImageId": ""},
    )
    listResults = commandDoctor._flistProjectScopeChecks(fconfigForDoctor())
    dictByName = {r.sName: r for r in listResults}
    assert dictByName["envelope-image-currency"].sLevel == (
        S_LEVEL_NOT_CHECKED
    )
    assert dictByName["workspace-ownership"].sLevel == S_LEVEL_OK


# ---------------------------------------------------------------------
# The command: --explain and --json
# ---------------------------------------------------------------------


def fresultRunDoctor(monkeypatch, listArguments, listShared):
    """Invoke doctor with no project and a fixed shared-check list."""
    monkeypatch.setattr(
        commandDoctor, "_fconfigResolveProjectOrNone", lambda sName: None,
    )
    monkeypatch.setattr(
        commandDoctor, "_flistSharedChecks", lambda: list(listShared),
    )
    monkeypatch.setattr(commandDoctor, "_flistInterpreterChecks", lambda: [])
    monkeypatch.setattr(commandDoctor, "_flistLoginShellChecks", lambda: [])
    return CliRunner().invoke(fnDoctorCommand, listArguments)


def testExplainPrintsTheMechanismOfTheNamedCheck(monkeypatch):
    resultInvoke = fresultRunDoctor(
        monkeypatch, ["--explain", "docker-daemon"], [PreflightResult(
            sName="docker-daemon", sLevel=S_LEVEL_OK,
            sMessage="the daemon answered",
            sMechanism="Runs docker info and reads its exit status.",
        )],
    )
    assert resultInvoke.exit_code == 0
    assert "docker-daemon: the daemon answered" in resultInvoke.output
    assert "How this is decided:" in resultInvoke.output
    assert "Runs docker info" in resultInvoke.output
    assert "ok / " not in resultInvoke.output, (
        "--explain must print the one check and nothing else"
    )


def testExplainOfACheckWithoutAMechanismSaysSo(monkeypatch):
    resultInvoke = fresultRunDoctor(
        monkeypatch, ["--explain", "docker-daemon"],
        [fpreflightNamed("docker-daemon")],
    )
    assert "records no mechanism note" in resultInvoke.output


def testExplainOfACheckThatDidNotRunSaysSo(monkeypatch):
    resultInvoke = fresultRunDoctor(
        monkeypatch, ["--explain", "no-such-check"],
        [fpreflightNamed("docker-daemon")],
    )
    assert "No check named 'no-such-check' ran" in resultInvoke.output


def testJsonReportCarriesEveryResultAndTheTallies(monkeypatch):
    resultInvoke = fresultRunDoctor(monkeypatch, ["--json"], [
        fpreflightNamed("docker-daemon"),
        fpreflightNamed("docker-endpoint", S_LEVEL_WARN),
        fpreflightNamed("council-credential", S_LEVEL_FAIL),
    ])
    assert resultInvoke.exit_code == 1
    jsonReport = json.loads(resultInvoke.output)
    assert [dictEntry["sName"] for dictEntry in jsonReport["listResults"]] == [
        "installed-checkout", "docker-daemon", "docker-endpoint",
        "council-credential",
    ]
    assert (jsonReport["iOk"], jsonReport["iWarn"], jsonReport["iFail"]) == (
        1, 1, 1,
    )
    assert jsonReport["iNotChecked"] == 0


# ---------------------------------------------------------------------
# Network graph: stage 1
# ---------------------------------------------------------------------


def testNoneModeWithoutTheFlagIsStillIsolationButSaysSo():
    bIsolated, sEvidence = doctorNetwork.ftDescribeIsolation(
        {"HostConfig": {"NetworkMode": "none"}, "Config": {"Env": []}},
    )
    assert bIsolated is True
    assert "isolation flag is absent" in sEvidence


def testUnrecognisedNetworkModeIsAnUnknownResolver():
    assert doctorNetwork.fsClassifyResolverConfiguration(
        {"HostConfig": {"NetworkMode": "host"}}, "",
    ) == doctorNetwork.S_RESOLVER_UNKNOWN


def testUninspectableContainerIsUnassessed(monkeypatch):
    fnPinInspect(monkeypatch, {})
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        fconfigForDoctor(), S_CONTAINER_NAME, _ContainerConnection(),
    )
    assert [(r.sName, r.sLevel) for r in listResults] == [
        ("network-attachment", S_LEVEL_NOT_CHECKED),
    ]


def testDetachedContainerFailsAttachmentAndFalseIsolationWarns(monkeypatch):
    fnPinInspect(monkeypatch, fjsonBridgeInspect(
        NetworkSettings={"Networks": {}},
        Config={"Env": ["VAIBIFY_NETWORK_ISOLATED=true"]},
    ))
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        fconfigForDoctor(), S_CONTAINER_NAME,
        _ContainerConnection(bResolvConfRaises=True),
    )
    dictByName = {r.sName: r for r in listResults}
    assert dictByName["network-attachment"].sLevel == S_LEVEL_FAIL
    assert dictByName["network-isolation"].sLevel == S_LEVEL_WARN
    assert "claims isolation but does not have it" in (
        dictByName["network-isolation"].sMessage
    )
    assert "(nameservers:" not in (
        dictByName["resolver-configuration"].sMessage
    )


def testAttachmentWithoutAGatewayWarns(monkeypatch):
    fnPinInspect(monkeypatch, fjsonBridgeInspect(
        NetworkSettings={"Networks": {"projectNet": {"Gateway": ""}}},
    ))
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        fconfigForDoctor(), S_CONTAINER_NAME, _ContainerConnection(),
    )
    assert listResults[0].sLevel == S_LEVEL_WARN
    assert "attached to projectNet, but with no default route" in (
        listResults[0].sMessage
    )


def testExplicitDnsIsFlaggedAsDriftThatNoRestartClears(monkeypatch):
    fnPinInspect(monkeypatch, fjsonBridgeInspect(
        HostConfig={"NetworkMode": "bridge", "Dns": ["10.9.9.9"]},
    ))
    listResults = doctorNetwork.flistDiagnoseContainerNetwork(
        fconfigForDoctor(), S_CONTAINER_NAME,
        _ContainerConnection(sResolvConf="nameserver 10.9.9.9\n"),
    )
    preflightResolver = {r.sName: r for r in listResults}[
        "resolver-configuration"
    ]
    assert preflightResolver.sLevel == S_LEVEL_WARN
    assert "introduced outside vaibify" in preflightResolver.sMessage
    assert "vaibify repair dns" in preflightResolver.sRemediation


# ---------------------------------------------------------------------
# Network graph: probe target, paired DNS verdicts, transport
# ---------------------------------------------------------------------


def testUnparseableRemoteGivesNoTarget():
    dictTarget = doctorNetwork.fdictResolveProbeTarget(fconfigForDoctor(
        listRepositories=[{"url": "not a url"}],
    ))
    assert dictTarget["sHostname"] == ""


def testAgentProviderIsTheFallbackTarget():
    dictTarget = doctorNetwork.fdictResolveProbeTarget(fconfigForDoctor(
        features=SimpleNamespace(bClaude=True),
    ))
    assert dictTarget["sHostname"] == "api.anthropic.com"
    assert dictTarget["iPort"] == 443
    assert dictTarget["bUsesTls"] is True
    assert "agent provider" in dictTarget["sOrigin"]


def flistRunGraph(
    monkeypatch, listHostAddresses, connectionContainer,
    jsonInspect=None, bOnline=False,
):
    """Walk the graph for a project whose remote is S_REMOTE_HOST."""
    fnPinInspect(monkeypatch, jsonInspect or fjsonBridgeInspect())
    fnPinHostResolution(monkeypatch, listHostAddresses)
    return doctorNetwork.flistDiagnoseContainerNetwork(
        fconfigForDoctor(listRepositories=[
            {"url": f"https://{S_REMOTE_HOST}/team/projectAlpha.git"},
        ]),
        S_CONTAINER_NAME, connectionContainer, bOnline,
    )


def fdictResultsByName(listResults):
    """Index results by check name."""
    return {r.sName: r for r in listResults}


def testContainerThatCannotLookUpAtAllIsUnassessed(monkeypatch):
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, ["192.0.2.10"], _ContainerConnection(dictResolution={
            "bAnswered": False, "sError": "no getent in image",
        }),
    ))
    assert dictByName["dns-resolution"].sLevel == S_LEVEL_NOT_CHECKED
    assert "no getent in image" in dictByName["dns-resolution"].sMessage


def testBothSidesFailingIsNotBlamedOnTheContainer(monkeypatch):
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, [], _ContainerConnection(dictResolution={
            "bAnswered": True, "listAddresses": [],
        }),
    ))
    preflightDns = dictByName["dns-resolution"]
    assert preflightDns.sLevel == S_LEVEL_FAIL
    assert preflightDns.sCommand == ""
    assert "Check the network this machine is on" in (
        preflightDns.sRemediation
    )
    assert "egress-transport" not in dictByName


def testContainerOnlyFailureOnTheBridgeRecommendsTheRepair(monkeypatch):
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, ["192.0.2.10"], _ContainerConnection(dictResolution={
            "bAnswered": True, "listAddresses": [],
        }),
    ))
    preflightDns = dictByName["dns-resolution"]
    assert preflightDns.sLevel == S_LEVEL_FAIL
    assert preflightDns.sCommand == "vaibify repair dns"
    assert "clears when the container restarts" in (
        preflightDns.sRemediation
    )


def testContainerOnlyFailureWithExplicitDnsRecommendsRecreation(
    monkeypatch,
):
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, ["192.0.2.10"],
        _ContainerConnection(dictResolution={
            "bAnswered": True, "listAddresses": [],
        }),
        jsonInspect=fjsonBridgeInspect(
            HostConfig={"NetworkMode": "bridge", "Dns": ["10.9.9.9"]},
        ),
    ))
    preflightDns = dictByName["dns-resolution"]
    assert preflightDns.sCommand == ""
    assert "Recreate it from the vaibify configuration" in (
        preflightDns.sRemediation
    )


def testHostOnlyFailureWarnsButDoesNotImpairTheContainer(monkeypatch):
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, [], _ContainerConnection(),
    ))
    preflightDns = dictByName["dns-resolution"]
    assert preflightDns.sLevel == S_LEVEL_WARN
    assert "Nothing to do for this container" in preflightDns.sRemediation
    assert dictByName["egress-transport"].sLevel == S_LEVEL_INFO


@pytest.mark.parametrize("dictProbe, sLevel, sFragment", [
    ({"bAnswered": False, "sError": "python3 missing"},
     S_LEVEL_NOT_CHECKED, "could not run inside the container"),
    ({"bAnswered": True, "bConnected": True, "bTlsVerified": False,
      "sTlsError": "self-signed certificate in chain"},
     S_LEVEL_WARN, "self-signed certificate in chain"),
    ({"bAnswered": True, "bConnected": False,
      "sError": "timed out"},
     S_LEVEL_FAIL, "could not open a connection"),
])
def testOnlineTransportVerdicts(monkeypatch, dictProbe, sLevel, sFragment):
    connectionContainer = _ContainerConnection(dictProbe=dictProbe)
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, ["192.0.2.10"], connectionContainer, bOnline=True,
    ))
    preflightTransport = dictByName["egress-transport"]
    assert preflightTransport.sLevel == sLevel
    assert sFragment in preflightTransport.sMessage
    assert connectionContainer.listProbeCalls[0][1:4] == (
        S_REMOTE_HOST, 443, 2.0,
    )


def testIpv6OnlyAnswerExplainsAFailedConnection(monkeypatch):
    dictByName = fdictResultsByName(flistRunGraph(
        monkeypatch, ["192.0.2.10"],
        _ContainerConnection(
            dictResolution={
                "bAnswered": True, "listAddresses": ["2001:db8::10"],
            },
            dictProbe={"bAnswered": True, "bConnected": False,
                       "sError": "network unreachable"},
        ),
        bOnline=True,
    ))
    assert dictByName["address-family"].sLevel == S_LEVEL_INFO
    assert "only IPv6 addresses (2001:db8::10)" in (
        dictByName["address-family"].sMessage
    )


def testContainerScopeResultsAreScopedToTheContainer(monkeypatch):
    listResults = flistRunGraph(
        monkeypatch, ["192.0.2.10"], _ContainerConnection(),
    )
    assert {r.sScope for r in listResults} == {S_SCOPE_CONTAINER}
