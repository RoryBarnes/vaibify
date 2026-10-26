"""The Docker setup plan for every kind of host vaibify supports.

The planner is a pure function of the facts ``runtimeSetupFacts``
reads, so each row of the host matrix is driven from fixture evidence:
macOS with Homebrew, MacPorts or neither, Intel and Apple Silicon;
Ubuntu, Debian, Fedora, RHEL, CentOS and their declared derivatives;
an unsupported distribution; runtimes installed but stopped; and each
privilege channel. Fixture values are distinct per field (a 2-CPU
default machine never coincides with a 9-CPU host), so a fact the
planner ignores cannot pass by coincidence.
"""

import pytest

from vaibify.install import runtimeSetupPlan as planModule
from vaibify.install.runtimeSetupPlan import (
    S_KIND_ADMINISTRATOR,
    S_KIND_NEEDS_YOU,
    S_KIND_RUN,
    fdictDefaultColimaSettings,
    fdictPlanRuntimeSetup,
    fdictValidateColimaSettings,
)


I_GIGABYTE = 2 ** 30
S_OS_RELEASE_TEXTS = {
    "ubuntu": {"ID": "ubuntu", "PRETTY_NAME": "Ubuntu 24.04 LTS",
               "VERSION_CODENAME": "noble", "UBUNTU_CODENAME": "noble"},
    "debian": {"ID": "debian", "PRETTY_NAME": "Debian GNU/Linux 12",
               "VERSION_CODENAME": "bookworm"},
    "mint": {"ID": "linuxmint", "ID_LIKE": "ubuntu debian",
             "PRETTY_NAME": "Linux Mint 22", "UBUNTU_CODENAME": "noble"},
    "fedora": {"ID": "fedora", "PRETTY_NAME": "Fedora Linux 44"},
    "rhel": {"ID": "rhel", "PRETTY_NAME": "Red Hat Enterprise Linux 9"},
    "rocky": {"ID": "rocky", "ID_LIKE": "rhel centos fedora",
              "PRETTY_NAME": "Rocky Linux 9"},
    "arch": {"ID": "arch", "PRETTY_NAME": "Arch Linux"},
}


def fdictMacFacts(**dictOverrides):
    dictFacts = {
        "sPlatform": "darwin", "sArchitecture": "arm64",
        "sMacVersion": "15.4", "dictOsRelease": {},
        "listCommands": ["brew", "osascript", "sudo"],
        "sMacPortsPath": "", "sHomebrewPrefix": "/opt/homebrew",
        "bHomebrewBuildxPresent": False, "bBuildxInstalledUnlinked": False,
        "bDaemonAnswers": False, "bDaemonPermissionDenied": False,
        "bBuildxWorks": False, "sRuntime": "", "listColimaMachines": [],
        "listRunningVaibifyContainers": [], "listDesktopApplications": [],
        "sPrivilegeChannel": "macos-dialog", "sUserName": "researcherAlpha",
        "iHostCpuCount": 10, "iHostMemoryBytes": 16 * I_GIGABYTE,
        "sDockerHost": "",
    }
    dictFacts.update(dictOverrides)
    return dictFacts


def fdictLinuxFacts(sDistribution, **dictOverrides):
    dictFacts = fdictMacFacts(
        sPlatform="linux", sArchitecture="amd64", sMacVersion="",
        dictOsRelease=dict(S_OS_RELEASE_TEXTS[sDistribution]),
        listCommands=["apt-get", "sudo", "systemctl", "pkexec"],
        sHomebrewPrefix="", sPrivilegeChannel="pkexec",
        iHostCpuCount=6, iHostMemoryBytes=32 * I_GIGABYTE,
        bEngineServiceInstalled=False, bEngineServiceActive=False,
        bRootlessServiceInstalled=False, bDesktopServiceInstalled=False,
        bUserListedInDockerGroup=False, bProcessHasDockerGroup=False,
    )
    dictFacts.update(dictOverrides)
    return dictFacts


