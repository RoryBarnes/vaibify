"""Manage conftest.py marker plugin and tests directory in containers."""

__all__ = [
    "S_CONFTEST_VERSION",
    "fsConftestPath",
    "fsConftestContent",
    "fsBuildConftestSource",
    "fsReadInstalledConftestVersion",
    "fnWriteConftestMarker",
    "fnEnsureTestsDirectory",
    "fnEnsureConftestsCurrent",
    "fnMigrateFlatMarkers",
]

import logging
import posixpath
import re


logger = logging.getLogger("vaibify")


# Bump this when the generated conftest source changes shape so installed
# copies on a researcher's host get refreshed on the next connect tick.
# The constant is embedded in every generated file as a comment line
# beginning with ``S_CONFTEST_VERSION_PREFIX`` so the reader can detect
# stale copies without parsing the source.
S_CONFTEST_VERSION = "2"
S_CONFTEST_VERSION_PREFIX = "# vaibify-conftest-version: "
_REGEX_CONFTEST_VERSION = re.compile(
    r"^# vaibify-conftest-version:\s*(\S+)\s*$", re.MULTILINE,
)


def fsConftestPath(sStepDirectory):
    """Return the conftest.py path for a step's tests directory."""
    return posixpath.join(sStepDirectory, "tests", "conftest.py")


def fsConftestContent(sProjectRepoPath=""):
    """Return the conftest.py marker plugin source for a project repo.

    When ``sProjectRepoPath`` is empty, returns the template body
    without its prologue — useful only for tests that inspect the
    template structure. Runtime callers (``fnWriteConftestMarker``)
    always pass a project-repo path so the generated file is
    self-contained. The version sentinel is always present so any
    installed copy can be inspected by the refresh helper.
    """
    if not sProjectRepoPath:
        return _fsVersionStampLine() + _CONFTEST_MARKER_TEMPLATE
    return fsBuildConftestSource(sProjectRepoPath)


def fsBuildConftestSource(sProjectRepoPath):
    """Return conftest.py source with a project-repo-aware prologue.

    Substitutes ``sProjectRepoPath`` into a small header that defines
    ``_PROJECT_REPO``, ``_MARKER_BASE``, and ``_WORKFLOWS_DIR``. The
    template body computes the active workflow's slug at run time and
    writes markers to
    ``<sProjectRepoPath>/.vaibify/test_markers/<slug>/`` so workflows
    sharing a step directory don't clobber each other. The ``!r``
    substitution produces a quoted Python literal and sidesteps the
    f-string/format-escape trap that affects the template body. A
    ``# vaibify-conftest-version:`` comment is prepended so
    ``fsReadInstalledConftestVersion`` can detect stale copies on a
    researcher's host without parsing the source.
    """
    sPrologue = _S_CONFTEST_PROLOGUE_FORMAT.format(
        sProjectRepoPath=sProjectRepoPath,
    )
    return (
        _fsVersionStampLine() + sPrologue + _CONFTEST_MARKER_TEMPLATE
    )


def _fsVersionStampLine():
    """Return the single-line version stamp comment with trailing newline."""
    return S_CONFTEST_VERSION_PREFIX + S_CONFTEST_VERSION + "\n"


def fsReadInstalledConftestVersion(
    connectionDocker, sContainerId, sConftestPath,
):
    """Return the ``S_CONFTEST_VERSION`` value embedded in an installed file.

    Reads ``sConftestPath`` from the container and parses the
    ``# vaibify-conftest-version:`` sentinel via a compiled regex.
    Returns the empty string when the file is missing, unreadable, or
    carries no sentinel — the refresh helper treats any of those
    outcomes as "needs rewriting".
    """
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, sConftestPath,
        )
    except Exception:
        return ""
    sSource = baContent.decode("utf-8", errors="replace")
    matchVersion = _REGEX_CONFTEST_VERSION.search(sSource)
    if matchVersion is None:
        return ""
    return matchVersion.group(1)


