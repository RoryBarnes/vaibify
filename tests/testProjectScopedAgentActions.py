"""One container, several projects: run state and agent actions stay in their project.

A researcher's container hosted two projects, and agents working in
both at once met three container-wide seams (2026-09-22/23): an
agent's ``vaibify-do insert-step`` landed in the OTHER project's
``project.json``; one ``pipeline_state.json`` for the whole container
let a run in one project read as running steps in the other; and a
refused run said only "already running", naming no project.

The rules for this file: container id != container name, two distinct
project directories in every fixture, and the agent lane driven
through a real client -- once over a real TCP socket with the real
``vaibify-do`` request code.
"""

import asyncio
import importlib.util
import json
import os
import shlex
import socket
import threading
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from vaibify.gui import agentProjectScope
from vaibify.gui import pipelineServer
from vaibify.gui import pipelineState
from vaibify.gui import stateManager
from vaibify.gui import workflowManager
from tests.testAgentLaneEnforcement import (  # noqa: F401 -- fixtures
    S_AGENT_TOKEN,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    appViewer,
    clientAgent,
)


S_RESOURCE_ID = "cid-two-projects"
S_REPO_OPEN = "/workspace/projectOpen"
S_REPO_OTHER = "/workspace/projectOther"
S_WORKFLOW_OPEN = S_REPO_OPEN + "/.vaibify/projects/opened.json"
S_WORKFLOW_OTHER = S_REPO_OTHER + "/.vaibify/projects/other.json"
S_ROOT_STATE_PATH = "/workspace/.vaibify/pipeline_state.json"


class _FakeContainerFiles:
    """An in-memory container filesystem for the state file's I/O.

    Honors the three commands the state writer and clearer issue --
    the temp-then-rename ``mv``, ``rm -f`` and ``mkdir -p`` -- so a
    write lands where the production code actually renames it to.
    """

    def __init__(self):
        self.dictFiles = {}

    def fnWriteFile(self, sContainerId, sPath, baContent, **kwargs):
        self.dictFiles[sPath] = baContent

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath not in self.dictFiles:
            raise FileNotFoundError(sPath)
        return self.dictFiles[sPath]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        listTokens = shlex.split(sCommand.split("||")[0])
        if listTokens[:1] == ["mv"]:
            self.dictFiles[listTokens[2]] = self.dictFiles.pop(
                listTokens[1],
            )
        elif listTokens[:2] == ["rm", "-f"]:
            for sPath in listTokens[2:]:
                self.dictFiles.pop(sPath, None)
        return (0, "")

    def fdictReadJson(self, sPath):
        return json.loads(self.dictFiles[sPath])


def _fdictRunningState(sRepoPath, sWorkflowPath, iActiveStep=3):
    dictState = pipelineState.fdictBuildInitialState(
        "runAll", sRepoPath + "/.vaibify/logs/run.log", 5,
        sWorkflowPath=sWorkflowPath, sProjectRepoPath=sRepoPath,
    )
    dictState["iActiveStep"] = iActiveStep
    dictState.update(pipelineState.fdictBuildHeartbeatUpdate())
    return dictState


def _fdictContextWithOpenProject(filesFake):
    return {
        "docker": filesFake,
        "workflows": {
            S_RESOURCE_ID: {
                "sWorkflowName": "Opened",
                "sProjectRepoPath": S_REPO_OPEN,
            },
        },
        "paths": {S_RESOURCE_ID: S_WORKFLOW_OPEN},
        "pipelineTasks": {},
        "lastDiscoveredWorkflows": {
            S_RESOURCE_ID: {S_WORKFLOW_OPEN, S_WORKFLOW_OTHER},
        },
    }


class _LiveTask:
    """A pipeline-task double that is still running."""

    def __init__(self, sProjectRepoPath, sWorkflowName="Other"):
        self.sProjectRepoPath = sProjectRepoPath
        self.sWorkflowName = sWorkflowName

    def done(self):
        return False


# ---------------------------------------------------------------------------
# Run state lives in its project
# ---------------------------------------------------------------------------


