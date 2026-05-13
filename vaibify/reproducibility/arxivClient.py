"""arXiv preprint server client for figure verification.

Read-only mirror of an arXiv paper's source tarball. The single
operation this module exposes is "given an arXiv ID and a list of
local repo-relative figure paths, return the SHA-256 hex digest of
each figure as it appears in the published e-print tarball." That
output is consumed by :mod:`scheduledReverify` exactly like the
GitHub, Overleaf, and Zenodo equivalents.

arXiv has no write API for third-party tools; submission is a
human-driven process. There is therefore no push-side counterpart to
this module.

Two policies are baked in:

- **Track latest.** Each verify call resolves the current latest
  version (``vN``) via the arXiv API and hashes against that version.
  A new version on arXiv that bytes-differs from local figures will
  surface as drift, identical to a local edit.
- **Basename match with explicit override.** Local figure paths map
  to tarball-internal paths through an optional
  ``dictPathMap = {sLocalRelPath: sTarballRelPath}``. When a local
  path is absent from the map, the module matches by basename across
  the tarball; ambiguous basenames raise
  :class:`ArxivAmbiguousMatchError` rather than picking arbitrarily.

Security notes:

- arXiv submissions are user content. The tarball-extraction helpers
  reject absolute paths, ``..`` segments, symlinks, hardlinks, and
  device nodes before any file is written to disk.
- A configurable total-uncompressed-size ceiling caps zip-bomb
  exposure (default 100 MB).
- The cache directory holds extracted tarballs for the lifetime of
  the workflow; the ``(arXivId, version)`` pair is immutable on
  arXiv so caching is semantic, not a stale-state hack.
"""

import hashlib
import os
import tarfile
import threading
import time
import urllib.parse
import xml.etree.ElementTree as etree

import requests


__all__ = [
    "ArxivError",
    "ArxivNotFoundError",
    "ArxivRateLimitError",
    "ArxivAmbiguousMatchError",
    "ArxivExtractionError",
    "ArxivPathMapError",
    "fdictFetchRemoteHashes",
    "fsResolveLatestVersion",
    "fsDownloadAndExtract",
]


class ArxivError(Exception):
    """General arXiv API or tarball-handling error."""


class ArxivNotFoundError(ArxivError):
    """arXiv paper or e-print not found (404)."""


class ArxivRateLimitError(ArxivError):
    """arXiv rate limit exceeded (429)."""


class ArxivAmbiguousMatchError(ArxivError):
    """A local figure's basename matches multiple files in the tarball."""


class ArxivExtractionError(ArxivError):
    """Tarball member rejected for security or size policy."""


class ArxivPathMapError(ArxivError):
    """``dictPathMap`` references a path that does not exist in the tarball."""


_S_API_URL = "https://export.arxiv.org/api/query"
_S_EPRINT_URL = "https://export.arxiv.org/e-print/"
_S_ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
_S_EXTRACTION_COMPLETE_SENTINEL = ".extraction-complete"

_F_REQUEST_INTERVAL_SECONDS = 3.0
_I_REQUEST_TIMEOUT_SECONDS = 60
_I_HASH_CHUNK_SIZE = 64 * 1024
_I_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

_lockRateLimit = threading.Lock()
_fLastRequestTime = 0.0


def _fnEnforceRateLimit():
    """Block until at least ``_F_REQUEST_INTERVAL_SECONDS`` since last request.

    arXiv asks API consumers to leave at least 3 seconds between
    successive requests. The floor is process-wide; concurrent verify
    calls will queue rather than burst.
    """
    global _fLastRequestTime
    with _lockRateLimit:
        fNow = time.monotonic()
        fSinceLast = fNow - _fLastRequestTime
        if fSinceLast < _F_REQUEST_INTERVAL_SECONDS:
            time.sleep(_F_REQUEST_INTERVAL_SECONDS - fSinceLast)
        _fLastRequestTime = time.monotonic()


def _fnRaiseForStatus(responseHttp, sResource):
    """Map HTTP error codes to typed arXiv exceptions."""
    iStatus = responseHttp.status_code
    if 200 <= iStatus < 300:
        return
    if iStatus == 404:
        raise ArxivNotFoundError(
            f"arXiv resource not found: {sResource}"
        )
    if iStatus == 429:
        raise ArxivRateLimitError(
            f"arXiv rate limit exceeded fetching {sResource}; "
            "retry after a backoff."
        )
    raise ArxivError(
        f"arXiv request failed ({iStatus}) for {sResource}."
    )


def _fsBuildApiUrl(sArxivId):
    """Return the API query URL for one arXiv identifier."""
    sQuery = urllib.parse.urlencode({"id_list": sArxivId})
    return f"{_S_API_URL}?{sQuery}"


