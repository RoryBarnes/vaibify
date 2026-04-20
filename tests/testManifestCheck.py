"""Tests for the pre-push manifest check."""

import os

import pytest

from vaibify.gui import manifestCheck


def _fsWrite(sRoot, sRelPath, sContent=""):
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath) or sAbsPath, exist_ok=True)
    if not os.path.isdir(sAbsPath):
        with open(sAbsPath, "w") as f:
            f.write(sContent)


def _fdictGit(dictFileStates=None, bIsRepo=True):
    return {
        "bIsRepo": bIsRepo,
        "sHeadSha": "abc",
        "sBranch": "main",
        "iAhead": 0,
        "iBehind": 0,
        "dictFileStates": dictFileStates or {},
        "sRefreshedAt": "2026-04-18T12:00:00Z",
        "sReason": "",
    }


# ----------------------------------------------------------------------
# flistFilesNeedingCommit
# ----------------------------------------------------------------------


def test_flistFilesNeedingCommit_empty_when_everything_clean():
    listResult = manifestCheck.flistFilesNeedingCommit(
        ["a.py", "b.py"], _fdictGit(),
    )
    assert listResult == []


def test_flistFilesNeedingCommit_flags_untracked():
    listResult = manifestCheck.flistFilesNeedingCommit(
        ["a.py", "new.py"],
        _fdictGit({"new.py": "untracked"}),
    )
    assert listResult == [{
        "sPath": "new.py", "sState": manifestCheck.S_STATE_UNTRACKED,
    }]


def test_flistFilesNeedingCommit_flags_dirty():
    listResult = manifestCheck.flistFilesNeedingCommit(
        ["figure.pdf"],
        _fdictGit({"figure.pdf": "dirty"}),
    )
    assert listResult == [{
        "sPath": "figure.pdf", "sState": manifestCheck.S_STATE_DIRTY,
    }]


def test_flistFilesNeedingCommit_flags_staged():
    listResult = manifestCheck.flistFilesNeedingCommit(
        ["workflow.json"],
        _fdictGit({"workflow.json": "uncommitted"}),
    )
    assert listResult == [{
        "sPath": "workflow.json", "sState": manifestCheck.S_STATE_STAGED,
    }]


def test_flistFilesNeedingCommit_ignores_non_canonical_dirty_files():
    listResult = manifestCheck.flistFilesNeedingCommit(
        ["wanted.py"],
        _fdictGit({"scratch.py": "dirty"}),
    )
    assert listResult == []


def test_flistFilesNeedingCommit_multiple_files():
    listResult = manifestCheck.flistFilesNeedingCommit(
        ["a.py", "b.py", "c.py", "d.py"],
        _fdictGit({
            "a.py": "dirty",
            "b.py": "untracked",
            "c.py": "uncommitted",
        }),
    )
    setSeen = {dict_["sPath"] for dict_ in listResult}
    assert setSeen == {"a.py", "b.py", "c.py"}


# ----------------------------------------------------------------------
# fdictBuildManifestReport (integration with gitStatus via real tmp dir)
# ----------------------------------------------------------------------


def test_fdictBuildManifestReport_non_repo_returns_bIsRepo_false(tmp_path):
    dictResult = manifestCheck.fdictBuildManifestReport(
        {"listSteps": []}, str(tmp_path),
    )
    assert dictResult["bIsRepo"] is False
    assert dictResult["listNeedsCommit"] == []
    assert dictResult["iCanonicalCount"] == 0


def test_fdictBuildManifestReport_shape_keys(tmp_path):
    dictResult = manifestCheck.fdictBuildManifestReport(
        {"listSteps": []}, str(tmp_path),
    )
    for sKey in (
        "bIsRepo", "listNeedsCommit", "iCanonicalCount",
        "sBranch", "iAhead", "iBehind", "sHeadSha", "sReason",
    ):
        assert sKey in dictResult


def test_fdictBuildManifestReport_handles_missing_workspace():
    dictResult = manifestCheck.fdictBuildManifestReport(
        {"listSteps": []}, "/does/not/exist",
    )
    assert dictResult["bIsRepo"] is False
    assert dictResult["iCanonicalCount"] == 0
