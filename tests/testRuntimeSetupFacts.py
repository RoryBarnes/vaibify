"""The evidence the Docker setup wizard plans from, read from a machine.

The parsers are driven with the real formats (an os-release file,
``colima list --json`` lines), and the privilege channel with the
environment that decides it: a graphical session gets the operating
system's own prompt, an SSH session never does, because the dialog
would open on a screen nobody is looking at.
"""

import os
import sys

import pytest

from vaibify.install import runtimeSetupFacts as factsModule
from vaibify.install.runtimeSetupFacts import (
    S_PRIVILEGE_MACOS_DIALOG,
    S_PRIVILEGE_NONE,
    S_PRIVILEGE_PASSWORDLESS_SUDO,
    S_PRIVILEGE_PKEXEC,
    fdictParseOsRelease,
    flistParseColimaMachines,
)


S_ROCKY_OS_RELEASE = '''NAME="Rocky Linux"
VERSION="9.4 (Blue Onyx)"
ID="rocky"
ID_LIKE="rhel centos fedora"
VERSION_ID="9.4"
PRETTY_NAME="Rocky Linux 9.4 (Blue Onyx)"
# a comment line
'''

S_COLIMA_LIST = (
    '{"name":"default","status":"Running","arch":"aarch64","cpus":9,'
    '"memory":8589934592,"disk":107374182400,"runtime":"docker"}\n'
    'time="2026-10-24" level=warning msg="not json"\n'
    '{"name":"work","status":"Stopped","arch":"x86_64","cpus":2,'
    '"memory":2147483648}\n'
)


def testAnOsReleaseFileIsReadWithItsQuotesRemoved():
    dictRelease = fdictParseOsRelease(S_ROCKY_OS_RELEASE)
    assert dictRelease["ID"] == "rocky"
    assert dictRelease["ID_LIKE"] == "rhel centos fedora"
    assert dictRelease["PRETTY_NAME"] == "Rocky Linux 9.4 (Blue Onyx)"
    assert "# a comment line" not in dictRelease.values()


def testEveryColimaMachineIsReadAndANoiseLineIsSkipped():
    listMachines = flistParseColimaMachines(S_COLIMA_LIST)
    assert [d["sProfile"] for d in listMachines] == ["default", "work"]
    assert listMachines[0]["iMemoryBytes"] == 8 * 2 ** 30
    assert listMachines[1] == {
        "sProfile": "work", "sStatus": "Stopped", "iCpus": 2,
        "iMemoryBytes": 2 * 2 ** 30, "sArchitecture": "x86_64"}


@pytest.fixture
def fnSetSession(monkeypatch):
    """Set the platform and session variables the channel is chosen from."""
    def fnSet(sPlatform, dictEnvironment, bSudoWorks=False):
        monkeypatch.setattr(factsModule.sys, "platform", sPlatform)
        for sName in ("SSH_CONNECTION", "SSH_TTY", "DISPLAY",
                      "WAYLAND_DISPLAY"):
            monkeypatch.delenv(sName, raising=False)
        for sName, sValue in dictEnvironment.items():
            monkeypatch.setenv(sName, sValue)
        monkeypatch.setattr(
            factsModule, "_ftRunProbe",
            lambda listCommand: (0 if bSudoWorks else 1, "", ""))
    return fnSet


@pytest.mark.parametrize("sPlatform, dictEnvironment, setCommands, sChannel", [
    ("darwin", {}, {"osascript", "sudo"}, S_PRIVILEGE_MACOS_DIALOG),
    ("linux", {"DISPLAY": ":0"}, {"pkexec", "sudo"}, S_PRIVILEGE_PKEXEC),
    ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, {"pkexec"}, S_PRIVILEGE_PKEXEC),
    ("linux", {}, {"pkexec", "sudo"}, S_PRIVILEGE_NONE),
])
def testAPromptIsChosenOnlyWhereSomeoneCanSeeIt(
        fnSetSession, sPlatform, dictEnvironment, setCommands, sChannel):
    fnSetSession(sPlatform, dictEnvironment)
    assert factsModule._fsDetectPrivilegeChannel(setCommands) == sChannel


