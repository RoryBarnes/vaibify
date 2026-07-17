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
    "fbIsUnderRepoRoot",
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


def fbIsUnderRepoRoot(sAbsPath, sRepoRoot):
    """Return True iff ``sAbsPath`` is ``sRepoRoot`` or lives under it.

    A relative path is treated as in-repo (it already has a
    repo-relative form). Empty inputs are under no root.
    """
    if not sAbsPath or not sRepoRoot:
        return False
    if not posixpath.isabs(sAbsPath):
        return True
    sNormRoot = posixpath.normpath(sRepoRoot).rstrip("/")
    sNormPath = posixpath.normpath(sAbsPath)
    return sNormPath == sNormRoot or sNormPath.startswith(sNormRoot + "/")


def fdictAbsKeysToRepoRelative(dictByAbs, sRepoRoot):
    """Return a new dict whose keys are repo-relative versions of dictByAbs's.

    Absolute keys that are NOT under ``sRepoRoot`` are dropped rather
    than passed through with a warning: they have no repo-relative form
    and do not belong on the repo-relative wire. Declared binaries live
    outside the repo (``/home/<user>/.local/bin/...``) and are collected
    in the poll's mtime batch only for the host-side binary-staleness
    computation, which reads the absolute-keyed dict BEFORE this
    conversion — so dropping them here is lossless and stops a per-poll
    warning flood. With an empty ``sRepoRoot`` every key is kept as-is.
    """
    dictResult = {}
    for sKey, value in dictByAbs.items():
        if sRepoRoot and not fbIsUnderRepoRoot(sKey, sRepoRoot):
            continue
        dictResult[fsAbsToRepoRelative(sKey, sRepoRoot)] = value
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
