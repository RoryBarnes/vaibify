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
# Zenodo endpoint awareness (sandbox vs. production)
# ----------------------------------------------------------------------


def _fdictZenodoEntryFor(sSha, sEndpoint):
    """Return a sync entry recording a Zenodo push at ``sEndpoint``."""
    return {
        "bZenodo": True,
        "sZenodoLastPushedDigest": sSha,
        "sZenodoLastPushedEndpoint": sEndpoint,
    }


def test_zenodo_badge_synced_when_endpoint_matches_sandbox(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fig.pdf", dictCache,
    )
    dictEntry = _fdictZenodoEntryFor(sSha, "sandbox")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), dictCache, sZenodoService="sandbox",
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_SYNCED


def test_zenodo_badge_synced_when_endpoint_matches_production(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fig.pdf", dictCache,
    )
    dictEntry = _fdictZenodoEntryFor(sSha, "zenodo")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), dictCache, sZenodoService="zenodo",
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_SYNCED


def test_zenodo_badge_drifted_when_endpoint_mismatched(tmp_path):
    """Sandbox push must not be reported as in-sync once workflow flips
    to production, even if the local SHA still matches."""
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictCache = {}
    sSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fig.pdf", dictCache,
    )
    dictEntry = _fdictZenodoEntryFor(sSha, "sandbox")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), dictCache, sZenodoService="zenodo",
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_DRIFTED


def test_zenodo_badge_drifted_when_legacy_entry_missing_endpoint(tmp_path):
    """Legacy entries without sZenodoLastPushedEndpoint must paint
    drifted under the active workflow service so a re-push populates
    the field honestly."""
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
        str(tmp_path), dictCache, sZenodoService="sandbox",
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_DRIFTED


def test_zenodo_badge_drifted_when_sha_changed_despite_endpoint_match(
    tmp_path,
):
    """A SHA change still drifts the badge regardless of endpoint
    match."""
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictEntry = _fdictZenodoEntryFor("0" * 40, "sandbox")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), dictEntry,
        str(tmp_path), {}, sZenodoService="sandbox",
    )
    assert dictResult["sZenodo"] == badgeState.S_BADGE_DRIFTED


def test_zenodo_badge_legacy_caller_without_service_keeps_old_behaviour(
    tmp_path,
):
    """Callers that never threaded sZenodoService still see the prior
    SHA-only behaviour (endpoint check is a no-op when current
    endpoint is empty)."""
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


def test_zenodo_badge_from_hashes_respects_endpoint(tmp_path):
    """fdictBadgeStateFromHashes also threads sZenodoService through."""
    dictSync = {
        "fig.pdf": _fdictZenodoEntryFor("abc123", "sandbox"),
    }
    dictResult = badgeState.fdictBadgeStateFromHashes(
        ["fig.pdf"], _fdictGit(), dictSync,
        {"fig.pdf": "abc123"}, sZenodoService="zenodo",
    )
    assert dictResult["fig.pdf"]["sZenodo"] == (
        badgeState.S_BADGE_DRIFTED
    )


def test_zenodo_badge_from_hashes_synced_on_matching_endpoint():
    """fdictBadgeStateFromHashes paints synced when endpoint matches."""
    dictSync = {
        "fig.pdf": _fdictZenodoEntryFor("abc123", "sandbox"),
    }
    dictResult = badgeState.fdictBadgeStateFromHashes(
        ["fig.pdf"], _fdictGit(), dictSync,
        {"fig.pdf": "abc123"}, sZenodoService="sandbox",
    )
    assert dictResult["fig.pdf"]["sZenodo"] == (
        badgeState.S_BADGE_SYNCED
    )


def test_zenodo_badge_round_trip_persists_endpoint_synced():
    """Push, serialize workflow.json, reload, re-render: still synced.

    Guards against silently dropping ``sZenodoLastPushedEndpoint``
    in workflow save/load, which would re-introduce the endpoint
    blindness the original commit fixed.
    """
    import json
    from vaibify.gui import workflowManager
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    workflowManager.fnSetServiceTracking(
        dictWorkflow, "Plot/fig.pdf", "Zenodo", True,
    )
    workflowManager.fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "abc123"},
        sZenodoService="sandbox",
    )
    dictReloaded = json.loads(json.dumps(dictWorkflow))
    dictResult = badgeState.fdictBadgeStateFromHashes(
        ["Plot/fig.pdf"], _fdictGit(),
        dictReloaded["dictSyncStatus"],
        {"Plot/fig.pdf": "abc123"}, sZenodoService="sandbox",
    )
    assert dictResult["Plot/fig.pdf"]["sZenodo"] == (
        badgeState.S_BADGE_SYNCED
    )