@pytest.mark.falsification
@pytest.mark.parametrize("sPlatform", ["darwin", "linux"])
def testAnSshSessionNeverGetsAGraphicalPrompt(fnSetSession, sPlatform):
    """Kills: a dialog opened on a remote machine's unwatched screen."""
    fnSetSession(sPlatform, {"SSH_CONNECTION": "10.0.0.2 51000 10.0.0.9 22",
                             "DISPLAY": ":0"})
    assert factsModule._fsDetectPrivilegeChannel(
        {"osascript", "pkexec", "sudo"}) == S_PRIVILEGE_NONE


def testPasswordlessSudoIsUsedWhenNoPromptCanBeShown(fnSetSession):
    fnSetSession("linux", {"SSH_TTY": "/dev/pts/3"}, bSudoWorks=True)
    assert factsModule._fsDetectPrivilegeChannel(
        {"pkexec", "sudo"}) == S_PRIVILEGE_PASSWORDLESS_SUDO


def testAMacsMemoryIsReadFromTheKernelNotSysconf(monkeypatch):
    monkeypatch.setattr(factsModule.sys, "platform", "darwin")
    monkeypatch.setattr(
        factsModule, "_ftRunProbe",
        lambda listCommand: (0, "17179869184\n", "")
        if listCommand == ["sysctl", "-n", "hw.memsize"] else (1, "", ""))
    assert factsModule._fiReadHostMemoryBytes() == 16 * 2 ** 30


def testAProbeWhoseCommandIsMissingAnswersAbsent():
    assert factsModule._ftRunProbe(
        ["vaibify-no-such-command-anywhere"]) == (-1, "", "")


@pytest.mark.skipif(sys.platform != "darwin" and not sys.platform.startswith(
    "linux"), reason="the facts are read only on macOS and Linux")
def testTheFactsOfThisMachineCarryEveryKeyThePlannerReads(monkeypatch):
    """Read the real machine running the suite: no key may be missing."""
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    dictFacts = factsModule.fdictReadHostFacts()
    for sKey in ("sPlatform", "sArchitecture", "listCommands",
                 "bDaemonAnswers", "bBuildxWorks", "listColimaMachines",
                 "sPrivilegeChannel", "sUserName", "iHostCpuCount",
                 "iHostMemoryBytes", "bUserListedInDockerGroup",
                 "bProcessHasDockerGroup", "sDockerHost",
                 "bBuildxInstalledUnlinked", "listRunningVaibifyContainers",
                 "sActiveDockerContext", "sActiveColimaProfile",
                 "bMacPortsWorks", "bMacPortsNeedsMigration",
                 "sMacPortsProblem"):
        assert sKey in dictFacts, sKey
    assert dictFacts["iHostMemoryBytes"] > 0
    assert dictFacts["iHostCpuCount"] >= 1


@pytest.mark.falsification
def testAnUnreadableRegistryCountsEveryRunningContainer(
        tmp_path, monkeypatch):
    """A registry nobody can read must never read as "nothing registered".

    Kills: a restart guard that sees no protected containers because
    the registry loader answered an empty registry for a broken file.
    """
    from vaibify.config import registryManager
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text("{ this is not json")
    monkeypatch.setattr(registryManager, "_S_REGISTRY_PATH", str(pathRegistry))
    monkeypatch.setattr(
        factsModule, "_ftRunProbe",
        lambda listCommand, dictEnvironment=None: (
            0, "projectAlphaBox\nunrelatedService\n", ""))
    assert factsModule._flistReadRunningVaibifyContainers(True) == [
        "projectAlphaBox", "unrelatedService"]


