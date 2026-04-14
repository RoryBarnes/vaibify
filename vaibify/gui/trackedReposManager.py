"""Tracked repositories sidecar manager.

Persists which git repositories in /workspace should appear in the
future Repos panel. The sidecar lives at
/workspace/.vaibify/tracked_repos.json inside the container and
contains two disjoint lists: listTracked and listIgnored.

Two-lists invariant
-------------------
vaibify.yml:repositories is the BUILD-TIME list (what entrypoint.sh
clones on container start, authoritative across rebuilds).
tracked_repos.json:listTracked is the RUNTIME list (what the GUI
surfaces right now, persists across container restarts but not
rebuilds since /workspace is volume-persistent). They are kept
consistent at well-defined moments: (1) on container start every
entry in vaibify.yml:repositories is auto-tracked idempotently;
(2) on user "Track" action, the entry is added to listTracked only
and NOT written back to vaibify.yml; (3) on rebuild, vaibify.yml is
the authoritative seed and previously-tracked repos that are no
longer present render as bMissing in the panel.

Dirty detection filtering
-------------------------
The ``bDirty`` field in repo status reflects whether the user has made
source-level changes, not whether the working tree is byte-identical to
HEAD.  Build and install artifacts produced by package managers (pip,
make, R, Julia, LaTeX, DVC) are filtered out before the dirty check.
The complete list of filtered patterns is in
``_FROZENSET_ARTIFACT_PATTERNS``.  This prevents false positives when
repos are freshly cloned and installed by the container entrypoint.

Paths that are deliberately NOT filtered (because changes to them are
meaningful): Manifest.toml, *.dvc, *.pdf, man/*.Rd, .coverage, htmlcov/.

This is a leaf module: no intra-package imports, standard library
only, following the pipelineUtils.py pattern.
"""

__all__ = [
    "S_TRACKED_REPOS_PATH",
    "S_TRACKED_REPOS_DIR",
    "I_SCHEMA_VERSION",
    "fdictReadSidecar",
    "fnWriteSidecar",
    "fdictBuildInitialState",
    "fdictReadOrSeedSidecar",
    "flistDiscoverGitDirs",
    "fdictComputeRepoStatus",
    "fnAddTracked",
    "fnAddIgnored",
    "fnRemoveTracked",
    "fnUnignore",
    "fbIsTracked",
    "fbIsIgnored",
    "flistGetTrackedNames",
    "fbIsArtifactPath",
    "fsFilterArtifacts",
    "flistBatchComputeRepoStatus",
    "FROZENSET_ARTIFACT_PATTERNS",
]

import json
import posixpath
import threading

S_TRACKED_REPOS_DIR = "/workspace/.vaibify"
S_TRACKED_REPOS_PATH = "/workspace/.vaibify/tracked_repos.json"
I_SCHEMA_VERSION = 1

_dictLocks = {}
_lockRegistry = threading.Lock()

# Suffixes of build/install artifacts to exclude from dirty detection.
_SET_ARTIFACT_SUFFIXES = frozenset([
    ".egg-info", ".egg-info/", ".pyc", ".o", ".so", ".dylib", ".a",
    ".aux", ".log", ".bbl", ".blg", ".synctex.gz", ".fls",
    ".fdb_latexmk", ".Rcheck/",
])

# Directory names that are always artifacts when they appear as a path
# component. A path is an artifact if any component matches exactly.
_SET_ARTIFACT_DIRECTORIES = frozenset([
    "__pycache__", "build", "dist", ".pytest_cache", ".Rproj.user",
])

# Exact relative paths or basenames that are always artifacts.
_SET_ARTIFACT_EXACT = frozenset([
    ".Rhistory", ".RData", "deps/build.log",
])

# Public constant for documentation and test access.
FROZENSET_ARTIFACT_PATTERNS = frozenset(
    list(_SET_ARTIFACT_SUFFIXES) +
    [d + "/" for d in _SET_ARTIFACT_DIRECTORIES] +
    list(_SET_ARTIFACT_EXACT)
)


