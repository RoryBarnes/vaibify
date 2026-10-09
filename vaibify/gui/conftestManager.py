"""Manage conftest.py marker plugin and tests directory in containers."""

__all__ = [
    "S_CONFTEST_VERSION",
    "fsConftestPath",
    "fsConftestContent",
    "fsBuildConftestSource",
    "fsReadInstalledConftestVersion",
    "fdictReadInstalledConftestVersions",
    "fnWriteConftestMarker",
    "fbWriteConftestMarkersBatch",
    "fnEnsureTestsDirectory",
    "fnEnsureConftestsCurrent",
    "flistRefreshConftestsForRun",
    "fnMigrateFlatMarkers",
]

import inspect
import json
import logging
import posixpath
import re
from collections import OrderedDict

from . import pipelineUtils
from . import testMarkerContract


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
S_CONFTEST_VERSION = "9"
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
        _fsVersionStampLine() + sPrologue
        + _fsTranscribeStepLabelDerivation()
        + _fsTranscribeUniqueTemporaryPath()
        + _fsTranscribeMarkerContract()
        + _CONFTEST_MARKER_TEMPLATE
    )


def _fsTranscribeMarkerContract():
    """Return the marker contract's writer half, verbatim, for the container.

    The conftest cannot import from the host, so the shape of a marker,
    the normalizer that reads an old one and the merge that adds a
    session to it are transcribed from ``testMarkerContract`` rather
    than written a second time: the container and the dashboard share
    one implementation of what a marker is, and a change to it reaches
    the container on the next conftest refresh. Appended after the
    prologue is formatted because the transcribed source contains
    format braces of its own.
    """
    try:
        listSources = [
            inspect.getsource(fnWriter)
            for fnWriter in testMarkerContract.LIST_TRANSCRIBED_WRITER_FUNCTIONS
        ]
    except (OSError, TypeError) as error:
        raise RuntimeError(
            "Cannot transcribe the test-marker contract into the "
            "container conftest because vaibify's own source is "
            f"unreadable ({error}). Install vaibify from a source "
            "distribution rather than a zipped one."
        )
    return (
        "T_CATEGORY_FILE_PREFIXES = "
        + repr(testMarkerContract.T_CATEGORY_FILE_PREFIXES) + "\n\n\n"
        + "\n\n".join(listSources) + "\n\n"
    )


def _fsTranscribeStepLabelDerivation():
    """Return the HOST label derivation's own source, for the container.

    The conftest runs inside the container, which has no vaibify
    install to import from, so the step-label rule cannot be called
    across the boundary. Transcribing
    ``pipelineUtils.fbStepIsInteractive`` and
    ``flistComputeAllStepLabels`` verbatim keeps the container's
    labels identical to the dashboard's by construction: the container
    gets a copy of the one derivation rather than a second one, and a
    change to the classifier propagates on the next conftest refresh.
    Appended after the prologue is formatted because the transcribed
    source contains format braces of its own.
    """
    try:
        sClassifier = inspect.getsource(
            pipelineUtils.fbStepIsInteractive)
        sLabeller = inspect.getsource(
            pipelineUtils.flistComputeAllStepLabels)
    except (OSError, TypeError) as error:
        raise RuntimeError(
            "Cannot transcribe the step-label derivation into the "
            "container conftest because vaibify's own source is "
            f"unreadable ({error}). Install vaibify from a source "
            "distribution rather than a zipped one."
        )
    return (
        "_T_INTERACTIVE_FALSE_TOKENS = "
        + repr(pipelineUtils._T_INTERACTIVE_FALSE_TOKENS) + "\n"
        + sClassifier + "\n" + sLabeller + "\n\n"
    )


