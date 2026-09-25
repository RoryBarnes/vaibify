"""An agent runs, reads and stops its OWN project's pipeline.

A container runs several projects at once, and each project's agent
works in its own directory. Before this, every agent action acted on
the project the dashboard had open, so an agent was refused its own
run whenever the researcher was looking at another project -- and
agents that met the refusal routed around vaibify with shell scripts
nobody could see (2026-09-22).

What an agent may do in a project that is not open is narrow: run its
pipeline over the socket and read that run's state. (Stopping a run is
a user-only action in every project.) The
project is resolved from what the hub itself discovered or recorded;
the directory the agent names is a lookup key, never a path the hub
opens. Changing a project's definition still requires it to be open.

The rules for this file: container id != container name, two projects
with distinct directories, the agent lane driven through the real
middleware, and each falsification test names its mutation.
"""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect

from vaibify.gui import agentProjectScope
from vaibify.gui import pipelineRunSlots
from vaibify.gui import pipelineServer
from vaibify.gui import workflowManager
from tests.testAgentLaneEnforcement import (  # noqa: F401 -- fixtures
    S_CONTAINER_ID,
    appViewer,
    clientAgent,
)
from tests.testProjectScopedAgentActions import _fnOpenProjectInApp


S_REPO_OPEN = "/workspace/projectOpen"
S_REPO_AGENT = "/workspace/projectAgent"
S_WORKFLOW_OPEN = S_REPO_OPEN + "/.vaibify/projects/opened.json"
S_WORKFLOW_AGENT = S_REPO_AGENT + "/.vaibify/projects/agent.json"
BA_AGENT_WORKFLOW = b'{"sWorkflowName": "Agent Project"}'
S_AGENT_FINGERPRINT = hashlib.sha256(BA_AGENT_WORKFLOW).hexdigest()


# ---------------------------------------------------------------------------
# The pipeline socket binds to the agent's project
# ---------------------------------------------------------------------------


class _FakeAgentSocket:
    """An agent's pipeline socket: a declared project and scripted frames."""

    def __init__(self, sDeclaredProject, listFrames):
        self.query_params = (
            {agentProjectScope.S_AGENT_PROJECT_QUERY: sDeclaredProject}
            if sDeclaredProject else {}
        )
        self._listFrames = [json.dumps(dictFrame) for dictFrame in listFrames]
        self.listSent = []
        self.bClosed = False

    async def accept(self):
        return None

    async def close(self, code=1000):
        self.bClosed = True

    async def receive_text(self):
        if self._listFrames:
            return self._listFrames.pop(0)
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, dictEvent):
        self.listSent.append(dictEvent)


class _FakeFiles:
    """Serves each project's workflow bytes; the agent's can be edited."""

    def __init__(self):
        self.dictFiles = {
            S_WORKFLOW_AGENT: BA_AGENT_WORKFLOW,
            S_WORKFLOW_OPEN: b'{"sWorkflowName": "Opened"}',
        }

    def fbaFetchFile(self, _sContainerId, sPath, iMaxBytes=None):
        return self.dictFiles[sPath]


def _fdictAgentWorkflow():
    return {
        "sWorkflowName": "Agent Project",
        "sProjectRepoPath": S_REPO_AGENT,
        "listSteps": [],
        "_sSourceFingerprint": hashlib.sha256(
            _FakeFiles().dictFiles[S_WORKFLOW_AGENT],
        ).hexdigest(),
        workflowManager.S_LOADED_FROM_KEY: S_WORKFLOW_AGENT,
    }


def _fdictContextWithTheOtherProjectOpen(filesFake):
    return {
        "docker": filesFake,
        "paths": {S_CONTAINER_ID: S_WORKFLOW_OPEN},
        "workflows": {S_CONTAINER_ID: {
            "sWorkflowName": "Opened", "sProjectRepoPath": S_REPO_OPEN,
            "listSteps": [],
            workflowManager.S_LOADED_FROM_KEY: S_WORKFLOW_OPEN,
        }},
        "pipelineTasks": {},
        "dictLastRunByProject": {},
    }


