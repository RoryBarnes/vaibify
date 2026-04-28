"""Coverage tests for vaibify.gui.pipelineRunner._fiRunStepsAndLog."""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fMockDocker():
    """Return a mock Docker connection that silently accepts writes."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"{}"
    return mockDocker


def _fMockCallbacks():
    """Return an async logging and status callback pair."""
    listLoggingEvents = []
    listStatusEvents = []

    async def fnLogging(dictEvent):
        listLoggingEvents.append(dictEvent)

    async def fnStatus(dictEvent):
        listStatusEvents.append(dictEvent)

    return fnLogging, fnStatus, listLoggingEvents, listStatusEvents


@patch(
    "vaibify.gui.pipelineRunner._fnFinalizeRun",
    new_callable=AsyncMock,
)
@patch(
    "vaibify.gui.pipelineRunner._fiRunStepList",
    new_callable=AsyncMock,
    return_value=0,
)
def test_fiRunStepsAndLog_success(mockRunStepList, mockFinalize):
    """Successful run: writes initial state, emits started, returns 0."""
    from vaibify.gui.pipelineRunner import _fiRunStepsAndLog
    mockDocker = _fMockDocker()
    fnLogging, fnStatus, listLog, _ = _fMockCallbacks()
    dictWorkflow = {
        "sWorkflowName": "demo",
        "listSteps": [{"sName": "s1"}, {"sName": "s2"}],
    }
    iResult = _fnRunAsync(_fiRunStepsAndLog(
        mockDocker, "cid1", dictWorkflow, "/work",
        {}, fnLogging, fnStatus,
        "/logs/run.log", [], "runAll", 1,
    ))
    assert iResult == 0
    mockRunStepList.assert_called_once()
    mockFinalize.assert_called_once()
    assert mockDocker.fnWriteFile.called
    assert any(e.get("sType") == "started" for e in listLog)


@patch(
    "vaibify.gui.pipelineRunner._fnFinalizeRun",
    new_callable=AsyncMock,
)
@patch(
    "vaibify.gui.pipelineRunner._fiRunStepList",
    new_callable=AsyncMock,
    return_value=7,
)
def test_fiRunStepsAndLog_propagates_nonzero_exit(
    mockRunStepList, mockFinalize,
):
    """Non-zero exit code from _fiRunStepList is returned and finalized."""
    from vaibify.gui.pipelineRunner import _fiRunStepsAndLog
    mockDocker = _fMockDocker()
    fnLogging, fnStatus, _, _ = _fMockCallbacks()
    dictWorkflow = {"sWorkflowName": "demo", "listSteps": [{}]}
    iResult = _fnRunAsync(_fiRunStepsAndLog(
        mockDocker, "cid1", dictWorkflow, "/work",
        {}, fnLogging, fnStatus,
        "/logs/run.log", [], "runAll", 1,
    ))
    assert iResult == 7
    tArgsFinalize, _ = mockFinalize.call_args
    assert tArgsFinalize[3] == 7


@patch(
    "vaibify.gui.pipelineRunner._fnFinalizeRun",
    new_callable=AsyncMock,
)
@patch(
    "vaibify.gui.pipelineRunner._fiRunStepList",
    new_callable=AsyncMock,
    return_value=0,
)
def test_fiRunStepsAndLog_forwards_step_count(
    mockRunStepList, mockFinalize,
):
    """Initial state is built using the number of steps in the workflow."""
    from vaibify.gui import pipelineState
    from vaibify.gui.pipelineRunner import _fiRunStepsAndLog
    mockDocker = _fMockDocker()
    fnLogging, fnStatus, _, _ = _fMockCallbacks()
    dictWorkflow = {
        "sWorkflowName": "demo",
        "listSteps": [{}, {}, {}],
    }
    with patch.object(
        pipelineState, "fdictBuildInitialState",
        wraps=pipelineState.fdictBuildInitialState,
    ) as spyState:
        _fnRunAsync(_fiRunStepsAndLog(
            mockDocker, "cid1", dictWorkflow, "/work",
            {}, fnLogging, fnStatus,
            "/logs/run.log", [], "runAll", 1,
        ))
    tArgs, _ = spyState.call_args
    assert tArgs[0] == "runAll"
    assert tArgs[1] == "/logs/run.log"
    assert tArgs[2] == 3


@patch(
    "vaibify.gui.pipelineRunner._fnFinalizeRun",
    new_callable=AsyncMock,
)
@patch(
    "vaibify.gui.pipelineRunner._fiRunStepList",
    new_callable=AsyncMock,
    return_value=0,
)
def test_fiRunStepsAndLog_passes_interactive_context(
    mockRunStepList, mockFinalize,
):
    """dictInteractive is threaded through to _fiRunStepList."""
    from vaibify.gui.pipelineRunner import _fiRunStepsAndLog
    mockDocker = _fMockDocker()
    fnLogging, fnStatus, _, _ = _fMockCallbacks()
    dictInteractive = {"sStepName": "pause"}
    dictWorkflow = {"sWorkflowName": "demo", "listSteps": [{}]}
    _fnRunAsync(_fiRunStepsAndLog(
        mockDocker, "cid1", dictWorkflow, "/work",
        {}, fnLogging, fnStatus,
        "/logs/run.log", [], "runFrom", 2,
        sWorkflowPath="/wf.json",
        dictInteractive=dictInteractive,
    ))
    _, dictKwargs = mockRunStepList.call_args
    assert dictKwargs["iStartStep"] == 2
    assert dictKwargs["dictInteractive"] is dictInteractive


# ---------------------------------------------------------------------------
# Runner liveness contract: PID stamping + heartbeat thread
# ---------------------------------------------------------------------------


def _fdictParseWrittenState(mockDocker):
    """Decode the most recent JSON blob written to fnWriteFile."""
    import json
    listCalls = mockDocker.fnWriteFile.call_args_list
    assert listCalls, "expected at least one fnWriteFile call"
    bytesPayload = listCalls[-1][0][2]
    return json.loads(bytesPayload.decode("utf-8"))


@patch(
    "vaibify.gui.pipelineRunner._fnFinalizeRun",
    new_callable=AsyncMock,
)
@patch(
    "vaibify.gui.pipelineRunner._fiRunStepList",
    new_callable=AsyncMock,
    return_value=0,
)
def test_fiRunStepsAndLog_stamps_runner_pid(
    mockRunStepList, mockFinalize,
):
    """The initial state file contains the runner's PID and a heartbeat."""
    import os
    from vaibify.gui.pipelineRunner import _fiRunStepsAndLog
    mockDocker = _fMockDocker()
    fnLogging, fnStatus, _, _ = _fMockCallbacks()
    dictWorkflow = {"sWorkflowName": "demo", "listSteps": [{}]}
    _fnRunAsync(_fiRunStepsAndLog(
        mockDocker, "cid1", dictWorkflow, "/work",
        {}, fnLogging, fnStatus,
        "/logs/run.log", [], "runAll", 1,
    ))
    dictWritten = _fdictParseWrittenState(mockDocker)
    assert dictWritten["iRunnerPid"] == os.getpid()
    assert dictWritten["sLastHeartbeat"]
    assert dictWritten["sFailureReason"] == ""


