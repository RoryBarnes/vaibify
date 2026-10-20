"""Four defects a template project met on its way through the host CLI.

Found by driving ``tests/testCliTemplateProjectTowardLevelThreeLive.py``
against a real hub and container; each is pinned here without a daemon
so the ordinary lanes guard it too.

* A host ``vaibify do run-*`` sent its run frame without echoing the
  workflow the socket announced, and the hub refused every one.
* The release after a run, or after a durable task such as Verify
  Level 3, landed while the hub still held the work and was refused, so
  the next command was refused as held by another session.
* An action given no fields sent no body, and a route whose body fields
  are all optional answered 422.
* Preflight demanded the working directory of a step that runs nothing
  (an AI Declaration), which stopped every run, the Level 3 rerun
  included.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from vaibify.cli import hubSession


S_FINGERPRINT = "f" * 64
S_WORKFLOW_PATH = "/workspace/projectAlpha/.vaibify/projects/project.json"


class _ScriptedSocket:
    """A pipeline socket that announces a workflow, then completes."""

    def __init__(self):
        self.listSent = []
        self.listFrames = [
            json.dumps({"sType": "workflowBound",
                        "sWorkflowPath": S_WORKFLOW_PATH,
                        "sExactSourceFingerprint": S_FINGERPRINT}),
            json.dumps({"sType": "completed", "iExitCode": 0}),
        ]

    def recv(self, timeout=None):
        return self.listFrames.pop(0)

    def send(self, sMessage):
        self.listSent.append(json.loads(sMessage))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fdictSession():
    return {"sBaseUrl": "http://127.0.0.1:1", "sCredential": "c",
            "sContainerName": "projectAlpha", "sContainerId": "cid-77",
            "sLeaseId": "lease-1"}


@pytest.mark.falsification
def testARunFrameEchoesTheWorkflowTheSocketAnnounced():
    """Kills: sending the run frame without the acknowledgment."""
    socketScripted = _ScriptedSocket()
    with patch("websockets.sync.client.connect",
               return_value=socketScripted):
        iExit = hubSession.fiStreamPipelineAction(
            _fdictSession(), {"sAction": "runAll"}, True, 5.0)
    assert iExit == 0
    assert socketScripted.listSent == [{
        "sAction": "runAll",
        "sAcknowledgedSourceFingerprint": S_FINGERPRINT,
        "sAcknowledgedWorkflowPath": S_WORKFLOW_PATH,
    }]


def _flistReleaseAnswers(listAnswers, fWaitSeconds):
    listCalls = []

    def ftSend(*args, **kwargs):
        listCalls.append(args)
        return listAnswers.pop(0)

    with patch.object(hubSession, "ftSendHttpRequest", ftSend), \
            patch.object(hubSession.time, "sleep"):
        hubSession.fnReleaseContainer(_fdictSession(), fWaitSeconds)
    return listCalls


S_BUSY = (409, {"sMessage": "Container 'projectAlpha' has a run still in "
                            "progress.", "bReleased": False})
S_RELEASED = (200, {"bReleased": True})


@pytest.mark.falsification
def testTheReleaseWaitsForTheWorkTheCommandStarted(capsys):
    """Kills: giving up on the first busy refusal."""
    listCalls = _flistReleaseAnswers([S_BUSY, S_BUSY, S_RELEASED], 30.0)
    assert len(listCalls) == 3
    sErr = capsys.readouterr().err
    assert "Waiting for the work this command started" in sErr
    assert "still holds" not in sErr


def testAReleaseThatMayNotWaitSaysWhyTheHubKeptTheContainer(capsys):
    listCalls = _flistReleaseAnswers([S_BUSY], 0.0)
    assert len(listCalls) == 1
    sErr = capsys.readouterr().err
    assert "still holds 'projectAlpha'" in sErr
    assert "has a run still in progress" in sErr


@pytest.mark.falsification
def testAnActionWithNoFieldsStillSendsABody():
    """Kills: dropping the body when every field was left at its default."""
    responseFake = MagicMock(status_code=200)
    responseFake.json.return_value = {}
    with patch("requests.request", return_value=responseFake) as mockSend:
        hubSession.ftSendHttpRequest(
            "http://127.0.0.1:1", "c", "POST", "/api/x", {}, 5.0)
        hubSession.ftSendHttpRequest(
            "http://127.0.0.1:1", "c", "GET", "/api/x", {}, 5.0)
    dictPost, dictGet = (dictCall.kwargs for dictCall in mockSend.call_args_list)
    assert dictPost["json"] == {}
    assert "json" not in dictGet and "params" not in dictGet


@pytest.mark.falsification
def testPreflightAsksNothingOfAStepThatRunsNothing():
    """Kills: validating the directory of a step with no commands."""
    from vaibify.gui import pipelineRunner
    from vaibify.gui.pipelineValidator import fbStepExecutesAnything
    dictDeclaration = {
        "sName": "AI Declaration", "sDirectory": "AIDeclaration",
        "bRunEnabled": True, "bInteractive": True,
        "saDataCommands": [], "saPlotCommands": [],
        "dictTests": {"dictQualitative": {"saCommands": []},
                      "listUserTests": []},
    }
    assert fbStepExecutesAnything(dictDeclaration) is False
    assert fbStepExecutesAnything(
        dict(dictDeclaration, saDataCommands=["python3 a.py"])) is True
    connectionDocker = MagicMock()
    listErrors = pipelineRunner._flistValidateStepsBlocking(
        connectionDocker, "cid-77",
        {"listSteps": [dict(dictDeclaration, sStepId="ai-declaration")]},
        {}, 1, None)
    assert listErrors == []
    connectionDocker.ftResultExecuteCommand.assert_not_called()
