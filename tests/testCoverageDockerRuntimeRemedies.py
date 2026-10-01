"""Every runtime-dependent remedy names a command that fits its runtime.

A remediation that does not apply to the researcher's runtime is worse
than none: it sends them looking for a service their machine does not
have, or -- on a rootless daemon -- starts a second, rootful one. These
tests walk the whole situation-by-runtime table and pin the command
each cell answers, plus the two invariants that hold across all of it:
an unidentified runtime is never guessed at, and no remedy reclaims
disk with ``docker system prune -a``.
"""

from unittest.mock import patch

import pytest

from vaibify.docker import runtimeRemedies
from vaibify.docker.dockerContext import (
    S_RUNTIME_COLIMA,
    S_RUNTIME_DOCKER_DESKTOP,
    S_RUNTIME_LINUX_ROOTFUL,
    S_RUNTIME_LINUX_ROOTLESS,
    S_RUNTIME_UNKNOWN,
)
from vaibify.docker.runtimeRemedies import (
    S_SITUATION_DAEMON_UNREACHABLE,
    S_SITUATION_MORE_DAEMON_DISK,
    S_SITUATION_MORE_DAEMON_MEMORY,
    S_SITUATION_MORE_HOST_DISK,
    S_SITUATION_RECLAIM_DISK,
    S_SITUATION_RESTART_RUNTIME,
    S_SITUATION_STALE_COLIMA_LOCK,
    fsColimaCommand,
    ftRemedyForSituation,
)


LIST_ALL_SITUATIONS = [
    S_SITUATION_DAEMON_UNREACHABLE, S_SITUATION_RESTART_RUNTIME,
    S_SITUATION_RECLAIM_DISK, S_SITUATION_MORE_DAEMON_MEMORY,
    S_SITUATION_MORE_DAEMON_DISK, S_SITUATION_MORE_HOST_DISK,
    S_SITUATION_STALE_COLIMA_LOCK,
]
LIST_ALL_RUNTIMES = [
    S_RUNTIME_COLIMA, S_RUNTIME_DOCKER_DESKTOP, S_RUNTIME_LINUX_ROOTFUL,
    S_RUNTIME_LINUX_ROOTLESS, S_RUNTIME_UNKNOWN,
]
S_UNKNOWN_COMMAND = "docker context ls"


def fdictRuntime(sRuntime, sColimaProfile=""):
    """Return a classifier answer naming one runtime."""
    return {"sRuntime": sRuntime, "sColimaProfile": sColimaProfile}


@pytest.mark.parametrize("sSituation,sRuntime,sExpectedCommand", [
    (S_SITUATION_DAEMON_UNREACHABLE, S_RUNTIME_COLIMA, "colima start"),
    (S_SITUATION_DAEMON_UNREACHABLE, S_RUNTIME_LINUX_ROOTLESS,
     "systemctl --user start docker"),
    (S_SITUATION_DAEMON_UNREACHABLE, S_RUNTIME_LINUX_ROOTFUL,
     "sudo systemctl start docker"),
    (S_SITUATION_RESTART_RUNTIME, S_RUNTIME_COLIMA, "colima restart"),
    (S_SITUATION_RESTART_RUNTIME, S_RUNTIME_DOCKER_DESKTOP, ""),
    (S_SITUATION_RESTART_RUNTIME, S_RUNTIME_LINUX_ROOTLESS,
     "systemctl --user restart docker"),
    (S_SITUATION_RESTART_RUNTIME, S_RUNTIME_LINUX_ROOTFUL,
     "sudo systemctl restart docker"),
    (S_SITUATION_MORE_DAEMON_MEMORY, S_RUNTIME_COLIMA,
     "colima stop && colima start --memory 8"),
    (S_SITUATION_MORE_DAEMON_MEMORY, S_RUNTIME_DOCKER_DESKTOP, ""),
    (S_SITUATION_MORE_DAEMON_MEMORY, S_RUNTIME_LINUX_ROOTFUL, ""),
    (S_SITUATION_MORE_DAEMON_MEMORY, S_RUNTIME_LINUX_ROOTLESS, ""),
    (S_SITUATION_MORE_DAEMON_DISK, S_RUNTIME_COLIMA,
     "colima stop && colima start --disk 100"),
    (S_SITUATION_MORE_DAEMON_DISK, S_RUNTIME_DOCKER_DESKTOP, ""),
    (S_SITUATION_MORE_DAEMON_DISK, S_RUNTIME_LINUX_ROOTFUL,
     "docker info --format '{{.DockerRootDir}}'"),
    (S_SITUATION_MORE_DAEMON_DISK, S_RUNTIME_LINUX_ROOTLESS,
     "docker info --format '{{.DockerRootDir}}'"),
    (S_SITUATION_STALE_COLIMA_LOCK, S_RUNTIME_COLIMA,
     "colima stop --force && colima start"),
])
def testEachRuntimeGetsItsOwnCommand(
    sSituation, sRuntime, sExpectedCommand,
):
    """The command in each cell belongs to the runtime it is printed for."""
    sRemediation, sCommand = ftRemedyForSituation(
        sSituation, fdictRuntime(sRuntime),
    )
    assert sCommand == sExpectedCommand
    assert sRemediation


@pytest.mark.parametrize("sSituation", [
    S_SITUATION_DAEMON_UNREACHABLE, S_SITUATION_RESTART_RUNTIME,
    S_SITUATION_MORE_DAEMON_MEMORY, S_SITUATION_MORE_DAEMON_DISK,
    S_SITUATION_STALE_COLIMA_LOCK,
])
def testAnUnidentifiedRuntimeIsSentToTheDiagnosticStep(sSituation):
    """Unknown never borrows the most popular runtime's command."""
    sRemediation, sCommand = ftRemedyForSituation(
        sSituation, fdictRuntime(S_RUNTIME_UNKNOWN),
    )
    assert sCommand == S_UNKNOWN_COMMAND
    assert "will not guess" in sRemediation


