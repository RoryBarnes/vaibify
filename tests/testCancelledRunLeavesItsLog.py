"""A run records where its log is before it writes one.

``fdictBuildInitialState`` stamps ``sLogPath`` into
``pipeline_state.json`` when the run starts. The log itself was only
written when a step FINISHED, so a run stopped before any step did
left that path naming a file nothing had created -- and the Logs tab
answered "Could not load logs" for that run, permanently. Seen on a
cancelled ``time.sleep`` step: the state named a log timestamped at
the cancel while only the previous run's log was on disk.

The fix is that the log exists from the moment the path is recorded:
the ``started`` event carries a header line, and the flush set
includes ``started`` and ``stepStarted``.

What is deliberately absent is a flush from the cancelled task's own
teardown, which is the only thing that would preserve the output of
the step being cancelled. That write would run inside a carrier
worker that is already unwinding, where an error poisons the run's
journal record and quarantines the container. The cost is stated
rather than paid: a stopped step's buffered output is lost.
"""

import asyncio
import base64
import re

import pytest

from vaibify.gui import pipelineState
from vaibify.gui.pipelineLogger import (
    _ffBuildFlushingCallback,
    ffBuildLoggingCallback,
)

S_CONTAINER_ID = "cid-log-demo"
S_LOG_PATH = "/workspace/repo/.vaibify/logs/Trial_20260813.log"


class ConnectionRecordingLogAppends:
    """Records the log appends, and answers the state write beside them.

    ``fnWriteFile`` is here because a step result also persists
    ``pipeline_state.json`` through the same callback; a double
    without it would fail the ordinary path for a reason that has
    nothing to do with logging. The state write is answered and
    discarded -- ``listAppendCommands`` keeps only the log writer's
    own commands, so an assertion about the log cannot be satisfied
    by some other write happening to be there.
    """

    def __init__(self):
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand, **dictKeywords):
        del sContainerId, dictKeywords
        self.listCommands.append(sCommand)
        return (0, "")

    def fnWriteFile(self, sContainerId, sPath, baContent, **dictKeywords):
        del sContainerId, sPath, baContent, dictKeywords

    @property
    def listAppendCommands(self):
        return [
            sCommand for sCommand in self.listCommands
            if "base64 -d >>" in sCommand
        ]


def _fnDriveEvents(connection, listEvents):
    """Send events through the real logging + flushing callbacks."""
    listLogLines = []
    fnLogging = ffBuildLoggingCallback(
        _fnIgnoreEvent, listLogLines,
    )
    dictState = pipelineState.fdictBuildInitialState(
        "runStep", S_LOG_PATH, 1,
    )
    fnFlushing = _ffBuildFlushingCallback(
        fnLogging, connection, S_CONTAINER_ID, dictState,
        S_LOG_PATH, listLogLines,
        lockState=_LockThatDoesNothing(),
    )

    async def fnSendThemAll():
        for dictEvent in listEvents:
            await fnFlushing(dictEvent)

    asyncio.new_event_loop().run_until_complete(fnSendThemAll())


async def _fnIgnoreEvent(dictEvent):
    """Stand in for the WebSocket status callback."""
    del dictEvent


class _LockThatDoesNothing:
    """The legacy in-line state lock the flushing callback accepts."""

    def __enter__(self):
        return self

    def __exit__(self, objType, objValue, objTraceback):
        return False


def _fsDecodeAppendedText(sCommand):
    """Return the text one log-append command carries.

    The writer base64-encodes the payload so a scientific stdout line
    holding any shell metacharacter cannot escape into command
    execution; reading it back means undoing that, not splitting on
    quotes.
    """
    matchPayload = re.search(
        r"printf '%s' '([A-Za-z0-9+/=]*)'", sCommand,
    )
    assert matchPayload, sCommand
    return base64.b64decode(matchPayload.group(1)).decode("utf-8")


@pytest.mark.falsification
def testARunStoppedBeforeAnyStepFinishedStillHasALog():
    """Kills: writing the log only when a step passes or fails.

    The run has announced itself and nothing else has happened, which
    is exactly the state a Cancel finds during a long first step. The
    path is already in ``pipeline_state.json`` by now, so if nothing
    has been written the dashboard is pointing at a file that does not
    exist.
    """
    connection = ConnectionRecordingLogAppends()

    _fnDriveEvents(connection, [
        {"sType": "started", "sCommand": "runStep"},
    ])

    assert connection.listAppendCommands, (
        "a run that announced itself wrote no log; a Cancel now leaves "
        "sLogPath naming a file nothing created"
    )
    assert S_LOG_PATH in connection.listAppendCommands[0]


@pytest.mark.falsification
def testTheLogSaysWhichActionProducedIt():
    """Kills: an empty header, which writes no file at all.

    ``fnWriteLogToContainer`` returns early on an empty buffer, so a
    flush with nothing to carry is indistinguishable from no flush.
    The header is not decoration -- it is the content that makes the
    file exist, and it names the action so the file is worth opening.
    """
    connection = ConnectionRecordingLogAppends()

    _fnDriveEvents(connection, [
        {"sType": "started", "sCommand": "runAll"},
    ])

    sDecoded = _fsDecodeAppendedText(connection.listAppendCommands[0])
    assert "runAll" in sDecoded, sDecoded
    assert "started" in sDecoded, sDecoded


def testAStepsOwnOutputStillReachesTheLog():
    """The ordinary path is unchanged: output flushes on the result."""
    connection = ConnectionRecordingLogAppends()

    _fnDriveEvents(connection, [
        {"sType": "started", "sCommand": "runStep"},
        {"sType": "output", "sLine": "computed 3 values"},
        {"sType": "stepPass", "iStepNumber": 1, "iExitCode": 0},
    ])

    listDecoded = [
        _fsDecodeAppendedText(sCommand)
        for sCommand in connection.listAppendCommands
    ]
    assert any("computed 3 values" in sText for sText in listDecoded), (
        listDecoded
    )
