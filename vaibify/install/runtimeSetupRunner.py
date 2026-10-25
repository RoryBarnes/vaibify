"""Run one action of a Docker setup plan, streaming what it prints.

The runner executes only what ``runtimeSetupPlan`` built: the caller
names an action, the runner checks that it belongs to the plan's FIRST
step, and runs that action's invocations in order. A user invocation
runs as the researcher; an administrator invocation runs as root
through the privilege channel the facts named, each of which shows a
prompt the operating system owns:

* macOS -- ``osascript`` ``do shell script ... with administrator
  privileges``, macOS's own dialog;
* Linux desktop -- ``pkexec``, polkit's own dialog;
* passwordless ``sudo -n`` where the machine is configured for it;
* the CLI in a terminal -- ``sudo``, which asks in that terminal.

One setup runs at a time on a machine, CLI and dashboard alike, held
by an exclusive ``flock``. Outside an interactive terminal the action
runs under a small supervisor (``runtimeSetupSupervisor``) started in
its own session: it inherits the lock's descriptor and waits for every
command of the step, so the lock lasts exactly as long as the step,
whether or not the hub that asked for it survives. That matters most
for an administrator step: ``sudo`` and ``pkexec`` close the
descriptors they inherit, so the command itself could never carry the
lock, but the supervisor that waits for it does. In a terminal the
command line is the supervisor: ``sudo`` needs that terminal to ask
for a password, and stopping it there is the researcher's own choice.

Each invocation has a deadline enforced while its output is read on a
thread, so a step that prints nothing cannot outwait it. A step that
misses it is stopped as a process group, and is reported stopped only
once the GROUP is empty: a descendant that ignores the terminate
signal keeps the group alive after its leader exits, and gets the kill
signal. A root process in that group is not ours to signal, and the
error says it may still be running.
"""

import contextlib
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

from vaibify.install.runtimeSetupFacts import (
    S_PRIVILEGE_MACOS_DIALOG,
    S_PRIVILEGE_PASSWORDLESS_SUDO,
    S_PRIVILEGE_PKEXEC,
)
from vaibify.install.runtimeSetupPlan import S_KIND_NEEDS_YOU


__all__ = [
    "S_PRIVILEGE_TERMINAL_SUDO",
    "RuntimeSetupError",
    "flistBuildArgv",
    "fdictFindRunnableAction",
    "fcontextHoldSetupLock",
    "S_SUPERVISOR_RESULT_PREFIX",
    "fnRunInvocations",
    "fnRunAction",
]


S_PRIVILEGE_TERMINAL_SUDO = "terminal-sudo"

_F_INVOCATION_TIMEOUT_SECONDS = 3600.0
_F_TERMINATE_GRACE_SECONDS = 10.0
_F_OUTPUT_DRAIN_SECONDS = 5.0
_F_SUPERVISOR_SLACK_SECONDS = 120.0
_S_SUPERVISOR_MODULE = "vaibify.install.runtimeSetupSupervisor"
S_SUPERVISOR_RESULT_PREFIX = "\x1evaibify-setup-result "
_S_LOCK_DIRECTORY = os.path.expanduser("~/.vaibify/locks")
_S_LOCK_NAME = "runtimeSetup.lock"
_I_PKEXEC_DISMISSED = 126
_I_PKEXEC_NOT_AUTHORIZED = 127
_S_APPLESCRIPT_CANCELLED = "(-128)"


class RuntimeSetupError(Exception):
    """A setup action that could not run, or ran and failed, with why."""


def _fsQuoteForAppleScript(sText):
    return sText.replace("\\", "\\\\").replace('"', '\\"')


def _fsShellCommandForScript(sScript, listScriptArguments):
    listParts = ["/bin/sh", "-c", shlex.quote(sScript)]
    if listScriptArguments:
        listParts.append("sh")
        listParts.extend(shlex.quote(s) for s in listScriptArguments)
    return " ".join(listParts)


def flistBuildArgv(dictInvocation, sPrivilegeChannel):
    """Return the argv one invocation runs as, through its channel."""
    if dictInvocation["sRunAs"] == "user":
        return list(dictInvocation["listArgv"])
    sScript = dictInvocation["sScript"]
    listArguments = list(dictInvocation["listScriptArguments"])
    listShell = ["/bin/sh", "-c", sScript]
    if listArguments:
        listShell += ["sh"] + listArguments
    if sPrivilegeChannel == S_PRIVILEGE_MACOS_DIALOG:
        sCommand = _fsShellCommandForScript(sScript, listArguments)
        return ["osascript", "-e", 'do shell script "'
                + _fsQuoteForAppleScript(sCommand)
                + '" with administrator privileges']
    if sPrivilegeChannel == S_PRIVILEGE_PKEXEC:
        return ["pkexec"] + listShell
    if sPrivilegeChannel == S_PRIVILEGE_PASSWORDLESS_SUDO:
        return ["sudo", "-n"] + listShell
    if sPrivilegeChannel == S_PRIVILEGE_TERMINAL_SUDO:
        return ["sudo"] + listShell
    raise RuntimeSetupError(
        "This step needs an administrator, and there is no prompt on this "
        "machine to ask with; run the commands it shows in a terminal.")


