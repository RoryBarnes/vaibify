"""Tests for vaibify.config.processLiveness."""

import contextlib
import datetime
import multiprocessing
import os
import subprocess
import sys
import time

import pytest


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
# fdatetimeParseClaimIso
# ---------------------------------------------------------------------------


def test_fdatetimeParseClaimIso_reads_a_naive_claim_as_local_time():
    from vaibify.config.processLiveness import fdatetimeParseClaimIso
    dtParsed = fdatetimeParseClaimIso("2026-06-25T12:30:00")
    dtLocal = datetime.datetime(2026, 6, 25, 12, 30, 0).astimezone()
    assert dtParsed == dtLocal
    assert dtParsed.utcoffset() == datetime.timedelta(0)


def test_fdatetimeParseClaimIso_normalizes_an_offset_claim_to_utc():
    from vaibify.config.processLiveness import fdatetimeParseClaimIso
    dtParsed = fdatetimeParseClaimIso("2026-06-25T17:30:00+05:00")
    assert dtParsed == datetime.datetime(
        2026, 6, 25, 12, 30, 0, tzinfo=datetime.timezone.utc)
    assert dtParsed.utcoffset() == datetime.timedelta(0)


def test_fdatetimeParseClaimIso_returns_none_for_empty_or_malformed():
    from vaibify.config.processLiveness import fdatetimeParseClaimIso
    assert fdatetimeParseClaimIso("") is None
    assert fdatetimeParseClaimIso(None) is None
    assert fdatetimeParseClaimIso("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# fdatetimeReadProcessStartClock
# ---------------------------------------------------------------------------


def test_fdatetimeReadProcessStartClock_returns_datetime_for_self():
    from vaibify.config.processLiveness import fdatetimeReadProcessStartClock
    dtStart = fdatetimeReadProcessStartClock(os.getpid())
    assert isinstance(dtStart, datetime.datetime)


def test_fdatetimeReadProcessStartClock_returns_none_for_invalid_pid():
    from vaibify.config.processLiveness import fdatetimeReadProcessStartClock
    assert fdatetimeReadProcessStartClock(0) is None
    assert fdatetimeReadProcessStartClock(-1) is None


def test_fdatetimeReadProcessStartClock_returns_none_on_unparsable_output(
    monkeypatch,
):
    from vaibify.config import processLiveness
    monkeypatch.setattr(
        processLiveness,
        "_fsReadElapsedTimeFromProcessStatus",
        lambda iPid: "garbage start time",
    )
    assert processLiveness.fdatetimeReadProcessStartClock(os.getpid()) is None


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
        processLiveness, "fdatetimeReadProcessStartClock", lambda iPid: None,
    )
    assert processLiveness.fbIsProcessAliveSince(
        os.getpid(), "2000-01-01T00:00:00",
    ) is True


def test_fbIsProcessAliveSince_conservative_when_claim_empty():
    """An absent claim falls back to the PID-only check (alive)."""
    from vaibify.config.processLiveness import fbIsProcessAliveSince
    assert fbIsProcessAliveSince(os.getpid(), None) is True
    assert fbIsProcessAliveSince(os.getpid(), "") is True


# ---------------------------------------------------------------------------
# Time zones: a claim and a start are instants, not wall-clock readings
# ---------------------------------------------------------------------------

S_ZONE_WEST_OF_UTC = "UTC+12"
S_ZONE_EAST_OF_UTC = "UTC-14"


@contextlib.contextmanager
def contextTimeZone(sPosixZone):
    sPrevious = os.environ.get("TZ")
    os.environ["TZ"] = sPosixZone
    time.tzset()
    try:
        yield
    finally:
        if sPrevious is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = sPrevious
        time.tzset()


@pytest.fixture
def processFreshHolder():
    """A live child that started a moment ago."""
    processChild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        yield processChild
    finally:
        processChild.kill()
        processChild.wait()


