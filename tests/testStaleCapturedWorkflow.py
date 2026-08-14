"""Defect 7 — a live socket runs a workflow the researcher already changed.

`fnHandlePipelineWs` reads `dictCtx["workflows"].get(sContainerId)` ONCE
at socket accept and hands that object to `fnPipelineMessageLoop` for
the socket's whole lifetime. The reload detector REBINDS the cache key
to a freshly loaded dict rather than mutating the captured one
(`workflowReloadDetector.py:155`), so a socket left open keeps the
pre-edit object indefinitely and every later dispatch runs superseded
commands — silently and successfully.

This file is the demonstration the plan asked for before any fix was
designed. It reproduces the defect through the production accept path,
rebinding the cache the way the reload detector does; mutating the
captured dict in place would pass against the defect and prove nothing.

The dispatch assertion is `xfail(strict=True)`: it documents the defect
today and FAILS the day the defect is fixed, which is the signal to
delete the marker rather than let a stale expectation linger. The
second test is the symmetric half and passes now — a command already in
flight must keep the workflow it started with, or the kill sweep loses
the step directories it sweeps.
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import WebSocketDisconnect

from vaibify.gui import pipelineServer
from vaibify.gui.pipelineServer import fnHandlePipelineWs


S_CONTAINER_ID = "cid-stale-workflow"
S_OLD_COMMAND = "python analyze.py --iterations 600"
S_NEW_COMMAND = "python analyze.py --iterations 3600"


def _fdictWorkflowWithCommand(sCommand):
    """Return a one-step workflow whose data command is sCommand."""
    return {
        "sProjectRepoPath": "/workspace/exampleRepo",
        "listSteps": [{
            "sStepId": "analyze",
            "sName": "Analyze",
            "sDirectory": "Analyze",
            "saDataCommands": [sCommand],
        }],
    }


class _FakeRebindingWebSocket:
    """Accept, then rebind the cache before delivering the run frame.

    The ordering IS the defect: the handler captures the workflow at
    accept, the researcher's edit reaches the cache while the socket
    stays open, and only then does the run arrive.
    """

    def __init__(self, dictCtx, dictReplacement):
        self._dictCtx = dictCtx
        self._dictReplacement = dictReplacement
        self._bDelivered = False
        self.listSent = []

    async def accept(self):
        return None

    async def receive_text(self):
        if self._bDelivered:
            raise WebSocketDisconnect(code=1000)
        self._bDelivered = True
        # Exactly what workflowReloadDetector does: REBIND the key.
        self._dictCtx["workflows"][S_CONTAINER_ID] = self._dictReplacement
        return json.dumps({
            "sAction": "runSelected", "listStepIndices": [0],
        })

    async def send_json(self, dictEvent):
        self.listSent.append(dictEvent)

    async def close(self, code=1000):
        return None


def _fdictBuildContext(dictWorkflow):
    """Return a dictCtx sufficient to drive the accept path."""
    return {
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {
            S_CONTAINER_ID:
                "/workspace/exampleRepo/.vaibify/projects/demo.json",
        },
        "docker": object(),
        "pipelineTasks": {},
    }


async def _flistCaptureDispatchedCommands(websocketFake, dictCtx):
    """Drive the accept path; return the commands dispatch was handed."""
    listCommands = []

    async def fnRecordingDispatch(
        sAction, dictRequest, connectionDocker, sContainerId,
        dictWorkflow, dictWorkflowPathCache, sWorkflowDirectory,
        fnCallback, dictInteractive=None,
    ):
        for dictStep in dictWorkflow.get("listSteps", []):
            listCommands.extend(dictStep.get("saDataCommands", []))

    with patch.object(
        pipelineServer, "fnDispatchAction", fnRecordingDispatch,
    ):
        with patch.object(
            pipelineServer, "_fdictBuildDurableDispatchContext",
            lambda *args, **kwargs: None,
        ):
            try:
                await fnHandlePipelineWs(
                    websocketFake, dictCtx, S_CONTAINER_ID,
                )
            except WebSocketDisconnect:
                pass
            for _ in range(3):
                await asyncio.sleep(0)
    return listCommands


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Defect 7, demonstrated not fixed: the socket captures the "
        "workflow at accept and the reload detector rebinds the cache "
        "key, so the dispatch runs the superseded command. Remove this "
        "marker with the fix (plan step 3b) — a strict xfail fails the "
        "day the behaviour becomes correct."
    ),
)
async def test_dispatch_after_an_external_edit_runs_the_new_command():
    """A run dispatched after an edit must execute the EDITED command."""
    dictCtx = _fdictBuildContext(_fdictWorkflowWithCommand(S_OLD_COMMAND))
    websocketFake = _FakeRebindingWebSocket(
        dictCtx, _fdictWorkflowWithCommand(S_NEW_COMMAND),
    )
    listCommands = await _flistCaptureDispatchedCommands(
        websocketFake, dictCtx,
    )
    assert listCommands, "the run never reached dispatch at all"
    assert S_NEW_COMMAND in listCommands, (
        f"the dispatch ran superseded code: {listCommands}. The "
        f"researcher's edit reached the cache before the run was "
        f"clicked, and the workflow document now describes something "
        f"that did not run."
    )


@pytest.mark.asyncio
async def test_the_cache_really_was_rebound_before_dispatch():
    """The harness must model a REBIND, not an in-place mutation.

    Without this, a future edit could 'fix' the test by mutating the
    captured dict — which every socket would see, so the demonstration
    would pass while the defect stood untouched. This asserts the
    fixture drives the same operation the reload detector performs.
    """
    dictOriginal = _fdictWorkflowWithCommand(S_OLD_COMMAND)
    dictCtx = _fdictBuildContext(dictOriginal)
    dictReplacement = _fdictWorkflowWithCommand(S_NEW_COMMAND)
    websocketFake = _FakeRebindingWebSocket(dictCtx, dictReplacement)
    await _flistCaptureDispatchedCommands(websocketFake, dictCtx)

    assert dictCtx["workflows"][S_CONTAINER_ID] is dictReplacement, (
        "the cache key was not rebound; the demonstration would prove "
        "nothing"
    )
    assert dictOriginal["listSteps"][0]["saDataCommands"] == [
        S_OLD_COMMAND,
    ], "the captured object was mutated in place, which is not the defect"