async def _ftDriveAgentSocket(dictCtx, sDeclared, listFrames,
                              listDiscovered=None):
    """Run the real socket handler for an agent; return (sent, dispatched)."""
    listDispatched = []

    async def fnRecordDispatch(
        sAction, dictRequest, connectionDocker, sContainerId,
        dictWorkflow, dictWorkflowPathCache, *args, **kwargs,
    ):
        listDispatched.append(
            (sAction, dictWorkflowPathCache.get(sContainerId)),
        )

    websocketFake = _FakeAgentSocket(sDeclared, listFrames)
    with patch.object(
        workflowManager, "flistFindWorkflowsInContainer",
        return_value=listDiscovered if listDiscovered is not None else [
            {"sPath": S_WORKFLOW_AGENT}, {"sPath": S_WORKFLOW_OPEN},
        ],
    ), patch.object(
        pipelineServer, "_fdictLoadAgentProjectWorkflow",
        side_effect=lambda *args: _fdictAgentWorkflow(),
    ), patch.object(pipelineServer, "fnDispatchAction", fnRecordDispatch):
        await pipelineServer.fnHandlePipelineWs(
            websocketFake, dictCtx, S_CONTAINER_ID,
        )
        for _ in range(3):
            await asyncio.sleep(0)
    return websocketFake.listSent, listDispatched


def _fdictAgentRunFrame(sFingerprint=S_AGENT_FINGERPRINT):
    return {
        "sAction": "runAll",
        agentProjectScope.S_AGENT_PROJECT_FIELD: S_REPO_AGENT,
        "sAcknowledgedSourceFingerprint": sFingerprint,
        "sAcknowledgedWorkflowPath": S_WORKFLOW_AGENT,
    }


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAnAgentRunsItsOwnProjectWhileAnotherIsOpen():
    """The socket serves the agent's project and its run dispatches there.

    Kills: binding every socket to the open project, which refuses the
    agent's run as aimed at the wrong project whenever the researcher
    is looking at another one.
    """
    dictCtx = _fdictContextWithTheOtherProjectOpen(_FakeFiles())
    listSent, listDispatched = await _ftDriveAgentSocket(
        dictCtx, S_REPO_AGENT, [_fdictAgentRunFrame()],
    )
    assert listSent[0]["sType"] == "workflowBound"
    assert listSent[0]["sWorkflowPath"] == S_WORKFLOW_AGENT
    assert listDispatched == [("runAll", S_WORKFLOW_AGENT)]
    assert dictCtx["workflows"][S_CONTAINER_ID]["sWorkflowName"] == "Opened"


@pytest.mark.asyncio
async def testAProjectTheHubCannotFindIsRefusedByName():
    """Nothing binds, nothing runs, and the agent is told why."""
    dictCtx = _fdictContextWithTheOtherProjectOpen(_FakeFiles())
    listSent, listDispatched = await _ftDriveAgentSocket(
        dictCtx, "/workspace/nowhere", [_fdictAgentRunFrame()],
    )
    assert listDispatched == []
    assert listSent[0]["sReason"] == "projectUnresolved"
    assert "/workspace/nowhere" in listSent[0]["sMessage"]


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAnEditToTheAgentsProjectReloadsThatProject():
    """The freshness gate reads and reloads the file the socket serves.

    Kills: reloading through the dashboard's reload detector, which
    only ever reloads the OPEN project -- the refusal then hands the
    agent the open project's fingerprint, and it can never run.
    """
    filesFake = _FakeFiles()
    dictCtx = _fdictContextWithTheOtherProjectOpen(filesFake)
    baEdited = b'{"sWorkflowName": "Agent Project, edited"}'
    sEditedFingerprint = hashlib.sha256(baEdited).hexdigest()

    def fdictLoadTheEditedFile(*args):
        dictWorkflow = _fdictAgentWorkflow()
        dictWorkflow["_sSourceFingerprint"] = sEditedFingerprint
        return dictWorkflow

    listDispatched = []

    async def fnRecordDispatch(sAction, *args, **kwargs):
        listDispatched.append(sAction)

    websocketFake = _FakeAgentSocket(S_REPO_AGENT, [_fdictAgentRunFrame()])
    with patch.object(
        workflowManager, "flistFindWorkflowsInContainer",
        return_value=[{"sPath": S_WORKFLOW_AGENT}],
    ), patch.object(
        pipelineServer, "_fdictLoadAgentProjectWorkflow",
        side_effect=[_fdictAgentWorkflow(), fdictLoadTheEditedFile()],
    ), patch.object(pipelineServer, "fnDispatchAction", fnRecordDispatch):
        filesFake.dictFiles[S_WORKFLOW_AGENT] = baEdited
        await pipelineServer.fnHandlePipelineWs(
            websocketFake, dictCtx, S_CONTAINER_ID,
        )
    [dictRefusal] = [
        dictEvent for dictEvent in websocketFake.listSent
        if dictEvent.get("sType") == "runRefused"
    ]
    assert dictRefusal["sReason"] == "workflowSuperseded"
    assert dictRefusal["sCurrentSourceFingerprint"] == sEditedFingerprint
    assert listDispatched == []


