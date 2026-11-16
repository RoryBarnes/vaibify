"""The network mode: one setting with an old spelling, and what a launch does with it.

Ruling D1 (revised 2026-11-04): ``networkMode`` is the setting;
``networkIsolation`` is its old spelling, whose ``true`` still means
isolated, whose ``false`` names no mode, and which is refused when the
two disagree. A file that names no mode is UNSTATED: it loads, it is
shown, and no launch path builds a single argument for it. Ruling D4:
a display channel and a sealing mode exclude each other. The launch in
allowlist mode joins the sealed network with its resolver black-holed
and every client pointed at the proxy, and is refused outright when no
proxy was prepared.
"""

from types import SimpleNamespace

import pytest

from vaibify.cli.configFieldPreflight import flistDescribeUnusableConfiguration
from vaibify.config import projectConfig
from vaibify.docker import containerManager, egressAllowlist, egressProxy


# The arguments built here are the subject; the reach-approval gate has
# its own guard (tests/conftest.py::fixtureReachApproved explains).
pytestmark = pytest.mark.usefixtures("fixtureReachApproved")

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

def testTheOldSpellingTrueStillMeansIsolatedAndFalseNamesNoMode():
    assert projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkIsolation": True}).sNetworkMode == "isolated"
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkIsolation": False})
    assert config.sNetworkMode == projectConfig.S_NETWORK_MODE_UNSTATED
    assert config.bNetworkIsolation is False


def testTheModeAloneDerivesTheOldSpelling():
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkMode": "isolated"})
    assert config.bNetworkIsolation is True
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkMode": "allowlist"})
    assert config.bNetworkIsolation is False


def testAFileThatNamesNeitherIsUnstated():
    config = projectConfig.fconfigFromYamlDict({"projectName": "demo"})
    assert (config.sNetworkMode, config.bNetworkIsolation, config.listAllowedHosts) == (
        projectConfig.S_NETWORK_MODE_UNSTATED, False, [])
    assert projectConfig.S_NETWORK_MODE_UNSTATED not in projectConfig.T_NETWORK_MODES
    assert projectConfig.fbIsSealedNetworkMode(
        projectConfig.S_NETWORK_MODE_UNSTATED) is False


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
    assert containerManager.fsNetworkModeOf(SimpleNamespace()) == (
        projectConfig.S_NETWORK_MODE_UNSTATED)
    assert containerManager.fsNetworkModeOf(
        SimpleNamespace(bNetworkIsolation=False)) == (
        projectConfig.S_NETWORK_MODE_UNSTATED)


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
    assert set(egressAllowlist.T_ARXIV_HOSTS) <= set(listHosts)
    for sPublishingHost in ("zenodo.org", "git.overleaf.com"):
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


# ----- the web mode -----------------------------------------------------------


@pytest.mark.falsification
def testAWebLaunchIsSealed():
    """Web mode is allowlist mode's seal: the internal network, no resolver, the proxy.

    Kills: the launch still returning early for any mode but allowlist.
    """
    saRunArgs = containerManager.flistBuildRunArgs(
        _fconfigForLaunch("web"), dictEgress=DICT_EGRESS)
    assert saRunArgs[saRunArgs.index("--network") + 1] == DICT_EGRESS["sNetworkName"]
    assert saRunArgs[saRunArgs.index("--dns") + 1] == egressProxy.S_BLACK_HOLE_NAMESERVER
    assert "HTTPS_PROXY=http://172.30.0.2:8888" in saRunArgs
    assert "VAIBIFY_NETWORK_MODE=web" in saRunArgs
    assert "host.docker.internal:host-gateway" not in saRunArgs
    with pytest.raises(RuntimeError, match="networkMode: web.*not started open"):
        containerManager.flistBuildRunArgs(_fconfigForLaunch("web"))


def testWebModeRefusesX11AndNeedsARegisteredProject(monkeypatch):
    with pytest.raises(RuntimeError, match="networkMode: web"):
        containerManager.flistBuildRunArgs(
            _fconfigForLaunch("web", bX11Forwarding=True), dictEgress=DICT_EGRESS)
    assert "web" in flistDescribeUnusableConfiguration(SimpleNamespace(
        bX11Forwarding=True, bNetworkIsolation=False, sNetworkMode="web"))[0]
    monkeypatch.setattr(containerManager, "_fsProjectUuidOrEmpty", lambda sName: "")
    with pytest.raises(RuntimeError, match="not a registered project"):
        containerManager.fdictPrepareEgressForLaunch(_fconfigForLaunch("web"))


