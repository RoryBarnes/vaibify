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

from vaibify.reproducibility import manifestWriter
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles

from . import mtimeCache

__all__ = [
    "fsetStaleOutputsForStep",
    "fbMarkerHasHashes",
    "fsetStaleOutputsAgainstManifest",
    "fbManifestExists",
]


_MANIFEST_FILENAME = "MANIFEST.sha256"


def fbMarkerHasHashes(dictMarker, sHashKey="dictOutputHashes"):
    """Return True when a marker carries content hashes under sHashKey."""
    if not isinstance(dictMarker, dict):
        return False
    dictHashes = dictMarker.get(sHashKey)
    return isinstance(dictHashes, dict) and len(dictHashes) > 0


def fsetStaleOutputsForStep(
    dictMarker, sWorkspaceRoot, dictCache, dictMtimeHints=None,
    sHashKey="dictOutputHashes",
):
    """Return the set of repo-relative paths whose content has drifted.

    Reads ``dictMarker[sHashKey]`` as the baseline (the step's output
    hashes by default; pass ``"dictInputHashes"`` for the input-data
    baseline) and compares each file's current blob SHA (via the
    mtime cache) to its baseline digest. Missing files are treated as
    stale; markers lacking hashes yield an empty set (nothing to
    compare). ``dictMtimeHints`` is an optional
    ``{sRepoRelPath: fMtime}`` map so a caller that already stat'd
    the files can spare the redundant ``os.stat`` per cache lookup;
    ``None`` falls back to live stats.
    """
    setStale = set()
    if not fbMarkerHasHashes(dictMarker, sHashKey):
        return setStale
    dictBaseline = dictMarker[sHashKey]
    dictHints = dictMtimeHints or {}
    for sRelPath, sBaselineSha in dictBaseline.items():
        bMatches = mtimeCache.fbFileMatchesDigest(
            sWorkspaceRoot, sRelPath, sBaselineSha, dictCache,
            fMtimeHint=dictHints.get(sRelPath),
        )
        if not bMatches:
            setStale.add(sRelPath)
    return setStale


def fbManifestExists(filesRepo):
    """Return True iff ``<repo>/MANIFEST.sha256`` is a file."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.sRootPath:
        return False
    return filesRepo.fbIsFile(_MANIFEST_FILENAME)


def fsetStaleOutputsAgainstManifest(
    filesRepo, listRelPaths, dictCache, dictMtimeHints=None,
):
    """Return paths whose current SHA-256 disagrees with MANIFEST.sha256.

    The manifest is authoritative for the set of tracked files; paths
    in ``listRelPaths`` that lack a manifest entry are silently
    skipped (untracked files are out of scope for this check). Files
    listed in the manifest but missing on disk are reported as stale.
    Returns the empty set when the manifest does not exist — the
    caller decides whether absence is meaningful.

    Host-rooted repos keep the persistent on-disk mtime cache;
    container-rooted repos recompute through the adapter, keyed off
    container mtimes (``dictMtimeHints``) against the in-memory
    ``dictCache`` whose honest scope is the server process lifetime.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    setStale = set()
    if not fbManifestExists(filesRepo):
        return setStale
    dictManifest = _fdictReadManifestEntries(filesRepo)
    if not dictManifest:
        return setStale
    listTracked = [s for s in listRelPaths if s in dictManifest]
    dictActual = _fdictActualShas(
        filesRepo, listTracked, dictCache, dictMtimeHints or {},
    )
    for sRelPath in listTracked:
        sActual = dictActual.get(sRelPath)
        if not sActual or sActual != dictManifest[sRelPath]:
            setStale.add(sRelPath)
    return setStale


def _fdictActualShas(filesRepo, listTracked, dictCache, dictMtimeHints):
    """Return ``{sRelPath: sSha256_or_None}`` for the tracked paths."""
    sLocalRoot = filesRepo.fsLocalRootOrNone()
    if sLocalRoot is not None:
        return {
            sRelPath: mtimeCache.fsSha256ForFile(
                sLocalRoot, sRelPath, dictCache,
            )
            for sRelPath in listTracked
        }
    return _fdictContainerShas(
        filesRepo, listTracked, dictCache, dictMtimeHints,
    )


def _fdictContainerShas(filesRepo, listTracked, dictCache, dictMtimeHints):
    """Resolve container-side SHA-256s via the in-memory mtime cache.

    A path whose hinted container mtime matches its cached entry
    reuses the cached digest; everything else is hashed in ONE adapter
    batch and the cache updated in place. Missing hints force a
    rehash, never a stale cached answer.
    """
    dictShas = {}
    listNeedHash = []
    for sRelPath in listTracked:
        iMtime = _fiCoerceMtime(dictMtimeHints.get(sRelPath))
        dictEntry = (dictCache or {}).get(sRelPath) or {}
        bCacheHit = (
            iMtime is not None
            and dictEntry.get("iMtime") == iMtime
            and dictEntry.get("sSha256")
        )
        if bCacheHit:
            dictShas[sRelPath] = dictEntry["sSha256"]
        else:
            listNeedHash.append((sRelPath, iMtime))
    _fnHashAndCache(filesRepo, listNeedHash, dictShas, dictCache)
    return dictShas


def _fnHashAndCache(filesRepo, listNeedHash, dictShas, dictCache):
    """Batch-hash uncached paths; record results in dictShas + dictCache."""
    if not listNeedHash:
        return
    dictHashed = filesRepo.fdictHashFiles(
        [sRelPath for sRelPath, _iMtime in listNeedHash],
    )
    for sRelPath, iMtime in listNeedHash:
        sSha256 = (dictHashed.get(sRelPath) or {}).get("sSha256")
        dictShas[sRelPath] = sSha256
        if sSha256 and iMtime is not None and dictCache is not None:
            dictCache[sRelPath] = {"iMtime": iMtime, "sSha256": sSha256}


def _fiCoerceMtime(mtimeValue):
    """Return the mtime as an int, or None when absent/malformed."""
    if mtimeValue is None:
        return None
    try:
        return int(float(mtimeValue))
    except (TypeError, ValueError):
        return None


def _fdictReadManifestEntries(filesRepo):
    """Parse MANIFEST.sha256 into a ``{sRelPath: sExpectedHash}`` dict.

    Delegates to ``manifestWriter.flistParseManifestLines`` so the
    GNU-escape semantics stay in one place. This is a defensive read
    path: an absent or corrupt manifest yields an empty dict rather
    than raising, because hashStaleness is consulted opportunistically
    by the dashboard.
    """
    try:
        listEntries = manifestWriter.flistParseManifestLines(filesRepo)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return {dictEntry["sPath"]: dictEntry["sExpected"]
            for dictEntry in listEntries}
