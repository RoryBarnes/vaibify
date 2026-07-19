"""Tests for the Supervised-mode watchdog and interval check.

Drives the real judgment helpers against a temp repo: an attributed
change (a recorded event inside the window) is not flagged, an
unattributed one is, each change is judged exactly once (the
watermark), and the reconnect check breaches only on genuinely
distinct manifest digests — never on stubs that trivially agree.
"""

import time

from vaibify.gui.attributionLog import (
    flistLoadFlags,
    fnAppendAttributionEvent,
)
from vaibify.gui.pipelineServer import _fnCheckSupervisedIntervalAtConnect
from vaibify.gui.routes.pipelineRoutes import (
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
