"""A step is judged against the data state of every run its results stand on.

Each run recorded the hashes of the files it was obtained at, so each is
its own data state. The STEP is drifted only when the files match none
of them: a step whose files match one run is clean, because that run's
results are about exactly these bytes. Unknown never outranks a match
and never becomes drift.
"""

import pytest

from vaibify.gui import hashStaleness
from vaibify.gui import testMarkerContract

S_OLD_DIGEST = "a" * 40
S_NEW_DIGEST = "b" * 40
S_THIRD_DIGEST = "c" * 40
S_PATH = "StepDir/out.dat"


def _fdictRun(sDigest, fTimestamp):
    return {
        "fTimestamp": fTimestamp, "sRunAtUtc": "2026-10-01T00:00:00Z",
        "iExitStatus": 0, "dictOutputHashes": {S_PATH: sDigest},
        "dictInputHashes": {},
    }


def _fdictMarkerOfRuns(dictRuns):
    dictMarker = testMarkerContract.fdictNormalizeMarker({})
    dictMarker["dictRuns"] = dictRuns
    return dictMarker


def _fdictSnapshotAnswering(sDigest, listPaths=(S_PATH,)):
    if sDigest is None:
        return {}
    return {sPath: {
        "sSha256": "0" * 64, "sBlobSha": sDigest, "sSymlinkSegment": None,
        "bEscapesRoot": False} for sPath in listPaths}


@pytest.mark.falsification
def test_a_step_whose_files_match_any_run_is_clean():
    """Kills: letting a drifted older run condemn a step a newer run vouches for."""
    dictMarker = _fdictMarkerOfRuns({
        "older": _fdictRun(S_OLD_DIGEST, 1.0),
        "newer": _fdictRun(S_NEW_DIGEST, 2.0)})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(S_NEW_DIGEST))
    assert dictAnswer["listDrifted"] == []
    assert dictAnswer["listUnknown"] == []
    assert dictAnswer["dictRunVerdicts"] == {
        "older": "drift", "newer": "match"}


@pytest.mark.falsification
def test_a_step_matching_no_run_is_drifted_by_its_newest_drifted_run():
    """Kills: reading the oldest drifted run's paths, or none, for the step."""
    dictMarker = _fdictMarkerOfRuns({
        "older": dict(_fdictRun(S_OLD_DIGEST, 1.0), dictOutputHashes={
            "StepDir/oldOnly.dat": S_OLD_DIGEST}),
        "newer": _fdictRun(S_NEW_DIGEST, 2.0)})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(
            S_THIRD_DIGEST, [S_PATH, "StepDir/oldOnly.dat"]))
    assert dictAnswer["listDrifted"] == [S_PATH]
    assert dictAnswer["dictRunVerdicts"]["older"] == "drift"
    assert dictAnswer["dictRunVerdicts"]["newer"] == "drift"


@pytest.mark.falsification
def test_an_unchecked_run_is_unknown_for_the_step_and_never_drift():
    """Kills: counting a run the snapshot could not answer for as drift."""
    dictMarker = _fdictMarkerOfRuns({"only": _fdictRun(S_OLD_DIGEST, 1.0)})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(None))
    assert dictAnswer["listDrifted"] == []
    assert dictAnswer["listUnknown"] == [S_PATH]
    assert dictAnswer["dictRunVerdicts"] == {"only": "unknown"}


@pytest.mark.falsification
def test_a_matching_run_does_not_vouch_for_another_runs_unchecked_paths():
    """A match shows the files are in ONE recorded state, not in every one.

    Run B recorded a path the snapshot did not answer, and run B holds
    results of its own. Run A matching clears the step of blanket
    invalidation (no drift), but it cannot say the data behind B's
    results is still what B ran on, so the step keeps B's paths as
    unchecked rather than reading plain green.

    Kills: letting any matching run empty the step's unknown paths.
    """
    dictMarker = _fdictMarkerOfRuns({
        "unchecked": dict(_fdictRun(S_OLD_DIGEST, 1.0), dictOutputHashes={
            "StepDir/unanswered.dat": S_OLD_DIGEST}),
        "matching": _fdictRun(S_NEW_DIGEST, 2.0)})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(S_NEW_DIGEST))
    assert dictAnswer["listDrifted"] == []
    assert dictAnswer["listUnknown"] == ["StepDir/unanswered.dat"]
    assert dictAnswer["dictRunVerdicts"] == {
        "unchecked": "unknown", "matching": "match"}


@pytest.mark.falsification
def test_every_unchecked_run_contributes_its_own_paths():
    """Two runs, two different unanswered paths: the step names both.

    Reporting only the newest unchecked run would leave the older run's
    results looking verified.

    Kills: reading the unknown paths of the newest unchecked run alone.
    """
    dictMarker = _fdictMarkerOfRuns({
        "older": dict(_fdictRun(S_OLD_DIGEST, 1.0), dictOutputHashes={
            "StepDir/olderOnly.dat": S_OLD_DIGEST}),
        "newer": dict(_fdictRun(S_NEW_DIGEST, 2.0), dictOutputHashes={
            "StepDir/newerOnly.dat": S_NEW_DIGEST})})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(None))
    assert dictAnswer["listUnknown"] == [
        "StepDir/newerOnly.dat", "StepDir/olderOnly.dat"]


def test_a_drifted_step_reports_no_unknown_paths():
    """No run matches and one drifted: the step is invalidated outright."""
    dictMarker = _fdictMarkerOfRuns({
        "drifted": dict(_fdictRun(S_OLD_DIGEST, 1.0)),
        "unchecked": dict(_fdictRun(S_OLD_DIGEST, 2.0), dictOutputHashes={
            "StepDir/unanswered.dat": S_OLD_DIGEST})})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(S_NEW_DIGEST))
    assert dictAnswer["listDrifted"] == [S_PATH]
    assert dictAnswer["listUnknown"] == []


def test_a_run_that_recorded_no_hashes_has_no_verdict_and_judges_nothing():
    dictMarker = _fdictMarkerOfRuns({
        "bare": dict(_fdictRun(S_OLD_DIGEST, 1.0), dictOutputHashes={})})
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictSnapshotAnswering(S_NEW_DIGEST))
    assert dictAnswer["listDrifted"] == []
    assert dictAnswer["listUnknown"] == []
    assert dictAnswer["dictRunVerdicts"] == {"bare": "none"}


def test_a_marker_in_the_old_shape_is_one_run_called_legacy():
    dictOld = {
        "fTimestamp": 5.0, "sRunAtUtc": "2026-10-01T00:00:00Z",
        "dictOutputHashes": {S_PATH: S_OLD_DIGEST}}
    dictAnswer = hashStaleness.fdictVerdictsForMarkerRuns(
        dictOld, _fdictSnapshotAnswering(S_NEW_DIGEST))
    assert dictAnswer["listDrifted"] == [S_PATH]
    assert list(dictAnswer["dictRunVerdicts"]) == ["legacy"]