# ---------------------------------------------------------------------------
# Reading the agent's own run over HTTP
# ---------------------------------------------------------------------------


def _fnRecordAgentRun(appViewer):
    """Register a live run of the agent's project in the real context."""
    dictCtx = appViewer.state.dictRouteContext
    taskRun = SimpleNamespace(
        done=lambda: False, add_done_callback=lambda fnCallback: None,
    )
    pipelineRunSlots.fnRegisterRun(
        dictCtx["pipelineTasks"], S_CONTAINER_ID, taskRun,
        dictWorkflow={
            "sProjectRepoPath": S_REPO_AGENT, "sWorkflowName": "Agent",
            workflowManager.S_LOADED_FROM_KEY: S_WORKFLOW_AGENT,
        },
        sRunId="ab12",
        dictLastRuns=dictCtx.setdefault("dictLastRunByProject", {}),
    )
    return taskRun


@pytest.mark.falsification
def testAnAgentReadsItsOwnRunWhileAnotherProjectIsOpen(
    appViewer, clientAgent,
):
    """The state route is served for the agent's project and names it.

    Kills: refusing every agent request for a project that is not open,
    which leaves an agent unable to learn whether its own run finished.
    """
    _fnOpenProjectInApp(appViewer, S_WORKFLOW_OPEN, "Opened")
    _fnRecordAgentRun(appViewer)
    mockRead = AsyncMock(return_value={"bRunning": True, "iActiveStep": 2})
    with patch(
        "vaibify.gui.pipelineState.fdictReadReconciledState", mockRead,
    ):
        responseHttp = clientAgent.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state",
            headers={agentProjectScope.S_AGENT_PROJECT_HEADER: S_REPO_AGENT},
        )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["bRunning"] is True
    assert mockRead.call_args.kwargs["sProjectRepoPath"] == S_REPO_AGENT
    assert responseHttp.headers[
        agentProjectScope.S_SERVED_PROJECT_HEADER
    ].endswith("projectAgent")


def testAProjectWithNoRecordedRunReadsAsNotRunning(appViewer, clientAgent):
    """No hub record, no read: the agent's name is never opened as a path."""
    _fnOpenProjectInApp(appViewer, S_WORKFLOW_OPEN, "Opened")
    mockRead = AsyncMock()
    with patch(
        "vaibify.gui.pipelineState.fdictReadReconciledState", mockRead,
    ):
        responseHttp = clientAgent.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state",
            headers={
                agentProjectScope.S_AGENT_PROJECT_HEADER: "/etc/elsewhere",
            },
        )
    assert responseHttp.json()["bRunning"] is False
    mockRead.assert_not_called()


def testAnAgentStillCannotEditAProjectThatIsNotOpen(appViewer, clientAgent):
    """Only the run routes follow the agent; a definition change is refused."""
    _fnOpenProjectInApp(appViewer, S_WORKFLOW_OPEN, "Opened")
    responseHttp = clientAgent.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": []},
        headers={agentProjectScope.S_AGENT_PROJECT_HEADER: S_REPO_AGENT},
    )
    assert responseHttp.status_code == 409
