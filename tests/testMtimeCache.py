"""Tests for the mtime-keyed blob SHA cache."""

import json
import os
import time

import pytest

from vaibify.gui import mtimeCache


def _fsWrite(sRoot, sRelPath, sContent=""):
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath) or sAbsPath, exist_ok=True)
    if not os.path.isdir(sAbsPath):
        with open(sAbsPath, "w") as f:
            f.write(sContent)
    return sAbsPath


# ----------------------------------------------------------------------
# fdictLoadCache / fnSaveCache
# ----------------------------------------------------------------------


def test_fdictLoadCache_returns_empty_when_absent(tmp_path):
    assert mtimeCache.fdictLoadCache(str(tmp_path)) == {}


def test_fdictLoadCache_returns_empty_for_corrupt_json(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), ".vaibify"), exist_ok=True)
    with open(os.path.join(
        str(tmp_path), ".vaibify", "mtime_cache.json",
    ), "w") as f:
        f.write("{not-json")
    assert mtimeCache.fdictLoadCache(str(tmp_path)) == {}


def test_fdictLoadCache_returns_empty_for_list(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), ".vaibify"), exist_ok=True)
    with open(os.path.join(
        str(tmp_path), ".vaibify", "mtime_cache.json",
    ), "w") as f:
        f.write("[1,2,3]")
    assert mtimeCache.fdictLoadCache(str(tmp_path)) == {}


def test_fnSaveCache_round_trip(tmp_path):
    dictIn = {"a/b.csv": {"fMtime": 1.5, "sBlobSha": "abc"}}
    mtimeCache.fnSaveCache(str(tmp_path), dictIn)
    dictOut = mtimeCache.fdictLoadCache(str(tmp_path))
    assert dictOut == dictIn


def test_fnSaveCache_creates_parent_directory(tmp_path):
    mtimeCache.fnSaveCache(str(tmp_path), {})
    assert os.path.isfile(
        os.path.join(str(tmp_path), ".vaibify", "mtime_cache.json")
    )


# ----------------------------------------------------------------------
# fsBlobShaForFile
# ----------------------------------------------------------------------


def test_fsBlobShaForFile_returns_empty_for_missing_file(tmp_path):
    dictCache = {}
    assert mtimeCache.fsBlobShaForFile(
        str(tmp_path), "ghost.csv", dictCache,
    ) == ""
    assert "ghost.csv" not in dictCache


def test_fsBlobShaForFile_removes_stale_entry_for_deleted_file(tmp_path):
    dictCache = {"ghost.csv": {"fMtime": 1.0, "sBlobSha": "abc"}}
    mtimeCache.fsBlobShaForFile(str(tmp_path), "ghost.csv", dictCache)
    assert "ghost.csv" not in dictCache


def test_fsBlobShaForFile_caches_on_first_call(tmp_path):
    _fsWrite(str(tmp_path), "data.csv", "hello")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    assert len(sSha) == 40
    assert "data.csv" in dictCache
    assert dictCache["data.csv"]["sBlobSha"] == sSha


def test_fsBlobShaForFile_uses_cache_when_mtime_unchanged(tmp_path):
    sHostPath = _fsWrite(str(tmp_path), "data.csv", "hello")
    dictCache = {}
    mtimeCache.fsBlobShaForFile(str(tmp_path), "data.csv", dictCache)
    dictCache["data.csv"]["sBlobSha"] = "FAKE-CACHED-VALUE"
    sResult = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    assert sResult == "FAKE-CACHED-VALUE"


def test_fsBlobShaForFile_recomputes_when_mtime_changed(tmp_path):
    sHostPath = _fsWrite(str(tmp_path), "data.csv", "hello")
    dictCache = {}
    sShaBefore = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    time.sleep(0.01)
    with open(sHostPath, "w") as f:
        f.write("world")
    fFutureMtime = os.path.getmtime(sHostPath) + 5
    os.utime(sHostPath, (fFutureMtime, fFutureMtime))
    sShaAfter = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    assert sShaAfter != sShaBefore
    assert dictCache["data.csv"]["sBlobSha"] == sShaAfter


