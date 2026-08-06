"""Tests for Supervised-mode attribution events and permanent flags.

Falsification focus: flags must be permanent — a later clean pass
must not remove them, and a crafted "clear" (editing or deleting a
record) must break the hash chain detectably. The attribution judge
is exercised against timestamps a container could plausibly write:
timezone-less, malformed, and forward-dated.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.gui import attributionLog
from vaibify.gui.attributionLog import (
    F_ATTRIBUTION_MTIME_CUTOFF_SECONDS,
    F_ATTRIBUTION_WINDOW_SECONDS,
    S_ATTRIBUTION_EVENTS_PATH,
    S_TERMINAL_CHANNEL,
    S_TERMINAL_CLOSED_DETAIL,
    S_TERMINAL_OPENED_DETAIL,
    fbAnyEventWithinWindow,
    fbSupervisionEnabled,
    fbVerifyEventChain,
    fbVerifyFlagChain,
    fdictSummarizeSupervisionEvidence,
    flistLoadAttributionEvents,
    flistLoadFlags,
    fnAppendAttributionEvent,
    fnAppendFlag,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


def _fdictSupervisedWorkflow():
    return {"dictAiProvenance": {"dictSupervision": {"bEnabled": True}}}


def fsIsoOffsetFromNow(fSecondsFromNow):
    """Return a UTC ISO stamp offset from now by the given seconds."""
    return (
        datetime.now(timezone.utc) + timedelta(seconds=fSecondsFromNow)
    ).isoformat()


def fnAppendEventAtTime(filesRepo, sChannel, sDetail, sTimestampUtc):
    """Append one correctly-chained event at an arbitrary timestamp.

    Drives the module's own record shape and chaining so the result is
    indistinguishable from one the hub wrote; only the clock is under
    the test's control.
    """
    listEvents = flistLoadAttributionEvents(filesRepo)
    attributionLog._fnAppendJsonlRecord(
        filesRepo, S_ATTRIBUTION_EVENTS_PATH, {
            "sChannel": sChannel,
            "sActor": "hub",
            "sDetail": sDetail,
            "sTimestampUtc": sTimestampUtc,
            "sPreviousEventSha256": (
                attributionLog._fsHashChainedRecord(listEvents[-1])
                if listEvents else ""
            ),
        },
    )


def test_supervision_enabled_reads_the_config_block():
    assert fbSupervisionEnabled(_fdictSupervisedWorkflow()) is True
    assert fbSupervisionEnabled({}) is False
    assert fbSupervisionEnabled(None) is False


def test_event_append_is_noop_when_unsupervised(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendAttributionEvent(
        filesRepo, {}, "pipeline", "hub", "runAll",
    )
    assert not filesRepo.fbIsFile(S_ATTRIBUTION_EVENTS_PATH)


def test_event_append_and_window_check(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendAttributionEvent(
        filesRepo, _fdictSupervisedWorkflow(), "pipeline", "hub",
        "runAll",
    )
    assert fbAnyEventWithinWindow(filesRepo) is True
    assert fbAnyEventWithinWindow(filesRepo, fWindowSeconds=0.0) is False


def test_flag_chain_survives_appends_and_detects_tampering(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendFlag(filesRepo, "unattributed-modification", "fileA")
    fnAppendFlag(filesRepo, "unsupervised-gap", "digest changed")
    listFlags = flistLoadFlags(filesRepo)
    assert len(listFlags) == 2
    assert fbVerifyFlagChain(listFlags)
    listEdited = [dict(listFlags[0]), dict(listFlags[1])]
    listEdited[0]["sDetail"] = "innocent"
    assert not fbVerifyFlagChain(listEdited)
    assert not fbVerifyFlagChain(listFlags[1:])


def test_naive_and_malformed_timestamps_never_raise(tmp_path):
    """A container-written events log must not break the judge.

    ``datetime.fromisoformat`` accepts a timezone-less stamp happily,
    and the old aware-minus-naive subtraction then raised TypeError
    out of a read path. Both bad shapes must yield a verdict, not an
    exception.
    """
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendEventAtTime(
        filesRepo, "write-file", "stepA/out.csv", "2026-07-25T00:00:00",
    )
    fnAppendEventAtTime(
        filesRepo, "write-file", "stepA/out.csv", "not-a-timestamp",
    )
    assert fbAnyEventWithinWindow(filesRepo) in (True, False)
    assert fbAnyEventWithinWindow(filesRepo, fChangeEpoch=time.time()) in (
        True, False,
    )


@pytest.fixture
def fixtureNonUtcTimezone():
    """Run the test under a fixed, DST-free, non-UTC local timezone."""
    sPrevious = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"
    time.tzset()
    yield
    if sPrevious is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = sPrevious
    time.tzset()


@pytest.mark.falsification
def test_naive_event_timestamp_is_read_as_utc_not_local(
    tmp_path, fixtureNonUtcTimezone,
):
    """A timezone-less stamp means UTC, not the hub host's local time.

    Every writer here stamps UTC, so reading a naive value as local
    time silently relocates the event by the host's offset — fourteen
    hours under this fixture — which drops a legitimate recorded cause
    outside the window and permanently flags ordinary work.

    Kills: Return ``dtParsed`` unchanged from ``fdatetimeParseTimestampAsUtc``
    in ``attributionLog.py`` (drop the naive-to-UTC normalization).
    """
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    sNaiveStamp = datetime.now(timezone.utc).replace(
        tzinfo=None,
    ).isoformat()
    fnAppendEventAtTime(
        filesRepo, "write-file", "stepA/out.csv", sNaiveStamp,
    )
    assert fbAnyEventWithinWindow(filesRepo) is True


@pytest.mark.falsification
def test_future_dated_event_never_attributes_a_change(tmp_path):
    """A forward-dated event must not blind the watchdog.

    ``events.jsonl`` lives in the container-writable repository and the
    supervised party has a shell. One line stamped just ahead of now
    sits inside the tolerance window of every subsequent change, so
    without a lower bound on event age it attributes everything —
    silently, because a satisfied watchdog writes nothing at all.

    Kills: Replace ``if fEpoch > fNowEpoch: continue`` with
    ``if False: continue`` in ``_flistTimestampedEvents``
    (``attributionLog.py``).
    """
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendEventAtTime(
        filesRepo, "write-file", "stepA/out.csv",
        fsIsoOffsetFromNow(F_ATTRIBUTION_WINDOW_SECONDS / 2.0),
    )
    assert fbAnyEventWithinWindow(filesRepo) is False
    assert fbAnyEventWithinWindow(
        filesRepo, fChangeEpoch=time.time(),
    ) is False


def test_far_future_event_never_attributes_a_change(tmp_path):
    """The plan's literal case: an event dated 2099 attributes nothing."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendEventAtTime(
        filesRepo, "write-file", "stepA/out.csv",
        "2099-01-01T00:00:00+00:00",
    )
    assert fbAnyEventWithinWindow(filesRepo) is False


