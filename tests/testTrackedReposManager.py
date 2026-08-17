"""Tests for trackedReposManager domain module."""

import json
import threading

import pytest

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
        self.listWorkspaceRepos = []
        self.listWorkspaceNonRepos = []

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent

    def fnScriptContains(self, sNeedle, iExit, sOutput):
        self.dictScripted[sNeedle] = (iExit, sOutput)

    def fnSetWorkspace(self, listRepos, listNonRepos=()):
        """Populate what workspace discovery will find.

        Discovery reads the workspace through typed reads now -- one
        directory listing plus one batched existence probe -- so a
        scripted `find` string would be answering a command nothing
        issues.
        """
        self.listWorkspaceRepos = list(listRepos)
        self.listWorkspaceNonRepos = list(listNonRepos)

    # --- the typed reads discovery and the sidecar read use ---

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        baContent = self.dictFiles.get((sContainerId, sPath))
        if baContent is None:
            raise FileNotFoundError(sPath)
        return baContent

    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        return sorted(
            self.listWorkspaceRepos + self.listWorkspaceNonRepos,
        )

    def flistContainerPathsExist(self, sContainerId, listPaths):
        return [
            sPath[len("/workspace/"):].rsplit("/.git", 1)[0]
            in self.listWorkspaceRepos
            for sPath in listPaths
        ]

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        """Answer discovery's second probe: is this entry a directory.

        Everything this fixture holds is one; a file would answer no
        here and stay out of both lists, which is the point.
        """
        return [
            sPath[len("/workspace/"):] in self.listWorkspaceRepos
            or sPath[len("/workspace/"):] in self.listWorkspaceNonRepos
            for sPath in listPaths
        ]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        for sNeedle, tResult in self.dictScripted.items():
            if sNeedle in sCommand:
                return tResult
        if sCommand.startswith("mkdir -p"):
            return (0, "")
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
    """Discovery sorts, and skips the workspace's own dot-directories.

    ``.vaibify`` is in the fixture deliberately: it lives beside the
    repositories and is not one, and the retired ``find`` filtered it
    by name. The typed-read partition filters it the same way, before
    the existence probe rather than after it.
    """
    mockDocker = MockDockerConnection()
    mockDocker.fnSetWorkspace(["vspace", "vplanet", ".vaibify"])
    listNames = flistDiscoverGitDirs(mockDocker, "ctr1")
    assert listNames == ["vplanet", "vspace"]


def test_flistDiscoverGitDirs_empty():
    mockDocker = MockDockerConnection()
    mockDocker.fnSetWorkspace([])
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


@pytest.mark.falsification
def test_fdictReadOrSeedSidecar_seeds_in_memory_without_writing():
    """The seed is returned and NOT persisted.

    Auto-tracking every discovered repository is unchanged; writing it
    from a READ is what stopped. The Repos panel polls this on a timer,
    so persisting here made the dashboard the one thing mutating a
    container on a schedule -- and a commit-guard carrier around it
    would have had to hold the mutation drain on that same schedule,
    refusing the researcher's own Run Step at random.

    Both halves are asserted because either alone is satisfiable by a
    broken implementation: the names must be there (a seed that
    tracked nothing would also write nothing) and the file must not.

    Kills: restoring the write in ``_fdictSeedSidecarInMemory``.
    """
    mockDocker = MockDockerConnection()
    mockDocker.fnSetWorkspace(["alpha", "beta"])
    dictSidecar = fdictReadOrSeedSidecar(mockDocker, "ctr-seed-1")
    listNames = sorted(flistGetTrackedNames(dictSidecar))
    assert listNames == ["alpha", "beta"]
    assert ("ctr-seed-1", S_TRACKED_REPOS_PATH) not in mockDocker.dictFiles, (
        "the read path persisted a sidecar; a GET that creates state "
        "puts a container mutation back on the panel's timer"
    )


@pytest.mark.falsification
def test_a_first_mutation_persists_the_whole_seed():
    """Tracking one repo must not silently untrack the others.

    The regression this guards is created by the change above. While
    the read path seeded the FILE, the first mutation loaded a full
    document; with the write gone, a mutation that fell back to an
    EMPTY state would persist a sidecar containing only the repository
    the researcher just touched -- and every other repo in the
    workspace would quietly leave the panel.

    Kills: reverting ``_fdictLoadOrInit``'s fallback to
    ``fdictBuildInitialState([])``.
    """
    mockDocker = MockDockerConnection()
    mockDocker.fnSetWorkspace(["alpha", "beta", "gamma"])
    fnAddIgnored(mockDocker, "ctr-seed-4", "gamma")
    dictPersisted = fdictReadSidecar(mockDocker, "ctr-seed-4")
    assert sorted(flistGetTrackedNames(dictPersisted)) == [
        "alpha", "beta",
    ], (
        "ignoring one repository dropped the others from the sidecar: "
        f"{dictPersisted}"
    )