def testTwoProjectsRunStatesLandInTheirOwnRepositories():
    """Each run's writer persists beside its own project, never the root."""
    filesFake = _FakeContainerFiles()
    pipelineState.fnWriteState(
        filesFake, S_RESOURCE_ID,
        _fdictRunningState(S_REPO_OPEN, S_WORKFLOW_OPEN, iActiveStep=1),
    )
    pipelineState.fnWriteState(
        filesFake, S_RESOURCE_ID,
        _fdictRunningState(S_REPO_OTHER, S_WORKFLOW_OTHER, iActiveStep=4),
    )
    assert S_ROOT_STATE_PATH not in filesFake.dictFiles
    assert filesFake.fdictReadJson(
        S_REPO_OPEN + "/.vaibify/pipeline_state.json",
    )["iActiveStep"] == 1
    assert filesFake.fdictReadJson(
        S_REPO_OTHER + "/.vaibify/pipeline_state.json",
    )["iActiveStep"] == 4


@pytest.mark.asyncio
@pytest.mark.falsification
async def testTheOpenProjectNeverReadsAnotherProjectsRun():
    """The poll's reader answers for the open project only.

    The open project's own last run finished; the other project is
    mid-run and holds the hub's live task. The reader must return the
    finished run.

    Kills: resolving the reader to the resource root, or to the live
    run's project ahead of the open one.
    """
    filesFake = _FakeContainerFiles()
    dictOpenFinished = _fdictRunningState(S_REPO_OPEN, S_WORKFLOW_OPEN)
    dictOpenFinished.update(pipelineState.fdictBuildCompletedState(0))
    pipelineState.fnWriteState(filesFake, S_RESOURCE_ID, dictOpenFinished)
    pipelineState.fnWriteState(
        filesFake, S_RESOURCE_ID,
        _fdictRunningState(S_REPO_OTHER, S_WORKFLOW_OTHER),
    )
    dictCtx = _fdictContextWithOpenProject(filesFake)
    dictCtx["pipelineTasks"][S_RESOURCE_ID] = _LiveTask(S_REPO_OTHER)
    dictState = await pipelineState.fdictReadReconciledState(
        dictCtx, S_RESOURCE_ID,
    )
    assert dictState["sWorkflowPath"] == S_WORKFLOW_OPEN
    assert dictState["bRunning"] is False


@pytest.mark.asyncio
async def testWithNoProjectOpenTheLiveRunsProjectIsRead():
    """Leaving a project mid-run must not hide the run from the agent lane."""
    filesFake = _FakeContainerFiles()
    pipelineState.fnWriteState(
        filesFake, S_RESOURCE_ID,
        _fdictRunningState(S_REPO_OTHER, S_WORKFLOW_OTHER, iActiveStep=2),
    )
    dictCtx = {
        "docker": filesFake, "workflows": {},
        "pipelineTasks": {S_RESOURCE_ID: _LiveTask(S_REPO_OTHER)},
    }
    dictState = await pipelineState.fdictReadReconciledState(
        dictCtx, S_RESOURCE_ID,
    )
    assert dictState["bRunning"] is True
    assert dictState["iActiveStep"] == 2


@pytest.mark.asyncio
async def testAStaleRunIsReconciledInTheProjectItWasReadFrom():
    """A dead runner's record is rewritten where it was found.

    A state written before ``sProjectRepoPath`` existed names no
    project; its reconcile must still go back to the file it came from
    rather than to the resource root, or the project keeps claiming a
    run that died.
    """
    filesFake = _FakeContainerFiles()
    dictLegacy = _fdictRunningState(S_REPO_OPEN, S_WORKFLOW_OPEN)
    del dictLegacy["sProjectRepoPath"]
    dictLegacy["sLastHeartbeat"] = "2000-01-01T00:00:00+00:00"
    sOpenStatePath = S_REPO_OPEN + "/.vaibify/pipeline_state.json"
    filesFake.dictFiles[sOpenStatePath] = json.dumps(dictLegacy).encode()
    dictState = await pipelineState.fdictReadReconciledState(
        _fdictContextWithOpenProject(filesFake), S_RESOURCE_ID,
    )
    assert dictState["bRunning"] is False
    assert filesFake.fdictReadJson(sOpenStatePath)["bRunning"] is False
    assert S_ROOT_STATE_PATH not in filesFake.dictFiles


