"""Read the evidence the Docker setup wizard plans from.

The wizard never guesses what a machine has. Every fact here is read
from the machine itself -- the platform, the package managers and
runtimes on PATH, what the daemon answers, which Colima machines exist,
and whether an administrator step can be authorized by a prompt the
operating system owns -- and the planner (``runtimeSetupPlan``) turns
the facts into steps without running anything. Keeping the two apart
is what lets every row of the host matrix be tested from fixture
evidence: the planner is a pure function of this dictionary.

Each probe answers "absent" when its command is missing, times out or
fails, because a probe that raised would make the wizard fail exactly
on the machines it exists to help.
"""

import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys


__all__ = [
    "T_PROBED_COMMANDS",
    "S_PRIVILEGE_MACOS_DIALOG",
    "S_PRIVILEGE_PKEXEC",
    "S_PRIVILEGE_PASSWORDLESS_SUDO",
    "S_PRIVILEGE_NONE",
    "T_MACOS_DESKTOP_APPLICATIONS",
    "T_MACOS_PACKAGE_MANAGER_DIRECTORIES",
    "fnAddPackageManagerDirectoriesToPath",
    "fsDesktopApplicationForContext",
    "fdictParseOsRelease",
    "flistParseColimaMachines",
    "fdictReadHostFacts",
]


T_PROBED_COMMANDS = (
    "brew", "port", "colima", "docker", "apt-get", "dnf", "pkexec",
    "sudo", "systemctl", "osascript", "dockerd-rootless-setuptool.sh",
)

S_PRIVILEGE_MACOS_DIALOG = "macos-dialog"
S_PRIVILEGE_PKEXEC = "pkexec"
S_PRIVILEGE_PASSWORDLESS_SUDO = "passwordless-sudo"
S_PRIVILEGE_NONE = "none"

T_MACOS_DESKTOP_APPLICATIONS = ("Docker", "OrbStack", "Rancher Desktop")

# Where Homebrew (Apple Silicon, then Intel) and MacPorts put their
# commands. A package manager installed while vaibify runs is on the
# PATH of a NEW terminal only; the hub's own PATH was fixed when it
# started, so Continue would never find what the researcher just
# installed without looking here too.
T_MACOS_PACKAGE_MANAGER_DIRECTORIES = (
    "/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/opt/local/sbin",
)

_DICT_DESKTOP_APPLICATION_BY_CONTEXT = {
    "desktop-linux": "Docker",
    "orbstack": "OrbStack",
    "rancher-desktop": "Rancher Desktop",
}

