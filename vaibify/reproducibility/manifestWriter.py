"""Write and verify a GNU-style SHA-256 manifest of workflow outputs.

The manifest is a single text file at ``<projectRepo>/MANIFEST.sha256``
containing one ``<hash>  <relpath>`` line per declared output. It is
the byte-exact, human-inspectable record of every artefact a workflow
produces, used by the AICS Level 3 reproducibility envelope to prove
that the artefacts a downstream consumer holds are bit-identical to
the ones the workflow generated.

Symbolic links anywhere on a declared path — leaf or intermediate
directory — are rejected at write and verify time. Following them
would let the manifest hash a target the declared path no longer
points to. The first symlink component on the path is named in the
error so the offending segment is easy to locate.

Path escaping follows the GNU ``sha256sum`` convention. When a
path contains a literal newline or backslash, the line is prefixed
with a single backslash and the path itself is encoded with
``\\\\`` for ``\\`` and ``\\n`` for newline. The leading backslash
prefix tells ``sha256sum -c`` that the path is escaped; with this
convention an attacker cannot smuggle a forged second line through
the manifest by injecting a newline into a filename.
"""

import os
from pathlib import Path

from vaibify.reproducibility.provenanceTracker import fsComputeFileHash


__all__ = [
    "fnWriteManifest",
    "flistVerifyManifest",
    "flistParseManifestLines",
    "fiCountManifestEntries",
]


_MANIFEST_FILENAME = "MANIFEST.sha256"
_MANIFEST_HEADER = "# SHA-256 manifest of workflow outputs\n"
_OUTPUT_KEYS = ("saOutputFiles", "saPlotFiles", "saDataFiles")


def fnWriteManifest(sProjectRepo, dictWorkflow):
    """Write a sorted SHA-256 manifest of every declared workflow output.

    Walks ``dictWorkflow['listSteps']`` and collects every path in
    ``saOutputFiles``, ``saPlotFiles``, and ``saDataFiles``. Hashes
    each file with ``fsComputeFileHash`` and writes GNU shasum format
    (``<hash>  <relpath>\\n``, escaped when needed) to
    ``<sProjectRepo>/MANIFEST.sha256``. Paths are repo-relative
    POSIX, sorted lexicographically. Raises ``ValueError`` if any
    component on a declared output path is a symbolic link.
    """
    pathRepo = Path(sProjectRepo)
    listRelativePaths = _flistCollectOutputPaths(dictWorkflow)
    listEntries = _flistBuildManifestEntries(pathRepo, listRelativePaths)
    _fnWriteManifestFile(pathRepo, listEntries)


def flistVerifyManifest(sProjectRepo):
    """Recompute hashes for every manifest entry and report mismatches.

    Returns a list of dicts of the form
    ``{'sPath': ..., 'sExpected': ..., 'sActual': ...}`` where
    ``sActual`` is ``None`` when the file is missing on disk. An empty
    list means every recorded file matches its stored hash. Raises
    ``ValueError`` if any component on a verified path is a symlink.
    """
    pathRepo = Path(sProjectRepo)
    listEntries = flistParseManifestLines(sProjectRepo)
    listMismatches = []
    for dictEntry in listEntries:
        dictMismatch = _fdictCheckEntry(
            pathRepo, dictEntry["sExpected"], dictEntry["sPath"],
        )
        if dictMismatch is not None:
            listMismatches.append(dictMismatch)
    return listMismatches


def flistParseManifestLines(sProjectRepo):
    """Return parsed manifest entries as a list of dicts.

    Each dict has ``sPath`` (repo-relative path, un-escaped) and
    ``sExpected`` (hex SHA-256). Comment (``#``) and blank lines are
    skipped. Raises ``ValueError`` on malformed lines (with line
    number and offending content). Raises ``FileNotFoundError`` when
    the manifest is absent.
    """
    pathManifest = Path(sProjectRepo) / _MANIFEST_FILENAME
    if not pathManifest.is_file():
        raise FileNotFoundError(f"manifest not found: '{pathManifest}'")
    listEntries = []
    with open(pathManifest, "r", encoding="utf-8") as fileHandle:
        for iLineNumber, sLine in enumerate(fileHandle, start=1):
            dictEntry = _fdictParseManifestLine(sLine, iLineNumber)
            if dictEntry is not None:
                listEntries.append(dictEntry)
    return listEntries


def fiCountManifestEntries(sProjectRepo):
    """Return the number of non-comment, non-blank entries in the manifest."""
    return len(flistParseManifestLines(sProjectRepo))


def _flistCollectOutputPaths(dictWorkflow):
    """Return a sorted, deduplicated list of repo-relative output paths."""
    setPaths = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        for sKey in _OUTPUT_KEYS:
            for sPath in dictStep.get(sKey, []) or []:
                setPaths.add(_fsNormalizeRelativePath(sPath))
    return sorted(setPaths)


def _fsNormalizeRelativePath(sPath):
    """Return a POSIX-form path without corrupting embedded backslashes.

    On Windows (which vaibify does not officially support but does run
    in CI smoke), ``os.sep`` is ``\\`` and the substitution maps real
    path separators to POSIX. On Linux/macOS ``\\`` is a legal filename
    character; rewriting it would silently break the GNU-escape path.
    """
    if os.sep == "\\":
        return str(sPath).replace("\\", "/")
    return str(sPath)


