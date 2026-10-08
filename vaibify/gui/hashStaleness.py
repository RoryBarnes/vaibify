"""Content-hash-based staleness: does a file still match its last-tested digest?

A test marker written by the conftest plugin (see ``conftestManager``)
records ``dictOutputHashes`` mapping repo-relative paths to the git
blob SHA each file had at the moment tests passed. After a fresh clone
mtimes reset but content hashes survive, so hashing is the only
reliable way to tell whether current disk content still matches the
verified baseline.

The module also exposes a SHA-256 path keyed off ``MANIFEST.sha256``
(the PROOF Level 3 reproducibility envelope's Tier 1 artefact). The
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
from vaibify.reproducibility.repoFiles import (
    SnapshotRepoFiles,
    ffilesEnsureRepoFiles,
)

from . import mtimeCache
from . import testMarkerContract

__all__ = [
    "fdictHashEntriesOfSnapshot",
    "fdictVerdictsForMarker",
    "fdictVerdictsForMarkerRuns",
    "flistMarkerHashedPaths",
    "fsVerdictForPath",
    "fbMarkerHasHashes",
    "fsetStaleOutputsAgainstManifest",
    "fbManifestExists",
    "fbAnyPathMissingFromManifest",
    "fbStepHashesMatchManifest",
]


_MANIFEST_FILENAME = "MANIFEST.sha256"


def fbMarkerHasHashes(dictMarker, sHashKey="dictOutputHashes"):
    """Return True when a marker carries content hashes under sHashKey."""
    if not isinstance(dictMarker, dict):
        return False
    dictHashes = dictMarker.get(sHashKey)
    return isinstance(dictHashes, dict) and len(dictHashes) > 0


S_VERDICT_MATCH = "match"
S_VERDICT_DRIFT = "drift"
S_VERDICT_UNKNOWN = "unknown"

_T_MARKER_HASH_KEYS = ("dictOutputHashes", "dictInputHashes")


def fdictHashEntriesOfSnapshot(filesPoll):
    """Return the hash entries the poll snapshot answered, keyed by path.

    Anything that is not a poll snapshot answers nothing, so every path
    reads unknown: the verdict never reaches for a second way to hash a
    file. A conservative snapshot is a snapshot with no hash entries,
    and reads the same way.
    """
    if not isinstance(filesPoll, SnapshotRepoFiles):
        return {}
    return filesPoll.fdictAllHashEntries()


def fsVerdictForPath(sBaselineSha, dictHashEntry):
    """Return match, drift or unknown for one path against its baseline.

    Drift is proven only by evidence: a digest that differs from the
    baseline, or the container's own report that the open raised
    ``FileNotFoundError`` (``bMissing``). A path absent from the answer,
    or answered without a digest (a torn read, an escape from the repo
    root, an open that failed for any other reason), proves nothing and
    is unknown. Absence from the poll's stat map is never consulted: it
    drops a path on any OSError, and a vanished container yields none.
    An empty baseline cannot match anything and counts as drift, as the
    host lane it replaces counted it.
    """
    if not sBaselineSha:
        return S_VERDICT_DRIFT
    if not isinstance(dictHashEntry, dict):
        return S_VERDICT_UNKNOWN
    if dictHashEntry.get("bMissing"):
        return S_VERDICT_DRIFT
    sCurrentSha = dictHashEntry.get("sBlobSha")
    if not sCurrentSha:
        return S_VERDICT_UNKNOWN
    if sCurrentSha == sBaselineSha:
        return S_VERDICT_MATCH
    return S_VERDICT_DRIFT


def fdictVerdictsForMarker(dictMarker, dictHashEntries):
    """Return ``{"listDrifted": [...], "listUnknown": [...]}`` for a marker.

    Every path the marker recorded a digest for, outputs and inputs
    alike, is judged against the poll snapshot's entry for it. Paths
    come back repo-relative and sorted. A marker with no hashes judges
    nothing.
    """
    setDrifted = set()
    setUnknown = set()
    for sHashKey in _T_MARKER_HASH_KEYS:
        if not fbMarkerHasHashes(dictMarker, sHashKey):
            continue
        for sRelPath, sBaselineSha in dictMarker[sHashKey].items():
            sVerdict = fsVerdictForPath(
                sBaselineSha, dictHashEntries.get(sRelPath),
            )
            if sVerdict == S_VERDICT_DRIFT:
                setDrifted.add(sRelPath)
            elif sVerdict == S_VERDICT_UNKNOWN:
                setUnknown.add(sRelPath)
    return {
        "listDrifted": sorted(setDrifted),
        "listUnknown": sorted(setUnknown - setDrifted),
    }


def _fsVerdictOfRun(dictRunVerdicts):
    """Name one run's verdict: drift beats unknown beats match; no hashes is none."""
    if dictRunVerdicts["listDrifted"]:
        return S_VERDICT_DRIFT
    if dictRunVerdicts["listUnknown"]:
        return S_VERDICT_UNKNOWN
    return S_VERDICT_MATCH


