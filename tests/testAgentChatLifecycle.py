"""A terminal records which agent chat it holds, and the hub reads it back honestly.

Source: ``vaibify/config/terminalRcfile.py`` (the wrappers) and
``vaibify/gui/agentChatLifecycle.py`` (reading the record).

The wrapper tests run the REAL rcfile in the REAL ``/bin/bash`` of this
machine (bash 3.2 on macOS, 5.x on Linux) against fake CLIs that print
their argv, so what is asserted is what bash actually passed. The
parser tests feed hand-written record lines whose expected verdicts
follow from the plan's rules (plan sections 5.3 and 5.4), not from the
code under test.
"""

import json
import os
import shutil
import stat
import subprocess

import pytest

from vaibify.config.terminalRcfile import (
    S_CLAUDE_SWITCH_HOOK_SETTINGS, S_PROMPT_RESET_RCFILE,
)
from vaibify.gui import agentChatLifecycle

S_WORKSPACE = "/workspace"
S_UUID_A = "1c37be47-a36d-45b8-9688-6f971b50c106"
S_UUID_B = "96969a67-6545-426c-b128-aeacfda91223"
S_FAKE_CLI = """#!/bin/sh
if [ "$1" = "--help" ]; then
    echo "  --session-id <uuid>   --settings <file-or-json>"
    exit 0
fi
for sArgument in "$@"; do printf '[%s]' "$sArgument"; done
echo
exit 3
"""


def _fsLine(dictEvent):
    return json.dumps(dictEvent)


def _fbaRecord(*listEvents):
    return ("\n".join(_fsLine(d) for d in listEvents) + "\n").encode()


def _fdictStart(sProvider="claude", sSessionId=S_UUID_A, bSwitchHook=True,
                sCwd="/workspace/repo"):
    return {"sEvent": "start", "sProvider": sProvider,
            "sSessionId": sSessionId, "bSwitchHook": bSwitchHook,
            "iStartedEpoch": 1794080000, "sCwd": sCwd,
            "listArguments": ["--dangerously-skip-permissions"]}


# ---------------------------------------------------------------------
# Reading the record
# ---------------------------------------------------------------------


def testAChatStillRunningAtLeaveIsTheTabsChat():
    dictChat = agentChatLifecycle.fdictCurrentChatFromRecord(
        _fbaRecord(_fdictStart()), S_WORKSPACE)
    assert dictChat["sSessionId"] == S_UUID_A
    assert dictChat["sIdentityClass"] == "tracked"
    assert dictChat["sCwd"] == "/workspace/repo"


@pytest.mark.falsification
def testAnEndedChatIsNotRevived():
    """A chat whose CLI returned to bash restores as a plain shell.

    Kills: ignoring the exit line, which resumed a conversation the
    researcher had already left.
    """
    assert agentChatLifecycle.fdictCurrentChatFromRecord(
        _fbaRecord(_fdictStart(), {"sEvent": "exit", "iStatus": 0}),
        S_WORKSPACE) is None


def testTheLastUnclosedStartWins():
    dictChat = agentChatLifecycle.fdictCurrentChatFromRecord(_fbaRecord(
        _fdictStart(sSessionId=S_UUID_A), {"sEvent": "exit", "iStatus": 0},
        _fdictStart(sProvider="gemini", sSessionId=S_UUID_B,
                    bSwitchHook=False)), S_WORKSPACE)
    assert dictChat["sProvider"] == "gemini"
    assert dictChat["sSessionId"] == S_UUID_B
    assert dictChat["sIdentityClass"] == "launched"


@pytest.mark.falsification
def testASwitchInsideClaudeReplacesTheLaunchId():
    """The SessionStart payload names the conversation now on screen.

    Kills: ignoring the switch signal, which resumed the conversation
    the tab STARTED with after the researcher had moved to another.
    """
    dictChat = agentChatLifecycle.fdictCurrentChatFromRecord(_fbaRecord(
        _fdictStart(sSessionId=S_UUID_A),
        {"hook_event_name": "SessionStart", "source": "resume",
         "session_id": S_UUID_B}), S_WORKSPACE)
    assert dictChat["sSessionId"] == S_UUID_B
    assert dictChat["sIdentityClass"] == "tracked"


def testASwitchLineIsIgnoredWithoutTheHook():
    dictChat = agentChatLifecycle.fdictCurrentChatFromRecord(_fbaRecord(
        _fdictStart(sSessionId=S_UUID_A, bSwitchHook=False),
        {"hook_event_name": "SessionStart", "session_id": S_UUID_B}),
        S_WORKSPACE)
    assert dictChat["sSessionId"] == S_UUID_A
    assert dictChat["sIdentityClass"] == "launched"


