"""Wire-format path contract for container paths.

Vaibify stores container paths in workflow.json and exchanges them
with the frontend in a single canonical form: **repo-relative**, i.e.
relative to ``dictWorkflow['sProjectRepoPath']``. Internal backend
operations (``stat``, file reads, container exec) still use absolute
container paths; conversion happens only at persistence and wire
boundaries.

This module is a leaf — it depends only on ``posixpath`` so it can be
imported anywhere without risk of cycles.
"""

import logging
import posixpath


__all__ = [
    "fsAbsToRepoRelative",
    "fsRepoRelativeToAbs",
    "fdictAbsKeysToRepoRelative",
    "flistNormalizeModifiedFiles",
]


logger = logging.getLogger("vaibify")


def fsAbsToRepoRelative(sAbsPath, sRepoRoot):
    """Strip ``sRepoRoot/`` from ``sAbsPath`` to yield a repo-relative path.

    Idempotent on already-relative input. Returns the input unchanged
    (with a one-shot WARNING log) when ``sAbsPath`` is absolute but
    not under ``sRepoRoot``. Empty ``sRepoRoot`` returns input as-is.
    """
    if not sAbsPath or not sRepoRoot:
        return sAbsPath
    if not posixpath.isabs(sAbsPath):
        return posixpath.normpath(sAbsPath)
    sNormRoot = posixpath.normpath(sRepoRoot).rstrip("/")
    sNormPath = posixpath.normpath(sAbsPath)
    if sNormPath == sNormRoot:
        return ""
    sPrefix = sNormRoot + "/"
    if sNormPath.startswith(sPrefix):
        return sNormPath[len(sPrefix):]
    logger.warning(
        "fsAbsToRepoRelative: %s is not under repo root %s; "
        "returning unchanged",
        sAbsPath, sRepoRoot,
    )
    return sAbsPath


def fsRepoRelativeToAbs(sRepoRelPath, sRepoRoot):
    """Join ``sRepoRoot`` and ``sRepoRelPath``; idempotent on absolute input."""
    if not sRepoRelPath:
        return sRepoRoot or ""
    if posixpath.isabs(sRepoRelPath):
        return posixpath.normpath(sRepoRelPath)
    if not sRepoRoot:
        return posixpath.normpath(sRepoRelPath)
    return posixpath.normpath(posixpath.join(sRepoRoot, sRepoRelPath))


def fdictAbsKeysToRepoRelative(dictByAbs, sRepoRoot):
    """Return a new dict whose keys are repo-relative versions of dictByAbs's."""
    dictResult = {}
    for sKey, value in dictByAbs.items():
        sNewKey = fsAbsToRepoRelative(sKey, sRepoRoot)
        dictResult[sNewKey] = value
    return dictResult


def flistNormalizeModifiedFiles(listPaths, sRepoRoot):
    """Convert mixed abs/rel paths to a deduped, sorted repo-relative list."""
    if not listPaths:
        return []
    setNormalized = set()
    for sPath in listPaths:
        if not sPath:
            continue
        setNormalized.add(fsAbsToRepoRelative(sPath, sRepoRoot))
    return sorted(setNormalized)
