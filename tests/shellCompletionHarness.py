"""Drive the shipped completion scripts in the real shells.

A completion script is shell text, and no Python stub can say whether a
shell reads it the way its author meant. Everything here therefore runs
the script in the actual shell, with one thing replaced: the ``vaibify``
command, which is a stub that records exactly how it was called and
answers ``complete-path`` from a file the test wrote. That separates the
two claims the scripts make -- WHERE a path is wanted, and HOW a name
reaches the command line -- from whether a container is reachable.

A shell that is not installed makes its tests SKIP on a developer's
machine and FAIL on a run that demanded it, because a test that skips
reports success for having tested nothing. The pull-request workflows
install the shells and set ``VAIBIFY_REQUIRE_SHELLS``, the same contract
as ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` and ``VAIBIFY_REQUIRE_BROWSER``.
"""

import os
import pty
import select
import shutil
import signal
import subprocess
import time

import pytest

from vaibify.install.shellSetup import _fsCompletionsDirectory


S_REQUIRE_SHELLS_ENV = "VAIBIFY_REQUIRE_SHELLS"

# macOS still ships bash 3.2 as /bin/bash, and that is the bash a
# researcher's completion actually runs under; whatever `bash` resolves
# to on PATH is a different, usually newer, program.
S_SYSTEM_BASH = "/bin/bash"

S_CALL_SEPARATOR = "\x01"
S_COMPLETE_PATH_VERB = "complete-path"


def fsRequireShell(sShellName):
    """Return the path of a shell, or skip -- unless the run demanded it."""
    sExecutable = (
        S_SYSTEM_BASH if sShellName == "bash" else shutil.which(sShellName)
    )
    if sExecutable and os.path.exists(sExecutable):
        return sExecutable
    if os.environ.get(S_REQUIRE_SHELLS_ENV):
        pytest.fail(
            f"{sShellName} is not installed, but {S_REQUIRE_SHELLS_ENV} is "
            "set: this run was required to test the completion script in "
            f"it. Install {sShellName} on this runner; do not unset the "
            "variable."
        )
    pytest.skip(f"{sShellName} is not installed")


def fsCompletionScript(sShellName):
    """Return the path of the completion script shipped for a shell."""
    return os.path.join(_fsCompletionsDirectory(), f"vaibify.{sShellName}")


def fdictBuildStubWorld(sDirectory, listCandidates=()):
    """Write a stub ``vaibify`` and return the environment that finds it.

    The stub appends each call's arguments, one per line and closed by a
    separator, to a log, and prints the candidates for ``complete-path``.
    """
    sBinDirectory = os.path.join(sDirectory, "bin")
    os.makedirs(sBinDirectory, exist_ok=True)
    sStub = os.path.join(sBinDirectory, "vaibify")
    with open(sStub, "w", encoding="utf-8") as fileStub:
        fileStub.write(
            "#!/bin/sh\n"
            "{ for sArgument in \"$@\"; do printf '%s\\n' \"$sArgument\"; "
            "done; printf '\\001\\n'; } >> \"$STUB_LOG\"\n"
            f"if [ \"$1\" = {S_COMPLETE_PATH_VERB} ]; then "
            "cat \"$STUB_CANDIDATES\"; fi\n"
        )
    os.chmod(sStub, 0o755)
    sCandidates = os.path.join(sDirectory, "candidates.txt")
    with open(sCandidates, "w", encoding="utf-8") as fileCandidates:
        fileCandidates.write("".join(f"{sName}\n" for sName in listCandidates))
    sHome = os.path.join(sDirectory, "home")
    os.makedirs(sHome, exist_ok=True)
    return {
        "PATH": sBinDirectory + os.pathsep + os.environ["PATH"],
        "HOME": sHome,
        "STUB_LOG": os.path.join(sDirectory, "calls.log"),
        "STUB_CANDIDATES": sCandidates,
        "TERM": "xterm",
    }


def flistReadStubCalls(dictWorld):
    """Return every call made to the stub, each as its list of arguments."""
    try:
        with open(dictWorld["STUB_LOG"], "r", encoding="utf-8") as fileLog:
            sLog = fileLog.read()
    except OSError:
        return []
    listCalls = []
    for sRecord in sLog.split(S_CALL_SEPARATOR + "\n"):
        if sRecord:
            listCalls.append(sRecord[:-1].split("\n"))
    return listCalls


def flistReadHelperCalls(dictWorld):
    """Return only the ``complete-path`` calls, in order."""
    return [
        listCall for listCall in flistReadStubCalls(dictWorld)
        if listCall[:1] == [S_COMPLETE_PATH_VERB]
    ]


def fsRunShellProgram(sExecutable, listArguments, sProgram, dictWorld):
    """Run a shell program with the stub on PATH; return its stdout."""
    tResult = subprocess.run(
        [sExecutable, *listArguments, sProgram], capture_output=True,
        text=True, env=dictWorld, timeout=60,
    )
    return tResult.stdout