_F_PROBE_TIMEOUT_SECONDS = 10.0
# MacPorts' words when it refuses to start after a major macOS upgrade.
_S_MACPORTS_PLATFORM_MISMATCH = "OS platform mismatch"
_I_PROBLEM_MAXIMUM_CHARACTERS = 300
_S_OS_RELEASE_PATH = "/etc/os-release"
_RE_OS_RELEASE_LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def _ftRunProbe(listCommand, dictEnvironment=None):
    """Return ``(iExitCode, sStdout, sStderr)``; exit -1 if it could not run."""
    try:
        processResult = subprocess.run(
            listCommand, capture_output=True, text=True,
            timeout=_F_PROBE_TIMEOUT_SECONDS, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, env=dictEnvironment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (-1, "", "")
    return (processResult.returncode, processResult.stdout or "",
            processResult.stderr or "")


def fnAddPackageManagerDirectoriesToPath():
    """Append the macOS package managers' directories to this process's PATH.

    Appended, never prepended, and only directories that exist and are
    not already there: nothing the researcher's PATH already chose is
    shadowed. It changes this process, deliberately -- after a
    Homebrew install in another terminal, the hub's own later docker
    and colima calls must find the same commands the wizard just did,
    without the researcher restarting vaibify.
    """
    if sys.platform != "darwin":
        return
    listPath = os.environ.get("PATH", "").split(os.pathsep)
    listAdded = [
        sDirectory for sDirectory in T_MACOS_PACKAGE_MANAGER_DIRECTORIES
        if sDirectory not in listPath and os.path.isdir(sDirectory)
    ]
    if listAdded:
        os.environ["PATH"] = os.pathsep.join(
            [s for s in listPath if s] + listAdded)


def fsDesktopApplicationForContext(sContext):
    """Name the desktop application a Docker context belongs to, or ''."""
    return _DICT_DESKTOP_APPLICATION_BY_CONTEXT.get((sContext or "").lower(), "")


def fdictParseOsRelease(sText):
    """Return the ``KEY: value`` pairs of an os-release file, quotes removed."""
    dictRelease = {}
    for sLine in (sText or "").splitlines():
        matchLine = _RE_OS_RELEASE_LINE.match(sLine.strip())
        if not matchLine:
            continue
        sValue = matchLine.group(2).strip()
        if len(sValue) >= 2 and sValue[0] == sValue[-1] and sValue[0] in "\"'":
            sValue = sValue[1:-1]
        dictRelease[matchLine.group(1)] = sValue
    return dictRelease


def _fdictReadOsRelease():
    try:
        with open(_S_OS_RELEASE_PATH, encoding="utf-8") as fileRelease:
            return fdictParseOsRelease(fileRelease.read())
    except OSError:
        return {}


def flistParseColimaMachines(sOutput):
    """Return one dict per machine in ``colima list --json`` output.

    Colima prints one JSON object per line (one per profile). A line
    that does not parse is skipped rather than guessed at.
    """
    listMachines = []
    for sLine in (sOutput or "").splitlines():
        try:
            jsonMachine = json.loads(sLine)
        except ValueError:
            continue
        if not isinstance(jsonMachine, dict) or not jsonMachine.get("name"):
            continue
        listMachines.append({
            "sProfile": str(jsonMachine.get("name")),
            "sStatus": str(jsonMachine.get("status") or ""),
            "iCpus": int(jsonMachine.get("cpus") or 0),
            "iMemoryBytes": int(jsonMachine.get("memory") or 0),
            "sArchitecture": str(jsonMachine.get("arch") or ""),
        })
    return listMachines


def _flistReadColimaMachines(setCommands):
    if "colima" not in setCommands:
        return []
    iExitCode, sStdout, _ = _ftRunProbe(["colima", "list", "--json"])
    if iExitCode != 0:
        return []
    return flistParseColimaMachines(sStdout)


def _fsResolveProbedEndpoint(setCommands):
    """Return the ONE daemon endpoint every Docker probe here asks.

    The researcher's own ``DOCKER_HOST`` if they set one, else the
    active context's endpoint, read now. A ``DOCKER_HOST`` the hub
    exported at startup is skipped: it names the context as it was
    then, and a probe that inherited it would ask a different daemon
    than the one the profile was read from.
    """
    from vaibify.docker.dockerContext import (
        fbDockerHostIsExportedByVaibify, fsReadActiveContextEndpoint,
    )
    sDockerHost = os.environ.get("DOCKER_HOST", "")
    if sDockerHost and not fbDockerHostIsExportedByVaibify():
        return sDockerHost
    if "docker" not in setCommands:
        return ""
    return fsReadActiveContextEndpoint()


def _fdictProbeEnvironment(sEndpoint):
    """Return the environment that points a docker probe at ``sEndpoint``."""
    from vaibify.docker.dockerContext import fbDockerHostIsExportedByVaibify
    dictEnvironment = dict(os.environ)
    if sEndpoint:
        dictEnvironment["DOCKER_HOST"] = sEndpoint
    elif fbDockerHostIsExportedByVaibify():
        dictEnvironment.pop("DOCKER_HOST", None)
    return dictEnvironment


def _ftReadDaemonAnswer(setCommands, dictEnvironment):
    """Return ``(bAnswers, bPermissionDenied, bBuildxWorks)``."""
    if "docker" not in setCommands:
        return (False, False, False)
    iExitCode, _, sStderr = _ftRunProbe(
        ["docker", "info", "--format", "{{.ServerVersion}}"], dictEnvironment)
    bAnswers = iExitCode == 0
    bPermissionDenied = "permission denied" in sStderr.lower()
    iBuildxCode, _, _ = _ftRunProbe(
        ["docker", "buildx", "version"], dictEnvironment)
    return (bAnswers, bPermissionDenied, iBuildxCode == 0)


def _fbHasGraphicalSession():
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _fsDetectPrivilegeChannel(setCommands):
    """Name the OS-owned way an administrator step can be authorized.

    A graphical prompt is chosen only when someone is at this machine's
    screen to answer it: over SSH, or for a hub reached remotely, the
    dialog would open where nobody is looking.
    """
    bGraphical = _fbHasGraphicalSession()
    if sys.platform == "darwin" and bGraphical and "osascript" in setCommands:
        return S_PRIVILEGE_MACOS_DIALOG
    if sys.platform.startswith("linux") and bGraphical and (
            "pkexec" in setCommands):
        return S_PRIVILEGE_PKEXEC
    if "sudo" in setCommands and _ftRunProbe(["sudo", "-n", "true"])[0] == 0:
        return S_PRIVILEGE_PASSWORDLESS_SUDO
    return S_PRIVILEGE_NONE


def _flistInstalledDesktopApplications():
    if sys.platform != "darwin":
        return []
    return [
        sApplication for sApplication in T_MACOS_DESKTOP_APPLICATIONS
        if os.path.isdir(f"/Applications/{sApplication}.app")
    ]


def _fdictReadLinuxServices(setCommands):
    """Return what systemd knows about the Docker services, or nothing."""
    dictServices = {
        "bEngineServiceInstalled": False, "bEngineServiceActive": False,
        "bRootlessServiceInstalled": False, "bDesktopServiceInstalled": False,
    }
    if "systemctl" not in setCommands:
        return dictServices
    iCode, sOut, _ = _ftRunProbe(
        ["systemctl", "list-unit-files", "docker.service", "--no-legend"])
    dictServices["bEngineServiceInstalled"] = iCode == 0 and bool(sOut.strip())
    iCode, sOut, _ = _ftRunProbe(["systemctl", "is-active", "docker"])
    dictServices["bEngineServiceActive"] = sOut.strip() == "active"
    sUserUnits = os.path.expanduser("~/.config/systemd/user")
    dictServices["bRootlessServiceInstalled"] = os.path.isfile(
        os.path.join(sUserUnits, "docker.service"))
    iCode, sOut, _ = _ftRunProbe(
        ["systemctl", "--user", "list-unit-files", "docker-desktop.service",
         "--no-legend"])
    dictServices["bDesktopServiceInstalled"] = iCode == 0 and bool(sOut.strip())
    return dictServices


def _flistReadRunningVaibifyContainers(bDaemonAnswers, dictEnvironment=None):
    """Return the registered vaibify containers the daemon reports running.

    Restarting a Colima machine stops every container in it, so the
    wizard offers that only when this list is empty. An unreadable
    registry counts every running container, never none: the question
    is whether a restart would stop the researcher's work.
    """
    if not bDaemonAnswers:
        return []
    from vaibify.config.registryManager import fdictLoadRegistryOrRaise
    iCode, sOut, _ = _ftRunProbe(
        ["docker", "ps", "--format", "{{.Names}}"], dictEnvironment)
    listRunning = [sName for sName in sOut.split() if sName]
    if iCode != 0:
        return ["(the daemon did not list its containers)"]
    try:
        setRegistered = {
            dictProject.get("sContainerName")
            for dictProject in fdictLoadRegistryOrRaise().get(
                "listProjects", [])
        }
    except (OSError, ValueError):
        return sorted(listRunning)
    return sorted(sName for sName in listRunning if sName in setRegistered)


def _fdictReadHomebrewBuildx(setCommands):
    """Return Homebrew's prefix and whether its buildx is installed."""
    if "brew" not in setCommands:
        return {"sHomebrewPrefix": "", "bHomebrewBuildxPresent": False}
    iCode, sOut, _ = _ftRunProbe(["brew", "--prefix"])
    sPrefix = sOut.strip() if iCode == 0 else ""
    return {
        "sHomebrewPrefix": sPrefix,
        "bHomebrewBuildxPresent": bool(sPrefix) and os.path.isfile(
            os.path.join(sPrefix, "opt", "docker-buildx", "bin",
                         "docker-buildx")),
    }


def _fsFirstErrorLine(sOutput):
    """Return a tool's first error line in its own words, bounded."""
    listLines = [
        sLine.strip() for sLine in sOutput.splitlines() if sLine.strip()]
    for sLine in listLines:
        if sLine.startswith("Error:"):
            return sLine[len("Error:"):].strip()[:_I_PROBLEM_MAXIMUM_CHARACTERS]
    return (listLines[0] if listLines else "the command did not run")[
        :_I_PROBLEM_MAXIMUM_CHARACTERS]


def _fdictReadMacPortsHealth(setCommands):
    """Return whether MacPorts runs, with its own words when it does not.

    After a major macOS upgrade MacPorts refuses every command until it
    is migrated ("OS platform mismatch"), while the programs it already
    installed keep running, so a ``port`` on PATH is not a ``port`` that
    can install anything (measured on macOS 26, 2026-10-26).
    """
    if "port" not in setCommands:
        return {"bMacPortsWorks": False, "bMacPortsNeedsMigration": False,
                "sMacPortsProblem": ""}
    sPort = shutil.which("port") or "port"
    iCode, sOut, sErr = _ftRunProbe([sPort, "version"])
    if iCode == 0:
        return {"bMacPortsWorks": True, "bMacPortsNeedsMigration": False,
                "sMacPortsProblem": ""}
    return {
        "bMacPortsWorks": False,
        "bMacPortsNeedsMigration": (
            _S_MACPORTS_PLATFORM_MISMATCH in sErr + sOut),
        "sMacPortsProblem": _fsFirstErrorLine(sErr + "\n" + sOut),
    }


def _fdictReadDockerGroupMembership(sUserName):
    """Return whether the user is in the docker group, and whether this process is.

    They differ for the rest of a login session after ``usermod``: the
    group file names the user at once, but a process gains the group
    only from a new login.
    """
    try:
        tGroupDocker = grp.getgrnam("docker")
    except KeyError:
        return {"bUserListedInDockerGroup": False,
                "bProcessHasDockerGroup": False}
    return {
        "bUserListedInDockerGroup": sUserName in tGroupDocker.gr_mem,
        "bProcessHasDockerGroup": tGroupDocker.gr_gid in os.getgroups(),
    }


def _fiReadHostMemoryBytes():
    """Return the machine's RAM in bytes, or 0 when it cannot be read.

    macOS does not report ``SC_PHYS_PAGES`` (it answers 0 bytes), so
    there the kernel's own ``hw.memsize`` is asked.
    """
    if sys.platform == "darwin":
        iCode, sOut, _ = _ftRunProbe(["sysctl", "-n", "hw.memsize"])
        return int(sOut.strip()) if iCode == 0 and sOut.strip().isdigit() else 0
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _fsReadMacVersion():
    if sys.platform != "darwin":
        return ""
    iCode, sOut, _ = _ftRunProbe(["sw_vers", "-productVersion"])
    return sOut.strip() if iCode == 0 else ""


def _fsReadActiveDockerContext(setCommands):
    """Return the active Docker context's name, which names a desktop app."""
    if "docker" not in setCommands:
        return ""
    from vaibify.docker.dockerContext import fsActiveDockerContext
    return fsActiveDockerContext()


def fdictReadHostFacts(bProbePrivilege=True):
    """Return every fact the planner reads, gathered from this machine.

    ``bProbePrivilege`` False skips asking ``sudo -n`` and answers no
    privilege channel: the dashboard's advisory check on each hub load
    needs only step titles, and a failed ``sudo -n`` is written to the
    system's auth log, which should not fill with one per page load.
    """
    from vaibify.cli.commandBuild import fsHostArch
    fnAddPackageManagerDirectoriesToPath()
    from vaibify.docker.dockerContext import fsColimaProfileOfEndpoint
    setCommands = {s for s in T_PROBED_COMMANDS if shutil.which(s)}
    sEndpoint = _fsResolveProbedEndpoint(setCommands)
    dictEnvironment = _fdictProbeEnvironment(sEndpoint)
    bAnswers, bPermissionDenied, bBuildxWorks = _ftReadDaemonAnswer(
        setCommands, dictEnvironment)
    dictFacts = {
        "sPlatform": sys.platform,
        "sArchitecture": fsHostArch(),
        "sMacVersion": _fsReadMacVersion(),
        "dictOsRelease": (
            _fdictReadOsRelease() if sys.platform.startswith("linux") else {}),
        "listCommands": sorted(setCommands),
        "sMacPortsPath": shutil.which("port") or "",
        "bDaemonAnswers": bAnswers,
        "bDaemonPermissionDenied": bPermissionDenied,
        "bBuildxWorks": bBuildxWorks,
        "sDockerEndpoint": sEndpoint,
        "sActiveDockerContext": _fsReadActiveDockerContext(setCommands),
        "sActiveColimaProfile": fsColimaProfileOfEndpoint(sEndpoint),
        "listColimaMachines": _flistReadColimaMachines(setCommands),
        "listRunningVaibifyContainers": (
            _flistReadRunningVaibifyContainers(bAnswers, dictEnvironment)),
        "listDesktopApplications": _flistInstalledDesktopApplications(),
        "sPrivilegeChannel": (
            _fsDetectPrivilegeChannel(setCommands) if bProbePrivilege
            else S_PRIVILEGE_NONE),
        "sUserName": pwd.getpwuid(os.getuid()).pw_name,
        "iHostCpuCount": os.cpu_count() or 1,
        "iHostMemoryBytes": _fiReadHostMemoryBytes(),
    }
    dictFacts.update(_fdictReadLinuxServices(setCommands))
    dictFacts.update(_fdictReadHomebrewBuildx(setCommands))
    dictFacts.update(_fdictReadMacPortsHealth(setCommands))
    dictFacts["bBuildxInstalledUnlinked"] = bool(
        dictFacts["bHomebrewBuildxPresent"] and not bBuildxWorks)
    dictFacts.update(_fdictReadDockerGroupMembership(dictFacts["sUserName"]))
    from vaibify.docker.dockerContext import fbDockerHostIsExportedByVaibify
    sDockerHost = os.environ.get("DOCKER_HOST", "")
    dictFacts["sDockerHost"] = (
        "" if fbDockerHostIsExportedByVaibify() else sDockerHost)
    return dictFacts
