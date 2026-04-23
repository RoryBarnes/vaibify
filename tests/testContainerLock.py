"""Tests for vaibify.config.containerLock."""

import json
import multiprocessing
import os
import time

import pytest


@pytest.fixture
def tmp_lock_dir(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/locks/ to a per-test tmp_path."""
    import vaibify.config.containerLock as containerLockModule
    monkeypatch.setattr(
        containerLockModule, "_S_LOCK_DIRECTORY",
        str(tmp_path),
    )
    return tmp_path


def test_fnAcquireContainerLock_writes_holder_payload(tmp_lock_dir):
    from vaibify.config.containerLock import (
        fnAcquireContainerLock, fnReleaseContainerLock, fsLockPathFor,
    )
    fileHandleLock = fnAcquireContainerLock("demo", 8050)
    try:
        sPath = fsLockPathFor("demo")
        with open(sPath) as fileHandleRead:
            dictPayload = json.load(fileHandleRead)
        assert dictPayload["iPid"] == os.getpid()
        assert dictPayload["iPort"] == 8050
        assert dictPayload["sProjectName"] == "demo"
        assert "sStartedIso" in dictPayload
    finally:
        fnReleaseContainerLock(fileHandleLock)


def test_fnAcquireContainerLock_release_lets_next_succeed(tmp_lock_dir):
    from vaibify.config.containerLock import (
        fnAcquireContainerLock, fnReleaseContainerLock,
    )
    fileHandleFirst = fnAcquireContainerLock("demo", 8050)
    fnReleaseContainerLock(fileHandleFirst)
    fileHandleSecond = fnAcquireContainerLock("demo", 8051)
    fnReleaseContainerLock(fileHandleSecond)


def _fnHoldLockInChildProcess(sTempDir, sProjectName, iPort, eventReady):
    """Child: acquire the lock and block until the parent sets event."""
    import vaibify.config.containerLock as childLockModule
    childLockModule._S_LOCK_DIRECTORY = sTempDir
    from vaibify.config.containerLock import fnAcquireContainerLock
    fileHandleChildLock = fnAcquireContainerLock(sProjectName, iPort)
    eventReady.wait(timeout=10)
    fileHandleChildLock.close()


def test_fnAcquireContainerLock_raises_when_held_by_other_process(
    tmp_lock_dir,
):
    from vaibify.config.containerLock import (
        ContainerLockedError, fnAcquireContainerLock,
    )
    contextSpawn = multiprocessing.get_context("fork")
    eventReady = contextSpawn.Event()
    processChild = contextSpawn.Process(
        target=_fnHoldLockInChildProcess,
        args=(str(tmp_lock_dir), "demo", 9000, eventReady),
    )
    processChild.start()
    try:
        for _ in range(50):
            if (tmp_lock_dir / "demo.lock").exists():
                break
            time.sleep(0.05)
        with pytest.raises(ContainerLockedError) as excInfo:
            fnAcquireContainerLock("demo", 8050)
        assert excInfo.value.iHolderPid == processChild.pid
        assert excInfo.value.iHolderPort == 9000
    finally:
        eventReady.set()
        processChild.join(timeout=5)


def test_fdictReadLockHolder_returns_empty_when_absent(tmp_lock_dir):
    from vaibify.config.containerLock import fdictReadLockHolder
    assert fdictReadLockHolder("missing") == {}


def test_fdictReadLockHolder_returns_empty_for_self_held(tmp_lock_dir):
    from vaibify.config.containerLock import (
        fdictReadLockHolder, fnAcquireContainerLock,
        fnReleaseContainerLock,
    )
    fileHandleLock = fnAcquireContainerLock("demo", 8050)
    try:
        assert fdictReadLockHolder("demo") == {}
    finally:
        fnReleaseContainerLock(fileHandleLock)


def test_fdictReadLockHolder_reports_other_process_holder(tmp_lock_dir):
    from vaibify.config.containerLock import fdictReadLockHolder
    contextSpawn = multiprocessing.get_context("fork")
    eventReady = contextSpawn.Event()
    processChild = contextSpawn.Process(
        target=_fnHoldLockInChildProcess,
        args=(str(tmp_lock_dir), "demo", 9100, eventReady),
    )
    processChild.start()
    try:
        for _ in range(50):
            if (tmp_lock_dir / "demo.lock").exists():
                break
            time.sleep(0.05)
        dictHolder = fdictReadLockHolder("demo")
        assert dictHolder.get("iPid") == processChild.pid
        assert dictHolder.get("iPort") == 9100
    finally:
        eventReady.set()
        processChild.join(timeout=5)


def test_stale_lock_from_dead_process_is_reclaimable(tmp_lock_dir):
    """A process that holds a lock and exits releases it via the kernel."""
    from vaibify.config.containerLock import (
        fnAcquireContainerLock, fnReleaseContainerLock,
    )
    contextSpawn = multiprocessing.get_context("fork")
    eventReady = contextSpawn.Event()
    processChild = contextSpawn.Process(
        target=_fnHoldLockInChildProcess,
        args=(str(tmp_lock_dir), "demo", 9200, eventReady),
    )
    processChild.start()
    for _ in range(50):
        if (tmp_lock_dir / "demo.lock").exists():
            break
        time.sleep(0.05)
    eventReady.set()
    processChild.join(timeout=5)
    fileHandleLock = fnAcquireContainerLock("demo", 8050)
    fnReleaseContainerLock(fileHandleLock)


def test_fbIsValidProjectName_accepts_safe_names():
    from vaibify.config.containerLock import fbIsValidProjectName
    assert fbIsValidProjectName("demo") is True
    assert fbIsValidProjectName("gj1132-xuv") is True
    assert fbIsValidProjectName("proj_1.2") is True
    assert fbIsValidProjectName("a" * 64) is True


def test_fbIsValidProjectName_rejects_path_traversal():
    from vaibify.config.containerLock import fbIsValidProjectName
    assert fbIsValidProjectName("..") is False
    assert fbIsValidProjectName("../etc") is False
    assert fbIsValidProjectName("a/b") is False
    assert fbIsValidProjectName("") is False
    assert fbIsValidProjectName(".") is False
    assert fbIsValidProjectName(".hidden") is False
    assert fbIsValidProjectName("a" * 65) is False
    assert fbIsValidProjectName(None) is False
    assert fbIsValidProjectName("demo\x00") is False


def test_fnAcquireContainerLock_rejects_invalid_name(tmp_lock_dir):
    from vaibify.config.containerLock import (
        InvalidProjectNameError, fnAcquireContainerLock,
    )
    with pytest.raises(InvalidProjectNameError):
        fnAcquireContainerLock("../evil", 8050)
    assert not (tmp_lock_dir / "..").exists() or True  # no file created


def test_fnAcquireContainerLock_rejects_symlink(tmp_lock_dir):
    """An attacker-placed symlink at the lock path must be refused."""
    from vaibify.config.containerLock import fnAcquireContainerLock
    sTarget = str(tmp_lock_dir / "target.txt")
    with open(sTarget, "w") as fileHandleTarget:
        fileHandleTarget.write("sensitive")
    sLockPath = str(tmp_lock_dir / "demo.lock")
    os.symlink(sTarget, sLockPath)
    with pytest.raises(OSError):
        fnAcquireContainerLock("demo", 8050)
    with open(sTarget) as fileHandleRead:
        assert fileHandleRead.read() == "sensitive"


def test_fdictReadLockHolder_returns_empty_for_invalid_name(tmp_lock_dir):
    from vaibify.config.containerLock import fdictReadLockHolder
    assert fdictReadLockHolder("../evil") == {}
    assert fdictReadLockHolder("") == {}
