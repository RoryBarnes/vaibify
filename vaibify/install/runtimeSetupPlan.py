"""Plan the steps that give this machine a Docker vaibify can use.

A pure function of the facts ``runtimeSetupFacts`` reads: no step is
run here. The plan lists the REMAINING steps in order; only the first
can be run, and it can be run only as one of the actions the plan names
for it -- the dashboard and the CLI choose an action by name, never a
command. Every command a step runs is built here from constants plus
the few values the machine itself supplied (its architecture, the
user's login name, a Colima profile it listed, two integers the
researcher may edit), so no text a researcher types reaches a shell.

Three kinds of step:

* ``run`` -- runs as the researcher (``brew install``, ``colima start``).
* ``administrator`` -- runs as root through a prompt the operating
  system owns (macOS's administrator dialog, polkit's ``pkexec``, or
  passwordless ``sudo``). vaibify never sees a password.
* ``needs-you`` -- something only the researcher can do (install a
  package manager, log out and back in, use a distribution Docker
  publishes no repository for, or an administrator step on a machine
  with no prompt to show). The step says what and gives the commands;
  Continue re-reads the machine and plans again from wherever it is.

The vendor commands follow Docker's own install pages (read
2026-10-24): docs.docker.com/engine/install/{ubuntu,debian,fedora,rhel,
centos}, linux-postinstall and security/rootless.
"""

import shlex

from vaibify.install.runtimeSetupFacts import (
    S_PRIVILEGE_NONE,
    T_MACOS_DESKTOP_APPLICATIONS,
    fsDesktopApplicationForContext,
)


__all__ = [
    "S_KIND_RUN",
    "S_KIND_ADMINISTRATOR",
    "S_KIND_NEEDS_YOU",
    "I_MINIMUM_DOCKER_MEMORY_GIGABYTES",
    "fdictDefaultColimaSettings",
    "fdictValidateColimaSettings",
    "fsDescribeAdministratorCommand",
    "fdictPlanRuntimeSetup",
]


S_KIND_RUN = "run"
S_KIND_ADMINISTRATOR = "administrator"
S_KIND_NEEDS_YOU = "needs-you"

I_MINIMUM_DOCKER_MEMORY_GIGABYTES = 4
_I_GIGABYTE = 2 ** 30

S_HOMEBREW_INSTALL_COMMAND = (
    '/bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
)
S_MACPORTS_INSTALL_PAGE = "https://www.macports.org/install.php"
S_DOCKER_INSTALL_PAGE = "https://docs.docker.com/engine/install/"

_DICT_HOMEBREW_FORMULA = {
    "colima": "colima", "docker": "docker", "buildx": "docker-buildx",
}
_DICT_MACPORTS_PORT = {
    "colima": "colima", "docker": "docker", "buildx": "docker-buildx-plugin",
}

# Docker publishes this fingerprint for the key that signs its rpm
# repositories (Fedora, RHEL and CentOS pages alike). Its apt pages
# publish none, so the apt scripts can only follow Docker's own steps.
_S_DOCKER_RPM_KEY_FINGERPRINT = "060A61C51B558A7F742B77AAC52FEB6B621E9F35"

_S_APT_ENGINE_SCRIPT = """set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/{sRepository}/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
cat > /etc/apt/sources.list.d/docker.sources <<VAIBIFY_EOF
Types: deb
URIs: https://download.docker.com/linux/{sRepository}
Suites: {sSuiteExpression}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
VAIBIFY_EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
"""

_DICT_APT_SUITE_EXPRESSION = {
    "ubuntu": "${UBUNTU_CODENAME:-$VERSION_CODENAME}",
    "debian": "$VERSION_CODENAME",
}

_S_RPM_KEY_SCRIPT = """sKeyFile=$(mktemp)
curl -fsSL https://download.docker.com/linux/{sRepository}/gpg -o "$sKeyFile"
sFingerprint=$(gpg --show-keys --with-colons "$sKeyFile" | awk -F: '/^fpr:/ {{print $10; exit}}')
if [ "$sFingerprint" != "{sFingerprint}" ]; then
    echo "Docker's signing key did not match its published fingerprint; nothing was installed." >&2
    rm -f "$sKeyFile"
    exit 1
fi
rpm --import "$sKeyFile"
rm -f "$sKeyFile"
"""

