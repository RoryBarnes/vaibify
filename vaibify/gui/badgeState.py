"""Per-file per-remote badge state for the Step Viewer.

Each file row in the dashboard carries a trio of mini-badges (G / O /
Z) that tell the user at a glance whether the file is in sync with
GitHub, Overleaf, and Zenodo respectively. This module is the single
source of truth for how those badges are computed: it combines
``gitStatus`` (repo state), ``mtimeCache`` (current content hash), and
the workflow's ``dictSyncStatus`` (last-pushed digest per service).

Badge values:
- ``synced``     local content matches the remote's last-known digest
- ``drifted``    local has changed since the last push to this remote
- ``dirty``      (git only) uncommitted working-tree changes
- ``untracked``  (git only) not tracked by git
- ``ignored``    (git only) explicitly gitignored
- ``none``       the service is not configured for this file
"""

from . import mtimeCache
from . import workflowManager

__all__ = [
    "S_BADGE_SYNCED",
    "S_BADGE_DRIFTED",
    "S_BADGE_DIRTY",
    "S_BADGE_UNTRACKED",
    "S_BADGE_IGNORED",
    "S_BADGE_NONE",
    "fdictBadgesForFile",
    "fdictBadgeStateForWorkspace",
    "fdictBadgeStateFromHashes",
]


S_BADGE_SYNCED = "synced"
S_BADGE_DRIFTED = "drifted"
S_BADGE_DIRTY = "dirty"
S_BADGE_UNTRACKED = "untracked"
S_BADGE_IGNORED = "ignored"
S_BADGE_NONE = "none"


_DICT_GIT_STATE_TO_BADGE = {
    "committed": S_BADGE_SYNCED,
    "uncommitted": S_BADGE_DRIFTED,
    "dirty": S_BADGE_DIRTY,
    "untracked": S_BADGE_UNTRACKED,
    "ignored": S_BADGE_IGNORED,
    "conflict": S_BADGE_DIRTY,
}


def _fsGitBadge(sRepoRelPath, dictGitStatus):
    """Return the git badge letter for one file, reading porcelain state."""
    if not dictGitStatus.get("bIsRepo"):
        return S_BADGE_NONE
    dictFileStates = dictGitStatus.get("dictFileStates", {}) or {}
    sState = dictFileStates.get(sRepoRelPath)
    if sState is None:
        return S_BADGE_SYNCED
    return _DICT_GIT_STATE_TO_BADGE.get(sState, S_BADGE_DRIFTED)


def _fsRemoteBadge(sCurrentSha, sLastPushedDigest, bTracked):
    """Three-state icon for one remote: none / drifted / synced.

    ``bTracked`` reflects whether the user opted this file into the
    remote (today the ``b{Service}`` flag in ``dictSyncStatus``).
    Without opt-in the icon stays grey even if the file happens to
    have been pushed previously; with opt-in but no matching digest
    it paints orange (tracked but not yet in sync).
    """
    if not bTracked:
        return S_BADGE_NONE
    if not sLastPushedDigest:
        return S_BADGE_DRIFTED
    if not sCurrentSha:
        return S_BADGE_DRIFTED
    if sCurrentSha == sLastPushedDigest:
        return S_BADGE_SYNCED
    return S_BADGE_DRIFTED


def fdictBadgesForFile(
    sRepoRelPath, dictGitStatus, dictSyncEntry,
    sWorkspaceRoot, dictMtimeCache,
):
    """Return the three-badge triple for one file.

    Git is both the transport and the source of truth for the GitHub
    column: whatever ``git status`` says about this file is what we
    show. Overleaf and Zenodo use their own last-pushed digests to
    compare against the file's current blob SHA.
    """
    sCurrentSha = mtimeCache.fsBlobShaForFile(
        sWorkspaceRoot, sRepoRelPath, dictMtimeCache,
    )
    dictEntry = dictSyncEntry or {}
    return {
        "sGithub": _fsGitBadge(sRepoRelPath, dictGitStatus),
        "sOverleaf": _fsRemoteBadge(
            sCurrentSha,
            dictEntry.get("sOverleafLastPushedDigest", ""),
            dictEntry.get("bOverleaf", False),
        ),
        "sZenodo": _fsRemoteBadge(
            sCurrentSha,
            dictEntry.get("sZenodoLastPushedDigest", ""),
            dictEntry.get("bZenodo", False),
        ),
    }


def fdictBadgeStateForWorkspace(
    listRepoRelPaths, dictGitStatus, dictSyncStatus,
    sWorkspaceRoot, dictMtimeCache, sProjectRepoPath="",
):
    """Return {repo-rel-path: badge-triple} for each file in the list.

    Mutates ``dictMtimeCache`` in place as a side effect of hashing;
    the caller is responsible for persisting the cache when done.
    """
    dictResult = {}
    dictSync = dictSyncStatus or {}
    for sRelPath in listRepoRelPaths:
        dictEntry = workflowManager.fdictLookupSyncEntry(
            dictSync, sRelPath, sProjectRepoPath,
        )
        dictResult[sRelPath] = fdictBadgesForFile(
            sRelPath, dictGitStatus, dictEntry,
            sWorkspaceRoot, dictMtimeCache,
        )
    return dictResult


def fdictBadgeStateFromHashes(
    listRepoRelPaths, dictGitStatus, dictSyncStatus,
    dictCurrentHashes, sProjectRepoPath="",
):
    """Compute badges when current hashes were obtained by some other means.

    Use this variant when the workspace is only accessible through a
    container (Docker volumes on macOS/Windows). The caller supplies
    ``dictCurrentHashes`` — a ``{repo-rel-path: blob-sha}`` map
    produced by ``containerGit.fdictComputeBlobShasInContainer`` or an
    equivalent — instead of asking the filesystem directly.
    """
    dictResult = {}
    dictSync = dictSyncStatus or {}
    dictHashes = dictCurrentHashes or {}
    for sRelPath in listRepoRelPaths:
        dictEntry = workflowManager.fdictLookupSyncEntry(
            dictSync, sRelPath, sProjectRepoPath,
        )
        sCurrentSha = dictHashes.get(sRelPath, "")
        dictResult[sRelPath] = {
            "sGithub": _fsGitBadge(sRelPath, dictGitStatus),
            "sOverleaf": _fsRemoteBadge(
                sCurrentSha,
                dictEntry.get("sOverleafLastPushedDigest", ""),
                dictEntry.get("bOverleaf", False),
            ),
            "sZenodo": _fsRemoteBadge(
                sCurrentSha,
                dictEntry.get("sZenodoLastPushedDigest", ""),
                dictEntry.get("bZenodo", False),
            ),
        }
    return dictResult