def fsResolveLatestVersion(sArxivId):
    """Return the latest version suffix (``"v3"``) for an arXiv paper.

    Queries the arXiv API and parses the Atom feed for the requested
    identifier. Raises :class:`ArxivNotFoundError` when the feed
    contains no matching entry (the API returns a 200 with an empty
    feed for unknown IDs, so the absence has to be detected from the
    payload, not the status code).
    """
    _fnEnforceRateLimit()
    sUrl = _fsBuildApiUrl(sArxivId)
    responseHttp = requests.get(
        sUrl, timeout=_I_REQUEST_TIMEOUT_SECONDS,
    )
    _fnRaiseForStatus(responseHttp, f"metadata for {sArxivId}")
    return _fsParseLatestVersion(responseHttp.text, sArxivId)


def _fsParseLatestVersion(sAtomXml, sArxivId):
    """Extract the version suffix from an arXiv Atom feed."""
    try:
        elementRoot = etree.fromstring(sAtomXml)
    except etree.ParseError as errorParse:
        raise ArxivError(
            f"arXiv API returned invalid XML for {sArxivId}."
        ) from errorParse
    elementEntry = elementRoot.find(_S_ATOM_NAMESPACE + "entry")
    if elementEntry is None:
        raise ArxivNotFoundError(
            f"arXiv API returned no entry for '{sArxivId}'."
        )
    elementId = elementEntry.find(_S_ATOM_NAMESPACE + "id")
    if elementId is None or not elementId.text:
        raise ArxivError(
            f"arXiv entry for '{sArxivId}' lacks an id field."
        )
    return _fsExtractVersionFromAbsUrl(elementId.text, sArxivId)


def _fsExtractVersionFromAbsUrl(sAbsUrl, sArxivId):
    """Return the trailing ``vN`` token in an arXiv abstract URL."""
    sTail = sAbsUrl.rstrip("/").rsplit("/", 1)[-1]
    iDelim = sTail.rfind("v")
    if iDelim <= 0 or not sTail[iDelim + 1:].isdigit():
        raise ArxivError(
            f"arXiv entry id for '{sArxivId}' has no version suffix."
        )
    return sTail[iDelim:]


def fsDownloadAndExtract(sArxivId, sVersion, sCacheDir):
    """Download e-print tarball for ``(sArxivId, sVersion)`` and extract it.

    Returns the absolute path of the extraction directory. When a
    previous extraction completed successfully (sentinel file
    present), the cached extraction is reused; the ``(id, version)``
    pair is immutable on arXiv so refetching is wasted I/O. A partial
    extraction left behind by an earlier failure (e.g. a rejected
    member halfway through the tarball) is purged before retrying so
    the cache cannot be poisoned with half-extracted attacker content.
    """
    sExtractDir = _fsBuildExtractionPath(sCacheDir, sArxivId, sVersion)
    if _fbExtractionIsComplete(sExtractDir):
        return sExtractDir
    _fnPurgePartialExtraction(sExtractDir)
    sTempTarPath = _fsBuildCachedTarballPath(
        sCacheDir, sArxivId, sVersion,
    )
    _fnDownloadEprintTarball(sArxivId, sVersion, sTempTarPath)
    _fnExtractTarballOrPurge(sTempTarPath, sExtractDir)
    _fnRemoveTarballAfterExtract(sTempTarPath)
    _fnMarkExtractionComplete(sExtractDir)
    return sExtractDir


def _fnExtractTarballOrPurge(sTempTarPath, sExtractDir):
    """Run safe extraction; purge ``sExtractDir`` on any failure."""
    try:
        _fnExtractTarballSafely(sTempTarPath, sExtractDir)
    except BaseException:
        _fnPurgePartialExtraction(sExtractDir)
        _fnRemoveTarballAfterExtract(sTempTarPath)
        raise


def _fsBuildExtractionPath(sCacheDir, sArxivId, sVersion):
    """Return ``<sCacheDir>/<sArxivIdSanitized><sVersion>/``."""
    sSafeId = sArxivId.replace("/", "_")
    return os.path.join(sCacheDir, f"{sSafeId}{sVersion}")


def _fsBuildCachedTarballPath(sCacheDir, sArxivId, sVersion):
    """Return the path used for the downloaded tarball before extraction."""
    sSafeId = sArxivId.replace("/", "_")
    return os.path.join(sCacheDir, f"{sSafeId}{sVersion}.tar.gz")


def _fsBuildSentinelPath(sExtractDir):
    """Return the path of the extraction-complete sentinel file."""
    return os.path.join(sExtractDir, _S_EXTRACTION_COMPLETE_SENTINEL)


def _fbExtractionIsComplete(sExtractDir):
    """Return True when an extraction in ``sExtractDir`` finished cleanly."""
    return os.path.isfile(_fsBuildSentinelPath(sExtractDir))