@pytest.mark.falsification
def testTheContainerBusyVetoSeesARunInAProjectThatIsNotOpen():
    """Release and idle-exit ask about the CONTAINER, not the open project.

    Kills: answering the busy veto from the open project's file alone,
    which would release a container, or let the hub exit, in the middle
    of another project's run.
    """
    filesFake = _FakeContainerFiles()
    dictCtx = _fdictContextWithOpenProject(filesFake)
    assert pipelineState.fbContainerHasLiveRun(
        dictCtx, S_RESOURCE_ID,
    ) is False
    pipelineState.fnWriteState(
        filesFake, S_RESOURCE_ID,
        _fdictRunningState(S_REPO_OTHER, S_WORKFLOW_OTHER),
    )
    assert pipelineState.fbContainerHasLiveRun(
        dictCtx, S_RESOURCE_ID,
    ) is True


def testTheBusyVetoCountsTheHubsOwnLiveTask():
    dictCtx = _fdictContextWithOpenProject(_FakeContainerFiles())
    dictCtx["pipelineTasks"][S_RESOURCE_ID] = _LiveTask(S_REPO_OTHER)
    assert pipelineState.fbContainerHasLiveRun(dictCtx, S_RESOURCE_ID)


def testLogsLiveInTheirProject():
    assert workflowManager.fsLogsDirectoryFor(
        S_RESOURCE_ID, S_REPO_OTHER,
    ) == S_REPO_OTHER + "/.vaibify/logs"


def testTheProjectGitignoreCoversTheRunStateAndLogs():
    """Run state now lives inside the repository; git must not see it.

    An untracked file refuses the reproduction export, so run
    bookkeeping left out of the ignore file would block verification.
    """
    listLines = stateManager.S_VAIBIFY_GITIGNORE_BODY.splitlines()
    for sEntry in (
        "pipeline_state.json", "pipeline_state.json.*.tmp", "logs/",
    ):
        assert sEntry in listLines


def _fappBuildStateRoute(filesFake, dictCtx):
    from vaibify.gui.routes.pipelineRoutes import _fnRegisterPipelineState
    app = FastAPI()
    dictCtx.setdefault("require", MagicMock())
    dictCtx["docker"] = filesFake
    _fnRegisterPipelineState(app, dictCtx)
    return TestClient(app)


@pytest.mark.falsification
def testThePipelineStateRouteWithNoProjectDoesNotReportTheRootFile():
    """A root file left by an older hub is not a run to report.

    Kills: letting the no-project case fall through to a read of the
    resource root, which would show a finished or dead run as live.
    """
    filesFake = _FakeContainerFiles()
    filesFake.dictFiles[S_ROOT_STATE_PATH] = json.dumps(
        {"bRunning": True, "iActiveStep": 7},
    ).encode()
    clientHttp = _fappBuildStateRoute(
        filesFake, {"workflows": {}, "pipelineTasks": {}},
    )
    dictAnswer = clientHttp.get(f"/api/pipeline/{S_RESOURCE_ID}/state").json()
    assert dictAnswer["bRunning"] is False


def testThePipelineStateRouteAnswersForTheOpenProject():
    filesFake = _FakeContainerFiles()
    pipelineState.fnWriteState(
        filesFake, S_RESOURCE_ID,
        _fdictRunningState(S_REPO_OTHER, S_WORKFLOW_OTHER),
    )
    clientHttp = _fappBuildStateRoute(
        filesFake, _fdictContextWithOpenProject(filesFake),
    )
    dictAnswer = clientHttp.get(f"/api/pipeline/{S_RESOURCE_ID}/state").json()
    assert dictAnswer["bRunning"] is False


# ---------------------------------------------------------------------------
# The agent lane refuses a request aimed at another project (HTTP)
# ---------------------------------------------------------------------------


