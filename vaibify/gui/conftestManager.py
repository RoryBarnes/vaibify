"""Manage conftest.py marker plugin and tests directory in containers."""

__all__ = [
    "S_CONFTEST_VERSION",
    "fsConftestPath",
    "fsConftestContent",
    "fsBuildConftestSource",
    "fsReadInstalledConftestVersion",
    "fdictReadInstalledConftestVersions",
    "fnWriteConftestMarker",
    "fnWriteConftestMarkersBatch",
    "fnEnsureTestsDirectory",
    "fnEnsureConftestsCurrent",
    "fnMigrateFlatMarkers",
]

import json
import logging
import posixpath
import re
from collections import OrderedDict


logger = logging.getLogger("vaibify")


# Upper bound on entries kept in the per-process de-dup caches below.
# Each entry is a small tuple of strings, so 256 caps memory at well
# under a kilobyte while still covering the realistic working set of
# (container, project-repo, version/slug) keys a single host process
# touches between restarts.
I_REFRESH_CACHE_MAX_ENTRIES = 256


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


# Switch-time de-dup: connect runs ``_fnRefreshConftestsAndMigrateMarkers``
# once, then the first poll runs it again. Both calls are idempotent
# from the container's perspective, but each was paying ~5–15 s on a
# 100-step workflow. These process-local caches make the second call a
# no-op so a fresh switch pays the refresh cost once, not twice.
#
# Backed by ``OrderedDict`` used as an ordered set with a hard FIFO
# cap (``I_REFRESH_CACHE_MAX_ENTRIES``). A plain ``set`` grew without
# bound across multi-week host uptimes — every new
# (container, project-repo, version/slug) triple landed and never left.
# Caches invalidate on process restart (which is when
# ``S_CONFTEST_VERSION`` bumps land via a vaibify reload).
_SET_REFRESHED_KEYS = OrderedDict()
_SET_MIGRATED_FLAT_KEYS = OrderedDict()


def fnClearRefreshCaches():
    """Clear the per-process refresh + migration caches (test helper)."""
    _SET_REFRESHED_KEYS.clear()
    _SET_MIGRATED_FLAT_KEYS.clear()


def _fnRememberRefreshKey(orderedCache, tKey):
    """Mark ``tKey`` as recently seen, evicting the oldest if over cap.

    ``OrderedDict`` preserves insertion order; ``move_to_end`` on a
    re-add keeps the most recently touched key at the tail, so
    ``popitem(last=False)`` evicts the actually-oldest entry instead
    of one that has been recently re-touched.
    """
    if tKey in orderedCache:
        orderedCache.move_to_end(tKey)
        return
    orderedCache[tKey] = None
    while len(orderedCache) > I_REFRESH_CACHE_MAX_ENTRIES:
        orderedCache.popitem(last=False)


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


def fdictReadInstalledConftestVersions(
    connectionDocker, sContainerId, listConftestPaths,
):
    """Probe ``# vaibify-conftest-version:`` stamps in one docker exec.

    Returns ``{sPath: sVersion}`` for every conftest that was readable
    and carried the sentinel. Paths that are missing, unreadable, or
    lack the sentinel are omitted — the refresh helper treats absence
    the same as "needs rewriting". One batched probe keeps switch-time
    flat at N=100 steps; the single-file
    ``fsReadInstalledConftestVersion`` stays available for callers
    (and tests) that probe one path at a time.
    """
    if not listConftestPaths:
        return {}
    sCommand = _fsBuildVersionsProbeCommand(listConftestPaths)
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return {}
    return _fdictParseVersionsProbeOutput(sOutput)


def _fsBuildVersionsProbeCommand(listConftestPaths):
    """Build a single ``python3 -c`` command that reads many conftest files."""
    from .pipelineRunner import fsShellQuote
    sPathsJson = json.dumps(list(listConftestPaths))
    sScript = (
        "import json, re, sys\n"
        "rx = re.compile("
        "r'^# vaibify-conftest-version:\\s*(\\S+)\\s*$', re.M)\n"
        "out = {}\n"
        "for p in json.loads(sys.stdin.read()):\n"
        "    try:\n"
        "        with open(p, 'r', encoding='utf-8') as f: s = f.read()\n"
        "    except OSError:\n"
        "        continue\n"
        "    m = rx.search(s)\n"
        "    if m:\n"
        "        out[p] = m.group(1)\n"
        "print(json.dumps(out))\n"
    )
    return (
        "python3 -c " + fsShellQuote(sScript)
        + " <<< " + fsShellQuote(sPathsJson)
    )


def _fdictParseVersionsProbeOutput(sOutput):
    """Return the trailing JSON dict from the probe stdout, or empty."""
    try:
        dictLoaded = json.loads(
            (sOutput or "").strip().splitlines()[-1]
        )
    except (ValueError, IndexError):
        return {}
    if not isinstance(dictLoaded, dict):
        return {}
    return dictLoaded


def fnWriteConftestMarkersBatch(
    connectionDocker, sContainerId, listConftestPaths, sContent,
):
    """Write the same conftest source to every path in a single docker exec.

    Used by ``fnEnsureConftestsCurrent`` when the version-bump rollout
    needs to rewrite many files at once — N writes collapse to one
    ``python3 -c`` invocation. Returns True on docker-exec success.
    The single-file ``fnWriteConftestMarker`` is unchanged for callers
    that target one step directory at a time.
    """
    if not listConftestPaths:
        return True
    sCommand = _fsBuildConftestBatchWriteCommand(
        listConftestPaths, sContent,
    )
    iExit, _sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    return iExit == 0


