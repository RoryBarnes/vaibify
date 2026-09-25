"""Switching projects inside one container leaves each project intact.

A container can host several projects, and the hub caches ONE of them
per container -- whichever the dashboard has open. A researcher ran two
projects in one container and switched between them while pipelines
ran (2026-09-24). An audit of that switch found work in flight for the
project being LEFT still acting on the container's open-project slot,
which by then named the other project:

- a save still in flight wrote the previous project's workflow over the
  open project's ``project.json``;
- the file-change baseline, kept per container, made every output of the
  newly opened project read as modified, resetting its passed tests;
- a pipeline socket opened in one project ran the next project's
  commands in the first project's step directories;
- a run's remote-data provenance landed in whichever project was open
  when its step finished;
- Stop swept the open project's command names, leaving the running
  project's processes alive;
- a poll that began before the switch could rebind the cache to the
  project the researcher had left.

The rules for this file: container id != container name, two projects
with DISTINCT directories and step names in every fixture, and each
test proves it can fail (a control case, or a named mutation).
"""

import posixpath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, WebSocketDisconnect

from vaibify.gui import pipelineRunSlots
from vaibify.gui import pipelineServer
from vaibify.gui import routeContext
from vaibify.gui import workflowManager
from vaibify.gui import workflowReloadDetector
from vaibify.gui.fileStatusManager import _fdictDetectAndInvalidate
from vaibify.gui.provenanceCommitter import fdictCommitRemoteDataRecords
from vaibify.gui.routes import pipelineRoutes
from tests.testProjectScopedAgentActions import _FakeRunSocket
from tests.testStopDoesNotNeedTheSession import (  # noqa: F401 -- fixtures
    appHub,
    clientBrowser,
    fixtureIsolate,
    _fsClaimAndReturnLease,
)
from tests.testAgentLaneEnforcement import S_CONTAINER_ID, S_CONTAINER_NAME


S_RESOURCE_ID = "cid-switching"
S_REPO_LEFT = "/workspace/projectLeft"
S_REPO_OPENED = "/workspace/projectOpened"
S_WORKFLOW_LEFT = S_REPO_LEFT + "/.vaibify/projects/left.json"
S_WORKFLOW_OPENED = S_REPO_OPENED + "/.vaibify/projects/opened.json"
S_LOADED = workflowManager.S_LOADED_FROM_KEY


def _fdictWorkflow(sRepoPath, sWorkflowPath, sScript, sStepName):
    """Return a cached workflow as the loader leaves it: stamped."""
    return {
        "sWorkflowName": posixpath.basename(sRepoPath),
        "sProjectRepoPath": sRepoPath,
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sStepId": sStepName.lower(),
            "sName": sStepName,
            "sDirectory": sStepName,
            "saDataCommands": [f"python {sScript}"],
            "saOutputDataFiles": ["result.dat"],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictVerification": {"sUnitTest": "passed", "sUser": "passed"},
        }],
        S_LOADED: sWorkflowPath,
    }


def _fdictLeftWorkflow():
    return _fdictWorkflow(
        S_REPO_LEFT, S_WORKFLOW_LEFT, "leftModel.py", "LeftStep",
    )


def _fdictOpenedWorkflow():
    return _fdictWorkflow(
        S_REPO_OPENED, S_WORKFLOW_OPENED, "openedModel.py", "OpenedStep",
    )


# ---------------------------------------------------------------------------
# A workflow is saved only into the file it came from
# ---------------------------------------------------------------------------


def testTheLoaderRecordsTheFileAndTheSaveNeverWritesIt():
    """The stamp exists on load, and neither project.json nor its hash sees it."""
    import json
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps({
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }).encode("utf-8")
    dictLoaded = workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, S_RESOURCE_ID, S_WORKFLOW_OPENED,
    )
    assert dictLoaded[S_LOADED] == S_WORKFLOW_OPENED
    dictUnstamped = dict(dictLoaded)
    dictUnstamped.pop(S_LOADED)
    assert workflowManager.fsComputeWorkflowFingerprint(dictLoaded) == (
        workflowManager.fsComputeWorkflowFingerprint(dictUnstamped)
    )
    assert S_LOADED not in workflowManager._fdictStripComputedFields(
        dictLoaded,
    )