def test_fsBlobShaForFile_matches_git_hash_object(tmp_path):
    _fsWrite(str(tmp_path), "fixture.txt", "what is up, doc?")
    dictCache = {}
    sResult = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fixture.txt", dictCache,
    )
    assert sResult == "bd9dbf5aae1a3862dd1526723246b20206e5fc37"


def test_fsBlobShaForFile_handles_nested_paths(tmp_path):
    _fsWrite(str(tmp_path), "step1/Plot/fig.pdf", "pdf-bytes")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "step1/Plot/fig.pdf", dictCache,
    )
    assert len(sSha) == 40


# ----------------------------------------------------------------------
# fbFileMatchesDigest
# ----------------------------------------------------------------------


def test_fbFileMatchesDigest_returns_true_when_match(tmp_path):
    _fsWrite(str(tmp_path), "data.csv", "hello")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    assert mtimeCache.fbFileMatchesDigest(
        str(tmp_path), "data.csv", sSha, dictCache,
    )


def test_fbFileMatchesDigest_returns_false_on_mismatch(tmp_path):
    _fsWrite(str(tmp_path), "data.csv", "hello")
    dictCache = {}
    assert not mtimeCache.fbFileMatchesDigest(
        str(tmp_path), "data.csv", "0" * 40, dictCache,
    )


def test_fbFileMatchesDigest_returns_false_for_missing_file(tmp_path):
    dictCache = {}
    assert not mtimeCache.fbFileMatchesDigest(
        str(tmp_path), "ghost.csv", "0" * 40, dictCache,
    )


def test_fbFileMatchesDigest_returns_false_for_empty_baseline(tmp_path):
    _fsWrite(str(tmp_path), "data.csv", "hello")
    dictCache = {}
    assert not mtimeCache.fbFileMatchesDigest(
        str(tmp_path), "data.csv", "", dictCache,
    )


# ----------------------------------------------------------------------
# fsSha256ForFile
# ----------------------------------------------------------------------


def test_fsSha256ForFile_returns_empty_for_missing(tmp_path):
    dictCache = {}
    sSha = mtimeCache.fsSha256ForFile(
        str(tmp_path), "ghost.csv", dictCache,
    )
    assert sSha == ""


def test_fsSha256ForFile_purges_stale_entry_for_deleted_file(tmp_path):
    """A previously cached SHA-256 must not survive deletion of its file.

    Otherwise a deleted output could appear hash-clean against a
    manifest entry, masking a regression in the dashboard's drift
    badge. The cache entry must vanish so the next read returns ``""``
    — the contract every caller depends on.
    """
    dictCache = {
        "ghost.csv": {"fMtime": 1.0, "sSha256": "a" * 64},
    }
    sSha = mtimeCache.fsSha256ForFile(
        str(tmp_path), "ghost.csv", dictCache,
    )
    assert sSha == ""
    assert "ghost.csv" not in dictCache


def test_fsSha256ForFile_caches_and_reuses_when_unchanged(tmp_path):
    sHostPath = _fsWrite(str(tmp_path), "data.csv", "alpha")
    dictCache = {}
    sFirst = mtimeCache.fsSha256ForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    assert len(sFirst) == 64
    dictCache["data.csv"]["sSha256"] = "FAKE-CACHED-SHA256"
    sSecond = mtimeCache.fsSha256ForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    assert sSecond == "FAKE-CACHED-SHA256"


def test_fsSha256ForFile_sha1_and_sha256_coexist(tmp_path):
    """Storing the SHA-256 must not destroy the SHA-1 entry, and vice versa."""
    _fsWrite(str(tmp_path), "data.csv", "payload")
    dictCache = {}
    sSha1 = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    sSha256 = mtimeCache.fsSha256ForFile(
        str(tmp_path), "data.csv", dictCache,
    )
    dictEntry = dictCache["data.csv"]
    assert dictEntry.get("sBlobSha") == sSha1
    assert dictEntry.get("sSha256") == sSha256