def _fsTranscribeUniqueTemporaryPath():
    """Return the shared unique-temporary-path derivation, for the container.

    The marker lands by renaming a temporary, and two sessions can write
    one marker at once, so the temporary must be unique per writer.
    Transcribed from ``pipelineUtils`` like the label derivation, so the
    container derives its names the way the host does.
    """
    try:
        sSource = inspect.getsource(pipelineUtils.fsBuildUniqueTemporaryPath)
    except (OSError, TypeError) as error:
        raise RuntimeError(
            "Cannot transcribe the temporary-path derivation into the "
            "container conftest because vaibify's own source is "
            f"unreadable ({error}). Install vaibify from a source "
            "distribution rather than a zipped one."
        )
    return sSource + "\n\n"


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


def fbWriteConftestMarkersBatch(
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
    bWritten = fbWriteConftestMarkersBatch(
        connectionDocker, sContainerId, listStale,
        fsBuildConftestSource(sProjectRepoPath),
    )
    _fnLogBatchRefreshOutcome(listStale, bWritten, dictInstalled)
    if bWritten:
        _fnRememberRefreshKey(_SET_REFRESHED_KEYS, tKey)


def flistRefreshConftestsForRun(
    connectionDocker, sContainerId, listStepDirs, sProjectRepoPath,
):
    """Re-probe and refresh conftests for a run; return warning lines.

    Deliberately does NOT consult ``_SET_REFRESHED_KEYS``.  That cache
    is keyed by ``(container, repo, version)`` and lives for the hub's
    whole process, so once a sweep has run, anything that rewrites the
    files underneath it -- a ``git pull``, a checkout, a branch switch
    -- is never noticed again, and reopening the project re-probes
    nothing.  That is not hypothetical: a researcher pulled a repo
    carrying committed generation-5 conftests, every one of which
    stamps a container path, and every test tier of every step then
    reported ``exit 1`` while pytest printed ``1 passed`` directly
    above it (2026-09-04).  A run is the moment the file's CONTENT
    starts to matter, and one batched version probe is negligible
    beside the run it precedes.

    Returns researcher-facing warnings rather than raising: a
    bookkeeping file that cannot be rewritten must not block a
    scientific run, but silence about it has already cost an
    afternoon of diagnosis.
    """
    if not listStepDirs or not sProjectRepoPath:
        return []
    listConftestPaths = _flistConftestPathsForSteps(
        listStepDirs, sProjectRepoPath,
    )
    dictInstalled = fdictReadInstalledConftestVersions(
        connectionDocker, sContainerId, listConftestPaths,
    )
    listStale = _flistStalePaths(listConftestPaths, dictInstalled)
    if not listStale:
        return []
    bWritten = fbWriteConftestMarkersBatch(
        connectionDocker, sContainerId, listStale,
        fsBuildConftestSource(sProjectRepoPath),
    )
    _fnLogBatchRefreshOutcome(listStale, bWritten, dictInstalled)
    if bWritten:
        _fnRememberRefreshKey(
            _SET_REFRESHED_KEYS,
            (sContainerId, sProjectRepoPath, S_CONFTEST_VERSION),
        )
        return []
    return [_fsDescribeRefreshFailure(listStale, dictInstalled)]


def _fsDescribeRefreshFailure(listStale, dictInstalled):
    """Return one warning line naming the stale conftests and the risk."""
    listDescribed = [
        posixpath.relpath(sPath) if not posixpath.isabs(sPath) else sPath
        for sPath in listStale
    ]
    return (
        "Could not update "
        + str(len(listStale))
        + " test-support file(s) to conftest generation "
        + S_CONFTEST_VERSION
        + " ("
        + ", ".join(listDescribed)
        + "). An out-of-date conftest can fail a step whose tests "
        "all passed, because it writes vaibify's test marker after "
        "the tests finish. Installed generations: "
        + ", ".join(
            sorted({
                dictInstalled.get(sPath) or "absent"
                for sPath in listStale
            })
        )
        + "."
    )


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
    "_STAMPED_PROJECT_REPO = Path({sProjectRepoPath!r})\n"
    "def _fpathLocateProjectRepo():\n"
    "    sSelf = globals().get('__file__') or ''\n"
    "    if sSelf:\n"
    "        for pathAncestor in Path(sSelf).resolve().parents:\n"
    "            if (pathAncestor / '.vaibify').is_dir():\n"
    "                return pathAncestor\n"
    "    return _STAMPED_PROJECT_REPO\n"
    "_PROJECT_REPO = _fpathLocateProjectRepo()\n"
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
import uuid
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # no file locking on this platform
    fcntl = None

# What THIS session has seen so far, filled by the hooks at the bottom.
# The run id is minted once, when pytest first imports this file.
_DICT_SESSION_STATE = {
    "sRunId": uuid.uuid4().hex, "listDeselected": [],
    "listCollectionErrors": [],
}
_T_CATEGORY_NAMES = ("integrity", "qualitative", "quantitative")


def _fsOutcomeOfItem(item):
    """Return the item's own outcome, or "" when it never ran.

    Failed covers a failing call AND a setup or teardown error. A skip
    decided in setup has no call report; an xfail is a skipped call
    that carries ``wasxfail``, and a non-strict xpass is a passed call
    that does (a strict xpass already reports as failed).
    """
    repSetup = getattr(item, "rep_setup", None)
    repCall = getattr(item, "rep_call", None)
    repTeardown = getattr(item, "rep_teardown", None)
    for rep in (repSetup, repCall, repTeardown):
        if rep is not None and getattr(rep, "failed", False):
            return "failed"
    if repCall is None:
        return "skipped" if getattr(repSetup, "skipped", False) else ""
    if getattr(repCall, "skipped", False):
        return "xfailed" if hasattr(repCall, "wasxfail") else "skipped"
    if getattr(repCall, "passed", False):
        return "xpassed" if hasattr(repCall, "wasxfail") else "passed"
    return ""


def _fsStepDirRepoRel(sDir):
    """Return a step directory as a posix path relative to the project repo.

    Accepts container-absolute paths whose prefix matches
    ``_PROJECT_REPO`` (e.g. ``/workspace/ProjectRepo/step1``), legacy
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
            for sKey in ("saOutputDataFiles", "saPlotFiles"):
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


def _flistStepInputFiles(sStepDir):
    """Return repo-relative posix paths of the step's input data files.

    Unlike output entries, ``saInputDataFiles`` values are already
    repo-relative — they are never joined onto the step directory.
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
            for sFile in dictStep.get("saInputDataFiles", []):
                if "{" in sFile:
                    continue
                sRel = _fsRepoRelFromFile(sFile, "")
                if sRel and sRel not in setSeen:
                    listResult.append(sRel)
                    setSeen.add(sRel)
    return listResult


def _fdictComputeInputHashes(sStepDir):
    """Return {repo-rel-path: blob-sha} for the step's input data files."""
    dictHashes = {}
    for sRel in _flistStepInputFiles(sStepDir):
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
    """Return sLabel for sStepDirRel within one workflow, or empty.

    Labels come from the transcribed host derivation
    (flistComputeAllStepLabels), never from an inline count, so a
    marker written in the container names the same step the dashboard
    names.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    listLabels = flistComputeAllStepLabels(listSteps)
    for iIndex, dictStep in enumerate(listSteps):
        sCandidate = _fsStepDirRepoRel(dictStep.get("sDirectory", ""))
        if sCandidate == sStepDirRel:
            return listLabels[iIndex]
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


def _fsTestsDirectory():
    """Return the absolute path of this conftest's tests directory."""
    return str(Path(__file__).resolve().parent)


def _fsTestFileKey(sAbsolutePath, sTestsDir):
    """Return a test file's key (its path under tests/), or "" outside it."""
    sRelative = os.path.relpath(sAbsolutePath, sTestsDir)
    if sRelative.startswith(".."):
        return ""
    return sRelative.replace(os.sep, "/")


def _fsAbsoluteTestPath(item, sTestsDir):
    """Return the item's test file, falling back to its node id's name."""
    pathItem = getattr(item, "path", None) or getattr(item, "fspath", None)
    if pathItem:
        return os.path.abspath(str(pathItem))
    sFileName = os.path.basename(item.nodeid.split("::", 1)[0])
    return os.path.join(sTestsDir, sFileName)


def _fsNodeIdRelativeToStep(item, sStepDir, sTestsDir):
    """Name a test by its path under the step directory, not pytest's rootdir.

    Pytest roots node ids wherever it decides the rootdir is, which
    depends on the arguments it was given; the step directory does not
    move, so the same test has the same name in every session.
    """
    sFile = os.path.relpath(_fsAbsoluteTestPath(item, sTestsDir), sStepDir)
    sRest = item.nodeid.split("::", 1)[1] if "::" in item.nodeid else ""
    return sFile.replace(os.sep, "/") + "::" + sRest


def _fsetFilesNamedByNodeId(session):
    """Return the absolute paths of files given as ``file.py::test`` arguments.

    Such a file was collected one test at a time, so its test list is
    not the file's.
    """
    config = getattr(session, "config", None)
    paramsInvocation = getattr(config, "invocation_params", None)
    if paramsInvocation is None:
        return set()
    setFiles = set()
    for objArgument in paramsInvocation.args:
        sArgument = str(objArgument)
        if "::" in sArgument and not sArgument.startswith("-"):
            setFiles.add(os.path.abspath(os.path.join(
                str(paramsInvocation.dir), sArgument.split("::", 1)[0])))
    return setFiles


def _fdictEmptyFileResult(bWholeFile):
    return {
        "bWholeFile": bWholeFile, "listNodeIds": [], "dictOutcomes": {},
        "dictCollectionError": None,
    }


def _fnRecordItem(dictFileResults, item, tPaths, setNodeIdFiles, bSelected):
    """Add one selected or deselected item to its file's result."""
    sStepDir, sTestsDir = tPaths
    sAbsolute = _fsAbsoluteTestPath(item, sTestsDir)
    sFileKey = _fsTestFileKey(sAbsolute, sTestsDir)
    if not sFileKey:
        return
    dictFile = dictFileResults.setdefault(
        sFileKey, _fdictEmptyFileResult(sAbsolute not in setNodeIdFiles))
    sNodeId = _fsNodeIdRelativeToStep(item, sStepDir, sTestsDir)
    if sNodeId not in dictFile["listNodeIds"]:
        dictFile["listNodeIds"].append(sNodeId)
    sOutcome = _fsOutcomeOfItem(item) if bSelected else ""
    if sOutcome:
        dictFile["dictOutcomes"][sNodeId] = sOutcome


def _fdictFileResultsOfSession(session, tPaths):
    """Return ``{file: result}`` for every test file this session touched."""
    setNodeIdFiles = _fsetFilesNamedByNodeId(session)
    dictFileResults = {}
    for item in getattr(session, "items", []):
        _fnRecordItem(dictFileResults, item, tPaths, setNodeIdFiles, True)
    for item in _DICT_SESSION_STATE["listDeselected"]:
        _fnRecordItem(dictFileResults, item, tPaths, setNodeIdFiles, False)
    return dictFileResults


def _fdictCollectionErrorRecord(sMessage, fNow):
    return {
        "sRunId": _DICT_SESSION_STATE["sRunId"], "fTimestamp": fNow,
        "sMessage": sMessage,
    }


def _flistPresentTestFiles(sTestsDir):
    """Return the ``test_*.py`` files directly in tests/, as the dashboard lists."""
    try:
        listNames = os.listdir(sTestsDir)
    except OSError:
        return []
    return sorted(
        sName for sName in listNames
        if sName.startswith("test_") and sName.endswith(".py"))


def _fsAuthoritativeCategory():
    """Return the category the dashboard named for this session, or "".

    The dashboard exports it from a fixed set; a value outside that set
    names nothing and is ignored, so no user text is ever believed.
    """
    sCategory = os.environ.get("VAIBIFY_TEST_CATEGORY", "").strip()
    return sCategory if sCategory in _T_CATEGORY_NAMES else ""


def _flistFilesBlamedFor(sFileKey, listPresentFiles):
    """Return the test files a collection error is charged to.

    The file itself when its name declares a category. Otherwise EVERY
    file of the category the dashboard named for the session, when it
    named one; with neither, no category can be blamed and the list is
    empty.
    """
    sCategory = fsCategoryOfFileName(sFileKey) if sFileKey else "other"
    if sCategory in _T_CATEGORY_NAMES:
        return [sFileKey]
    sNamed = _fsAuthoritativeCategory()
    listBlamed = [
        sFile for sFile in listPresentFiles
        if sNamed and fsCategoryOfFileName(sFile) == sNamed]
    return listBlamed


def _fnAttributeCollectionErrors(dictFileResults, listPresentFiles, fNow):
    """Record each collection error against the files blamed; return orphans.

    A file's error is always recorded on the file itself; an error no
    category can be blamed for is returned so the session can say it
    failed with no one to blame.
    """
    listOrphans = []
    for sFileKey, sMessage in _DICT_SESSION_STATE["listCollectionErrors"]:
        dictError = _fdictCollectionErrorRecord(sMessage, fNow)
        listBlamed = _flistFilesBlamedFor(sFileKey, listPresentFiles)
        for sFile in listBlamed + ([sFileKey] if sFileKey else []):
            dictFileResults.setdefault(
                sFile, _fdictEmptyFileResult(False)
            )["dictCollectionError"] = dictError
        if not listBlamed:
            listOrphans.append(sMessage)
    return listOrphans


def _fbSessionLeftAResult(dictFileResults, listOrphans):
    """Return True iff some test ran, some file failed to collect, or a
    failure had no one to blame: anything a marker could say."""
    return bool(listOrphans) or any(
        dictFile["dictOutcomes"] or dictFile["dictCollectionError"]
        for dictFile in dictFileResults.values())


def _fdictUnattributedFailure(exitstatus, dictFileResults, listOrphans, fNow):
    """Return the failure no category owns, or None.

    A collection error nobody can be blamed for, or a session that
    stopped (interrupted, internal or usage error) with no result at all.
    Exit 0 and 1 are results, and exit 5 collected nothing.
    """
    if listOrphans:
        sMessage = listOrphans[0]
    elif exitstatus in (2, 3, 4) and not _fbSessionLeftAResult(
        dictFileResults, listOrphans
    ):
        sMessage = "pytest stopped with exit status " + str(exitstatus)
    else:
        return None
    return {
        "sRunId": _DICT_SESSION_STATE["sRunId"], "fTimestamp": fNow,
        "iExitStatus": exitstatus, "sMessage": sMessage,
    }


def _fdictBuildSession(session, exitstatus):
    """Compose this session's record, or None when it left nothing to record.

    The files it calls present are the dashboard's ``test_*.py`` files
    plus every file the session ran: a file run by path, whatever it is
    called, is a result the marker must hold.
    """
    sTestsDir = _fsTestsDirectory()
    sStepDir = str(Path(sTestsDir).parent)
    sStepDirRel = _fsStepDirRepoRel(sStepDir)
    fNow = time.time()
    listPresent = _flistPresentTestFiles(sTestsDir)
    dictFileResults = _fdictFileResultsOfSession(
        session, (sStepDir, sTestsDir))
    listOrphans = _fnAttributeCollectionErrors(
        dictFileResults, listPresent, fNow)
    if exitstatus == 5 and not _fbSessionLeftAResult(
        dictFileResults, listOrphans
    ):
        return None
    return {
        "sRunId": _DICT_SESSION_STATE["sRunId"], "fTimestamp": fNow,
        "sRunAtUtc": datetime.fromtimestamp(
            fNow, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "iExitStatus": exitstatus, "sDirectory": sStepDirRel,
        "sLabel": _fsLabelForStep(sStepDirRel),
        "dictOutputHashes": _fdictComputeOutputHashes(sStepDir),
        "dictInputHashes": _fdictComputeInputHashes(sStepDir),
        "listPresentFiles": sorted(set(listPresent) | set(dictFileResults)),
        "dictFileResults": dictFileResults,
        "dictUnattributedFailure": _fdictUnattributedFailure(
            exitstatus, dictFileResults, listOrphans, fNow),
    }


def _fdictReadExistingMarker(sMarkerPath):
    """Return the marker on disk, or None when absent or unreadable."""
    try:
        with open(sMarkerPath) as fileMarker:
            return json.load(fileMarker)
    except (OSError, ValueError):
        return None


def _fnWriteMergedMarker(sMarkerPath, dictSession):
    """Merge the session into the marker under a lock, replacing it atomically.

    The dashboard runs one session per category and may run them
    together, so the read-modify-write holds an exclusive lock on a
    sibling lock file and the new marker lands by rename: two sessions
    both survive, and a reader never meets a half-written file.
    """
    os.makedirs(os.path.dirname(sMarkerPath), exist_ok=True)
    with open(sMarkerPath + ".lock", "a") as fileLock:
        if fcntl is not None:
            fcntl.flock(fileLock.fileno(), fcntl.LOCK_EX)
        dictMerged = fdictMergeSessionIntoMarker(
            _fdictReadExistingMarker(sMarkerPath), dictSession)
        sTempPath = fsBuildUniqueTemporaryPath(sMarkerPath)
        with open(sTempPath, "w") as fileTemp:
            json.dump(dictMerged, fileTemp, indent=2)
        os.replace(sTempPath, sMarkerPath)


def _fnWriteSessionMarker(session, exitstatus):
    """Compose this session's record and merge it under _MARKER_BASE."""
    dictSession = _fdictBuildSession(session, exitstatus)
    if dictSession is None:
        return
    sFilename = dictSession["sDirectory"].replace("/", "_") + ".json"
    sMarkerPath = str(_MARKER_BASE / _fsActiveWorkflowSlug() / sFilename)
    _fnWriteMergedMarker(sMarkerPath, dictSession)


def pytest_sessionfinish(session, exitstatus):
    """Write a JSON marker after every pytest run, or say why not.

    Writing the marker can NEVER fail the session. The marker is
    vaibify's bookkeeping, not a scientific result, and an
    unwritable marker directory once turned a fully passing suite
    into ``exit 1`` -- which reads on screen as the researcher's
    own tests failing, because pytest had already printed
    "1 passed" above it. The cause is printed instead, so a step
    whose test status goes unknown says why.
    """
    try:
        _fnWriteSessionMarker(session, exitstatus)
    except Exception as error:
        print(
            "vaibify: could not write the test-result marker under "
            + str(_MARKER_BASE) + ": " + repr(error)
        )
        print(
            "vaibify: the test results above stand; this step's "
            "test status will show as unknown until the marker "
            "directory is writable."
        )


def pytest_deselected(items):
    """Remember tests a ``-k`` or ``-m`` expression left out of this run.

    They still belong to their file's test list.
    """
    _DICT_SESSION_STATE["listDeselected"].extend(items)


def pytest_collectreport(report):
    """Remember a file that failed to collect, with the first line of why."""
    if not getattr(report, "failed", False):
        return
    sMessage = str(getattr(report, "longrepr", "") or "collection failed")
    sFirstLine = (sMessage.strip().splitlines() or ["collection failed"])[-1]
    sTestsDir = _fsTestsDirectory()
    sNodePath = str(getattr(report, "nodeid", "")).split("::", 1)[0]
    sFileKey = _fsTestFileKey(
        os.path.abspath(os.path.join(sTestsDir, os.path.basename(sNodePath))),
        sTestsDir) if sNodePath.endswith(".py") else ""
    _DICT_SESSION_STATE["listCollectionErrors"].append(
        (sFileKey, sFirstLine[:300]))


import pytest

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store each phase's report on the item for sessionfinish access."""
    outcome = yield
    setattr(item, "rep_" + call.when, outcome.get_result())
'''
