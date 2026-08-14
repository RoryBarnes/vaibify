"""A live socket dispatches the workflow the researcher is looking at.

Defect D1, now FIXED (spec §4.2): ``fnHandlePipelineWs`` used to read
``dictCtx["workflows"].get(sContainerId)`` ONCE at socket accept and
hand that object to the message loop for the socket's whole life. The
reload detector REBINDS the cache key to a freshly loaded dict, so a
tab left open kept the pre-edit object indefinitely and every later
dispatch ran superseded commands — silently, successfully.

The loop now re-reads the LIVE cache at every dispatch and the
pre-dispatch freshness gate proves the bound workflow matches the
file's bytes read now. These tests drive the production accept path
and rebind the cache the way the reload detector does; mutating the
captured dict in place would pass against the defect and prove
nothing, and a companion test pins that the harness models the rebind.

The symmetric half still holds: a command already IN FLIGHT keeps the
workflow it started with, or the kill sweep loses the step
directories it sweeps.
"""

import asyncio
import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi import WebSocketDisconnect

from vaibify.gui import pipelineServer
from vaibify.gui.pipelineServer import fnHandlePipelineWs


S_CONTAINER_ID = "cid-stale-workflow"
S_WORKFLOW_PATH = "/workspace/exampleRepo/.vaibify/projects/demo.json"
S_OLD_COMMAND = "python analyze.py --iterations 600"
S_NEW_COMMAND = "python analyze.py --iterations 3600"
BA_NEW_PROJECT_BYTES = b'{"sEdited": "the researcher\'s new revision"}'


def _fdictWorkflowWithCommand(sCommand, baSourceBytes):
    """Return a one-step workflow whose data command is sCommand."""
    return {
        "sProjectRepoPath": "/workspace/exampleRepo",
        "_sSourceFingerprint": hashlib.sha256(baSourceBytes).hexdigest(),
        "listSteps": [{
            "sStepId": "analyze",
            "sName": "Analyze",
            "sDirectory": "Analyze",
            "saDataCommands": [sCommand],
        }],
    }


class _ConnectionServingProjectBytes:
    """Serve the project file's current bytes for the freshness gate."""

    def __init__(self, baProjectBytes):
        self.baProjectBytes = baProjectBytes

    def fbaFetchFile(self, _sContainerId, sPath):
        if sPath == S_WORKFLOW_PATH:
            return self.baProjectBytes
        raise FileNotFoundError(sPath)