def _fsAbsoluteStepDir(sStepDirectory, sProjectRepoPath):
    """Return container-absolute step dir for path ops in this module."""
    if not sStepDirectory or posixpath.isabs(sStepDirectory):
        return sStepDirectory
    if not sProjectRepoPath:
        return sStepDirectory
    return posixpath.join(sProjectRepoPath, sStepDirectory)


def fnWriteConftestMarker(
    connectionDocker, sContainerId, sStepDirectory, sProjectRepoPath,
):
    """Write the conftest.py marker plugin into a step's tests dir."""
    sAbsStepDir = _fsAbsoluteStepDir(sStepDirectory, sProjectRepoPath)
    sPath = fsConftestPath(sAbsStepDir)
    sSource = fsBuildConftestSource(sProjectRepoPath)
    connectionDocker.fnWriteFile(
        sContainerId, sPath, sSource.encode("utf-8"),
    )


def fnEnsureTestsDirectory(
    connectionDocker, sContainerId, sStepDirectory,
    sProjectRepoPath="",
):
    """Create the tests subdirectory in the container if missing."""
    from .pipelineRunner import fsShellQuote
    sAbsStepDir = _fsAbsoluteStepDir(sStepDirectory, sProjectRepoPath)
    sTestsDir = posixpath.join(sAbsStepDir, "tests")
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sTestsDir)}"
    )


def fnEnsureConftestsCurrent(
    connectionDocker, sContainerId, listStepDirs, sProjectRepoPath,
):
    """Refresh stale or missing conftest.py copies in each step's tests/.

    For every step directory in ``listStepDirs``, compares the
    installed conftest's embedded version stamp against
    ``S_CONFTEST_VERSION``. When the stamp is absent (legacy conftest
    or no file at all) or mismatched (template bumped since the file
    was written), rewrites the file with the current
    ``fsBuildConftestSource`` output. Idempotent: a current file is
    left untouched. Short-circuits when ``listStepDirs`` is empty so
    the connect path pays no per-step cost on a brand-new workflow.
    """
    if not listStepDirs:
        return
    for sStepDir in listStepDirs:
        sAbsStepDir = _fsAbsoluteStepDir(sStepDir, sProjectRepoPath)
        sConftestPath = fsConftestPath(sAbsStepDir)
        sInstalled = fsReadInstalledConftestVersion(
            connectionDocker, sContainerId, sConftestPath,
        )
        if sInstalled == S_CONFTEST_VERSION:
            continue
        _fnRewriteConftest(
            connectionDocker, sContainerId, sStepDir,
            sProjectRepoPath, sInstalled,
        )


def _fnRewriteConftest(
    connectionDocker, sContainerId, sStepDirectory,
    sProjectRepoPath, sInstalledVersion,
):
    """Write a fresh conftest.py and log the version transition."""
    try:
        fnWriteConftestMarker(
            connectionDocker, sContainerId,
            sStepDirectory, sProjectRepoPath,
        )
    except Exception as exc:
        logger.error(
            "Failed to refresh conftest.py for %s: %s",
            sStepDirectory, exc,
        )
        return
    logger.info(
        "Refreshed conftest.py for %s (installed=%r -> %r)",
        sStepDirectory, sInstalledVersion or "absent",
        S_CONFTEST_VERSION,
    )


