"""Step commands under the CPU-time wrapper, in a real container with GNU time.

``time`` is among the default system packages, so ``/usr/bin/time`` is in
every default image and every container step runs under the wrapper. GNU
time executes its arguments directly, with no shell: the researcher's
``cd d && python run.py`` made it try to run ``cd`` as a program (exit
127, the rest never ran), and for ``a && b`` it timed only ``a``.
Measured on 2026-10-03 in a container with GNU time installed.

These tests run the commands through the runner's own command-list
function against a real daemon, so the composition (run marker, env
prefix, wrapper, exec) is the production one. Skipped when no daemon
answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one (see
``tests/testDockerConnectionLive.py``).
"""

import asyncio
import time

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import pipelineRunner

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_WORKDIR = "/home/researcher"
S_RUN_ID = "ab12cd34ef56ab78"


@pytest.fixture(scope="module")
def liveContainer():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "900"], detach=True, labels=fdictLabels(),
    )
    try:
        iExit, baOutput = container.exec_run(
            ["sh", "-c",
             f"useradd -m {S_USER} && apt-get update -qq && "
             "apt-get install -y -qq time"], user="root")
        assert iExit == 0, baOutput.decode("utf-8", errors="replace")
        iExit, _ = container.exec_run(
            ["sh", "-c", "test -x /usr/bin/time && "
             "/usr/bin/time -f x true"], user="root")
        assert iExit == 0, "the image needs GNU time for this lane"
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _ftRunThroughTheRunner(liveContainer, listCommands, sEnvPrefix=""):
    """Run commands the way a pipeline step does; return what happened."""
    container, connection = liveContainer
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    dictVariables = {pipelineRunner.S_ENV_PREFIX_KEY: sEnvPrefix}
    iExitCode, fCpu = asyncio.run(pipelineRunner._ftRunCommandList(
        connection, container.id, listCommands, S_WORKDIR,
        dictVariables, fnCallback))
    listLines = []
    for dictEvent in listEvents:
        if dictEvent.get("sType") == "outputBatch":
            listLines.extend(dictEvent["listLines"])
        elif dictEvent.get("sType") == "output":
            listLines.append(dictEvent.get("sLine", ""))
    return iExitCode, fCpu, listLines


@pytest.mark.falsification
def testACommandThatStartsWithABuiltinRunsInTheShellNotUnderTheTimeBinary(
    liveContainer,
):
    """``cd d && cmd``: ``cd`` is the shell's, not a program name.

    Measured in a container with GNU time: ``cannot run cd``, exit 127,
    and the rest of the command never ran.

    Kills: handing the command to ``/usr/bin/time`` as its argument
    vector again.
    """
    iExitCode, _, listLines = _ftRunThroughTheRunner(
        liveContainer, ["cd /tmp && pwd"])
    assert iExitCode == 0, listLines
    assert "/tmp" in listLines, listLines
    assert not any("cannot run" in sLine for sLine in listLines), listLines


@pytest.mark.parametrize("sCommand, sExpected", [
    ("export VAIBIFY_PROBE=7 && echo $VAIBIFY_PROBE", "7"),
    ("VAIBIFY_PROBE=9 sh -c 'echo $VAIBIFY_PROBE'", "9"),
    ("echo piped | tr a-z A-Z", "PIPED"),
    ("false || echo recovered", "recovered"),
])
def testOtherShellSyntaxReachesTheShellToo(liveContainer, sCommand, sExpected):
    iExitCode, _, listLines = _ftRunThroughTheRunner(
        liveContainer, [sCommand])
    assert iExitCode == 0, listLines
    assert sExpected in listLines, listLines
    assert not any("cannot run" in sLine for sLine in listLines), listLines


@pytest.mark.falsification
def testBothSidesOfAnAndChainRunAndTheSecondIsTimed(liveContainer):
    """``a && b``: both run, and the CPU reading covers ``b``.

    The old wrapper timed only ``a``, so a step that did its work after a
    leading ``cd`` reported no CPU at all.

    Kills: timing only the first command of a chain.
    """
    iExitCode, fCpu, listLines = _ftRunThroughTheRunner(
        liveContainer,
        ["echo first && python3 -c 'sum(range(25_000_000)); print(\"second\")'"])
    assert iExitCode == 0, listLines
    assert "first" in listLines and "second" in listLines, listLines
    assert fCpu is not None and fCpu > 0.2, fCpu
    assert not any(
        sLine.startswith("__VAIBIFY_CPU__") for sLine in listLines)


