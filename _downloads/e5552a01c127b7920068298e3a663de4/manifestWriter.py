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
* Test files: every ``sFilePath`` declared under the same
  ``dictTests`` categories, plus every ``.py`` file named in a
  step's ``saTestCommands`` (e.g. ``pytest tests/test_step01.py``).
  Tests are part of the verification chain; a deposit without them
  cannot prove what "passing" meant. Workflows may opt out of
  archiving tests and standards by setting ``bArchiveTests`` to
  ``False`` at the workflow root; the default is ``True``.

Symbolic links anywhere on a declared path — leaf or intermediate
directory — are resolved and validated against the repo root. A
symlink whose resolved target stays *inside* the root hashes that
target's content, recorded under the declared (symlink) path. A
symlink whose target escapes the root is never opened or hashed;
the write path skips that single entry as a per-file gap (logged,
and surfaced by ``flistDeclaredButMissingFromManifest`` in the
manifest-completeness blockers) instead of aborting the whole
manifest. Non-symlink paths that escape the root (``..`` traversal)
still raise. The resolution and containment checks live in the
``repoFiles`` adapter so they run *inside* the container when the
repo lives there.

Path escaping follows the GNU ``sha256sum`` convention. When a
path contains a literal newline or backslash, the line is prefixed
with a single backslash and the path itself is encoded with
``\\\\`` for ``\\`` and ``\\n`` for newline. The leading backslash
prefix tells ``sha256sum -c`` that the path is escaped; with this
convention an attacker cannot smuggle a forged second line through
the manifest by injecting a newline into a filename.

Every public function accepts either a project-repo path string
(wrapped in ``HostRepoFiles``) or a ``repoFiles`` adapter, so the
same code is correct on a host clone and inside a container.
"""

import logging
import os
import posixpath

from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)
from vaibify.reproducibility.manifestPaths import (
    TUPLE_OUTPUT_KEYS,
    TUPLE_TEST_CATEGORY_KEYS,
    flistStepScriptRepoPaths,
    flistStepStandardsRepoPaths,
    fsToRepoRelative,
)


__all__ = [
    "fnWriteManifest",
    "flistVerifyManifest",
    "flistParseManifestLines",
    "flistDeclaredButMissingFromManifest",
    "fiCountManifestEntries",
    "fbWorkflowArchivesTests",
    "flistStepTestFileRepoPaths",
]


logger = logging.getLogger("vaibify")


_MANIFEST_FILENAME = "MANIFEST.sha256"
_MANIFEST_HEADER = "# SHA-256 manifest of workflow artefacts\n"
# Re-exported as a module attribute so the architectural-invariant test
# can introspect the canonical output-key set without importing
# ``manifestPaths`` directly.
_OUTPUT_KEYS = TUPLE_OUTPUT_KEYS


def fnWriteManifest(filesRepo, dictWorkflow):
    """Write a sorted SHA-256 manifest of every declared workflow artefact.

    Walks ``dictWorkflow['listSteps']`` and collects every output path
    (``saOutputFiles``, ``saPlotFiles``, ``saDataFiles``), every step
    script referenced by ``saDataCommands`` / ``saPlotCommands``, and
    every test ``sStandardsPath`` under ``dictTests``. Hashes the
    files in one adapter batch and writes GNU shasum format
    (``<hash>  <relpath>\\n``, escaped when needed) to
    ``<repo>/MANIFEST.sha256``. Paths are repo-relative POSIX, sorted
    lexicographically. A symlinked path resolving inside the repo
    root hashes its target content; one resolving outside the root is
    skipped as a logged per-file gap so a single stray symlink never
    aborts the write. Raises ``ValueError`` only when a non-symlink
    declared path escapes the repo root.

    Host adapters stream entry-by-entry into the temp file so a 1 M
    entry manifest does not materialize 80 MB of string + 80 MB of
    UTF-8 bytes in process memory. The non-host fallback retains the
    one-shot atomic write through the adapter API so container and
    snapshot adapters keep their existing semantics.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listRelativePaths = _flistCollectManifestPaths(dictWorkflow)
    listEntries = _flistBuildManifestEntries(filesRepo, listRelativePaths)
    if _fbCanStreamWrite(filesRepo):
        _fnStreamWriteManifest(filesRepo, listEntries)
        return
    sBody = _MANIFEST_HEADER + "".join(
        _fsFormatManifestLine(sHash, sRelativePath)
        for sHash, sRelativePath in listEntries
    )
    filesRepo.fnWriteTextAtomic(_MANIFEST_FILENAME, sBody)


