"""Per-file per-remote badge state for the Step Viewer.

Each file row in the dashboard carries a row of mini-badges (G / O /
Z / A) that tell the user at a glance whether the file is in sync
with GitHub, Overleaf, Zenodo, and arXiv respectively. This module
is the single source of truth for how those badges are computed: it
combines ``gitStatus`` (repo state), ``mtimeCache`` (current content
hash), the workflow's ``dictSyncStatus`` (last-pushed digest per
push-side service), and the cached pull-side ``syncStatus.json``
entry per service (currently only consulted for arXiv, which has no
push-side counterpart).

Badge values:
- ``synced``     the published copy was compared and matches
- ``drifted``    the published copy was compared and differs
- ``unknown``    no comparison has been made for this file
- ``none``       the remote does not have this file, or the service
                 is not configured for it
- ``dirty`` / ``untracked`` / ``ignored``  local git states, carried
                 on ``sGitState`` only — never on a remote key

**The GitHub badge is agreement with the remote, not local git
cleanliness (2026-08-25).** It read ``git status --porcelain`` until
then, which meant a file committed but never pushed had no porcelain
entry and so lit the octocat as "in sync with remote" — a positive
claim about a remote that had never seen the file. It is now driven by
the same cached verify the Level 2 cells read: a real SHA-256 of the
file as it exists now against the bytes raw.githubusercontent.com
serves. The local answer did not go away; it moved to ``sGitState``,
which the declaration row's track/untrack gate reads because that gate
genuinely asks "does git hold this file".

``unknown`` exists because its absence was itself the bug. A
researcher whose files were byte-identical to GitHub saw the faint
grey ``none`` icon — whose tooltip reads "not synced to this remote" —
and could not distinguish it from a file that had genuinely never been
published, or from a badge map that failed to load. Ignorance now has
its own value and its own colour, and never borrows a verdict from
either neighbour.

A file that is NOT THERE gets ``none`` from every column. ``git status
--porcelain`` lists only files it has something to say about, and the
git badge read "not mentioned" as ``synced`` -- absence of evidence as
evidence of sync. That is true of a tracked, clean file and false of a
file that was never committed and does not exist, which the dashboard
then labelled "in sync with remote" beside its own red "missing"
marker. Existence is therefore asked as its own question rather than
inferred from silence, or from an empty content hash, which would be
the same mistake one step along.
"""

import os

from . import mtimeCache
from . import workflowManager

