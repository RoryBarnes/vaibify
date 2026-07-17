"""The remote-data overwrite gate at the pipeline dispatch choke point.

A run action covering a step whose ``listRemoteData`` files already
exist on disk must be answered with ``runRefused``
``sReason=remoteDataOverwrite`` and never started, unless the request
carries ``bConfirmRemoteOverwrite``. The gate sits after the
busy-refusal in ``fnPipelineMessageLoop``, so every lane — browser
buttons and the agent CLI — meets it identically. First-ever pulls
(nothing on disk) never prompt.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.websockets import WebSocketDisconnect

from vaibify.gui import pipelineServer
from vaibify.gui.pipelineServer import (
    _fdictRemoteOverwriteRefusal,
    fnPipelineMessageLoop,
)


_S_REPO_ROOT = "/workspace/repo"


class _FakeDocker:
    """Answers the existence heredoc with a fixed set of present paths."""

    def __init__(self, listExistingAbsPaths):
        self.setExisting = set(listExistingAbsPaths)
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        listPresent = [
            sPath for sPath in self.setExisting if sPath in sCommand
        ]
        return (0, "\n".join(listPresent))


def _fdictPullStep(sName="Pull", sPath="data/archive_pull.fits"):
    return {
        "sName": sName,
        "sDirectory": sName.lower(),
        "bRunEnabled": True,
        "listRemoteData": [{"sPath": sPath, "sSourceUrl": "u"}],
    }


def _fdictPlainStep(sName="Plain"):
    return {
        "sName": sName,
        "sDirectory": sName.lower(),
        "bRunEnabled": True,
    }


def _fdictWorkflow(listSteps):
    return {
        "sProjectRepoPath": _S_REPO_ROOT,
        "listSteps": listSteps,
    }


def _fdictRefusalFor(sAction, dictRequest, dictWorkflow, listExisting):
    connectionDocker = _FakeDocker(listExisting)
    return asyncio.run(_fdictRemoteOverwriteRefusal(
        sAction, dictRequest, connectionDocker, "cid", dictWorkflow,
    ))


_S_ABS_PULL = _S_REPO_ROOT + "/data/archive_pull.fits"


def test_run_selected_over_existing_pull_is_refused_with_details():
    dictWorkflow = _fdictWorkflow([_fdictPlainStep(), _fdictPullStep()])
    dictRefusal = _fdictRefusalFor(
        "runSelected", {"listStepIndices": [1], "sRunMode": "full"},
        dictWorkflow, [_S_ABS_PULL],
    )
    assert dictRefusal is not None
    assert dictRefusal["sType"] == "runRefused"
    assert dictRefusal["sReason"] == "remoteDataOverwrite"
    assert dictRefusal["listStepIndices"] == [1]
    assert dictRefusal["listRemoteOverwritePaths"] == [
        "data/archive_pull.fits",
    ]
    assert dictRefusal["dictOriginalRequest"] == {
        "listStepIndices": [1], "sRunMode": "full",
    }
    assert "--confirm-remote-overwrite" in dictRefusal["sMessage"]


def test_confirm_flag_lets_the_run_proceed():
    dictWorkflow = _fdictWorkflow([_fdictPullStep()])
    dictRefusal = _fdictRefusalFor(
        "runSelected",
        {"listStepIndices": [0], "bConfirmRemoteOverwrite": True},
        dictWorkflow, [_S_ABS_PULL],
    )
    assert dictRefusal is None


def test_first_pull_with_nothing_on_disk_never_prompts():
    dictWorkflow = _fdictWorkflow([_fdictPullStep()])
    dictRefusal = _fdictRefusalFor(
        "runSelected", {"listStepIndices": [0]}, dictWorkflow, [],
    )
    assert dictRefusal is None


def test_gated_step_outside_the_selected_set_does_not_refuse():
    dictWorkflow = _fdictWorkflow([_fdictPlainStep(), _fdictPullStep()])
    dictRefusal = _fdictRefusalFor(
        "runSelected", {"listStepIndices": [0]},
        dictWorkflow, [_S_ABS_PULL],
    )
    assert dictRefusal is None


def test_run_from_after_the_pull_step_does_not_refuse():
    dictWorkflow = _fdictWorkflow([_fdictPullStep(), _fdictPlainStep()])
    assert _fdictRefusalFor(
        "runFrom", {"iStartStep": 2}, dictWorkflow, [_S_ABS_PULL],
    ) is None
    dictRefusal = _fdictRefusalFor(
        "runFrom", {"iStartStep": 1}, dictWorkflow, [_S_ABS_PULL],
    )
    assert dictRefusal is not None
    assert dictRefusal["dictOriginalRequest"] == {"iStartStep": 1}


def test_run_all_skips_run_disabled_pull_steps():
    dictPull = _fdictPullStep()
    dictPull["bRunEnabled"] = False
    dictWorkflow = _fdictWorkflow([_fdictPlainStep(), dictPull])
    assert _fdictRefusalFor(
        "runAll", {}, dictWorkflow, [_S_ABS_PULL],
    ) is None


def test_run_from_skips_run_disabled_pull_steps():
    """runFrom honors bRunEnabled like the runner — a disabled pull
    step in range must not trigger the gate (it will not run)."""
    dictPull = _fdictPullStep()
    dictPull["bRunEnabled"] = False
    dictWorkflow = _fdictWorkflow([_fdictPlainStep(), dictPull])
    assert _fdictRefusalFor(
        "runFrom", {"iStartStep": 1}, dictWorkflow, [_S_ABS_PULL],
    ) is None


@pytest.mark.parametrize("sAction", ["verify", "runAllTests"])
def test_non_run_actions_are_never_gated(sAction):
    dictWorkflow = _fdictWorkflow([_fdictPullStep()])
    assert _fdictRefusalFor(
        sAction, {}, dictWorkflow, [_S_ABS_PULL],
    ) is None


def test_workflow_without_repo_root_is_not_gated():
    dictWorkflow = _fdictWorkflow([_fdictPullStep()])
    dictWorkflow["sProjectRepoPath"] = ""
    assert _fdictRefusalFor(
        "runAll", {}, dictWorkflow, [_S_ABS_PULL],
    ) is None


# ---------------------------------------------------------------------------
# Loop-level: the gate refuses at dispatch, on the real message loop
# ---------------------------------------------------------------------------


class _FakeGateWebSocket:
    """Feed scripted client messages; record everything sent back."""

    def __init__(self, listMessages):
        self._listMessages = list(listMessages)
        self.listSent = []

    async def receive_text(self):
        if self._listMessages:
            return self._listMessages.pop(0)
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, dictEvent):
        self.listSent.append(dictEvent)


@pytest.mark.asyncio
@pytest.mark.falsification
async def test_gated_run_is_refused_and_never_dispatched():
    """The refusal happens BEFORE dispatch: no task starts, no state
    mutates, and the confirmed retry in the same connection runs.

    Kills: moving the gate after create_task, or gating on the
    browser side only (the agent lane would bypass it).
    """
    listDispatched = []

    async def fnRecordingDispatch(sAction, *args, **kwargs):
        listDispatched.append(sAction)

    dictWorkflow = _fdictWorkflow([_fdictPullStep()])
    websocketFake = _FakeGateWebSocket([
        json.dumps({"sAction": "runSelected", "listStepIndices": [0]}),
        json.dumps({
            "sAction": "runSelected", "listStepIndices": [0],
            "bConfirmRemoteOverwrite": True,
        }),
    ])
    dictPipelineTasks = {}
    with patch.object(
        pipelineServer, "fnDispatchAction", fnRecordingDispatch,
    ):
        with pytest.raises(WebSocketDisconnect):
            await fnPipelineMessageLoop(
                websocketFake, _FakeDocker([_S_ABS_PULL]), "cid-gate",
                dictWorkflow, {}, "/workspace",
                dictPipelineTasks=dictPipelineTasks,
            )
        for _ in range(3):
            await asyncio.sleep(0)
    listRefusals = [
        dictEvent for dictEvent in websocketFake.listSent
        if dictEvent.get("sType") == "runRefused"
    ]
    assert len(listRefusals) == 1
    assert listRefusals[0]["sReason"] == "remoteDataOverwrite"
    assert listDispatched == ["runSelected"], (
        "exactly the confirmed retry may dispatch; the gated attempt "
        "must never start"
    )


# ---------------------------------------------------------------------------
# Agent CLI flag parsing
# ---------------------------------------------------------------------------


def _fmodLoadVaibifyDo():
    sPath = str(
        Path(__file__).resolve().parent.parent
        / "docker" / "vaibifyDo.py"
    )
    spec = importlib.util.spec_from_file_location("vaibifyDoGate", sPath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_bare_confirm_flag_sets_the_body_boolean():
    modCli = _fmodLoadVaibifyDo()
    listPositional, dictBody = modCli.ftParsePositionalArgs(
        ["A09", "--confirm-remote-overwrite"],
    )
    assert listPositional == ["A09"]
    assert dictBody == {"bConfirmRemoteOverwrite": True}


def test_cli_confirm_flag_with_value_also_works():
    modCli = _fmodLoadVaibifyDo()
    _listPositional, dictBody = modCli.ftParsePositionalArgs(
        ["--confirm-remote-overwrite=true"],
    )
    assert dictBody == {"bConfirmRemoteOverwrite": True}


def test_cli_unknown_bare_flags_stay_positional():
    modCli = _fmodLoadVaibifyDo()
    listPositional, dictBody = modCli.ftParsePositionalArgs(
        ["--branch"],
    )
    assert listPositional == ["--branch"]
    assert dictBody == {}
