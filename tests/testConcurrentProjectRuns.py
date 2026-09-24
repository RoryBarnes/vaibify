"""Several projects in one container may run their pipelines at once.

The container's CPU and memory are shared, so a second project's run
is the researcher's decision: the dashboard asks before starting it and
``vaibify-do`` prints the same notice. Two runs of ONE project never
overlap. What must stay true with several runs live:

- the container still holds ONE durable record, the unit ownership
  transfer, release and the reaper reason about, for as long as any
  project's run is live -- and a transfer adopts every run's commands;
- a run's commands carry its own process marker, so a Stop ends that
  run and never another project's.

The rules for this file: container id != container name, projects with
distinct directories, and each falsification test names its mutation.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vaibify.config import mutationAdmission, operationJournal
from vaibify.gui import commitCarrier
from vaibify.gui import pipelineRunner
from vaibify.gui import pipelineRunSlots
from vaibify.gui import pipelineServer
from vaibify.gui import sessionLifecycle
from tests.testHostTransfer import (  # noqa: F401 -- autouse fixtures
    S_CONTAINER_ID,
    S_PROJECT_NAME,
    _dictBuildBrowserLaneTuple,
    _fsMintTransferCapability,
    _fstateBuildAppState,
    _tSeedOwnedContainer,
    _tTransfer,
    fixtureIsolateLockDirectory,
    fixtureShortTransferWaits,
)
from tests.testProjectScopedAgentActions import _FakeRunSocket


S_REPO_FIRST = "/workspace/firstProject"
S_REPO_SECOND = "/workspace/secondProject"
S_KIND = pipelineRunSlots.S_JOINABLE_PIPELINE_WORK


async def _fdictLaunch(stateApp, dictLaneTuple, fnStart, sMemberKey,
                       sJoinableKind=S_KIND):
    return await commitCarrier.fdictLaunchDurableTask(
        stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple, fnStart,
        sOperation="a pipeline run", sJoinableKind=sJoinableKind,
        sMemberKey=sMemberKey,
    )


def _ftSeededCarrier():
    stateApp = _fstateBuildAppState()
    sSessionId, _, sLease = _tSeedOwnedContainer(stateApp)
    return stateApp, _dictBuildBrowserLaneTuple(stateApp, sSessionId, sLease)


async def _fnSettleCallbacks():
    for _ in range(4):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# The carrier: one durable record, several projects' runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.falsification
async def testRunsOfTwoProjectsShareOneDurableRecord():
    """The second project's run joins; the record lives until the last ends.

    Kills: refusing every launch beside a live pipeline record, which
    is one run per container -- the rule this change retires.
    """
    stateApp, dictLaneTuple = _ftSeededCarrier()
    eventFirst, eventSecond = asyncio.Event(), asyncio.Event()
    dictFirst = await _fdictLaunch(
        stateApp, dictLaneTuple,
        lambda: asyncio.ensure_future(eventFirst.wait()), S_REPO_FIRST,
    )
    dictSecond = await _fdictLaunch(
        stateApp, dictLaneTuple,
        lambda: asyncio.ensure_future(eventSecond.wait()), S_REPO_SECOND,
    )
    assert dictSecond["bLaunched"] is True
    assert dictSecond["sTaskId"] == dictFirst["sTaskId"]
    assert dictSecond["taskAsync"] is not dictFirst["taskAsync"]
    recordTask = stateApp.dictDurableTaskRecords[S_PROJECT_NAME]
    eventFirst.set()
    await dictFirst["taskAsync"]
    await _fnSettleCallbacks()
    assert S_PROJECT_NAME in stateApp.dictDurableTaskRecords, (
        "the record ended while the second project was still running"
    )
    assert commitCarrier.fbContainerHasLiveMutationWork(
        stateApp, S_PROJECT_NAME,
    )
    eventSecond.set()
    await recordTask.taskAsync
    await _fnSettleCallbacks()
    assert S_PROJECT_NAME not in stateApp.dictDurableTaskRecords


@pytest.mark.asyncio
@pytest.mark.falsification
async def testTheSameProjectNeverJoinsItsOwnLiveRun():
    """The carrier's backstop for the dispatch check: one run per project.

    Kills: admitting any member key, which lets two sockets start the
    same project twice -- two runs writing one state file and one set
    of outputs.
    """
    stateApp, dictLaneTuple = _ftSeededCarrier()
    eventHeld = asyncio.Event()
    await _fdictLaunch(
        stateApp, dictLaneTuple,
        lambda: asyncio.ensure_future(eventHeld.wait()), S_REPO_FIRST,
    )
    dictAgain = await _fdictLaunch(
        stateApp, dictLaneTuple,
        lambda: asyncio.ensure_future(asyncio.sleep(0)), S_REPO_FIRST,
    )
    assert dictAgain["bLaunched"] is False
    eventHeld.set()
    await stateApp.dictDurableTaskRecords[S_PROJECT_NAME].taskAsync


@pytest.mark.asyncio
@pytest.mark.falsification
async def testOtherDurableWorkNeverJoinsARun():
    """Only work of the same joinable kind joins; everything else waits.

    Kills: joining whatever record is live, which would start a run
    beside an environment archive or a reproducibility rerun that owns
    the whole container.
    """
    stateApp, dictLaneTuple = _ftSeededCarrier()
    eventHeld = asyncio.Event()
    await commitCarrier.fdictLaunchDurableTask(
        stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
        lambda: asyncio.ensure_future(eventHeld.wait()),
        sOperation="an environment archive",
    )
    dictRun = await _fdictLaunch(
        stateApp, dictLaneTuple,
        lambda: asyncio.ensure_future(asyncio.sleep(0)), S_REPO_FIRST,
    )
    assert dictRun["bLaunched"] is False
    assert "environment archive" in dictRun["sReason"]
    eventHeld.set()
    await stateApp.dictDurableTaskRecords[S_PROJECT_NAME].taskAsync
    await _fnSettleCallbacks()
    eventRun = asyncio.Event()
    await _fdictLaunch(
        stateApp, dictLaneTuple,
        lambda: asyncio.ensure_future(eventRun.wait()), S_REPO_FIRST,
    )
    dictArchive = await commitCarrier.fdictLaunchDurableTask(
        stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
        lambda: asyncio.ensure_future(asyncio.sleep(0)),
        sOperation="an environment archive",
    )
    assert dictArchive["bLaunched"] is False
    eventRun.set()
    await stateApp.dictDurableTaskRecords[S_PROJECT_NAME].taskAsync


def _ffnJournaledMember(eventRelease, sExecId):
    """Return a member that holds one journaled exec open until released."""
    async def fnHoldAnExec():
        dictHandle = mutationAdmission.fdictBeginJournaledExec(S_CONTAINER_ID)
        mutationAdmission.fnPromoteJournaledExec(dictHandle, sExecId)
        await eventRelease.wait()
        mutationAdmission.fnSettleJournaledExec(dictHandle)
    return lambda: asyncio.ensure_future(fnHoldAnExec())


@pytest.mark.asyncio
@pytest.mark.falsification
async def testATransferAdoptsEveryProjectsRunningCommand():
    """Two projects each mid-command: the transfer adopts both records.

    Kills: adopting only the admission's latest exec record, which reads
    the other project's command as an orphan and refuses the transfer
    with advice to run 'vaibify reconcile' over healthy work.
    """
    stateApp, dictLaneTuple = _ftSeededCarrier()
    eventFirst, eventSecond = asyncio.Event(), asyncio.Event()
    await _fdictLaunch(
        stateApp, dictLaneTuple,
        _ffnJournaledMember(eventFirst, "exec-first"), S_REPO_FIRST,
    )
    await _fdictLaunch(
        stateApp, dictLaneTuple,
        _ffnJournaledMember(eventSecond, "exec-second"), S_REPO_SECOND,
    )
    await _fnSettleCallbacks()
    recordTask = stateApp.dictDurableTaskRecords[S_PROJECT_NAME]
    assert len(mutationAdmission.fsetActiveExecOperationIds(
        recordTask.admission,
    )) == 2
    sOutcome, dictPayload = await _tTransfer(
        stateApp, _fsMintTransferCapability(stateApp),
    )
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED, dictPayload
    eventFirst.set()
    eventSecond.set()
    await recordTask.taskAsync
    assert mutationAdmission.fsetActiveExecOperationIds(
        recordTask.admission,
    ) == set()


@pytest.mark.asyncio
@pytest.mark.falsification
async def testATransferRetagsEveryProjectsRun():
    """Each run's task carries the successor generation, not just the record.

    Kills: retagging only the record's aggregate task, which leaves each
    run's completion attributed to the owner the container was taken
    from.
    """
    stateApp, dictLaneTuple = _ftSeededCarrier()
    eventHeld = asyncio.Event()
    dictPipelineTasks = {}
    for sRepo in (S_REPO_FIRST, S_REPO_SECOND):
        dictLaunch = await _fdictLaunch(
            stateApp, dictLaneTuple,
            lambda: asyncio.ensure_future(eventHeld.wait()), sRepo,
        )
        pipelineRunSlots.fnRegisterRun(
            dictPipelineTasks, S_CONTAINER_ID, dictLaunch["taskAsync"],
            iOwnerGeneration=dictLaunch["iOwnerGeneration"],
            dictWorkflow={"sProjectRepoPath": sRepo},
        )
    sOutcome, _ = await _tTransfer(
        stateApp, _fsMintTransferCapability(stateApp),
    )
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert [
        taskRun.iOwnerGeneration
        for taskRun in dictPipelineTasks[S_CONTAINER_ID].values()
    ] == [2, 2]
    eventHeld.set()
    await stateApp.dictDurableTaskRecords[S_PROJECT_NAME].taskAsync


# ---------------------------------------------------------------------------
# Dispatch: another project's run is announced, then allowed
# ---------------------------------------------------------------------------


class _FakeLimitedDocker:
    """A container with a one-CPU, five-gigabyte limit."""

    def fcontainerGetById(self, _sContainerId):
        return SimpleNamespace(
            reload=lambda: None,
            attrs={"HostConfig": {
                "NanoCpus": 1_000_000_000, "Memory": 5 * 1024 ** 3,
            }},
        )


class _HeldRun:
    def done(self):
        return False

    def add_done_callback(self, _fnCallback):
        return None


async def _ftDispatchBesideARun(dictFrame):
    """Send one run frame for the second project while the first runs."""
    dictPipelineTasks = {}
    pipelineRunSlots.fnRegisterRun(
        dictPipelineTasks, S_CONTAINER_ID, _HeldRun(),
        dictWorkflow={
            "sProjectRepoPath": S_REPO_FIRST, "sWorkflowName": "First",
        },
    )
    listDispatched = []

    async def fnRecordDispatch(sAction, *args, **kwargs):
        listDispatched.append(sAction)

    websocketFake = _FakeRunSocket([dictFrame])
    with patch.object(pipelineServer, "fnDispatchAction", fnRecordDispatch):
        with pytest.raises(Exception):
            await pipelineServer.fnPipelineMessageLoop(
                websocketFake, _FakeLimitedDocker(), S_CONTAINER_ID,
                {"sWorkflowName": "Second", "sProjectRepoPath": S_REPO_SECOND,
                 "listSteps": []},
                {S_CONTAINER_ID: S_REPO_SECOND + "/.vaibify/projects/s.json"},
                S_REPO_SECOND, dictPipelineTasks=dictPipelineTasks,
            )
        await _fnSettleCallbacks()
    return websocketFake.listSent, listDispatched


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAnotherProjectsRunIsAnnouncedBeforeAnythingStarts():
    """Unacknowledged: refused with the notice. Acknowledged: warned, run.

    Kills: starting beside another project's run without asking, which
    silently halves both runs' share of a one-CPU container.
    """
    listSent, listDispatched = await _ftDispatchBesideARun(
        {"sAction": "runAll"},
    )
    assert listDispatched == []
    [dictRefusal] = listSent
    assert dictRefusal["sReason"] == pipelineRunSlots.S_REFUSAL_CONCURRENT_RUN
    assert dictRefusal["iRunningProjectCount"] == 1
    assert "First" in dictRefusal["sMessage"]
    assert "1 CPU and 5 GB of memory" in dictRefusal["sMessage"]
    listSent, listDispatched = await _ftDispatchBesideARun({
        "sAction": "runAll",
        pipelineRunSlots.S_ACKNOWLEDGE_CONCURRENT_RUN_FIELD: True,
    })
    assert listDispatched == ["runAll"]
    assert listSent[0]["sType"] == "concurrentRunWarning"


def testTheCpuLimitIsTheQuotaNotTheCoreCount():
    """Either way Docker records a CPU limit, it is what the warning names."""
    def fdictLimitsOf(dictHostConfig):
        connectionDocker = MagicMock()
        connectionDocker.fcontainerGetById.return_value = SimpleNamespace(
            reload=lambda: None, attrs={"HostConfig": dictHostConfig},
        )
        return pipelineRunSlots.fdictReadContainerLimits(
            connectionDocker, S_CONTAINER_ID,
        )
    assert fdictLimitsOf({"NanoCpus": 2_500_000_000})["fCpuLimit"] == 2.5
    assert fdictLimitsOf(
        {"CpuQuota": 150000, "CpuPeriod": 100000},
    )["fCpuLimit"] == 1.5
    assert fdictLimitsOf({}) == {"fCpuLimit": None, "fMemoryGigabytes": None}


# ---------------------------------------------------------------------------
# Each run's commands carry its marker
# ---------------------------------------------------------------------------


class _RecordingConnection:
    """Record the command text a run hands the container."""

    def __init__(self):
        self.listCommands = []

    def ftRunInContainerStreamedWithChunks(
        self, _sContainerId, sCommand, _fnEmitChunk, sWorkdir=None,
    ):
        self.listCommands.append(sCommand)
        return SimpleNamespace(iExitCode=0, fCpuSeconds=0.0)


@pytest.mark.asyncio
@pytest.mark.falsification
async def testADispatchedRunsCommandsCarryItsMarker():
    """The dispatch sets the marker; every container command exports it.

    Kills: dropping the marker from the command, which leaves Stop
    nothing but command names to find a run by.
    """
    connectionRecording = _RecordingConnection()

    async def fnRunOneCommand(*args, **kwargs):
        await pipelineRunner._ftRunSingleCommand(
            connectionRecording, S_CONTAINER_ID, "python model.py",
            "python model.py", "/workspace/firstProject/Step",
            _fnIgnoreEvent,
        )

    with patch.object(pipelineServer, "fnDispatchAction", fnRunOneCommand):
        await pipelineServer._fnSafeDispatch(
            "runAll", {}, connectionRecording, S_CONTAINER_ID,
            {}, {}, "/workspace", _fnIgnoreEvent, None, sRunId="c0ffee",
        )
    [sCommand] = connectionRecording.listCommands
    assert sCommand.startswith(
        f"export {pipelineRunner.S_RUN_ID_VARIABLE}=c0ffee && ",
    )


async def _fnIgnoreEvent(_dictEvent):
    return None


def testAMarkerIsNeverWrittenFromOutsideTheHub():
    """Only the hub's hex tokens reach the sweep's shell."""
    from vaibify.gui.routes import pipelineRoutes
    with pytest.raises(ValueError):
        pipelineRoutes._fsProcessCarriesRunTest(["ab'; rm -rf ~; '"])
    assert json.dumps(
        pipelineRoutes._fsProcessCarriesRunTest(["ab12"]),
    ).count("VAIBIFY_RUN_ID=(ab12)") == 1
