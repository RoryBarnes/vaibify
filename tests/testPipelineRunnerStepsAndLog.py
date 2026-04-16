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