def fdictFirstStep(dictFacts, dictSettings=None):
    dictPlan = fdictPlanRuntimeSetup(dictFacts, dictSettings)
    assert dictPlan["listSteps"], dictPlan
    return dictPlan["listSteps"][0]


def flistFirstArgv(dictStep, iAction=0):
    return dictStep["listActions"][iAction]["listInvocations"][0]["listArgv"]


def fsFirstScript(dictStep, iAction=0):
    return dictStep["listActions"][iAction]["listInvocations"][0]["sScript"]


# ---------------------------------------------------------------------------
# Defaults and settings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iHostGigabytes, iExpected", [
    (64, 32), (16, 8), (12, 6), (8, 4), (6, 4),
])
def testANewColimaMachineGetsHalfTheMemoryWithinTheBounds(
        iHostGigabytes, iExpected):
    dictSettings = fdictDefaultColimaSettings(fdictMacFacts(
        iHostMemoryBytes=iHostGigabytes * I_GIGABYTE))
    assert dictSettings["iMemoryGigabytes"] == iExpected


def testANewColimaMachineGetsEveryCoreButOne():
    assert fdictDefaultColimaSettings(
        fdictMacFacts(iHostCpuCount=10))["iCpus"] == 9
    assert fdictDefaultColimaSettings(
        fdictMacFacts(iHostCpuCount=1))["iCpus"] == 1


@pytest.mark.falsification
@pytest.mark.parametrize("dictRequested", [
    {"iCpus": "4; rm -rf ~"}, {"iCpus": True}, {"iCpus": 0},
    {"iCpus": 11}, {"iMemoryGigabytes": 4.5}, {"iMemoryGigabytes": 17},
    {"iMemoryGigabytes": -1},
])
def testOnlyAWholeNumberThisMachineCanHoldIsAccepted(dictRequested):
    """Kills: a setting that reaches ``colima start`` unchecked."""
    with pytest.raises(ValueError):
        fdictValidateColimaSettings(dictRequested, fdictMacFacts())


def testAnAcceptedSettingReachesTheStartCommand():
    dictFacts = fdictMacFacts(listCommands=["brew", "colima", "docker"],
                              bBuildxWorks=True)
    dictSettings = fdictValidateColimaSettings(
        {"iCpus": 3, "iMemoryGigabytes": 7}, dictFacts)
    dictStep = fdictFirstStep(dictFacts, dictSettings)
    listArgv = flistFirstArgv(dictStep)
    assert listArgv[listArgv.index("--cpus") + 1] == "3"
    assert listArgv[listArgv.index("--memory") + 1] == "7"


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

def testAMacWithHomebrewInstallsWhatIsMissingThenStartsColima():
    dictPlan = fdictPlanRuntimeSetup(fdictMacFacts())
    listNames = [d["sStepName"] for d in dictPlan["listSteps"]]
    assert listNames == ["install-runtime", "start-colima"]
    dictInstall = dictPlan["listSteps"][0]
    assert dictInstall["sKind"] == S_KIND_RUN
    assert flistFirstArgv(dictInstall) == [
        "brew", "install", "colima", "docker", "docker-buildx"]


def testAMacWithMacPortsInstallsAsAdministratorByFullPath():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["port", "osascript"], sHomebrewPrefix="",
        sMacPortsPath="/opt/local/bin/port"))
    assert dictStep["sKind"] == S_KIND_ADMINISTRATOR
    assert fsFirstScript(dictStep).startswith(
        "/opt/local/bin/port -N install colima docker docker-buildx-plugin")


def testAMacWithNeitherPackageManagerIsToldToInstallOne():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["osascript"], sHomebrewPrefix=""))
    assert dictStep["sKind"] == S_KIND_NEEDS_YOU
    assert dictStep["sStepName"] == "install-package-manager"
    assert dictStep["listActions"] == []
    assert planModule.S_HOMEBREW_INSTALL_COMMAND in dictStep[
        "listCommandsToShow"]
    assert planModule.S_MACPORTS_INSTALL_PAGE in dictStep["sExplanation"]


