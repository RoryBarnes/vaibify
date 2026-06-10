"""Logging hygiene: rotation, idempotent attachment, freshness spam.

Guards against the failure mode where ``~/.vaibify/vaibify.log`` grew
to gigabytes: a non-rotating handler, duplicate attachment at import
time plus command time, and a per-step INFO line on every 5-second
freshness poll.
"""

import logging
import logging.handlers
import subprocess
import sys

import pytest

from tests.conftest import _fnRemoveFileHandlersFromVaibifyLogger
from vaibify.cli.main import I_LOG_BACKUP_COUNT, I_LOG_MAX_BYTES
from vaibify.gui import fileStatusManager


@pytest.fixture
def fnCleanVaibifyFileHandlers():
    """Detach vaibify file handlers before and after each test."""
    _fnRemoveFileHandlersFromVaibifyLogger()
    yield
    _fnRemoveFileHandlersFromVaibifyLogger()


def _flistVaibifyFileHandlers():
    """Return the file handlers attached to the ``vaibify`` logger."""
    return [
        handlerAttached
        for handlerAttached in logging.getLogger("vaibify").handlers
        if isinstance(handlerAttached, logging.FileHandler)
    ]


def test_importing_cli_main_attaches_no_file_handler():
    sProgram = (
        "import logging\n"
        "import vaibify.cli.main\n"
        "listHandlers = [\n"
        "    handlerAttached\n"
        "    for handlerAttached in"
        " logging.getLogger('vaibify').handlers\n"
        "    if isinstance(handlerAttached, logging.FileHandler)\n"
        "]\n"
        "print(len(listHandlers))\n"
    )
    resultRun = subprocess.run(
        [sys.executable, "-c", sProgram],
        capture_output=True, text=True, timeout=120,
    )
    assert resultRun.returncode == 0, resultRun.stderr
    assert resultRun.stdout.strip() == "0"


def test_configure_twice_attaches_one_rotating_handler(
    fnCleanVaibifyFileHandlers, tmp_path,
):
    import vaibify.cli.main as cliMain
    cliMain._fnConfigureErrorLogging(sLogDirOverride=str(tmp_path))
    cliMain._fnConfigureErrorLogging(sLogDirOverride=str(tmp_path))
    listHandlers = _flistVaibifyFileHandlers()
    assert len(listHandlers) == 1
    assert isinstance(
        listHandlers[0], logging.handlers.RotatingFileHandler,
    )


def test_rotating_handler_has_expected_rotation_parameters(
    fnCleanVaibifyFileHandlers, tmp_path,
):
    import vaibify.cli.main as cliMain
    cliMain._fnConfigureErrorLogging(sLogDirOverride=str(tmp_path))
    handlerRotating = _flistVaibifyFileHandlers()[0]
    assert handlerRotating.maxBytes == I_LOG_MAX_BYTES
    assert handlerRotating.maxBytes == 10 * 1024 * 1024
    assert handlerRotating.backupCount == I_LOG_BACKUP_COUNT
    assert handlerRotating.backupCount == 5
    assert handlerRotating.level == logging.INFO


def _fnEmitFreshnessCheck(iIndex, bStale):
    fileStatusManager._fnLogFreshnessCheck(
        iIndex, "2026-01-01T00:00:00Z", 1767225600,
        ["Plot/figure.pdf"], bStale,
    )


def test_freshness_check_logs_info_only_on_transition(caplog):
    fileStatusManager._dictLastLoggedStaleByStep.clear()
    with caplog.at_level(logging.DEBUG, logger="vaibify"):
        _fnEmitFreshnessCheck(0, False)
        _fnEmitFreshnessCheck(0, False)
        _fnEmitFreshnessCheck(0, True)
    listInfoRecords = [
        record for record in caplog.records
        if record.levelno == logging.INFO
    ]
    listDebugRecords = [
        record for record in caplog.records
        if record.levelno == logging.DEBUG
    ]
    assert len(listInfoRecords) == 1
    assert (
        "Freshness transition step 0"
        in listInfoRecords[0].getMessage()
    )
    assert len(listDebugRecords) == 2


def test_freshness_transition_cache_is_per_step(caplog):
    fileStatusManager._dictLastLoggedStaleByStep.clear()
    with caplog.at_level(logging.DEBUG, logger="vaibify"):
        _fnEmitFreshnessCheck(0, False)
        _fnEmitFreshnessCheck(1, True)
        _fnEmitFreshnessCheck(0, True)
        _fnEmitFreshnessCheck(1, True)
    listInfoRecords = [
        record for record in caplog.records
        if record.levelno == logging.INFO
    ]
    assert len(listInfoRecords) == 1
    assert (
        "Freshness transition step 0"
        in listInfoRecords[0].getMessage()
    )
