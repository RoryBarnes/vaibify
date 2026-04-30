"""Tests for trackedReposManager domain module."""

import json
import threading

from vaibify.gui.trackedReposManager import (
    I_SCHEMA_VERSION,
    S_TRACKED_REPOS_PATH,
    fbIsIgnored,
    fbIsTracked,
    fdictBuildInitialState,
    fdictComputeRepoStatus,
    fdictReadOrSeedSidecar,
    fdictReadSidecar,
    flistDiscoverGitDirs,
    flistGetTrackedNames,
    fnAddIgnored,
    fnAddTracked,
    fnRemoveTracked,
    fnUnignore,
    fnWriteSidecar,
)


class MockDockerConnection:
    """Fake docker connection with scripted responses per command."""

    def __init__(self):
        self.dictFiles = {}
        self.listCommands = []
        self.dictScripted = {}

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent

    def fnScriptContains(self, sNeedle, iExit, sOutput):
        self.dictScripted[sNeedle] = (iExit, sOutput)

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        for sNeedle, tResult in self.dictScripted.items():
            if sNeedle in sCommand:
                return tResult
        if sCommand.startswith("mkdir -p"):
            return (0, "")
        if "cat " in sCommand and S_TRACKED_REPOS_PATH in sCommand:
            sKey = (sContainerId, S_TRACKED_REPOS_PATH)
            if sKey in self.dictFiles:
                return (0, self.dictFiles[sKey].decode("utf-8"))
            return (1, "")
        return (1, "")


def test_fdictReadSidecar_missing():
    mockDocker = MockDockerConnection()
    assert fdictReadSidecar(mockDocker, "ctr1") is None


def test_fdictReadSidecar_malformed():
    mockDocker = MockDockerConnection()
    mockDocker.dictFiles[("ctr1", S_TRACKED_REPOS_PATH)] = b"{not json"
    assert fdictReadSidecar(mockDocker, "ctr1") is None


def test_fdictReadSidecar_valid():
    mockDocker = MockDockerConnection()
    dictSidecar = {"iSchemaVersion": 1, "listTracked": [], "listIgnored": []}
    baContent = json.dumps(dictSidecar).encode("utf-8")
    mockDocker.dictFiles[("ctr1", S_TRACKED_REPOS_PATH)] = baContent
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    assert dictRead == dictSidecar


def test_fnWriteSidecar_writes_json():
    mockDocker = MockDockerConnection()
    dictSidecar = fdictBuildInitialState([{"sName": "vplanet", "sUrl": "u"}])
    fnWriteSidecar(mockDocker, "ctr1", dictSidecar)
    baStored = mockDocker.dictFiles[("ctr1", S_TRACKED_REPOS_PATH)]
    dictParsed = json.loads(baStored.decode("utf-8"))
    assert dictParsed["listTracked"][0]["sName"] == "vplanet"
    assert any("mkdir -p" in sCmd for sCmd in mockDocker.listCommands)


def test_fdictBuildInitialState_schema():
    listRepos = [{"sName": "vspace", "sUrl": "https://x"}]
    dictSidecar = fdictBuildInitialState(listRepos)
    assert dictSidecar["iSchemaVersion"] == I_SCHEMA_VERSION
    assert dictSidecar["listTracked"] == listRepos
    assert dictSidecar["listIgnored"] == []


def test_flistDiscoverGitDirs_parses_and_sorts():
    mockDocker = MockDockerConnection()
    sFindOutput = (
        "/workspace/vspace\n/workspace/vplanet\n"
        "/workspace/.vaibify\n\n"
    )
    mockDocker.fnScriptContains("find /workspace", 0, sFindOutput)
    listNames = flistDiscoverGitDirs(mockDocker, "ctr1")
    assert listNames == ["vplanet", "vspace"]


def test_flistDiscoverGitDirs_empty():
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains("find /workspace", 0, "")
    assert flistDiscoverGitDirs(mockDocker, "ctr1") == []


def test_fdictComputeRepoStatus_clean():
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains("test -d", 0, "yes")
    mockDocker.fnScriptContains("rev-parse", 0, "main\n")
    mockDocker.fnScriptContains("status --porcelain", 0, "")
    mockDocker.fnScriptContains("remote.origin.url", 0, "https://x/vplanet\n")
    dictStatus = fdictComputeRepoStatus(mockDocker, "ctr1", "vplanet")
    assert dictStatus["sBranch"] == "main"
    assert dictStatus["bDirty"] is False
    assert dictStatus["bMissing"] is False
    assert dictStatus["sUrl"] == "https://x/vplanet"


