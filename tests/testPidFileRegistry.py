"""Tests for vaibify.config.pidFileRegistry."""

import os
import stat

import pytest

from vaibify.config import pidFileRegistry


# ---------------------------------------------------------------------------
# fnEnsureDirectory
# ---------------------------------------------------------------------------


def test_fnEnsureDirectory_creates_dir_at_mode_0700(tmp_path):
    sDirectory = str(tmp_path / "registry")
    pidFileRegistry.fnEnsureDirectory(sDirectory)
    assert os.path.isdir(sDirectory)
    iMode = stat.S_IMODE(os.stat(sDirectory).st_mode)
    assert iMode == 0o700


def test_fnEnsureDirectory_is_idempotent(tmp_path):
    sDirectory = str(tmp_path / "registry")
    pidFileRegistry.fnEnsureDirectory(sDirectory)
    pidFileRegistry.fnEnsureDirectory(sDirectory)
    assert stat.S_IMODE(os.stat(sDirectory).st_mode) == 0o700


def test_fnEnsureDirectory_swallows_chmod_oserror(tmp_path, monkeypatch):
    sDirectory = str(tmp_path / "registry")

    def _fnRaiseOnChmod(sPath, iMode):
        raise OSError("simulated chmod failure")

    monkeypatch.setattr(os, "chmod", _fnRaiseOnChmod)
    pidFileRegistry.fnEnsureDirectory(sDirectory)
    assert os.path.isdir(sDirectory)


# ---------------------------------------------------------------------------
# ffileOpenNoFollow
# ---------------------------------------------------------------------------


def test_ffileOpenNoFollow_rejects_symlink(tmp_path):
    """An attacker-placed symlink at the target path must be refused."""
    sTarget = str(tmp_path / "target.txt")
    with open(sTarget, "w") as fileHandleTarget:
        fileHandleTarget.write("sensitive")
    sLinkPath = str(tmp_path / "registry.lock")
    os.symlink(sTarget, sLinkPath)
    with pytest.raises(OSError):
        pidFileRegistry.ffileOpenNoFollow(sLinkPath)
    with open(sTarget) as fileHandleRead:
        assert fileHandleRead.read() == "sensitive"


def test_ffileOpenNoFollow_creates_missing_file(tmp_path):
    sPath = str(tmp_path / "fresh.lock")
    fileHandle = pidFileRegistry.ffileOpenNoFollow(sPath)
    try:
        assert os.path.isfile(sPath)
    finally:
        fileHandle.close()


# ---------------------------------------------------------------------------
# payload round-trip
# ---------------------------------------------------------------------------


def test_payload_round_trip_via_path(tmp_path):
    sPath = str(tmp_path / "holder.lock")
    dictPayload = {"iPid": 4321, "iPort": 8050, "sProjectName": "demo"}
    fileHandle = pidFileRegistry.ffileOpenNoFollow(sPath)
    try:
        pidFileRegistry.fnWritePayload(fileHandle, dictPayload)
    finally:
        fileHandle.close()
    assert pidFileRegistry.fdictReadPayload(sPath) == dictPayload


def test_fdictReadPayloadFromHandle_round_trips(tmp_path):
    sPath = str(tmp_path / "holder.slot")
    dictPayload = {"iPid": 99, "sRole": "hub", "iPort": 8051}
    fileHandle = pidFileRegistry.ffileOpenNoFollow(sPath)
    try:
        pidFileRegistry.fnWritePayload(fileHandle, dictPayload)
        assert (
            pidFileRegistry.fdictReadPayloadFromHandle(fileHandle)
            == dictPayload
        )
    finally:
        fileHandle.close()


def test_fdictReadPayload_returns_empty_on_missing_file(tmp_path):
    assert pidFileRegistry.fdictReadPayload(str(tmp_path / "absent")) == {}


def test_fdictReadPayload_returns_empty_on_malformed_json(tmp_path):
    sPath = str(tmp_path / "corrupt.lock")
    with open(sPath, "w") as fileHandleCorrupt:
        fileHandleCorrupt.write("{not-valid-json")
    assert pidFileRegistry.fdictReadPayload(sPath) == {}


def test_fdictReadPayload_returns_empty_on_non_dict_json(tmp_path):
    sPath = str(tmp_path / "list.lock")
    with open(sPath, "w") as fileHandleList:
        fileHandleList.write("[1, 2, 3]")
    assert pidFileRegistry.fdictReadPayload(sPath) == {}


def test_fdictReadPayloadFromHandle_empty_returns_empty_dict(tmp_path):
    sPath = str(tmp_path / "empty.lock")
    with open(sPath, "w"):
        pass
    with open(sPath, "r+") as fileHandleEmpty:
        assert pidFileRegistry.fdictReadPayloadFromHandle(
            fileHandleEmpty,
        ) == {}


# ---------------------------------------------------------------------------
# flistRegistryFiles
# ---------------------------------------------------------------------------


def test_flistRegistryFiles_filters_by_suffix(tmp_path):
    (tmp_path / "a.lock").write_text("{}")
    (tmp_path / "b.lock").write_text("{}")
    (tmp_path / "c.slot").write_text("{}")
    listPaths = pidFileRegistry.flistRegistryFiles(str(tmp_path), ".lock")
    assert sorted(os.path.basename(sPath) for sPath in listPaths) == [
        "a.lock", "b.lock",
    ]


def test_flistRegistryFiles_returns_empty_for_missing_directory(tmp_path):
    sMissing = str(tmp_path / "does-not-exist")
    assert pidFileRegistry.flistRegistryFiles(sMissing, ".lock") == []


# ---------------------------------------------------------------------------
# fnReapStaleFilesIn
# ---------------------------------------------------------------------------


def test_fnReapStaleFilesIn_removes_only_stale_files(tmp_path):
    (tmp_path / "dead.lock").write_text("{}")
    (tmp_path / "alive.lock").write_text("{}")

    def _fbIsStale(sPath):
        return os.path.basename(sPath) == "dead.lock"

    pidFileRegistry.fnReapStaleFilesIn(str(tmp_path), _fbIsStale, ".lock")
    assert not (tmp_path / "dead.lock").exists()
    assert (tmp_path / "alive.lock").exists()


def test_fnReapStaleFilesIn_ignores_other_suffixes(tmp_path):
    (tmp_path / "keep.slot").write_text("{}")

    def _fbIsStale(sPath):
        return True

    pidFileRegistry.fnReapStaleFilesIn(str(tmp_path), _fbIsStale, ".lock")
    assert (tmp_path / "keep.slot").exists()


def test_fnReapStaleFilesIn_missing_directory_is_noop(tmp_path):
    sMissing = str(tmp_path / "does-not-exist")
    pidFileRegistry.fnReapStaleFilesIn(sMissing, lambda sPath: True, ".lock")


# ---------------------------------------------------------------------------
# fnUnlinkQuietly
# ---------------------------------------------------------------------------


def test_fnUnlinkQuietly_removes_file(tmp_path):
    sPath = str(tmp_path / "gone.lock")
    open(sPath, "w").close()
    pidFileRegistry.fnUnlinkQuietly(sPath)
    assert not os.path.exists(sPath)


def test_fnUnlinkQuietly_swallows_missing_file(tmp_path):
    pidFileRegistry.fnUnlinkQuietly(str(tmp_path / "never-existed"))
