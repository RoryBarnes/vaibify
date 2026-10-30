"""The network mode: one setting with an old spelling, and what a launch does with it.

Ruling D1: ``networkMode`` is the setting; ``networkIsolation`` is its
old spelling, read as it always was, derived when absent, and refused
when the two disagree. Ruling D4: a display channel and a sealing mode
exclude each other. The launch in allowlist mode joins the sealed
network with its resolver black-holed and every client pointed at the
proxy, and is refused outright when no proxy was prepared.
"""

from types import SimpleNamespace

import pytest

from vaibify.cli.configFieldPreflight import flistDescribeUnusableConfiguration
from vaibify.config import projectConfig
from vaibify.docker import containerManager, egressAllowlist, egressProxy


def _fconfigForLaunch(sNetworkMode="open", bX11Forwarding=False, **dictExtra):
    features = SimpleNamespace(
        bGpu=False, bClaude=True, bClaudeAutoUpdate=True, bCodex=False,
        bGemini=False, bAntigravity=False, bOpenCode=False, bCline=False,
        bOpenHands=False, bPi=False,
    )
    return SimpleNamespace(
        sProjectName="sealedProject", sWorkspaceRoot="/workspace",
        sContainerUser="researcher", listPorts=[], listBindMounts=[],
        listSecrets=[], features=features, sNetworkMode=sNetworkMode,
        bNetworkIsolation=sNetworkMode == "isolated",
        bX11Forwarding=bX11Forwarding, sPackageManager="pip",
        listRepositories=[], listAllowedHosts=[], **dictExtra,
    )


DICT_EGRESS = {"sNetworkName": "vaibifyProjectEgress-" + "a" * 32,
               "sProxyName": "vaibifyProjectProxy-" + "a" * 32,
               "sProxyAddress": "172.30.0.2"}


# ----- the configuration ---------------------------------------------------

def testTheOldSpellingAloneStillMeansWhatItMeant():
    assert projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkIsolation": True}).sNetworkMode == "isolated"
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkIsolation": False})
    assert config.sNetworkMode == "open" and config.bNetworkIsolation is False


def testTheModeAloneDerivesTheOldSpelling():
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkMode": "isolated"})
    assert config.bNetworkIsolation is True
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkMode": "allowlist"})
    assert config.bNetworkIsolation is False


def testTheDefaultIsOpenForAFileThatNamesNeither():
    config = projectConfig.fconfigFromYamlDict({"projectName": "demo"})
    assert (config.sNetworkMode, config.bNetworkIsolation, config.listAllowedHosts) == (
        "open", False, [])


@pytest.mark.falsification
def testAFileWhoseTwoSpellingsDisagreeIsRefused(tmp_path):
    """Two keys for one setting must say one thing, or the launch cannot know which.

    Kills: reading networkMode and ignoring a contradicting networkIsolation.
    """
    assert projectConfig.fbValidateConfig(
        {"projectName": "demo", "networkMode": "open", "networkIsolation": True}) is False
    assert projectConfig.fbValidateConfig(
        {"projectName": "demo", "networkMode": "isolated", "networkIsolation": False}) is False
    assert projectConfig.fbValidateConfig(
        {"projectName": "demo", "networkMode": "isolated", "networkIsolation": True}) is True
    sPath = str(tmp_path / "vaibify.yml")
    with open(sPath, "w", encoding="utf-8") as fileYaml:
        fileYaml.write("projectName: demo\nnetworkMode: allowlist\nnetworkIsolation: true\n")
    with pytest.raises(ValueError, match="disagree"):
        projectConfig.fconfigLoadFromFile(sPath)


def testAFileWithOnlyTheOldSpellingLoadsAsIsolated(tmp_path):
    sPath = str(tmp_path / "vaibify.yml")
    with open(sPath, "w", encoding="utf-8") as fileYaml:
        fileYaml.write("projectName: demo\nnetworkIsolation: true\n")
    config = projectConfig.fconfigLoadFromFile(sPath)
    assert (config.sNetworkMode, config.bNetworkIsolation) == ("isolated", True)


