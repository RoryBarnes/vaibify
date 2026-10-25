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


# ---------------------------------------------------------------------------
# The deadline, and the lock after the hub dies
# ---------------------------------------------------------------------------

def _fbProcessIsAlive(iPid):
    try:
        os.kill(iPid, 0)
    except ProcessLookupError:
        return False
    return True


def _fiWaitForPid(pathPid, fTimeout=10.0):
    import time
    fDeadline = time.monotonic() + fTimeout
    while time.monotonic() < fDeadline:
        if pathPid.exists() and pathPid.read_text().strip():
            return int(pathPid.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"{pathPid} was never written")


@pytest.mark.falsification
def testASilentStepPastItsDeadlineIsStoppedWithItsChildren(
        tmp_path, monkeypatch):
    """A step that prints nothing cannot outwait the deadline.

    Kills: reading the step's output before the timed wait, which
    blocks until the step ends however long that takes.
    """
    import time
    monkeypatch.setattr(runnerModule, "_F_INVOCATION_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(runnerModule, "_F_TERMINATE_GRACE_SECONDS", 2.0)
    pathPid = tmp_path / "sleeper.pid"
    fStart = time.monotonic()
    with pytest.raises(RuntimeSetupError, match="was stopped"):
        runnerModule._fnRunInvocation(
            ["sh", "-c", f'sleep 6 & echo $! > "{pathPid}"; wait'],
            lambda sLine: None, False)
    assert time.monotonic() - fStart < 4.0
    assert not _fbProcessIsAlive(_fiWaitForPid(pathPid))


@pytest.mark.falsification
def testADescendantThatIgnoresTerminateIsStillStopped(tmp_path, monkeypatch):
    """The leader exits on SIGTERM; its child ignores it and works on.

    Kills: reporting "was stopped" once the group's leader has exited,
    while a descendant goes on to finish the work.
    """
    import time
    monkeypatch.setattr(runnerModule, "_F_INVOCATION_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(runnerModule, "_F_TERMINATE_GRACE_SECONDS", 0.5)
    pathPid = tmp_path / "stubborn.pid"
    pathWork = tmp_path / "workDone"
    with pytest.raises(RuntimeSetupError, match="was stopped"):
        runnerModule._fnRunInvocation(
            ["sh", "-c", f'( trap "" TERM; sleep 2; touch "{pathWork}" ) & '
                         f'echo $! > "{pathPid}"; wait'],
            lambda sLine: None, False)
    assert not _fbProcessIsAlive(_fiWaitForPid(pathPid))
    time.sleep(2.5)
    assert not pathWork.exists()


S_HUB_PROGRAM = """
import sys
from vaibify.install import runtimeSetupRunner as runnerModule
runnerModule._S_LOCK_DIRECTORY = sys.argv[1]
dictPlan = {"bReady": False, "listSteps": [{
    "sStepName": "long", "sTitle": "Long", "sKind": "run",
    "sExplanation": "", "listCommandsToShow": [],
    "listActions": [{"sActionName": "long", "sLabel": "long",
                     "listInvocations": [{"sRunAs": "user", "listArgv": [
                         "sh", "-c", 'echo $$ > "' + sys.argv[2] + '"; sleep 20']}]}],
}]}
runnerModule.fnRunAction(dictPlan, "long", "none", print)
"""


def _fnWaitForTheLockToBeFree(fTimeout=10.0):
    import time
    fDeadline = time.monotonic() + fTimeout
    while True:
        try:
            with fcontextHoldSetupLock():
                return
        except RuntimeSetupError:
            if time.monotonic() > fDeadline:
                raise
            time.sleep(0.1)


S_HUB_RUNNING_A_DESCRIPTOR_CLOSER = """
import sys
from vaibify.install import runtimeSetupRunner as runnerModule
runnerModule._S_LOCK_DIRECTORY = sys.argv[1]
sStep = (
    "import os, sys, time; os.closerange(3, 4096); "
    "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(20)")
dictPlan = {"bReady": False, "listSteps": [{
    "sStepName": "root-like", "sTitle": "Root-like", "sKind": "run",
    "sExplanation": "", "listCommandsToShow": [],
    "listActions": [{"sActionName": "root-like", "sLabel": "root-like",
                     "listInvocations": [{"sRunAs": "user", "listArgv": [
                         sys.executable, "-c", sStep, sys.argv[2]]}]}],
}]}
runnerModule.fnRunAction(dictPlan, "root-like", "none", print)
"""


@pytest.mark.falsification
def testAStepThatClosesItsDescriptorsStillHoldsTheLock(tmp_path):
    """sudo and pkexec close what they inherit; the lock must not go with it.

    The step here closes every inherited descriptor, as sudo does, then
    keeps running after its hub is killed. Kills: carrying the lock in
    the step's own process rather than a supervisor that waits for it.
    """
    import signal
    import sys
    pathPid = tmp_path / "rootLike.pid"
    processHub = subprocess.Popen(
        [sys.executable, "-c", S_HUB_RUNNING_A_DESCRIPTOR_CLOSER,
         runnerModule._S_LOCK_DIRECTORY, str(pathPid)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    iStepPid = _fiWaitForPid(pathPid, fTimeout=20.0)
    processHub.kill()
    processHub.wait(timeout=10)
    try:
        assert _fbProcessIsAlive(iStepPid)
        with pytest.raises(RuntimeSetupError, match="already running"):
            with fcontextHoldSetupLock():
                pass
    finally:
        os.kill(iStepPid, signal.SIGKILL)
    _fnWaitForTheLockToBeFree()


@pytest.mark.falsification
def testTheLockOutlivesAHubKilledMidStep(tmp_path):
    """A restarted hub must not start a second setup beside a running one.

    Kills: a step process that does not carry the setup lock.
    """
    import signal
    import sys
    pathPid = tmp_path / "step.pid"
    processHub = subprocess.Popen(
        [sys.executable, "-c", S_HUB_PROGRAM, runnerModule._S_LOCK_DIRECTORY,
         str(pathPid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    iStepPid = _fiWaitForPid(pathPid, fTimeout=20.0)
    processHub.kill()
    processHub.wait(timeout=10)
    try:
        assert _fbProcessIsAlive(iStepPid)
        with pytest.raises(RuntimeSetupError, match="already running"):
            with fcontextHoldSetupLock():
                pass
    finally:
        os.killpg(iStepPid, signal.SIGKILL)
    _fnWaitForTheLockToBeFree()