def fdictVerdictsForMarkerRuns(dictMarker, dictHashEntries):
    """Judge every run a marker still stands on, and say what the step is.

    Each run's recorded hashes are its own data state, judged against
    the files as the container hashed them (``dictRunVerdicts``: match,
    drift, unknown, or none for a run that recorded no hashes). The
    STEP is drifted when no run's data matches the files and some run's
    drifted -- the files then describe none of the data states any
    recorded result was obtained at -- and its paths are the newest
    drifted run's. A run that matches keeps the step from being
    drifted, but it vouches only for its own results: the step also
    lists the unanswered paths of EVERY run that could not be checked
    (unless the step is drifted), so a pass obtained under an unchecked
    run is never shown as verified because another run matched. A
    marker in the old single-run shape is one run called ``legacy``, so
    it judges as before.
    """
    dictNormalized = testMarkerContract.fdictNormalizeMarker(dictMarker)
    dictRuns = (dictNormalized or {}).get("dictRuns", {})
    dictPerRun = {}
    dictRunVerdicts = {}
    for sRunId, dictRun in dictRuns.items():
        dictPerRun[sRunId] = fdictVerdictsForMarker(dictRun, dictHashEntries)
        bHasHashes = any(
            fbMarkerHasHashes(dictRun, sKey) for sKey in _T_MARKER_HASH_KEYS)
        dictRunVerdicts[sRunId] = (
            _fsVerdictOfRun(dictPerRun[sRunId]) if bHasHashes else "none")
    dictStep = _fdictStepVerdictFromRuns(dictPerRun, dictRunVerdicts, dictRuns)
    dictStep["dictRunVerdicts"] = dictRunVerdicts
    return dictStep


def _fdictStepVerdictFromRuns(dictPerRun, dictRunVerdicts, dictRuns):
    """Fold per-run verdicts into the step's drifted and unknown paths.

    The two verdicts are folded by different rules because they mean
    different things. DRIFT is evidence the files changed, so any run
    that still matches the files clears the step of it. UNKNOWN is the
    absence of evidence about one run's data, and a run that matches
    says nothing about another run's results, so every unchecked run
    keeps its unanswered paths on the step.
    """
    listDrifted = _flistDriftedPathsOfTheStep(
        dictPerRun, dictRunVerdicts, dictRuns)
    listUnknown = [] if listDrifted else _flistUncheckedPathsOfEveryRun(
        dictPerRun, dictRunVerdicts)
    return {"listDrifted": listDrifted, "listUnknown": listUnknown}


