"""Tests for log file rotation, append-mode writes, byte budgeting.

Covers audit CRITICAL #5: append-mode writes resilient to disk-full,
per-line cap, per-run rotation pruning, and an in-memory byte budget
beside the existing line-count budget.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from vaibify.gui.pipelineLogger import (
    I_LOG_BYTE_BUDGET,
    I_LOG_LINE_BYTE_CAP,
    I_LOG_RETENTION_COUNT,
    I_MAX_LOG_LINES,
    ffBuildLoggingCallback,
    fnPruneOldLogs,
    fnWriteLogToContainer,
)


def _frunAsync(coro):
    """Run a coroutine to completion on a fresh event loop."""
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro
    )


# ---------------------------------------------------------------------------
# Append-mode writes (item #7)
# ---------------------------------------------------------------------------


def test_log_write_uses_base64_append_not_full_rewrite():
    """fnWriteLogToContainer must base64-pipe-append, not rewrite the file.

    The base64 form is content-immune: a line containing any shell
    metacharacter — or a literal heredoc sentinel — cannot escape into
    command execution.
    """
    import base64
    mockConn = MagicMock()
    mockConn.ftResultExecuteCommand.return_value = (0, "")

    asyncio.run(fnWriteLogToContainer(
        mockConn, "cid", "/log.txt", ["line one", "line two"],
    ))

    mockConn.ftResultExecuteCommand.assert_called_once()
    sCommand = mockConn.ftResultExecuteCommand.call_args[0][1]
    assert "base64 -d" in sCommand
    assert ">> '/log.txt'" in sCommand
    sExpected = base64.b64encode(
        b"line one\nline two\n",
    ).decode("ascii")
    assert sExpected in sCommand
    # The legacy put_archive path must not be touched.
    mockConn.fnWriteFile.assert_not_called()


def test_log_write_immune_to_heredoc_sentinel_in_payload():
    """A scientific stdout line equal to the old sentinel cannot break out."""
    mockConn = MagicMock()
    mockConn.ftResultExecuteCommand.return_value = (0, "")
    listLines = [
        "first line",
        "VAIBIFY_LOG_EOF",
        "rm -rf /  # would have run under heredoc",
        "third line",
    ]
    asyncio.run(fnWriteLogToContainer(
        mockConn, "cid", "/log.txt", listLines,
    ))
    sCommand = mockConn.ftResultExecuteCommand.call_args[0][1]
    # No here-doc; no raw payload in the command line.
    assert "<<" not in sCommand
    assert "rm -rf" not in sCommand


def test_log_write_clears_buffer_after_successful_append():
    """Successful append empties the in-memory buffer so flushes are incremental."""
    mockConn = MagicMock()
    mockConn.ftResultExecuteCommand.return_value = (0, "")
    listLines = ["a", "b"]
    asyncio.run(fnWriteLogToContainer(mockConn, "cid", "/log.txt", listLines))
    assert listLines == []


def test_log_write_empty_buffer_is_a_noop():
    """No docker call is issued when there is nothing to append."""
    mockConn = MagicMock()
    asyncio.run(fnWriteLogToContainer(mockConn, "cid", "/log.txt", []))
    mockConn.ftResultExecuteCommand.assert_not_called()


# ---------------------------------------------------------------------------
# Per-line byte cap (item #7)
# ---------------------------------------------------------------------------


def test_log_line_byte_cap_truncates_oversized_lines():
    """A single huge line is capped before being appended to the buffer."""
    listLogLines = []

    async def fnNoop(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnNoop, listLogLines)
    sHuge = "X" * (I_LOG_LINE_BYTE_CAP * 4)
    asyncio.run(fnCallback({"sType": "output", "sLine": sHuge}))
    assert len(listLogLines) == 1
    sStored = listLogLines[0]
    assert len(sStored.encode("utf-8")) <= I_LOG_LINE_BYTE_CAP + 128
    assert "truncated" in sStored


# ---------------------------------------------------------------------------
# In-memory byte budget alongside the line-count cap (item #7)
# ---------------------------------------------------------------------------


def test_log_byte_budget_evicts_head():
    """When the byte budget is exceeded the oldest lines are evicted."""
    listLogLines = []

    async def fnNoop(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnNoop, listLogLines)
    sChunk = "X" * (I_LOG_LINE_BYTE_CAP - 16)  # one cap-sized line each
    # Push enough lines to exceed the byte budget.
    iLinesToPush = (I_LOG_BYTE_BUDGET // len(sChunk)) + 50

    async def fnPushAll():
        for iIndex in range(iLinesToPush):
            await fnCallback({"sType": "output", "sLine": sChunk})

    asyncio.run(fnPushAll())
    iTotalBytes = sum(len(s.encode("utf-8")) for s in listLogLines)
    assert iTotalBytes <= I_LOG_BYTE_BUDGET + len(sChunk)
    assert len(listLogLines) <= I_MAX_LOG_LINES


# ---------------------------------------------------------------------------
# Run-start pruning of old log files (item #7)
# ---------------------------------------------------------------------------


def test_prune_old_logs_keeps_only_recent():
    """fnPruneOldLogs issues a find + sort + tail + xargs rm pipeline."""
    from vaibify.gui.pipelineLogger import I_LOG_PRUNE_AGE_MINUTES
    mockConn = MagicMock()
    mockConn.ftResultExecuteCommand.return_value = (0, "")
    asyncio.run(fnPruneOldLogs(
        mockConn, "cid", "/workspace/.vaibify/logs",
    ))
    mockConn.ftResultExecuteCommand.assert_called_once()
    sCommand = mockConn.ftResultExecuteCommand.call_args[0][1]
    assert "find " in sCommand
    assert "/workspace/.vaibify/logs" in sCommand
    assert f"-mmin +{I_LOG_PRUNE_AGE_MINUTES}" in sCommand
    assert f"tail -n +{I_LOG_RETENTION_COUNT + 1}" in sCommand
    assert "xargs -r rm -f" in sCommand


def test_prune_old_logs_respects_custom_retention():
    mockConn = MagicMock()
    mockConn.ftResultExecuteCommand.return_value = (0, "")
    asyncio.run(fnPruneOldLogs(
        mockConn, "cid", "/logs", iRetentionCount=5,
    ))
    sCommand = mockConn.ftResultExecuteCommand.call_args[0][1]
    assert "tail -n +6" in sCommand


def test_prune_old_logs_excludes_recently_modified_files():
    """The -mmin guard prevents truncating another concurrent run's log."""
    from vaibify.gui.pipelineLogger import I_LOG_PRUNE_AGE_MINUTES
    mockConn = MagicMock()
    mockConn.ftResultExecuteCommand.return_value = (0, "")
    asyncio.run(fnPruneOldLogs(mockConn, "cid", "/logs"))
    sCommand = mockConn.ftResultExecuteCommand.call_args[0][1]
    assert f"-mmin +{I_LOG_PRUNE_AGE_MINUTES}" in sCommand