def testAChatWithNoIdIsInferred():
    dictChat = agentChatLifecycle.fdictCurrentChatFromRecord(_fbaRecord(
        _fdictStart(sProvider="codex", sSessionId="", bSwitchHook=False)),
        S_WORKSPACE)
    assert dictChat["sIdentityClass"] == "inferred"


@pytest.mark.falsification
def testAPlantedNonUuidIdIsNeverOffered():
    """A session id that is not a UUID makes the whole line unusable.

    Kills: dropping the session-id format check, which let a planted
    record put shell text where a resume command expects an id.
    """
    for sPlanted in ("$(touch /tmp/owned)", "1c37be47; rm -rf ~",
                     S_UUID_A.upper()):
        assert agentChatLifecycle.fdictCurrentChatFromRecord(
            _fbaRecord(_fdictStart(sSessionId=sPlanted)), S_WORKSPACE) is None
        assert agentChatLifecycle.fdictCurrentChatFromRecord(_fbaRecord(
            _fdictStart(sSessionId=S_UUID_A),
            {"hook_event_name": "SessionStart", "session_id": sPlanted}),
            S_WORKSPACE)["sSessionId"] == S_UUID_A


@pytest.mark.falsification
def testAWorkingDirectoryOutsideTheWorkspaceIsRefused():
    """Kills: dropping the working-directory confinement."""
    for sCwd in ("/etc", "/workspace/../etc", "relative/path",
                 "/workspace2/repo"):
        assert agentChatLifecycle.fdictCurrentChatFromRecord(
            _fbaRecord(_fdictStart(sCwd=sCwd)), S_WORKSPACE) is None, sCwd


def testAnUnknownProviderAndMalformedLinesAreIgnored():
    baRecord = b"not json\n" + _fbaRecord(_fdictStart(sProvider="sh"))
    assert agentChatLifecycle.fdictCurrentChatFromRecord(
        baRecord, S_WORKSPACE) is None
    assert agentChatLifecycle.fdictCurrentChatFromRecord(
        None, S_WORKSPACE) is None


def testATerminalIdMustBeThirtyTwoHexDigits():
    assert agentChatLifecycle.fbIsTerminalId("0" * 32)
    for sBad in ("0" * 31, "../../etc/passwd" + "0" * 16, "G" * 32, ""):
        assert not agentChatLifecycle.fbIsTerminalId(sBad)
        assert agentChatLifecycle.fdictLifecycleEnvironment(
            "a1b2c3", sBad) == {}


def testAContainerTerminalRecordsUnderTheContainerScratchRoot():
    dictOverlay = agentChatLifecycle.fdictLifecycleEnvironment(
        "a1b2c3", "ab" * 16)
    assert dictOverlay == {"VAIBIFY_TERMINAL_LIFECYCLE":
                           "/tmp/vaibify-terminal-" + "ab" * 16 + ".jsonl"}


# ---------------------------------------------------------------------
# The wrappers, in real bash
# ---------------------------------------------------------------------


@pytest.fixture()
def tBash(tmp_path):
    """Return a runner: commands in an interactive bash with the rcfile."""
    sBin = tmp_path / "bin"
    sBin.mkdir()
    for sName in ("claude", "gemini", "codex"):
        (sBin / sName).write_text(S_FAKE_CLI)
        (sBin / sName).chmod(0o755)
    sRecord = str(tmp_path / "terminal.jsonl")
    sWork = tmp_path / "work dir \"quoted\""
    sWork.mkdir()

    def ftRun(sCommands):
        sRcDirectory = tmp_path / "rcdir"
        sRcDirectory.mkdir(exist_ok=True)
        (sRcDirectory / "rc").write_text(S_PROMPT_RESET_RCFILE)
        processBash = subprocess.run(
            ["/bin/bash", "--rcfile", str(sRcDirectory / "rc"), "-i", "-c",
             sCommands],
            cwd=str(sWork), capture_output=True, text=True, timeout=60,
            env={"HOME": str(tmp_path), "PATH": f"{sBin}:/usr/bin:/bin",
                 "VAIBIFY_TERMINAL_LIFECYCLE": sRecord},
        )
        listEvents = []
        if os.path.exists(sRecord):
            with open(sRecord, encoding="utf-8") as fileRecord:
                listEvents = [json.loads(s) for s in fileRecord if s.strip()]
        return processBash, listEvents, sRecord, sRcDirectory

    return ftRun


