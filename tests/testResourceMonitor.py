"""Tests for vaibcask.gui.resourceMonitor."""

import json
from unittest.mock import patch, MagicMock

import pytest

from vaibcask.gui.resourceMonitor import fdictGetContainerStats


def _fmockCompletedProcess(sStdout="", iReturncode=0):
    """Build a mock subprocess.CompletedProcess."""
    mockResult = MagicMock()
    mockResult.stdout = sStdout
    mockResult.stderr = ""
    mockResult.returncode = iReturncode
    return mockResult


def test_fdictGetContainerStats_parses_json_output():
    dictDockerOutput = {
        "CPUPerc": "25.50%",
        "MemPerc": "12.34%",
        "MemUsage": "512MiB / 4GiB",
    }
    sJsonOutput = json.dumps(dictDockerOutput)

    with patch(
        "vaibcask.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.return_value = _fmockCompletedProcess(
            sStdout=sJsonOutput, iReturncode=0,
        )

        dictStats = fdictGetContainerStats("container_abc")

    assert dictStats["fCpuPercent"] == pytest.approx(25.50)
    assert dictStats["fMemoryPercent"] == pytest.approx(12.34)
    assert dictStats["sMemoryUsage"] == "512MiB"
    assert dictStats["sMemoryLimit"] == "4GiB"


def test_fdictGetContainerStats_handles_error():
    with patch(
        "vaibcask.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.return_value = _fmockCompletedProcess(
            sStdout="", iReturncode=1,
        )

        dictStats = fdictGetContainerStats("bad_container")

    assert dictStats["fCpuPercent"] == 0.0
    assert dictStats["fMemoryPercent"] == 0.0
    assert dictStats["sMemoryUsage"] == "0B"
    assert dictStats["sMemoryLimit"] == "0B"


def test_fdictGetContainerStats_handles_timeout():
    import subprocess

    with patch(
        "vaibcask.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.side_effect = subprocess.TimeoutExpired(
            cmd="docker stats", timeout=10,
        )

        dictStats = fdictGetContainerStats("timeout_container")

    assert dictStats["fCpuPercent"] == 0.0
    assert dictStats["sMemoryUsage"] == "0B"


def test_fdictGetContainerStats_handles_malformed_json():
    with patch(
        "vaibcask.gui.resourceMonitor.subprocess.run"
    ) as mockRun:
        mockRun.return_value = _fmockCompletedProcess(
            sStdout="not valid json{{{", iReturncode=0,
        )

        dictStats = fdictGetContainerStats("malformed_container")

    assert dictStats["fCpuPercent"] == 0.0
    assert dictStats["sMemoryLimit"] == "0B"