class _FakeRebindingWebSocket:
    """Accept, then rebind the cache before delivering the run frame.

    The ordering IS the defect this file guards against: the handler
    binds the workflow at accept, the researcher's edit reaches the
    cache while the socket stays open, and only then does the run
    arrive.
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


def _fdictBuildContext(dictWorkflow, connectionDocker):
    """Return a dictCtx sufficient to drive the accept path."""
    return {
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {S_CONTAINER_ID: S_WORKFLOW_PATH},
        "docker": connectionDocker,
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
            lambda *tArgs, **dictKeywords: None,
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
@pytest.mark.falsification
async def test_dispatch_after_an_external_edit_runs_the_new_command():
    """Kills: capturing the workflow once at socket accept.

    A run dispatched after an edit must execute the EDITED command.
    The cache was rebound to the researcher's new revision, the disk
    holds the new bytes, and the frame is a legacy one with no
    acknowledgment fields — the two-way record==disk check agrees, so
    the dispatch must run the replacement, not the captured snapshot.
    """
    connection = _ConnectionServingProjectBytes(BA_NEW_PROJECT_BYTES)
    dictCtx = _fdictBuildContext(
        _fdictWorkflowWithCommand(S_OLD_COMMAND, b'{"sOld": true}'),
        connection,
    )
    websocketFake = _FakeRebindingWebSocket(
        dictCtx,
        _fdictWorkflowWithCommand(S_NEW_COMMAND, BA_NEW_PROJECT_BYTES),
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
@pytest.mark.falsification
async def test_a_stale_acknowledgment_is_refused_and_republished():
    """Kills: dispatching a frame that acknowledges a superseded copy.

    The cache and the disk agree on the new revision, but the FRAME
    says the client is still rendering the old one. Running would
    execute code the researcher never saw (ruling R1: refuse and
    report); the refusal is typed so the dashboard does not claim "a
    pipeline action is already running", and it carries the current
    fingerprint so a client that has since applied the revision can
    re-acknowledge.
    """
    connection = _ConnectionServingProjectBytes(BA_NEW_PROJECT_BYTES)
    dictReplacement = _fdictWorkflowWithCommand(
        S_NEW_COMMAND, BA_NEW_PROJECT_BYTES,
    )
    dictCtx = _fdictBuildContext(dictReplacement, connection)
    websocketFake = _FakeRebindingWebSocket(dictCtx, dictReplacement)
    websocketFake.receive_text = _ffnSendStaleAcknowledgment(
        websocketFake,
    )
    listCommands = await _flistCaptureDispatchedCommands(
        websocketFake, dictCtx,
    )
    assert listCommands == [], "a stale acknowledgment was dispatched"
    listRefusals = [
        dictEvent for dictEvent in websocketFake.listSent
        if dictEvent.get("sType") == "runRefused"
    ]
    assert listRefusals, "no refusal event reached the client"
    assert listRefusals[0]["sReason"] == "workflowSuperseded"
    assert listRefusals[0]["sCurrentSourceFingerprint"] == (
        dictReplacement["_sSourceFingerprint"]
    )
    assert "already running" not in listRefusals[0]["sMessage"]


def _ffnSendStaleAcknowledgment(websocketFake):
    """Return a receive_text that sends one stale-acknowledged frame."""

    async def fnReceiveOnce():
        if websocketFake._bDelivered:
            raise WebSocketDisconnect(code=1000)
        websocketFake._bDelivered = True
        return json.dumps({
            "sAction": "runSelected", "listStepIndices": [0],
            "sAcknowledgedSourceFingerprint": "a-superseded-revision",
            "sAcknowledgedWorkflowPath": S_WORKFLOW_PATH,
        })
    return fnReceiveOnce


@pytest.mark.asyncio
@pytest.mark.falsification
async def test_an_unreloaded_disk_edit_refuses_and_reloads():
    """Kills: trusting the cache when the FILE has already moved on.

    The poller has not yet noticed an out-of-band edit: the cache and
    the client agree with each other and both disagree with the disk.
    Dispatching would run superseded commands; refusing WITHOUT
    reloading would strand the researcher clicking Run forever. The
    gate must do both in one operation: delegate the reload (which
    republishes through the workflow epoch) and refuse, typed.
    """
    from vaibify.gui import workflowReloadDetector
    baOldBytes = b'{"sOld": true}'
    dictStale = _fdictWorkflowWithCommand(S_OLD_COMMAND, baOldBytes)
    connection = _ConnectionServingProjectBytes(BA_NEW_PROJECT_BYTES)
    dictCtx = _fdictBuildContext(dictStale, connection)
    listReloadCalls = []

    def fnRecordingReload(
        dictCtxSeen, sContainerId, sWorkflowPath, dictModTimes,
        sPolledFingerprint="",
    ):
        listReloadCalls.append(sPolledFingerprint)
        dictCtxSeen["workflows"][sContainerId] = (
            _fdictWorkflowWithCommand(
                S_NEW_COMMAND, BA_NEW_PROJECT_BYTES,
            )
        )
        return {"bReplaced": True, "dictWorkflow": None, "sError": None}

    websocketFake = _FakeRebindingWebSocket(dictCtx, dictStale)
    with patch.object(
        workflowReloadDetector, "fdictMaybeReloadWorkflow",
        fnRecordingReload,
    ):
        listCommands = await _flistCaptureDispatchedCommands(
            websocketFake, dictCtx,
        )

    assert listCommands == [], (
        "the dispatch ran commands the disk had already superseded"
    )
    assert listReloadCalls == [
        hashlib.sha256(BA_NEW_PROJECT_BYTES).hexdigest(),
    ], "the refusal did not reload; the researcher is stranded"
    listRefusals = [
        dictEvent for dictEvent in websocketFake.listSent
        if dictEvent.get("sType") == "runRefused"
    ]
    assert listRefusals and listRefusals[0]["sReason"] == (
        "workflowSuperseded"
    )


@pytest.mark.asyncio
async def test_the_cache_really_was_rebound_before_dispatch():
    """The harness must model a REBIND, not an in-place mutation.

    Without this, a future edit could 'fix' the test by mutating the
    captured dict — which every socket would see, so the demonstration
    would pass while the defect stood untouched. This asserts the
    fixture drives the same operation the reload detector performs.
    """
    connection = _ConnectionServingProjectBytes(BA_NEW_PROJECT_BYTES)
    dictOriginal = _fdictWorkflowWithCommand(
        S_OLD_COMMAND, b'{"sOld": true}',
    )
    dictCtx = _fdictBuildContext(dictOriginal, connection)
    dictReplacement = _fdictWorkflowWithCommand(
        S_NEW_COMMAND, BA_NEW_PROJECT_BYTES,
    )
    websocketFake = _FakeRebindingWebSocket(dictCtx, dictReplacement)
    await _flistCaptureDispatchedCommands(websocketFake, dictCtx)

    assert dictCtx["workflows"][S_CONTAINER_ID] is dictReplacement, (
        "the cache key was not rebound; the demonstration would prove "
        "nothing"
    )
    assert dictOriginal["listSteps"][0]["saDataCommands"] == [
        S_OLD_COMMAND,
    ], "the captured object was mutated in place, which is not the defect"
