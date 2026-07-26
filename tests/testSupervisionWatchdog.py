"""Tests for the Supervised-mode watchdog and interval check.

Drives the real judgment helpers against a temp repo: an attributed
change (a recorded event inside the window) is not flagged, an
unattributed one is, each change is judged exactly once (the
watermark), and the reconnect check breaches only on genuinely
distinct manifest digests — never on stubs that trivially agree.
"""

import time

import pytest

from tests.testAttributionLog import (
    fnAppendEventAtTime,
    fsIsoOffsetFromNow,
)
from vaibify.gui.attributionLog import (
    F_ATTRIBUTION_WINDOW_SECONDS,
    flistLoadFlags,
    fnAppendAttributionEvent,
)
from vaibify.gui.pipelineServer import _fnCheckSupervisedIntervalAtConnect
from vaibify.gui.routes.pipelineRoutes import (
    _fbEventChainNewlyBroken,
    _fbSnapshotHasRecentEvent,
    _flistUnattributedRecentPaths,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


def _fdictSupervisedWorkflow(sRepoPath):
    return {
        "sProjectRepoPath": sRepoPath,
        "dictAiProvenance": {"dictSupervision": {"bEnabled": True}},
    }


def test_recent_change_without_event_is_unattributed(tmp_path):
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): time.time(),
    }
    listUnattributed = _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    )
    assert len(listUnattributed) == 1


def test_recent_change_with_recorded_event_is_attributed(tmp_path):
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    fnAppendAttributionEvent(
        ffilesEnsureRepoFiles(str(tmp_path)), dictWorkflow,
        "write-file", "hub", "stepA/dataOutput.csv",
    )
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): time.time(),
    }
    assert _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    ) == []


def test_each_change_is_judged_exactly_once(tmp_path):
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): time.time(),
    }
    assert _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    )
    # Same mtimes on the next tick: the watermark has advanced, so
    # the change is not re-judged (and not re-flagged forever).
    assert _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    ) == []


def test_vaibify_internal_writes_are_not_watched(tmp_path):
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictModTimes = {
        str(tmp_path / ".vaibify" / "pipeline_state.json"): time.time(),
    }
    assert _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    ) == []


def test_stale_mtimes_are_ignored(tmp_path):
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictModTimes = {
        str(tmp_path / "stepA" / "old.csv"): time.time() - 3600,
    }
    assert _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    ) == []


def test_snapshot_event_check_reads_the_events_file(tmp_path):
    assert _fbSnapshotHasRecentEvent(str(tmp_path)) is False
    fnAppendAttributionEvent(
        ffilesEnsureRepoFiles(str(tmp_path)),
        _fdictSupervisedWorkflow(str(tmp_path)),
        "terminal", "hub", "session-opened",
    )
    assert _fbSnapshotHasRecentEvent(str(tmp_path)) is True


@pytest.mark.falsification
def test_delayed_tick_does_not_flag_an_explained_change(tmp_path):
    """A change is judged against its own mtime, not against now.

    The mtime cutoff is wider than the attribution window, so a tick
    delayed past the window — a background tab whose timers the
    browser throttles to about one a minute, a slow exec, the tick
    after a run clears — judges a change whose explaining event is
    exactly as old as the change itself. Anchoring on now flags that
    ordinary work permanently.

    Kills: Replace the body of ``_flistAttributionAnchors`` in
    ``attributionLog.py`` with ``return [fNowEpoch]`` so the change's
    own mtime is no longer an anchor.
    """
    fChangeEpoch = time.time() - 85.0
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    fnAppendEventAtTime(
        ffilesEnsureRepoFiles(str(tmp_path)), "write-file",
        "stepA/dataOutput.csv", fsIsoOffsetFromNow(-85.0),
    )
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): fChangeEpoch,
    }
    assert _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    ) == []


