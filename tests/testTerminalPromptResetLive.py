"""The terminal's prompt reset, run under a real pty in a real container.

The wrapper ``fsBuildGroupReportingCommand`` builds is executed exactly
as a container terminal runs it -- ``/bin/sh -c <wrapper>`` as the
unprivileged user -- under a pseudo-terminal, so the bash it execs is
interactive and reads the rcfile. Typed lines are written to the pty and
everything the shell prints is captured, byte for byte.

What is proved, each against a hook the researcher wrote in their own
``~/.bashrc``:

* the reset runs LAST after an array of hooks, and in the string form
  after a hook that ends in ``;`` with no syntax error;
* it hands on the exit status it was given, observed by a hook a tool
  appends at runtime to the STRING form, after it. (In the array form
  each element was observed to see the original status whatever the
  previous element returned, so only a later hook in the same string
  can see what the reset returns.)
* the rcfile and its directory are gone once the shell has started;
* a program that turns on mouse reporting and is SIGKILLed leaves the
  next prompt switching it off.

THROWAWAY containers only, from a public image, force-removed in
teardown. Live-daemon convention: skips with no daemon unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import os
import secrets

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui.terminalContainment import fsBuildGroupReportingCommand

pytestmark = pytest.mark.docker_live

S_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
S_RESET_MOUSE_ANY = b"\x1b[?1003l"
S_SET_MOUSE_ANY = b"\x1b[?1003h"

# Runs INSIDE the container: fork a pty, exec the wrapper, type the
# lines one at a time, and print everything the shell wrote as hex.
S_PTY_DRIVER = r"""
import os, pty, sys, time
sWrapper, sLines = sys.argv[1], sys.argv[2]
iPid, iMaster = pty.fork()
if iPid == 0:
    os.execv("/bin/sh", ["/bin/sh", "-c", sWrapper])
baOutput = b""
def fnDrain(fSeconds):
    global baOutput
    fEnd = time.time() + fSeconds
    while time.time() < fEnd:
        try:
            import select
            listReady, _, _ = select.select([iMaster], [], [], 0.1)
            if listReady:
                baOutput += os.read(iMaster, 65536)
        except OSError:
            return
fnDrain(1.5)
for sLine in sLines.split("\n"):
    os.write(iMaster, (sLine + "\n").encode())
    fnDrain(1.0)
os.write(iMaster, b"exit\n")
fnDrain(1.0)
sys.stdout.write(baOutput.hex())
"""

S_ARRAY_BASHRC = (
    "fnUserHook() { local i=$?; printf 'HOOK-user\\n'; return $i; }\n"
    "fnSecondHook() { local i=$?; printf 'HOOK-second\\n'; return $i; }\n"
    "fnLateHook() { printf 'LATE[%s]\\n' \"$?\"; }\n"
    "PROMPT_COMMAND=(fnUserHook fnSecondHook)\n"
)
S_STRING_BASHRC = (
    "fnUserHook() { local i=$?; printf 'HOOK-user\\n'; return $i; }\n"
    "fnLateHook() { printf 'LATE[%s]\\n' \"$?\"; }\n"
    "PROMPT_COMMAND='fnUserHook;'\n"
)


@pytest.fixture
def containerThrowaway():
    fnRequireDaemonReachable()
    import docker
    clientDocker = docker.from_env()
    container = clientDocker.containers.run(
        S_TEST_IMAGE, ["sleep", "600"], detach=True,
        name="vaibify-prompt-reset-live-" + secrets.token_hex(6),
        labels=fdictLabels({"vaibify.test": "prompt-reset-live"}))
    try:
        iExitCode, baOutput = container.exec_run(
            ["useradd", "-m", "-s", "/bin/bash", "researcher"])
        assert iExitCode == 0, baOutput
        yield container
    finally:
        try:
            container.remove(force=True)
        except Exception:  # noqa: BLE001 -- teardown is best-effort
            pass


def _fnWriteBashrc(container, sText):
    """Install the researcher's own ~/.bashrc, owned by the researcher."""
    import io
    import tarfile
    baText = sText.encode()
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w") as tarOut:
        infoEntry = tarfile.TarInfo(".bashrc")
        infoEntry.size = len(baText)
        infoEntry.uid = infoEntry.gid = 1000
        infoEntry.mode = 0o644
        tarOut.addfile(infoEntry, io.BytesIO(baText))
    assert container.put_archive("/home/researcher", bufferArchive.getvalue())


