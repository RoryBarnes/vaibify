"""Running one step of a Docker setup plan, through the right channel.

The runner runs only an action of the plan's FIRST step, named by the
caller; it wraps an administrator script in a prompt the operating
system owns; one setup holds the machine at a time; and a failure says
what failed. The scripts that matter most are executed for real here:
the rpm key check runs against stub ``curl``, ``gpg`` and ``rpm``
programs on PATH, so a fingerprint mismatch is shown to stop the
install rather than read off the script text.
"""

import os
import stat
import subprocess

import pytest

from vaibify.install import runtimeSetupPlan as planModule
from vaibify.install import runtimeSetupRunner as runnerModule
from vaibify.install.runtimeSetupRunner import (
    RuntimeSetupError,
    fcontextHoldSetupLock,
    fdictFindRunnableAction,
    flistBuildArgv,
    fnRunAction,
)


@pytest.fixture(autouse=True)
def fixtureIsolateTheLock(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runnerModule, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))


def fdictPlanOf(*listSteps):
    return {"bReady": False, "listSteps": list(listSteps)}


def fdictRunStep(sStepName, listActions, sKind="run"):
    return {"sStepName": sStepName, "sTitle": sStepName.title(),
            "sKind": sKind, "sExplanation": "", "listActions": listActions,
            "listCommandsToShow": []}


def fdictActionRunning(sActionName, *listArgvs):
    return {"sActionName": sActionName, "sLabel": sActionName,
            "listInvocations": [
                {"sRunAs": "user", "listArgv": list(listArgv)}
                for listArgv in listArgvs]}


S_ROOT_SCRIPT = 'usermod -aG docker "$1"\n'
DICT_ROOT_INVOCATION = {"sRunAs": "administrator", "sScript": S_ROOT_SCRIPT,
                        "listScriptArguments": ["researcher alpha"]}


# ---------------------------------------------------------------------------
# Which action may run
# ---------------------------------------------------------------------------

@pytest.mark.falsification
def testOnlyAnActionOfTheFirstStepCanRun():
    """Kills: running any action the plan mentions, in any order."""
    dictPlan = fdictPlanOf(
        fdictRunStep("install-runtime", [fdictActionRunning("install", ["true"])]),
        fdictRunStep("start-colima", [fdictActionRunning("start", ["true"])]))
    assert fdictFindRunnableAction(dictPlan, "install")["sActionName"] == (
        "install")
    with pytest.raises(RuntimeSetupError, match="not an action of the next"):
        fdictFindRunnableAction(dictPlan, "start")
    with pytest.raises(RuntimeSetupError):
        fdictFindRunnableAction(dictPlan, "rm -rf /")


def testAStepOnlyTheResearcherCanDoIsNeverRun():
    dictPlan = fdictPlanOf(fdictRunStep("log-out", [], sKind="needs-you"))
    with pytest.raises(RuntimeSetupError, match="only you can do"):
        fdictFindRunnableAction(dictPlan, "anything")


def testAReadyMachineHasNothingToRun():
    with pytest.raises(RuntimeSetupError, match="already set up"):
        fdictFindRunnableAction({"bReady": True, "listSteps": []}, "x")


# ---------------------------------------------------------------------------
# The privilege channels
# ---------------------------------------------------------------------------

def testAUserInvocationRunsExactlyItsArgv():
    assert flistBuildArgv({"sRunAs": "user", "listArgv": ["colima", "start"]},
                          "none") == ["colima", "start"]


def testPkexecAndSudoPassTheArgumentsOutsideTheScript():
    assert flistBuildArgv(DICT_ROOT_INVOCATION, "pkexec") == [
        "pkexec", "/bin/sh", "-c", S_ROOT_SCRIPT, "sh", "researcher alpha"]
    assert flistBuildArgv(DICT_ROOT_INVOCATION, "passwordless-sudo")[:2] == [
        "sudo", "-n"]
    assert flistBuildArgv(DICT_ROOT_INVOCATION, "terminal-sudo")[0] == "sudo"


def testTheMacDialogRunsTheSameShellCommandQuoted():
    listArgv = flistBuildArgv(DICT_ROOT_INVOCATION, "macos-dialog")
    assert listArgv[:2] == ["osascript", "-e"]
    assert listArgv[2].startswith('do shell script "/bin/sh -c ')
    assert listArgv[2].endswith('" with administrator privileges')
    assert '\\"$1\\"' in listArgv[2]