def testAReadableRegistryProtectsOnlyItsOwnContainers(tmp_path, monkeypatch):
    import json
    from vaibify.config import registryManager
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({"listProjects": [
        {"sName": "alpha", "sContainerName": "projectAlphaBox"}]}))
    monkeypatch.setattr(registryManager, "_S_REGISTRY_PATH", str(pathRegistry))
    monkeypatch.setattr(
        factsModule, "_ftRunProbe",
        lambda listCommand, dictEnvironment=None: (
            0, "projectAlphaBox\nunrelatedService\n", ""))
    assert factsModule._flistReadRunningVaibifyContainers(True) == [
        "projectAlphaBox"]


@pytest.mark.falsification
def testAPackageManagerInstalledOutsideTheHubsPathIsFound(
        tmp_path, monkeypatch):
    """Continue after installing MacPorts in another terminal finds it.

    Kills: looking only at the PATH the hub started with.
    """
    import shutil
    pathPorts = tmp_path / "opt-local-bin"
    pathPorts.mkdir()
    pathPort = pathPorts / "port"
    pathPort.write_text("#!/bin/sh\n")
    pathPort.chmod(0o755)
    monkeypatch.setattr(factsModule.sys, "platform", "darwin")
    monkeypatch.setattr(factsModule, "T_MACOS_PACKAGE_MANAGER_DIRECTORIES",
                        ("/nonexistent/homebrew/bin", str(pathPorts)))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert shutil.which("port") is None or shutil.which("port") != str(pathPort)
    factsModule.fnAddPackageManagerDirectoriesToPath()
    assert shutil.which("port") == str(pathPort)
    assert os.environ["PATH"].split(os.pathsep)[:2] == ["/usr/bin", "/bin"]
    factsModule.fnAddPackageManagerDirectoriesToPath()
    assert os.environ["PATH"].count(str(pathPorts)) == 1


def testADesktopContextNamesItsApplication():
    assert factsModule.fsDesktopApplicationForContext("orbstack") == "OrbStack"
    assert factsModule.fsDesktopApplicationForContext("desktop-linux") == (
        "Docker")
    assert factsModule.fsDesktopApplicationForContext("colima") == ""


@pytest.mark.falsification
def testEveryDockerProbeAsksTheDaemonTheProfileWasReadFrom(monkeypatch):
    """The context moved after the hub exported DOCKER_HOST at startup.

    The profile comes from the context's endpoint NOW; a docker probe
    that inherited the stale export would ask another machine's daemon,
    and the restart guard would check the wrong containers.

    Kills: a docker probe that inherits the hub's exported DOCKER_HOST.
    """
    from vaibify.docker import dockerContext
    sStale = "unix:///Users/researcher/.colima/alpha/docker.sock"
    sNow = "unix:///Users/researcher/.colima/beta/docker.sock"
    monkeypatch.setenv("DOCKER_HOST", sStale)
    monkeypatch.setattr(dockerContext, "fbDockerHostIsExportedByVaibify",
                        lambda: True)
    monkeypatch.setattr(dockerContext, "fsReadActiveContextEndpoint",
                        lambda: sNow)
    monkeypatch.setattr(dockerContext, "fsActiveDockerContext",
                        lambda: "colima-beta")
    listDockerHosts = []

    def ftRecordProbe(listCommand, dictEnvironment=None):
        if listCommand[0] == "docker":
            listDockerHosts.append((dictEnvironment or {}).get("DOCKER_HOST"))
        return (0, "", "")

    monkeypatch.setattr(factsModule, "_ftRunProbe", ftRecordProbe)
    monkeypatch.setattr(factsModule.shutil, "which",
                        lambda sName, path=None: "/usr/bin/" + sName)
    dictFacts = factsModule.fdictReadHostFacts(bProbePrivilege=False)
    assert dictFacts["sActiveColimaProfile"] == "beta"
    assert dictFacts["sDockerEndpoint"] == sNow
    assert listDockerHosts and set(listDockerHosts) == {sNow}