def _fbCanStreamWrite(filesRepo):
    """Return True iff the adapter exposes a host root we can stream to."""
    fnLocalRoot = getattr(filesRepo, "fsLocalRootOrNone", None)
    if not callable(fnLocalRoot):
        return False
    return bool(fnLocalRoot())


def _fnStreamWriteManifest(filesRepo, listEntries):
    """Write the manifest line-by-line into a temp file, then atomic-rename.

    Iterates ``listEntries`` (an iterable of ``(sHash, sRelativePath)``
    tuples) writing each formatted line directly to the open temp
    handle. The whole-body string and its byte copy are never
    materialized. The temp file is replaced atomically over the
    canonical name; any failure cleans up the temp file.
    """
    sRoot = filesRepo.fsLocalRootOrNone()
    sAbsolute = os.path.join(sRoot, _MANIFEST_FILENAME)
    sTempPath = sAbsolute + ".tmp"
    try:
        with open(sTempPath, "w", encoding="utf-8", newline="\n") as f:
            f.write(_MANIFEST_HEADER)
            for sHash, sRelativePath in listEntries:
                f.write(_fsFormatManifestLine(sHash, sRelativePath))
        os.replace(sTempPath, sAbsolute)
    except OSError:
        _fnRemoveTempFileQuietly(sTempPath)
        raise


def _fnRemoveTempFileQuietly(sTempPath):
    """Best-effort remove of the streaming temp file on failure."""
    try:
        os.remove(sTempPath)
    except OSError:
        pass


