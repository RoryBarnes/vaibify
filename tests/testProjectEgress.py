"""A project's sealed network and proxy sidecar, driven against a scripted docker.

Every ``docker`` command the module runs is recorded and answered from
a script, so these prove what the module ASKS the daemon for and how it
reads the answers: the isolated gateway mode, the refusal of an engine
that leaves a gateway, the sidecar's hardened posture and its pinned
hub, the release, the reconcile, and the refused-destination tally.
The live counterpart is ``tests/testProjectEgressLive.py``.
"""

import os
from types import SimpleNamespace

import pytest

from vaibify.docker import egressProxy, projectEgress

S_UUID = "b" * 32
S_NETWORK = f"vaibifyProjectEgress-{S_UUID}"
S_PROXY = f"vaibifyProjectProxy-{S_UUID}"


class _ScriptedDocker:
    """Answers docker commands from a dict keyed by their first words."""

    def __init__(self, dictAnswers):
        self.dictAnswers = dict(dictAnswers)
        self.listCommands = []
        self.setNetworks = set()

    def __call__(self, listArguments, fTimeoutSeconds=60.0):
        self.listCommands.append(list(listArguments))
        sKey = " ".join(listArguments[:2])
        if sKey == "network create":
            self.setNetworks.add(listArguments[-1])
        if sKey == "network inspect" and listArguments[-1] not in self.setNetworks:
            return SimpleNamespace(returncode=1, stdout="", stderr="No such network")
        if sKey == "network rm":
            self.setNetworks.discard(listArguments[-1])
        for sPrefix, tAnswer in self.dictAnswers.items():
            if " ".join(listArguments).startswith(sPrefix):
                iCode, sOut = tAnswer
                return SimpleNamespace(returncode=iCode, stdout=sOut, stderr="" if iCode == 0 else sOut)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def flistOf(self, sVerb):
        return [listCommand for listCommand in self.listCommands if listCommand[0] == sVerb]


def _fconfig(listPorts=()):
    features = SimpleNamespace(bClaude=True, bClaudeAutoUpdate=False, bCodex=False,
                               bGemini=False, bAntigravity=False, bOpenCode=False,
                               bCline=False, bOpenHands=False, bPi=False)
    return SimpleNamespace(sProjectName="sealedProject", features=features,
                           sPackageManager="pip", listRepositories=[],
                           listAllowedHosts=["data.example.org"], listPorts=list(listPorts))


DICT_HEALTHY = {
    "network inspect --format {{json .IPAM.Config}}": (0, '[{"Subnet":"172.30.0.0/16"}]\n'),
    "network inspect --format {{index .Options": (0, "isolated\n"),
    "logs": (0, egressProxy.S_PROXY_READY_LINE + " on 172.30.0.2:8888\n"),
    "inspect --format {{(index .NetworkSettings.Networks": (0, "172.30.0.2\n"),
    "inspect --format {{.State.Running}}": (0, "true\n"),
}