def _fnMarkExtractionComplete(sExtractDir):
    """Write the sentinel that marks the extraction as cleanly finished."""
    sSentinel = _fsBuildSentinelPath(sExtractDir)
    with open(sSentinel, "wb") as fileHandle:
        fileHandle.write(b"")


def _fnPurgePartialExtraction(sExtractDir):
    """Remove a half-extracted directory so the cache cannot be poisoned."""
    import shutil
    if os.path.isdir(sExtractDir):
        shutil.rmtree(sExtractDir, ignore_errors=True)


def _fnRemoveTarballAfterExtract(sTarballPath):
    """Delete the downloaded tarball once its contents are on disk."""
    try:
        os.remove(sTarballPath)
    except OSError:
        pass


def _fnDownloadEprintTarball(sArxivId, sVersion, sDestinationPath):
    """Stream the e-print tarball for ``(sArxivId, sVersion)`` to disk."""
    _fnEnforceRateLimit()
    sUrl = _S_EPRINT_URL + sArxivId + sVersion
    os.makedirs(os.path.dirname(sDestinationPath), mode=0o700, exist_ok=True)
    responseHttp = requests.get(
        sUrl, stream=True, timeout=_I_REQUEST_TIMEOUT_SECONDS,
    )
    _fnRaiseForStatus(responseHttp, f"e-print {sArxivId}{sVersion}")
    iBytesWritten = 0
    with open(sDestinationPath, "wb") as fileHandle:
        for baChunk in responseHttp.iter_content(_I_HASH_CHUNK_SIZE):
            iBytesWritten += len(baChunk)
            if iBytesWritten > _I_MAX_UNCOMPRESSED_BYTES:
                fileHandle.close()
                os.remove(sDestinationPath)
                raise ArxivExtractionError(
                    "arXiv e-print exceeds the maximum download size."
                )
            fileHandle.write(baChunk)


def _fnExtractTarballSafely(sTarballPath, sExtractDir):
    """Extract a downloaded tarball into ``sExtractDir`` under strict rules.

    Rejects absolute paths, ``..`` segments after resolution, symbolic
    links, hard links, and special files. Caps total uncompressed
    bytes at ``_I_MAX_UNCOMPRESSED_BYTES``. Creates the extraction
    directory with mode 0700.
    """
    os.makedirs(sExtractDir, mode=0o700, exist_ok=True)
    sRealRoot = os.path.realpath(sExtractDir)
    iTotalUncompressed = 0
    with tarfile.open(sTarballPath, mode="r:*") as tarballHandle:
        for memberTar in tarballHandle:
            _fnValidateTarMember(memberTar, sRealRoot)
            iTotalUncompressed += memberTar.size
            if iTotalUncompressed > _I_MAX_UNCOMPRESSED_BYTES:
                raise ArxivExtractionError(
                    "arXiv e-print tarball exceeds the maximum "
                    "uncompressed-size ceiling."
                )
            tarballHandle.extract(memberTar, path=sExtractDir)


def _fnValidateTarMember(memberTar, sRealRoot):
    """Reject any tar member that would escape ``sRealRoot`` or is unsafe."""
    if memberTar.issym() or memberTar.islnk():
        raise ArxivExtractionError(
            f"arXiv tarball contains a symlink or hardlink: "
            f"{memberTar.name!r}; refusing to extract."
        )
    if memberTar.isdev() or memberTar.isfifo():
        raise ArxivExtractionError(
            f"arXiv tarball contains a special file: {memberTar.name!r}."
        )
    if os.path.isabs(memberTar.name):
        raise ArxivExtractionError(
            f"arXiv tarball entry is absolute: {memberTar.name!r}."
        )
    sCandidate = os.path.realpath(
        os.path.join(sRealRoot, memberTar.name),
    )
    if not _fbPathInsideRoot(sCandidate, sRealRoot):
        raise ArxivExtractionError(
            f"arXiv tarball entry escapes extraction root: "
            f"{memberTar.name!r}."
        )


def _fbPathInsideRoot(sCandidate, sRealRoot):
    """Return True when ``sCandidate`` lies inside ``sRealRoot``."""
    sNormalizedRoot = sRealRoot.rstrip(os.sep) + os.sep
    return (
        sCandidate == sRealRoot
        or sCandidate.startswith(sNormalizedRoot)
    )


def _fsHashFileSha256(sAbsolutePath):
    """Return the SHA-256 hex digest of one file, streaming in chunks."""
    objHasher = hashlib.sha256()
    with open(sAbsolutePath, "rb") as fileHandle:
        while True:
            baChunk = fileHandle.read(_I_HASH_CHUNK_SIZE)
            if not baChunk:
                break
            objHasher.update(baChunk)
    return objHasher.hexdigest()