def _fnOpenProjectInApp(app, sWorkflowPath, sProjectName):
    dictCtx = app.state.dictRouteContext
    dictCtx["paths"][S_CONTAINER_ID] = sWorkflowPath
    dictCtx["workflows"][S_CONTAINER_ID] = {
        "sWorkflowName": sProjectName,
        "sProjectRepoPath": agentProjectScope.fsProjectDirectoryOfWorkflow(
            sWorkflowPath,
        ),
        "listSteps": [],
    }


@pytest.mark.falsification
def testAnAgentInAnotherProjectIsRefusedBeforeTheRouteRuns(
    appViewer, clientAgent,
):
    """A declared project that is not the open one is a 409, and nothing runs.

    Kills: dropping the comparison, or comparing against anything that
    is not the open project's directory.
    """
    _fnOpenProjectInApp(appViewer, S_WORKFLOW_OPEN, "Opened")
    with patch.object(
        appViewer.state.dictRouteContext["docker"],
        "flistContainerPathsExist", create=True, return_value=[True],
    ) as mockProbe:
        responseHttp = clientAgent.post(
            f"/api/files/{S_CONTAINER_ID}/exist",
            json={"saRelativePaths": ["stepA/output.dat"]},
            headers={agentProjectScope.S_AGENT_PROJECT_HEADER: S_REPO_OTHER},
        )
    assert responseHttp.status_code == 409
    dictDetail = responseHttp.json()["detail"]
    assert dictDetail["sRefusal"] == "project-mismatch"
    assert dictDetail["sOpenProjectDirectory"] == S_REPO_OPEN
    assert dictDetail["sAgentProjectDirectory"] == S_REPO_OTHER
    assert "Opened" in dictDetail["sMessage"]
    mockProbe.assert_not_called()


def testAnAgentInTheOpenProjectIsServedAndToldWhichProject(
    appViewer, clientAgent,
):
    _fnOpenProjectInApp(appViewer, S_WORKFLOW_OPEN, "Opened Project")
    responseHttp = clientAgent.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": []},
        headers={
            agentProjectScope.S_AGENT_PROJECT_HEADER: S_REPO_OPEN + "/",
        },
    )
    assert responseHttp.status_code == 200
    assert urllib.parse.unquote(
        responseHttp.headers[agentProjectScope.S_SERVED_PROJECT_HEADER],
    ) == S_REPO_OPEN
    assert urllib.parse.unquote(
        responseHttp.headers[agentProjectScope.S_SERVED_PROJECT_NAME_HEADER],
    ) == "Opened Project"


def testAnUndeclaredAgentIsServedAsBeforeButStillToldTheProject(
    appViewer, clientAgent,
):
    """An older vaibify-do sends no declaration; stranding it would need a rebuild."""
    _fnOpenProjectInApp(appViewer, S_WORKFLOW_OPEN, "Opened")
    responseHttp = clientAgent.post(
        f"/api/files/{S_CONTAINER_ID}/exist", json={"saRelativePaths": []},
    )
    assert responseHttp.status_code == 200
    assert agentProjectScope.S_SERVED_PROJECT_HEADER in responseHttp.headers


# ---------------------------------------------------------------------------
# The pipeline socket: a misdirected run, and a busy container named
# ---------------------------------------------------------------------------


class _FakeRunSocket:
    """Feed scripted run frames; record every event sent back."""

    def __init__(self, listMessages):
        self._listMessages = [json.dumps(d) for d in listMessages]
        self.listSent = []

    async def receive_text(self):
        if self._listMessages:
            return self._listMessages.pop(0)
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, dictEvent):
        self.listSent.append(dictEvent)