@pytest.mark.falsification
def testASaveHeldAcrossASwitchNeverLandsInTheOpenProject():
    """The previous project's workflow is refused; the open one's is written.

    Kills: saving to the open path without asking which file the
    workflow came from -- the bug, which replaced the open project's
    project.json with the other project's.
    """
    dictCtx = pipelineServer.fdictBuildContext(MagicMock())
    dictCtx["paths"][S_RESOURCE_ID] = S_WORKFLOW_OPENED
    listWrittenPaths = []

    def fnRecordWrite(_docker, _sId, _dictWorkflow, sWorkflowPath):
        listWrittenPaths.append(sWorkflowPath)

    with patch.object(
        workflowManager, "fnSaveWorkflowToContainer", fnRecordWrite,
    ):
        with pytest.raises(HTTPException) as excinfo:
            dictCtx["save"](S_RESOURCE_ID, _fdictLeftWorkflow())
        dictCtx["save"](S_RESOURCE_ID, _fdictOpenedWorkflow())
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["sRefusal"] == (
        routeContext.S_REFUSAL_PROJECT_SWITCHED
    )
    assert S_WORKFLOW_LEFT in excinfo.value.detail["sMessage"]
    assert listWrittenPaths == [S_WORKFLOW_OPENED]


def testAWorkflowWithNoRecordedFileSavesAsBefore():
    """A workflow built in memory (a new project) is not refused."""
    dictCtx = pipelineServer.fdictBuildContext(MagicMock())
    dictCtx["paths"][S_RESOURCE_ID] = S_WORKFLOW_OPENED
    dictInMemory = _fdictOpenedWorkflow()
    dictInMemory.pop(S_LOADED)
    with patch.object(
        workflowManager, "fnSaveWorkflowToContainer",
    ) as mockWrite:
        dictCtx["save"](S_RESOURCE_ID, dictInMemory)
    assert mockWrite.call_args.args[3] == S_WORKFLOW_OPENED


@pytest.mark.falsification
def testTheCarriedSaveRefusesBeforeAnyJournalRecordOpens():
    """The refusal precedes the carrier, so no record is left in flight.

    Kills: relying on the plain save's refusal alone, which raises
    INSIDE the carrier's effect and leaves its journal record
    IN_FLIGHT for a write that never started.
    """
    dictCtx = {"paths": {S_RESOURCE_ID: S_WORKFLOW_OPENED}}
    with patch.object(
        routeContext, "fdictRequireLaneTupleForCommit",
        return_value={"sContainerName": S_CONTAINER_NAME},
    ), patch(
        "vaibify.gui.commitCarrier.fdictCommitSynchronousMutation",
    ) as mockCommit:
        with pytest.raises(HTTPException) as excinfo:
            routeContext.fdictCommitWorkflowSave(
                dictCtx, S_RESOURCE_ID, _fdictLeftWorkflow(),
                MagicMock(), "a test save",
            )
    assert excinfo.value.status_code == 409
    mockCommit.assert_not_called()


# ---------------------------------------------------------------------------
# The file-change baseline belongs to the project it measured
# ---------------------------------------------------------------------------


def _fdictModTimesOf(dictWorkflow, sMtime):
    sRepo = dictWorkflow["sProjectRepoPath"]
    sStep = dictWorkflow["listSteps"][0]["sDirectory"]
    return {f"{sRepo}/{sStep}/result.dat": sMtime}


def _fdictInvalidate(dictCtx, dictWorkflow, dictModTimes):
    return _fdictDetectAndInvalidate(
        dictCtx, S_RESOURCE_ID, dictWorkflow, dictModTimes,
        bPipelineRunning=False,
    )


def testControlAChangedOutputInTheSameProjectIsStillCaught():
    """Without this, the switch test below could pass by detecting nothing."""
    dictOpened = _fdictOpenedWorkflow()
    dictCtx = {"save": MagicMock()}
    _fdictInvalidate(dictCtx, dictOpened, _fdictModTimesOf(dictOpened, "100"))
    dictInvalidated = _fdictInvalidate(
        dictCtx, dictOpened, _fdictModTimesOf(dictOpened, "200"),
    )
    assert dictInvalidated
    assert dictOpened["listSteps"][0]["dictVerification"]["sUnitTest"] == (
        "untested"
    )