def test_fdictComputeRepoStatus_dirty():
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains("test -d", 0, "yes")
    mockDocker.fnScriptContains("rev-parse", 0, "dev\n")
    mockDocker.fnScriptContains("status --porcelain", 0, " M file.py\n")
    mockDocker.fnScriptContains("remote.origin.url", 0, "u\n")
    dictStatus = fdictComputeRepoStatus(mockDocker, "ctr1", "vplanet")
    assert dictStatus["bDirty"] is True


def test_fdictComputeRepoStatus_missing():
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains("test -d", 0, "no")
    dictStatus = fdictComputeRepoStatus(mockDocker, "ctr1", "ghost")
    assert dictStatus["bMissing"] is True
    assert dictStatus["sBranch"] is None
    assert dictStatus["sUrl"] is None


def test_fdictComputeRepoStatus_no_remote():
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains("test -d", 0, "yes")
    mockDocker.fnScriptContains("rev-parse", 0, "main\n")
    mockDocker.fnScriptContains("status --porcelain", 0, "")
    mockDocker.fnScriptContains("remote.origin.url", 1, "")
    dictStatus = fdictComputeRepoStatus(mockDocker, "ctr1", "local")
    assert dictStatus["sUrl"] is None
    assert dictStatus["bMissing"] is False


def test_fnAddTracked_adds_and_removes_from_ignored():
    mockDocker = MockDockerConnection()
    dictInitial = {
        "iSchemaVersion": 1, "listTracked": [],
        "listIgnored": [{"sName": "vplanet"}],
    }
    baContent = json.dumps(dictInitial).encode("utf-8")
    mockDocker.dictFiles[("ctr1", S_TRACKED_REPOS_PATH)] = baContent
    fnAddTracked(mockDocker, "ctr1", "vplanet", "https://x")
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    assert flistGetTrackedNames(dictRead) == ["vplanet"]
    assert dictRead["listIgnored"] == []


def test_fnAddTracked_idempotent():
    mockDocker = MockDockerConnection()
    fnAddTracked(mockDocker, "ctr1", "vspace", "u")
    fnAddTracked(mockDocker, "ctr1", "vspace", "u")
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    assert flistGetTrackedNames(dictRead) == ["vspace"]


def test_fnAddIgnored_symmetric():
    mockDocker = MockDockerConnection()
    fnAddTracked(mockDocker, "ctr1", "vplanet", "u")
    fnAddIgnored(mockDocker, "ctr1", "vplanet")
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    assert flistGetTrackedNames(dictRead) == []
    assert fbIsIgnored(dictRead, "vplanet") is True


def test_fnRemoveTracked_only_from_tracked():
    mockDocker = MockDockerConnection()
    fnAddTracked(mockDocker, "ctr1", "vplanet", "u")
    fnRemoveTracked(mockDocker, "ctr1", "vplanet")
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    assert dictRead["listTracked"] == []
    assert dictRead["listIgnored"] == []


def test_fnUnignore_only_from_ignored():
    mockDocker = MockDockerConnection()
    fnAddIgnored(mockDocker, "ctr1", "vplanet")
    fnUnignore(mockDocker, "ctr1", "vplanet")
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    assert dictRead["listTracked"] == []
    assert dictRead["listIgnored"] == []


def test_fbIsTracked_and_fbIsIgnored():
    dictSidecar = {
        "listTracked": [{"sName": "a"}],
        "listIgnored": [{"sName": "b"}],
    }
    assert fbIsTracked(dictSidecar, "a") is True
    assert fbIsTracked(dictSidecar, "b") is False
    assert fbIsIgnored(dictSidecar, "b") is True
    assert fbIsIgnored(dictSidecar, "a") is False


def test_flistGetTrackedNames():
    dictSidecar = {
        "listTracked": [{"sName": "x"}, {"sName": "y"}],
    }
    assert flistGetTrackedNames(dictSidecar) == ["x", "y"]


class LockingMockDocker(MockDockerConnection):
    """Mock that sleeps inside fnWriteFile to amplify races."""

    def fnWriteFile(self, sContainerId, sPath, baContent):
        import time
        time.sleep(0.01)
        super().fnWriteFile(sContainerId, sPath, baContent)