@pytest.mark.falsification
def test_open_terminal_session_attributes_a_later_change(tmp_path):
    """A terminal is an interval, not two instants.

    The terminal opens on workflow entry and stays open; a researcher
    who works for ten minutes and saves from an editor is doing
    ordinary, fully-recorded work. Judging the channel as two instants
    made that a permanent, never-removable unattributed flag on the
    channel with the longest sessions.

    Kills: Replace the final ``return iOpenCount > 0 and fSpanStart <=
    fAnchorEpoch <= fNowEpoch`` in ``_fbInsideTerminalSession``
    (``attributionLog.py``) with ``return False``.
    """
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendEventAtTime(
        filesRepo, S_TERMINAL_CHANNEL, S_TERMINAL_OPENED_DETAIL,
        fsIsoOffsetFromNow(-600.0),
    )
    assert fbAnyEventWithinWindow(
        filesRepo, fChangeEpoch=time.time() - 5.0,
    ) is True


def test_closed_terminal_session_bounds_its_interval(tmp_path):
    """After the session closes, later changes are unattributed again."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendEventAtTime(
        filesRepo, S_TERMINAL_CHANNEL, S_TERMINAL_OPENED_DETAIL,
        fsIsoOffsetFromNow(-900.0),
    )
    fnAppendEventAtTime(
        filesRepo, S_TERMINAL_CHANNEL, S_TERMINAL_CLOSED_DETAIL,
        fsIsoOffsetFromNow(-600.0),
    )
    # Inside the closed span: attributed.
    assert fbAnyEventWithinWindow(
        filesRepo, fChangeEpoch=time.time() - 700.0,
    ) is True
    # After it, and outside the window of both events: not attributed.
    assert fbAnyEventWithinWindow(
        filesRepo, fChangeEpoch=time.time() - 300.0,
    ) is False


def test_mtime_cutoff_is_derived_from_the_attribution_window():
    """The judged-age cutoff must never be narrower than the window."""
    assert (
        F_ATTRIBUTION_MTIME_CUTOFF_SECONDS >= F_ATTRIBUTION_WINDOW_SECONDS
    )


def test_event_chain_detects_editing_and_truncation(tmp_path):
    """Editing a recorded event breaks the chain; truncation shows up
    against the persisted count."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    dictWorkflow = _fdictSupervisedWorkflow()
    for iIndex in range(3):
        fnAppendAttributionEvent(
            filesRepo, dictWorkflow, "pipeline", "hub", f"run{iIndex}",
        )
    listEvents = flistLoadAttributionEvents(filesRepo)
    assert len(listEvents) == 3
    assert fbVerifyEventChain(listEvents) is True
    listEdited = [dict(dictEvent) for dictEvent in listEvents]
    listEdited[1]["sDetail"] = "innocent"
    assert fbVerifyEventChain(listEdited) is False
    # A truncated chain is still prefix-valid — that is exactly why
    # the count anchor exists.
    assert fbVerifyEventChain(listEvents[:2]) is True


def test_evidence_summary_flags_a_count_that_disagrees(tmp_path):
    """The persisted count is an anchor, never the grader."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendFlag(filesRepo, "unattributed-modification", "stepA/out.csv")
    dictWorkflow = {"dictAiProvenance": {"dictSupervision": {
        "bEnabled": True, "iUnattributedFlagCount": 1,
    }}}
    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, dictWorkflow,
    )
    assert dictEvidence["iFlagCount"] == 1
    assert dictEvidence["bPersistedFlagCountMatches"] is True
    # The supervised party edits project.json to claim a clean run.
    dictWorkflow["dictAiProvenance"]["dictSupervision"][
        "iUnattributedFlagCount"] = 0
    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, dictWorkflow,
    )
    assert dictEvidence["iFlagCount"] == 1
    assert dictEvidence["bPersistedFlagCountMatches"] is False


def test_nothing_in_the_module_removes_flags(tmp_path):
    """A clean follow-up append leaves earlier flags in place."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendFlag(filesRepo, "unattributed-modification", "fileA")
    fnAppendAttributionEvent(
        filesRepo, _fdictSupervisedWorkflow(), "pipeline", "hub",
        "runAll",
    )
    fnAppendFlag(filesRepo, "unattributed-modification", "fileB")
    listFlags = flistLoadFlags(filesRepo)
    assert [dictFlag["sDetail"] for dictFlag in listFlags] == [
        "fileA", "fileB",
    ]
    assert fbVerifyFlagChain(listFlags)