def _fsBuildConftestBatchWriteCommand(listConftestPaths, sContent):
    """Build a ``python3 -c`` command that writes sContent to every path."""
    from .pipelineRunner import fsShellQuote
    sPayload = json.dumps({
        "listPaths": list(listConftestPaths),
        "sContent": sContent,
    })
    sScript = (
        "import json, os, sys\n"
        "d = json.loads(sys.stdin.read())\n"
        "for p in d['listPaths']:\n"
        "    os.makedirs(os.path.dirname(p), exist_ok=True)\n"
        "    with open(p, 'w', encoding='utf-8') as f:\n"
        "        f.write(d['sContent'])\n"
        "print('OK')\n"
    )
    return (
        "python3 -c " + fsShellQuote(sScript)
        + " <<< " + fsShellQuote(sPayload)
    )


def fnEnsureConftestsCurrent(
    connectionDocker, sContainerId, listStepDirs, sProjectRepoPath,
):
    """Refresh stale or missing conftest.py copies in each step's tests/.

    Batches both the version probe and the rewrite into one
    ``docker exec`` each, so switch-time stays flat at ~100 steps.
    Idempotent at any N: current files are left untouched. Empty
    input short-circuits before any container work. A successful
    sweep is cached in ``_SET_REFRESHED_KEYS`` so the connect-time
    and first-poll callers no longer pay the cost twice.
    """
    if not listStepDirs:
        return
    tKey = (sContainerId, sProjectRepoPath, S_CONFTEST_VERSION)
    if tKey in _SET_REFRESHED_KEYS:
        return
    listConftestPaths = _flistConftestPathsForSteps(
        listStepDirs, sProjectRepoPath,
    )
    dictInstalled = fdictReadInstalledConftestVersions(
        connectionDocker, sContainerId, listConftestPaths,
    )
    listStale = _flistStalePaths(listConftestPaths, dictInstalled)
    if not listStale:
        _fnRememberRefreshKey(_SET_REFRESHED_KEYS, tKey)
        return
    bWritten = fnWriteConftestMarkersBatch(
        connectionDocker, sContainerId, listStale,
        fsBuildConftestSource(sProjectRepoPath),
    )
    _fnLogBatchRefreshOutcome(listStale, bWritten, dictInstalled)
    if bWritten:
        _fnRememberRefreshKey(_SET_REFRESHED_KEYS, tKey)


def _flistConftestPathsForSteps(listStepDirs, sProjectRepoPath):
    """Return the absolute conftest path for every step in input order.

    Defense-in-depth: filters out any path that, after normalization,
    does not live under ``sProjectRepoPath``. Workflow load-time
    validation already rejects ``..``-escaping ``sDirectory`` values,
    so a non-empty drop here means a refactor regressed that gate.
    """
    listResolved = [
        fsConftestPath(_fsAbsoluteStepDir(sDir, sProjectRepoPath))
        for sDir in listStepDirs
    ]
    if not sProjectRepoPath:
        return listResolved
    return _flistPathsWithinRoot(listResolved, sProjectRepoPath)


def _flistPathsWithinRoot(listPaths, sRoot):
    """Return paths whose normalized form is under sRoot. Log dropped paths."""
    sNormRoot = posixpath.normpath(sRoot)
    listKept = []
    for sPath in listPaths:
        sNorm = posixpath.normpath(sPath)
        if sNorm == sNormRoot or sNorm.startswith(sNormRoot + "/"):
            listKept.append(sPath)
        else:
            logging.warning(
                "conftestManager: dropped path outside project repo: %s", sPath,
            )
    return listKept


def _flistStalePaths(listConftestPaths, dictInstalled):
    """Return paths whose installed version does not match the current stamp."""
    return [
        sPath for sPath in listConftestPaths
        if dictInstalled.get(sPath) != S_CONFTEST_VERSION
    ]


def _fnLogBatchRefreshOutcome(listStale, bWritten, dictInstalled):
    """Log per-path transitions after a batched conftest rewrite."""
    if not bWritten:
        logger.error(
            "Batch conftest rewrite failed for %d paths", len(listStale),
        )
        return
    for sPath in listStale:
        sInstalled = dictInstalled.get(sPath, "")
        logger.info(
            "Refreshed conftest.py at %s (installed=%r -> %r)",
            sPath, sInstalled or "absent", S_CONFTEST_VERSION,
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
    exits. Files in the slug subdir are never touched. Cached in
    ``_SET_MIGRATED_FLAT_KEYS`` so the connect-time and first-poll
    callers do not both pay the migration cost.
    """
    if not sProjectRepoPath or not sWorkflowSlug:
        return
    tKey = (sContainerId, sProjectRepoPath, sWorkflowSlug)
    if tKey in _SET_MIGRATED_FLAT_KEYS:
        return
    from .pipelineRunner import fsShellQuote
    sCommand = _fsBuildFlatMarkerMigrationCommand(
        sProjectRepoPath, sWorkflowSlug,
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    _fnLogMigrationOutcome(iExit, sOutput, sWorkflowSlug)
    if iExit == 0:
        _fnRememberRefreshKey(_SET_MIGRATED_FLAT_KEYS, tKey)


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
    "_PROJECTS_DIR = _PROJECT_REPO / '.vaibify' / 'projects'\n"
    "_WORKFLOWS_DIR = _PROJECT_REPO / '.vaibify' / 'workflows'\n"
    "def _flistProjectJsons():\n"
    "    listFiles = []\n"
    "    for _sDir in (_PROJECTS_DIR, _WORKFLOWS_DIR):\n"
    "        if _sDir.is_dir():\n"
    "            listFiles.extend(sorted(_sDir.glob('*.json')))\n"
    "    return listFiles\n"
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
    for pathJson in _flistProjectJsons():
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
    for pathJson in _flistProjectJsons():
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
    for pathJson in _flistProjectJsons():
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