def _fbaRunTerminal(container, listLines):
    sWrapper = fsBuildGroupReportingCommand(
        "/bin/bash", "/tmp/.vaibifyTerminalGroup.livetest")
    iExitCode, baOutput = container.exec_run(
        ["python3", "-c", S_PTY_DRIVER, sWrapper, "\n".join(listLines)],
        user="researcher", workdir="/home/researcher")
    assert iExitCode == 0, baOutput
    return bytes.fromhex(baOutput.decode().strip())


def _fbaAfter(baOutput, baMarker):
    iIndex = baOutput.rfind(baMarker)
    assert iIndex >= 0, (baMarker, baOutput)
    return baOutput[iIndex + len(baMarker):]


@pytest.mark.falsification
def testTheResetRunsLastAfterTheResearchersArrayHooks(containerThrowaway):
    """Kills: appending the hook as a scalar to an array PROMPT_COMMAND.

    A scalar assignment to an array changes only element 0, so the reset
    would run between the researcher's first and second hooks.
    """
    _fnWriteBashrc(containerThrowaway, S_ARRAY_BASHRC)
    baOutput = _fbaRunTerminal(containerThrowaway, ["false"])
    baPrompt = _fbaAfter(baOutput, b"false")
    iUser = baPrompt.index(b"HOOK-user")
    iSecond = baPrompt.index(b"HOOK-second")
    iReset = baPrompt.index(S_RESET_MOUSE_ANY)
    assert iUser < iSecond < iReset, baPrompt


@pytest.mark.falsification
def testTheResetHandsOnTheStatusItWasGiven(containerThrowaway):
    """Kills: a reset hook that does not return the status it received."""
    _fnWriteBashrc(containerThrowaway, S_STRING_BASHRC)
    baOutput = _fbaRunTerminal(
        containerThrowaway,
        ['PROMPT_COMMAND="$PROMPT_COMMAND; fnLateHook"', "false"])
    baPrompt = _fbaAfter(baOutput, b"false")
    assert b"LATE[1]" in baPrompt, baPrompt


def testAStringHookEndingInASemicolonJoinsCleanly(containerThrowaway):
    _fnWriteBashrc(containerThrowaway, S_STRING_BASHRC)
    baOutput = _fbaRunTerminal(containerThrowaway, ["true"])
    assert b"syntax error" not in baOutput
    baPrompt = _fbaAfter(baOutput, b"true")
    assert baPrompt.index(b"HOOK-user") < baPrompt.index(S_RESET_MOUSE_ANY)


@pytest.mark.falsification
def testTheRcfileAndItsDirectoryAreGoneOnceTheShellStarts(containerThrowaway):
    """Kills: dropping the rcfile's self-deletion."""
    _fnWriteBashrc(containerThrowaway, S_ARRAY_BASHRC)
    baOutput = _fbaRunTerminal(
        containerThrowaway,
        ["ls -d /tmp/tmp.* 2>/dev/null | wc -l | sed 's/^/LEFT=/'"])
    assert b"LEFT=0" in baOutput, baOutput


def testAKilledMouseProgramLeavesTheNextPromptReset(containerThrowaway):
    _fnWriteBashrc(containerThrowaway, "")
    baOutput = _fbaRunTerminal(
        containerThrowaway,
        ["sh -c 'printf \"\\033[?1003h\"; kill -9 $$'"])
    iSet = baOutput.rfind(S_SET_MOUSE_ANY)
    assert iSet >= 0, baOutput
    assert S_RESET_MOUSE_ANY in baOutput[iSet:], baOutput[iSet:]
