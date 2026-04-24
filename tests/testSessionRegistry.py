"""Tests for vaibify.config.sessionRegistry."""

import json
import multiprocessing
import os
import time

import pytest


@pytest.fixture
def tmp_session_dir(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/sessions/ to a per-test tmp_path."""
    import vaibify.config.sessionRegistry as sessionRegistryModule
    monkeypatch.setattr(
        sessionRegistryModule, "_S_SESSION_DIRECTORY",
        str(tmp_path),
    )
    return tmp_path


def test_fnAcquireSessionSlot_writes_payload(tmp_session_dir):
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
    )
    fileHandleSlot = fnAcquireSessionSlot("hub", 8050)
    try:
        sPath = str(tmp_session_dir / f"{os.getpid()}.slot")
        with open(sPath) as fileHandleRead:
            dictPayload = json.load(fileHandleRead)
        assert dictPayload["iPid"] == os.getpid()
        assert dictPayload["sRole"] == "hub"
        assert dictPayload["iPort"] == 8050
        assert "sStartedIso" in dictPayload
    finally:
        fnReleaseSessionSlot(fileHandleSlot)


def test_fnReleaseSessionSlot_removes_file(tmp_session_dir):
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
    )
    fileHandleSlot = fnAcquireSessionSlot("hub", 8050)
    fnReleaseSessionSlot(fileHandleSlot)
    assert list(tmp_session_dir.glob("*.slot")) == []


def test_fiCountActiveSessions_returns_zero_when_empty(tmp_session_dir):
    from vaibify.config.sessionRegistry import fiCountActiveSessions
    assert fiCountActiveSessions() == 0


def test_fiCountActiveSessions_returns_zero_when_directory_missing(
    tmp_session_dir,
):
    from vaibify.config.sessionRegistry import fiCountActiveSessions
    tmp_session_dir.rmdir()
    assert fiCountActiveSessions() == 0


def test_fiCountActiveSessions_ignores_non_slot_files(tmp_session_dir):
    from vaibify.config.sessionRegistry import fiCountActiveSessions
    (tmp_session_dir / "stray.txt").write_text("not a slot")
    assert fiCountActiveSessions() == 0


def test_fiCountActiveSessions_skips_unheld_slot_files(tmp_session_dir):
    """A .slot file with no live flock is not counted."""
    from vaibify.config.sessionRegistry import fiCountActiveSessions
    (tmp_session_dir / "99999.slot").write_text("{}")
    assert fiCountActiveSessions() == 0


def _fnHoldSessionSlotInChild(sTempDir, eventReady):
    """Child: acquire a session slot and block until parent signals."""
    import vaibify.config.sessionRegistry as childSessionModule
    childSessionModule._S_SESSION_DIRECTORY = sTempDir
    from vaibify.config.sessionRegistry import fnAcquireSessionSlot
    fileHandleChildSlot = fnAcquireSessionSlot("hub", 9001)
    eventReady.wait(timeout=10)
    fileHandleChildSlot.close()


def test_fiCountActiveSessions_counts_child_held_slot(tmp_session_dir):
    from vaibify.config.sessionRegistry import fiCountActiveSessions
    contextSpawn = multiprocessing.get_context("fork")
    eventReady = contextSpawn.Event()
    processChild = contextSpawn.Process(
        target=_fnHoldSessionSlotInChild,
        args=(str(tmp_session_dir), eventReady),
    )
    processChild.start()
    try:
        for _ in range(50):
            if list(tmp_session_dir.glob("*.slot")):
                break
            time.sleep(0.05)
        assert fiCountActiveSessions() == 1
    finally:
        eventReady.set()
        processChild.join(timeout=5)


def test_fnAcquireSessionSlot_raises_when_limit_reached(
    tmp_session_dir, monkeypatch,
):
    import vaibify.config.sessionRegistry as sessionRegistryModule
    from vaibify.config.sessionRegistry import (
        SessionLimitExceededError, fnAcquireSessionSlot,
    )
    monkeypatch.setattr(
        sessionRegistryModule, "fiCountActiveSessions", lambda: 99,
    )
    with pytest.raises(SessionLimitExceededError) as excInfo:
        fnAcquireSessionSlot("hub", 8050)
    assert excInfo.value.iActive == 99
    assert excInfo.value.iLimit == 99


def test_I_MAX_SESSIONS_is_99():
    from vaibify.config.sessionRegistry import I_MAX_SESSIONS
    assert I_MAX_SESSIONS == 99


def test_fnAcquireSessionSlot_second_acquire_from_same_process_is_idempotent(
    tmp_session_dir,
):
    """flock is per-open-fd; same-pid reacquire creates a new slot file."""
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
    )
    fileHandleFirst = fnAcquireSessionSlot("hub", 8050)
    fnReleaseSessionSlot(fileHandleFirst)
    fileHandleSecond = fnAcquireSessionSlot("hub", 8051)
    fnReleaseSessionSlot(fileHandleSecond)


def test_stale_slot_from_dead_process_is_not_counted(tmp_session_dir):
    """A slot held by a process that exits must free its slot."""
    from vaibify.config.sessionRegistry import fiCountActiveSessions
    contextSpawn = multiprocessing.get_context("fork")
    eventReady = contextSpawn.Event()
    processChild = contextSpawn.Process(
        target=_fnHoldSessionSlotInChild,
        args=(str(tmp_session_dir), eventReady),
    )
    processChild.start()
    for _ in range(50):
        if list(tmp_session_dir.glob("*.slot")):
            break
        time.sleep(0.05)
    eventReady.set()
    processChild.join(timeout=5)
    assert fiCountActiveSessions() == 0