def test_fnAddTracked_threadsafe():
    mockDocker = LockingMockDocker()
    listThreads = []
    listNames = [f"repo{iIndex}" for iIndex in range(10)]
    for sName in listNames:
        tThread = threading.Thread(
            target=fnAddTracked,
            args=(mockDocker, "ctr1", sName, "u"),
        )
        listThreads.append(tThread)
    for tThread in listThreads:
        tThread.start()
    for tThread in listThreads:
        tThread.join()
    dictRead = fdictReadSidecar(mockDocker, "ctr1")
    listTrackedNames = flistGetTrackedNames(dictRead)
    assert sorted(listTrackedNames) == sorted(listNames)


def test_fdictReadOrSeedSidecar_seeds_when_missing():
    """Auto-seed writes a fresh sidecar from discovered repos."""
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains(
        "find /workspace", 0, "/workspace/alpha\n/workspace/beta\n"
    )
    mockDocker.fnScriptContains("test -d", 0, "yes")
    mockDocker.fnScriptContains("rev-parse", 0, "main\n")
    mockDocker.fnScriptContains("status --porcelain", 0, "")
    mockDocker.fnScriptContains("remote.origin.url", 0, "u\n")
    dictSidecar = fdictReadOrSeedSidecar(mockDocker, "ctr-seed-1")
    listNames = sorted(flistGetTrackedNames(dictSidecar))
    assert listNames == ["alpha", "beta"]
    assert (("ctr-seed-1", S_TRACKED_REPOS_PATH)
            in mockDocker.dictFiles)


def test_fdictReadOrSeedSidecar_returns_existing_without_write():
    """Second call after seed returns existing sidecar unchanged."""
    mockDocker = MockDockerConnection()
    mockDocker.fnScriptContains(
        "find /workspace", 0, "/workspace/alpha\n"
    )
    mockDocker.fnScriptContains("test -d", 0, "yes")
    mockDocker.fnScriptContains("rev-parse", 0, "main\n")
    mockDocker.fnScriptContains("status --porcelain", 0, "")
    mockDocker.fnScriptContains("remote.origin.url", 0, "u\n")
    dictFirst = fdictReadOrSeedSidecar(mockDocker, "ctr-seed-2")
    dictSecond = fdictReadOrSeedSidecar(mockDocker, "ctr-seed-2")
    assert flistGetTrackedNames(dictFirst) == ["alpha"]
    assert flistGetTrackedNames(dictSecond) == ["alpha"]


def _fnScriptSeedingResponses(mockDocker, sFindOutput):
    """Script the mock docker connection for a successful seed pass."""
    mockDocker.fnScriptContains("find /workspace", 0, sFindOutput)
    mockDocker.fnScriptContains("test -d", 0, "yes")
    mockDocker.fnScriptContains("rev-parse", 0, "main\n")
    mockDocker.fnScriptContains("status --porcelain", 0, "")
    mockDocker.fnScriptContains("remote.origin.url", 0, "u\n")


def _flistRunSeedWorkers(mockDocker, sContainerId, iWorkers):
    """Run iWorkers threads calling fdictReadOrSeedSidecar in parallel."""
    listResults = []

    def fnWorker():
        listResults.append(
            fdictReadOrSeedSidecar(mockDocker, sContainerId)
        )

    listThreads = [
        threading.Thread(target=fnWorker) for _ in range(iWorkers)
    ]
    for tThread in listThreads:
        tThread.start()
    for tThread in listThreads:
        tThread.join()
    return listResults


def test_fdictReadOrSeedSidecar_concurrent_single_sidecar():
    """Concurrent seed calls produce a single consistent sidecar."""
    mockDocker = MockDockerConnection()
    _fnScriptSeedingResponses(
        mockDocker, "/workspace/alpha\n/workspace/beta\n"
    )
    listResults = _flistRunSeedWorkers(mockDocker, "ctr-seed-3", 8)
    dictRead = fdictReadSidecar(mockDocker, "ctr-seed-3")
    listTracked = sorted(flistGetTrackedNames(dictRead))
    assert listTracked == ["alpha", "beta"]
    for dictResult in listResults:
        assert sorted(flistGetTrackedNames(dictResult)) == [
            "alpha", "beta"
        ]


# -----------------------------------------------------------------------
# Artifact filtering tests
# -----------------------------------------------------------------------

from vaibify.gui.trackedReposManager import (
    fbIsArtifactPath, fsFilterArtifacts,
    FROZENSET_ARTIFACT_PATTERNS,
)


def test_fbIsArtifactPath_egg_info():
    assert fbIsArtifactPath("foo.egg-info/") is True
    assert fbIsArtifactPath("foo.egg-info") is True
    assert fbIsArtifactPath("vplanet.egg-info/PKG-INFO") is True