@pytest.mark.falsification
def testSwitchingProjectsDoesNotResetTheNewProjectsTests():
    """The left project's baseline is no baseline for the opened one.

    Kills: comparing against a baseline measured for another project,
    where every file of the new project is absent and so reads as
    changed -- the opened project's passed tests reset and SAVED on
    nothing more than a click on the project switcher.
    """
    dictLeft = _fdictLeftWorkflow()
    dictOpened = _fdictOpenedWorkflow()
    mockSave = MagicMock()
    dictCtx = {"save": mockSave}
    _fdictInvalidate(dictCtx, dictLeft, _fdictModTimesOf(dictLeft, "100"))
    dictInvalidated = _fdictInvalidate(
        dictCtx, dictOpened, _fdictModTimesOf(dictOpened, "100"),
    )
    assert dictInvalidated == {}
    assert dictOpened["listSteps"][0]["dictVerification"]["sUnitTest"] == (
        "passed"
    )
    mockSave.assert_not_called()


def testALatePollOfTheLeftProjectDoesNotPoisonTheOpenedBaseline():
    """A poll that finishes after the switch must not become the baseline."""
    dictLeft = _fdictLeftWorkflow()
    dictOpened = _fdictOpenedWorkflow()
    dictCtx = {"save": MagicMock()}
    _fdictInvalidate(dictCtx, dictOpened, _fdictModTimesOf(dictOpened, "100"))
    _fdictInvalidate(dictCtx, dictLeft, _fdictModTimesOf(dictLeft, "100"))
    assert _fdictInvalidate(
        dictCtx, dictOpened, _fdictModTimesOf(dictOpened, "100"),
    ) == {}


# ---------------------------------------------------------------------------
# A run executes in its own project's directories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.falsification
async def testARunUsesItsOwnProjectsDirectoryNotTheSockets():
    """The socket opened in the left project; the opened project's run runs there.

    Kills: dispatching with the directory captured at socket accept --
    which ran the opened project's commands in the left project's step
    directories and wrote its figures into the left project's Plot.
    """
    listDispatches = []

    async def fnRecordingDispatch(
        sAction, dictRequest, connectionDocker, sContainerId,
        dictWorkflow, dictWorkflowPathCache, sWorkflowDirectory,
        *args, **kwargs,
    ):
        listDispatches.append((
            dictWorkflowPathCache.get(sContainerId), sWorkflowDirectory,
            dictWorkflow["sProjectRepoPath"],
        ))

    websocketFake = _FakeRunSocket([{"sAction": "runAll"}])
    dictOpened = _fdictOpenedWorkflow()
    with patch.object(pipelineServer, "fnDispatchAction", fnRecordingDispatch):
        with pytest.raises(WebSocketDisconnect):
            await pipelineServer.fnPipelineMessageLoop(
                websocketFake, MagicMock(), S_RESOURCE_ID,
                _fdictLeftWorkflow(),
                {S_RESOURCE_ID: S_WORKFLOW_OPENED},
                posixpath.dirname(S_WORKFLOW_LEFT),
                dictPipelineTasks={},
                fdictGetLiveWorkflow=lambda: dictOpened,
            )
        for _ in range(3):
            import asyncio
            await asyncio.sleep(0)
    assert listDispatches == [(
        S_WORKFLOW_OPENED, posixpath.dirname(S_WORKFLOW_OPENED),
        S_REPO_OPENED,
    )]


# ---------------------------------------------------------------------------
# A run's provenance lands in the run's project
# ---------------------------------------------------------------------------


def _fdictProvenanceWorkflow(sRepoPath, sWorkflowPath):
    return {
        "sProjectRepoPath": sRepoPath,
        "listSteps": [{
            "sStepId": "pull-archive", "sName": "Pull Archive",
            "sDirectory": "PullArchive",
            "saPlotCommands": [], "saPlotFiles": [],
            "listRemoteData": [{
                "sPath": "data/pull.fits",
                "sSourceUrl": "https://archive.example/query",
            }],
        }],
        S_LOADED: sWorkflowPath,
    }