def _fbWaitUntil(fbCondition, fTimeoutSeconds, iFileDescriptor):
    """Poll a condition while draining the terminal; return whether it held.

    The terminal is READ while waiting because a shell may block, before
    it runs a command, until the output it has written is consumed (zsh
    does, on every platform with a real tty line discipline). Sleeping
    instead made a command appear to take the whole timeout.
    """
    fDeadline = time.time() + fTimeoutSeconds
    while time.time() < fDeadline:
        if fbCondition():
            return True
        _fnReadUntilQuiet(iFileDescriptor, 0.05, 0.1)
    return fbCondition()


def _fnReadUntilQuiet(iFileDescriptor, fQuietSeconds, fTimeoutSeconds):
    """Read terminal output until nothing has arrived for a quiet spell.

    A reply is written for the one terminal query a shell may block on:
    fish 4 asks for the terminal's primary device attributes at start-up
    and a pty answers nothing.
    """
    fDeadline = time.time() + fTimeoutSeconds
    fLastOutput = time.time()
    while (
        time.time() < fDeadline
        and time.time() - fLastOutput < fQuietSeconds
    ):
        listReady, listWritable, listErroring = select.select(
            [iFileDescriptor], [], [], 0.05,
        )
        if not listReady:
            continue
        try:
            baChunk = os.read(iFileDescriptor, 65536)
        except OSError:
            return
        if not baChunk:
            return
        fLastOutput = time.time()
        if b"\x1b[0c" in baChunk:
            os.write(iFileDescriptor, b"\x1b[?62;c")


def _fnTerminateAndReap(iPid, iFileDescriptor):
    """Kill the shell and collect it, draining its terminal while it dies.

    A session leader cannot finish exiting while its terminal still has
    unread output (macOS waits for the master to drain it), so a plain
    ``waitpid`` after the kill hangs forever.
    """
    os.kill(iPid, signal.SIGKILL)
    fDeadline = time.time() + 20.0
    while time.time() < fDeadline:
        iReaped, _iStatus = os.waitpid(iPid, os.WNOHANG)
        if iReaped:
            break
        _fnReadUntilQuiet(iFileDescriptor, 0.05, 0.2)
    os.close(iFileDescriptor)


def _flistBuildInteractiveCommand(sShellName, sExecutable, dictWorld):
    """Return the argv that starts one shell interactively, script loaded."""
    sScript = fsCompletionScript(sShellName)
    sHome = dictWorld["HOME"]
    if sShellName == "fish":
        return [
            sExecutable, "--no-config", "-i", "-C", f"source {sScript}",
        ]
    if sShellName == "zsh":
        with open(os.path.join(sHome, ".zshrc"), "w") as fileRc:
            fileRc.write(
                "PROMPT='> '\n"
                "autoload -Uz compinit && compinit -u\n"
                f"source {sScript}\n"
            )
        return [sExecutable, "-i"]
    sRcFile = os.path.join(sHome, ".bashrc")
    with open(sRcFile, "w") as fileRc:
        fileRc.write(f"PS1='> '\nsource {sScript}\n")
    return [sExecutable, "--noprofile", "--rcfile", sRcFile, "-i"]


def flistTabCompleteAndRun(
    sShellName, sExecutable, sTyped, dictWorld, sWorkDirectory,
    fTimeoutSeconds=20.0,
):
    """Type a line, press TAB, then Enter, in a real interactive shell.

    Returns the stub's calls. The final call is the command the shell
    ACTUALLY executed after inserting the completed name, so its
    arguments are exactly how the shell parsed what completion inserted,
    which is the only witness that cannot be satisfied by looking at the
    candidate list. Waits observe the stub's log rather than the clock.
    """
    listCommand = _flistBuildInteractiveCommand(
        sShellName, sExecutable, dictWorld,
    )
    iPid, iFileDescriptor = pty.fork()
    if iPid == 0:
        try:
            os.chdir(sWorkDirectory)
            os.execve(listCommand[0], listCommand, dictWorld)
        finally:
            os._exit(127)
    try:
        _fnReadUntilQuiet(iFileDescriptor, 1.0, 15.0)
        os.write(iFileDescriptor, sTyped.encode())
        _fnReadUntilQuiet(iFileDescriptor, 0.3, 5.0)
        os.write(iFileDescriptor, b"\t")
        _fbWaitUntil(
            lambda: bool(flistReadHelperCalls(dictWorld)), fTimeoutSeconds,
            iFileDescriptor,
        )
        _fnReadUntilQuiet(iFileDescriptor, 0.7, 10.0)
        os.write(iFileDescriptor, b"\r")
        _fbWaitUntil(
            lambda: len(flistReadStubCalls(dictWorld))
            > len(flistReadHelperCalls(dictWorld)),
            fTimeoutSeconds, iFileDescriptor,
        )
    finally:
        _fnTerminateAndReap(iPid, iFileDescriptor)
    return flistReadStubCalls(dictWorld)