@pytest.fixture
def docker(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(egressProxy, "F_PROXY_READY_POLL_SECONDS", 0.001)
    dockerScripted = _ScriptedDocker(DICT_HEALTHY)
    monkeypatch.setattr(projectEgress, "_fprocessDocker", dockerScripted)
    return dockerScripted


@pytest.mark.falsification
def testTheNetworkIsInternalIsolatedAndWithoutIPv6(docker):
    """--internal alone leaves the bridge an address the host's listeners answer on.

    Kills: creating the network without the isolated gateway mode.
    """
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    listCreate = next(listCommand for listCommand in docker.listCommands
                      if listCommand[:2] == ["network", "create"])
    assert listCreate[:3] == ["network", "create", "--internal"]
    assert "--ipv6=false" in listCreate
    assert "com.docker.network.bridge.gateway_mode_ipv4=isolated" in listCreate
    assert f"{projectEgress.S_LABEL_KEY}={S_UUID}" in listCreate
    assert listCreate[-1] == S_NETWORK


@pytest.mark.falsification
def testAnEngineThatLeavesAGatewayIsRefusedWithNoFallback(docker):
    """Kills: accepting a network the engine gave a gateway despite the option."""
    docker.dictAnswers["network inspect --format {{json .IPAM.Config}}"] = (
        0, '[{"Subnet":"172.30.0.0/16","Gateway":"172.30.0.1"}]\n')
    with pytest.raises(egressProxy.EgressSetupError, match="28.0.0"):
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    assert ["network", "rm", S_NETWORK] in docker.listCommands
    assert docker.flistOf("run") == []


def testAnEngineThatRejectsTheOptionIsRefusedByName(docker):
    docker.dictAnswers["network create"] = (1, "invalid option: gateway_mode_ipv4")
    with pytest.raises(egressProxy.EgressSetupError) as errorRaised:
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    assert "28.0.0" in str(errorRaised.value) and "gateway_mode_ipv4" in str(errorRaised.value)


def testTheProxyIsHardenedServesNoHubYetAndForwardsThePublishedPorts(docker):
    dictEgress = projectEgress.fdictPrepareProjectEgress(
        _fconfig(listPorts=[{"host": 8888, "container": 8888}]), S_UUID)
    assert dictEgress == {"sNetworkName": S_NETWORK, "sProxyName": S_PROXY,
                          "sProxyAddress": "172.30.0.2"}
    listRun = docker.flistOf("run")[0]
    for sExpected in ("--cap-drop", "ALL", "--read-only", "--pids-limit",
                      "--network", S_NETWORK, "--user", "65534:65534"):
        assert sExpected in listRun
    assert listRun[listRun.index("--security-opt") + 1] == "no-new-privileges:true"
    assert listRun[listRun.index("--add-host") + 1] == "host.docker.internal:host-gateway"
    assert listRun[listRun.index("-p") + 1] == "127.0.0.1:8888:8888"
    assert egressProxy.S_PROXY_IMAGE in listRun
    iImage = listRun.index(egressProxy.S_PROXY_IMAGE)
    listCommand = listRun[iImage + 1:]
    assert "--hub-gateway" not in listCommand
    assert listCommand[listCommand.index("--hub-host") + 1] == "host.docker.internal"
    assert listCommand[listCommand.index("--hub-port-file") + 1] == (
        egressProxy.S_HUB_PORT_FILE_CONTAINER_PATH)
    assert listCommand[listCommand.index("--bind-cidr") + 1] == "172.30.0.0/16"
    assert listCommand[listCommand.index("--forward") + 1] == "8888=sealedProject:8888"
    assert "data.example.org" in listCommand[listCommand.index("--allowlist") + 1]
    listMounts = [listRun[iIndex + 1] for iIndex, sArgument in enumerate(listRun)
                  if sArgument == "--mount"]
    assert len(listMounts) == 2
    for sMount in listMounts:
        assert sMount.endswith(",readonly") and projectEgress._fsEgressRoot() in sMount
    assert f"target=/{egressProxy.S_HUB_PORT_DIRECTORY}," in listMounts[1]
    assert ["network", "connect", "bridge", S_PROXY] in docker.listCommands


def testAProxyThatNeverListensIsRemovedAndTheLaunchRefused(docker, monkeypatch):
    monkeypatch.setattr(egressProxy, "F_PROXY_READY_DEADLINE_SECONDS", 0.01)
    docker.dictAnswers["logs"] = (0, "starting\n")
    with pytest.raises(egressProxy.EgressSetupError, match="never reported"):
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    assert ["rm", "-f", S_PROXY] in docker.listCommands


def testAProxyThatExitsOnStartupReportsItsLog(docker):
    docker.dictAnswers["logs"] = (0, "")
    docker.dictAnswers["inspect --format {{.State.Running}}"] = (0, "false\n")
    with pytest.raises(egressProxy.EgressSetupError, match="exited on startup"):
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)


