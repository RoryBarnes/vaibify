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


# ---------------------------------------------------------------------------
# fnReapStaleSessionSlots
# ---------------------------------------------------------------------------


def _fiSpawnDeadPid():
    """Return the PID of a forked child that has already exited."""
    contextFork = multiprocessing.get_context("fork")
    processChild = contextFork.Process(target=lambda: None)
    processChild.start()
    processChild.join(timeout=5)
    return processChild.pid


def test_fnReapStaleSessionSlots_removes_dead_pid_slot_file(
    tmp_session_dir,
):
    from vaibify.config.sessionRegistry import fnReapStaleSessionSlots
    sDeadSlot = f"{_fiSpawnDeadPid()}.slot"
    (tmp_session_dir / sDeadSlot).write_text("{}")
    fnReapStaleSessionSlots()
    assert not (tmp_session_dir / sDeadSlot).exists()


def test_fnReapStaleSessionSlots_keeps_live_pid_slot_file(
    tmp_session_dir,
):
    """A slot named for a live PID survives even with no flock held."""
    from vaibify.config.sessionRegistry import fnReapStaleSessionSlots
    sLiveSlot = f"{os.getpid()}.slot"
    (tmp_session_dir / sLiveSlot).write_text("{}")
    fnReapStaleSessionSlots()
    assert (tmp_session_dir / sLiveSlot).exists()


def test_fnReapStaleSessionSlots_removes_malformed_slot_names(
    tmp_session_dir,
):
    from vaibify.config.sessionRegistry import fnReapStaleSessionSlots
    (tmp_session_dir / "garbage.slot").write_text("{}")
    fnReapStaleSessionSlots()
    assert not (tmp_session_dir / "garbage.slot").exists()


def test_fnReapStaleSessionSlots_handles_missing_directory(
    tmp_path, monkeypatch,
):
    import vaibify.config.sessionRegistry as sessionRegistryModule
    monkeypatch.setattr(
        sessionRegistryModule, "_S_SESSION_DIRECTORY",
        str(tmp_path / "does-not-exist"),
    )
    sessionRegistryModule.fnReapStaleSessionSlots()


def test_fnAcquireSessionSlot_reaps_dead_slots_first(tmp_session_dir):
    """Acquiring a slot cleans out files left by killed processes."""
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
    )
    sDeadSlot = f"{_fiSpawnDeadPid()}.slot"
    (tmp_session_dir / sDeadSlot).write_text("{}")
    fileHandleSlot = fnAcquireSessionSlot("hub", 8050)
    try:
        assert not (tmp_session_dir / sDeadSlot).exists()
    finally:
        fnReleaseSessionSlot(fileHandleSlot)


# ---------------------------------------------------------------------------
# fdictReadHubSlotByPort
# ---------------------------------------------------------------------------


def test_fdictReadHubSlotByPort_returns_empty_when_directory_missing(
    tmp_path, monkeypatch,
):
    import vaibify.config.sessionRegistry as sessionRegistryModule
    monkeypatch.setattr(
        sessionRegistryModule, "_S_SESSION_DIRECTORY",
        str(tmp_path / "does-not-exist"),
    )
    from vaibify.config.sessionRegistry import fdictReadHubSlotByPort
    assert fdictReadHubSlotByPort(8050) == {}


def test_fdictReadHubSlotByPort_finds_live_hub_on_matching_port(
    tmp_session_dir,
):
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
        fdictReadHubSlotByPort,
    )
    fileHandleSlot = fnAcquireSessionSlot("hub", 8077)
    try:
        dictHolder = fdictReadHubSlotByPort(8077)
        assert dictHolder.get("sRole") == "hub"
        assert dictHolder.get("iPort") == 8077
        assert dictHolder.get("iPid") == os.getpid()
    finally:
        fnReleaseSessionSlot(fileHandleSlot)


def test_fdictReadHubSlotByPort_ignores_other_port(tmp_session_dir):
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
        fdictReadHubSlotByPort,
    )
    fileHandleSlot = fnAcquireSessionSlot("hub", 8050)
    try:
        assert fdictReadHubSlotByPort(9999) == {}
    finally:
        fnReleaseSessionSlot(fileHandleSlot)


def test_fdictReadHubSlotByPort_ignores_non_hub_roles(tmp_session_dir):
    from vaibify.config.sessionRegistry import (
        fnAcquireSessionSlot, fnReleaseSessionSlot,
        fdictReadHubSlotByPort,
    )
    fileHandleSlot = fnAcquireSessionSlot("viewer", 8050)
    try:
        assert fdictReadHubSlotByPort(8050) == {}
    finally:
        fnReleaseSessionSlot(fileHandleSlot)