@pytest.mark.falsification
def testARunsProvenanceLandsInItsProjectAfterASwitch():
    """Same step id in both projects: the digest must reach the run's own.

    Kills: resolving the target from the open project at commit time,
    which installed the left run's checksum into the opened project's
    project.json -- a record claiming a download that never happened
    there.
    """
    dictOpenedCache = _fdictProvenanceWorkflow(S_REPO_OPENED, S_WORKFLOW_OPENED)
    dictLeftOnDisk = _fdictProvenanceWorkflow(S_REPO_LEFT, S_WORKFLOW_LEFT)
    mockOpenSave = MagicMock()
    dictCtx = {
        "docker": MagicMock(),
        "paths": {S_RESOURCE_ID: S_WORKFLOW_OPENED},
        "workflows": {S_RESOURCE_ID: dictOpenedCache},
        "save": mockOpenSave,
    }
    listWrites = []

    def fnRecordWrite(_docker, _sId, dictWorkflow, sWorkflowPath):
        listWrites.append((sWorkflowPath, dictWorkflow))

    with patch.object(
        workflowManager, "fdictLoadWorkflowFromContainer",
        return_value=dictLeftOnDisk,
    ) as mockLoad, patch.object(
        workflowManager, "fnSaveWorkflowToContainer", fnRecordWrite,
    ):
        dictResult = fdictCommitRemoteDataRecords(
            dictCtx, S_RESOURCE_ID, "pull-archive", [{
                "sPath": "data/pull.fits",
                "sSourceUrl": "https://archive.example/query",
                "sSha256": "c" * 64,
                "sDigestBecameCurrentUtc": "2026-09-24T01:00:00Z",
            }],
            sRunWorkflowPath=S_WORKFLOW_LEFT,
        )
    assert dictResult["bCommitted"] is True
    assert mockLoad.call_args.args[2] == S_WORKFLOW_LEFT
    assert [sPath for sPath, _ in listWrites] == [S_WORKFLOW_LEFT]
    assert listWrites[0][1]["listSteps"][0]["listRemoteData"][0][
        "sSha256"] == "c" * 64
    mockOpenSave.assert_not_called()
    assert "sSha256" not in (
        dictOpenedCache["listSteps"][0]["listRemoteData"][0]
    )


# ---------------------------------------------------------------------------
# A poll that began before the switch cannot rebind the cache
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testALatePollNeverReloadsTheLeftProjectIntoTheCache():
    """Only the open project's file may be reloaded into the open slot.

    Kills: reloading whatever path the poll carried, which put the left
    project's workflow in the cache under the opened project's path.
    """
    dictOpened = _fdictOpenedWorkflow()
    dictCtx = {
        "docker": MagicMock(),
        "paths": {S_RESOURCE_ID: S_WORKFLOW_OPENED},
        "workflows": {S_RESOURCE_ID: dictOpened},
        "lastSelfWriteFingerprints": {S_RESOURCE_ID: "a" * 64},
    }
    with patch.object(
        workflowManager, "fdictLoadWorkflowFromContainer",
    ) as mockLoad:
        dictResult = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, S_RESOURCE_ID, S_WORKFLOW_LEFT,
            {S_WORKFLOW_LEFT: "100"}, sPolledFingerprint="b" * 64,
        )
    assert dictResult["bReplaced"] is False
    mockLoad.assert_not_called()
    assert dictCtx["workflows"][S_RESOURCE_ID] is dictOpened


def testThePollDescribesTheWorkflowItHoldsNotTheOpenSlot():
    """A poll holding the left workflow names the left file."""
    dictCtx = {"paths": {S_RESOURCE_ID: S_WORKFLOW_OPENED}}
    assert pipelineRoutes._fsWorkflowPathOfPoll(
        dictCtx, S_RESOURCE_ID, _fdictLeftWorkflow(),
    ) == S_WORKFLOW_LEFT


# ---------------------------------------------------------------------------
# The dashboard hears about the other project's run
# ---------------------------------------------------------------------------