def test_future_dated_event_does_not_suppress_a_flag(tmp_path):
    """The watchdog still flags when the only "cause" is forward-dated."""
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    fnAppendEventAtTime(
        ffilesEnsureRepoFiles(str(tmp_path)), "write-file",
        "stepA/dataOutput.csv",
        fsIsoOffsetFromNow(F_ATTRIBUTION_WINDOW_SECONDS / 2.0),
    )
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): time.time(),
    }
    assert len(_flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    )) == 1


def test_malformed_events_file_does_not_raise_from_the_judge(tmp_path):
    """A hand-mangled events log yields a verdict, never an exception."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendEventAtTime(
        filesRepo, "write-file", "stepA/out.csv", "2026-07-25T00:00:00",
    )
    fnAppendEventAtTime(filesRepo, "write-file", "stepA/out.csv", "")
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): time.time(),
    }
    assert isinstance(_flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    ), list)
    assert _fbSnapshotHasRecentEvent(str(tmp_path)) in (True, False)


def test_clock_skew_is_recorded_rather_than_absorbed(tmp_path):
    """Container mtimes ahead of the host clock are surfaced, not hidden."""
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictModTimes = {
        str(tmp_path / "stepA" / "dataOutput.csv"): time.time() + 3600.0,
    }
    _flistUnattributedRecentPaths(
        dictWorkflow, dictModTimes, str(tmp_path),
    )
    assert dictWorkflow["dictAiProvenance"]["dictSupervision"][
        "bClockSkewSuspected"] is True


def test_broken_event_chain_is_detected_once_and_latched(tmp_path):
    """A tampered events log breaks once into a flag, not every tick."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    for iIndex in range(2):
        fnAppendAttributionEvent(
            filesRepo, dictWorkflow, "pipeline", "hub", f"run{iIndex}",
        )
    assert _fbEventChainNewlyBroken(dictWorkflow, str(tmp_path)) is False
    # Drop the first record: the survivors no longer name their
    # predecessor, so the chain does not verify.
    sText = filesRepo.fsReadText(
        ".vaibify/promptRecord/attribution/events.jsonl",
    )
    filesRepo.fnWriteTextAtomic(
        ".vaibify/promptRecord/attribution/events.jsonl",
        sText.splitlines()[1] + "\n",
    )
    assert _fbEventChainNewlyBroken(dictWorkflow, str(tmp_path)) is True
    assert _fbEventChainNewlyBroken(dictWorkflow, str(tmp_path)) is False


def _fdictBuildConnectContext(tmp_path, dictWorkflow):
    return {
        "workflows": {"cid": dictWorkflow},
        "save": lambda sId, dictWf: None,
    }


def test_reconnect_with_unchanged_manifest_closes_cleanly(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text("entry one\n")
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictCtx = _fdictBuildConnectContext(tmp_path, dictWorkflow)
    _fnCheckSupervisedIntervalAtConnect(dictCtx, "cid", dictWorkflow)
    assert flistLoadFlags(ffilesEnsureRepoFiles(str(tmp_path))) == []
    # Same digest on the next connect: still clean.
    _fnCheckSupervisedIntervalAtConnect(dictCtx, "cid", dictWorkflow)
    assert flistLoadFlags(ffilesEnsureRepoFiles(str(tmp_path))) == []


def test_reconnect_with_changed_manifest_flags_a_gap(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text("entry one\n")
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    dictCtx = _fdictBuildConnectContext(tmp_path, dictWorkflow)
    _fnCheckSupervisedIntervalAtConnect(dictCtx, "cid", dictWorkflow)
    # The repo changes while the hub is away — genuinely distinct
    # digests, not stubs that agree.
    (tmp_path / "MANIFEST.sha256").write_text("entry one\nentry two\n")
    _fnCheckSupervisedIntervalAtConnect(dictCtx, "cid", dictWorkflow)
    listFlags = flistLoadFlags(ffilesEnsureRepoFiles(str(tmp_path)))
    assert len(listFlags) == 1
    assert listFlags[0]["sFlagKind"] == "unsupervised-gap"
    assert dictWorkflow["dictAiProvenance"]["dictSupervision"][
        "iUnattributedFlagCount"] == 1
