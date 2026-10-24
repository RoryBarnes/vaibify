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
by an exclusive ``flock`` the kernel releases if the process dies.
"""

import contextlib
import fcntl
import os
import shlex
import subprocess

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
    "fnRunAction",
]


S_PRIVILEGE_TERMINAL_SUDO = "terminal-sudo"

_F_INVOCATION_TIMEOUT_SECONDS = 3600.0
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


def _fnRunInvocation(listArgv, fnEmitLine, bInteractive):
    """Run one argv to completion, streaming each output line."""
    try:
        processChild = subprocess.Popen(
            listArgv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=None if bInteractive else subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as error:
        raise RuntimeSetupError(
            f"Could not run '{listArgv[0]}': {error.strerror}.") from None
    listTail = []
    for sLine in processChild.stdout:
        fnEmitLine(sLine.rstrip("\n"))
        listTail = (listTail + [sLine.rstrip("\n")])[-20:]
    try:
        iExitCode = processChild.wait(timeout=_F_INVOCATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        processChild.kill()
        raise RuntimeSetupError(
            f"'{listArgv[0]}' ran for an hour without finishing and was "
            "stopped.") from None
    if iExitCode != 0:
        raise RuntimeSetupError(_fsExplainFailure(listArgv, iExitCode, listTail))


def fnRunAction(dictPlan, sActionName, sPrivilegeChannel, fnEmitLine,
                bInteractive=False, fnLockHeld=None):
    """Run one action of the plan's first step under the machine-wide lock.

    ``fnLockHeld`` runs once the lock is held and before anything else:
    a caller that resets a shared progress record does it there, so a
    second setup refused by the lock cannot wipe the first one's log.
    """
    dictAction = fdictFindRunnableAction(dictPlan, sActionName)
    with fcontextHoldSetupLock():
        if fnLockHeld is not None:
            fnLockHeld()
        for dictInvocation in dictAction["listInvocations"]:
            listArgv = flistBuildArgv(dictInvocation, sPrivilegeChannel)
            _fnEmitWhatRuns(dictInvocation, listArgv, fnEmitLine)
            _fnRunInvocation(listArgv, fnEmitLine, bInteractive)


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
