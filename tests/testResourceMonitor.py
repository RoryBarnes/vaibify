"""Tests for vaibify.gui.resourceMonitor."""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from vaibify.gui.resourceMonitor import fdictGetContainerStats


def _fmockCompletedProcess(sStdout="", sStderr="", iReturncode=0):
    """Build a mock subprocess.CompletedProcess."""
    mockResult = MagicMock()
    mockResult.stdout = sStdout
    mockResult.stderr = sStderr
    mockResult.returncode = iReturncode
    return mockResult


def _fbuildHealthyStatsJson():
    """Return a representative docker stats JSON payload."""
    return json.dumps({
        "CPUPerc": "25.50%",
        "MemPerc": "12.34%",
        "MemUsage": "512MiB / 4GiB",
    })


def _fsbuildDfOutput(iTotal, iUsed, iFree):
    """Return a fake `df -PB1 /` table with the given counts."""
    return (
        "Filesystem 1B-blocks Used Available Capacity Mounted on\n"
        "overlay {} {} {} 1% /\n"
    ).format(iTotal, iUsed, iFree)


def _fnsetSubprocessReturns(mockRun, listResults):
    """Drive successive subprocess.run calls with prebuilt results."""
    mockRun.side_effect = listResults


def test_fdictGetContainerStats_parses_healthy_state():
    listResults = [
        _fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
        _fmockCompletedProcess(
            sStdout=_fsbuildDfOutput(
                100 * 1024 ** 3, 40 * 1024 ** 3, 60 * 1024 ** 3,
            ),
            iReturncode=0,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["bAvailable"] is True
    assert dictStats["sReason"] == ""
    assert dictStats["fCpuPercent"] == pytest.approx(25.50)
    assert dictStats["fMemoryPercent"] == pytest.approx(12.34)
    assert dictStats["sMemoryUsage"] == "512MiB"
    assert dictStats["sMemoryLimit"] == "4GiB"
    assert dictStats["dictDisk"]["bAvailable"] is True
    assert dictStats["dictDisk"]["iTotalBytes"] == 100 * 1024 ** 3
    assert dictStats["dictDisk"]["fFreeFraction"] == pytest.approx(0.6)
    assert dictStats["bDiskWarning"] is False


def test_fdictGetContainerStats_signals_daemon_unreachable():
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.side_effect = FileNotFoundError("docker missing")
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "daemon-unreachable"
    assert dictStats["fCpuPercent"] == 0.0
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "daemon-unreachable"
    assert dictStats["bDiskWarning"] is False


def test_fdictGetContainerStats_signals_timeout():
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.side_effect = subprocess.TimeoutExpired(
            cmd="docker stats", timeout=10,
        )
        dictStats = fdictGetContainerStats("timeout_container")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "timeout"
    assert dictStats["sMemoryUsage"] == "0B"


def test_fdictGetContainerStats_signals_container_not_running():
    listResults = [
        _fmockCompletedProcess(
            sStderr="Error: No such container: bad_id",
            iReturncode=1,
        ),
        _fmockCompletedProcess(
            sStderr="Error: No such container: bad_id",
            iReturncode=1,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("bad_id")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "container-not-running"
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "container-not-running"


def test_fdictGetContainerStats_classifies_daemon_stderr():
    listResults = [
        _fmockCompletedProcess(
            sStderr="Cannot connect to the Docker daemon at unix:///",
            iReturncode=1,
        ),
        _fmockCompletedProcess(
            sStderr="Cannot connect to the Docker daemon at unix:///",
            iReturncode=1,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["sReason"] == "daemon-unreachable"


def test_fdictGetContainerStats_handles_malformed_stats_json():
    listResults = [
        _fmockCompletedProcess(
            sStdout="not valid json{{{", iReturncode=0,
        ),
        _fmockCompletedProcess(
            sStdout=_fsbuildDfOutput(
                100 * 1024 ** 3, 40 * 1024 ** 3, 60 * 1024 ** 3,
            ),
            iReturncode=0,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("malformed_container")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "parse-error"
    assert dictStats["dictDisk"]["bAvailable"] is True


def test_fdictGetContainerStats_flags_low_disk_warning():
    listResults = [
        _fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
        _fmockCompletedProcess(
            sStdout=_fsbuildDfOutput(
                100 * 1024 ** 3, 97 * 1024 ** 3, 3 * 1024 ** 3,
            ),
            iReturncode=0,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("nearly_full")

    assert dictStats["bDiskWarning"] is True
    assert dictStats["dictDisk"]["fFreeFraction"] == pytest.approx(0.03)
    assert "GiB" in dictStats["dictDisk"]["sFreeHuman"]


def test_fdictGetContainerStats_handles_disk_parse_failure():
    listResults = [
        _fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
        _fmockCompletedProcess(
            sStdout="Filesystem 1B-blocks Used Available Capacity Mounted on\n",
            iReturncode=0,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["bAvailable"] is True
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "parse-error"
    assert dictStats["bDiskWarning"] is False


def test_fdictGetContainerStats_handles_garbage_disk_fields():
    listResults = [
        _fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
        _fmockCompletedProcess(
            sStdout=(
                "Filesystem 1B-blocks Used Available Capacity Mounted on\n"
                "overlay foo bar baz 1% /\n"
            ),
            iReturncode=0,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "parse-error"


def test_fdictGetContainerStats_disk_handles_zero_total():
    listResults = [
        _fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
        _fmockCompletedProcess(
            sStdout=_fsbuildDfOutput(0, 0, 0),
            iReturncode=0,
        ),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        _fnsetSubprocessReturns(mockRun, listResults)
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["dictDisk"]["bAvailable"] is True
    assert dictStats["dictDisk"]["fFreeFraction"] == 0.0


def test_fdictGetContainerStats_disk_timeout_does_not_break_stats():
    listResults = [
        _fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
        subprocess.TimeoutExpired(cmd="docker exec", timeout=10),
    ]
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.side_effect = listResults
        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["bAvailable"] is True
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "timeout"