def test_fdictReadOrSeedSidecar_is_stable_across_reads():
    """Two reads of an unseeded workspace agree with each other."""
    mockDocker = MockDockerConnection()
    mockDocker.fnSetWorkspace(["alpha"])
    dictFirst = fdictReadOrSeedSidecar(mockDocker, "ctr-seed-2")
    dictSecond = fdictReadOrSeedSidecar(mockDocker, "ctr-seed-2")
    assert flistGetTrackedNames(dictFirst) == ["alpha"]
    assert flistGetTrackedNames(dictSecond) == ["alpha"]


def _fnScriptSeedingResponses(mockDocker, listRepoNames):
    """Populate the workspace a seed pass will discover."""
    mockDocker.fnSetWorkspace(listRepoNames)


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


def test_fdictReadOrSeedSidecar_concurrent_readers_agree():
    """Concurrent seed calls all answer the same thing.

    They no longer produce a FILE -- the read path writes nothing --
    so what is asserted is that eight racing readers agree, which is
    the property the lock is actually there for.
    """
    mockDocker = MockDockerConnection()
    _fnScriptSeedingResponses(mockDocker, ["alpha", "beta"])
    listResults = _flistRunSeedWorkers(mockDocker, "ctr-seed-3", 8)
    assert fdictReadSidecar(mockDocker, "ctr-seed-3") is None, (
        "eight concurrent READS persisted a sidecar between them"
    )
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


# ── The build-time list is authoritative ─────────────────────────
#
# /etc/vaibify/container.conf is baked into the image, so a repository
# it names must never reach the "undecided" state that raises the New
# Repository prompt — a sidecar snapshotted while the entrypoint was
# still cloning used to leave every later-arriving configured repo
# prompting on every visit.

from vaibify.gui import trackedReposManager
from vaibify.gui.trackedReposManager import (
    S_CONTAINER_CONF_PATH,
    flistReadConfiguredRepoNames,
    fnMergeConfiguredIntoTracked,
)


def fnResetConfiguredCache():
    trackedReposManager._dictConfiguredNamesCache.clear()


def fnStoreContainerConf(mockDocker, sContainerId, sText):
    mockDocker.dictFiles[(sContainerId, S_CONTAINER_CONF_PATH)] = (
        sText.encode("utf-8")
    )


def test_configured_repo_missing_from_sidecar_reads_as_tracked():
    """A sidecar predating a configured repo still reports it tracked.

    This is the incomplete-snapshot case: the sidecar was persisted
    before toolkitBeta reached the workspace, so it lists only
    toolkitAlpha. The union must repair the read.
    """
    fnResetConfiguredCache()
    mockDocker = MockDockerConnection()
    mockDocker.fnSetWorkspace(["toolkitAlpha", "toolkitBeta"])
    dictSidecar = {
        "iSchemaVersion": I_SCHEMA_VERSION,
        "listTracked": [{"sName": "toolkitAlpha", "sUrl": "u"}],
        "listIgnored": [],
    }
    mockDocker.dictFiles[("ctr-conf-1", S_TRACKED_REPOS_PATH)] = (
        json.dumps(dictSidecar).encode("utf-8")
    )
    fnStoreContainerConf(
        mockDocker, "ctr-conf-1",
        "toolkitAlpha|https://x/a.git|main|reference\n"
        "toolkitBeta|https://x/b.git|main|reference\n",
    )
    dictRead = fdictReadOrSeedSidecar(mockDocker, "ctr-conf-1")
    assert sorted(flistGetTrackedNames(dictRead)) == [
        "toolkitAlpha", "toolkitBeta",
    ]
    fnResetConfiguredCache()