def test_zenodo_badge_round_trip_drifts_when_service_flips():
    """Push to sandbox, serialize/reload, then read with workflow
    flipped to production: badge must drift even though SHA matches."""
    import json
    from vaibify.gui import workflowManager
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    workflowManager.fnSetServiceTracking(
        dictWorkflow, "Plot/fig.pdf", "Zenodo", True,
    )
    workflowManager.fnUpdateZenodoDigests(
        dictWorkflow,
        {"/workspace/Proj/Plot/fig.pdf": "abc123"},
        sZenodoService="sandbox",
    )
    dictReloaded = json.loads(json.dumps(dictWorkflow))
    dictResult = badgeState.fdictBadgeStateFromHashes(
        ["Plot/fig.pdf"], _fdictGit(),
        dictReloaded["dictSyncStatus"],
        {"Plot/fig.pdf": "abc123"}, sZenodoService="zenodo",
    )
    assert dictResult["Plot/fig.pdf"]["sZenodo"] == (
        badgeState.S_BADGE_DRIFTED
    )


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


# ----------------------------------------------------------------------
# arXiv column (pull-side, sourced from syncStatus.json)
# ----------------------------------------------------------------------


def _fdictArxivStatusFor(listDivergedPaths):
    """Return a syncStatus.json-shaped dict for arxiv with listDiverged."""
    return {
        "sService": "arxiv",
        "sLastVerified": "2026-05-13T12:00:00Z",
        "iTotalFiles": 3,
        "iMatching": 3 - len(listDivergedPaths),
        "listDiverged": [
            {"sPath": sPath, "sExpected": "expected", "sActual": "actual"}
            for sPath in listDivergedPaths
        ],
    }


def test_arxiv_badge_none_when_not_configured(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), {}, str(tmp_path), {},
        dictArxivStatus=None, bArxivConfigured=False,
    )
    assert dictResult["sArxiv"] == badgeState.S_BADGE_NONE


def test_arxiv_badge_drifted_when_configured_but_never_verified(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), {}, str(tmp_path), {},
        dictArxivStatus=None, bArxivConfigured=True,
    )
    assert dictResult["sArxiv"] == badgeState.S_BADGE_DRIFTED


def test_arxiv_badge_synced_when_file_absent_from_diverged(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), {}, str(tmp_path), {},
        dictArxivStatus=_fdictArxivStatusFor([]),
        bArxivConfigured=True,
    )
    assert dictResult["sArxiv"] == badgeState.S_BADGE_SYNCED


def test_arxiv_badge_drifted_when_file_in_diverged(tmp_path):
    _fsWrite(str(tmp_path), "fig.pdf", "x")
    dictResult = badgeState.fdictBadgesForFile(
        "fig.pdf", _fdictGit(), {}, str(tmp_path), {},
        dictArxivStatus=_fdictArxivStatusFor(["fig.pdf"]),
        bArxivConfigured=True,
    )
    assert dictResult["sArxiv"] == badgeState.S_BADGE_DRIFTED


def test_arxiv_badge_from_hashes_threads_status_through():
    """fdictBadgeStateFromHashes also paints the arxiv column."""
    dictResult = badgeState.fdictBadgeStateFromHashes(
        ["a.pdf", "b.pdf"], _fdictGit(), {}, {"a.pdf": "x", "b.pdf": "y"},
        dictArxivStatus=_fdictArxivStatusFor(["b.pdf"]),
        bArxivConfigured=True,
    )
    assert dictResult["a.pdf"]["sArxiv"] == badgeState.S_BADGE_SYNCED
    assert dictResult["b.pdf"]["sArxiv"] == badgeState.S_BADGE_DRIFTED