def testClaudeGetsASessionIdAndTheSwitchHook(tBash):
    processBash, listEvents, _, _ = tBash('claude "fix it"; echo "rc=$?"')
    sInjected = listEvents[0]["sSessionId"]
    assert agentChatLifecycle.fbIsSessionId(sInjected)
    assert ("[--session-id][" + sInjected + "][--settings]["
            + S_CLAUDE_SWITCH_HOOK_SETTINGS + "][fix it]") in processBash.stdout
    dictHook = json.loads(S_CLAUDE_SWITCH_HOOK_SETTINGS)
    assert dictHook["hooks"]["SessionStart"][0]["hooks"][0]["command"] == (
        'cat >> "$VAIBIFY_TERMINAL_LIFECYCLE"; '
        'echo >> "$VAIBIFY_TERMINAL_LIFECYCLE"')
    assert "rc=3" in processBash.stdout
    assert listEvents[0]["bSwitchHook"] is True
    assert listEvents[0]["listArguments"] == ["fix it"]
    assert listEvents[1] == {"sEvent": "exit", "iStatus": 3}


def testTheRecordIsValidJsonForAnAwkwardDirectoryAndPrivate(tBash):
    _, listEvents, sRecord, _ = tBash("claude")
    assert listEvents[0]["sCwd"].endswith('work dir "quoted"')
    assert stat.S_IMODE(os.stat(sRecord).st_mode) == 0o600


def testAResumeIsNeverGivenASecondId(tBash):
    processBash, listEvents, _, _ = tBash(
        "claude --resume " + S_UUID_B.upper())
    assert "--session-id" not in processBash.stdout
    assert listEvents[0]["sSessionId"] == S_UUID_B


def testTheResearchersOwnSettingsAreNeverOverridden(tBash):
    processBash, listEvents, _, _ = tBash("claude --settings /tmp/mine.json")
    assert processBash.stdout.count("[--settings]") == 1
    assert listEvents[0]["bSwitchHook"] is False


def testNonChatInvocationsRecordNothing(tBash):
    _, listEvents, _, _ = tBash(
        "claude --version; claude -p hi; gemini --help; codex exec x")
    assert listEvents == []


def testGeminiGetsAnIdAndCodexRecordsItsResume(tBash):
    _, listEvents, _, _ = tBash(
        "gemini; codex resume " + S_UUID_A + "; codex")
    assert agentChatLifecycle.fbIsSessionId(listEvents[0]["sSessionId"])
    assert listEvents[0]["bSwitchHook"] is False
    assert listEvents[2]["sProvider"] == "codex"
    assert listEvents[2]["sSessionId"] == S_UUID_A
    assert listEvents[4]["sSessionId"] == ""


def testWithoutARecordPathTheWrapperOnlyRunsTheCli(tBash, tmp_path):
    processBash = subprocess.run(
        ["/bin/bash", "-c", S_PROMPT_RESET_RCFILE.split("\n", 2)[2]
         + "\nclaude hi"],
        capture_output=True, text=True, timeout=60,
        env={"HOME": str(tmp_path),
             "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"})
    assert "[hi]" in processBash.stdout


def testAnAliasDefinedFirstDoesNotBreakTheRcfile(tBash, tmp_path):
    """A ~/.bashrc alias of the same name must not stop the wrappers."""
    (tmp_path / ".bashrc").write_text(
        "alias claude='claude --dangerously-skip-permissions'\n")
    processBash, listEvents, _, _ = tBash("claude")
    assert "syntax error" not in processBash.stderr
    assert listEvents[0]["listArguments"] == [
        "--dangerously-skip-permissions"]


@pytest.mark.falsification
def testTheExtendedRcfileStillDeletesItselfAndResetsLast(tBash):
    """The extension kept the rcfile's existing contract.

    Kills: placing the agent wrappers after the reset hook, which made
    the hook no longer the last thing the rcfile installs.
    """
    processBash, _, _, sRcDirectory = tBash(
        'false; fnVaibifyResetInputModes; echo "status=$?"')
    assert not os.path.exists(sRcDirectory), "the rcfile must delete itself"
    assert "status=1" in processBash.stdout, "the hook must keep the status"
    assert S_PROMPT_RESET_RCFILE.rstrip().endswith(
        "fnVaibifyResetInputModes\"\nfi")
    assert S_PROMPT_RESET_RCFILE.index("function claude") < (
        S_PROMPT_RESET_RCFILE.index("fnVaibifyResetInputModes() {"))


def testTheRcfileUsesNothingBashThreeLacks():
    for sConstruct in ("declare -A", ",,}", "^^}", "mapfile", "readarray",
                       "local -n", "${var@"):
        assert sConstruct not in S_PROMPT_RESET_RCFILE, sConstruct


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def testTheRcfileParsesInThisMachinesBash():
    processBash = subprocess.run(
        ["/bin/bash", "-n"], input=S_PROMPT_RESET_RCFILE,
        capture_output=True, text=True, timeout=30)
    assert processBash.returncode == 0, processBash.stderr