def test_ignored_configured_repo_stays_ignored():
    """An explicit Ignore wins over the configured union."""
    fnResetConfiguredCache()
    mockDocker = MockDockerConnection()
    dictSidecar = {
        "iSchemaVersion": I_SCHEMA_VERSION,
        "listTracked": [],
        "listIgnored": [{"sName": "toolkitBeta"}],
    }
    mockDocker.dictFiles[("ctr-conf-2", S_TRACKED_REPOS_PATH)] = (
        json.dumps(dictSidecar).encode("utf-8")
    )
    fnStoreContainerConf(
        mockDocker, "ctr-conf-2",
        "toolkitBeta|https://x/b.git|main|reference\n",
    )
    dictRead = fdictReadOrSeedSidecar(mockDocker, "ctr-conf-2")
    assert flistGetTrackedNames(dictRead) == []
    assert fbIsIgnored(dictRead, "toolkitBeta")
    fnResetConfiguredCache()


def test_missing_or_malformed_conf_unions_nothing():
    fnResetConfiguredCache()
    mockDocker = MockDockerConnection()
    assert flistReadConfiguredRepoNames(mockDocker, "ctr-conf-3") == []
    fnStoreContainerConf(mockDocker, "ctr-conf-3", "only|two\n")
    assert flistReadConfiguredRepoNames(mockDocker, "ctr-conf-3") == []
    fnResetConfiguredCache()


def test_hidden_and_nested_destinations_are_not_unioned():
    """Discovery cannot surface them, so the union must not track them.

    A dot-destination overlay or a nested path would otherwise render
    as a permanently missing tracked repository.
    """
    fnResetConfiguredCache()
    mockDocker = MockDockerConnection()
    fnStoreContainerConf(
        mockDocker, "ctr-conf-4",
        "agentOverlay|https://x/o.git|main|reference|.claude\n"
        "toolkitNested|https://x/n.git|main|reference|sub/dir\n"
        "toolkitMoved|https://x/m.git|main|reference|renamed\n",
    )
    assert flistReadConfiguredRepoNames(mockDocker, "ctr-conf-4") == [
        "renamed",
    ]
    fnResetConfiguredCache()


def test_host_project_never_reads_the_conf(monkeypatch):
    """A host project has no image; the conf path must not be touched."""

    class RefusingConnection:
        def fbaFetchFile(self, *tArgs, **dictKwargs):
            raise AssertionError(
                "a host project must never read container.conf"
            )

    fnResetConfiguredCache()
    monkeypatch.setattr(
        trackedReposManager, "fsRepositoryRootFor",
        lambda sResourceId: "/home/researcher/project",
    )
    assert flistReadConfiguredRepoNames(
        RefusingConnection(), "hostProject",
    ) == []


def test_configured_union_persists_on_first_mutation():
    """A mutation writes the unioned list, repairing the sidecar on disk."""
    fnResetConfiguredCache()
    mockDocker = MockDockerConnection()
    dictSidecar = {
        "iSchemaVersion": I_SCHEMA_VERSION,
        "listTracked": [],
        "listIgnored": [],
    }
    mockDocker.dictFiles[("ctr-conf-5", S_TRACKED_REPOS_PATH)] = (
        json.dumps(dictSidecar).encode("utf-8")
    )
    fnStoreContainerConf(
        mockDocker, "ctr-conf-5",
        "toolkitBeta|https://x/b.git|main|reference\n",
    )
    fnAddIgnored(mockDocker, "ctr-conf-5", "handMade")
    dictWritten = json.loads(
        mockDocker.dictFiles[("ctr-conf-5", S_TRACKED_REPOS_PATH)]
    )
    assert flistGetTrackedNames(dictWritten) == ["toolkitBeta"]
    assert fbIsIgnored(dictWritten, "handMade")
    fnResetConfiguredCache()


def test_configured_read_is_cached_per_resource():
    """The conf is immutable per image, so one successful read suffices."""
    fnResetConfiguredCache()
    mockDocker = MockDockerConnection()
    listFetchedPaths = []
    fnOriginalFetch = mockDocker.fbaFetchFile

    def fbaCountingFetch(sContainerId, sPath, iMaxBytes=None):
        listFetchedPaths.append(sPath)
        return fnOriginalFetch(sContainerId, sPath, iMaxBytes)

    mockDocker.fbaFetchFile = fbaCountingFetch
    fnStoreContainerConf(
        mockDocker, "ctr-conf-6",
        "toolkitBeta|https://x/b.git|main|reference\n",
    )
    flistReadConfiguredRepoNames(mockDocker, "ctr-conf-6")
    flistReadConfiguredRepoNames(mockDocker, "ctr-conf-6")
    assert listFetchedPaths.count(S_CONTAINER_CONF_PATH) == 1
    fnResetConfiguredCache()
