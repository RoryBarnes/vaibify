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
workspace root is supplied by the caller. The host-side cache is the
primary path for workflows whose project repo lives on the host; the
container-side mirror (``fdictLoadContainerCache`` /
``fnSaveContainerCache``) is the equivalent for project repos that
live inside the container, where the host filesystem cannot reach
the file directly without an additional bind mount.
"""

import json
import logging
import os
import posixpath

from vaibify.reproducibility.overleafMirror import fsComputeBlobSha

__all__ = [
    "S_MTIME_CACHE_RELATIVE_PATH",
    "S_CONTAINER_SHA_CACHE_RELATIVE_PATH",
    "fdictLoadCache",
    "fnSaveCache",
    "fdictLoadContainerCache",
    "fnSaveContainerCache",
    "fsBlobShaForFile",
    "fbFileMatchesDigest",
    "fsSha256ForFile",
]


S_MTIME_CACHE_RELATIVE_PATH = ".vaibify/mtime_cache.json"
S_CONTAINER_SHA_CACHE_RELATIVE_PATH = (
    ".vaibify/container_mtime_cache.json"
)

_logger = logging.getLogger("vaibify")


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


def _fsContainerCachePath(sProjectRepoPath):
    """Return the container-side cache path for a project repo."""
    return posixpath.join(
        sProjectRepoPath, S_CONTAINER_SHA_CACHE_RELATIVE_PATH,
    )


def fdictLoadContainerCache(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Load the container-side sha cache; empty dict on miss/corruption.

    Mirrors :func:`fdictLoadCache` for project repos that live inside
    the container. The cache survives ``dictCtx`` rebuilds (server
    restart, reconnect) so multi-GB outputs only need to rehash the
    files whose mtime has changed since the last save.
    """
    if not sProjectRepoPath:
        return {}
    sPath = _fsContainerCachePath(sProjectRepoPath)
    try:
        baContent = connectionDocker.fbaFetchFile(sContainerId, sPath)
    except (FileNotFoundError, OSError):
        return {}
    except Exception:
        _logger.info(
            "container sha cache fetch failed for %s", sContainerId,
        )
        return {}
    try:
        dictLoaded = json.loads(baContent.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(dictLoaded, dict):
        return {}
    return dictLoaded


def fnSaveContainerCache(
    connectionDocker, sContainerId, sProjectRepoPath, dictCache,
):
    """Write the container-side sha cache atomically inside the container.

    Writes to ``<path>.tmp`` first, then renames over the canonical
    path so a concurrent reader between truncate and full write never
    sees a half-written file. Two concurrent workflow polls in the
    same project repo can still race the rename, but each race winner
    leaves a valid JSON file — never a corrupted one. Failures are
    logged at INFO and swallowed so a transient docker hiccup never
    converts a successful poll into a user-facing error; the next save
    reattempts.
    """
    if not sProjectRepoPath:
        return
    sPath = _fsContainerCachePath(sProjectRepoPath)
    sPathTemp = sPath + ".tmp"
    try:
        baBody = json.dumps(
            dictCache, indent=2, sort_keys=True,
        ).encode("utf-8")
        connectionDocker.fnWriteFile(sContainerId, sPathTemp, baBody)
        connectionDocker.ftResultExecuteCommand(
            sContainerId, f"mv {sPathTemp} {sPath}",
        )
    except Exception:
        _logger.info(
            "container sha cache save failed for %s", sContainerId,
        )


def _fsHostPathFor(sWorkspaceRoot, sRepoRelPath):
    """Join a repo-relative posix path against a host workspace root."""
    return os.path.join(sWorkspaceRoot, *sRepoRelPath.split("/"))


def fsBlobShaForFile(
    sWorkspaceRoot, sRepoRelPath, dictCache, fMtimeHint=None,
):
    """Return current git blob SHA for a file, using the cache when valid.

    Updates ``dictCache`` in place. Returns an empty string if the
    file does not exist; the caller can detect that and treat it as a
    cache miss or a deleted file. ``fMtimeHint`` lets a caller that
    already stat'd the file pass the mtime in to skip a redundant
    syscall; ``None`` falls back to ``os.path.getmtime``.
    """
    sHostPath = _fsHostPathFor(sWorkspaceRoot, sRepoRelPath)
    fMtime = _ffResolveMtime(sHostPath, fMtimeHint)
    if fMtime is None:
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


def _ffResolveMtime(sHostPath, fMtimeHint):
    """Return a float mtime via ``fMtimeHint`` when provided, else stat."""
    if fMtimeHint is not None:
        try:
            return float(fMtimeHint)
        except (TypeError, ValueError):
            pass
    try:
        return os.path.getmtime(sHostPath)
    except OSError:
        return None


def fbFileMatchesDigest(
    sWorkspaceRoot, sRepoRelPath, sBaselineSha, dictCache,
    fMtimeHint=None,
):
    """Return True if the file's current blob SHA matches the baseline."""
    if not sBaselineSha:
        return False
    sCurrent = fsBlobShaForFile(
        sWorkspaceRoot, sRepoRelPath, dictCache, fMtimeHint,
    )
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
