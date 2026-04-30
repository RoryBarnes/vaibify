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


def test_flistTrackedDirtyPaths_includes_modified_and_staged():
    dictGit = {
        "dictFileStates": {
            "Step01/out.npz": "modified",
            "Step02/fig.pdf": "staged",
            "README.md": "untracked",
            ".envrc": "ignored",
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
