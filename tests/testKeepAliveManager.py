"""Tests for vaibify.docker.keepAliveManager."""

import os
import signal
import sys

import pytest
from unittest.mock import patch, MagicMock


from vaibify.docker.keepAliveManager import (
    fnStartKeepAlive,
    fnStopKeepAlive,
    _fiReadPid,
    _fnKillIfRunning,
    _fnRemovePidFile,
    _fnWritePidFile,
    _fsPidFilePath,
    _fiSpawnCaffeinate,
)


# ---------------------------------------------------------------
# _fsPidFilePath
# ---------------------------------------------------------------


def test_fsPidFilePath_includes_container_name():
    sPath = _fsPidFilePath("myproj")
    assert sPath.endswith("myproj.pid")


def test_fsPidFilePath_different_containers_differ():
    sOne = _fsPidFilePath("alpha")
    sTwo = _fsPidFilePath("beta")
    assert sOne != sTwo


# ---------------------------------------------------------------
# _fnWritePidFile + _fiReadPid round-trip
# ---------------------------------------------------------------


def test_writePid_then_readPid_returns_value(tmp_path):
    sPath = str(tmp_path / "proj.pid")
    with open(sPath, "w") as fh:
        fh.write("12345\n")
    iPid = _fiReadPid(sPath)
    assert iPid == 12345


def test_readPid_missing_file_returns_zero(tmp_path):
    sPath = str(tmp_path / "missing.pid")
    assert _fiReadPid(sPath) == 0


def test_readPid_invalid_contents_returns_zero(tmp_path):
    sPath = str(tmp_path / "bad.pid")
    with open(sPath, "w") as fh:
        fh.write("not-an-integer\n")
    assert _fiReadPid(sPath) == 0


def test_writePidFile_creates_file(tmp_path):
    sPath = str(tmp_path / "proj.pid")
    with patch(
        "vaibify.docker.keepAliveManager._fsPidFilePath",
        return_value=sPath,
    ):
        _fnWritePidFile("proj", 9999)
    with open(sPath, "r") as fh:
        assert fh.read().strip() == "9999"


# ---------------------------------------------------------------
# _fnRemovePidFile
# ---------------------------------------------------------------


def test_removePidFile_removes_existing(tmp_path):
    sPath = str(tmp_path / "to_remove.pid")
    with open(sPath, "w") as fh:
        fh.write("1")
    _fnRemovePidFile(sPath)
    assert not os.path.exists(sPath)


def test_removePidFile_silent_on_missing(tmp_path):
    sPath = str(tmp_path / "never_existed.pid")
    _fnRemovePidFile(sPath)


# ---------------------------------------------------------------
# _fnKillIfRunning
# ---------------------------------------------------------------


def test_killIfRunning_calls_os_kill():
    with patch("vaibify.docker.keepAliveManager.os.kill") as mockKill:
        _fnKillIfRunning(12345)
    mockKill.assert_called_once_with(12345, signal.SIGTERM)


def test_killIfRunning_tolerates_process_lookup_error():
    with patch(
        "vaibify.docker.keepAliveManager.os.kill",
        side_effect=ProcessLookupError,
    ):
        _fnKillIfRunning(12345)


def test_killIfRunning_tolerates_permission_error():
    with patch(
        "vaibify.docker.keepAliveManager.os.kill",
        side_effect=PermissionError,
    ):
        _fnKillIfRunning(12345)


# ---------------------------------------------------------------
# _fiSpawnCaffeinate
# ---------------------------------------------------------------


def test_fiSpawnCaffeinate_returns_pid():
    mockProcess = MagicMock()
    mockProcess.pid = 4242
    with patch(
        "vaibify.docker.keepAliveManager.subprocess.Popen",
        return_value=mockProcess,
    ):
        iPid = _fiSpawnCaffeinate()
    assert iPid == 4242


def test_fiSpawnCaffeinate_file_not_found_returns_zero():
    with patch(
        "vaibify.docker.keepAliveManager.subprocess.Popen",
        side_effect=FileNotFoundError,
    ):
        assert _fiSpawnCaffeinate() == 0


# ---------------------------------------------------------------
# fnStartKeepAlive / fnStopKeepAlive integration
# ---------------------------------------------------------------


def test_fnStartKeepAlive_noop_on_non_darwin():
    with patch(
        "vaibify.docker.keepAliveManager.sys.platform", "linux"
    ), patch(
        "vaibify.docker.keepAliveManager._fiSpawnCaffeinate"
    ) as mockSpawn:
        fnStartKeepAlive("proj")
    mockSpawn.assert_not_called()


def test_fnStartKeepAlive_writes_pid_on_darwin(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    with patch(
        "vaibify.docker.keepAliveManager.sys.platform", "darwin"
    ), patch(
        "vaibify.docker.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.docker.keepAliveManager._fiSpawnCaffeinate",
        return_value=7777,
    ):
        fnStartKeepAlive("proj")
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "r") as fh:
        assert fh.read().strip() == "7777"


def test_fnStartKeepAlive_zero_pid_skips_write(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    with patch(
        "vaibify.docker.keepAliveManager.sys.platform", "darwin"
    ), patch(
        "vaibify.docker.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.docker.keepAliveManager._fiSpawnCaffeinate",
        return_value=0,
    ):
        fnStartKeepAlive("proj")
    assert not os.path.exists(
        os.path.join(sPidDir, "proj.pid")
    )


def test_fnStopKeepAlive_missing_file_noop(tmp_path):
    sPidDir = str(tmp_path / "caffeinate_empty")
    os.makedirs(sPidDir, exist_ok=True)
    with patch(
        "vaibify.docker.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.docker.keepAliveManager.os.kill"
    ) as mockKill:
        fnStopKeepAlive("missing_proj")
    mockKill.assert_not_called()


def test_fnStopKeepAlive_kills_and_removes(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    os.makedirs(sPidDir, exist_ok=True)
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "w") as fh:
        fh.write("54321\n")
    with patch(
        "vaibify.docker.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.docker.keepAliveManager.os.kill"
    ) as mockKill:
        fnStopKeepAlive("proj")
    mockKill.assert_called_once_with(54321, signal.SIGTERM)
    assert not os.path.exists(sPath)


def test_fnStartKeepAlive_stops_existing_first(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    os.makedirs(sPidDir, exist_ok=True)
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "w") as fh:
        fh.write("111\n")
    with patch(
        "vaibify.docker.keepAliveManager.sys.platform", "darwin"
    ), patch(
        "vaibify.docker.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.docker.keepAliveManager._fiSpawnCaffeinate",
        return_value=222,
    ), patch(
        "vaibify.docker.keepAliveManager.os.kill"
    ) as mockKill:
        fnStartKeepAlive("proj")
    mockKill.assert_called_once_with(111, signal.SIGTERM)
    with open(sPath, "r") as fh:
        assert fh.read().strip() == "222"
