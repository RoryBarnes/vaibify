"""The agent wrappers against the REAL Codex and Gemini CLIs, no credentials.

Source: ``vaibify/config/terminalRcfile.py`` and
``vaibify/gui/agentChatResume.py``.

Runs in the nightly ``agent_cli_live`` lane on the same throwaway image
the Prompt Record live test builds (``node:22-slim`` with both CLIs).
Neither CLI is given a credential: each writes its session file before
it reaches the network, which is all these checks need.

- where a CLI's own ``--help`` offers ``--session-id``, the wrapper's
  probe says so and the session file appears under the id it injects;
- where it does not (Codex), the wrapper records the chat with NO id;
- the resume commands vaibify composes are accepted by each CLI's
  argument parser (they fail later, for want of a session or a login,
  never as an unknown argument).
"""

import json
import shlex

import pytest

from tests.testPromptRecordAgentsLive import tAgentContainer  # noqa: F401
from vaibify.config.terminalRcfile import S_PROMPT_RESET_RCFILE
from vaibify.gui import agentChatResume

pytestmark = [pytest.mark.docker_live, pytest.mark.agent_cli_live]

S_WORK = "/home/researcher/projectRepo"
S_RECORD = "/tmp/vaibify-terminal-" + "cd" * 16 + ".jsonl"
S_UUID = "1c37be47-a36d-45b8-9688-6f971b50c106"
T_UNKNOWN_ARGUMENT = ("Unknown argument", "unexpected argument",
                      "Unknown arguments", "error: unrecognized")


def _ftRunInBashWithTheRcfile(container, sCommands):
    """Run commands in an interactive bash that read the real rcfile."""
    sScript = (
        "mkdir -p /tmp/rcdir " + S_WORK + " && printf '%s' "
        + shlex.quote(S_PROMPT_RESET_RCFILE) + " > /tmp/rcdir/rc && cd "
        + S_WORK + " && timeout 120 bash --rcfile /tmp/rcdir/rc -i -c "
        + shlex.quote(sCommands) + " </dev/null; true")
    iExitCode, baOutput = container.exec_run(
        ["bash", "-c", sScript], user="researcher",
        environment={"VAIBIFY_TERMINAL_LIFECYCLE": S_RECORD,
                     "GEMINI_CLI_TRUST_WORKSPACE": "true",
                     "GEMINI_API_KEY": "not-a-credential"})
    return iExitCode, baOutput.decode("utf-8", "replace")


def _flistRecordedEvents(container):
    iExitCode, baRecord = container.exec_run(
        ["sh", "-c", "cat " + S_RECORD + " 2>/dev/null; rm -f " + S_RECORD],
        user="researcher")
    return [json.loads(s) for s in baRecord.decode().splitlines() if s.strip()]


def testGeminisProbeOffersAnIdAndItsSessionFileCarriesIt(tAgentContainer):
    container, _ = tAgentContainer
    _, sOutput = _ftRunInBashWithTheRcfile(container, (
        'sHelp=$(fsVaibifyProbeHelp gemini); '
        'fbVaibifyHelpOffers "$sHelp" --session-id && echo OFFERS-ID; '
        'sId=$(fsVaibifyNewSessionId); echo "ID[$sId]"; '
        'command gemini --session-id "$sId" -p hi; true'))
    assert "OFFERS-ID" in sOutput, sOutput
    sInjected = sOutput.split("ID[", 1)[1].split("]", 1)[0]
    assert len(sInjected) == 36
    iExitCode, baFound = container.exec_run(
        ["sh", "-c", "ls ~/.gemini/tmp/*/chats/ 2>/dev/null"],
        user="researcher")
    assert sInjected[:8] in baFound.decode(), (
        "no Gemini session file carries the injected id")


def testCodexIsRecordedWithNoInjectedId(tAgentContainer):
    container, _ = tAgentContainer
    _, sOutput = _ftRunInBashWithTheRcfile(container, "codex hello; true")
    listEvents = _flistRecordedEvents(container)
    listStarts = [d for d in listEvents if d.get("sEvent") == "start"]
    assert listStarts, sOutput
    assert listStarts[0]["sProvider"] == "codex"
    assert listStarts[0]["sSessionId"] == ""
    assert listStarts[0]["listArguments"] == ["hello"]
    assert "--session-id" not in sOutput


@pytest.mark.parametrize("sProvider", ["gemini", "codex"])
def testTheComposedResumeArgvIsAccepted(tAgentContainer, sProvider):
    container, _ = tAgentContainer
    dictPlan = agentChatResume.fdictBuildResumePlan({
        "sProvider": sProvider, "sSessionId": S_UUID, "sCwd": S_WORK,
        "listArguments": []}, {}, "")
    for sCommand in (dictPlan["sResumeCommand"], dictPlan["sStandardCommand"]):
        _, sOutput = _ftRunInBashWithTheRcfile(
            container, sCommand + " </dev/null; true")
        assert not any(s in sOutput for s in T_UNKNOWN_ARGUMENT), (
            sCommand, sOutput)
    _flistRecordedEvents(container)