@pytest.mark.parametrize("valueBad", [
    {"networkMode": "sealed"}, {"allowedHosts": "pypi.org"},
    {"allowedHosts": ["bad host"]}, {"allowedHosts": ["pypi.org:70000"]},
    {"allowedHosts": [7]},
])
def testAnUnknownModeOrAMalformedHostIsRefused(valueBad):
    assert projectConfig.fbValidateConfig(dict({"projectName": "demo"}, **valueBad)) is False


def testTheModeAndTheHostsRoundTripThroughYaml(tmp_path):
    sPath = str(tmp_path / "vaibify.yml")
    config = projectConfig.fconfigFromYamlDict({
        "projectName": "demo", "networkMode": "allowlist",
        "allowedHosts": ["data.example.org", "mirror.example.org:8443"],
    })
    projectConfig.fnSaveToFile(config, sPath)
    configLoaded = projectConfig.fconfigLoadFromFile(sPath)
    assert configLoaded.sNetworkMode == "allowlist"
    assert configLoaded.listAllowedHosts == ["data.example.org", "mirror.example.org:8443"]
    assert configLoaded.bNetworkIsolation is False


# ----- the launch ----------------------------------------------------------

def testIsolatedModeStillSealsWithNoNetwork():
    saRunArgs = containerManager.flistBuildRunArgs(_fconfigForLaunch("isolated"))
    assert saRunArgs[saRunArgs.index("--network") + 1] == "none"
    assert "VAIBIFY_NETWORK_ISOLATED=true" in saRunArgs


def testOpenModeAddsNoNetworkArgumentsAndBridgesTheAgent():
    saRunArgs = containerManager.flistBuildRunArgs(_fconfigForLaunch("open"))
    assert "--network" not in saRunArgs
    assert "host.docker.internal:host-gateway" in saRunArgs


@pytest.mark.falsification
def testAllowlistModeJoinsTheSealedNetworkBehindTheProxy():
    """Kills: starting an allowlist-mode container on the default bridge."""
    saRunArgs = containerManager.flistBuildRunArgs(
        _fconfigForLaunch("allowlist"), dictEgress=DICT_EGRESS)
    assert saRunArgs[saRunArgs.index("--network") + 1] == DICT_EGRESS["sNetworkName"]
    assert saRunArgs[saRunArgs.index("--dns") + 1] == egressProxy.S_BLACK_HOLE_NAMESERVER
    assert "HTTPS_PROXY=http://172.30.0.2:8888" in saRunArgs
    assert "https_proxy=http://172.30.0.2:8888" in saRunArgs
    assert "VAIBIFY_NETWORK_MODE=allowlist" in saRunArgs
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1" in saRunArgs
    # The hub is reached THROUGH the proxy, which pins it: no direct bridge.
    assert "host.docker.internal:host-gateway" not in saRunArgs
    assert not any(sArg.startswith("NO_PROXY=") and "host.docker" in sArg for sArg in saRunArgs)


@pytest.mark.falsification
def testAllowlistModeWithoutAPreparedProxyIsRefusedNotStartedOpen():
    """Kills: falling back to an open network when the proxy could not be made."""
    with pytest.raises(RuntimeError, match="not started open"):
        containerManager.flistBuildRunArgs(_fconfigForLaunch("allowlist"))
    with pytest.raises(RuntimeError, match="not started open"):
        containerManager.flistBuildRunArgs(
            _fconfigForLaunch("allowlist"), dictEgress={"sNetworkName": "x"})


@pytest.mark.falsification
def testX11WithAllowlistModeIsRefusedEverywhereItIsAsked():
    """Ruling D4: a display channel into a sealed container defeats the seal.

    Kills: letting X11 forwarding through beside allowlist mode.
    """
    with pytest.raises(RuntimeError) as errorRaised:
        containerManager.flistBuildRunArgs(
            _fconfigForLaunch("allowlist", bX11Forwarding=True), dictEgress=DICT_EGRESS)
    assert "allowlist" in str(errorRaised.value) and "x11Forwarding" in str(errorRaised.value)
    listComplaints = flistDescribeUnusableConfiguration(
        SimpleNamespace(bX11Forwarding=True, bNetworkIsolation=False, sNetworkMode="allowlist"))
    assert len(listComplaints) == 1 and "allowlist" in listComplaints[0]
    assert flistDescribeUnusableConfiguration(
        SimpleNamespace(bX11Forwarding=True, bNetworkIsolation=False, sNetworkMode="open")) == []


