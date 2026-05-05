"""Write and verify a GNU-style SHA-256 manifest of workflow artefacts.

The manifest is a single text file at ``<projectRepo>/MANIFEST.sha256``
containing one ``<hash>  <relpath>`` line per declared artefact. It is
the byte-exact, human-inspectable record of every artefact a workflow
involves, used by the AICS Level 3 reproducibility envelope to prove
that the inputs and outputs a downstream consumer holds are
bit-identical to the ones the workflow used.

The manifest envelope covers the full input-to-output chain so that a
third party can verify the *code* that produced the outputs as well as
the outputs themselves:

* Output artefacts: every path in ``saOutputFiles``, ``saPlotFiles``,
  and ``saDataFiles`` for every step.
* Step scripts: every ``.py`` file referenced by ``saDataCommands``
  and ``saPlotCommands``. Without these in the manifest, a downstream
  consumer could verify the outputs match but could not detect that
  the producing script was tampered with after the run.
* Test standards: every ``sStandardsPath`` declared in
  ``dictTests.dictQualitative``, ``dictQuantitative``, and
  ``dictIntegrity``. Standards are golden references; without them
  hash-pinned, a consumer can't tell if a "passing" test row was
  passing against the original reference or a substituted one.

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
import warnings
from pathlib import Path

from vaibify.reproducibility.provenanceTracker import fsComputeFileHash
from vaibify.reproducibility.manifestPaths import (
    TUPLE_OUTPUT_KEYS,
    flistStepScriptRepoPaths,
    flistStepStandardsRepoPaths,
)


__all__ = [
    "fnWriteManifest",
    "flistVerifyManifest",
    "flistParseManifestLines",
    "flistDeclaredButMissingFromManifest",
    "fiCountManifestEntries",
]


_MANIFEST_FILENAME = "MANIFEST.sha256"
_MANIFEST_HEADER = "# SHA-256 manifest of workflow artefacts\n"
# Re-exported as a module attribute so the architectural-invariant test
# can introspect the canonical output-key set without importing
# ``manifestPaths`` directly.
_OUTPUT_KEYS = TUPLE_OUTPUT_KEYS


def fnWriteManifest(sProjectRepo, dictWorkflow):
    """Write a sorted SHA-256 manifest of every declared workflow artefact.

    Walks ``dictWorkflow['listSteps']`` and collects every output path
    (``saOutputFiles``, ``saPlotFiles``, ``saDataFiles``), every step
    script referenced by ``saDataCommands`` / ``saPlotCommands``, and
    every test ``sStandardsPath`` under ``dictTests``. Hashes each
    file with ``fsComputeFileHash`` and writes GNU shasum format
    (``<hash>  <relpath>\\n``, escaped when needed) to
    ``<sProjectRepo>/MANIFEST.sha256``. Paths are repo-relative
    POSIX, sorted lexicographically. Raises ``ValueError`` if any
    component on a declared path is a symbolic link.
    """
    pathRepo = Path(sProjectRepo)
    listRelativePaths = _flistCollectManifestPaths(dictWorkflow)
    listEntries = _flistBuildManifestEntries(pathRepo, listRelativePaths)
    _fnWriteManifestFile(pathRepo, listEntries)


def flistVerifyManifest(sProjectRepo, dictWorkflow=None):
    """Recompute hashes for every manifest entry and report mismatches.

    Returns a list of dicts of the form
    ``{'sPath': ..., 'sExpected': ..., 'sActual': ...}`` where
    ``sActual`` is ``None`` when the file is missing on disk. An empty
    list means every recorded file matches its stored hash. Raises
    ``ValueError`` if any component on a verified path is a symlink.

    When ``dictWorkflow`` is supplied, also emits a ``UserWarning`` if
    the workflow currently declares paths absent from the manifest
    (e.g. a manifest written before the script + standards rows were
    added to the envelope). The warning is advisory: legacy manifests
    still verify clean for what they cover, but the user is told the
    coverage is partial so they can re-run to refresh.
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
    if dictWorkflow is not None:
        _fnWarnIfManifestIncomplete(listEntries, dictWorkflow)
    return listMismatches


def flistDeclaredButMissingFromManifest(sProjectRepo, dictWorkflow):
    """Return repo-relative paths the workflow declares but the manifest omits.

    Pure helper that surfaces the manifest-completeness gap honestly.
    A user upgrading vaibify keeps a legacy manifest that pins outputs
    only; without this query the GUI cannot tell them their manifest
    is silently weaker than the new envelope guarantees. Raises
    ``FileNotFoundError`` when the manifest is absent.
    """
    listEntries = flistParseManifestLines(sProjectRepo)
    setManifestPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    listDeclared = _flistCollectManifestPaths(dictWorkflow)
    return [sPath for sPath in listDeclared if sPath not in setManifestPaths]


def _fnWarnIfManifestIncomplete(listEntries, dictWorkflow):
    """Emit ``UserWarning`` when declared paths are absent from the manifest."""
    setManifestPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    listDeclared = _flistCollectManifestPaths(dictWorkflow)
    listMissing = [s for s in listDeclared if s not in setManifestPaths]
    if listMissing:
        warnings.warn(
            f"manifest is missing entries for {len(listMissing)} "
            f"path(s) the workflow currently declares "
            f"(first: '{listMissing[0]}'); re-run to refresh coverage.",
            UserWarning,
            stacklevel=2,
        )


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


def _flistCollectManifestPaths(dictWorkflow):
    """Return a sorted, deduplicated list of repo-relative artefact paths.

    The set spans declared outputs, step scripts, and test standards
    so that the manifest pins the entire input-to-output chain. Each
    path-extraction sub-helper is single-purposed and orthogonal so
    the union never silently drops a category.
    """
    setPaths = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        setPaths.update(_flistStepOutputPaths(dictStep))
        setPaths.update(flistStepScriptRepoPaths(dictStep))
        setPaths.update(flistStepStandardsRepoPaths(dictStep))
    return sorted(sPath for sPath in setPaths if sPath)


def _flistStepOutputPaths(dictStep):
    """Return repo-relative output paths declared by one step."""
    listPaths = []
    for sKey in TUPLE_OUTPUT_KEYS:
        for sPath in dictStep.get(sKey, []) or []:
            listPaths.append(_fsNormalizeRelativePath(sPath))
    return listPaths


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
        _fnRejectPathEscape(pathRepo, sRelativePath)
        sHash = fsComputeFileHash(str(pathFile))
        listEntries.append((sHash, sRelativePath))
    return listEntries


def _fnRejectPathEscape(pathRepo, sRelativePath):
    """Raise ``ValueError`` if the relative path escapes ``pathRepo``.

    Rejects absolute paths and any relative path whose realpath does
    not stay strictly inside ``pathRepo``. Symlink rejection runs
    first; this guard catches plain ``..`` traversal that bypasses the
    symlink check because ``..`` is not itself a link.
    """
    if os.path.isabs(sRelativePath):
        raise ValueError(
            f"refusing to hash absolute path: '{sRelativePath}'"
        )
    sRepoReal = os.path.realpath(str(pathRepo))
    sCandidateReal = os.path.realpath(os.path.join(sRepoReal, sRelativePath))
    if sCandidateReal != sRepoReal and not sCandidateReal.startswith(
        sRepoReal + os.sep,
    ):
        raise ValueError(
            f"refusing to hash path escaping repo root: '{sRelativePath}'"
        )


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
    _fnRejectPathEscape(pathRepo, sRelativePath)
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
