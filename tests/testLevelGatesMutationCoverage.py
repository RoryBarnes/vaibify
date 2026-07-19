"""Mutation-coverage tests for the AICS Level 2 gate in ``levelGates``.

Each test isolates a single fail-closed guard in the L2 sync-cache and
GitHub-SHA gate path so that a surviving mutant — one that lights a
level it should not (the cardinal honesty lie) or wrongly evicts the LRU
cache — is killed. The fixtures deliberately make exactly one guard the
decisive one: the sibling guards are satisfied so the test cannot pass
"for the wrong reason".

The cached sync status is written to a real
``<projectRepo>/.vaibify/syncStatus.json`` (no IO mocking), matching the
companion ``testLevelGates`` suite.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)
from vaibify.reproducibility.levelGates import (
    _DICT_BLOCKER_CACHE,
    _I_BLOCKER_CACHE_MAX_ENTRIES,
    _fnBlockerCacheStore,
    fbWorkflowFullySyncedWithGithub,
    fdictLevel2Gaps,
    fnClearLevelBlockerCache,
)

pytestmark = pytest.mark.falsification


def _fsBuildIsoTimestamp(fHoursAgo=0.0):
    """Return an ISO-8601 UTC timestamp fHoursAgo before now."""
    dtNow = datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    return dtNow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fnWriteSyncStatusFile(sProjectRepo, dictPerService):
    """Write a sample syncStatus.json under .vaibify/."""
    sDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    sPath = os.path.join(sDir, "syncStatus.json")
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictPerService, fileHandle)


def _fdictAllGreenStep(sStepKind=None):
    """Return one L1-satisfying step, with optional sStepKind."""
    dictStep = {
        "sName": "A", "sDirectory": "A",
        "bNoInputData": True,
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }
    if sStepKind:
        dictStep["sStepKind"] = sStepKind
    return dictStep


def _fdictBuildLevel2ReadyWorkflow():
    """Return a workflow with all four L2 dict-level criteria satisfied."""
    return {
        "listSteps": [
            _fdictAllGreenStep(),
            _fdictAllGreenStep(
                sStepKind=S_AI_DECLARATION_STEP_KIND,
            ),
        ],
        "dictRemotes": {
            "github": {
                "sOwner": "u", "sRepo": "r", "sBranch": "main",
                "sCommittedSha": "abc123",
            },
            "zenodo": {
                "sRecordId": "1234", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
        },
        "dictAiProvenance": {
            "listDeclaredModels": [{
                "sVendor": "ExampleVendor",
                "sModelId": "example-model-1",
                "sUseStartDate": "2026-01-01",
                "sUseEndDate": "2026-02-01",
            }],
        },
    }


# ------------------------------------------------------------------------
# _fbCachedSyncStatusFullMatch: divergence guard (counts agree)
# ------------------------------------------------------------------------


def test_github_full_count_with_nonempty_diverged_is_not_synced(tmp_path):
    """iMatching==iTotal but a populated listDiverged must fail closed.

    Isolates the divergence guard: the count check is satisfied
    (3 == 3) and the SHA + freshness clauses are green, so only the
    ``listDiverged`` guard can keep the gate dark. A mutant that drops
    that guard would light L2 for files known to differ from the mirror.

    Kills: Remove the `if dictStatus.get('listDiverged'): return False`
    divergence guard in _fbCachedSyncStatusFullMatch
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3,
            "listDiverged": [{"sPath": "a"}],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


# ------------------------------------------------------------------------
# _fbCachedSyncStatusFullMatch: count guard (no divergence reported)
# ------------------------------------------------------------------------


def test_github_undercount_with_empty_diverged_is_not_synced(tmp_path):
    """iMatching<iTotal with empty listDiverged must fail closed.

    Isolates the count check: ``listDiverged`` is empty and the SHA +
    freshness clauses are green, so only ``iMatching != iTotal`` can
    keep the gate dark. A mutant that drops the count check would light
    L2 on an undercount of matched files.

    Kills: Remove the `if dictStatus.get('iMatching') != iTotal: return
    False` count check in _fbCachedSyncStatusFullMatch
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 2,
            "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


# ------------------------------------------------------------------------
# _fbGithubHeadMatchesVerifiedSha: permissive guard is AND, not OR
# ------------------------------------------------------------------------


def test_github_verified_sha_empty_but_live_sha_present_is_not_synced(tmp_path):
    """Empty verified SHA + present live SHA must fail closed.

    The permissive ``not sVerifiedSha and not sLiveSha`` branch may only
    pass when BOTH are empty. Here the live config records ``abc123`` but
    the cached verify captured no SHA — an unverified/unpushed commit. An
    ``or`` mutant would permissively light L2.

    Kills: Change the permissive guard `if not sVerifiedSha and not
    sLiveSha: return True` to `or` in _fbGithubHeadMatchesVerifiedSha
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictWorkflow["dictRemotes"]["github"]["sCommittedSha"] = "abc123"
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