def fbIsArtifactPath(sPath):
    """Return True if sPath is a known build/install artifact."""
    sStripped = sPath.rstrip("/")
    if not sStripped:
        return False
    if sStripped in _SET_ARTIFACT_EXACT:
        return True
    if _fbMatchesSuffix(sStripped):
        return True
    if _fbContainsArtifactDirectory(sStripped):
        return True
    return _fbMatchesArtifactPrefix(sStripped)


def _fbMatchesSuffix(sPath):
    """Return True if sPath ends with a known artifact suffix."""
    for sSuffix in _SET_ARTIFACT_SUFFIXES:
        sSuffixClean = sSuffix.rstrip("/")
        if sPath.endswith(sSuffixClean):
            return True
    return False


def _fbContainsArtifactDirectory(sPath):
    """Return True if any path component is an artifact directory."""
    listParts = sPath.replace("\\", "/").split("/")
    for sPart in listParts:
        if sPart in _SET_ARTIFACT_DIRECTORIES:
            return True
        if sPart.endswith(".egg-info"):
            return True
    return False


# Path prefixes that mark everything underneath as artifacts.
_LIST_ARTIFACT_PREFIXES = [".dvc/tmp/", ".dvc/tmp"]


def _fbMatchesArtifactPrefix(sPath):
    """Return True if sPath starts with a known artifact prefix."""
    sNormalized = sPath.replace("\\", "/")
    for sPrefix in _LIST_ARTIFACT_PREFIXES:
        if sNormalized.startswith(sPrefix):
            return True
    return False


def fsFilterArtifacts(sPorcelainOutput):
    """Remove artifact lines from git status --porcelain output."""
    if not sPorcelainOutput:
        return ""
    listFiltered = []
    for sLine in sPorcelainOutput.splitlines():
        sFilePath = _fsExtractPorcelainPath(sLine)
        if sFilePath and not fbIsArtifactPath(sFilePath):
            listFiltered.append(sLine)
    return "\n".join(listFiltered)


def _fsExtractPorcelainPath(sLine):
    """Extract the file path from a git porcelain line."""
    if len(sLine) < 4:
        return ""
    sRemainder = sLine[3:]
    if " -> " in sRemainder:
        return sRemainder.rsplit(" -> ", 1)[-1]
    return sRemainder


def _flockGetLock(sContainerId):
    """Return a per-container threading.Lock, creating on first use."""
    with _lockRegistry:
        if sContainerId not in _dictLocks:
            _dictLocks[sContainerId] = threading.Lock()
        return _dictLocks[sContainerId]


def fdictReadSidecar(connectionDocker, sContainerId):
    """Read the tracked_repos sidecar, returning None on any failure."""
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId,
            f"cat {S_TRACKED_REPOS_PATH} 2>/dev/null",
        )
        if iExitCode != 0 or not sOutput.strip():
            return None
        return json.loads(sOutput)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def fnWriteSidecar(connectionDocker, sContainerId, dictSidecar):
    """Write the sidecar dict to the container as indented JSON."""
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {S_TRACKED_REPOS_DIR}"
    )
    sContent = json.dumps(dictSidecar, indent=2)
    connectionDocker.fnWriteFile(
        sContainerId, S_TRACKED_REPOS_PATH, sContent.encode("utf-8")
    )


def fdictBuildInitialState(listRepoEntries):
    """Return the initial sidecar dict for a fresh container."""
    return {
        "iSchemaVersion": I_SCHEMA_VERSION,
        "listTracked": list(listRepoEntries),
        "listIgnored": [],
    }