def testAnAdministratorStepWithNoChannelRefusesToRun():
    with pytest.raises(RuntimeSetupError, match="no prompt"):
        flistBuildArgv(DICT_ROOT_INVOCATION, "none")


# ---------------------------------------------------------------------------
# Running, the lock, and failures
# ---------------------------------------------------------------------------

def testAnActionStreamsEachLineAndWritesWhatRanFirst():
    listLines = []
    dictPlan = fdictPlanOf(fdictRunStep("echo", [fdictActionRunning(
        "echo", ["sh", "-c", "echo first; echo second"])]))
    fnRunAction(dictPlan, "echo", "none", listLines.append)
    assert listLines[0].startswith("$ sh -c")
    assert listLines[1:] == ["first", "second"]


def testAFailedCommandSaysWhichAndStopsTheAction(tmp_path):
    pathMarker = tmp_path / "secondRan"
    dictPlan = fdictPlanOf(fdictRunStep("two", [fdictActionRunning(
        "two", ["sh", "-c", "echo broken; exit 3"],
        ["touch", str(pathMarker)])]))
    with pytest.raises(RuntimeSetupError, match="exited with code 3"):
        fnRunAction(dictPlan, "two", "none", lambda sLine: None)
    assert not pathMarker.exists()


def testACancelledMacDialogIsNamedAsCancelled():
    sExplanation = runnerModule._fsExplainFailure(
        ["osascript", "-e", "x"], 1,
        ["execution error: User canceled. (-128)"])
    assert sExplanation.startswith("The administrator prompt was cancelled")


@pytest.mark.falsification
def testASecondSetupIsRefusedBeforeItTouchesTheFirstOnesRecord():
    """Kills: a refused setup that resets the running one's progress first."""
    listCalls = []
    dictPlan = fdictPlanOf(fdictRunStep("echo", [fdictActionRunning(
        "echo", ["true"])]))
    with fcontextHoldSetupLock():
        with pytest.raises(RuntimeSetupError, match="already running"):
            fnRunAction(dictPlan, "echo", "none", lambda sLine: None,
                        fnLockHeld=lambda: listCalls.append("reset"))
    assert listCalls == []
    fnRunAction(dictPlan, "echo", "none", lambda sLine: None,
                fnLockHeld=lambda: listCalls.append("reset"))
    assert listCalls == ["reset"]


# ---------------------------------------------------------------------------
# The rpm key check, executed
# ---------------------------------------------------------------------------

def _fnWriteStub(pathDirectory, sName, sBody):
    pathStub = pathDirectory / sName
    pathStub.write_text("#!/bin/sh\n" + sBody)
    pathStub.chmod(pathStub.stat().st_mode | stat.S_IEXEC)


def _ftRunKeyCheckWithFingerprint(tmp_path, sFingerprint):
    """Run the real key-check fragment against stub tools; return (code, log)."""
    pathBin = tmp_path / "stubBin"
    pathBin.mkdir()
    pathLog = tmp_path / "rpmCalls.log"
    _fnWriteStub(pathBin, "curl", 'for a; do :; done; : > "$a"\n')
    _fnWriteStub(pathBin, "gpg",
                 f'echo "pub:-:4096:1:x:x::-:::scESC:"\n'
                 f'echo "fpr:::::::::{sFingerprint}:"\n')
    _fnWriteStub(pathBin, "rpm", f'echo "rpm $*" >> "{pathLog}"\n')
    sScript = "set -e\n" + planModule._S_RPM_KEY_SCRIPT.format(
        sRepository="fedora",
        sFingerprint=planModule._S_DOCKER_RPM_KEY_FINGERPRINT)
    processResult = subprocess.run(
        ["/bin/sh", "-c", sScript], capture_output=True, text=True,
        env={"PATH": f"{pathBin}:/usr/bin:/bin", "TMPDIR": str(tmp_path)})
    sLog = pathLog.read_text() if pathLog.exists() else ""
    return processResult.returncode, sLog


@pytest.mark.falsification
def testAKeyWhoseFingerprintDiffersIsNeverImported(tmp_path):
    """Kills: importing Docker's key whatever its fingerprint."""
    iCode, sLog = _ftRunKeyCheckWithFingerprint(
        tmp_path, "0" * 40)
    assert iCode != 0
    assert sLog == ""


def testTheKeyDockerPublishesIsImported(tmp_path):
    iCode, sLog = _ftRunKeyCheckWithFingerprint(
        tmp_path, planModule._S_DOCKER_RPM_KEY_FINGERPRINT)
    assert iCode == 0
    assert sLog.startswith("rpm --import ")