@pytest.mark.falsification
def testTheCommandsOwnExitStatusComesThrough(liveContainer):
    """``exit 3`` is 3: the wrapper neither swallows nor forces success.

    Kills: repairing the wrapper by forcing a zero exit.
    """
    iExitCode, _, _ = _ftRunThroughTheRunner(liveContainer, ["exit 3"])
    assert iExitCode == 3
    iExitCode, _, listLines = _ftRunThroughTheRunner(
        liveContainer, ["echo before && false && echo after"])
    assert iExitCode == 1
    assert "before" in listLines and "after" not in listLines


def testTheEnvironmentPrefixReachesTheTimedShell(liveContainer):
    """The run marker and the determinism exports are inherited."""
    iExitCode, _, listLines = _ftRunThroughTheRunner(
        liveContainer, ["echo $SOURCE_DATE_EPOCH-$VAIBIFY_RUN_ID"],
        sEnvPrefix=(
            "export SOURCE_DATE_EPOCH=1700000000 && "
            f"export VAIBIFY_RUN_ID={S_RUN_ID} && "))
    assert iExitCode == 0, listLines
    assert f"1700000000-{S_RUN_ID}" in listLines, listLines


def _fiCountProcessesNamed(container, sProcessName):
    """Count LIVE processes whose kernel name is ``sProcessName``.

    A killed process whose parent is gone stays a zombie (its name, no
    command line) until pid 1 reaps it, and the container's pid 1 is a
    plain ``sleep`` that never does, so a zombie is not counted.
    """
    iExit, sCount = _fsRun(
        container,
        "for sPid in /proc/[0-9]*; do "
        f"[ \"$(cat $sPid/comm 2>/dev/null)\" = {sProcessName} ] && "
        "[ -n \"$(tr -d '\\0' < $sPid/cmdline 2>/dev/null)\" ] && "
        "echo $sPid; done | wc -l")
    return int(sCount)


def testCancellationKillsEveryProcessTheWrapperStarted(liveContainer):
    """The stop sweep matches the run marker, which the child inherits.

    The wrapper adds a process level (time, then a shell, then the
    command); the sweep must still reach all of them.
    """
    from vaibify.gui.routes import pipelineRoutes
    container, connection = liveContainer
    sWrapped = pipelineRunner._fsWrapWithTime("sleep 300 && echo unreached")
    sMarked = f"export VAIBIFY_RUN_ID={S_RUN_ID} && {sWrapped}"
    iSleepersBefore = _fiCountProcessesNamed(container, "sleep")
    container.exec_run(["/bin/bash", "-c", sMarked], user=S_USER, detach=True)
    fDeadline = time.monotonic() + 15
    while time.monotonic() < fDeadline:
        if _fiCountProcessesNamed(container, "sleep") > iSleepersBefore and (
            _fiCountProcessesNamed(container, "time")
        ):
            break
        time.sleep(0.2)
    assert _fiCountProcessesNamed(container, "sleep") == iSleepersBefore + 1, (
        "the wrapped command never started")
    assert _fiCountProcessesNamed(container, "time") == 1
    iKilled = pipelineRoutes._fiKillRunProcesses(
        connection, container.id, S_RUN_ID)
    assert iKilled >= 3, iKilled
    time.sleep(0.5)
    assert _fiCountProcessesNamed(container, "sleep") == iSleepersBefore
    assert _fiCountProcessesNamed(container, "time") == 0


def _fsRun(container, sCommand):
    iExit, baOutput = container.exec_run(["sh", "-c", sCommand], user=S_USER)
    return iExit, baOutput.decode("utf-8", errors="replace").strip()


@pytest.mark.falsification
def testACommandWithNoTrailingNewlineKeepsItsTextAndItsCpuReading(
    liveContainer,
):
    """GNU time writes its marker right after the last output.

    The command's final line has no newline, so the marker is glued to
    it. Matching only a line that STARTS with the marker lost the CPU
    reading and printed the marker in the run log.

    Kills: recognising the marker only at the start of a line.
    """
    iExitCode, fCpu, listLines = _ftRunThroughTheRunner(
        liveContainer,
        ["python3 -c \"import sys; sum(range(25_000_000)); "
         "sys.stdout.write('no newline here')\""])
    assert iExitCode == 0, listLines
    assert listLines[-1] == "no newline here", listLines
    assert not any("__VAIBIFY_CPU__" in sLine for sLine in listLines)
    assert fCpu is not None and fCpu > 0.2, fCpu
