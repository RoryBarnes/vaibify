"""Content-hash-based staleness: does a file still match its last-tested digest?

A test marker written by the conftest plugin (see ``conftestManager``)
records ``dictOutputHashes`` mapping repo-relative paths to the git
blob SHA each file had at the moment tests passed. After a fresh clone
mtimes reset but content hashes survive, so hashing is the only
reliable way to tell whether current disk content still matches the
verified baseline.

This module is deliberately orthogonal to the live mtime-based flow in
``fileStatusManager``. Phase 3/4 wires its helpers into the dashboard;
Phase 2 ships the helpers + tests so the foundation is in place and
validated independently.
"""

from . import mtimeCache

__all__ = [
    "fsetStaleOutputsForStep",
    "fbMarkerHasHashes",
]


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