def test_fbIsArtifactPath_pycache():
    assert fbIsArtifactPath("__pycache__/mod.cpython-312.pyc") is True
    assert fbIsArtifactPath("src/__pycache__/") is True
    assert fbIsArtifactPath("__pycache__") is True


def test_fbIsArtifactPath_object_files():
    assert fbIsArtifactPath("src/main.o") is True
    assert fbIsArtifactPath("lib/math.so") is True
    assert fbIsArtifactPath("lib/ffi.dylib") is True
    assert fbIsArtifactPath("lib/static.a") is True


def test_fbIsArtifactPath_latex_artifacts():
    assert fbIsArtifactPath("paper.aux") is True
    assert fbIsArtifactPath("paper.log") is True
    assert fbIsArtifactPath("paper.bbl") is True
    assert fbIsArtifactPath("paper.synctex.gz") is True
    assert fbIsArtifactPath("src/tex/main.fls") is True
    assert fbIsArtifactPath("main.fdb_latexmk") is True


def test_fbIsArtifactPath_r_artifacts():
    assert fbIsArtifactPath(".Rhistory") is True
    assert fbIsArtifactPath(".RData") is True
    assert fbIsArtifactPath(".Rproj.user/") is True
    assert fbIsArtifactPath(".Rproj.user/shared") is True
    assert fbIsArtifactPath("pkg.Rcheck/") is True


def test_fbIsArtifactPath_julia_build_log():
    assert fbIsArtifactPath("deps/build.log") is True


def test_fbIsArtifactPath_dvc_tmp():
    assert fbIsArtifactPath(".dvc/tmp/cache") is True
    assert fbIsArtifactPath(".dvc/tmp/") is True


def test_fbIsArtifactPath_real_files_not_filtered():
    for sPath in [
        "src/main.py", "README.md", "Manifest.toml", "data.dvc",
        "paper.pdf", ".coverage", "htmlcov/index.html",
        "setup.py", "pyproject.toml", "man/foo.Rd",
    ]:
        assert fbIsArtifactPath(sPath) is False, (
            sPath + " should not be filtered"
        )


def test_fbIsArtifactPath_build_dir():
    assert fbIsArtifactPath("build/lib/foo.py") is True
    assert fbIsArtifactPath("build/") is True
    assert fbIsArtifactPath("buildtools/config.py") is False
    assert fbIsArtifactPath("cmake-build/out.o") is True


def test_fbIsArtifactPath_dist_dir():
    assert fbIsArtifactPath("dist/package-1.0.tar.gz") is True
    assert fbIsArtifactPath("distribute.py") is False


def test_fsFilterArtifacts_mixed_output():
    sPorcelain = (
        "?? foo.egg-info/\n"
        " M src/main.py\n"
        "?? __pycache__/mod.cpython-312.pyc\n"
        " M README.md\n"
    )
    sFiltered = fsFilterArtifacts(sPorcelain)
    assert "src/main.py" in sFiltered
    assert "README.md" in sFiltered
    assert "egg-info" not in sFiltered
    assert "__pycache__" not in sFiltered


def test_fsFilterArtifacts_all_artifacts():
    sPorcelain = "?? foo.egg-info/\n?? __pycache__/\n"
    assert fsFilterArtifacts(sPorcelain) == ""


def test_fsFilterArtifacts_empty_input():
    assert fsFilterArtifacts("") == ""
    assert fsFilterArtifacts(None) == ""


def test_fsFilterArtifacts_rename_syntax():
    sPorcelain = "R  old.py -> new.py\nR  old.pyc -> new.pyc\n"
    sFiltered = fsFilterArtifacts(sPorcelain)
    assert "new.py" in sFiltered
    assert "new.pyc" not in sFiltered


def test_fdictComputeRepoStatus_ignores_artifacts():
    """Integration: porcelain with only artifacts -> bDirty is False."""
    listCommands = []

    class ArtifactMockDocker:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            listCommands.append(sCommand)
            if "test -d" in sCommand:
                return (0, "yes\n")
            if "rev-parse" in sCommand:
                return (0, "main\n")
            if "status --porcelain" in sCommand:
                return (0, "?? alabi.egg-info/\n?? __pycache__/\n")
            if "config --get remote" in sCommand:
                return (0, "https://github.com/example/alabi.git\n")
            return (0, "")

    dictStatus = fdictComputeRepoStatus(
        ArtifactMockDocker(), "ctr-1", "alabi")
    assert dictStatus["bDirty"] is False
    assert dictStatus["sBranch"] == "main"
    assert dictStatus["bMissing"] is False
