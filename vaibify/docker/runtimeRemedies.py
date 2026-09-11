"""The one place vaibify turns a runtime situation into a COMMAND.

Every remediation vaibify prints is runtime-dependent, and a wrong one
is worse than none: ``sudo systemctl start docker`` is wrong on Docker
Desktop, wrong on Colima, and on a rootless daemon it starts a SECOND,
rootful daemon that the researcher's context does not point at, which
looks like the advice failing to work.

Two rules hold this honest, and both are the same rule seen twice.

**An unidentified runtime is never guessed.** The answer for
``S_RUNTIME_UNKNOWN`` names the diagnostic step -- find out which
runtime this is -- rather than the most popular command. A command
that does not apply sends a researcher to look for a service that does
not exist on their machine.

It lives under ``vaibify/docker/`` rather than beside the CLI because
the error-diagnosis catalogue needs it too. That is not a tidiness
move: routing only the NEW checks through the classifier left the
oldest and most-seen finding of all -- "the Docker daemon is not
reachable" -- still answering ``colima start`` to a Docker Desktop
user on macOS, the default profile to somebody running
``colima-gpu``, and ``sudo systemctl start docker`` to a rootless
daemon. A second authority on the same question is how one surface
gets corrected and the other does not.

**Nothing here reclaims disk with ``docker system prune -af``.** The
``-a`` removes every image not attached to a running container, which
on a laptop where the project container is merely stopped is the
project's ONLY local copy of its image -- the same image an L3
envelope pins by digest. The advice offered instead is the build cache
and dangling layers, which no envelope pins.
"""

import sys

from .dockerContext import (
    S_RUNTIME_COLIMA, S_RUNTIME_DOCKER_DESKTOP, S_RUNTIME_LINUX_ROOTFUL,
    S_RUNTIME_LINUX_ROOTLESS, S_RUNTIME_UNKNOWN,
)


__all__ = [
    "ftRemedyForSituation", "fsColimaCommand",
    "S_SITUATION_DAEMON_UNREACHABLE", "S_SITUATION_RECLAIM_DISK",
    "S_SITUATION_MORE_DAEMON_MEMORY", "S_SITUATION_MORE_DAEMON_DISK",
    "S_SITUATION_MORE_HOST_DISK", "S_SITUATION_RESTART_RUNTIME",
    "S_SITUATION_STALE_COLIMA_LOCK",
]


S_SITUATION_DAEMON_UNREACHABLE = "daemon-unreachable"
S_SITUATION_RECLAIM_DISK = "reclaim-disk"
S_SITUATION_MORE_DAEMON_MEMORY = "more-daemon-memory"
S_SITUATION_MORE_DAEMON_DISK = "more-daemon-disk"
S_SITUATION_RESTART_RUNTIME = "restart-runtime"
S_SITUATION_MORE_HOST_DISK = "more-host-disk"
S_SITUATION_STALE_COLIMA_LOCK = "stale-colima-lock"

_S_UNKNOWN_RUNTIME_REMEDIATION = (
    "vaibify could not identify which Docker runtime this host uses, "
    "so it will not guess at the command that manages it. Find out "
    "which one is active first -- the command below lists every "
    "context and marks the current one."
)
_S_UNKNOWN_RUNTIME_COMMAND = "docker context ls"


def fsColimaCommand(sVerb, dictRuntime, sArguments=""):
    """Return a ``colima`` command carrying the ACTIVE profile.

    A researcher running ``colima start --profile gpu`` gets advice
    naming ``gpu``. Omitting it would print a command that operates on
    a different virtual machine than the one their context points at,
    which is a way to lose the running one.
    """
    sProfile = dictRuntime.get("sColimaProfile") or ""
    listParts = ["colima", sVerb]
    if sProfile and sProfile != "default":
        listParts.extend(["--profile", sProfile])
    if sArguments:
        listParts.append(sArguments)
    return " ".join(listParts)