def testTheReleaseRemovesProxyNetworkAndScript(docker):
    sScriptDirectory = os.path.join(projectEgress._fsEgressRoot(), S_UUID)
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    assert os.path.isdir(sScriptDirectory)
    projectEgress.fnReleaseProjectEgressQuietly(S_UUID, "test")
    assert ["rm", "-f", S_PROXY] in docker.listCommands[-3:]
    assert ["network", "rm", S_NETWORK] in docker.listCommands[-3:]
    assert not os.path.exists(sScriptDirectory)
    projectEgress.fnReleaseProjectEgressQuietly("not an identity", "test")


def _fsAttachedKey(sUuid):
    return f"ps -a --filter network=vaibifyProjectEgress-{sUuid}"


@pytest.mark.falsification
def testTheReconcileReleasesOnlyIdentitiesNoContainerUses(docker):
    """Kills: releasing an identity without asking the daemon who uses it.

    The reconcile once released every identity its registry lacked, so
    a test hub with a throwaway registry took a running container's
    proxy. Here no registry exists at all: only the daemon's attachment
    answer may decide. Mutation: the reconcile's ``if
    fbEgressIsInUse(sProjectUuid):`` becomes ``if not sProjectUuid:``.
    """
    sOrphan, sNetworkOnly = "c" * 32, "d" * 32
    docker.dictAnswers["ps -a --filter label"] = (0, f"{S_UUID}\n{sOrphan}\n")
    docker.dictAnswers["network ls --filter"] = (0, f"{sNetworkOnly}\n")
    docker.dictAnswers[_fsAttachedKey(S_UUID)] = (0, f"{S_PROXY}\nsealedProject\n")
    docker.dictAnswers[_fsAttachedKey(sOrphan)] = (0, f"vaibifyProjectProxy-{sOrphan}\n")
    docker.dictAnswers[_fsAttachedKey(sNetworkOnly)] = (0, "")
    dictOutcomes = projectEgress.fdictReconcileProjectEgress()
    assert dictOutcomes == {S_UUID: "kept", sOrphan: "released", sNetworkOnly: "released"}
    assert ["rm", "-f", f"vaibifyProjectProxy-{sOrphan}"] in docker.listCommands
    assert ["rm", "-f", S_PROXY] not in docker.listCommands


@pytest.mark.falsification
def testAnUnansweredDaemonReleasesNothing(docker):
    """Kills: reading a failed attachment question as "nothing uses it".

    Mutation: ``fbEgressIsInUse``'s ``if processResult.returncode !=
    0:`` becomes ``if False:``, so a daemon error reads as an empty list.
    """
    docker.dictAnswers["ps -a --filter label"] = (0, f"{S_UUID}\n")
    docker.dictAnswers["network ls --filter"] = (0, "")
    docker.dictAnswers[_fsAttachedKey(S_UUID)] = (1, "Cannot connect to the Docker daemon")
    with pytest.raises(egressProxy.EgressSetupError):
        projectEgress.fdictReconcileProjectEgress()
    projectEgress.fnReleaseProjectEgressIfUnused(S_UUID, "test")
    assert docker.flistOf("rm") == []
    assert ["network", "rm", S_NETWORK] not in docker.listCommands


def testTheProxyItselfDoesNotCountAsAUser(docker):
    docker.dictAnswers[_fsAttachedKey(S_UUID)] = (0, f"{S_PROXY}\n")
    assert not projectEgress.fbEgressIsInUse(S_UUID)
    docker.dictAnswers[_fsAttachedKey(S_UUID)] = (0, f"{S_PROXY}\nsealedProject\n")
    assert projectEgress.fbEgressIsInUse(S_UUID)