class _LiveTaskStub:
    """A running pipeline task the registry can stamp."""

    def __init__(self):
        self.bCancelled = False

    def done(self):
        return False

    def cancel(self):
        self.bCancelled = True
        return True

    def add_done_callback(self, _fnCallback):
        return None


def _fdictTasksRunning(
    dictWorkflow, sContainerId=S_RESOURCE_ID, dictPipelineTasks=None,
    sRunId="",
):
    dictPipelineTasks = {} if dictPipelineTasks is None else dictPipelineTasks
    pipelineRunSlots.fnRegisterRun(
        dictPipelineTasks, sContainerId, _LiveTaskStub(),
        dictWorkflow=dictWorkflow, sRunId=sRunId,
    )
    return dictPipelineTasks


@pytest.mark.falsification
def testTheOpenProjectIsToldHowManyOtherProjectsAreRunning():
    """Kills: reporting only the open project's own run state."""
    dictPipelineTasks = _fdictTasksRunning(_fdictLeftWorkflow())
    dictThird = _fdictWorkflow(
        "/workspace/projectThird",
        "/workspace/projectThird/.vaibify/projects/third.json",
        "thirdModel.py", "ThirdStep",
    )
    _fdictTasksRunning(dictThird, dictPipelineTasks=dictPipelineTasks)
    _fdictTasksRunning(
        _fdictOpenedWorkflow(), dictPipelineTasks=dictPipelineTasks,
    )
    dictCtx = {"pipelineTasks": dictPipelineTasks}
    dictOthers = pipelineRoutes._fdictOtherProjectRunsForWire(
        dictCtx, S_RESOURCE_ID, _fdictOpenedWorkflow(),
    )
    assert dictOthers["iRunningProjectCount"] == 2
    assert sorted(
        dictProject["sProjectRepoPath"]
        for dictProject in dictOthers["listRunningProjects"]
    ) == sorted(["/workspace/projectThird", S_REPO_LEFT])
    assert pipelineRoutes._fdictOtherProjectRunsForWire(
        {"pipelineTasks": _fdictTasksRunning(_fdictOpenedWorkflow())},
        S_RESOURCE_ID, _fdictOpenedWorkflow(),
    ) == {}


# ---------------------------------------------------------------------------
# Stop ends the open project's run, and only its processes
# ---------------------------------------------------------------------------


def _fdictStopWithRuns(appHub, clientBrowser, dictPipelineTasks):
    """Open the OPENED project, install the runs, press Stop; return all."""
    sLeaseId = _fsClaimAndReturnLease(clientBrowser, S_CONTAINER_NAME)
    dictCtx = appHub.state.dictRouteContext
    dictCtx["workflows"][S_CONTAINER_ID] = _fdictOpenedWorkflow()
    dictCtx["paths"][S_CONTAINER_ID] = S_WORKFLOW_OPENED
    dictCtx["pipelineTasks"].update(dictPipelineTasks)
    dictCalls = {"listRunKills": [], "listNameSweeps": []}

    def fiRecordRunKill(_docker, _sId, sRunId):
        dictCalls["listRunKills"].append(sRunId)
        return 1

    def fiRecordNameSweep(_docker, _sId, listPatterns, _sGrep, listSpared):
        dictCalls["listNameSweeps"].append((listPatterns, list(listSpared)))
        return 1

    async def fiRunTheWorker(_dictCtx, _sId, fiKill, _request):
        return fiKill()

    with patch.object(
        pipelineRoutes, "_fiCountThenKillUnderTheDrain", fiRunTheWorker,
    ), patch.object(
        pipelineRoutes, "_fiKillRunProcesses", fiRecordRunKill,
    ), patch.object(
        pipelineRoutes, "_fiCountAndKillMatchingProcesses", fiRecordNameSweep,
    ), patch.object(
        pipelineRoutes, "_fiMarkPipelineStopped", AsyncMock(return_value=1),
    ):
        responseKill = clientBrowser.post(
            f"/api/pipeline/{S_CONTAINER_ID}/kill",
            headers={"X-Vaibify-Lease": sLeaseId},
        )
    assert responseKill.status_code == 200, responseKill.text
    dictCalls["dictResponse"] = responseKill.json()
    return dictCalls