async def _flistRunFrames(listMessages, dictPipelineTasks):
    listDispatched = []

    async def fnRecordingDispatch(sAction, *args, **kwargs):
        listDispatched.append(sAction)

    websocketFake = _FakeRunSocket(listMessages)
    with patch.object(pipelineServer, "fnDispatchAction", fnRecordingDispatch):
        with pytest.raises(WebSocketDisconnect):
            await pipelineServer.fnPipelineMessageLoop(
                websocketFake, MagicMock(), S_RESOURCE_ID,
                {"sWorkflowName": "Opened", "listSteps": []},
                {S_RESOURCE_ID: S_WORKFLOW_OPEN}, S_REPO_OPEN,
                dictPipelineTasks=dictPipelineTasks,
            )
        for _ in range(3):
            await asyncio.sleep(0)
    return websocketFake.listSent, listDispatched


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAnAgentRunAimedAtAnotherProjectIsNeverDispatched():
    """The run frame's project is checked before dispatch; the matching one runs.

    Kills: checking after the task starts, or not checking the socket
    lane at all (runs never pass the HTTP middleware).
    """
    listSent, listDispatched = await _flistRunFrames([
        {"sAction": "runAll",
         agentProjectScope.S_AGENT_PROJECT_FIELD: S_REPO_OTHER},
        {"sAction": "runAll",
         agentProjectScope.S_AGENT_PROJECT_FIELD: S_REPO_OPEN},
    ], {})
    listRefusals = [d for d in listSent if d.get("sType") == "runRefused"]
    assert [d["sReason"] for d in listRefusals] == ["projectMismatch"]
    assert listRefusals[0]["sOpenProjectDirectory"] == S_REPO_OPEN
    assert listDispatched == ["runAll"]


@pytest.mark.asyncio
async def testABusyRefusalNamesTheProjectHoldingTheContainer():
    """'Already running' must say whose run, or it reads as this project's."""
    taskHolder = asyncio.ensure_future(asyncio.sleep(30))
    dictPipelineTasks = {}
    pipelineServer._fnRegisterPipelineTask(
        dictPipelineTasks, S_RESOURCE_ID, taskHolder,
        dictWorkflow={
            "sProjectRepoPath": S_REPO_OTHER, "sWorkflowName": "Other",
        },
    )
    try:
        listSent, listDispatched = await _flistRunFrames(
            [{"sAction": "runAll"}], dictPipelineTasks,
        )
    finally:
        taskHolder.cancel()
    assert listDispatched == []
    dictRefusal = listSent[0]
    assert dictRefusal["sType"] == "runRefused"
    assert dictRefusal["sHolderProject"] == (
        f"project 'Other' ({S_REPO_OTHER})"
    )
    assert S_REPO_OTHER in dictRefusal["sMessage"]


# ---------------------------------------------------------------------------
# vaibify-do: declares its project, announces the served one
# ---------------------------------------------------------------------------


_S_VAIBIFY_DO_PATH = (
    Path(__file__).resolve().parent.parent
    / "vaibify" / "containerImage" / "vaibifyDo.py"
)


@pytest.fixture
def modCli():
    specModule = importlib.util.spec_from_file_location(
        "vaibifyDoProjectScope", _S_VAIBIFY_DO_PATH,
    )
    moduleCli = importlib.util.module_from_spec(specModule)
    specModule.loader.exec_module(moduleCli)
    return moduleCli


@pytest.fixture
def pathTwoProjects(tmp_path):
    for sName in ("projectOpen", "projectOther"):
        (tmp_path / sName / ".vaibify" / "projects").mkdir(parents=True)
        (tmp_path / sName / "stepDirectory").mkdir()
    return tmp_path


def testVaibifyDoFindsTheProjectAboveItsDirectory(modCli, pathTwoProjects):
    sStep = str(pathTwoProjects / "projectOther" / "stepDirectory")
    assert modCli.fsFindEnclosingProjectDirectory(sStep) == str(
        pathTwoProjects / "projectOther",
    )
    assert modCli.fsFindEnclosingProjectDirectory(str(pathTwoProjects)) == ""


def testVaibifyDoDeclaresItsProjectOnBothLanes(
    modCli, pathTwoProjects, monkeypatch,
):
    monkeypatch.chdir(pathTwoProjects / "projectOther" / "stepDirectory")
    sExpected = str(pathTwoProjects / "projectOther")
    assert modCli.fdictBuildRequestHeaders("tok")[
        modCli.S_AGENT_PROJECT_HEADER
    ] == sExpected
    dictPayload = modCli.fdictResolveWsPayload(
        {"sName": "run-all", "sPath": "runAll"}, [],
    )
    assert dictPayload[modCli.S_AGENT_PROJECT_FIELD] == sExpected