@pytest.mark.parametrize("sArchitecture, sColimaArch", [
    ("arm64", "aarch64"), ("amd64", "x86_64"),
])
def testANewColimaMachineMatchesTheMacsOwnArchitecture(
        sArchitecture, sColimaArch):
    dictStep = fdictFirstStep(fdictMacFacts(
        sArchitecture=sArchitecture,
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True))
    listArgv = flistFirstArgv(dictStep)
    assert listArgv[:2] == ["colima", "start"]
    assert listArgv[listArgv.index("--arch") + 1] == sColimaArch
    assert listArgv[listArgv.index("--vm-type") + 1] == "vz"
    assert listArgv[listArgv.index("--cpus") + 1] == "9"
    assert listArgv[listArgv.index("--memory") + 1] == "8"


@pytest.mark.falsification
def testAStoppedColimaMachineStartsWithItsOwnSettings():
    """Kills: resize flags passed to an existing machine on a plain start."""
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        listColimaMachines=[{
            "sProfile": "work", "sStatus": "Stopped", "iCpus": 2,
            "iMemoryBytes": 6 * I_GIGABYTE, "sArchitecture": "aarch64"}]))
    assert dictStep["sStepName"] == "start-colima"
    assert flistFirstArgv(dictStep) == ["colima", "start", "--profile", "work"]


def _fdictUndersizedMacFacts(listRunning):
    return fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        bDaemonAnswers=True, sRuntime="colima",
        sActiveDockerContext="colima", sActiveColimaProfile="default",
        listRunningVaibifyContainers=listRunning,
        listColimaMachines=[{
            "sProfile": "default", "sStatus": "Running", "iCpus": 2,
            "iMemoryBytes": 2 * I_GIGABYTE, "sArchitecture": "aarch64"}])


def testAnUndersizedRunningMachineIsOfferedMoreMemoryAsAnOption():
    dictPlan = fdictPlanRuntimeSetup(_fdictUndersizedMacFacts([]))
    assert dictPlan["bReady"] is True
    dictStep = dictPlan["listSteps"][0]
    assert dictStep["bOptional"] is True
    listInvocations = dictStep["listActions"][0]["listInvocations"]
    assert listInvocations[0]["listArgv"] == ["colima", "stop"]
    assert listInvocations[1]["listArgv"][-4:] == [
        "--cpus", "9", "--memory", "8"]


@pytest.mark.falsification
@pytest.mark.parametrize("listRunning", [[], ["projectAlphaContainer"]])
def testOnlyAnUndersizedMachineIsAnnouncedOnTheHub(listRunning):
    """Kills: a missing buildx raising the hub banner on a working machine.

    The oracle is the 2026-10-26 ruling: builds without buildx still
    run, so only a machine too small to build in earns a banner.
    """
    dictFacts = dict(_fdictUndersizedMacFacts(listRunning), bBuildxWorks=False)
    dictPlan = fdictPlanRuntimeSetup(dictFacts)
    assert [d["sStepName"] for d in dictPlan["listSteps"]] == [
        "install-runtime", "resize-colima"]
    assert planModule.fdictStepAnnouncedOnHub(dictPlan)["sStepName"] == (
        "resize-colima")


def testAWorkingMachineWithoutBuildxIsNotAnnouncedOnTheHub():
    for dictFacts in (
            fdictMacFacts(listCommands=["brew", "colima", "docker"],
                          bDaemonAnswers=True, sRuntime="docker-desktop"),
            fdictLinuxFacts("ubuntu", bDaemonAnswers=True)):
        dictPlan = fdictPlanRuntimeSetup(dictFacts)
        assert dictPlan["listSteps"], dictPlan
        assert planModule.fdictStepAnnouncedOnHub(dictPlan) is None