_DICT_DNF_REPOSITORY_SETUP = {
    "fedora": (
        "dnf config-manager addrepo --from-repofile "
        "https://download.docker.com/linux/fedora/docker-ce.repo\n"),
    "rhel": (
        "dnf -y install dnf-plugins-core\n"
        "dnf config-manager --add-repo "
        "https://download.docker.com/linux/rhel/docker-ce.repo\n"),
    "centos": (
        "dnf -y install dnf-plugins-core\n"
        "dnf config-manager --add-repo "
        "https://download.docker.com/linux/centos/docker-ce.repo\n"),
}

_S_DNF_ENGINE_INSTALL = (
    "dnf -y install docker-ce docker-ce-cli containerd.io "
    "docker-buildx-plugin docker-compose-plugin\n"
    "systemctl enable --now docker\n"
)

_DICT_BUILDX_SCRIPT = {
    "apt": (
        "set -e\nexport DEBIAN_FRONTEND=noninteractive\napt-get update\n"
        "apt-get install -y docker-buildx-plugin "
        "|| apt-get install -y docker-buildx\n"),
    "dnf": "set -e\ndnf -y install docker-buildx-plugin\n",
}

_DICT_ROOTLESS_PREREQUISITES_SCRIPT = {
    "apt": (
        "set -e\nexport DEBIAN_FRONTEND=noninteractive\napt-get update\n"
        "apt-get install -y uidmap docker-ce-rootless-extras\n"),
    "dnf": "set -e\ndnf -y install docker-ce-rootless-extras\n",
}

_S_JOIN_DOCKER_GROUP_SCRIPT = (
    "set -e\n"
    "getent group docker >/dev/null || groupadd docker\n"
    'usermod -aG docker "$1"\n'
)
_S_START_ENGINE_SCRIPT = "systemctl enable --now docker\n"
_S_USE_ROOTLESS_CONTEXT_SCRIPT = (
    "docker context inspect rootless >/dev/null 2>&1 "
    "&& docker context use rootless || true\n"
)


# ---------------------------------------------------------------------------
# Step and action records
# ---------------------------------------------------------------------------

def _fdictInvocation(listArgv=None, sScript="", listScriptArguments=None):
    """Return one command: argv run as the user, or a script run as root."""
    if sScript:
        return {"sRunAs": "administrator", "sScript": sScript,
                "listScriptArguments": list(listScriptArguments or [])}
    return {"sRunAs": "user", "listArgv": list(listArgv)}


def _fdictAction(sActionName, sLabel, listInvocations):
    return {"sActionName": sActionName, "sLabel": sLabel,
            "listInvocations": listInvocations}


def _fdictStep(sStepName, sTitle, sKind, sExplanation,
               listActions=(), listCommandsToShow=(), **dictExtra):
    dictStep = {
        "sStepName": sStepName, "sTitle": sTitle, "sKind": sKind,
        "sExplanation": sExplanation, "listActions": list(listActions),
        "listCommandsToShow": list(listCommandsToShow),
    }
    dictStep.update(dictExtra)
    return dictStep


def fsDescribeAdministratorCommand(sScript, listScriptArguments=()):
    """Return the one command a researcher can paste to run a root script."""
    listParts = ["sudo", "sh", "-c", shlex.quote(sScript)]
    if listScriptArguments:
        listParts.append("sh")
        listParts.extend(shlex.quote(s) for s in listScriptArguments)
    return " ".join(listParts)