@pytest.mark.falsification
def testAKeylessLegacyFileIsUnstatedAndCannotStart(tmp_path):
    """An existing project that never named a mode is refused until it does.

    Nothing is reinterpreted in either direction: the file is not read
    as open (the old default) and not as web (the new-project mode).

    Kills: deriving open again for a file that names no mode.
    """
    sPath = str(tmp_path / "vaibify.yml")
    with open(sPath, "w", encoding="utf-8") as fileYaml:
        fileYaml.write("projectName: legacyProject\n")
    configLegacy = projectConfig.fconfigLoadFromFile(sPath)
    assert (configLegacy.sNetworkMode, configLegacy.bNetworkIsolation) == (
        projectConfig.S_NETWORK_MODE_UNSTATED, False)
    assert projectConfig.ProjectConfig(sProjectName="legacyProject").sNetworkMode == (
        projectConfig.S_NETWORK_MODE_UNSTATED)
    with pytest.raises(RuntimeError, match="names no networkMode"):
        containerManager.flistBuildRunArgs(configLegacy)
    assert projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "networkMode": "web"}).sNetworkMode == "web"


def _fnIsolateTheRegistry(tmp_path, monkeypatch):
    from vaibify.config import registryManager
    sDirectory = str(tmp_path / "registryHome")
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sDirectory)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_PATH", sDirectory + "/registry.json")
    monkeypatch.setattr(registryManager, "_S_LOCK_PATH", sDirectory + "/registry.lock")


def _fsModeWrittenByInit(tmp_path, monkeypatch, sFolder, listArguments):
    from click.testing import CliRunner
    from vaibify.cli import commandInit
    pathProject = tmp_path / sFolder
    pathProject.mkdir()
    monkeypatch.chdir(pathProject)
    resultInit = CliRunner().invoke(commandInit.fnInitCommand, listArguments)
    assert resultInit.exit_code == 0, resultInit.output
    return projectConfig.fconfigLoadFromFile(str(pathProject / "vaibify.yml")).sNetworkMode


@pytest.mark.falsification
def testEveryNewProjectPathWritesWeb(tmp_path, monkeypatch):
    """Plain, minimal and template init, and the creation route, all start at web.

    Kills: init leaving the mode to the configuration's own default,
    which is no mode at all (unstated), so the new project could not
    start.
    """
    from vaibify.cli import commandInit
    from vaibify.config import templateManager
    from vaibify.gui import registryRoutes
    _fnIsolateTheRegistry(tmp_path, monkeypatch)
    pathTemplates = tmp_path / "templates"
    (pathTemplates / "custom").mkdir(parents=True)
    (pathTemplates / "custom" / "container.conf").write_text("")
    monkeypatch.setattr(commandInit, "fsTemplatePath", lambda sName: str(pathTemplates / sName))
    monkeypatch.setattr(templateManager, "_PATH_TEMPLATES", pathTemplates)
    assert _fsModeWrittenByInit(tmp_path, monkeypatch, "plain", ["--name", "plainProject"]) == "web"
    assert _fsModeWrittenByInit(
        tmp_path, monkeypatch, "minimal", ["--name", "minimalProject", "--minimal"]) == "web"
    assert _fsModeWrittenByInit(
        tmp_path, monkeypatch, "template",
        ["--template", "custom", "--name", "templateProject"]) == "web"
    requestBare = registryRoutes.CreateProjectRequest(
        sDirectory=str(tmp_path), sProjectName="routeProject", sTemplateName="")
    assert registryRoutes.fsNetworkModeOfRequest(requestBare) == "web"
    requestOld = registryRoutes.CreateProjectRequest(
        sDirectory=str(tmp_path), sProjectName="routeProject", sTemplateName="",
        bNetworkIsolation=True)
    assert registryRoutes.fsNetworkModeOfRequest(requestOld) == "isolated"


def testTheNetworkRowSaysWhetherTheModeIsSealed(monkeypatch):
    from vaibify.config import registryManager
    from vaibify.gui import pipelineServer
    from vaibify.gui.routes import systemRoutes
    from vaibify.docker import projectEgress
    monkeypatch.setattr(
        projectEgress, "fdictDescribeProjectEgress",
        lambda sProjectUuid: {"sProxyState": "running", "listRefused": []})
    monkeypatch.setattr(pipelineServer, "fsContainerNameForId", lambda *tArgs: "sealedProject")
    monkeypatch.setattr(registryManager, "fdictGetProject", lambda sName: {"sProjectUuid": "a" * 32})
    for sMode, bSealed in (("web", True), ("allowlist", True), ("open", False),
                           ("isolated", False)):
        monkeypatch.setattr(
            systemRoutes, "_fconfigForContainerOrNone",
            lambda *tArgs, sMode=sMode: SimpleNamespace(sNetworkMode=sMode))
        dictNetwork = systemRoutes._fdictDescribeNetwork(None, "cid")
        assert (dictNetwork["sMode"], dictNetwork["bSealed"]) == (sMode, bSealed)
        assert (dictNetwork["sProxyState"] == "running") is bSealed
    monkeypatch.setattr(
        systemRoutes, "_fconfigForContainerOrNone",
        lambda *tArgs: SimpleNamespace(sNetworkMode="", bNetworkIsolation=False))
    dictNetwork = systemRoutes._fdictDescribeNetwork(None, "cid")
    assert (dictNetwork["sMode"], dictNetwork["bSealed"]) == ("unstated", False)