def flistVerifyManifest(filesRepo):
    """Recompute hashes for every manifest entry and report mismatches.

    Returns a list of dicts of the form
    ``{'sPath': ..., 'sExpected': ..., 'sActual': ...}`` where
    ``sActual`` is ``None`` when the file is missing on disk or its
    symlink target escapes the repo root. An empty list means every
    recorded file matches its stored hash. In-root symlinked entries
    verify against their resolved target's content.

    Manifest-completeness (workflow declares paths the manifest does
    not cover) is surfaced via the explicit
    ``flistDeclaredButMissingFromManifest`` query, which both the
    dashboard route and the reproduce CLI consume.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listEntries = flistParseManifestLines(filesRepo)
    dictHashed = _fdictHashCheckedPaths(
        filesRepo, [dictEntry["sPath"] for dictEntry in listEntries],
    )
    listMismatches = []
    for dictEntry in listEntries:
        sActual = dictHashed[dictEntry["sPath"]].get("sSha256")
        if sActual != dictEntry["sExpected"]:
            listMismatches.append({
                "sPath": dictEntry["sPath"],
                "sExpected": dictEntry["sExpected"],
                "sActual": sActual,
            })
    return listMismatches


def flistDeclaredButMissingFromManifest(filesRepo, dictWorkflow):
    """Return repo-relative paths the workflow declares but the manifest omits.

    Pure helper that surfaces the manifest-completeness gap honestly.
    A user upgrading vaibify keeps a legacy manifest that pins outputs
    only; without this query the GUI cannot tell them their manifest
    is silently weaker than the new envelope guarantees. Raises
    ``FileNotFoundError`` when the manifest is absent.
    """
    listEntries = flistParseManifestLines(filesRepo)
    setManifestPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    listDeclared = _flistCollectManifestPaths(dictWorkflow)
    return [sPath for sPath in listDeclared if sPath not in setManifestPaths]


def flistParseManifestLines(filesRepo):
    """Return parsed manifest entries as a list of dicts.

    Each dict has ``sPath`` (repo-relative path, un-escaped) and
    ``sExpected`` (hex SHA-256). Comment (``#``) and blank lines are
    skipped. Raises ``ValueError`` on malformed lines (with line
    number and offending content). Raises ``FileNotFoundError`` when
    the manifest is absent.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.fbIsFile(_MANIFEST_FILENAME):
        sDisplayPath = os.path.join(
            fsRepoRootOf(filesRepo), _MANIFEST_FILENAME,
        )
        raise FileNotFoundError(f"manifest not found: '{sDisplayPath}'")
    listEntries = []
    listLines = filesRepo.fsReadText(_MANIFEST_FILENAME).splitlines(True)
    for iLineNumber, sLine in enumerate(listLines, start=1):
        dictEntry = _fdictParseManifestLine(sLine, iLineNumber)
        if dictEntry is not None:
            listEntries.append(dictEntry)
    return listEntries


def fiCountManifestEntries(filesRepo):
    """Return the number of non-comment, non-blank entries in the manifest."""
    return len(flistParseManifestLines(filesRepo))


def fbWorkflowArchivesTests(dictWorkflow):
    """Return True unless the workflow sets ``bArchiveTests`` to False.

    Tests and standards are archived and verified by default; the
    opt-out is an explicit per-workflow flag, never a silent
    exclusion.
    """
    return bool(dictWorkflow.get("bArchiveTests", True))


def flistStepTestFileRepoPaths(dictStep):
    """Return repo-relative paths of the test files one step declares.

    Covers the ``sFilePath`` of every ``dictTests`` category and every
    ``.py`` file named in ``saTestCommands`` (the legacy unit-test
    invocation, e.g. ``pytest tests/test_step01.py``). Command-derived
    paths are resolved against the step directory because the test
    commands run from the step's workdir.
    """
    listPaths = _flistTestCategoryFilePaths(dictStep)
    listPaths.extend(_flistTestCommandScriptPaths(dictStep))
    return listPaths


def _flistTestCategoryFilePaths(dictStep):
    """Return repo-relative ``dictTests[*].sFilePath`` entries."""
    dictTests = dictStep.get("dictTests", {})
    if not isinstance(dictTests, dict):
        return []
    listPaths = []
    for sCategory in TUPLE_TEST_CATEGORY_KEYS:
        dictCategory = dictTests.get(sCategory, {})
        if not isinstance(dictCategory, dict):
            continue
        sFilePath = dictCategory.get("sFilePath", "")
        if sFilePath and "{" not in sFilePath:
            listPaths.append(fsToRepoRelative(sFilePath))
    return listPaths


def _flistTestCommandScriptPaths(dictStep):
    """Return repo-relative ``.py`` paths named in saTestCommands."""
    sDirectory = dictStep.get("sDirectory", "") or ""
    listPaths = []
    for sCommand in dictStep.get("saTestCommands", []) or []:
        for sToken in str(sCommand).split():
            if _fbLooksLikeTestScriptToken(sToken):
                listPaths.append(
                    _fsResolveTestPathToRepoPath(sToken, sDirectory)
                )
    return listPaths


def _fbLooksLikeTestScriptToken(sToken):
    """Return True for a concrete ``.py`` path token (no flag/template)."""
    return (
        sToken.endswith(".py")
        and "{" not in sToken
        and not sToken.startswith("-")
    )


def _fsResolveTestPathToRepoPath(sPath, sDirectory):
    """Return ``sPath`` resolved against the step directory as repo path."""
    if sPath.startswith("/"):
        return fsToRepoRelative(sPath)
    if sDirectory:
        sJoined = posixpath.normpath(posixpath.join(sDirectory, sPath))
        return fsToRepoRelative(sJoined)
    return fsToRepoRelative(sPath)


def _flistCollectManifestPaths(dictWorkflow):
    """Return a sorted, deduplicated list of repo-relative artefact paths.

    The set spans declared outputs, step scripts, and — unless the
    workflow opts out via ``bArchiveTests`` — test files and test
    standards, so that the manifest pins the entire
    input-to-output-to-verification chain. Each path-extraction
    sub-helper is single-purposed and orthogonal so the union never
    silently drops a category.
    """
    setPaths = set()
    bArchiveTests = fbWorkflowArchivesTests(dictWorkflow)
    for dictStep in dictWorkflow.get("listSteps", []):
        setPaths.update(_flistStepOutputPaths(dictStep))
        setPaths.update(flistStepScriptRepoPaths(dictStep))
        if bArchiveTests:
            setPaths.update(flistStepStandardsRepoPaths(dictStep))
            setPaths.update(flistStepTestFileRepoPaths(dictStep))
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


def _flistBuildManifestEntries(filesRepo, listRelativePaths):
    """Return ``(hash, relpath)`` tuples, skipping out-of-root symlinks.

    A declared path whose symlink target resolves outside the repo
    root is skipped as a per-file gap (logged here, surfaced by
    ``flistDeclaredButMissingFromManifest``) so one stray symlink can
    never abort the whole manifest write. Missing or unreadable
    regular files still raise so a broken declaration stays loud.
    """
    dictHashed = _fdictHashCheckedPaths(filesRepo, listRelativePaths)
    listEntries = []
    for sRelativePath in listRelativePaths:
        dictEntry = dictHashed[sRelativePath]
        if _fbSymlinkTargetEscapesRoot(dictEntry):
            _fnLogSkippedSymlinkGap(filesRepo, sRelativePath, dictEntry)
            continue
        sHash = dictEntry.get("sSha256")
        if sHash is None:
            _fnRaiseUnhashableFile(filesRepo, sRelativePath)
        listEntries.append((sHash, sRelativePath))
    return listEntries


def _fdictHashCheckedPaths(filesRepo, listRelativePaths):
    """Hash paths in one adapter batch, raising on traversal findings.

    Returns ``{sRelativePath: dictAdapterEntry}`` where each entry
    carries ``sSha256`` (``None`` when the file is absent, unreadable,
    or an out-of-root symlink), ``sSymlinkSegment``, and
    ``bEscapesRoot``. Only a *non-symlink* escape (``..`` traversal or
    an absolute path) raises here; symlink policy is the callers'.
    """
    for sRelativePath in listRelativePaths:
        _fnRejectAbsolutePath(sRelativePath)
    dictResults = filesRepo.fdictHashFiles(listRelativePaths)
    dictHashed = {}
    for sRelativePath in listRelativePaths:
        dictEntry = dictResults.get(sRelativePath) or {}
        _fnRejectAdapterFindings(sRelativePath, dictEntry)
        dictHashed[sRelativePath] = dictEntry
    return dictHashed


def _fnRejectAbsolutePath(sRelativePath):
    """Raise ``ValueError`` when a declared path is absolute."""
    if os.path.isabs(sRelativePath):
        raise ValueError(
            f"refusing to hash absolute path: '{sRelativePath}'"
        )


def _fnRejectAdapterFindings(sRelativePath, dictEntry):
    """Raise ``ValueError`` for a non-symlink path escaping the repo root.

    Symlinked paths never raise here: an in-root target hashes
    normally, while an out-of-root target is skipped per-file by the
    write path and reported as ``sActual = None`` by the verify path.
    """
    if dictEntry.get("bEscapesRoot") and (
        dictEntry.get("sSymlinkSegment") is None
    ):
        raise ValueError(
            f"refusing to hash path escaping repo root: '{sRelativePath}'"
        )


def _fbSymlinkTargetEscapesRoot(dictEntry):
    """Return True when a symlinked path resolves outside the repo root."""
    return bool(
        dictEntry.get("sSymlinkSegment") is not None
        and dictEntry.get("bEscapesRoot")
    )


def _fnLogSkippedSymlinkGap(filesRepo, sRelativePath, dictEntry):
    """Log the per-file gap left by an out-of-root symlinked declaration."""
    logger.warning(
        "MANIFEST.sha256 skips '%s': symlink '%s' resolves outside "
        "repo root '%s'; the gap stays visible in the "
        "manifest-completeness blockers.",
        sRelativePath, dictEntry.get("sSymlinkSegment"),
        fsRepoRootOf(filesRepo),
    )


def _fnRaiseUnhashableFile(filesRepo, sRelativePath):
    """Raise ``FileNotFoundError`` naming the unhashable declared file."""
    sDisplayPath = os.path.join(fsRepoRootOf(filesRepo), sRelativePath)
    raise FileNotFoundError(
        "Cannot hash file (refusing to follow symlink or open): "
        f"'{sDisplayPath}'"
    )


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
