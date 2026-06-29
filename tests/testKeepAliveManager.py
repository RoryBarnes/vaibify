"""Tests for vaibify.config.keepAliveManager."""

import datetime
import os
import signal
import sys

import pytest
from unittest.mock import call, patch, MagicMock


def _fsNowIso():
    """Return the current local time as an ISO-8601 claim string."""
    return datetime.datetime.now().isoformat()


def _fbSigtermWasSent(mockKill, iPid):
    """Return True if mockKill recorded a SIGTERM to the given PID.

    The liveness probe shares the patched ``os.kill`` and issues a
    signal-0 call, so a plain call-count assertion is ambiguous; this
    isolates the terminating signal.
    """
    return call(iPid, signal.SIGTERM) in mockKill.call_args_list


import json

from vaibify.config.keepAliveManager import (
    fnStartKeepAlive,
    fnStopKeepAlive,
    _fdictReadPidPayload,
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
# _fnWritePidFile + _fdictReadPidPayload round-trip
# ---------------------------------------------------------------


def test_writePidFile_then_readPayload_round_trip(tmp_path):
    sPath = str(tmp_path / "proj.pid")
    with patch(
        "vaibify.config.keepAliveManager._fsPidFilePath",
        return_value=sPath,
    ):
        _fnWritePidFile("proj", 12345)
    dictPayload = _fdictReadPidPayload(sPath)
    assert dictPayload["iPid"] == 12345
    assert "sStartedIso" in dictPayload


def test_readPidPayload_missing_file_returns_empty(tmp_path):
    sPath = str(tmp_path / "missing.pid")
    assert _fdictReadPidPayload(sPath) == {}


def test_readPidPayload_legacy_bare_int_returns_pid(tmp_path):
    """A pre-JSON pid file holding a bare integer still parses."""
    sPath = str(tmp_path / "legacy.pid")
    with open(sPath, "w") as fh:
        fh.write("12345\n")
    assert _fdictReadPidPayload(sPath) == {"iPid": 12345}


def test_readPidPayload_invalid_contents_returns_empty(tmp_path):
    sPath = str(tmp_path / "bad.pid")
    with open(sPath, "w") as fh:
        fh.write("not-an-integer\n")
    assert _fdictReadPidPayload(sPath) == {}


def test_writePidFile_creates_json_file(tmp_path):
    sPath = str(tmp_path / "proj.pid")
    with patch(
        "vaibify.config.keepAliveManager._fsPidFilePath",
        return_value=sPath,
    ):
        _fnWritePidFile("proj", 9999)
    with open(sPath, "r") as fh:
        dictPayload = json.load(fh)
    assert dictPayload["iPid"] == 9999
    assert "sStartedIso" in dictPayload


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


def test_killIfRunning_calls_os_kill_when_alive():
    with patch(
        "vaibify.config.keepAliveManager.fbIsProcessAliveSince",
        return_value=True,
    ), patch("vaibify.config.keepAliveManager.os.kill") as mockKill:
        _fnKillIfRunning(12345, "2026-06-25T12:00:00")
    mockKill.assert_called_once_with(12345, signal.SIGTERM)


def test_killIfRunning_skips_recycled_pid():
    """A recycled PID (start time after the claim) is never killed."""
    with patch(
        "vaibify.config.keepAliveManager.fbIsProcessAliveSince",
        return_value=False,
    ), patch("vaibify.config.keepAliveManager.os.kill") as mockKill:
        _fnKillIfRunning(12345, "2000-01-01T00:00:00")
    mockKill.assert_not_called()


def test_killIfRunning_tolerates_process_lookup_error():
    with patch(
        "vaibify.config.keepAliveManager.fbIsProcessAliveSince",
        return_value=True,
    ), patch(
        "vaibify.config.keepAliveManager.os.kill",
        side_effect=ProcessLookupError,
    ):
        _fnKillIfRunning(12345, None)


def test_killIfRunning_tolerates_permission_error():
    with patch(
        "vaibify.config.keepAliveManager.fbIsProcessAliveSince",
        return_value=True,
    ), patch(
        "vaibify.config.keepAliveManager.os.kill",
        side_effect=PermissionError,
    ):
        _fnKillIfRunning(12345, None)


# ---------------------------------------------------------------
# _fiSpawnCaffeinate
# ---------------------------------------------------------------


def test_fiSpawnCaffeinate_returns_pid():
    mockProcess = MagicMock()
    mockProcess.pid = 4242
    with patch(
        "vaibify.config.keepAliveManager.subprocess.Popen",
        return_value=mockProcess,
    ):
        iPid = _fiSpawnCaffeinate()
    assert iPid == 4242


def test_fiSpawnCaffeinate_file_not_found_returns_zero():
    with patch(
        "vaibify.config.keepAliveManager.subprocess.Popen",
        side_effect=FileNotFoundError,
    ):
        assert _fiSpawnCaffeinate() == 0


# ---------------------------------------------------------------
# fnStartKeepAlive / fnStopKeepAlive integration
# ---------------------------------------------------------------


def test_fnStartKeepAlive_noop_on_non_darwin():
    with patch(
        "vaibify.config.keepAliveManager.sys.platform", "linux"
    ), patch(
        "vaibify.config.keepAliveManager._fiSpawnCaffeinate"
    ) as mockSpawn:
        fnStartKeepAlive("proj")
    mockSpawn.assert_not_called()


def test_fnStartKeepAlive_writes_pid_on_darwin(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    with patch(
        "vaibify.config.keepAliveManager.sys.platform", "darwin"
    ), patch(
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager._fiSpawnCaffeinate",
        return_value=7777,
    ):
        fnStartKeepAlive("proj")
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "r") as fh:
        dictPayload = json.load(fh)
    assert dictPayload["iPid"] == 7777


def test_fnStartKeepAlive_zero_pid_skips_write(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    with patch(
        "vaibify.config.keepAliveManager.sys.platform", "darwin"
    ), patch(
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager._fiSpawnCaffeinate",
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
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager.os.kill"
    ) as mockKill:
        fnStopKeepAlive("missing_proj")
    mockKill.assert_not_called()


def test_fnStopKeepAlive_kills_and_removes(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    os.makedirs(sPidDir, exist_ok=True)
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "w") as fh:
        json.dump({"iPid": os.getpid(),
                   "sStartedIso": _fsNowIso()}, fh)
    with patch(
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager.os.kill"
    ) as mockKill:
        fnStopKeepAlive("proj")
    assert _fbSigtermWasSent(mockKill, os.getpid())
    assert not os.path.exists(sPath)


def test_fnStopKeepAlive_ancient_claim_does_not_kill(tmp_path):
    """A live PID recorded against an ancient claim is not SIGTERMed."""
    sPidDir = str(tmp_path / "caffeinate")
    os.makedirs(sPidDir, exist_ok=True)
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "w") as fh:
        json.dump({"iPid": os.getpid(),
                   "sStartedIso": "2000-01-01T00:00:00"}, fh)
    with patch(
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager.os.kill"
    ) as mockKill:
        fnStopKeepAlive("proj")
    assert not _fbSigtermWasSent(mockKill, os.getpid())
    assert not os.path.exists(sPath)


def test_fnStopKeepAlive_legacy_bare_int_still_kills(tmp_path):
    """A pre-JSON bare-int pid file still terminates a live process."""
    sPidDir = str(tmp_path / "caffeinate")
    os.makedirs(sPidDir, exist_ok=True)
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    with patch(
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager.os.kill"
    ) as mockKill:
        fnStopKeepAlive("proj")
    assert _fbSigtermWasSent(mockKill, os.getpid())
    assert not os.path.exists(sPath)


def test_fnStartKeepAlive_stops_existing_first(tmp_path):
    sPidDir = str(tmp_path / "caffeinate")
    os.makedirs(sPidDir, exist_ok=True)
    sPath = os.path.join(sPidDir, "proj.pid")
    with open(sPath, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    with patch(
        "vaibify.config.keepAliveManager.sys.platform", "darwin"
    ), patch(
        "vaibify.config.keepAliveManager._S_PID_DIRECTORY", sPidDir
    ), patch(
        "vaibify.config.keepAliveManager._fiSpawnCaffeinate",
        return_value=222,
    ), patch(
        "vaibify.config.keepAliveManager.os.kill"
    ) as mockKill:
        fnStartKeepAlive("proj")
    assert _fbSigtermWasSent(mockKill, os.getpid())
    with open(sPath, "r") as fh:
        dictPayload = json.load(fh)
    assert dictPayload["iPid"] == 222