def testAnUndersizedMachineWithRunningContainersIsNotRestarted():
    dictStep = fdictPlanRuntimeSetup(
        _fdictUndersizedMacFacts(["projectAlphaContainer"]))["listSteps"][0]
    assert dictStep["sKind"] == S_KIND_NEEDS_YOU
    assert dictStep["listActions"] == []
    assert "projectAlphaContainer" in dictStep["sExplanation"]


def _fdictMachine(sProfile, sStatus, iGigabytes):
    return {"sProfile": sProfile, "sStatus": sStatus, "iCpus": 2,
            "iMemoryBytes": iGigabytes * I_GIGABYTE,
            "sArchitecture": "aarch64"}


@pytest.mark.falsification
def testOnlyTheMachineDockerPointsAtIsOfferedARestart():
    """Two running machines; the containers were read from beta's daemon.

    Kills: restarting the first running machine, whose containers
    nobody checked.
    """
    dictStep = fdictPlanRuntimeSetup(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        bDaemonAnswers=True, sRuntime="colima",
        sActiveDockerContext="colima-beta", sActiveColimaProfile="beta",
        listColimaMachines=[_fdictMachine("alpha", "Running", 2),
                            _fdictMachine("beta", "Running", 2)],
    ))["listSteps"][0]
    listInvocations = dictStep["listActions"][0]["listInvocations"]
    assert listInvocations[0]["listArgv"] == [
        "colima", "stop", "--profile", "beta"]
    assert "'beta'" in dictStep["sExplanation"]


def testNoRestartIsOfferedWhenDockerNamesNoColimaMachine():
    dictPlan = fdictPlanRuntimeSetup(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        bDaemonAnswers=True, sRuntime="colima", sActiveColimaProfile="",
        listColimaMachines=[_fdictMachine("alpha", "Running", 2)]))
    assert dictPlan == {"bReady": True, "listSteps": []}


def testAStoppedMachineDockerPointsAtIsTheOneStarted():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        sActiveDockerContext="colima-beta", sActiveColimaProfile="beta",
        listColimaMachines=[_fdictMachine("default", "Stopped", 8),
                            _fdictMachine("beta", "Stopped", 8)]))
    assert flistFirstArgv(dictStep) == ["colima", "start", "--profile", "beta"]


@pytest.mark.falsification
def testTheDesktopApplicationDockerPointsAtBeatsAnOldColimaMachine():
    """Kills: an old Colima profile winning over the researcher's choice."""
    dictPlan = fdictPlanRuntimeSetup(fdictMacFacts(
        listCommands=["colima", "docker"], sActiveDockerContext="orbstack",
        listDesktopApplications=["OrbStack"],
        listColimaMachines=[_fdictMachine("default", "Stopped", 2)]))
    assert [d["sStepName"] for d in dictPlan["listSteps"]] == [
        "start-desktop-application"]
    assert flistFirstArgv(dictPlan["listSteps"][0]) == [
        "open", "-a", "OrbStack"]


def testWithNoContextToGoOnTheResearcherChoosesTheRuntime():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["colima", "docker"], sActiveDockerContext="default",
        listDesktopApplications=["OrbStack", "Docker"],
        listColimaMachines=[_fdictMachine("old", "Stopped", 2)]))
    assert dictStep["sStepName"] == "choose-runtime"
    assert [d["sLabel"] for d in dictStep["listActions"]] == [
        "Start OrbStack", "Start Docker", "Start Colima 'old'"]
    assert dictStep["listActions"][2]["listInvocations"][0]["listArgv"] == [
        "colima", "start", "--profile", "old"]


def testAColimaContextKeepsTheColimaPathBesideADesktopApplication():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        sActiveDockerContext="colima", sActiveColimaProfile="default",
        listDesktopApplications=["OrbStack"],
        listColimaMachines=[_fdictMachine("default", "Stopped", 8)]))
    assert flistFirstArgv(dictStep) == ["colima", "start"]


def testAStoppedDesktopApplicationIsStartedNotReplaced():
    dictPlan = fdictPlanRuntimeSetup(fdictMacFacts(
        listCommands=["docker"], listDesktopApplications=["OrbStack"]))
    assert [d["sStepName"] for d in dictPlan["listSteps"]] == [
        "start-desktop-application"]
    assert flistFirstArgv(dictPlan["listSteps"][0]) == [
        "open", "-a", "OrbStack"]


