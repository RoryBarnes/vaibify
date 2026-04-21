"""Tests for per-file per-remote badge state computation."""

import os

import pytest

from vaibify.gui import badgeState, mtimeCache


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
# Git column
# ----------------------------------------------------------------------


def test_git_badge_synced_for_committed_file(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), {}, str(tmp_path), {},
    )
    assert dictResult["sGithub"] == badgeState.S_BADGE_SYNCED


def test_git_badge_dirty_for_working_tree_modification(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf",
        _fdictGit({"fig.pdf": "dirty"}),
        {}, str(tmp_path), {},
    )
    assert dictResult["sGithub"] == badgeState.S_BADGE_DIRTY


def test_git_badge_drifted_for_staged_modification(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf",
        _fdictGit({"fig.pdf": "uncommitted"}),
        {}, str(tmp_path), {},
    )
    assert dictResult["sGithub"] == badgeState.S_BADGE_DRIFTED


def test_git_badge_untracked(tmp_path):
    _fsWrite(str(tmp_path), "new.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "new.pdf",
        _fdictGit({"new.pdf": "untracked"}),
        {}, str(tmp_path), {},
    )
    assert dictResult["sGithub"] == badgeState.S_BADGE_UNTRACKED


def test_git_badge_none_when_not_a_repo(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(bIsRepo=False),
        {}, str(tmp_path), {},
    )
    assert dictResult["sGithub"] == badgeState.S_BADGE_NONE


# ----------------------------------------------------------------------
# Overleaf column
# ----------------------------------------------------------------------


def test_overleaf_badge_none_when_never_pushed(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), {}, str(tmp_path), {},
    )
    assert dictResult["sOverleaf"] == badgeState.S_BADGE_NONE


def test_overleaf_badge_synced_when_digest_matches(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fig.pdf", dictCache,
    )
    dictEntry = {
        "bOverleaf": True,
        "sOverleafLastPushedDigest": sSha,
    }
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), dictCache,
    )
    assert dictResult["sOverleaf"] == badgeState.S_BADGE_SYNCED


def test_overleaf_badge_drifted_when_digest_differs(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictEntry = {
        "bOverleaf": True,
        "sOverleafLastPushedDigest": "0" * 40,
    }
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), {},
    )
    assert dictResult["sOverleaf"] == badgeState.S_BADGE_DRIFTED


def test_overleaf_badge_drifted_when_file_missing(tmp_path):
    dictEntry = {
        "bOverleaf": True,
        "sOverleafLastPushedDigest": "a" * 40,
    }
    dictResult = badgeState.fdictBadgesForFile(
        "ghost.pdf", _fdictGit(), dictEntry,
        str(tmp_path), {},
    )
    assert dictResult["sOverleaf"] == badgeState.S_BADGE_DRIFTED


def test_overleaf_badge_none_when_not_tracked(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fig.pdf", dictCache,
    )
    dictEntry = {
        "bOverleaf": False,
        "sOverleafLastPushedDigest": sSha,
    }
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), dictCache,
    )
    assert dictResult["sOverleaf"] == badgeState.S_BADGE_NONE


def test_overleaf_badge_drifted_when_tracked_but_never_pushed(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictEntry = {
        "bOverleaf": True,
        "sOverleafLastPushedDigest": "",
    }
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), {},
    )
    assert dictResult["sOverleaf"] == badgeState.S_BADGE_DRIFTED


# ----------------------------------------------------------------------
# Zenodo column (mirrors Overleaf semantics)
# ----------------------------------------------------------------------


def test_zenodo_badge_synced_when_digest_matches(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fig.pdf", dictCache,
    )
    dictEntry = {
        "bZenodo": True,
        "sZenodoLastPushedDigest": sSha,
    }
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), dictCache,
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_SYNCED


def test_zenodo_badge_none_when_only_overleaf_configured(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictEntry = {"sOverleafLastPushedDigest": "a" * 40}
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), {},
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_NONE


# ----------------------------------------------------------------------
# fdictBadgeStateForWorkspace
# ----------------------------------------------------------------------


def test_workspace_badges_many_files(tmp_path):
    _fsWrite(str(tmp_path), "a.pdf", "a")
    _fsWrite(str(tmp_path), "b.pdf", "b")
    dictGit = _fdictGit({"b.pdf": "dirty"})
    dictSync = {}
    dictCache = {}
    dictResult = badgeState.fdictBadgeStateForWorkspace(
        ["a.pdf", "b.pdf"], dictGit, dictSync,
        str(tmp_path), dictCache,
    )
    assert dictResult["a.pdf"]["sGithub"] == badgeState.S_BADGE_SYNCED
    assert dictResult["b.pdf"]["sGithub"] == badgeState.S_BADGE_DIRTY


def test_workspace_badges_tolerate_container_absolute_keys(tmp_path):
    """Sync entries recorded with /workspace/ prefix still match."""
    _fsWrite(str(tmp_path), "step1/fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "step1/fig.pdf", dictCache,
    )
    dictSync = {
        "step1/fig.pdf": {
            "bOverleaf": True,
            "sOverleafLastPushedDigest": sSha,
        },
    }
    dictResult = badgeState.fdictBadgeStateForWorkspace(
        ["step1/fig.pdf"], _fdictGit(), dictSync,
        str(tmp_path), dictCache,
    )
    assert dictResult["step1/fig.pdf"]["sOverleaf"] == (
        badgeState.S_BADGE_SYNCED
    )


def test_workspace_badges_empty_path_list(tmp_path):
    dictResult = badgeState.fdictBadgeStateForWorkspace(
        [], _fdictGit(), {}, str(tmp_path), {},
    )
    assert dictResult == {}


def test_workspace_badges_populates_mtime_cache(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    dictEntry = {
        "bOverleaf": True,
        "sOverleafLastPushedDigest": "a" * 40,
    }
    badgeState.fdictBadgeStateForWorkspace(
        ["fig.pdf"], _fdictGit(), {"fig.pdf": dictEntry},
        str(tmp_path), dictCache,
    )
    assert "fig.pdf" in dictCache