@patch(
    "vaibify.gui.pipelineRunner._fnFinalizeRun",
    new_callable=AsyncMock,
)
@patch(
    "vaibify.gui.pipelineRunner._fiRunStepList",
    new_callable=AsyncMock,
    return_value=0,
)
def test_fiRunStepsAndLog_starts_heartbeat_thread(
    mockRunStepList, mockFinalize,
):
    """A daemon heartbeat thread is started and joins on completion."""
    from vaibify.gui import pipelineRunner
    from vaibify.gui.pipelineRunner import _fiRunStepsAndLog
    mockDocker = _fMockDocker()
    fnLogging, fnStatus, _, _ = _fMockCallbacks()
    dictWorkflow = {"sWorkflowName": "demo", "listSteps": [{}]}
    listStartedThreads = []

    def fnSpyStart(*args, **kwargs):
        threadHeartbeat = pipelineRunner._fnStartHeartbeatThread.__wrapped__(
            *args, **kwargs)
        listStartedThreads.append(threadHeartbeat)
        return threadHeartbeat

    fnOriginal = pipelineRunner._fnStartHeartbeatThread
    fnSpyStart.__wrapped__ = fnOriginal
    pipelineRunner._fnStartHeartbeatThread.__wrapped__ = fnOriginal
    with patch(
        "vaibify.gui.pipelineRunner._fnStartHeartbeatThread",
        side_effect=lambda *a, **kw: (
            listStartedThreads.append(fnOriginal(*a, **kw))
            or listStartedThreads[-1]
        ),
    ):
        _fnRunAsync(_fiRunStepsAndLog(
            mockDocker, "cid1", dictWorkflow, "/work",
            {}, fnLogging, fnStatus,
            "/logs/run.log", [], "runAll", 1,
        ))
    assert listStartedThreads, "heartbeat thread was not started"
    threadHeartbeat = listStartedThreads[0]
    assert threadHeartbeat.daemon is True
    # The thread is joined inside the finally block of _fiRunStepsAndLog.
    assert not threadHeartbeat.is_alive()