def testADaemonFromAnotherRuntimeIsNeverJoinedByColima():
    dictPlan = fdictPlanRuntimeSetup(fdictMacFacts(
        listCommands=["docker"], bDaemonAnswers=True, bBuildxWorks=True,
        sRuntime="docker-desktop"))
    assert dictPlan == {"bReady": True, "listSteps": []}


def testAnUnlinkedHomebrewBuildxGetsTheLinkCommandNotAReinstall():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["brew", "colima", "docker"],
        bHomebrewBuildxPresent=True, bBuildxInstalledUnlinked=True))
    assert dictStep["sStepName"] == "link-buildx"
    assert dictStep["sKind"] == S_KIND_NEEDS_YOU
    assert any("/opt/homebrew/opt/docker-buildx/bin/docker-buildx" in s
               for s in dictStep["listCommandsToShow"])


def testAMacThatIsReadyHasNothingLeft():
    dictPlan = fdictPlanRuntimeSetup(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        bDaemonAnswers=True, sRuntime="colima", listColimaMachines=[{
            "sProfile": "default", "sStatus": "Running", "iCpus": 9,
            "iMemoryBytes": 8 * I_GIGABYTE, "sArchitecture": "aarch64"}]))
    assert dictPlan == {"bReady": True, "listSteps": []}


def testAMachineThatShouldAnswerButDoesNotIsDiagnosedNotLeftEmpty():
    dictStep = fdictFirstStep(fdictMacFacts(
        listCommands=["brew", "colima", "docker"], bBuildxWorks=True,
        listColimaMachines=[{
            "sProfile": "default", "sStatus": "Running", "iCpus": 9,
            "iMemoryBytes": 8 * I_GIGABYTE, "sArchitecture": "aarch64"}]))
    assert dictStep["sStepName"] == "diagnose"
    assert "docker context use colima" in dictStep["sExplanation"]


def testADockerHostThatDoesNotAnswerIsNamedRatherThanReplaced():
    dictStep = fdictFirstStep(fdictMacFacts(
        sDockerHost="tcp://remote-builder:2376"))
    assert dictStep["sStepName"] == "docker-host-variable"
    assert "tcp://remote-builder:2376" in dictStep["sExplanation"]


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sDistribution, sRepository, bOfficial", [
    ("ubuntu", "ubuntu", True), ("debian", "debian", True),
    ("mint", "ubuntu", False),
])
def testAnAptDistributionInstallsFromDockersOwnRepository(
        sDistribution, sRepository, bOfficial):
    dictStep = fdictFirstStep(fdictLinuxFacts(sDistribution))
    assert dictStep["sKind"] == S_KIND_ADMINISTRATOR
    sScript = fsFirstScript(dictStep)
    assert f"https://download.docker.com/linux/{sRepository}/gpg" in sScript
    assert "docker-buildx-plugin" in sScript
    assert "systemctl enable --now docker" in sScript
    assert ("does not officially support" in dictStep["sExplanation"]) is (
        not bOfficial)


def testUbuntusSuiteFallsBackToTheVersionCodename():
    sUbuntu = fsFirstScript(fdictFirstStep(fdictLinuxFacts("ubuntu")))
    sDebian = fsFirstScript(fdictFirstStep(fdictLinuxFacts("debian")))
    assert "Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}" in sUbuntu
    assert "Suites: $VERSION_CODENAME" in sDebian