__all__ = [
    "S_BADGE_SYNCED",
    "S_BADGE_DRIFTED",
    "S_BADGE_DIRTY",
    "S_BADGE_UNTRACKED",
    "S_BADGE_IGNORED",
    "S_BADGE_NONE",
    "S_BADGE_UNKNOWN",
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
# "I have no answer", as distinct from "the answer is no" (2026-08-25).
# Their absence is why a researcher whose files were byte-identical to
# GitHub saw the same faint grey icon as a file that was never
# published, and read it — correctly, per the tooltip — as "not synced
# to this remote".
S_BADGE_UNKNOWN = "unknown"


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


def _fsVerifiedRemoteBadge(sRepoRelPath, dictStatus, bConfigured=True):
    """Four-state icon driven by a cached remote verify result.

    The reader for every remote whose truth is a real comparison
    against the published copy — a SHA-256 of the file as it exists
    now against the bytes the remote serves. GitHub and arXiv share
    it because they ask the identical question; only arXiv gates on
    ``bConfigured``, because a workflow with no arxiv remote has
    nothing to compare against, whereas a project repo always has a
    GitHub answer once a verify has run.

    The four states, and the distinction that did not exist before
    2026-08-25:

    - ``unknown``  no verify has run, or this path was not in the set
      the last verify compared. NOT a claim about the remote.
    - ``none``     the verify looked and the remote does not have the
      file (``sActual`` empty — a 404 from the mirror).
    - ``drifted``  the verify compared both copies and they differ.
    - ``synced``   the verify compared both copies and they match.

    ``listComparedPaths`` is what makes ``unknown`` separable from
    ``synced``: absence from ``listDiverged`` means "matched" only for
    a path that was actually compared, and means nothing at all for
    one that was not. A cache written before that field existed has
    no compared set, so every path reads ``unknown`` until the next
    verify — self-correcting, and honest in the meantime.
    """
    if not bConfigured:
        return S_BADGE_NONE
    if not dictStatus or not dictStatus.get("sLastVerified"):
        return S_BADGE_UNKNOWN
    if sRepoRelPath not in _fsetComparedPaths(dictStatus):
        return S_BADGE_UNKNOWN
    dictDiverged = _fdictDivergedEntries(dictStatus)
    if sRepoRelPath not in dictDiverged:
        return S_BADGE_SYNCED
    if not (dictDiverged[sRepoRelPath].get("sActual") or ""):
        return S_BADGE_NONE
    return S_BADGE_DRIFTED


def _fsetComparedPaths(dictStatus):
    """Return the set of repo-relative paths the last verify compared."""
    return {
        sPath
        for sPath in (dictStatus.get("listComparedPaths") or [])
        if isinstance(sPath, str) and sPath
    }


def _fdictDivergedEntries(dictStatus):
    """Return ``{repo-rel-path: entry}`` for the divergence list."""
    return {
        dictEntry.get("sPath", ""): dictEntry
        for dictEntry in (dictStatus.get("listDiverged") or [])
        if isinstance(dictEntry, dict)
    }


def _fsZenodoBadge(
    sCurrentSha, sLastPushedDigest, bTracked,
    sLastPushedEndpoint, sCurrentEndpoint,
):
    """Three-state Zenodo icon that also checks the endpoint.

    A digest captured against ``zenodo.org`` must not paint synced
    once the workflow flips to ``sandbox.zenodo.org`` (or vice
    versa). When ``sCurrentEndpoint`` is non-empty, a missing or
    mismatched stored endpoint forces ``drifted`` regardless of SHA;
    the user must re-push to repopulate the field honestly. When
    empty, the endpoint check is skipped (legacy SHA-only behaviour).
    """
    if not bTracked:
        return S_BADGE_NONE
    if sCurrentEndpoint and sLastPushedEndpoint != sCurrentEndpoint:
        return S_BADGE_DRIFTED
    return _fsRemoteBadge(sCurrentSha, sLastPushedDigest, bTracked)


def fdictBadgesForFile(
    sRepoRelPath, dictGitStatus, dictSyncEntry,
    sWorkspaceRoot, dictMtimeCache, sZenodoService="",
    dictArxivStatus=None, bArxivConfigured=False,
    dictGithubStatus=None,
):
    """Return the per-file badge dict for one file.

    Git is both the transport and the source of truth for the GitHub
    column: whatever ``git status`` says about this file is what we
    show. Overleaf and Zenodo use their own last-pushed digests to
    compare against the file's current blob SHA. ``sZenodoService``
    (the workflow's currently selected Zenodo endpoint) is compared
    against the stored ``sZenodoLastPushedEndpoint`` so a sandbox
    push is not reported as in-sync against production (or vice versa).
    The arXiv column is pull-side: ``dictArxivStatus`` is the cached
    verify report from ``syncStatus.json``; ``bArxivConfigured`` is
    True when the workflow has an arxiv remote in ``dictRemotes``.
    """
    sCurrentSha = mtimeCache.fsBlobShaForFile(
        sWorkspaceRoot, sRepoRelPath, dictMtimeCache,
    )
    dictEntry = dictSyncEntry or {}
    return _fdictAssembleBadges(
        sRepoRelPath, dictGitStatus, dictEntry, sCurrentSha,
        sZenodoService, dictArxivStatus, bArxivConfigured,
        not os.path.exists(
            os.path.join(sWorkspaceRoot, sRepoRelPath),
        ),
        dictGithubStatus=dictGithubStatus,
    )


def _fdictAllBadgesNone():
    """Return the badge dict for a file that is not there to have state."""
    return {
        "sGithub": S_BADGE_NONE,
        "sOverleaf": S_BADGE_NONE,
        "sZenodo": S_BADGE_NONE,
        "sArxiv": S_BADGE_NONE,
        "sGitState": S_BADGE_NONE,
    }


def _fdictAssembleBadges(
    sRepoRelPath, dictGitStatus, dictEntry, sCurrentSha,
    sZenodoService, dictArxivStatus, bArxivConfigured,
    bFileIsMissing, dictGithubStatus=None,
):
    """Combine the per-remote badge functions into the per-file dict.

    ``bFileIsMissing`` has no default on purpose: defaulting it to
    False is precisely the bug -- every caller that forgot to answer
    would go on reporting absent files as in sync.

    ``sGitState`` carries the LOCAL git answer that ``sGithub`` used
    to carry. The two were one key until 2026-08-25, which meant a
    file committed but never pushed had no porcelain entry and so lit
    the octocat as "in sync with remote" — a claim about a remote that
    had never seen it. They are separate questions and now separate
    keys: ``sGithub`` is agreement with the published copy,
    ``sGitState`` is the working tree. Only the four remote keys are
    rendered as badges; ``sGitState`` is read by the declaration
    row's track/untrack gate, which genuinely wants "does git hold
    this file".
    """
    if bFileIsMissing:
        return _fdictAllBadgesNone()
    return {
        "sGitState": _fsGitBadge(sRepoRelPath, dictGitStatus),
        "sGithub": _fsVerifiedRemoteBadge(
            sRepoRelPath, dictGithubStatus,
        ),
        "sOverleaf": _fsRemoteBadge(
            sCurrentSha,
            dictEntry.get("sOverleafLastPushedDigest", ""),
            dictEntry.get("bOverleaf", False),
        ),
        "sZenodo": _fsZenodoBadge(
            sCurrentSha,
            dictEntry.get("sZenodoLastPushedDigest", ""),
            dictEntry.get("bZenodo", False),
            dictEntry.get("sZenodoLastPushedEndpoint", ""),
            sZenodoService,
        ),
        "sArxiv": _fsVerifiedRemoteBadge(
            sRepoRelPath, dictArxivStatus, bArxivConfigured,
        ),
    }


def fdictBadgeStateForWorkspace(
    listRepoRelPaths, dictGitStatus, dictSyncStatus,
    sWorkspaceRoot, dictMtimeCache, sProjectRepoPath="",
    sZenodoService="", dictArxivStatus=None,
    bArxivConfigured=False, dictGithubStatus=None,
):
    """Return {repo-rel-path: badge-dict} for each file in the list.

    Mutates ``dictMtimeCache`` in place as a side effect of hashing;
    the caller is responsible for persisting the cache when done.
    ``sZenodoService`` is the workflow's currently selected Zenodo
    endpoint; see :func:`fdictBadgesForFile`.
    """
    dictResult = {}
    dictSync = dictSyncStatus or {}
    for sRelPath in listRepoRelPaths:
        dictEntry = workflowManager.fdictLookupSyncEntry(
            dictSync, sRelPath, sProjectRepoPath,
        )
        dictResult[sRelPath] = fdictBadgesForFile(
            sRelPath, dictGitStatus, dictEntry,
            sWorkspaceRoot, dictMtimeCache, sZenodoService,
            dictArxivStatus=dictArxivStatus,
            bArxivConfigured=bArxivConfigured,
            dictGithubStatus=dictGithubStatus,
        )
    return dictResult


def fdictBadgeStateFromHashes(
    listRepoRelPaths, dictGitStatus, dictSyncStatus,
    dictCurrentHashes, setMissingRepoRelPaths, sProjectRepoPath="",
    sZenodoService="", dictArxivStatus=None, bArxivConfigured=False,
    dictGithubStatus=None,
):
    """Compute badges when current hashes were obtained by some other means.

    Use this variant when the workspace is only accessible through a
    container (Docker volumes on macOS/Windows). The caller supplies
    ``dictCurrentHashes`` — a ``{repo-rel-path: blob-sha}`` map
    produced by ``containerGit.fdictComputeBlobShasInContainer`` or an
    equivalent — instead of asking the filesystem directly.
    ``sZenodoService`` is the workflow's currently selected Zenodo
    endpoint; ``dictArxivStatus`` is the cached arXiv verify report
    from ``syncStatus.json``; see :func:`fdictBadgesForFile`.

    ``setMissingRepoRelPaths`` is positional and has no default: this
    variant cannot see the filesystem, so it must be TOLD which files
    are absent. Deriving it from an empty hash would repeat the defect
    -- the hash map is also empty when the probe that built it failed,
    and a whole repository badged ``none`` because one read broke is
    the same lie in the other direction.
    """
    dictResult = {}
    dictSync = dictSyncStatus or {}
    dictHashes = dictCurrentHashes or {}
    for sRelPath in listRepoRelPaths:
        dictEntry = workflowManager.fdictLookupSyncEntry(
            dictSync, sRelPath, sProjectRepoPath,
        )
        dictResult[sRelPath] = _fdictBadgesForHashedFile(
            sRelPath, dictGitStatus, dictEntry,
            dictHashes.get(sRelPath, ""), sZenodoService,
            dictArxivStatus, bArxivConfigured,
            sRelPath in setMissingRepoRelPaths,
            dictGithubStatus,
        )
    return dictResult


def _fdictBadgesForHashedFile(
    sRepoRelPath, dictGitStatus, dictEntry,
    sCurrentSha, sZenodoService,
    dictArxivStatus, bArxivConfigured, bFileIsMissing,
    dictGithubStatus=None,
):
    """Compose the per-file badge dict from a precomputed hash."""
    return _fdictAssembleBadges(
        sRepoRelPath, dictGitStatus, dictEntry, sCurrentSha,
        sZenodoService, dictArxivStatus, bArxivConfigured,
        bFileIsMissing, dictGithubStatus,
    )