def fdictFindRunnableAction(dictPlan, sActionName):
    """Return the named action of the plan's first step, or raise."""
    listSteps = dictPlan.get("listSteps") or []
    if not listSteps:
        raise RuntimeSetupError("Docker is already set up; nothing to run.")
    dictStep = listSteps[0]
    if dictStep["sKind"] == S_KIND_NEEDS_YOU:
        raise RuntimeSetupError(
            f"'{dictStep['sTitle']}' is a step only you can do; follow its "
            "instructions, then press Continue.")
    for dictAction in dictStep["listActions"]:
        if dictAction["sActionName"] == sActionName:
            return dictAction
    raise RuntimeSetupError(
        f"'{sActionName}' is not an action of the next step, "
        f"'{dictStep['sTitle']}'. The machine may have changed; read the "
        "plan again.")


@contextlib.contextmanager
def fcontextHoldSetupLock():
    """Hold the machine-wide setup lock, or raise if another setup holds it."""
    from vaibify.config.pidFileRegistry import (
        fnEnsureDirectory, ffileOpenNoFollow,
    )
    fnEnsureDirectory(_S_LOCK_DIRECTORY)
    fileLock = ffileOpenNoFollow(os.path.join(_S_LOCK_DIRECTORY, _S_LOCK_NAME))
    try:
        try:
            fcntl.flock(fileLock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeSetupError(
                "Another Docker setup is already running on this machine "
                "(in another tab or a terminal). Wait for it to finish."
            ) from None
        yield fileLock
    finally:
        fileLock.close()


def _fsExplainFailure(listArgv, iExitCode, listTail):
    sOutput = "\n".join(listTail)
    if listArgv[0] == "osascript" and _S_APPLESCRIPT_CANCELLED in sOutput:
        return "The administrator prompt was cancelled, so nothing ran."
    if listArgv[0] == "pkexec" and iExitCode in (
            _I_PKEXEC_DISMISSED, _I_PKEXEC_NOT_AUTHORIZED):
        return ("The administrator prompt was dismissed or refused, so "
                "nothing ran.")
    return (f"'{shlex.join(listArgv[:3])}' exited with code {iExitCode}. "
            "Its last output is above.")


def _fnPumpOutput(fileOutput, fnEmitLine, listTail):
    """Emit each line the child prints, keeping the last twenty."""
    for sLine in fileOutput:
        sLine = sLine.rstrip("\n")
        fnEmitLine(sLine)
        listTail.append(sLine)
        del listTail[:-20]


def _fbProcessGroupIsGone(iGroup):
    """Return True once no process of the group is left to signal."""
    try:
        os.killpg(iGroup, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _fbWaitForGroupToEnd(processChild, iGroup, fSeconds):
    """Wait up to ``fSeconds`` for the whole group to end; reap the leader."""
    fDeadline = time.monotonic() + fSeconds
    while True:
        processChild.poll()
        if _fbProcessGroupIsGone(iGroup):
            return True
        if time.monotonic() >= fDeadline:
            return False
        time.sleep(0.05)


def _fbStopProcessGroup(processChild, bOwnGroup):
    """Stop the child and all it started; True only once all of it is gone.

    The leader exiting proves nothing about its descendants, so the
    group itself is watched until it is empty, and whatever is left
    after the terminate grace gets the kill signal.
    """
    if not bOwnGroup:
        processChild.kill()
        try:
            processChild.wait(timeout=_F_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return False
        return True
    iGroup = processChild.pid
    for iSignal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(iGroup, iSignal)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if _fbWaitForGroupToEnd(
                processChild, iGroup, _F_TERMINATE_GRACE_SECONDS):
            return True
    return False


def _fprocessStartInvocation(listArgv, bInteractive, iLockDescriptor):
    """Start one argv in its own process group, passing it the lock if given.

    An interactive run stays in the terminal's session, because a
    ``sudo`` that has left it has no terminal to ask for a password on.
    """
    try:
        return subprocess.Popen(
            listArgv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=None if bInteractive else subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=not bInteractive,
            pass_fds=() if iLockDescriptor is None else (iLockDescriptor,),
        )
    except OSError as error:
        raise RuntimeSetupError(
            f"Could not run '{listArgv[0]}': {error.strerror}.") from None


def _fnRunInvocation(listArgv, fnEmitLine, bInteractive):
    """Run one argv to completion or its deadline, streaming each line."""
    processChild = _fprocessStartInvocation(listArgv, bInteractive, None)
    listTail = []
    threadPump = threading.Thread(
        target=_fnPumpOutput, args=(processChild.stdout, fnEmitLine, listTail),
        daemon=True)
    threadPump.start()
    try:
        iExitCode = processChild.wait(timeout=_F_INVOCATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        bStopped = _fbStopProcessGroup(processChild, not bInteractive)
        raise RuntimeSetupError(
            f"'{listArgv[0]}' ran past its {_F_INVOCATION_TIMEOUT_SECONDS:g}-"
            "second limit and " + ("was stopped." if bStopped else
            "could not be stopped, because it runs as administrator; it "
            "may still be running.")) from None
    threadPump.join(timeout=_F_OUTPUT_DRAIN_SECONDS)
    if iExitCode != 0:
        raise RuntimeSetupError(_fsExplainFailure(listArgv, iExitCode, listTail))


def fnRunInvocations(listInvocations, sPrivilegeChannel, fnEmitLine,
                     bInteractive):
    """Run an action's invocations in order, here, in this process."""
    for dictInvocation in listInvocations:
        listArgv = flistBuildArgv(dictInvocation, sPrivilegeChannel)
        _fnEmitWhatRuns(dictInvocation, listArgv, fnEmitLine)
        _fnRunInvocation(listArgv, fnEmitLine, bInteractive)


def _fnRunUnderSupervisor(listInvocations, sPrivilegeChannel, fnEmitLine,
                          iLockDescriptor):
    """Run the invocations in a supervisor that holds the lock until done."""
    processSupervisor = _fprocessStartInvocation(
        [sys.executable, "-m", _S_SUPERVISOR_MODULE, sPrivilegeChannel,
         json.dumps(listInvocations)], False, iLockDescriptor)
    dictResult = {}

    def fnRelayLine(sLine):
        if sLine.startswith(S_SUPERVISOR_RESULT_PREFIX):
            dictResult.update(json.loads(
                sLine[len(S_SUPERVISOR_RESULT_PREFIX):]))
        else:
            fnEmitLine(sLine)

    threadRelay = threading.Thread(
        target=_fnPumpOutput, args=(processSupervisor.stdout, fnRelayLine, []),
        daemon=True)
    threadRelay.start()
    try:
        iExitCode = processSupervisor.wait(timeout=(
            _F_INVOCATION_TIMEOUT_SECONDS * len(listInvocations)
            + _F_SUPERVISOR_SLACK_SECONDS))
    except subprocess.TimeoutExpired:
        raise RuntimeSetupError(
            "The setup step did not finish in time and may still be "
            "running; vaibify will not start another until it ends.") from None
    threadRelay.join(timeout=_F_OUTPUT_DRAIN_SECONDS)
    if not dictResult:
        raise RuntimeSetupError(
            "The setup step ended without saying how it went (supervisor "
            f"exit code {iExitCode}).")
    if not dictResult.get("bSucceeded"):
        raise RuntimeSetupError(dictResult.get("sMessage") or "It failed.")


def fnRunAction(dictPlan, sActionName, sPrivilegeChannel, fnEmitLine,
                bInteractive=False, fnLockHeld=None):
    """Run one action of the plan's first step under the machine-wide lock.

    ``fnLockHeld`` runs once the lock is held and before anything else:
    a caller that resets a shared progress record does it there, so a
    second setup refused by the lock cannot wipe the first one's log.
    """
    dictAction = fdictFindRunnableAction(dictPlan, sActionName)
    with fcontextHoldSetupLock() as fileLock:
        if fnLockHeld is not None:
            fnLockHeld()
        if bInteractive:
            fnRunInvocations(dictAction["listInvocations"], sPrivilegeChannel,
                             fnEmitLine, True)
            return
        _fnRunUnderSupervisor(dictAction["listInvocations"],
                              sPrivilegeChannel, fnEmitLine, fileLock.fileno())


def _fnEmitWhatRuns(dictInvocation, listArgv, fnEmitLine):
    """Write the command about to run into the log, script lines and all."""
    if dictInvocation["sRunAs"] == "user":
        fnEmitLine("$ " + shlex.join(listArgv))
        return
    fnEmitLine("$ (as administrator) /bin/sh -c <<script")
    for sLine in dictInvocation["sScript"].rstrip("\n").splitlines():
        fnEmitLine("    " + sLine)
    if dictInvocation["listScriptArguments"]:
        fnEmitLine("  with arguments: "
                   + shlex.join(dictInvocation["listScriptArguments"]))
