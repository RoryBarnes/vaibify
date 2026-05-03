"""Write and verify a BSD-style SHA-256 manifest of workflow outputs.

The manifest is a single text file at ``<projectRepo>/MANIFEST.sha256``
containing one ``<hash>  <relpath>`` line per declared output. It is
the byte-exact, human-inspectable record of every artefact a workflow
produces, used by the AICS Level 3 reproducibility envelope to prove
that the artefacts a downstream consumer holds are bit-identical to
the ones the workflow generated.

Symbolic links inside declared outputs are rejected at write time:
following them silently would let the manifest hash a target that the
declared path no longer points to.
"""

from pathlib import Path

from vaibify.reproducibility.provenanceTracker import fsComputeFileHash


__all__ = [
    "fnWriteManifest",
    "flistVerifyManifest",
]


_MANIFEST_FILENAME = "MANIFEST.sha256"
_MANIFEST_HEADER = "# SHA-256 manifest of workflow outputs\n"
_OUTPUT_KEYS = ("saOutputFiles", "saPlotFiles", "saDataFiles")


def fnWriteManifest(sProjectRepo, dictWorkflow):
    """Write a sorted SHA-256 manifest of every declared workflow output.

    Walks ``dictWorkflow['listSteps']`` and collects every path in
    ``saOutputFiles``, ``saPlotFiles``, and ``saDataFiles``. Hashes
    each file with ``fsComputeFileHash`` and writes BSD shasum format
    (``<hash>  <relpath>\\n``) to ``<sProjectRepo>/MANIFEST.sha256``.
    Paths are repo-relative POSIX, sorted lexicographically. Raises
    ``ValueError`` if any declared output is a symbolic link.
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
    list means every recorded file matches its stored hash.
    """
    pathRepo = Path(sProjectRepo)
    listEntries = _flistReadManifestFile(pathRepo)
    listMismatches = []
    for sExpectedHash, sRelativePath in listEntries:
        dictMismatch = _fdictCheckEntry(
            pathRepo, sExpectedHash, sRelativePath,
        )
        if dictMismatch is not None:
            listMismatches.append(dictMismatch)
    return listMismatches


def _flistCollectOutputPaths(dictWorkflow):
    """Return a sorted, deduplicated list of repo-relative output paths."""
    setPaths = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        for sKey in _OUTPUT_KEYS:
            for sPath in dictStep.get(sKey, []) or []:
                setPaths.add(_fsNormalizeRelativePath(sPath))
    return sorted(setPaths)


def _fsNormalizeRelativePath(sPath):
    """Convert a path to forward-slash POSIX form for manifest output."""
    return str(sPath).replace("\\", "/")


def _flistBuildManifestEntries(pathRepo, listRelativePaths):
    """Return a list of ``(hash, relpath)`` tuples in sorted-input order."""
    listEntries = []
    for sRelativePath in listRelativePaths:
        pathFile = pathRepo / sRelativePath
        _fnRejectSymlink(pathFile, sRelativePath)
        sHash = fsComputeFileHash(str(pathFile))
        listEntries.append((sHash, sRelativePath))
    return listEntries


def _fnRejectSymlink(pathFile, sRelativePath):
    """Raise ``ValueError`` if ``pathFile`` is a symlink."""
    if pathFile.is_symlink():
        raise ValueError(
            f"refusing to hash symlink in manifest: '{sRelativePath}'"
        )


def _fnWriteManifestFile(pathRepo, listEntries):
    """Persist the manifest, header first then sorted shasum lines."""
    pathManifest = pathRepo / _MANIFEST_FILENAME
    pathManifest.parent.mkdir(parents=True, exist_ok=True)
    with open(pathManifest, "w", encoding="utf-8", newline="\n") as fileHandle:
        fileHandle.write(_MANIFEST_HEADER)
        for sHash, sRelativePath in listEntries:
            fileHandle.write(f"{sHash}  {sRelativePath}\n")


def _flistReadManifestFile(pathRepo):
    """Return ``(hash, relpath)`` tuples parsed from the manifest file."""
    pathManifest = pathRepo / _MANIFEST_FILENAME
    if not pathManifest.is_file():
        raise FileNotFoundError(
            f"manifest not found: '{pathManifest}'"
        )
    listEntries = []
    with open(pathManifest, "r", encoding="utf-8") as fileHandle:
        for sLine in fileHandle:
            tParsed = _tParseManifestLine(sLine)
            if tParsed is not None:
                listEntries.append(tParsed)
    return listEntries


def _tParseManifestLine(sLine):
    """Return ``(hash, relpath)`` or ``None`` for blank/comment lines."""
    sStripped = sLine.rstrip("\n")
    if not sStripped or sStripped.startswith("#"):
        return None
    sSeparator = "  "
    iSplit = sStripped.find(sSeparator)
    if iSplit < 0:
        raise ValueError(f"malformed manifest line: {sLine!r}")
    sHash = sStripped[:iSplit]
    sRelativePath = sStripped[iSplit + len(sSeparator):]
    return (sHash, sRelativePath)


def _fdictCheckEntry(pathRepo, sExpectedHash, sRelativePath):
    """Return mismatch dict or ``None`` when hashes agree."""
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
