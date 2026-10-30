"""A project's sealed network and proxy sidecar, driven against a scripted docker.

Every ``docker`` command the module runs is recorded and answered from
a script, so these prove what the module ASKS the daemon for and how it
reads the answers: the isolated gateway mode, the refusal of an engine
that leaves a gateway, the sidecar's hardened posture and its pinned
hub, the release, the reconcile, and the refused-destination tally.
The live counterpart is ``tests/testProjectEgressLive.py``.
"""

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
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID, 8050)
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
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID, 8050)
    assert ["network", "rm", S_NETWORK] in docker.listCommands
    assert docker.flistOf("run") == []


def testAnEngineThatRejectsTheOptionIsRefusedByName(docker):
    docker.dictAnswers["network create"] = (1, "invalid option: gateway_mode_ipv4")
    with pytest.raises(egressProxy.EgressSetupError) as errorRaised:
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID, 8050)
    assert "28.0.0" in str(errorRaised.value) and "gateway_mode_ipv4" in str(errorRaised.value)


def testTheProxyIsHardenedPinnedToTheHubAndForwardsThePublishedPorts(docker, tmp_path):
    dictEgress = projectEgress.fdictPrepareProjectEgress(
        _fconfig(listPorts=[{"host": 8888, "container": 8888}]), S_UUID, 8050)
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
    assert listCommand[listCommand.index("--hub-gateway") + 1] == "host.docker.internal:8050"
    assert listCommand[listCommand.index("--bind-cidr") + 1] == "172.30.0.0/16"
    assert listCommand[listCommand.index("--forward") + 1] == "8888=sealedProject:8888"
    assert "data.example.org" in listCommand[listCommand.index("--allowlist") + 1]
    sMount = listRun[listRun.index("--mount") + 1]
    assert "readonly" in sMount and str(tmp_path) in sMount
    assert ["network", "connect", "bridge", S_PROXY] in docker.listCommands


def testAProxyThatNeverListensIsRemovedAndTheLaunchRefused(docker, monkeypatch):
    monkeypatch.setattr(egressProxy, "F_PROXY_READY_DEADLINE_SECONDS", 0.01)
    docker.dictAnswers["logs"] = (0, "starting\n")
    with pytest.raises(egressProxy.EgressSetupError, match="never reported"):
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID, 8050)
    assert ["rm", "-f", S_PROXY] in docker.listCommands


def testAProxyThatExitsOnStartupReportsItsLog(docker):
    docker.dictAnswers["logs"] = (0, "")
    docker.dictAnswers["inspect --format {{.State.Running}}"] = (0, "false\n")
    with pytest.raises(egressProxy.EgressSetupError, match="exited on startup"):
        projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID, 8050)


def testTheReleaseRemovesProxyNetworkAndScript(docker, tmp_path):
    projectEgress.fdictPrepareProjectEgress(_fconfig(), S_UUID, 8050)
    assert (tmp_path / ".vaibify" / "egress" / S_UUID).is_dir()
    projectEgress.fnReleaseProjectEgressQuietly(S_UUID)
    assert ["rm", "-f", S_PROXY] in docker.listCommands[-3:]
    assert ["network", "rm", S_NETWORK] in docker.listCommands[-3:]
    assert not (tmp_path / ".vaibify" / "egress" / S_UUID).exists()
    projectEgress.fnReleaseProjectEgressQuietly("not an identity")


def testTheReconcileReleasesOnlyIdentitiesWithoutAContainer(docker):
    docker.dictAnswers["ps -a --filter"] = (0, f"{S_UUID}\n{'c' * 32}\n")
    docker.dictAnswers["network ls --filter"] = (0, f"{'d' * 32}\n")
    dictOutcomes = projectEgress.fdictReconcileProjectEgress(
        lambda sUuid: sUuid == S_UUID)
    assert dictOutcomes == {S_UUID: "kept", "c" * 32: "released", "d" * 32: "released"}
    assert ["rm", "-f", f"vaibifyProjectProxy-{'c' * 32}"] in docker.listCommands
    assert ["rm", "-f", S_PROXY] not in docker.listCommands


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
         "sReason": "host not on allowlist"},
        {"sHost": "10.0.0.5", "iPort": 22, "iCount": 1,
         "sReason": "raw-address CONNECT target refused"},
    ]


def testAnAbsentProxyIsSaidToBeAbsentAndADeadOneDown(docker):
    docker.dictAnswers["inspect --format {{.State.Running}}"] = (1, "No such object")
    assert projectEgress.fdictDescribeProjectEgress(S_UUID) == {
        "sProxyState": "absent", "listRefused": []}
    docker.dictAnswers["inspect --format {{.State.Running}}"] = (0, "false\n")
    assert projectEgress.fsProxyState(S_UUID) == "down"


@pytest.mark.falsification
def testAnIdentityThatIsNotOneNamesNoResource():
    """Kills: naming a network or proxy after an unvalidated identity."""
    with pytest.raises(egressProxy.EgressSetupError):
        projectEgress.fsNetworkName("../escape")
    with pytest.raises(egressProxy.EgressSetupError):
        projectEgress.fsProxyName("B" * 32)