@pytest.mark.parametrize("sRuntime", [
    S_RUNTIME_DOCKER_DESKTOP, S_RUNTIME_LINUX_ROOTFUL,
    S_RUNTIME_LINUX_ROOTLESS, S_RUNTIME_UNKNOWN,
])
def testAStaleColimaLockOffNonColimaRuntimesIsNotGuessed(sRuntime):
    """A Colima lock remedy is only meaningful on Colima."""
    _sRemediation, sCommand = ftRemedyForSituation(
        S_SITUATION_STALE_COLIMA_LOCK, fdictRuntime(sRuntime),
    )
    assert "colima" not in sCommand
    assert sCommand == S_UNKNOWN_COMMAND


def testAMissingRuntimeAnswerIsTreatedAsUnknown():
    """No classifier answer at all is the unknown runtime, not a default."""
    _sRemediation, sCommand = ftRemedyForSituation(
        S_SITUATION_DAEMON_UNREACHABLE, None,
    )
    assert sCommand == S_UNKNOWN_COMMAND


def testAnUndeclaredSituationRaisesNamingTheDeclaredSet():
    """A diagnostic that silently answers the wrong question is refused."""
    with pytest.raises(ValueError) as errorRaised:
        ftRemedyForSituation("reboot-the-universe", fdictRuntime(
            S_RUNTIME_COLIMA,
        ))
    sMessage = str(errorRaised.value)
    assert "'reboot-the-universe'" in sMessage
    assert S_SITUATION_RECLAIM_DISK in sMessage


@pytest.mark.parametrize("sPlatform,sExpectedCommand", [
    ("darwin", "open -a Docker"),
    ("linux", "systemctl --user start docker-desktop"),
])
def testDockerDesktopStartCommandFollowsTheHostPlatform(
    sPlatform, sExpectedCommand,
):
    """Docker Desktop starts differently on macOS and on Linux."""
    with patch.object(runtimeRemedies.sys, "platform", sPlatform):
        _sRemediation, sCommand = ftRemedyForSituation(
            S_SITUATION_DAEMON_UNREACHABLE,
            fdictRuntime(S_RUNTIME_DOCKER_DESKTOP),
        )
    assert sCommand == sExpectedCommand


@pytest.mark.parametrize("sSituation", [
    S_SITUATION_DAEMON_UNREACHABLE, S_SITUATION_RESTART_RUNTIME,
    S_SITUATION_MORE_DAEMON_MEMORY, S_SITUATION_MORE_DAEMON_DISK,
    S_SITUATION_STALE_COLIMA_LOCK,
])
def testANamedColimaProfileRidesOnEveryColimaCommand(sSituation):
    """Advice for profile 'gpu' must operate on 'gpu', not on 'default'."""
    _sRemediation, sCommand = ftRemedyForSituation(
        sSituation, fdictRuntime(S_RUNTIME_COLIMA, "gpu"),
    )
    listColimaInvocations = [
        sPart.strip() for sPart in sCommand.split("&&")
    ]
    assert listColimaInvocations
    for sInvocation in listColimaInvocations:
        assert sInvocation.startswith("colima ")
        assert "--profile gpu" in sInvocation


def testTheDefaultColimaProfileIsNotSpelledOut():
    """'default' is what colima assumes; naming it adds nothing."""
    assert fsColimaCommand(
        "start", fdictRuntime(S_RUNTIME_COLIMA, "default"),
    ) == "colima start"
    assert fsColimaCommand(
        "start", fdictRuntime(S_RUNTIME_COLIMA, "work"), "--cpu 4",
    ) == "colima start --profile work --cpu 4"


def testRootlessAdviceNeverReachesForSudo():
    """sudo on a rootless host starts the daemon the context ignores."""
    for sSituation in LIST_ALL_SITUATIONS:
        _sRemediation, sCommand = ftRemedyForSituation(
            sSituation, fdictRuntime(S_RUNTIME_LINUX_ROOTLESS),
        )
        assert "sudo" not in sCommand, sSituation


@pytest.mark.parametrize("sRuntime", LIST_ALL_RUNTIMES)
def testNoRemedyEverPrunesEveryImage(sRuntime):
    """`system prune -a` would delete the image an envelope pins."""
    for sSituation in LIST_ALL_SITUATIONS:
        _sRemediation, sCommand = ftRemedyForSituation(
            sSituation, fdictRuntime(sRuntime),
        )
        assert "prune -a" not in sCommand
        assert "system prune" not in sCommand


@pytest.mark.parametrize("sRuntime", LIST_ALL_RUNTIMES)
def testReclaimAndHostDiskAreRuntimeIndependent(sRuntime):
    """The host filesystem is not inside the VM; no Docker command frees it."""
    tReclaim = ftRemedyForSituation(
        S_SITUATION_RECLAIM_DISK, fdictRuntime(sRuntime),
    )
    tHostDisk = ftRemedyForSituation(
        S_SITUATION_MORE_HOST_DISK, fdictRuntime(sRuntime),
    )
    assert tReclaim[1] == "docker builder prune"
    assert "Do NOT run `docker system prune -a`" in tReclaim[0]
    assert tHostDisk[1] == ""
    assert "no Docker command frees it" in tHostDisk[0]