def _flistDriftedPathsOfTheStep(dictPerRun, dictRunVerdicts, dictRuns):
    """Return the newest drifted run's paths, or none when any run matches."""
    if S_VERDICT_MATCH in dictRunVerdicts.values():
        return []
    listRunIds = [
        sRunId for sRunId, sVerdict in dictRunVerdicts.items()
        if sVerdict == S_VERDICT_DRIFT]
    if not listRunIds:
        return []
    sNewest = max(listRunIds, key=lambda sKey: dictRuns[sKey].get(
        "fTimestamp", 0))
    return dictPerRun[sNewest]["listDrifted"]


def _flistUncheckedPathsOfEveryRun(dictPerRun, dictRunVerdicts):
    """Return the sorted unanswered paths of every run that could not be checked."""
    setPaths = set()
    for sRunId, sVerdict in dictRunVerdicts.items():
        if sVerdict == S_VERDICT_UNKNOWN:
            setPaths.update(dictPerRun[sRunId]["listUnknown"])
    return sorted(setPaths)


def flistMarkerHashedPaths(dictMarkersByStep):
    """Return every repo-relative path any marker recorded a digest for.

    Every run counts, because each run's results are judged against its
    own data state; the poll adds these to the snapshot's hash batch, so
    a path a marker names but the workflow no longer declares is still
    judged.
    """
    setPaths = set()
    for dictMarker in (dictMarkersByStep or {}).values():
        dictNormalized = testMarkerContract.fdictNormalizeMarker(dictMarker)
        for dictRun in (dictNormalized or {}).get("dictRuns", {}).values():
            for sHashKey in _T_MARKER_HASH_KEYS:
                if fbMarkerHasHashes(dictRun, sHashKey):
                    setPaths.update(dictRun[sHashKey])
    return sorted(setPaths)


def fbManifestExists(filesRepo):
    """Return True iff ``<repo>/MANIFEST.sha256`` is a file."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.sRootPath:
        return False
    return filesRepo.fbIsFile(_MANIFEST_FILENAME)


def fbAnyPathMissingFromManifest(listRelPaths, dictEntries):
    """Return True iff any path is absent from the parsed manifest.

    A path the manifest does not list is not vouched for by it, so a
    freshness verdict built on the manifest must refuse rather than
    skip it: :func:`fsetStaleOutputsAgainstManifest` silently drops
    untracked paths, and a comparison that ignored them would call a
    step fresh on the strength of the files that happened to be pinned.
    """
    return any(sRelPath not in dictEntries for sRelPath in listRelPaths)


def fbStepHashesMatchManifest(
    dictStep, sRepoRoot, filesRepo, dictManifestCache=None,
):
    """Return True iff the step's pinned files still match MANIFEST.sha256.

    THE one freshness question the step row and the Level 1
    ``script-stale`` gate both ask, because the two kept their own
    copies and disagreed: the row counted a step's declared INPUTS and
    the gate counted outputs only, so a step whose tracked input had
    changed but whose outputs were identical showed ``modified`` while
    the gate stayed silent.

    Tracked inputs COUNT. An input that drifted since the manifest was
    pinned means the pinned outputs were produced from other bytes, and
    a fresh-clone shortcut that ignored it would mark a step clean on
    the strength of outputs that no longer follow from its inputs.

    Conservative on every uncertain path -- no repo root, no manifest,
    nothing declared, any declared path absent from the manifest, any
    hash that differs or cannot be read -- because True here SUPPRESSES
    a staleness signal.
    """
    if not sRepoRoot:
        return False
    from .fileStatusManager import _flistStepOutputsRepoRelative
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not fbManifestExists(filesRepo):
        return False
    listRelPaths = _flistStepOutputsRepoRelative(dictStep, sRepoRoot)
    if not listRelPaths:
        return False
    dictEntries = _fdictReadManifestEntries(filesRepo)
    if not dictEntries or fbAnyPathMissingFromManifest(
        listRelPaths, dictEntries,
    ):
        return False
    setStale = fsetStaleOutputsAgainstManifest(
        filesRepo, listRelPaths,
        {} if dictManifestCache is None else dictManifestCache,
    )
    return len(setStale) == 0


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