def fnMigrateFlatMarkers(
    connectionDocker, sContainerId, sProjectRepoPath, sWorkflowSlug,
):
    """Move flat-layout test markers under the per-slug subdirectory.

    Older workspaces wrote markers to
    ``<repo>/.vaibify/test_markers/<step>.json``; current workflows
    expect ``<repo>/.vaibify/test_markers/<slug>/<step>.json``. This
    helper moves any flat ``*.json`` siblings of the slug subdir into
    that subdir so stranded results re-appear in the dashboard. Safe
    to call repeatedly: when no flat markers exist it logs nothing and
    exits. Files in the slug subdir are never touched.
    """
    if not sProjectRepoPath or not sWorkflowSlug:
        return
    from .pipelineRunner import fsShellQuote
    sCommand = _fsBuildFlatMarkerMigrationCommand(
        sProjectRepoPath, sWorkflowSlug,
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    _fnLogMigrationOutcome(iExit, sOutput, sWorkflowSlug)


def _fsBuildFlatMarkerMigrationCommand(sProjectRepoPath, sWorkflowSlug):
    """Build a shell-quoted python3 command that performs the migration."""
    from .pipelineRunner import fsShellQuote
    sScript = (
        "import json, os, shutil, sys\n"
        "sRepo, sSlug = sys.argv[1], sys.argv[2]\n"
        "sBase = os.path.join(sRepo, '.vaibify', 'test_markers')\n"
        "if not os.path.isdir(sBase):\n"
        "    print(json.dumps({'iMoved': 0, 'listMoved': []}))\n"
        "    sys.exit(0)\n"
        "sDest = os.path.join(sBase, sSlug)\n"
        "listMoved = []\n"
        "for sEntry in os.listdir(sBase):\n"
        "    sFlatPath = os.path.join(sBase, sEntry)\n"
        "    if not os.path.isfile(sFlatPath):\n"
        "        continue\n"
        "    if not sEntry.endswith('.json'):\n"
        "        continue\n"
        "    os.makedirs(sDest, exist_ok=True)\n"
        "    sTarget = os.path.join(sDest, sEntry)\n"
        "    if os.path.exists(sTarget):\n"
        "        os.remove(sFlatPath)\n"
        "    else:\n"
        "        shutil.move(sFlatPath, sTarget)\n"
        "    listMoved.append(sEntry)\n"
        "print(json.dumps({"
        "'iMoved': len(listMoved), 'listMoved': listMoved}))\n"
    )
    return (
        "python3 -c " + fsShellQuote(sScript) + " "
        + fsShellQuote(sProjectRepoPath) + " "
        + fsShellQuote(sWorkflowSlug)
    )


def _fnLogMigrationOutcome(iExit, sOutput, sWorkflowSlug):
    """Log INFO when files migrated, DEBUG-quiet otherwise."""
    import json as _json
    if iExit != 0:
        logger.warning(
            "Flat marker migration exited %d (slug=%r): %s",
            iExit, sWorkflowSlug, (sOutput or "").strip(),
        )
        return
    try:
        dictResult = _json.loads((sOutput or "").strip() or "{}")
    except (ValueError, _json.JSONDecodeError):
        return
    if dictResult.get("iMoved", 0) > 0:
        logger.info(
            "Migrated %d flat test markers into slug=%r: %s",
            dictResult["iMoved"], sWorkflowSlug,
            dictResult.get("listMoved", []),
        )


_S_CONFTEST_PROLOGUE_FORMAT = (
    "from pathlib import Path\n"
    "_PROJECT_REPO = Path({sProjectRepoPath!r})\n"
    "_MARKER_BASE = _PROJECT_REPO / '.vaibify' / 'test_markers'\n"
    "_WORKFLOWS_DIR = _PROJECT_REPO / '.vaibify' / 'workflows'\n"
)


_CONFTEST_MARKER_TEMPLATE = '''\
"""Vaibify test result marker plugin.

Auto-generated by vaibify. Do not remove.
Writes a JSON result marker after every pytest session so the
dashboard can detect test outcomes regardless of how pytest was invoked.
Records git blob SHA1 digests of the step's output files so a fresh
clone can reconstruct staleness state.
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone

_CATEGORY_MAP = {
    "test_integrity": "integrity",
    "test_qualitative": "qualitative",
    "test_quantitative": "quantitative",
}


def _fsGetCategory(sNodeId):
    """Map a test node ID to a category name."""
    for sPrefix, sCategory in _CATEGORY_MAP.items():
        if sPrefix in sNodeId:
            return sCategory
    return "other"


def _fdictBuildCategoryResults(session):
    """Tally pass/fail counts per test category from session items."""
    dictCategories = {}
    for item in session.items:
        sCategory = _fsGetCategory(item.nodeid)
        dictCat = dictCategories.setdefault(
            sCategory, {"iPassed": 0, "iFailed": 0}
        )
        if not hasattr(item, "rep_call") or item.rep_call is None:
            continue
        if item.rep_call.passed:
            dictCat["iPassed"] += 1
        elif item.rep_call.failed:
            dictCat["iFailed"] += 1
    return dictCategories


def _fsStepDirRepoRel(sDir):
    """Return a step directory as a posix path relative to the project repo.

    Accepts container-absolute paths whose prefix matches
    ``_PROJECT_REPO`` (e.g. ``/workspace/GJ1132_XUV/step1``), legacy
    workspace-rooted paths, or already-relative paths. Produces a
    normalized repo-relative posix string so the workflow's
    repo-relative entries and the live ``__file__``-derived directory
    compare on equal footing.
    """
    if not sDir:
        return ""
    sNorm = os.path.normpath(sDir)
    sRepo = os.path.normpath(str(_PROJECT_REPO))
    if sNorm == sRepo:
        return ""
    if sNorm.startswith(sRepo + os.sep):
        sNorm = sNorm[len(sRepo) + 1:]
    elif sNorm.startswith("/"):
        sNorm = sNorm.lstrip("/")
    return sNorm.replace(os.sep, "/")


def _fsRepoRelFromFile(sFile, sStepDirRel):
    """Return a workflow's file entry as a repo-relative posix path."""
    sFilePosix = sFile.replace(os.sep, "/")
    sRepoPosix = str(_PROJECT_REPO).replace(os.sep, "/")
    if sFilePosix.startswith(sRepoPosix + "/"):
        return sFilePosix[len(sRepoPosix) + 1:]
    if sFilePosix.startswith("/"):
        return sFilePosix.lstrip("/")
    if sStepDirRel:
        sJoined = sStepDirRel + "/" + sFilePosix
    else:
        sJoined = sFilePosix
    return os.path.normpath(sJoined).replace(os.sep, "/")


def _flistStepOutputFiles(sStepDir):
    """Return repo-relative posix paths of the step's output files.

    Reads every workflow JSON under ``.vaibify/workflows`` and
    collects files from the step whose directory (repo-relative)
    matches sStepDir's repo-relative form. Duplicates are removed
    while order is preserved.
    """
    listResult = []
    setSeen = set()
    sWantedRel = _fsStepDirRepoRel(sStepDir)
    if not _WORKFLOWS_DIR.is_dir():
        return listResult
    for pathJson in sorted(_WORKFLOWS_DIR.glob("*.json")):
        try:
            dictWorkflow = json.loads(pathJson.read_text())
        except (OSError, ValueError):
            continue
        for dictStep in dictWorkflow.get("listSteps", []):
            sCandidateRel = _fsStepDirRepoRel(
                dictStep.get("sDirectory", "")
            )
            if sCandidateRel != sWantedRel:
                continue
            for sKey in ("saDataFiles", "saPlotFiles"):
                for sFile in dictStep.get(sKey, []):
                    if "{" in sFile:
                        continue
                    sRel = _fsRepoRelFromFile(sFile, sWantedRel)
                    if sRel and sRel not in setSeen:
                        listResult.append(sRel)
                        setSeen.add(sRel)
    return listResult


def _fsBlobSha(sHostPath):
    """Return the git-blob SHA1 for a file, or empty string on error."""
    try:
        with open(sHostPath, "rb") as handle:
            baContent = handle.read()
    except OSError:
        return ""
    sHeader = "blob " + str(len(baContent)) + chr(0)
    hasher = hashlib.sha1()
    hasher.update(sHeader.encode("utf-8"))
    hasher.update(baContent)
    return hasher.hexdigest()


def _fdictComputeOutputHashes(sStepDir):
    """Return {repo-rel-path: blob-sha} for the step's output files."""
    dictHashes = {}
    for sRel in _flistStepOutputFiles(sStepDir):
        sAbs = str(_PROJECT_REPO / sRel)
        sSha = _fsBlobSha(sAbs)
        if sSha:
            dictHashes[sRel] = sSha
    return dictHashes


def _fsLabelForStep(sStepDirRel):
    """Return a display label (A09, I01) for a step by repo-rel dir.

    Scans workflow JSONs for a step whose directory matches and
    computes the label from its position among same-type steps.
    Returns an empty string when no match exists.
    """
    if not _WORKFLOWS_DIR.is_dir():
        return ""
    for pathJson in sorted(_WORKFLOWS_DIR.glob("*.json")):
        try:
            dictWorkflow = json.loads(pathJson.read_text())
        except (OSError, ValueError):
            continue
        sLabel = _fsLabelWithinWorkflow(dictWorkflow, sStepDirRel)
        if sLabel:
            return sLabel
    return ""


def _fsLabelWithinWorkflow(dictWorkflow, sStepDirRel):
    """Return sLabel for sStepDirRel within one workflow, or empty."""
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex, dictStep in enumerate(listSteps):
        sCandidate = _fsStepDirRepoRel(dictStep.get("sDirectory", ""))
        if sCandidate != sStepDirRel:
            continue
        bInteractive = dictStep.get("bInteractive", False)
        sPrefix = "I" if bInteractive else "A"
        iCount = sum(
            1 for j in range(iIndex + 1)
            if listSteps[j].get("bInteractive", False) == bInteractive
        )
        return "{}{:02d}".format(sPrefix, iCount)
    return ""


def _fsActiveWorkflowSlug():
    """Return the slug subdir markers go into for this pytest run.

    Reads VAIBIFY_ACTIVE_WORKFLOW_SLUG (set by the pipeline runner).
    Falls back to the first workflow JSON in _WORKFLOWS_DIR for manual
    pytest invocations in single-workflow repos. Returns "default"
    only when both inputs are absent — the conftest must always pick
    some non-empty slug so writes don't escape the namespace.
    """
    sSlug = os.environ.get("VAIBIFY_ACTIVE_WORKFLOW_SLUG", "").strip()
    if sSlug:
        return sSlug
    if _WORKFLOWS_DIR.is_dir():
        for pathJson in sorted(_WORKFLOWS_DIR.glob("*.json")):
            return pathJson.stem
    return "default"


def pytest_sessionfinish(session, exitstatus):
    """Write a JSON marker after every pytest run."""
    sStepDir = str(Path(__file__).resolve().parent.parent)
    sStepDirRel = _fsStepDirRepoRel(sStepDir)
    fNow = time.time()
    dictMarker = {
        "sDirectory": sStepDirRel,
        "sLabel": _fsLabelForStep(sStepDirRel),
        "iExitStatus": exitstatus,
        "fTimestamp": fNow,
        "sRunAtUtc": datetime.fromtimestamp(
            fNow, tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "iCollected": session.testscollected,
        "dictCategories": _fdictBuildCategoryResults(session),
        "dictOutputHashes": _fdictComputeOutputHashes(sStepDir),
    }
    sMarkerDir = _MARKER_BASE / _fsActiveWorkflowSlug()
    sMarkerDir.mkdir(parents=True, exist_ok=True)
    sFilename = sStepDirRel.replace("/", "_") + ".json"
    (sMarkerDir / sFilename).write_text(
        json.dumps(dictMarker, indent=2)
    )


import pytest

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store the call report on the item for sessionfinish access."""
    outcome = yield
    if call.when == "call":
        item.rep_call = outcome.get_result()
'''