def _ftDaemonUnreachableRemedy(dictRuntime):
    """Return (remediation, command) for a daemon that will not answer."""
    sRuntime = dictRuntime.get("sRuntime")
    if sRuntime == S_RUNTIME_COLIMA:
        return (
            "The Colima virtual machine is not running.",
            fsColimaCommand("start", dictRuntime),
        )
    if sRuntime == S_RUNTIME_DOCKER_DESKTOP:
        if sys.platform == "darwin":
            return (
                "Docker Desktop is not running. Start it and wait for "
                "the whale icon to stop animating.",
                "open -a Docker",
            )
        return (
            "Docker Desktop is not running. Start it from your "
            "application launcher, or through its user service.",
            "systemctl --user start docker-desktop",
        )
    if sRuntime == S_RUNTIME_LINUX_ROOTLESS:
        return (
            "The rootless Docker daemon is not running. Start it as "
            "your own user -- `sudo` would start the rootful daemon, "
            "which your Docker context does not point at.",
            "systemctl --user start docker",
        )
    if sRuntime == S_RUNTIME_LINUX_ROOTFUL:
        return (
            "The system Docker daemon is not running.",
            "sudo systemctl start docker",
        )
    return (_S_UNKNOWN_RUNTIME_REMEDIATION, _S_UNKNOWN_RUNTIME_COMMAND)


def _ftRestartRuntimeRemedy(dictRuntime):
    """Return (remediation, command) for restarting the runtime itself."""
    sRuntime = dictRuntime.get("sRuntime")
    if sRuntime == S_RUNTIME_COLIMA:
        return (
            "Restart the Colima virtual machine. Every container it "
            "hosts stops with it.",
            fsColimaCommand("restart", dictRuntime),
        )
    if sRuntime == S_RUNTIME_DOCKER_DESKTOP:
        return (
            "Restart Docker Desktop from its menu (Troubleshoot -> "
            "Restart). Every container it hosts stops with it.",
            "",
        )
    if sRuntime == S_RUNTIME_LINUX_ROOTLESS:
        return (
            "Restart the rootless daemon. Every container it hosts "
            "stops with it.",
            "systemctl --user restart docker",
        )
    if sRuntime == S_RUNTIME_LINUX_ROOTFUL:
        return (
            "Restart the system daemon. Every container it hosts "
            "stops with it.",
            "sudo systemctl restart docker",
        )
    return (_S_UNKNOWN_RUNTIME_REMEDIATION, _S_UNKNOWN_RUNTIME_COMMAND)


def _ftReclaimDiskRemedy(dictRuntime):
    """Return (remediation, command) for reclaiming daemon storage."""
    del dictRuntime
    return (
        "Reclaim the build cache first -- it is usually the largest "
        "reclaimable thing and no envelope pins it. Then `docker "
        "image prune` for dangling layers. Do NOT run `docker system "
        "prune -a`: it removes every image without a running "
        "container, which on this machine includes the image your "
        "project's environment snapshot pins by digest.",
        "docker builder prune",
    )


def _ftMoreDaemonMemoryRemedy(dictRuntime):
    """Return (remediation, command) for enlarging the daemon's RAM."""
    sRuntime = dictRuntime.get("sRuntime")
    if sRuntime == S_RUNTIME_COLIMA:
        return (
            "Give the Colima virtual machine more memory. It must be "
            "stopped first, and every container it hosts stops with "
            "it.",
            fsColimaCommand("stop", dictRuntime) + " && "
            + fsColimaCommand("start", dictRuntime, "--memory 8"),
        )
    if sRuntime == S_RUNTIME_DOCKER_DESKTOP:
        return (
            "Raise the memory limit in Docker Desktop: Settings -> "
            "Resources -> Memory. There is no command-line equivalent.",
            "",
        )
    if sRuntime in (S_RUNTIME_LINUX_ROOTFUL, S_RUNTIME_LINUX_ROOTLESS):
        return (
            "This daemon runs directly on the host, so it has as much "
            "memory as the host does. Lower the project's memory cap "
            "in vaibify.yml instead, or add host RAM.",
            "",
        )
    return (_S_UNKNOWN_RUNTIME_REMEDIATION, _S_UNKNOWN_RUNTIME_COMMAND)