def flistDiscoverGitDirs(connectionDocker, sContainerId):
    """Return sorted basenames of /workspace/<name>/.git directories."""
    sCommand = (
        "find /workspace -mindepth 2 -maxdepth 2 -type d "
        "-name .git -printf '%h\\n' 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExitCode != 0 or not sOutput:
        return []
    listNames = _flistParseFindOutput(sOutput)
    return sorted(listNames)


def _flistParseFindOutput(sOutput):
    """Parse find output into a filtered list of basenames."""
    listNames = []
    for sLine in sOutput.splitlines():
        sPath = sLine.strip()
        if not sPath:
            continue
        sBasename = sPath.rsplit("/", 1)[-1]
        if sBasename.startswith(".vaibify"):
            continue
        listNames.append(sBasename)
    return listNames


def _fsRunGitCommand(connectionDocker, sContainerId, sRepoName, sArgs):
    """Run a git command inside the repo and return stripped stdout or None."""
    sCommand = f"git -C /workspace/{sRepoName} {sArgs} 2>/dev/null"
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExitCode != 0:
        return None
    return sOutput.strip()


def _fbRepoIsMissing(connectionDocker, sContainerId, sRepoName):
    """Return True if /workspace/<repo>/.git is absent."""
    sCommand = (
        f"test -d /workspace/{sRepoName}/.git && echo yes || echo no"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() != "yes"


def _fdictBuildPresentStatus(sRepoName, sBranch, sPorcelain, sUrl):
    """Build the status dict for a present (non-missing) repository."""
    return {
        "sName": sRepoName,
        "sBranch": sBranch,
        "sUrl": sUrl if sUrl else None,
        "bDirty": bool(sPorcelain),
        "bMissing": False,
    }


def fdictComputeRepoStatus(connectionDocker, sContainerId, sRepoName):
    """Return status dict for a repo: branch, url, bDirty, bMissing."""
    if _fbRepoIsMissing(connectionDocker, sContainerId, sRepoName):
        return _fdictMissingStatus(sRepoName)
    sBranch = _fsRunGitCommand(
        connectionDocker, sContainerId, sRepoName,
        "rev-parse --abbrev-ref HEAD",
    )
    sRawPorcelain = _fsRunGitCommand(
        connectionDocker, sContainerId, sRepoName, "status --porcelain"
    )
    sFiltered = fsFilterArtifacts(sRawPorcelain or "")
    sUrl = _fsRunGitCommand(
        connectionDocker, sContainerId, sRepoName,
        "config --get remote.origin.url",
    )
    return _fdictBuildPresentStatus(sRepoName, sBranch, sFiltered, sUrl)


def _fdictMissingStatus(sRepoName):
    """Return the status dict for a missing repository."""
    return {
        "sName": sRepoName,
        "sBranch": None,
        "sUrl": None,
        "bDirty": False,
        "bMissing": True,
    }


def flistBatchComputeRepoStatus(
    connectionDocker, sContainerId, listRepoNames,
):
    """Compute status for multiple repos in a single docker exec."""
    if not listRepoNames:
        return []
    sScript = _fsBuildBatchStatusScript(listRepoNames)
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sScript,
    )
    if iExitCode != 0 or not sOutput:
        return _flistFallbackSequential(
            connectionDocker, sContainerId, listRepoNames)
    return _flistParseBatchOutput(sOutput, listRepoNames)


def _fsBuildBatchStatusScript(listRepoNames):
    """Build a shell script that dumps status for every repo."""
    listParts = ['echo "["']
    for iIdx, sName in enumerate(listRepoNames):
        sComma = ',' if iIdx > 0 else ''
        listParts.append(
            _fsBuildSingleRepoBlock(sName, sComma))
    listParts.append('echo "]"')
    return " && ".join(listParts)


def _fsBuildSingleRepoBlock(sRepoName, sComma):
    """Build shell commands that emit one JSON object for sRepoName."""
    sPath = "/workspace/" + sRepoName
    return (
        'echo "' + sComma + '{"'
        ' && echo "\\"sName\\": \\"' + sRepoName + '\\","'
        ' && ' + _fsBuildGitFieldCommand(sPath, "sBranch",
            "rev-parse --abbrev-ref HEAD")
        + ' && ' + _fsBuildGitFieldCommand(sPath, "sUrl",
            "config --get remote.origin.url")
        + ' && ' + _fsBuildPorcelainCommand(sPath)
        + ' && ' + _fsBuildMissingCheck(sPath)
        + ' && echo "}"'
    )


def _fsBuildGitFieldCommand(sPath, sFieldName, sGitArgs):
    """Build shell for one git field: echo key, run git, close quote."""
    return (
        'echo -n "\\"' + sFieldName + '\\": \\""'
        ' && (git -C ' + sPath + ' ' + sGitArgs
        + ' 2>/dev/null || echo -n "")'
        ' && echo "\\","'
    )


def _fsBuildPorcelainCommand(sPath):
    """Build shell for the sPorcelain field using pipe-delimited output."""
    return (
        'echo -n "\\"sPorcelain\\": \\""'
        " && (git -C " + sPath + " status --porcelain"
        " 2>/dev/null | tr '\\n' '|' || echo -n \"\")"
        ' && echo "\\","'
    )


def _fsBuildMissingCheck(sPath):
    """Build shell for the bMissing field via test -d."""
    return (
        "if test -d " + sPath + "/.git;"
        ' then echo "\\"bMissing\\": false";'
        ' else echo "\\"bMissing\\": true"; fi'
    )


def _flistParseBatchOutput(sOutput, listRepoNames):
    """Parse the batch script's JSON output into status dicts."""
    try:
        listRaw = json.loads(sOutput)
    except (json.JSONDecodeError, ValueError):
        return [_fdictMissingStatus(s) for s in listRepoNames]
    return [_fdictFromRawBatchEntry(d) for d in listRaw]


def _fdictFromRawBatchEntry(dictRaw):
    """Convert one raw batch entry into a clean status dict."""
    sName = dictRaw.get("sName", "")
    if dictRaw.get("bMissing", True):
        return _fdictMissingStatus(sName)
    sPorcelain = (dictRaw.get("sPorcelain", "") or "")
    sPorcelain = sPorcelain.replace("|", "\n")
    sFiltered = fsFilterArtifacts(sPorcelain)
    return _fdictBuildPresentStatus(
        sName, (dictRaw.get("sBranch") or "").strip(),
        sFiltered, (dictRaw.get("sUrl") or "").strip() or None,
    )


def _flistFallbackSequential(
    connectionDocker, sContainerId, listRepoNames,
):
    """Fall back to per-repo status if the batch script fails."""
    return [
        fdictComputeRepoStatus(connectionDocker, sContainerId, s)
        for s in listRepoNames
    ]


def _fdictLoadOrInit(connectionDocker, sContainerId):
    """Read sidecar or build an empty one if absent/invalid."""
    dictSidecar = fdictReadSidecar(connectionDocker, sContainerId)
    if dictSidecar is None:
        return fdictBuildInitialState([])
    dictSidecar.setdefault("iSchemaVersion", I_SCHEMA_VERSION)
    dictSidecar.setdefault("listTracked", [])
    dictSidecar.setdefault("listIgnored", [])
    return dictSidecar


def _flistBuildSeedEntries(connectionDocker, sContainerId, listNames):
    """Build tracked entries for auto-seeding from discovered repo names."""
    listEntries = []
    for sName in listNames:
        dictStatus = fdictComputeRepoStatus(
            connectionDocker, sContainerId, sName
        )
        listEntries.append(
            {"sName": sName, "sUrl": dictStatus.get("sUrl")}
        )
    return listEntries


def _fdictSeedSidecarFromDisk(connectionDocker, sContainerId):
    """Discover repos and write a fresh sidecar tracking all of them."""
    listDiscovered = flistDiscoverGitDirs(connectionDocker, sContainerId)
    listEntries = _flistBuildSeedEntries(
        connectionDocker, sContainerId, listDiscovered
    )
    dictSidecar = fdictBuildInitialState(listEntries)
    fnWriteSidecar(connectionDocker, sContainerId, dictSidecar)
    return dictSidecar


def fdictReadOrSeedSidecar(connectionDocker, sContainerId):
    """Return the sidecar, atomically seeding it from disk when absent."""
    with _flockGetLock(sContainerId):
        dictSidecar = fdictReadSidecar(connectionDocker, sContainerId)
        if dictSidecar is not None:
            return dictSidecar
        return _fdictSeedSidecarFromDisk(connectionDocker, sContainerId)


def _fnRemoveByName(listEntries, sRepoName):
    """Remove entries whose sName matches in-place."""
    listEntries[:] = [
        dictEntry for dictEntry in listEntries
        if dictEntry.get("sName") != sRepoName
    ]


def _fbContainsName(listEntries, sRepoName):
    """Return True if listEntries contains an entry with sName."""
    return any(
        dictEntry.get("sName") == sRepoName for dictEntry in listEntries
    )


def fnAddTracked(connectionDocker, sContainerId, sRepoName, sUrl):
    """Add a repo to listTracked and remove it from listIgnored."""
    with _flockGetLock(sContainerId):
        dictSidecar = _fdictLoadOrInit(connectionDocker, sContainerId)
        _fnRemoveByName(dictSidecar["listIgnored"], sRepoName)
        if not _fbContainsName(dictSidecar["listTracked"], sRepoName):
            dictSidecar["listTracked"].append(
                {"sName": sRepoName, "sUrl": sUrl}
            )
        fnWriteSidecar(connectionDocker, sContainerId, dictSidecar)


def fnAddIgnored(connectionDocker, sContainerId, sRepoName):
    """Add a repo to listIgnored and remove it from listTracked."""
    with _flockGetLock(sContainerId):
        dictSidecar = _fdictLoadOrInit(connectionDocker, sContainerId)
        _fnRemoveByName(dictSidecar["listTracked"], sRepoName)
        if not _fbContainsName(dictSidecar["listIgnored"], sRepoName):
            dictSidecar["listIgnored"].append({"sName": sRepoName})
        fnWriteSidecar(connectionDocker, sContainerId, dictSidecar)


def fnRemoveTracked(connectionDocker, sContainerId, sRepoName):
    """Remove a repo from listTracked without moving it to listIgnored."""
    with _flockGetLock(sContainerId):
        dictSidecar = _fdictLoadOrInit(connectionDocker, sContainerId)
        _fnRemoveByName(dictSidecar["listTracked"], sRepoName)
        fnWriteSidecar(connectionDocker, sContainerId, dictSidecar)


def fnUnignore(connectionDocker, sContainerId, sRepoName):
    """Remove a repo from listIgnored without adding it to listTracked."""
    with _flockGetLock(sContainerId):
        dictSidecar = _fdictLoadOrInit(connectionDocker, sContainerId)
        _fnRemoveByName(dictSidecar["listIgnored"], sRepoName)
        fnWriteSidecar(connectionDocker, sContainerId, dictSidecar)


def fbIsTracked(dictSidecar, sRepoName):
    """Return True if the sidecar lists the repo as tracked."""
    return _fbContainsName(dictSidecar.get("listTracked", []), sRepoName)


def fbIsIgnored(dictSidecar, sRepoName):
    """Return True if the sidecar lists the repo as ignored."""
    return _fbContainsName(dictSidecar.get("listIgnored", []), sRepoName)


def flistGetTrackedNames(dictSidecar):
    """Return the list of sName values from listTracked."""
    return [
        dictEntry.get("sName")
        for dictEntry in dictSidecar.get("listTracked", [])
    ]