@pytest.mark.falsification
def testStopEndsOnlyTheOpenProjectsRun(appHub, clientBrowser):
    """Two projects running: Stop in one ends it by its marker alone.

    Kills: cancelling or sweeping another project's run from this
    project's Stop -- a researcher stopping one analysis would kill the
    other one running beside it.
    """
    dictPipelineTasks = _fdictTasksRunning(
        _fdictLeftWorkflow(), S_CONTAINER_ID, sRunId="aa11",
    )
    _fdictTasksRunning(
        _fdictOpenedWorkflow(), S_CONTAINER_ID, dictPipelineTasks,
        sRunId="bb22",
    )
    dictSlots = dictPipelineTasks[S_CONTAINER_ID]
    dictCalls = _fdictStopWithRuns(appHub, clientBrowser, dictPipelineTasks)
    assert dictCalls["listRunKills"] == ["bb22"]
    assert dictCalls["listNameSweeps"] == []
    assert dictSlots[S_REPO_OPENED].bCancelled is True
    assert dictSlots[S_REPO_LEFT].bCancelled is False
    assert dictCalls["dictResponse"]["sStoppedWorkflowPath"] == (
        S_WORKFLOW_OPENED
    )


@pytest.mark.falsification
def testANameSweepSparesAnotherProjectsRun(appHub, clientBrowser):
    """No live run here (a restarted hub): the name sweep spares the other run.

    Kills: a name sweep that ignores run markers, which kills another
    project's process whenever the two share a script name.
    """
    dictPipelineTasks = _fdictTasksRunning(
        _fdictLeftWorkflow(), S_CONTAINER_ID, sRunId="aa11",
    )
    dictCalls = _fdictStopWithRuns(appHub, clientBrowser, dictPipelineTasks)
    assert dictCalls["listRunKills"] == []
    [(listPatterns, listSpared)] = dictCalls["listNameSweeps"]
    assert "openedModel.py" in listPatterns
    assert "leftModel.py" not in listPatterns
    assert listSpared == ["aa11"]
    assert dictPipelineTasks[S_CONTAINER_ID][S_REPO_LEFT].bCancelled is False


@pytest.mark.asyncio
@pytest.mark.falsification
async def testThePollNamesItsProjectAndTheOtherRun():
    """Driven through the payload builder, with the open slot naming the other project.

    Kills: labelling the answer with the container's open path, which
    marks a late answer about the left project as the opened project's
    and lets the dashboard apply it.
    """
    import contextlib
    from tests.testRunStateWireCarriesStepResults import _T_EMPTY_POLL_HELPERS
    dictCtx = {
        "docker": MagicMock(),
        "save": MagicMock(),
        "paths": {S_RESOURCE_ID: S_WORKFLOW_OPENED},
        "pipelineTasks": _fdictTasksRunning(_fdictOpenedWorkflow()),
    }
    sModule = "vaibify.gui.routes.pipelineRoutes."
    with contextlib.ExitStack() as stackPatches:
        for sHelper in _T_EMPTY_POLL_HELPERS:
            stackPatches.enter_context(
                patch(sModule + sHelper, return_value={}),
            )
        stackPatches.enter_context(patch(
            sModule + "_flistCollectOutputPaths", return_value=[],
        ))
        stackPatches.enter_context(patch(
            sModule + "ftGetModTimesAndFingerprint", return_value=({}, ""),
        ))
        stackPatches.enter_context(patch(
            sModule + "_fbCheckStaleUserVerification", return_value=False,
        ))
        stackPatches.enter_context(patch(
            sModule + "_fdictReconcilePipelineState",
            new=AsyncMock(return_value={}),
        ))
        dictPayload = await pipelineRoutes._fdictFetchOutputStatus(
            dictCtx, S_RESOURCE_ID, _fdictLeftWorkflow(), {},
        )
    assert dictPayload["sServedWorkflowPath"] == S_WORKFLOW_LEFT
    assert dictPayload["dictOtherProjectRuns"]["listRunningProjects"][0][
        "sWorkflowPath"] == S_WORKFLOW_OPENED