def testTheResearchersOwnDockerHostIsTheEndpoint(monkeypatch):
    from vaibify.docker import dockerContext
    sTheirs = "unix:///Users/researcher/.colima/work/docker.sock"
    monkeypatch.setenv("DOCKER_HOST", sTheirs)
    monkeypatch.setattr(dockerContext, "fbDockerHostIsExportedByVaibify",
                        lambda: False)
    assert factsModule._fsResolveProbedEndpoint({"docker"}) == sTheirs
    assert dockerContext.fsColimaProfileOfEndpoint(sTheirs) == "work"
    assert dockerContext.fsColimaProfileOfEndpoint(
        "unix:///var/run/docker.sock") == ""


# MacPorts' own output on a Mac upgraded to macOS 26 before migration,
# captured stream by stream from a real machine on 2026-10-26.
S_MACPORTS_MISMATCH_STDOUT = (
    'OS platform mismatch\n'
    '    while executing\n'
    '"mportinit ui_options global_options global_variations"\n'
)
S_MACPORTS_MISMATCH_STDERR = (
    'Error: Current platform "darwin 25" does not match expected '
    'platform "darwin 24"\n'
    "Error: Please run 'sudo port migrate' or follow the migration "
    "instructions: https://trac.macports.org/wiki/Migration\n"
    'Error: /opt/local/bin/port: Failed to initialize MacPorts, OS '
    'platform mismatch\n'
)


def _fnInstallFakePort(tmp_path, monkeypatch, sStdout, sStderr, iExit):
    """Put a real executable named port first on PATH; it replays output."""
    pathDirectory = tmp_path / "fakeMacPorts"
    pathDirectory.mkdir()
    (pathDirectory / "stdout.txt").write_text(sStdout)
    (pathDirectory / "stderr.txt").write_text(sStderr)
    pathPort = pathDirectory / "port"
    pathPort.write_text(
        "#!/bin/sh\n"
        f'cat "{pathDirectory}/stdout.txt"\n'
        f'cat "{pathDirectory}/stderr.txt" >&2\n'
        f"exit {iExit}\n")
    pathPort.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(pathDirectory) + os.pathsep + os.environ.get("PATH", ""))


@pytest.mark.falsification
def testAMacPortsThatNeedsMigratingIsReadAsNotRunning(tmp_path, monkeypatch):
    """Kills: reading ``port`` on PATH as a MacPorts that can install.

    The oracle is MacPorts' own output from an upgraded Mac, replayed by
    a real executable through the real probe.
    """
    _fnInstallFakePort(tmp_path, monkeypatch, S_MACPORTS_MISMATCH_STDOUT,
                       S_MACPORTS_MISMATCH_STDERR, 1)
    dictHealth = factsModule._fdictReadMacPortsHealth({"port"})
    assert dictHealth == {
        "bMacPortsWorks": False, "bMacPortsNeedsMigration": True,
        "sMacPortsProblem": (
            'Current platform "darwin 25" does not match expected '
            'platform "darwin 24"'),
    }


def testAMacPortsThatRunsIsReadAsWorking(tmp_path, monkeypatch):
    _fnInstallFakePort(tmp_path, monkeypatch, "Version: 2.11.5\n", "", 0)
    assert factsModule._fdictReadMacPortsHealth({"port"})[
        "bMacPortsWorks"] is True


def testAMacPortsBrokenAnotherWayKeepsItsOwnWords(tmp_path, monkeypatch):
    _fnInstallFakePort(tmp_path, monkeypatch, "",
                       "Error: Port registry is locked\n", 1)
    assert factsModule._fdictReadMacPortsHealth({"port"}) == {
        "bMacPortsWorks": False, "bMacPortsNeedsMigration": False,
        "sMacPortsProblem": "Port registry is locked"}


def testAMacWithoutMacPortsNeverRunsIt(monkeypatch):
    listProbes = []
    monkeypatch.setattr(factsModule, "_ftRunProbe", lambda listCommand: (
        listProbes.append(listCommand) or (0, "", "")))
    assert factsModule._fdictReadMacPortsHealth({"brew"})[
        "bMacPortsWorks"] is False
    assert listProbes == []
