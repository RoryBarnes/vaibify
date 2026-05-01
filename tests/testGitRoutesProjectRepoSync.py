"""Unit tests for the project-repo fetch/pull route helpers.

The fetch + pull routes themselves are exercised via the
architectural-invariant suite (action registration + project-repo
threading). These tests cover the pure helpers in isolation: dirty
working-tree detection and the 30-second fetch cache.
"""

import time

import pytest

from vaibify.gui.routes import gitRoutes


def test_flistTrackedDirtyPaths_returns_empty_for_clean_tree():
    dictGit = {"dictFileStates": {}}
    assert gitRoutes._flistTrackedDirtyPaths(dictGit) == []


def test_flistTrackedDirtyPaths_includes_dirty_and_uncommitted():
    """Worktree-modified ('dirty') and index-only changes ('uncommitted')
    block a fast-forward; untracked and ignored files do not (matching
    git's own ``--ff-only`` behavior). The state-name set must agree
    with ``gitStatus._fsStateFromXy``'s actual outputs.
    """
    dictGit = {
        "dictFileStates": {
            "Step01/out.npz": "dirty",
            "Step02/fig.pdf": "uncommitted",
            "README.md": "untracked",
            ".envrc": "ignored",
            "src/clean.py": "committed",
        },
    }
    listResult = gitRoutes._flistTrackedDirtyPaths(dictGit)
    assert listResult == ["Step01/out.npz", "Step02/fig.pdf"]


def test_flistTrackedDirtyPaths_includes_conflicts():
    dictGit = {
        "dictFileStates": {
            "Step01/out.npz": "conflict",
        },
    }
    assert (
        gitRoutes._flistTrackedDirtyPaths(dictGit)
        == ["Step01/out.npz"]
    )


def test_flistTrackedDirtyPaths_ignores_untracked_and_ignored():
    dictGit = {
        "dictFileStates": {
            "scratch.tmp": "untracked",
            "build/cache": "ignored",
        },
    }
    assert gitRoutes._flistTrackedDirtyPaths(dictGit) == []


def test_flistTrackedDirtyPaths_handles_missing_dict():
    """A status with no dictFileStates field must not crash."""
    assert gitRoutes._flistTrackedDirtyPaths({}) == []


def test_pull_refusal_vocabulary_matches_gitStatus_emitted_states():
    """SET_TRACKED_CHANGE_STATES must be a subset of gitStatus's vocabulary.

    Regression for a bug where the route used hypothetical names
    ('modified', 'staged') that never appear in gitStatus output, so
    real dirty trees fell through the refusal branch and surfaced as a
    raw git failure 502 instead of the structured refusal the plan
    promised. Every name here must be a value gitStatus could actually
    emit, otherwise the route silently never fires.
    """
    from vaibify.gui import gitStatus

    set_xy_states = {
        gitStatus._fsStateFromXy(sIndex + sWorktree)
        for sIndex in (".", " ", "M", "A", "D", "R", "C", "U", "T")
        for sWorktree in (".", " ", "M", "D", "T")
    }
    set_emitted = set_xy_states | {"untracked", "ignored", "conflict"}
    assert gitRoutes.SET_TRACKED_CHANGE_STATES.issubset(set_emitted), (
        "Route uses names gitStatus never emits: "
        f"{gitRoutes.SET_TRACKED_CHANGE_STATES - set_emitted}"
    )


@pytest.fixture(autouse=True)
def _fnClearFetchCache():
    """Reset the module-level fetch cache between tests."""
    gitRoutes._DICT_LAST_FETCH.clear()
    yield
    gitRoutes._DICT_LAST_FETCH.clear()


def test_fetch_cache_starts_cold():
    assert gitRoutes._fbFetchCacheIsFresh("cid", bForce=False) is False


def test_fetch_cache_treats_recent_record_as_fresh():
    gitRoutes._fnRecordFetchTime("cid")
    assert gitRoutes._fbFetchCacheIsFresh("cid", bForce=False) is True


def test_fetch_cache_bypassed_when_force_flag_set():
    gitRoutes._fnRecordFetchTime("cid")
    assert gitRoutes._fbFetchCacheIsFresh("cid", bForce=True) is False


def test_fetch_cache_expires_after_ttl():
    gitRoutes._DICT_LAST_FETCH["cid"] = (
        time.time() - gitRoutes.F_FETCH_CACHE_SECONDS - 1.0
    )
    assert gitRoutes._fbFetchCacheIsFresh("cid", bForce=False) is False


def test_fetch_cache_is_keyed_per_container():
    gitRoutes._fnRecordFetchTime("cidA")
    assert gitRoutes._fbFetchCacheIsFresh("cidA", bForce=False) is True
    assert gitRoutes._fbFetchCacheIsFresh("cidB", bForce=False) is False