def test_fnRunHeartbeatLoop_exits_promptly_on_event():
    """The heartbeat loop stops within one interval after the event is set."""
    import threading
    import time
    from vaibify.gui.pipelineRunner import _fnRunHeartbeatLoop
    from vaibify.gui import pipelineState
    mockDocker = _fMockDocker()
    dictState = pipelineState.fdictBuildInitialState(
        "runAll", "/log", 1, iRunnerPid=12345
    )
    lockState = threading.Lock()
    eventStop = threading.Event()
    threadHeartbeat = threading.Thread(
        target=_fnRunHeartbeatLoop,
        args=(mockDocker, "cid1", dictState, lockState, eventStop),
        daemon=True,
    )
    threadHeartbeat.start()
    eventStop.set()
    threadHeartbeat.join(timeout=2)
    assert not threadHeartbeat.is_alive()


def test_fnRunHeartbeatLoop_writes_updated_heartbeat():
    """One iteration of the heartbeat loop writes a refreshed sLastHeartbeat."""
    import threading
    from datetime import datetime, timezone
    from vaibify.gui.pipelineRunner import _fnRunHeartbeatLoop
    from vaibify.gui import pipelineState
    mockDocker = _fMockDocker()
    dictState = pipelineState.fdictBuildInitialState(
        "runAll", "/log", 1, iRunnerPid=12345
    )
    sOriginalBeat = dictState["sLastHeartbeat"]
    lockState = threading.Lock()
    eventStop = threading.Event()
    # Patch the interval to 0.05 s so the loop fires once before we stop it.
    with patch.object(
        pipelineState, "I_HEARTBEAT_INTERVAL_SECONDS", 0.05
    ):
        threadHeartbeat = threading.Thread(
            target=_fnRunHeartbeatLoop,
            args=(mockDocker, "cid1", dictState, lockState, eventStop),
            daemon=True,
        )
        threadHeartbeat.start()
        # Give the loop time to write at least once before stopping.
        import time
        time.sleep(0.2)
        eventStop.set()
        threadHeartbeat.join(timeout=2)
    assert mockDocker.fnWriteFile.called
    assert dictState["sLastHeartbeat"] != sOriginalBeat
    dtUpdated = datetime.fromisoformat(dictState["sLastHeartbeat"])
    dtOriginal = datetime.fromisoformat(sOriginalBeat)
    assert dtUpdated >= dtOriginal