def test_github_verified_sha_present_but_live_sha_empty_is_not_synced(tmp_path):
    """Present verified SHA + empty live SHA must fail closed.

    The mirror image of the previous case: the cache recorded ``abc123``
    while the live config carries no SHA. ``not sVerifiedSha`` is False,
    so the permissive AND branch cannot fire and the equality check
    rejects. An ``or`` mutant would let the empty live SHA satisfy the
    guard and light L2.

    Kills: Change the permissive guard `if not sVerifiedSha and not
    sLiveSha: return True` to `or` in _fbGithubHeadMatchesVerifiedSha
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictWorkflow["dictRemotes"]["github"]["sCommittedSha"] = ""
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


# ------------------------------------------------------------------------
# _fbCachedSyncStatusFresh: a full-match cache with no timestamp is stale
# ------------------------------------------------------------------------


def test_github_full_match_without_timestamp_is_not_synced(tmp_path):
    """A full-match cache lacking sLastVerified must fail closed.

    Full-match and SHA clauses are green; ``sLastVerified`` is omitted
    entirely. The freshness guard treats a missing timestamp as stale so
    the gate stays dark. A mutant that drops ``if not sLastVerified:
    return False`` would assert a verification that never happened.

    Kills: Disable `if not sLastVerified: return False` in
    _fbCachedSyncStatusFresh
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


# ------------------------------------------------------------------------
# fdictLevel2Gaps: bAtLeastLevel2 is the conjunction of every criterion
# ------------------------------------------------------------------------


def test_fdictLevel2Gaps_subset_failure_keeps_level2_false(tmp_path):
    """bAtLeastLevel2 must be False when any single criterion fails.

    No sync caches are written, so GitHub and Zenodo are unsynced while
    L1, arXiv (data-only) and the AI-declaration step pass. The
    aggregate must stay False — a mutant that weakens the conjunction to
    a disjunction would report the work as publication-reproducible while
    the mirror/deposit sync is still missing.

    Kills: Change one conjunct in `bL1 and bGithub and bZenodo and
    bArxiv and bDecl` to `or` in fdictLevel2Gaps (applied as `bL1 or
    bGithub and ...`)
    """
    sProjectRepo = str(tmp_path)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictGaps = fdictLevel2Gaps(dictWorkflow, sProjectRepo)
    assert dictGaps["bAtLeastLevel1"] is True
    assert dictGaps["bGithubFullySynced"] is False
    assert dictGaps["bZenodoFullySynced"] is False
    assert dictGaps["bAiDeclarationAttested"] is True
    assert dictGaps["bArxivFullySynced"] is True
    assert dictGaps["bAtLeastLevel2"] is False


# ------------------------------------------------------------------------
# _fnBlockerCacheStore: LRU evicts the oldest entry, not the newest
# ------------------------------------------------------------------------


def test_blocker_cache_evicts_oldest_entry_first():
    """Overflow eviction must drop the first-inserted key, keep the last.

    Storing one more than the capacity (9 vs 8) must evict the
    least-recently-inserted key while retaining the most recent. A mutant
    that evicts the newest entry (``popitem(last=True)``) would keep the
    stale first key and discard the fresh ninth.

    Kills: Change `_DICT_BLOCKER_CACHE.popitem(last=False)` to
    `popitem(last=True)` in _fnBlockerCacheStore
    """
    fnClearLevelBlockerCache()
    listKeys = [("L1", "fingerprint", str(iIndex)) for iIndex in range(9)]
    for tKey in listKeys:
        _fnBlockerCacheStore(tKey, [])
    assert len(_DICT_BLOCKER_CACHE) == _I_BLOCKER_CACHE_MAX_ENTRIES
    assert listKeys[0] not in _DICT_BLOCKER_CACHE
    assert listKeys[8] in _DICT_BLOCKER_CACHE
    fnClearLevelBlockerCache()