@pytest.mark.parametrize("sDistribution, sRepository, sRepoCommand", [
    ("fedora", "fedora", "addrepo --from-repofile"),
    ("rhel", "rhel", "--add-repo"),
    ("rocky", "centos", "--add-repo"),
])
def testAnRpmDistributionChecksDockersKeyBeforeImportingIt(
        sDistribution, sRepository, sRepoCommand):
    sScript = fsFirstScript(fdictFirstStep(fdictLinuxFacts(
        sDistribution, listCommands=["dnf", "sudo", "systemctl", "pkexec"])))
    assert f"linux/{sRepository}/docker-ce.repo" in sScript
    assert sRepoCommand in sScript
    assert sScript.index("060A61C51B558A7F742B77AAC52FEB6B621E9F35") < (
        sScript.index("rpm --import"))
    assert sScript.index("rpm --import") < sScript.index("dnf -y install docker-ce")


@pytest.mark.falsification
def testADistributionDockerPublishesNothingForRunsNothing():
    """Kills: guessing at packages for a distribution with no repository."""
    dictStep = fdictFirstStep(fdictLinuxFacts("arch"))
    assert dictStep["sKind"] == S_KIND_NEEDS_YOU
    assert dictStep["listActions"] == []
    assert "Arch Linux" in dictStep["sExplanation"]


def testWithNoPromptAnAdministratorStepBecomesCommandsToPaste():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "ubuntu", sPrivilegeChannel="none"))
    assert dictStep["sKind"] == S_KIND_NEEDS_YOU
    assert dictStep["listActions"] == []
    assert dictStep["listCommandsToShow"][0].startswith("sudo sh -c ")
    assert "download.docker.com/linux/ubuntu" in dictStep[
        "listCommandsToShow"][0]


def testAPermissionDeniedDaemonOffersTheGroupOrRootless():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "ubuntu", bDaemonPermissionDenied=True,
        bEngineServiceInstalled=True, bEngineServiceActive=True))
    assert dictStep["sStepName"] == "choose-docker-access"
    listNames = [d["sActionName"] for d in dictStep["listActions"]]
    assert listNames == ["join-docker-group", "set-up-rootless"]
    dictJoin = dictStep["listActions"][0]["listInvocations"][0]
    assert 'usermod -aG docker "$1"' in dictJoin["sScript"]
    assert dictJoin["listScriptArguments"] == ["researcherAlpha"]
    assert "equivalent to root" in dictStep["sExplanation"]


def testAChoiceWithNoPromptShowsEveryOption():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "ubuntu", bDaemonPermissionDenied=True, sPrivilegeChannel="none",
        bEngineServiceInstalled=True))
    listShown = dictStep["listCommandsToShow"]
    assert "# Join the docker group:" in listShown
    assert "# Set up rootless Docker:" in listShown


def testAUserWhoJoinedTheGroupIsToldToLogOutAndBackIn():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "ubuntu", bDaemonPermissionDenied=True,
        bUserListedInDockerGroup=True, bProcessHasDockerGroup=False))
    assert dictStep["sStepName"] == "log-out-and-back-in"
    assert dictStep["sKind"] == S_KIND_NEEDS_YOU


def testAStoppedEngineIsStartedAsAdministrator():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "fedora", bEngineServiceInstalled=True))
    assert dictStep["sStepName"] == "start-engine"
    assert fsFirstScript(dictStep) == "systemctl enable --now docker\n"


def testAStoppedRootlessEngineStartsAsTheUser():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "debian", bRootlessServiceInstalled=True))
    assert dictStep["sKind"] == S_KIND_RUN
    assert flistFirstArgv(dictStep) == [
        "systemctl", "--user", "start", "docker.service"]


def testAWorkingDaemonWithoutBuildxInstallsOnlyBuildx():
    dictStep = fdictFirstStep(fdictLinuxFacts(
        "ubuntu", bDaemonAnswers=True, bBuildxWorks=False))
    assert dictStep["sStepName"] == "install-buildx"
    assert "docker-buildx-plugin" in fsFirstScript(dictStep)
    assert "docker-ce " not in fsFirstScript(dictStep)


def testAnUnsupportedPlatformSaysSo():
    dictStep = fdictFirstStep(fdictMacFacts(sPlatform="freebsd14"))
    assert dictStep["sStepName"] == "unsupported-platform"
    assert "freebsd14" in dictStep["sExplanation"]
