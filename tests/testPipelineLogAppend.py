"""A run's log reaches the container whatever its size, and loses nothing.

The log was appended as one ``bash -c`` argument: past roughly 96 KB
between flushes it exceeded Linux's per-argument limit, the append
failed, and the whole buffer was cleared anyway. On a host project the
failure surfaced as an ``OSError`` that ended the run after its step had
passed. These tests run the real append against a fake container that
decodes exactly what the command would write.
"""

import asyncio
import base64
import re

import pytest

from vaibify.docker.execArgumentBudget import I_EXEC_ARGUMENT_BUDGET_BYTES
from vaibify.gui import pipelineLogger

S_LOG_PATH = "/workspace/project/.vaibify/logs/run.log"
RE_ENCODED = re.compile(r"printf '%s' '([A-Za-z0-9+/=]*)' \| base64 -d")


class FakeContainerLog:
    """Decodes each append command the way the container's shell would."""

    def __init__(self, listOutcomes=None):
        self.listCommandLengths = []
        self.sWritten = ""
        self.listOutcomes = list(listOutcomes or [])

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommandLengths.append(len(sCommand))
        sOutcome = self.listOutcomes.pop(0) if self.listOutcomes else "ok"
        if sOutcome == "oserror":
            raise OSError("the host refused the write")
        if sOutcome == "argument-too-long":
            return (126, "bash: Argument list too long")
        self.sWritten += base64.b64decode(
            RE_ENCODED.search(sCommand).group(1)).decode("utf-8")
        return (0, "")


def _flistLines(iCount, iLineBytes=200):
    return [f"{iIndex:06d} " + "x" * iLineBytes for iIndex in range(iCount)]


def _fnFlush(connection, listLines):
    asyncio.run(pipelineLogger.fnWriteLogToContainer(
        connection, "cid", S_LOG_PATH, listLines))


@pytest.mark.falsification
def testALogLargerThanOneExecArgumentIsWrittenInChunksThatEachFit():
    """Kills: sending the whole buffer as one command argument."""
    connection = FakeContainerLog()
    listLines = _flistLines(2000)
    sExpected = "\n".join(listLines) + "\n"
    assert len(sExpected) > 3 * I_EXEC_ARGUMENT_BUDGET_BYTES
    _fnFlush(connection, listLines)
    assert connection.sWritten == sExpected
    assert len(connection.listCommandLengths) > 3
    assert max(connection.listCommandLengths) < I_EXEC_ARGUMENT_BUDGET_BYTES
    assert listLines == []


@pytest.mark.falsification
def testAFailedAppendKeepsTheUnwrittenLinesForTheNextFlush():
    """Kills: clearing the buffer whether or not the append happened."""
    connection = FakeContainerLog(listOutcomes=["ok", "argument-too-long"])
    listLines = _flistLines(1000)
    _fnFlush(connection, listLines)
    iWritten = connection.sWritten.count("\n")
    assert 0 < iWritten < 1000
    assert listLines == _flistLines(1000)[iWritten:]
    connection.listOutcomes = []
    _fnFlush(connection, listLines)
    assert connection.sWritten == "\n".join(_flistLines(1000)) + "\n"
    assert listLines == []


@pytest.mark.falsification
def testAHostLegFailureDoesNotEndTheRunAndKeepsTheLines():
    """Kills: letting a host-side OSError propagate out of the flush."""
    connection = FakeContainerLog(listOutcomes=["oserror"])
    listLines = _flistLines(5)
    _fnFlush(connection, listLines)
    assert listLines == _flistLines(5)
    assert connection.sWritten == ""


def testALineBufferedWhileTheAppendRunsIsNotDiscarded():
    """The buffer is not cleared wholesale after the write returns."""
    listLines = _flistLines(3)

    class AppendingContainer(FakeContainerLog):
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            listLines.append("arrived during the append")
            return super().ftResultExecuteCommand(sContainerId, sCommand)

    connection = AppendingContainer()
    _fnFlush(connection, listLines)
    assert listLines == ["arrived during the append"]
    _fnFlush(connection, listLines)
    assert connection.sWritten.endswith("arrived during the append\n")


def testChunksNeverSplitALineAndStayWithinTheirBudget():
    listLines = _flistLines(800, iLineBytes=500)
    listChunks = pipelineLogger._flistChunkLogLines(listLines)
    assert [sLine for listChunk in listChunks for sLine in listChunk] == (
        listLines)
    for listChunk in listChunks:
        iBytes = len(("\n".join(listChunk) + "\n").encode("utf-8"))
        assert iBytes <= pipelineLogger.I_LOG_APPEND_CHUNK_BYTES


def testAnEmptyBufferRunsNoCommand():
    connection = FakeContainerLog()
    _fnFlush(connection, [])
    assert connection.listCommandLengths == []
