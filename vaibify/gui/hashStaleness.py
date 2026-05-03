"""Content-hash-based staleness: does a file still match its last-tested digest?

A test marker written by the conftest plugin (see ``conftestManager``)
records ``dictOutputHashes`` mapping repo-relative paths to the git
blob SHA each file had at the moment tests passed. After a fresh clone
mtimes reset but content hashes survive, so hashing is the only
reliable way to tell whether current disk content still matches the
verified baseline.

The module also exposes a SHA-256 path keyed off ``MANIFEST.sha256``
(the AICS Level 3 reproducibility envelope's Tier 1 artefact). The
two digest paths coexist: test markers stay on git blob SHA-1 (the
locked-in choice for execution verification); the manifest path lets
the dashboard answer "did anything in the archive deposit drift?"
with content addressing.

This module is deliberately orthogonal to the live mtime-based flow in
``fileStatusManager``. Phase 3/4 wires its helpers into the dashboard;
Phase 2 ships the helpers + tests so the foundation is in place and
validated independently.
"""

import os

from . import mtimeCache

__all__ = [
    "fsetStaleOutputsForStep",
    "fbMarkerHasHashes",
    "fsetStaleOutputsAgainstManifest",
    "fbManifestExists",
]


_MANIFEST_FILENAME = "MANIFEST.sha256"


def fbMarkerHasHashes(dictMarker):
    """Return True when a marker carries content hashes for outputs."""
    if not isinstance(dictMarker, dict):
        return False
    dictHashes = dictMarker.get("dictOutputHashes")
    return isinstance(dictHashes, dict) and len(dictHashes) > 0


def fsetStaleOutputsForStep(dictMarker, sWorkspaceRoot, dictCache):
    """Return the set of repo-relative paths whose content has drifted.

    Reads ``dictMarker['dictOutputHashes']`` as the baseline and
    compares each file's current blob SHA (via the mtime cache) to
    its baseline digest. Missing files are treated as stale; markers
    lacking hashes yield an empty set (nothing to compare).
    """
    setStale = set()
    if not fbMarkerHasHashes(dictMarker):
        return setStale
    dictBaseline = dictMarker["dictOutputHashes"]
    for sRelPath, sBaselineSha in dictBaseline.items():
        bMatches = mtimeCache.fbFileMatchesDigest(
            sWorkspaceRoot, sRelPath, sBaselineSha, dictCache,
        )
        if not bMatches:
            setStale.add(sRelPath)
    return setStale


def fbManifestExists(sProjectRepo):
    """Return True iff ``<sProjectRepo>/MANIFEST.sha256`` is a file."""
    if not sProjectRepo:
        return False
    return os.path.isfile(
        os.path.join(sProjectRepo, _MANIFEST_FILENAME),
    )


def fsetStaleOutputsAgainstManifest(
    sProjectRepo, listRelPaths, dictCache,
):
    """Return paths whose current SHA-256 disagrees with MANIFEST.sha256.

    The manifest is authoritative for the set of tracked files; paths
    in ``listRelPaths`` that lack a manifest entry are silently
    skipped (untracked files are out of scope for this check). Files
    listed in the manifest but missing on disk are reported as stale.
    Returns the empty set when the manifest does not exist — the
    caller decides whether absence is meaningful.
    """
    setStale = set()
    if not fbManifestExists(sProjectRepo):
        return setStale
    dictManifest = _fdictReadManifestEntries(sProjectRepo)
    if not dictManifest:
        return setStale
    for sRelPath in listRelPaths:
        sExpected = dictManifest.get(sRelPath)
        if sExpected is None:
            continue
        sActual = mtimeCache.fsSha256ForFile(
            sProjectRepo, sRelPath, dictCache,
        )
        if not sActual or sActual != sExpected:
            setStale.add(sRelPath)
    return setStale


def _fdictReadManifestEntries(sProjectRepo):
    """Parse MANIFEST.sha256 into a ``{sRelPath: sExpectedHash}`` dict."""
    sPath = os.path.join(sProjectRepo, _MANIFEST_FILENAME)
    dictEntries = {}
    try:
        with open(sPath, "r", encoding="utf-8") as handle:
            for sLine in handle:
                _fnAddManifestLine(sLine, dictEntries)
    except OSError:
        return {}
    return dictEntries


def _fnAddManifestLine(sLine, dictEntries):
    """Parse one shasum line into the entries dict (skip comments/blanks)."""
    sStripped = sLine.rstrip("\n")
    if not sStripped or sStripped.startswith("#"):
        return
    iSplit = sStripped.find("  ")
    if iSplit < 0:
        return
    sHash = sStripped[:iSplit]
    sRelPath = sStripped[iSplit + 2:]
    if sHash and sRelPath:
        dictEntries[sRelPath] = sHash