def _fdictAdministratorStep(dictFacts, sStepName, sTitle, sExplanation,
                            listActions):
    """Return an administrator step, or the needs-you form of it.

    With no prompt the operating system can show (an SSH session, a
    headless machine), the same scripts become commands to paste, and
    Continue picks up once they have run.
    """
    if dictFacts.get("sPrivilegeChannel", S_PRIVILEGE_NONE) != S_PRIVILEGE_NONE:
        return _fdictStep(
            sStepName, sTitle, S_KIND_ADMINISTRATOR,
            sExplanation + " Your computer will ask for an administrator "
            "password; vaibify never sees it.", listActions)
    listCommands = []
    for dictAction in listActions:
        if len(listActions) > 1:
            listCommands.append("# " + dictAction["sLabel"] + ":")
        for dictInvocation in dictAction["listInvocations"]:
            if dictInvocation["sRunAs"] == "administrator":
                listCommands.append(fsDescribeAdministratorCommand(
                    dictInvocation["sScript"],
                    dictInvocation["listScriptArguments"]))
            else:
                listCommands.append(shlex.join(dictInvocation["listArgv"]))
    return _fdictStep(
        sStepName, sTitle, S_KIND_NEEDS_YOU,
        sExplanation + " This step needs an administrator, and there is "
        "no screen here for your computer to ask on, so run the command "
        "below in a terminal on this machine, then press Continue.",
        listCommandsToShow=listCommands)


# ---------------------------------------------------------------------------
# Colima settings
# ---------------------------------------------------------------------------

def _fiHostMemoryGigabytes(dictFacts):
    return int(dictFacts.get("iHostMemoryBytes", 0)) // _I_GIGABYTE


