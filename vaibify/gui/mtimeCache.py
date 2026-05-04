"""Local mtime-keyed cache of git blob SHAs for workspace files.

Complements the committed test-marker content hashes (Phase 2 of the
workspace-as-git-repo plan). Markers store authoritative ``blob SHA1``
digests of output files at the time a test ran. On every poll we need
to know whether a file's *current* content still matches the baseline
digest the marker recorded.

Rehashing every file on every poll is wasteful. This cache sits at
``.vaibify/mtime_cache.json`` (gitignored, per stateContract) and maps
repo-relative paths to ``{fMtime, sBlobSha}``. When the live mtime of
a file matches the cached mtime we trust the cached digest. When the
mtime changed we recompute and update the cache in place.

Paths in and out are repo-root-relative posix strings; the host
workspace root is supplied by the caller. Container callers do not
use this module (they run off in-container mtimes directly).
"""

import json
import os

from vaibify.reproducibility.overleafMirror import fsComputeBlobSha

__all__ = [
    "S_MTIME_CACHE_RELATIVE_PATH",
    "fdictLoadCache",
    "fnSaveCache",
    "fsBlobShaForFile",
    "fbFileMatchesDigest",
    "fsSha256ForFile",
]


S_MTIME_CACHE_RELATIVE_PATH = ".vaibify/mtime_cache.json"


def _fsCachePath(sWorkspaceRoot):
    """Return host path of the mtime cache file."""
    return os.path.join(
        sWorkspaceRoot, *S_MTIME_CACHE_RELATIVE_PATH.split("/")
    )


def fdictLoadCache(sWorkspaceRoot):
    """Load and return the mtime cache dict; empty dict if missing.

    Corrupt or unreadable cache files are treated as empty; the cache
    is an optimization, so a bad cache must never block dashboard
    updates. The caller mutates the returned dict and passes it to
    ``fnSaveCache``.
    """
    sPath = _fsCachePath(sWorkspaceRoot)
    if not os.path.isfile(sPath):
        return {}
    try:
        with open(sPath, "r", encoding="utf-8") as handle:
            dictLoaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(dictLoaded, dict):
        return {}
    return dictLoaded


def fnSaveCache(sWorkspaceRoot, dictCache):
    """Write the mtime cache back to disk atomically."""
    sPath = _fsCachePath(sWorkspaceRoot)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    sTempPath = sPath + ".tmp"
    with open(sTempPath, "w", encoding="utf-8") as handle:
        json.dump(dictCache, handle, indent=2, sort_keys=True)
    os.replace(sTempPath, sPath)


def _fsHostPathFor(sWorkspaceRoot, sRepoRelPath):
    """Join a repo-relative posix path against a host workspace root."""
    return os.path.join(sWorkspaceRoot, *sRepoRelPath.split("/"))


def fsBlobShaForFile(sWorkspaceRoot, sRepoRelPath, dictCache):
    """Return current git blob SHA for a file, using the cache when valid.

    Updates ``dictCache`` in place. Returns an empty string if the
    file does not exist; the caller can detect that and treat it as a
    cache miss or a deleted file.
    """
    sHostPath = _fsHostPathFor(sWorkspaceRoot, sRepoRelPath)
    try:
        fMtime = os.path.getmtime(sHostPath)
    except OSError:
        dictCache.pop(sRepoRelPath, None)
        return ""
    dictEntry = dictCache.get(sRepoRelPath)
    if (
        isinstance(dictEntry, dict)
        and dictEntry.get("fMtime") == fMtime
        and dictEntry.get("sBlobSha")
    ):
        return dictEntry["sBlobSha"]
    try:
        sSha = fsComputeBlobSha(sHostPath)
    except OSError:
        return ""
    if not isinstance(dictEntry, dict) or dictEntry.get("fMtime") != fMtime:
        dictEntry = {"fMtime": fMtime}
    dictEntry["sBlobSha"] = sSha
    dictCache[sRepoRelPath] = dictEntry
    return sSha


def fbFileMatchesDigest(
    sWorkspaceRoot, sRepoRelPath, sBaselineSha, dictCache,
):
    """Return True if the file's current blob SHA matches the baseline."""
    if not sBaselineSha:
        return False
    sCurrent = fsBlobShaForFile(sWorkspaceRoot, sRepoRelPath, dictCache)
    if not sCurrent:
        return False
    return sCurrent == sBaselineSha


def fsSha256ForFile(sWorkspaceRoot, sRepoRelPath, dictCache):
    """Return current SHA-256 hex digest for a file using the cache.

    Reuses the same per-path entry as ``fsBlobShaForFile`` but stores
    the SHA-256 result under a separate ``sSha256`` key so the two
    digest universes (git blob SHA-1, content SHA-256) coexist without
    collision. Returns the empty string when the file is missing — and
    purges any stale cache entry for that path so a subsequent call
    cannot resurface a digest for a file that no longer exists. The
    purge mirrors :func:`fsBlobShaForFile`'s missing-file handling so
    both digest paths give the same answer for a deleted file.
    """
    from vaibify.reproducibility.provenanceTracker import fsComputeFileHash
    sHostPath = _fsHostPathFor(sWorkspaceRoot, sRepoRelPath)
    try:
        fMtime = os.path.getmtime(sHostPath)
    except OSError:
        dictCache.pop(sRepoRelPath, None)
        return ""
    dictEntry = dictCache.get(sRepoRelPath)
    if (
        isinstance(dictEntry, dict)
        and dictEntry.get("fMtime") == fMtime
        and dictEntry.get("sSha256")
    ):
        return dictEntry["sSha256"]
    try:
        sSha = fsComputeFileHash(sHostPath)
    except (OSError, FileNotFoundError):
        return ""
    if not isinstance(dictEntry, dict) or dictEntry.get("fMtime") != fMtime:
        dictEntry = {"fMtime": fMtime}
    dictEntry["sSha256"] = sSha
    dictCache[sRepoRelPath] = dictEntry
    return sSha
