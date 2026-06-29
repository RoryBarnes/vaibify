"""Tests for vaibify.config.processLiveness."""

import datetime
import multiprocessing
import os


def _fiSpawnDeadPid():
    """Return the PID of a forked child that has already exited."""
    contextFork = multiprocessing.get_context("fork")
    processChild = contextFork.Process(target=lambda: None)
    processChild.start()
    processChild.join(timeout=5)
    return processChild.pid


def test_fbIsProcessAlive_true_for_current_process():
    from vaibify.config.processLiveness import fbIsProcessAlive
    assert fbIsProcessAlive(os.getpid()) is True


def test_fbIsProcessAlive_false_for_exited_child():
    from vaibify.config.processLiveness import fbIsProcessAlive
    assert fbIsProcessAlive(_fiSpawnDeadPid()) is False


def test_fbIsProcessAlive_false_for_invalid_pids():
    from vaibify.config.processLiveness import fbIsProcessAlive
    assert fbIsProcessAlive(0) is False
    assert fbIsProcessAlive(-1) is False
    assert fbIsProcessAlive(None) is False
    assert fbIsProcessAlive("8050") is False
    assert fbIsProcessAlive(True) is False


def test_fbIsProcessAlive_true_on_permission_error(monkeypatch):
    """EPERM means the PID exists under another user: alive."""
    from vaibify.config import processLiveness

    def _fnRaisePermissionError(iPid, iSignal):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(processLiveness.os, "kill", _fnRaisePermissionError)
    assert processLiveness.fbIsProcessAlive(12345) is True


# ---------------------------------------------------------------------------
# fdtParseClaimIso
# ---------------------------------------------------------------------------


def test_fdtParseClaimIso_parses_naive_iso():
    from vaibify.config.processLiveness import fdtParseClaimIso
    dtParsed = fdtParseClaimIso("2026-06-25T12:30:00")
    assert dtParsed == datetime.datetime(2026, 6, 25, 12, 30, 0)


def test_fdtParseClaimIso_normalizes_aware_to_naive_local():
    from vaibify.config.processLiveness import fdtParseClaimIso
    dtParsed = fdtParseClaimIso("2026-06-25T12:30:00+00:00")
    assert dtParsed.tzinfo is None


def test_fdtParseClaimIso_returns_none_for_empty_or_malformed():
    from vaibify.config.processLiveness import fdtParseClaimIso
    assert fdtParseClaimIso("") is None
    assert fdtParseClaimIso(None) is None
    assert fdtParseClaimIso("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# fdtReadProcessStartClock
# ---------------------------------------------------------------------------


def test_fdtReadProcessStartClock_returns_datetime_for_self():
    from vaibify.config.processLiveness import fdtReadProcessStartClock
    dtStart = fdtReadProcessStartClock(os.getpid())
    assert isinstance(dtStart, datetime.datetime)


def test_fdtReadProcessStartClock_returns_none_for_invalid_pid():
    from vaibify.config.processLiveness import fdtReadProcessStartClock
    assert fdtReadProcessStartClock(0) is None
    assert fdtReadProcessStartClock(-1) is None


def test_fdtReadProcessStartClock_returns_none_on_unparsable_output(
    monkeypatch,
):
    from vaibify.config import processLiveness
    monkeypatch.setattr(
        processLiveness,
        "_fsReadStartTimeFromProcessStatus",
        lambda iPid: "garbage start time",
    )
    assert processLiveness.fdtReadProcessStartClock(os.getpid()) is None


# ---------------------------------------------------------------------------
# fbIsProcessAliveSince
# ---------------------------------------------------------------------------


def test_fbIsProcessAliveSince_false_for_dead_pid():
    from vaibify.config.processLiveness import fbIsProcessAliveSince
    sFuture = datetime.datetime.now().isoformat()
    assert fbIsProcessAliveSince(_fiSpawnDeadPid(), sFuture) is False


def test_fbIsProcessAliveSince_true_for_live_pid_past_claim():
    """A live process whose claim postdates its real start is genuine."""
    from vaibify.config.processLiveness import fbIsProcessAliveSince
    sNow = datetime.datetime.now().isoformat()
    assert fbIsProcessAliveSince(os.getpid(), sNow) is True


def test_fbIsProcessAliveSince_false_for_live_pid_ancient_claim():
    """A live PID started long after an ancient claim looks recycled."""
    from vaibify.config.processLiveness import fbIsProcessAliveSince
    assert fbIsProcessAliveSince(os.getpid(), "2000-01-01T00:00:00") is False


def test_fbIsProcessAliveSince_conservative_when_start_unreadable(
    monkeypatch,
):
    """Unreadable start time falls back to the PID-only check (alive)."""
    from vaibify.config import processLiveness
    monkeypatch.setattr(
        processLiveness, "fdtReadProcessStartClock", lambda iPid: None,
    )
    assert processLiveness.fbIsProcessAliveSince(
        os.getpid(), "2000-01-01T00:00:00",
    ) is True


def test_fbIsProcessAliveSince_conservative_when_claim_empty():
    """An absent claim falls back to the PID-only check (alive)."""
    from vaibify.config.processLiveness import fbIsProcessAliveSince
    assert fbIsProcessAliveSince(os.getpid(), None) is True
    assert fbIsProcessAliveSince(os.getpid(), "") is True