def fdictDefaultColimaSettings(dictFacts):
    """Return the CPU and memory a NEW Colima machine gets by default.

    CPUs: every host core but one. Memory: half the host's RAM, never
    more than the RAM less 4 GB that macOS needs, and never under the
    4 GB a vaibify build needs (ruled 2026-10-24).
    """
    iHostGigabytes = _fiHostMemoryGigabytes(dictFacts)
    iMemory = min(iHostGigabytes // 2, iHostGigabytes - 4)
    return {
        "iCpus": max(1, int(dictFacts.get("iHostCpuCount", 1)) - 1),
        "iMemoryGigabytes": max(I_MINIMUM_DOCKER_MEMORY_GIGABYTES, iMemory),
    }


def fdictValidateColimaSettings(dictRequested, dictFacts):
    """Return the requested CPU and memory as integers, or raise ValueError."""
    dictSettings = fdictDefaultColimaSettings(dictFacts)
    dictBounds = {
        "iCpus": (1, max(1, int(dictFacts.get("iHostCpuCount", 1)))),
        "iMemoryGigabytes": (2, max(2, _fiHostMemoryGigabytes(dictFacts))),
    }
    for sKey, (iLow, iHigh) in dictBounds.items():
        if sKey not in (dictRequested or {}):
            continue
        if isinstance(dictRequested[sKey], bool) or not isinstance(
                dictRequested[sKey], int):
            raise ValueError(f"{sKey} must be a whole number.")
        iRequested = dictRequested[sKey]
        if not iLow <= iRequested <= iHigh:
            raise ValueError(
                f"{sKey} must be between {iLow} and {iHigh} on this machine.")
        dictSettings[sKey] = iRequested
    return dictSettings


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

def _flistMissingMacPieces(dictFacts):
    """Name what a Colima setup still lacks; only buildx if Docker answers.

    A daemon that already answers belongs to whatever runtime the
    researcher chose (Docker Desktop, OrbStack, Colima), and the wizard
    never installs a second one beside it.
    """
    setCommands = set(dictFacts.get("listCommands", []))
    listMissing = []
    if not dictFacts.get("bDaemonAnswers"):
        listMissing = [s for s in ("colima", "docker") if s not in setCommands]
    if not dictFacts.get("bBuildxWorks") and not dictFacts.get(
            "bBuildxInstalledUnlinked"):
        listMissing.append("buildx")
    return listMissing


def _fdictMacInstallStep(dictFacts, listMissing):
    setCommands = set(dictFacts.get("listCommands", []))
    if "brew" in setCommands:
        listFormulae = [_DICT_HOMEBREW_FORMULA[s] for s in listMissing]
        return _fdictStep(
            "install-runtime", "Install Colima and Docker", S_KIND_RUN,
            "Homebrew will install " + ", ".join(listFormulae) + ".",
            [_fdictAction("install-with-homebrew", "Install", [
                _fdictInvocation(["brew", "install"] + listFormulae)])])
    if "port" in setCommands:
        listPorts = [_DICT_MACPORTS_PORT[s] for s in listMissing]
        # By full path: macOS runs an administrator script with a PATH
        # of /usr/bin:/bin:/usr/sbin:/sbin, which MacPorts is not on.
        sPort = dictFacts.get("sMacPortsPath") or "/opt/local/bin/port"
        sScript = shlex.quote(sPort) + " -N install " + " ".join(
            listPorts) + "\n"
        return _fdictAdministratorStep(
            dictFacts, "install-runtime", "Install Colima and Docker",
            "MacPorts will install " + ", ".join(listPorts) + ".",
            [_fdictAction("install-with-macports", "Install", [
                _fdictInvocation(sScript=sScript)])])
    return _fdictStep(
        "install-package-manager", "Install a package manager",
        S_KIND_NEEDS_YOU,
        "vaibify installs Colima and Docker with Homebrew or MacPorts, and "
        "this Mac has neither. Install one of them, then press Continue: "
        "vaibify will find it and carry on. Homebrew installs with the "
        "command below. MacPorts has an installer for each macOS version "
        "at " + S_MACPORTS_INSTALL_PAGE + ".",
        listCommandsToShow=[S_HOMEBREW_INSTALL_COMMAND])


def _fdictLinkBuildxStep(dictFacts):
    sPrefix = dictFacts.get("sHomebrewPrefix") or "$(brew --prefix)"
    return _fdictStep(
        "link-buildx", "Let Docker find buildx", S_KIND_NEEDS_YOU,
        "Homebrew installed docker buildx, but Docker looks for plugins "
        "in ~/.docker/cli-plugins and does not find it there. Link it "
        "with the commands below, then press Continue.",
        listCommandsToShow=[
            "mkdir -p ~/.docker/cli-plugins",
            "ln -sfn " + shlex.quote(
                sPrefix + "/opt/docker-buildx/bin/docker-buildx")
            + " ~/.docker/cli-plugins/docker-buildx",
        ])


def _flistColimaProfileArguments(dictMachine):
    if dictMachine["sProfile"] == "default":
        return []
    return ["--profile", dictMachine["sProfile"]]


def _fsColimaArchitecture(dictFacts):
    return "aarch64" if dictFacts.get("sArchitecture") == "arm64" else "x86_64"


def _fdictStartNewColimaStep(dictFacts, dictSettings):
    listArgv = [
        "colima", "start", "--arch", _fsColimaArchitecture(dictFacts),
        "--vm-type", "vz", "--cpus", str(dictSettings["iCpus"]),
        "--memory", str(dictSettings["iMemoryGigabytes"]),
    ]
    return _fdictStep(
        "start-colima", "Start Colima", S_KIND_RUN,
        "The first start downloads and boots the small Linux virtual "
        "machine that runs Docker, with the CPUs and memory below; later "
        "starts reuse it. Colima also makes itself Docker's active "
        "context, so every docker command on this Mac will use it.",
        [_fdictAction("start-new-colima", "Start", [
            _fdictInvocation(listArgv)])],
        dictSettings=dict(dictSettings))


def _fdictStartExistingColimaStep(dictMachine):
    listArgv = ["colima", "start"] + _flistColimaProfileArguments(dictMachine)
    return _fdictStep(
        "start-colima", "Start Colima", S_KIND_RUN,
        f"Your Colima machine '{dictMachine['sProfile']}' is stopped. "
        "Starting it keeps its settings.",
        [_fdictAction("start-existing-colima", "Start", [
            _fdictInvocation(listArgv)])])


def _fdictResizeColimaStep(dictFacts, dictMachine, dictSettings):
    """Offer more memory for an undersized machine, or say why not now."""
    fGigabytes = dictMachine["iMemoryBytes"] / _I_GIGABYTE
    sExplanation = (
        f"Your Colima machine '{dictMachine['sProfile']}' has "
        f"{fGigabytes:.1f} GB of memory; a vaibify build needs at least "
        f"{I_MINIMUM_DOCKER_MEMORY_GIGABYTES} GB.")
    listRunning = dictFacts.get("listRunningVaibifyContainers", [])
    if listRunning:
        return _fdictStep(
            "resize-colima", "Give Colima more memory", S_KIND_NEEDS_YOU,
            sExplanation + " Restarting it would stop these running "
            "containers: " + ", ".join(listRunning) + ". Stop them, then "
            "press Continue.", bOptional=True)
    listProfile = _flistColimaProfileArguments(dictMachine)
    return _fdictStep(
        "resize-colima", "Give Colima more memory", S_KIND_RUN,
        sExplanation + " Restarting it with more memory stops every "
        "container in it while it restarts.",
        [_fdictAction("resize-colima", "Restart with more memory", [
            _fdictInvocation(["colima", "stop"] + listProfile),
            _fdictInvocation(
                ["colima", "start"] + listProfile + [
                    "--cpus", str(dictSettings["iCpus"]),
                    "--memory", str(dictSettings["iMemoryGigabytes"])]),
        ])],
        dictSettings=dict(dictSettings), bOptional=True)


def _fdictSelectColimaMachine(listMachines, sActiveProfile):
    """Return the machine Docker points at, else the default, else the first."""
    for dictMachine in listMachines:
        if sActiveProfile and dictMachine["sProfile"] == sActiveProfile:
            return dictMachine
    for dictMachine in listMachines:
        if dictMachine["sProfile"] == "default":
            return dictMachine
    return listMachines[0] if listMachines else None


def _fsSlugForApplication(sApplication):
    return sApplication.lower().replace(" ", "-")


def _fdictStartDesktopApplicationAction(sApplication):
    return _fdictAction(
        "start-" + _fsSlugForApplication(sApplication), f"Start {sApplication}",
        [_fdictInvocation(["open", "-a", sApplication])])


def _fdictStartDesktopApplicationStep(sApplication):
    return _fdictStep(
        "start-desktop-application", f"Start {sApplication}", S_KIND_RUN,
        f"{sApplication} is installed but not running. vaibify uses it as "
        "your Docker and will not install a second one beside it.",
        [_fdictStartDesktopApplicationAction(sApplication)])


def _fdictChooseMacRuntimeStep(listApplications, dictMachine):
    """Ask which Docker to start when nothing on the machine says.

    Docker's active context names neither the desktop applications nor
    the Colima machine, so either could be the researcher's choice;
    starting one silently could switch them away from the other.
    """
    listActions = [_fdictStartDesktopApplicationAction(s)
                   for s in listApplications]
    listActions.append(_fdictAction(
        "start-existing-colima", f"Start Colima '{dictMachine['sProfile']}'",
        [_fdictInvocation(["colima", "start"]
                          + _flistColimaProfileArguments(dictMachine))]))
    return _fdictStep(
        "choose-runtime", "Choose which Docker to start", S_KIND_RUN,
        "This Mac has " + ", ".join(listApplications) + " and a Colima "
        f"machine '{dictMachine['sProfile']}', none of them running, and "
        "Docker's active context does not say which one you use. Start "
        "the one you want vaibify to use.", listActions)


def _flistPlanStoppedMacRuntime(dictFacts):
    """Return the step that starts the researcher's chosen runtime, or None.

    None means the Colima path decides: the active context names a
    Colima machine, or no desktop application is installed.
    """
    listApplications = [
        s for s in dictFacts.get("listDesktopApplications", [])
        if s in T_MACOS_DESKTOP_APPLICATIONS]
    sContextApplication = fsDesktopApplicationForContext(
        dictFacts.get("sActiveDockerContext", ""))
    if sContextApplication in listApplications:
        return [_fdictStartDesktopApplicationStep(sContextApplication)]
    if dictFacts.get("sActiveColimaProfile") or not listApplications:
        return None
    dictMachine = _fdictSelectColimaMachine(
        dictFacts.get("listColimaMachines", []), "")
    if dictMachine is None:
        return [_fdictStartDesktopApplicationStep(listApplications[0])]
    return [_fdictChooseMacRuntimeStep(listApplications, dictMachine)]


def _fdictPlanColimaMachineStep(dictFacts, dictSettings):
    """Return the step the Colima machine needs, or None.

    A restart for more memory is offered only for the machine Docker's
    active context names: the running containers were read from that
    machine's daemon, so it is the only one whose restart was checked.
    """
    sActiveProfile = dictFacts.get("sActiveColimaProfile", "")
    dictMachine = _fdictSelectColimaMachine(
        dictFacts.get("listColimaMachines", []), sActiveProfile)
    if dictMachine is None:
        return _fdictStartNewColimaStep(dictFacts, dictSettings)
    if dictMachine["sStatus"].lower() != "running":
        return _fdictStartExistingColimaStep(dictMachine)
    if dictMachine["sProfile"] != sActiveProfile:
        return None
    if dictMachine["iMemoryBytes"] < (
            I_MINIMUM_DOCKER_MEMORY_GIGABYTES * _I_GIGABYTE):
        return _fdictResizeColimaStep(dictFacts, dictMachine, dictSettings)
    return None


def _flistPlanMacSteps(dictFacts, dictSettings):
    if not dictFacts.get("bDaemonAnswers"):
        listStopped = _flistPlanStoppedMacRuntime(dictFacts)
        if listStopped is not None:
            return listStopped
    listSteps = []
    listMissing = _flistMissingMacPieces(dictFacts)
    if listMissing:
        listSteps.append(_fdictMacInstallStep(dictFacts, listMissing))
    elif not dictFacts.get("bBuildxWorks"):
        listSteps.append(_fdictLinkBuildxStep(dictFacts))
    if dictFacts.get("bDaemonAnswers") and not dictFacts.get(
            "sActiveColimaProfile"):
        return listSteps
    dictMachineStep = _fdictPlanColimaMachineStep(dictFacts, dictSettings)
    if dictMachineStep is not None:
        listSteps.append(dictMachineStep)
    return listSteps


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------

def _ftResolveLinuxFamily(dictOsRelease):
    """Return ``(sManager, sRepository, bOfficial)``, or ``None``.

    Only distributions Docker publishes a repository for, and their
    declared derivatives (``ID_LIKE``), which use the parent's
    repository and which Docker does not officially support.
    """
    sId = (dictOsRelease.get("ID") or "").lower()
    listLike = (dictOsRelease.get("ID_LIKE") or "").lower().split()
    dictOfficial = {"ubuntu": ("apt", "ubuntu"), "debian": ("apt", "debian"),
                    "fedora": ("dnf", "fedora"), "rhel": ("dnf", "rhel"),
                    "centos": ("dnf", "centos")}
    if sId in dictOfficial:
        return dictOfficial[sId] + (True,)
    for sLike, tFamily in (("ubuntu", ("apt", "ubuntu")),
                           ("debian", ("apt", "debian")),
                           ("rhel", ("dnf", "centos")),
                           ("centos", ("dnf", "centos")),
                           ("fedora", ("dnf", "fedora"))):
        if sLike in listLike:
            return tFamily + (False,)
    return None


def _fsBuildEngineScript(sManager, sRepository):
    if sManager == "apt":
        return _S_APT_ENGINE_SCRIPT.format(
            sRepository=sRepository,
            sSuiteExpression=_DICT_APT_SUITE_EXPRESSION[sRepository])
    return ("set -e\n" + _DICT_DNF_REPOSITORY_SETUP[sRepository]
            + _S_RPM_KEY_SCRIPT.format(
                sRepository=sRepository,
                sFingerprint=_S_DOCKER_RPM_KEY_FINGERPRINT)
            + _S_DNF_ENGINE_INSTALL)


def _fdictInstallEngineStep(dictFacts, tFamily):
    sManager, sRepository, bOfficial = tFamily
    sName = dictFacts.get("dictOsRelease", {}).get("PRETTY_NAME") or "Linux"
    sExplanation = (
        f"vaibify will install Docker Engine on {sName} from Docker's own "
        f"{sRepository} repository, following Docker's install page, and "
        "start it.")
    if not bOfficial:
        sExplanation += (
            f" Docker does not officially support {sName}; its {sRepository} "
            "repository usually works on it.")
    return _fdictAdministratorStep(
        dictFacts, "install-engine", "Install Docker Engine", sExplanation,
        [_fdictAction("install-engine", "Install Docker", [
            _fdictInvocation(sScript=_fsBuildEngineScript(
                sManager, sRepository))])])


def _fdictUnsupportedDistributionStep(dictFacts):
    sName = dictFacts.get("dictOsRelease", {}).get("PRETTY_NAME") or (
        "this Linux distribution")
    return _fdictStep(
        "install-engine", "Install Docker Engine", S_KIND_NEEDS_YOU,
        f"Docker publishes no repository for {sName}, so vaibify will not "
        "guess at its package names. Install Docker Engine and its buildx "
        "plugin with your distribution's own instructions (Docker's index "
        "is at " + S_DOCKER_INSTALL_PAGE + "), then press Continue.")


def _fdictChooseAccessStep(dictFacts, tFamily):
    sUser = dictFacts.get("sUserName", "")
    listActions = [_fdictAction(
        "join-docker-group", "Join the docker group", [_fdictInvocation(
            sScript=_S_JOIN_DOCKER_GROUP_SCRIPT,
            listScriptArguments=[sUser])])]
    if tFamily is not None:
        listRootless = []
        if "dockerd-rootless-setuptool.sh" not in dictFacts.get(
                "listCommands", []):
            listRootless.append(_fdictInvocation(
                sScript=_DICT_ROOTLESS_PREREQUISITES_SCRIPT[tFamily[0]]))
        listRootless.append(_fdictInvocation(
            ["dockerd-rootless-setuptool.sh", "install"]))
        listRootless.append(_fdictInvocation(
            ["sh", "-c", _S_USE_ROOTLESS_CONTEXT_SCRIPT]))
        listActions.append(_fdictAction(
            "set-up-rootless", "Set up rootless Docker", listRootless))
    return _fdictAdministratorStep(
        dictFacts, "choose-docker-access", "Let vaibify reach Docker",
        f"Docker is running, but {sUser} may not use it. Choose one: the "
        "docker group (simple, and equivalent to root on this machine, "
        "because a container can mount any host directory), or rootless "
        "Docker (a daemon of your own, recommended on a shared machine).",
        listActions)


def _fdictLogOutStep():
    return _fdictStep(
        "log-out-and-back-in", "Log out and back in", S_KIND_NEEDS_YOU,
        "You are now in the docker group, but this session started before "
        "you joined it. Log out and back in (or restart the machine if it "
        "is a virtual machine), start vaibify again, and press Continue.")


def _flistPlanLinuxAccessSteps(dictFacts, tFamily):
    if dictFacts.get("bUserListedInDockerGroup") and not dictFacts.get(
            "bProcessHasDockerGroup"):
        return [_fdictLogOutStep()]
    return [_fdictChooseAccessStep(dictFacts, tFamily)]


def _fdictInstallBuildxStep(dictFacts, tFamily):
    if tFamily is None:
        return _fdictStep(
            "install-buildx", "Install docker buildx", S_KIND_NEEDS_YOU,
            "Docker works, but its buildx plugin is missing, and vaibify "
            "builds with it. Install your distribution's buildx package, "
            "then press Continue.")
    return _fdictAdministratorStep(
        dictFacts, "install-buildx", "Install docker buildx",
        "Docker works, but its buildx plugin is missing, and vaibify builds "
        "with it.",
        [_fdictAction("install-buildx", "Install buildx", [
            _fdictInvocation(sScript=_DICT_BUILDX_SCRIPT[tFamily[0]])])])


def _flistPlanLinuxDaemonSteps(dictFacts, tFamily):
    """Return the step that gets a daemon answering, by what is installed."""
    if dictFacts.get("bRootlessServiceInstalled"):
        return [_fdictStep(
            "start-rootless-engine", "Start rootless Docker", S_KIND_RUN,
            "Your rootless Docker is installed but not running.",
            [_fdictAction("start-rootless-engine", "Start", [
                _fdictInvocation(
                    ["systemctl", "--user", "start", "docker.service"]),
                _fdictInvocation(["sh", "-c", _S_USE_ROOTLESS_CONTEXT_SCRIPT]),
            ])])]
    if dictFacts.get("bDesktopServiceInstalled"):
        return [_fdictStep(
            "start-desktop-application", "Start Docker Desktop", S_KIND_RUN,
            "Docker Desktop is installed but not running. vaibify uses it "
            "and will not install a second Docker beside it.",
            [_fdictAction("start-desktop-application", "Start", [
                _fdictInvocation(
                    ["systemctl", "--user", "start", "docker-desktop"])])])]
    if dictFacts.get("bEngineServiceInstalled"):
        return [_fdictAdministratorStep(
            dictFacts, "start-engine", "Start Docker Engine",
            "Docker Engine is installed but not running; vaibify will start "
            "it and have it start with the machine.",
            [_fdictAction("start-engine", "Start Docker", [
                _fdictInvocation(sScript=_S_START_ENGINE_SCRIPT)])])]
    if tFamily is None:
        return [_fdictUnsupportedDistributionStep(dictFacts)]
    return [_fdictInstallEngineStep(dictFacts, tFamily)]


def _flistPlanLinuxSteps(dictFacts):
    tFamily = _ftResolveLinuxFamily(dictFacts.get("dictOsRelease", {}))
    if dictFacts.get("bDaemonAnswers"):
        if not dictFacts.get("bBuildxWorks"):
            return [_fdictInstallBuildxStep(dictFacts, tFamily)]
        return []
    if dictFacts.get("bDaemonPermissionDenied"):
        return _flistPlanLinuxAccessSteps(dictFacts, tFamily)
    return _flistPlanLinuxDaemonSteps(dictFacts, tFamily)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def _fdictForeignDockerHostStep(dictFacts):
    return _fdictStep(
        "docker-host-variable", "Check DOCKER_HOST", S_KIND_NEEDS_YOU,
        "DOCKER_HOST is set in vaibify's environment, so vaibify talks to "
        f"the daemon it names ({dictFacts.get('sDockerHost')}), and that "
        "daemon does not answer. Start it, or unset DOCKER_HOST and start "
        "vaibify again, then press Continue.")


def _fdictUnsupportedPlatformStep(dictFacts):
    return _fdictStep(
        "unsupported-platform", "Unsupported platform", S_KIND_NEEDS_YOU,
        f"vaibify's containers run on macOS and Linux; this machine reports "
        f"'{dictFacts.get('sPlatform')}'. Install Docker yourself, then "
        "press Continue.")


def _fdictStillUnansweredStep(dictFacts):
    """Everything the wizard looks for is present, yet Docker is silent."""
    sExplanation = (
        "Everything vaibify looks for is installed and running, but Docker "
        "still does not answer as you. ")
    listCommands = ["docker context ls", "vaibify doctor"]
    if dictFacts.get("listColimaMachines"):
        sExplanation += (
            "Docker's active context may point away from Colima; "
            "`docker context use colima` points it back. ")
    return _fdictStep(
        "diagnose", "Find out why Docker does not answer", S_KIND_NEEDS_YOU,
        sExplanation + "The commands below show where Docker is looking "
        "and run vaibify's full diagnosis. Press Continue once it answers.",
        listCommandsToShow=listCommands)


def fdictPlanRuntimeSetup(dictFacts, dictSettings=None):
    """Return ``{bReady, listSteps}`` for this machine's facts.

    ``bReady`` means the daemon answers and buildx works, which is what
    a vaibify build needs. A ready machine can still carry an optional
    step (more memory for an undersized Colima machine).
    """
    dictSettings = dictSettings or fdictDefaultColimaSettings(dictFacts)
    sPlatform = dictFacts.get("sPlatform", "")
    bReady = bool(dictFacts.get("bDaemonAnswers")
                  and dictFacts.get("bBuildxWorks"))
    if dictFacts.get("sDockerHost") and not dictFacts.get("bDaemonAnswers"):
        listSteps = [_fdictForeignDockerHostStep(dictFacts)]
    elif sPlatform == "darwin":
        listSteps = _flistPlanMacSteps(dictFacts, dictSettings)
    elif sPlatform.startswith("linux"):
        listSteps = _flistPlanLinuxSteps(dictFacts)
    else:
        listSteps = [] if bReady else [_fdictUnsupportedPlatformStep(dictFacts)]
    if bReady:
        listSteps = [d for d in listSteps if d.get("bOptional")]
    elif not listSteps:
        listSteps = [_fdictStillUnansweredStep(dictFacts)]
    return {"bReady": bReady, "listSteps": listSteps}