@pytest.mark.falsification
def testTheRefusedDestinationsAreTalliedFromTheProxyLog(docker):
    """Kills: counting allowed destinations among the refused."""
    docker.dictAnswers["logs"] = (0, "\n".join([
        "vaibify-egress: listening on 172.30.0.2:8888",
        "vaibify-egress: CONNECT api.anthropic.com:443 allowed",
        "vaibify-egress: CONNECT evil.example.org:443 refused host not on allowlist",
        "vaibify-egress: CONNECT evil.example.org:443 refused host not on allowlist",
        "vaibify-egress: CONNECT 10.0.0.5:22 refused raw-address CONNECT target refused",
    ]) + "\n")
    dictDescribed = projectEgress.fdictDescribeProjectEgress(S_UUID)
    assert dictDescribed["sProxyState"] == "running"
    assert dictDescribed["listRefused"] == [
        {"sHost": "evil.example.org", "iPort": 443, "iCount": 2,
         "sReason": "host not on allowlist", "bAllowable": True},
        {"sHost": "10.0.0.5", "iPort": 22, "iCount": 1,
         "sReason": "raw-address CONNECT target refused", "bAllowable": True},
    ]


def testAnAbsentProxyIsSaidToBeAbsentAndADeadOneDown(docker):
    docker.dictAnswers["inspect --format {{.State.Running}}"] = (1, "No such object")
    assert projectEgress.fdictDescribeProjectEgress(S_UUID) == {
        "sProxyState": "absent", "sPin": "absent", "iPinnedHubPort": 0,
        "listRefused": []}
    docker.dictAnswers["inspect --format {{.State.Running}}"] = (0, "false\n")
    assert projectEgress.fsProxyState(S_UUID) == "down"


@pytest.mark.falsification
def testAnIdentityThatIsNotOneNamesNoResource():
    """Kills: naming a network or proxy after an unvalidated identity."""
    with pytest.raises(egressProxy.EgressSetupError):
        projectEgress.fsNetworkName("../escape")
    with pytest.raises(egressProxy.EgressSetupError):
        projectEgress.fsProxyName("B" * 32)


def testOnlyAWebModeProxyMayTunnelToAnyPublicHost():
    def flistRunArguments(sNetworkMode):
        config = _fconfig()
        config.sNetworkMode = sNetworkMode
        return projectEgress.flistProxyRunArguments(
            config, S_UUID, S_NETWORK, "/script.py", "172.30.0.0/16")

    assert "--any-public-host" in flistRunArguments("web")
    assert "--any-public-host" not in flistRunArguments("allowlist")
    assert "Engine 28.0.0" in projectEgress.S_ENGINE_REQUIREMENT
    assert "networkMode: open" in projectEgress.S_ENGINE_REQUIREMENT


S_REPINNABLE_COMMAND = (
    '["python","/vaibifyEgress/vaibifyEgressProxy.py","--hub-host",'
    '"host.docker.internal","--hub-port-file","/vaibifyEgressGateway/hubPort"]\n')
S_OLDER_COMMAND = (
    '["python","/vaibifyEgress/vaibifyEgressProxy.py",'
    '"--hub-gateway","host.docker.internal:8051"]\n')


def _fsReadPinFile():
    return open(projectEgress._fsHubPortPath(S_UUID), encoding="ascii").read()


@pytest.mark.falsification
def testTheLaunchServesNoHubWhateverPortIsPreferred(docker):
    """The launch once pinned the per-user PREFERRED port, not the hub's.

    A preference of 8051 is persisted first; nothing the launch writes
    or runs may name it, and the pin it leaves names no hub at all.

    Kills: the launch writing a hub port into the pin.
    """
    from vaibify.config.hubPortRegistry import fnPersistHubPort
    fnPersistHubPort(8051)
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    listRun = docker.flistOf("run")[0]
    assert not any("8051" in sArgument for sArgument in listRun)
    assert _fsReadPinFile() == ""
    assert oct(os.stat(projectEgress._fsHubPortDirectory(S_UUID)).st_mode & 0o777) == "0o755"
    assert oct(os.stat(os.path.dirname(
        projectEgress._fsHubPortDirectory(S_UUID))).st_mode & 0o777) == "0o700"


