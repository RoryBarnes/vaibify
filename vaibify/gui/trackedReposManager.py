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
    "flistBuildSeedEntries",
    "fdictReadOrSeedSidecar",
    "flistDiscoverGitDirs",
    "flistDiscoverNonGitDirs",
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

S_WORKSPACE_ROOT = "/workspace"
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
    """Read the tracked_repos sidecar, returning None on any failure.

    A TYPED READ. It used to assemble ``cat <path>`` and hand it to the
    general exec primitive, which the mutation gate must treat as
    mutating because command text cannot be told apart from a delete --
    so on an enforced lane the Repos panel's own read would be refused.
    ``fbaFetchFile`` names a declared read operation and the adapter
    builds the command, so the path can never become program text.

    ``FileNotFoundError`` is an ``OSError`` and lands in the same net
    as a malformed document: both mean "no usable sidecar", which the
    caller answers by seeding one in memory.
    """
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, S_TRACKED_REPOS_PATH,
        )
        if not baContent.strip():
            return None
        return json.loads(baContent.decode("utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
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


def _ftPartitionWorkspaceDirectories(connectionDocker, sContainerId):
    """Return ``(listGitDirs, listNonGitDirs)`` under the workspace root.

    TWO TYPED READS for what used to be two ``find`` execs -- and the
    old pair ran THREE, because the non-git discovery called the git
    discovery again to subtract it. One directory listing plus one
    batched existence probe answers both questions at once, from one
    consistent view of the workspace, and neither reaches the general
    exec primitive.

    Anything that is not a directory falls out naturally: a plain file
    has no ``.git`` child, and the workspace's own dot-directories are
    filtered by name as they always were.
    """
    try:
        listEntries = connectionDocker.flistDirectoryEntries(
            sContainerId, S_WORKSPACE_ROOT,
        )
    except OSError:
        return ([], [])
    listNames = sorted(
        sName for sName in listEntries
        if not sName.startswith(".")
    )
    if not listNames:
        return ([], [])
    listGitMarkers = [
        S_WORKSPACE_ROOT + "/" + sName + "/.git" for sName in listNames
    ]
    try:
        listHasGit = connectionDocker.flistContainerPathsExist(
            sContainerId, listGitMarkers,
        )
    except OSError:
        return ([], [])
    listGitDirs = [
        sName for sName, bHasGit in zip(listNames, listHasGit) if bHasGit
    ]
    listNonGitDirs = [
        sName for sName, bHasGit in zip(listNames, listHasGit)
        if not bHasGit
    ]
    return (listGitDirs, listNonGitDirs)


def flistDiscoverGitDirs(connectionDocker, sContainerId):
    """Return sorted basenames of /workspace/<name>/.git directories."""
    listGitDirs, _listNonGitDirs = _ftPartitionWorkspaceDirectories(
        connectionDocker, sContainerId,
    )
    return listGitDirs


def flistDiscoverNonGitDirs(connectionDocker, sContainerId):
    """Return sorted basenames of /workspace/<name> dirs lacking .git/."""
    _listGitDirs, listNonGitDirs = _ftPartitionWorkspaceDirectories(
        connectionDocker, sContainerId,
    )
    return listNonGitDirs


def _flistFilterDirNames(sOutput):
    """Filter raw basenames, dropping vaibify system dirs."""
    listNames = []
    for sLine in sOutput.splitlines():
        sName = sLine.strip()
        if not sName or sName.startswith(".vaibify"):
            continue
        listNames.append(sName)
    return listNames


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
    """Read the sidecar, or build the seed a fresh workspace implies.

    The SEED, not an empty state, and that is load-bearing now that the
    read path no longer writes. Before, the first GET persisted a
    sidecar tracking every discovered repository, so the first mutation
    loaded a full document. With the write gone, an empty fallback
    would make the first Track action persist a sidecar containing that
    one repository and silently untrack every other one.
    """
    dictSidecar = fdictReadSidecar(connectionDocker, sContainerId)
    if dictSidecar is None:
        return _fdictSeedSidecarInMemory(connectionDocker, sContainerId)
    dictSidecar.setdefault("iSchemaVersion", I_SCHEMA_VERSION)
    dictSidecar.setdefault("listTracked", [])
    dictSidecar.setdefault("listIgnored", [])
    return dictSidecar


def flistBuildSeedEntries(listNames):
    """Build the tracked entries a fresh workspace seeds itself with.

    PURE, and it used to run one ``git config --get remote.origin.url``
    per repository to fill ``sUrl``. That was duplicated work as well as
    a per-repo exec: the status payload's batch already reads every
    tracked repo's URL, and ``_fdictBuildTrackedEntry`` prefers the
    stored URL only when there IS one, falling back to the live value.
    So a seed entry carries the name and leaves the URL to the batch.
    """
    return [{"sName": sName, "sUrl": None} for sName in listNames]


def _fdictSeedSidecarInMemory(connectionDocker, sContainerId):
    """Return the sidecar a fresh workspace implies, WITHOUT writing it.

    Auto-tracking every discovered repository is the product behaviour
    and it is unchanged; what changed is that discovering it no longer
    WRITES. A GET that creates state is wrong on its own terms, and
    this one was wrong in a way that mattered: the Repos panel polls it
    on a timer, so the dashboard held the only container mutation that
    fired on a schedule, and a commit-guard carrier around it would
    have had to hold the mutation drain on that same schedule --
    refusing the researcher's own Run Step at random.

    The seed is a pure function of what discovery found, so recomputing
    it per read costs nothing beyond the discovery every poll does
    anyway. It reaches disk the first time a MUTATION persists it,
    which is the first moment the file has anything to say that
    discovery does not.
    """
    return fdictBuildInitialState(
        flistBuildSeedEntries(
            flistDiscoverGitDirs(connectionDocker, sContainerId),
        ),
    )


def fdictReadOrSeedSidecar(connectionDocker, sContainerId):
    """Return the sidecar, seeding it IN MEMORY when absent."""
    with _flockGetLock(sContainerId):
        dictSidecar = fdictReadSidecar(connectionDocker, sContainerId)
        if dictSidecar is not None:
            return dictSidecar
        return _fdictSeedSidecarInMemory(connectionDocker, sContainerId)


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