def testVaibifyDoOutsideEveryProjectDeclaresNothing(
    modCli, pathTwoProjects, monkeypatch,
):
    monkeypatch.chdir(pathTwoProjects)
    assert modCli.S_AGENT_PROJECT_HEADER not in (
        modCli.fdictBuildRequestHeaders("tok")
    )


def testTheCliAndTheHubAgreeOnTheWireNames(modCli):
    """vaibify-do cannot import the host package, so its copies must match."""
    for sName in (
        "S_AGENT_PROJECT_HEADER", "S_SERVED_PROJECT_HEADER",
        "S_SERVED_PROJECT_NAME_HEADER", "S_AGENT_PROJECT_FIELD",
    ):
        assert getattr(modCli, sName) == getattr(agentProjectScope, sName)


def _fiFreePort():
    with socket.socket() as socketProbe:
        socketProbe.bind(("127.0.0.1", 0))
        return socketProbe.getsockname()[1]


@pytest.fixture
def sLiveHubUrl(appViewer):
    """Serve the real application on a real loopback socket."""
    import uvicorn
    iPort = _fiFreePort()
    serverUvicorn = uvicorn.Server(uvicorn.Config(
        appViewer, host="127.0.0.1", port=iPort, log_level="error",
    ))
    threadServer = threading.Thread(target=serverUvicorn.run, daemon=True)
    threadServer.start()
    fDeadline = time.monotonic() + 10.0
    while not serverUvicorn.started and time.monotonic() < fDeadline:
        time.sleep(0.05)
    assert serverUvicorn.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{iPort}"
    serverUvicorn.should_exit = True
    threadServer.join(timeout=10.0)


def testTheRealCliInTheWrongProjectIsRefusedOverTheWire(
    modCli, pathTwoProjects, monkeypatch, appViewer, clientAgent,
    sLiveHubUrl, capsys,
):
    """End to end: real vaibify-do request code, real socket, real middleware.

    The agent works in one project directory while the dashboard has
    the other open; the request must come back refused and say so,
    and the first line the agent sees names the project the hub has
    open.
    """
    sOpenDirectory = str(pathTwoProjects / "projectOpen")
    _fnOpenProjectInApp(
        appViewer,
        sOpenDirectory + "/.vaibify/projects/opened.json", "Opened",
    )
    monkeypatch.chdir(pathTwoProjects / "projectOther" / "stepDirectory")
    iExitCode = modCli.fiSendHttpRequest(
        {"sUrl": f"{sLiveHubUrl}/api/files/{S_CONTAINER_ID}/exist",
         "dictBody": {"saRelativePaths": []}},
        S_AGENT_TOKEN, "POST", True,
    )
    captured = capsys.readouterr()
    assert iExitCode == 1
    assert json.loads(captured.out)["detail"]["sRefusal"] == (
        "project-mismatch"
    )
    assert captured.err.startswith(
        f"vaibify-do: project 'Opened' at {sOpenDirectory}",
    )


# ---------------------------------------------------------------------------
# Every production reader names its project
# ---------------------------------------------------------------------------


def testEveryProductionRunStateReaderNamesItsProject():
    """A reader that omits the project silently reads the resource root.

    The project argument defaults to empty for the direct library lane,
    so forgetting it is not an error Python reports; this makes it one
    for code under ``vaibify/``.
    """
    import ast
    setGuarded = {"fdictReadState": 3, "fsLogsDirectoryFor": 2}
    listViolations = []
    for pathSource in Path("vaibify").rglob("*.py"):
        treeModule = ast.parse(pathSource.read_text(encoding="utf-8"))
        for nodeCall in ast.walk(treeModule):
            if not isinstance(nodeCall, ast.Call):
                continue
            sName = getattr(nodeCall.func, "attr", None) or getattr(
                nodeCall.func, "id", None,
            )
            if sName not in setGuarded:
                continue
            if len(nodeCall.args) + len(nodeCall.keywords) < (
                setGuarded[sName]
            ):
                listViolations.append(f"{pathSource}:{nodeCall.lineno}")
    assert listViolations == []