def _ftMoreDaemonDiskRemedy(dictRuntime):
    """Return (remediation, command) for enlarging the daemon's disk."""
    sRuntime = dictRuntime.get("sRuntime")
    if sRuntime == S_RUNTIME_COLIMA:
        return (
            "Restart Colima asking for a larger disk. Current Colima "
            "versions grow the virtual machine's disk in place when "
            "the configured size increases; if yours does not, the "
            "size will simply be unchanged and nothing is lost. Only "
            "then consider recreating the VM -- which DESTROYS every "
            "image and volume in it, so deposit or push anything you "
            "need first.",
            fsColimaCommand("stop", dictRuntime) + " && "
            + fsColimaCommand("start", dictRuntime, "--disk 100"),
        )
    if sRuntime == S_RUNTIME_DOCKER_DESKTOP:
        return (
            "Raise the disk image size in Docker Desktop: Settings -> "
            "Resources -> Virtual disk limit.",
            "",
        )
    if sRuntime in (S_RUNTIME_LINUX_ROOTFUL, S_RUNTIME_LINUX_ROOTLESS):
        return (
            "This daemon stores images on the host filesystem, so its "
            "headroom is the host's. Free space on the filesystem "
            "holding Docker's data root.",
            "docker info --format '{{.DockerRootDir}}'",
        )
    return (_S_UNKNOWN_RUNTIME_REMEDIATION, _S_UNKNOWN_RUNTIME_COMMAND)


def _ftMoreHostDiskRemedy(dictRuntime):
    """Return (remediation, command) for the HOST filesystem being full.

    Deliberately runtime-independent, and that is the whole point of
    it having its own situation. An earlier version answered this with
    the DAEMON-disk remedy, so a researcher short of space in their
    own home directory was told to delete their Colima virtual machine
    -- destroying every image and volume in it, and not freeing a byte
    of the filesystem that was actually full.
    """
    del dictRuntime
    return (
        "This is space on your own machine's filesystem, not inside "
        "the Docker virtual machine, so no Docker command frees it. "
        "Remove files you no longer need, or free space on the volume "
        "holding your home directory.",
        "",
    )


def _ftStaleColimaLockRemedy(dictRuntime):
    """Return (remediation, command) for a Colima VM lock left behind."""
    if dictRuntime.get("sRuntime") != S_RUNTIME_COLIMA:
        return (_S_UNKNOWN_RUNTIME_REMEDIATION, _S_UNKNOWN_RUNTIME_COMMAND)
    return (
        "Colima's VM lock is stale, likely from an unclean shutdown. "
        "Force-stop and restart it.",
        fsColimaCommand("stop", dictRuntime, "--force") + " && "
        + fsColimaCommand("start", dictRuntime),
    )


_DICT_SITUATION_REMEDIES = {
    S_SITUATION_DAEMON_UNREACHABLE: _ftDaemonUnreachableRemedy,
    S_SITUATION_RESTART_RUNTIME: _ftRestartRuntimeRemedy,
    S_SITUATION_RECLAIM_DISK: _ftReclaimDiskRemedy,
    S_SITUATION_MORE_DAEMON_MEMORY: _ftMoreDaemonMemoryRemedy,
    S_SITUATION_MORE_DAEMON_DISK: _ftMoreDaemonDiskRemedy,
    S_SITUATION_MORE_HOST_DISK: _ftMoreHostDiskRemedy,
    S_SITUATION_STALE_COLIMA_LOCK: _ftStaleColimaLockRemedy,
}


def ftRemedyForSituation(sSituation, dictRuntime):
    """Return ``(sRemediation, sCommand)`` for one situation.

    An unknown situation name raises rather than returning a plausible
    default: a diagnostic that silently answers the wrong question is
    the failure mode this module exists to prevent.
    """
    ffnRemedy = _DICT_SITUATION_REMEDIES.get(sSituation)
    if ffnRemedy is None:
        raise ValueError(
            f"{sSituation!r} is not a declared remediation situation; "
            f"the declared set is {sorted(_DICT_SITUATION_REMEDIES)}"
        )
    return ffnRemedy(dictRuntime or {"sRuntime": S_RUNTIME_UNKNOWN})