def _flistBuildManifestEntries(pathRepo, listRelativePaths):
    """Return a list of ``(hash, relpath)`` tuples in sorted-input order."""
    listEntries = []
    for sRelativePath in listRelativePaths:
        pathFile = pathRepo / sRelativePath
        _fnRejectSymlinkComponent(pathRepo, sRelativePath)
        sHash = fsComputeFileHash(str(pathFile))
        listEntries.append((sHash, sRelativePath))
    return listEntries


def _fnRejectSymlinkComponent(pathRepo, sRelativePath):
    """Raise ``ValueError`` if any component of the path is a symlink.

    Walks the relative path one segment at a time from ``pathRepo``
    and uses ``Path.is_symlink`` (which inspects the link itself,
    not the target). The first offending component is reported by
    name so the user can locate it without scanning the full tree.
    """
    sFirstSymlink = _fsFindFirstSymlinkSegment(pathRepo, sRelativePath)
    if sFirstSymlink is not None:
        raise ValueError(
            f"refusing to hash path crossing symlink '{sFirstSymlink}' "
            f"in declared output: '{sRelativePath}'"
        )


def _fsFindFirstSymlinkSegment(pathRepo, sRelativePath):
    """Return the first symlinked segment along the path, or ``None``."""
    listSegments = _flistSplitRelativePath(sRelativePath)
    pathCurrent = pathRepo
    for sSegment in listSegments:
        pathCurrent = pathCurrent / sSegment
        if pathCurrent.is_symlink():
            return sSegment
    return None


def _flistSplitRelativePath(sRelativePath):
    """Split a relative path into ordered segments, ignoring empties."""
    sNormalized = _fsNormalizeRelativePath(sRelativePath)
    return [sPart for sPart in sNormalized.split("/") if sPart]


def _fnWriteManifestFile(pathRepo, listEntries):
    """Persist the manifest, header first then sorted shasum lines."""
    pathManifest = pathRepo / _MANIFEST_FILENAME
    pathManifest.parent.mkdir(parents=True, exist_ok=True)
    with open(pathManifest, "w", encoding="utf-8", newline="\n") as fileHandle:
        fileHandle.write(_MANIFEST_HEADER)
        for sHash, sRelativePath in listEntries:
            fileHandle.write(_fsFormatManifestLine(sHash, sRelativePath))


def _fsFormatManifestLine(sHash, sRelativePath):
    """Return one shasum line with GNU escaping when the path needs it."""
    if _fbPathNeedsEscape(sRelativePath):
        sEscaped = _fsEscapeManifestPath(sRelativePath)
        return f"\\{sHash}  {sEscaped}\n"
    return f"{sHash}  {sRelativePath}\n"


def _fbPathNeedsEscape(sRelativePath):
    """Return True when the path contains characters requiring GNU escape."""
    return "\\" in sRelativePath or "\n" in sRelativePath


def _fsEscapeManifestPath(sRelativePath):
    """Encode backslash and newline per GNU sha256sum convention."""
    sBackslashEscaped = sRelativePath.replace("\\", "\\\\")
    return sBackslashEscaped.replace("\n", "\\n")


def _fsUnescapeManifestPath(sEscaped):
    """Reverse the GNU escaping applied by ``_fsEscapeManifestPath``."""
    listChars = []
    iIndex = 0
    while iIndex < len(sEscaped):
        sChar = sEscaped[iIndex]
        if sChar == "\\" and iIndex + 1 < len(sEscaped):
            sNext = sEscaped[iIndex + 1]
            if sNext == "n":
                listChars.append("\n")
                iIndex += 2
                continue
            if sNext == "\\":
                listChars.append("\\")
                iIndex += 2
                continue
        listChars.append(sChar)
        iIndex += 1
    return "".join(listChars)


def _fdictParseManifestLine(sLine, iLineNumber):
    """Return entry dict or ``None`` for blank/comment lines."""
    sStripped = sLine.rstrip("\n")
    if not sStripped or sStripped.startswith("#"):
        return None
    bEscaped = sStripped.startswith("\\")
    sBody = sStripped[1:] if bEscaped else sStripped
    sSeparator = "  "
    iSplit = sBody.find(sSeparator)
    if iSplit < 0:
        raise ValueError(
            f"malformed manifest line {iLineNumber}: {sLine!r}"
        )
    sHash = sBody[:iSplit]
    sStoredPath = sBody[iSplit + len(sSeparator):]
    sPath = _fsUnescapeManifestPath(sStoredPath) if bEscaped else sStoredPath
    return {"sPath": sPath, "sExpected": sHash}


def _fdictCheckEntry(pathRepo, sExpectedHash, sRelativePath):
    """Return mismatch dict or ``None`` when hashes agree."""
    _fnRejectSymlinkComponent(pathRepo, sRelativePath)
    pathFile = pathRepo / sRelativePath
    if not pathFile.is_file():
        return {
            "sPath": sRelativePath,
            "sExpected": sExpectedHash,
            "sActual": None,
        }
    sActualHash = fsComputeFileHash(str(pathFile))
    if sActualHash == sExpectedHash:
        return None
    return {
        "sPath": sRelativePath,
        "sExpected": sExpectedHash,
        "sActual": sActualHash,
    }
