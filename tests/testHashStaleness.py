"""Tests for content-hash-based staleness detection."""

import os

import pytest

from tests.snapshotProgramHarness import fsetDriftedAgainstRealSnapshot
from vaibify.gui import hashStaleness, mtimeCache


def _fsWrite(sRoot, sRelPath, sContent=""):
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath) or sAbsPath, exist_ok=True)
    if not os.path.isdir(sAbsPath):
        with open(sAbsPath, "w") as f:
            f.write(sContent)


def test_fbMarkerHasHashes_true_when_dict_populated():
    assert hashStaleness.fbMarkerHasHashes(
        {"dictOutputHashes": {"f.csv": "a" * 40}}
    )


def test_fbMarkerHasHashes_false_when_empty():
    assert not hashStaleness.fbMarkerHasHashes(
        {"dictOutputHashes": {}}
    )


def test_fbMarkerHasHashes_false_when_missing_field():
    assert not hashStaleness.fbMarkerHasHashes(
        {"iExitStatus": 0}
    )


def test_fbMarkerHasHashes_false_when_not_a_dict():
    assert not hashStaleness.fbMarkerHasHashes(None)
    assert not hashStaleness.fbMarkerHasHashes("not-a-dict")


def test_verdicts_empty_when_no_baseline(tmp_path):
    dictMarker = {"dictResults": {"sUnitTest": "passed"}}
    setStale = fsetDriftedAgainstRealSnapshot(dictMarker, str(tmp_path))
    assert setStale == set()


def test_verdicts_return_mismatched_files(tmp_path):
    _fsWrite(str(tmp_path), "step1/f.csv", "current")
    dictMarker = {
        "dictOutputHashes": {
            "step1/f.csv": "0" * 40,
        },
    }
    setStale = fsetDriftedAgainstRealSnapshot(dictMarker, str(tmp_path))
    assert setStale == {"step1/f.csv"}


def test_verdicts_clean_when_hashes_match(tmp_path):
    """The host helper's digest and the snapshot's are the same digest."""
    _fsWrite(str(tmp_path), "step1/f.csv", "content")
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "step1/f.csv", {},
    )
    dictMarker = {"dictOutputHashes": {"step1/f.csv": sSha}}
    setStale = fsetDriftedAgainstRealSnapshot(dictMarker, str(tmp_path))
    assert setStale == set()


def test_verdicts_missing_files_marked_stale(tmp_path):
    dictMarker = {"dictOutputHashes": {"step1/ghost.csv": "a" * 40}}
    setStale = fsetDriftedAgainstRealSnapshot(dictMarker, str(tmp_path))
    assert setStale == {"step1/ghost.csv"}


def test_verdicts_mixed_results(tmp_path):
    _fsWrite(str(tmp_path), "step1/clean.csv", "clean")
    _fsWrite(str(tmp_path), "step1/dirty.csv", "dirty")
    sCleanSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "step1/clean.csv", {},
    )
    dictMarker = {
        "dictOutputHashes": {
            "step1/clean.csv": sCleanSha,
            "step1/dirty.csv": "0" * 40,
            "step1/ghost.csv": "f" * 40,
        },
    }
    setStale = fsetDriftedAgainstRealSnapshot(dictMarker, str(tmp_path))
    assert setStale == {"step1/dirty.csv", "step1/ghost.csv"}
