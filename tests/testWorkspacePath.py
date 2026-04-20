"""Tests for containerId -> host workspace path resolution."""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from vaibify.gui import workspacePath


@pytest.fixture(autouse=True)
def _fnClearCacheEachTest():
    workspacePath.fnClearCache()
    yield
    workspacePath.fnClearCache()


def _fsInspectJson(sSource, sDestination="/workspace"):
    return json.dumps([{"Mounts": [
        {"Source": sSource, "Destination": sDestination},
    ]}])


def test_fsHostWorkspacePathForContainer_returns_empty_on_no_id():
    assert workspacePath.fsHostWorkspacePathForContainer("") == ""


def test_fsHostWorkspacePathForContainer_returns_mount_source(tmp_path):
    sSource = str(tmp_path)
    os.makedirs(sSource, exist_ok=True)
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_fsInspectJson(sSource), stderr="",
        )
        sResult = workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert sResult == sSource


def test_fsHostWorkspacePathForContainer_returns_empty_when_no_match(tmp_path):
    dictOutput = json.dumps([{"Mounts": [
        {"Source": str(tmp_path), "Destination": "/other/path"},
    ]}])
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=dictOutput, stderr="",
        )
        sResult = workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert sResult == ""


def test_fsHostWorkspacePathForContainer_returns_empty_on_docker_failure():
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no such container",
        )
        sResult = workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert sResult == ""


def test_fsHostWorkspacePathForContainer_returns_empty_on_timeout():
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.side_effect = subprocess.TimeoutExpired(
            cmd="docker inspect", timeout=10,
        )
        sResult = workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert sResult == ""


def test_fsHostWorkspacePathForContainer_returns_empty_on_bad_json(tmp_path):
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-json", stderr="",
        )
        sResult = workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert sResult == ""


def test_fsHostWorkspacePathForContainer_caches_positive_result(tmp_path):
    sSource = str(tmp_path)
    os.makedirs(sSource, exist_ok=True)
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_fsInspectJson(sSource), stderr="",
        )
        workspacePath.fsHostWorkspacePathForContainer("abc123")
        workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert mockRun.call_count == 1


def test_fsHostWorkspacePathForContainer_retries_after_cache_clear(tmp_path):
    sSource = str(tmp_path)
    os.makedirs(sSource, exist_ok=True)
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_fsInspectJson(sSource), stderr="",
        )
        workspacePath.fsHostWorkspacePathForContainer("abc123")
        workspacePath.fnClearCache()
        workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert mockRun.call_count == 2


def test_fsHostWorkspacePathForContainer_skips_nonexistent_mount_source():
    dictOutput = _fsInspectJson("/does/not/exist/on/host")
    with patch(
        "vaibify.gui.workspacePath.subprocess.run",
    ) as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=dictOutput, stderr="",
        )
        sResult = workspacePath.fsHostWorkspacePathForContainer("abc123")
    assert sResult == ""