# ----- the unstated mode (ruling D1, revised 2026-11-04) ----------------------


def _fnRefuseEveryLaunchCommand(monkeypatch):
    monkeypatch.setattr(
        containerManager, "_fsRunDetachedCommand",
        lambda saCommand: pytest.fail("a container was launched"))
    monkeypatch.setattr(
        containerManager, "_fnRunDockerCommand",
        lambda saCommand: pytest.fail("a container was launched"))
    monkeypatch.setattr(
        containerManager, "_fsRunKillableDockerCommand",
        lambda *tArgs, **dictKeywords: pytest.fail("a container was created"))
    monkeypatch.setattr(
        containerManager, "fdictResolveLaunchPosture",
        lambda sProjectName, sImageReference="": {
            "sMode": "built", "bWithCredentials": True, "sImageDigest": "sha256:built"})


@pytest.mark.falsification
def testEveryLaunchFunctionRefusesAnUnstatedModeBeforeAnyArgumentIsBuilt(monkeypatch):
    """Kills: the run-argument builder no longer asking for a stated mode first."""
    _fnRefuseEveryLaunchCommand(monkeypatch)
    listBuilt = []
    monkeypatch.setattr(
        containerManager, "_flistBuildProcessModeArgs",
        lambda *tArgs: listBuilt.append(tArgs) or [])
    config = _fconfigForLaunch("")
    sReservation = "0123456789abcdef0123456789abcdef"
    for fnLaunch in (
        lambda: containerManager.flistBuildRunArgs(config),
        lambda: containerManager.fnStartContainer(config, "/d"),
        lambda: containerManager.fsStartContainerDetached(config, "/d"),
        lambda: containerManager.fsCreateContainerForReservation(config, sReservation),
        lambda: containerManager.fsRecreateContainerDetachedFromImage(config, "sha256:x"),
    ):
        with pytest.raises(RuntimeError, match="names no networkMode"):
            fnLaunch()
    assert listBuilt == []


def testNothingReadsAnUnstatedModeAsOpen(monkeypatch):
    configUnstated = _fconfigForLaunch("")
    assert containerManager.fsNetworkModeOf(configUnstated) == ""
    assert containerManager._fbAgentBridgeRequired(configUnstated) is False
    assert containerManager.fdictPrepareEgressForLaunch(configUnstated) is None
    assert containerManager._fbAgentBridgeRequired(_fconfigForLaunch("open")) is True


def testASavedUnstatedConfigurationWritesNoModeKeyAndLoadsBackUnstated(tmp_path):
    sPath = str(tmp_path / "vaibify.yml")
    projectConfig.fnSaveToFile(projectConfig.ProjectConfig(sProjectName="demo"), sPath)
    with open(sPath, encoding="utf-8") as fileYaml:
        sWritten = fileYaml.read()
    assert "networkMode" not in sWritten
    configLoaded = projectConfig.fconfigLoadFromFile(sPath)
    assert configLoaded.sNetworkMode == projectConfig.S_NETWORK_MODE_UNSTATED
    assert "networkMode" not in projectConfig.fdictLoadDefaults()
    assert projectConfig.fbValidateConfig({"projectName": "demo", "networkMode": ""}) is False


def testTheConfigurationPreflightComplainsWithTheOneSentence():
    from vaibify.cli.configFieldPreflight import (
        flistDescribeUnusableProjectConfiguration, fpreflightConfigurationFields,
    )
    configUnstated = projectConfig.ProjectConfig(sProjectName="legacyProject")
    listComplaints = flistDescribeUnusableProjectConfiguration(configUnstated)
    assert listComplaints == [
        projectConfig.fsDescribeUnstatedNetworkMode("legacyProject")]
    assert fpreflightConfigurationFields(configUnstated).sLevel == "fail"
    assert flistDescribeUnusableProjectConfiguration(
        projectConfig.ProjectConfig(sProjectName="demo", sNetworkMode="web")) == []
    assert flistDescribeUnusableConfiguration(configUnstated) == []


def testTheBuildRouteRefusesAnUnstatedModeBeforeBuilding(monkeypatch):
    from vaibify.gui import buildRoutes
    configUnstated = projectConfig.ProjectConfig(sProjectName="legacyProject")
    listChecks = buildRoutes._flistConfigurationPreflights()
    fpreflightFields, sRefusal = listChecks[0]
    preflightResult = fpreflightFields(configUnstated)
    assert preflightResult.sLevel == "fail"
    assert "names no networkMode" in preflightResult.sMessage
    assert sRefusal == buildRoutes.S_REFUSAL_UNUSABLE_CONFIGURATION
