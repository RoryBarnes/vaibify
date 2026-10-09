"""Match, drift, unknown: the three answers a marker digest can get.

A marker records the git blob digest each output had when its tests
passed. The poll judges those digests against what the container
hashed. The answers are not interchangeable: only evidence makes a
drift (a differing digest, or the container reporting that the open
raised ``FileNotFoundError``), and anything short of evidence is
UNKNOWN, which invalidates nothing and is never hidden.
"""

import pytest

from vaibify.gui import hashStaleness
from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

S_BASELINE = "a" * 40
S_OTHER = "b" * 40


def _fdictEntry(sBlobSha="", **dictExtras):
    dictEntry = {"sSha256": "0" * 64 if sBlobSha else None,
                 "sBlobSha": sBlobSha or None,
                 "sSymlinkSegment": None, "bEscapesRoot": False}
    dictEntry.update(dictExtras)
    return dictEntry


@pytest.mark.falsification
def test_each_kind_of_evidence_gets_its_own_verdict():
    """Kills: collapsing unknown into drift, or drift into match.

    Every row is a distinct fact: a matching digest, a differing one,
    the container's own deletion report, an unanswered path, a torn
    read, a path that left the repo root, and an open that failed for
    some other reason. Only the second and third are drift.
    """
    for dictEntry, sExpected in (
        (_fdictEntry(S_BASELINE), "match"),
        (_fdictEntry(S_OTHER), "drift"),
        (_fdictEntry(bMissing=True), "drift"),
        (None, "unknown"),
        (_fdictEntry(bTornRead=True), "unknown"),
        (_fdictEntry(bEscapesRoot=True), "unknown"),
        (_fdictEntry(), "unknown"),
    ):
        assert hashStaleness.fsVerdictForPath(
            S_BASELINE, dictEntry) == sExpected, dictEntry


@pytest.mark.falsification
def test_an_empty_baseline_cannot_match_and_is_drift():
    """The host lane this replaces counted an unusable baseline as drift.

    Kills: letting an empty recorded digest match an unreadable file.
    """
    assert hashStaleness.fsVerdictForPath("", _fdictEntry(S_BASELINE)) == (
        "drift")
    assert hashStaleness.fsVerdictForPath(None, None) == "drift"


@pytest.mark.falsification
def test_outputs_and_inputs_are_both_judged_and_listed_sorted():
    """Both recorded baselines are judged, and the answer is sorted.

    Kills: judging only the output baselines, which would leave a
    drifted raw input reading as a passing test.
    """
    dictMarker = {
        "dictOutputHashes": {"z/out.dat": S_BASELINE, "a/out.dat": S_BASELINE},
        "dictInputHashes": {"raw/in.csv": S_BASELINE, "raw/gone.csv": S_BASELINE},
    }
    dictEntries = {
        "z/out.dat": _fdictEntry(S_OTHER),
        "a/out.dat": _fdictEntry(S_BASELINE),
        "raw/gone.csv": _fdictEntry(bMissing=True),
    }
    dictVerdicts = hashStaleness.fdictVerdictsForMarker(
        dictMarker, dictEntries)
    assert dictVerdicts["listDrifted"] == ["raw/gone.csv", "z/out.dat"]
    assert dictVerdicts["listUnknown"] == ["raw/in.csv"]


def test_a_marker_with_no_hashes_judges_nothing():
    assert hashStaleness.fdictVerdictsForMarker({}, {}) == {
        "listDrifted": [], "listUnknown": []}
    assert hashStaleness.fdictVerdictsForMarker(
        {"dictOutputHashes": {}}, {}) == {
        "listDrifted": [], "listUnknown": []}


def test_a_path_in_both_hash_sets_is_listed_once():
    dictMarker = {"dictOutputHashes": {"shared.dat": S_BASELINE},
                  "dictInputHashes": {"shared.dat": S_BASELINE}}
    dictVerdicts = hashStaleness.fdictVerdictsForMarker(dictMarker, {})
    assert dictVerdicts["listUnknown"] == ["shared.dat"]


@pytest.mark.falsification
def test_only_a_poll_snapshot_answers_digests():
    """Anything else is silence, which reads unknown for every path.

    A string root (a context that predates the snapshot) or a live
    adapter never reaches for a second way to hash a file here.

    Kills: falling back to hashing the path on the host.
    """
    assert hashStaleness.fdictHashEntriesOfSnapshot("/workspace/r") == {}
    assert hashStaleness.fdictHashEntriesOfSnapshot(None) == {}
    snapshot = SnapshotRepoFiles("/r", {}, {"x": _fdictEntry(S_BASELINE)})
    assert set(hashStaleness.fdictHashEntriesOfSnapshot(snapshot)) == {"x"}


def test_every_marker_path_is_requested_once_sorted():
    dictMarkers = {
        0: {"dictOutputHashes": {"b.dat": S_BASELINE},
            "dictInputHashes": {"a.csv": S_BASELINE}},
        1: {"dictOutputHashes": {"b.dat": S_BASELINE}},
        2: None,
    }
    assert hashStaleness.flistMarkerHashedPaths(dictMarkers) == [
        "a.csv", "b.dat"]
    assert hashStaleness.flistMarkerHashedPaths(None) == []