def _fdictHashTreeByRelPath(sExtractDir):
    """Walk ``sExtractDir`` and return ``{tarball-relpath: sha256_hex}``.

    Skips the extraction-complete sentinel file so it never shadows a
    real tarball entry of the same name.
    """
    dictResult = {}
    iPrefixLen = len(sExtractDir.rstrip(os.sep)) + 1
    for sRoot, _listDirs, listFiles in os.walk(sExtractDir):
        for sFile in listFiles:
            sAbsolute = os.path.join(sRoot, sFile)
            sRelative = sAbsolute[iPrefixLen:]
            if sRelative == _S_EXTRACTION_COMPLETE_SENTINEL:
                continue
            dictResult[sRelative] = _fsHashFileSha256(sAbsolute)
    return dictResult


def _fsLocateTarballPath(
    sLocalRelPath, dictPathMap, dictTarballHashes,
):
    """Return the tarball relpath that supplies ``sLocalRelPath``'s bytes.

    Returns ``None`` when the local figure has no counterpart in the
    tarball *and* no explicit override was configured (the file is
    legitimately absent from the published e-print and surfaces as
    drift via the comparator). Raises
    :class:`ArxivAmbiguousMatchError` when basename fallback finds
    more than one match. Raises :class:`ArxivPathMapError` when the
    caller supplied a ``dictPathMap`` entry for this local path but
    the named tarball path does not exist — that is a workflow
    configuration bug, not drift, and silently treating it as drift
    would hide the misconfiguration from the user.
    """
    dictMap = dictPathMap or {}
    sOverride = dictMap.get(sLocalRelPath)
    if sOverride is not None:
        if sOverride not in dictTarballHashes:
            raise ArxivPathMapError(
                f"dictPathMap entry for '{sLocalRelPath}' points to "
                f"'{sOverride}', which does not exist in the arXiv "
                "tarball. Update or remove the mapping in vaibify.yml."
            )
        return sOverride
    sBasename = os.path.basename(sLocalRelPath)
    listMatches = [
        sTarballPath for sTarballPath in dictTarballHashes
        if os.path.basename(sTarballPath) == sBasename
    ]
    if len(listMatches) == 0:
        return None
    if len(listMatches) > 1:
        raise ArxivAmbiguousMatchError(
            f"Local figure '{sLocalRelPath}' matches multiple files "
            f"in the arXiv tarball by basename: {sorted(listMatches)!r}. "
            "Add an explicit dictPathMap entry to disambiguate."
        )
    return listMatches[0]


def fdictFetchRemoteHashes(
    sArxivId, listLocalRelPaths, dictPathMap=None, sCacheDir=None,
):
    """Return ``{sLocalRelPath: sha256_hex or None}`` from arXiv.

    Resolves the latest version of ``sArxivId``, downloads (or reuses
    a cached copy of) that version's e-print tarball, and matches each
    requested local path to a tarball entry via ``dictPathMap`` or by
    basename. Files absent from the tarball map to ``None`` so the
    divergence comparator in :mod:`scheduledReverify` can report them
    as drifted.

    ``sCacheDir`` defaults to a per-process temp directory when
    omitted; callers from the verify route pass the workflow's
    ``.vaibify/arxivCache/`` directory so cached tarballs survive
    server restarts.
    """
    sVersion = fsResolveLatestVersion(sArxivId)
    sResolvedCacheDir = sCacheDir or _fsBuildDefaultCacheDir()
    sExtractDir = fsDownloadAndExtract(
        sArxivId, sVersion, sResolvedCacheDir,
    )
    dictTarballHashes = _fdictHashTreeByRelPath(sExtractDir)
    return _fdictMapLocalToTarballHashes(
        listLocalRelPaths, dictPathMap, dictTarballHashes,
    )


def _fdictMapLocalToTarballHashes(
    listLocalRelPaths, dictPathMap, dictTarballHashes,
):
    """Return the per-local-path hash dict, in input order."""
    dictResult = {}
    for sLocalRelPath in listLocalRelPaths:
        sTarballPath = _fsLocateTarballPath(
            sLocalRelPath, dictPathMap, dictTarballHashes,
        )
        if sTarballPath is None:
            dictResult[sLocalRelPath] = None
        else:
            dictResult[sLocalRelPath] = dictTarballHashes[sTarballPath]
    return dictResult


def _fsBuildDefaultCacheDir():
    """Return a per-process default cache directory under tmp."""
    import tempfile
    sBase = tempfile.gettempdir()
    sPath = os.path.join(sBase, "vaibify-arxiv-cache")
    os.makedirs(sPath, mode=0o700, exist_ok=True)
    return sPath