@pytest.mark.falsification
def testAZoneChangeBetweenClaimAndCheckDoesNotMakeALiveHolderLookRecycled(
    processFreshHolder,
):
    """Kills: a claim written as naive local time, read in another zone."""
    from vaibify.config import processLiveness
    with contextTimeZone(S_ZONE_WEST_OF_UTC):
        sClaimIso = processLiveness.fsNowClaimIso()
    with contextTimeZone(S_ZONE_EAST_OF_UTC):
        assert processLiveness.fbIsProcessAliveSince(
            processFreshHolder.pid, sClaimIso) is True
    with contextTimeZone(S_ZONE_WEST_OF_UTC):
        assert processLiveness.fbIsProcessAliveSince(
            processFreshHolder.pid, sClaimIso) is True


@pytest.mark.falsification
def testTheStartClockDoesNotMoveWithTheLocalZone(processFreshHolder):
    """Kills: a start time read as local wall-clock text from ps."""
    from vaibify.config import processLiveness
    with contextTimeZone(S_ZONE_WEST_OF_UTC):
        datetimeWest = processLiveness.fdatetimeReadProcessStartClock(
            processFreshHolder.pid)
    with contextTimeZone(S_ZONE_EAST_OF_UTC):
        datetimeEast = processLiveness.fdatetimeReadProcessStartClock(
            processFreshHolder.pid)
    fSecondsApart = abs((datetimeWest - datetimeEast).total_seconds())
    assert fSecondsApart < 5.0


def testARecycledPidIsStillDetectedAfterAZoneChange(processFreshHolder):
    """The holder named by the claim died; a newer process wears its pid."""
    from vaibify.config import processLiveness
    datetimeAnHourAgo = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=1)
    )
    sOldClaimIso = datetimeAnHourAgo.isoformat()
    for sZone in (S_ZONE_WEST_OF_UTC, S_ZONE_EAST_OF_UTC):
        with contextTimeZone(sZone):
            assert processLiveness.fbIsProcessAliveSince(
                processFreshHolder.pid, sOldClaimIso) is False


def testAClaimWithAnExplicitOffsetNamesTheSameInstant(processFreshHolder):
    from vaibify.config import processLiveness
    datetimeNow = datetime.datetime.now(datetime.timezone.utc)
    sClaimIso = datetimeNow.astimezone(
        datetime.timezone(datetime.timedelta(hours=5))).isoformat()
    assert processLiveness.fbIsProcessAliveSince(
        processFreshHolder.pid, sClaimIso) is True


@pytest.mark.parametrize("sElapsed, dExpectedSeconds", [
    ("05:07", 307.0),
    ("  00:00\n", 0.0),
    ("01:05:07", 3907.0),
    ("2-03:04:05", 2 * 86400 + 3 * 3600 + 4 * 60 + 5.0),
    ("123-00:00:01", 123 * 86400 + 1.0),
])
def testElapsedTimeFormatsOfPsAreParsed(sElapsed, dExpectedSeconds):
    from vaibify.config.processLiveness import fdParseElapsedSeconds
    assert fdParseElapsedSeconds(sElapsed) == dExpectedSeconds


@pytest.mark.parametrize("sElapsed", ["", None, "12", "1:2", "a:bc", "1-2"])
def testUnparseableElapsedTimeIsNone(sElapsed):
    from vaibify.config.processLiveness import fdParseElapsedSeconds
    assert fdParseElapsedSeconds(sElapsed) is None


def testTheStartClockIsNowMinusTheElapsedTime(monkeypatch):
    from vaibify.config import processLiveness
    monkeypatch.setattr(
        processLiveness, "_fsReadElapsedTimeFromProcessStatus",
        lambda iPid: "01:00:00",
    )
    datetimeBefore = datetime.datetime.now(datetime.timezone.utc)
    datetimeStart = processLiveness.fdatetimeReadProcessStartClock(os.getpid())
    datetimeAfter = datetime.datetime.now(datetime.timezone.utc)
    assert datetimeBefore - datetime.timedelta(hours=1) <= datetimeStart
    assert datetimeStart <= datetimeAfter - datetime.timedelta(hours=1)