def testTheModeIsReadFromTheOldSpellingWhenAConfigPredatesIt():
    assert containerManager.fsNetworkModeOf(
        SimpleNamespace(bNetworkIsolation=True)) == "isolated"
    assert containerManager.fsNetworkModeOf(SimpleNamespace()) == "open"


def testAnUnregisteredProjectCannotLaunchInAllowlistMode(monkeypatch):
    monkeypatch.setattr(containerManager, "_fsProjectUuidOrEmpty", lambda sName: "")
    with pytest.raises(RuntimeError, match="not a registered project"):
        containerManager.fdictPrepareEgressForLaunch(_fconfigForLaunch("allowlist"))
    assert containerManager.fdictPrepareEgressForLaunch(_fconfigForLaunch("open")) is None


# ----- the allowlist ---------------------------------------------------------

def testTheBaseAllowlistNamesEachAgentsHostsGitHubAndTheIndexes():
    listHosts, listPorts = egressAllowlist.ftComputeAllowlist(_fconfigForLaunch("allowlist"))
    assert set(egressAllowlist.T_GITHUB_HOSTS) <= set(listHosts)
    assert set(egressAllowlist.T_PYPI_HOSTS) <= set(listHosts)
    assert set(egressAllowlist.DICT_AGENT_HOSTS["bClaude"]) <= set(listHosts)
    assert set(egressAllowlist.T_NPM_HOSTS) <= set(listHosts)
    assert not set(egressAllowlist.DICT_AGENT_HOSTS["bCodex"]) & set(listHosts)
    assert listPorts == [443]
    for sPublishingHost in ("zenodo.org", "git.overleaf.com", "arxiv.org"):
        assert sPublishingHost not in listHosts


def testCondaRepositoriesAndAllowedHostsJoinTheAllowlistWithTheirPorts():
    config = _fconfigForLaunch("allowlist")
    config.sPackageManager = "conda"
    config.listRepositories = [
        {"url": "https://gitlab.example.org/group/code.git"},
        {"url": "git@forge.example.org:group/other.git"},
    ]
    config.listAllowedHosts = ["data.example.org", "mirror.example.org:8443"]
    listHosts, listPorts = egressAllowlist.ftComputeAllowlist(config)
    assert set(egressAllowlist.T_CONDA_HOSTS) <= set(listHosts)
    assert {"gitlab.example.org", "forge.example.org", "data.example.org",
            "mirror.example.org"} <= set(listHosts)
    assert listPorts == [443, 8443]


def testAnAgentWithoutAutoUpdateNeedsNoNpm():
    config = _fconfigForLaunch("allowlist")
    config.features.bClaudeAutoUpdate = False
    listHosts, _ = egressAllowlist.ftComputeAllowlist(config)
    assert not set(egressAllowlist.T_NPM_HOSTS) & set(listHosts)


@pytest.mark.parametrize("sEntry, tExpected", [
    ("Data.Example.org", ("data.example.org", 443)),
    ("mirror.example.org:8443", ("mirror.example.org", 8443)),
])
def testAHostEntrySplitsIntoHostAndPort(sEntry, tExpected):
    assert egressAllowlist.ftSplitHostEntry(sEntry) == tExpected


@pytest.mark.parametrize("sEntry", [
    "", "bad host", "10.0.0.1:443", "10.0.0.1", "a:b", "x:0", "x:65536",
])
def testAMalformedOrRawAddressEntryIsRefused(sEntry):
    assert egressAllowlist.fbIsAllowedHostEntry(sEntry) is False
    with pytest.raises(ValueError):
        egressAllowlist.ftSplitHostEntry(sEntry)


def testARawAddressInAllowedHostsNeverReachesTheProxy():
    config = _fconfigForLaunch("allowlist")
    config.listAllowedHosts = ["10.0.0.1"]
    with pytest.raises(ValueError, match="raw address"):
        egressAllowlist.ftComputeAllowlist(config)