@pytest.mark.falsification
def testAConnectingHubRepinsAFilePinnedProxyToItsOwnPort(docker):
    """Kills: pinning that never writes the port the connecting hub listens on."""
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    docker.dictAnswers["inspect --format {{json .Config.Cmd}}"] = (0, S_REPINNABLE_COMMAND)
    assert projectEgress.fsPinProjectEgressHub(S_UUID, 8053) == projectEgress.S_PIN_PINNED
    assert _fsReadPinFile() == "8053"
    assert projectEgress.fdictReadHubPin(S_UUID) == {"sPin": "pinned", "iPinnedHubPort": 8053}
    assert projectEgress.fsPinProjectEgressHub(S_UUID, 9017) == projectEgress.S_PIN_PINNED
    assert _fsReadPinFile() == "9017"


def testAnUnchangedPinIsNotRewritten(docker):
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    docker.dictAnswers["inspect --format {{json .Config.Cmd}}"] = (0, S_REPINNABLE_COMMAND)
    projectEgress.fsPinProjectEgressHub(S_UUID, 8053)
    iInodeBefore = os.stat(projectEgress._fsHubPortPath(S_UUID)).st_ino
    projectEgress.fsPinProjectEgressHub(S_UUID, 8053)
    assert os.stat(projectEgress._fsHubPortPath(S_UUID)).st_ino == iInodeBefore


def testAnOlderProxyIsReportedFixedAndLeftAlone(docker):
    docker.dictAnswers["inspect --format {{json .Config.Cmd}}"] = (0, S_OLDER_COMMAND)
    assert projectEgress.fsPinProjectEgressHub(S_UUID, 8053) == projectEgress.S_PIN_FIXED
    assert not os.path.exists(projectEgress._fsHubPortPath(S_UUID))
    assert projectEgress.fdictReadHubPin(S_UUID) == {"sPin": "fixed", "iPinnedHubPort": 8051}


def testNoProxyMeansNothingToPin(docker):
    docker.dictAnswers["inspect --format {{json .Config.Cmd}}"] = (1, "No such object")
    assert projectEgress.fsPinProjectEgressHub(S_UUID, 8053) == projectEgress.S_PIN_ABSENT
    assert projectEgress.fdictReadHubPin(S_UUID) == {"sPin": "absent", "iPinnedHubPort": 0}


@pytest.mark.parametrize("iHubPort", [0, 80, 1023, 65536, "8053", True])
def testOnlyAPortAHubListensOnIsPinned(docker, iHubPort):
    with pytest.raises(egressProxy.EgressSetupError):
        projectEgress.fsPinProjectEgressHub(S_UUID, iHubPort)


@pytest.mark.falsification
def testHubRefusalsAreNotAllowableAndThoseOfTheServedPortAreDropped(docker):
    """A refusal of the hub's host offered "Allow host", which changes nothing.

    Kills: offering the allowlist remedy for a refusal of the hub's host.
    """
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID)
    docker.dictAnswers["inspect --format {{json .Config.Cmd}}"] = (0, S_REPINNABLE_COMMAND)
    projectEgress.fsPinProjectEgressHub(S_UUID, 8053)
    docker.dictAnswers["logs"] = (0, "\n".join([
        "vaibify-egress: CONNECT host.docker.internal:8053 refused only the hub's "
        "port is served on the host: this container's hub is port 8051, not 8053",
        "vaibify-egress: CONNECT host.docker.internal:5432 refused only the hub's "
        "port is served on the host: this container's hub is port 8053, not 5432",
        "vaibify-egress: CONNECT data.example.net:443 refused host not on allowlist",
    ]) + "\n")
    dictDescribed = projectEgress.fdictDescribeProjectEgress(S_UUID)
    assert dictDescribed["sPin"] == "pinned" and dictDescribed["iPinnedHubPort"] == 8053
    assert [(dictEntry["sHost"], dictEntry["iPort"], dictEntry["bAllowable"])
            for dictEntry in dictDescribed["listRefused"]] == [
        ("host.docker.internal", 5432, False), ("data.example.net", 443, True)]
