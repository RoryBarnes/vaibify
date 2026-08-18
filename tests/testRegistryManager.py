"""Tests for vaibify.config.registryManager."""

import json
import os

import pytest

from vaibify.config import registryManager


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect registry to a temp directory for every test."""
    sRegistryDir = str(tmp_path / ".vaibify")
    sRegistryPath = os.path.join(sRegistryDir, "registry.json")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", sRegistryPath,
    )


def _fnWriteMinimalConfig(tmp_path, sProjectName="test-project"):
    """Create a minimal vaibify.yml in a temp project directory."""
    sProjectDir = str(tmp_path / sProjectName)
    os.makedirs(sProjectDir, exist_ok=True)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(f"projectName: {sProjectName}\n")
    return sProjectDir


# --- fdictLoadRegistry ---

def testLoadRegistryReturnsEmptyWhenNoFile():
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == {"listProjects": []}


def testLoadRegistryReturnsEmptyWhenCorrupt(tmp_path):
    sDir = registryManager._S_REGISTRY_DIRECTORY
    os.makedirs(sDir, exist_ok=True)
    sPath = registryManager._S_REGISTRY_PATH
    with open(sPath, "w") as fileHandle:
        fileHandle.write("not valid json{{{")
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == {"listProjects": []}


def testLoadRegistryReturnsContent(tmp_path):
    sDir = registryManager._S_REGISTRY_DIRECTORY
    os.makedirs(sDir, exist_ok=True)
    dictExpected = {"listProjects": [{"sName": "foo"}]}
    with open(registryManager._S_REGISTRY_PATH, "w") as fileHandle:
        json.dump(dictExpected, fileHandle)
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == dictExpected


# --- fnSaveRegistry ---

def testSaveRegistryCreatesDirectory():
    dictRegistry = {"listProjects": [{"sName": "bar"}]}
    registryManager.fnSaveRegistry(dictRegistry)
    assert os.path.isdir(registryManager._S_REGISTRY_DIRECTORY)
    dictLoaded = registryManager.fdictLoadRegistry()
    assert dictLoaded["listProjects"][0]["sName"] == "bar"


def testSaveRegistryAtomicOverwrite():
    registryManager.fnSaveRegistry({"listProjects": [{"sName": "a"}]})
    registryManager.fnSaveRegistry({"listProjects": [{"sName": "b"}]})
    dictLoaded = registryManager.fdictLoadRegistry()
    assert len(dictLoaded["listProjects"]) == 1
    assert dictLoaded["listProjects"][0]["sName"] == "b"


# --- fsDiscoverConfigInDirectory ---

def testDiscoverConfigFindsVaibifyYml(tmp_path):
    sProjectDir = str(tmp_path / "myproject")
    os.makedirs(sProjectDir)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("projectName: myproject\n")
    sResult = registryManager.fsDiscoverConfigInDirectory(sProjectDir)
    assert sResult == sConfigPath


def testDiscoverConfigRaisesWhenMissing(tmp_path):
    sEmptyDir = str(tmp_path / "empty")
    os.makedirs(sEmptyDir)
    with pytest.raises(FileNotFoundError):
        registryManager.fsDiscoverConfigInDirectory(sEmptyDir)



# --- fnAddProject ---

def testAddProjectRegistersSuccessfully(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    listProjects = registryManager.flistGetAllProjects()
    assert len(listProjects) == 1
    assert listProjects[0]["sName"] == "test-project"
    assert listProjects[0]["sDirectory"] == sProjectDir


def testAddProjectRejectsDuplicateName(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "dup-project")
    registryManager.fnAddProject(sProjectDir)
    with pytest.raises(ValueError, match="Container.*already registered"):
        registryManager.fnAddProject(sProjectDir)


def testAddProjectAllowsSameNameFromDifferentDirectory(tmp_path):
    """After removing a container, re-adding from a new dir succeeds."""
    sProjectDirA = _fnWriteMinimalConfig(tmp_path, "reuse-project")
    registryManager.fnAddProject(sProjectDirA)
    registryManager.fnRemoveProject("reuse-project")
    sProjectDirB = str(tmp_path / "alt" / "reuse-project")
    os.makedirs(sProjectDirB, exist_ok=True)
    sConfigPath = os.path.join(sProjectDirB, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("projectName: reuse-project\n")
    registryManager.fnAddProject(sProjectDirB)
    assert len(registryManager.flistGetAllProjects()) == 1


def testAddProjectRejectsMissingConfig(tmp_path):
    sEmptyDir = str(tmp_path / "no-config")
    os.makedirs(sEmptyDir)
    with pytest.raises(FileNotFoundError):
        registryManager.fnAddProject(sEmptyDir)


def testAddProjectRejectsSameDirectoryUnderNewName(tmp_path):
    """One physical directory must never carry two registry entries."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "first-name")
    registryManager.fnAddProject(sProjectDir)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("projectName: second-name\n")
    with pytest.raises(ValueError, match="already registered"):
        registryManager.fnAddProject(sProjectDir)
    assert len(registryManager.flistGetAllProjects()) == 1


def testAddProjectRejectsSameDirectoryViaSymlink(tmp_path):
    """A symlink alias of a registered directory is the same directory."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "linked-project")
    registryManager.fnAddProject(sProjectDir)
    sLinkPath = str(tmp_path / "alias-link")
    os.symlink(sProjectDir, sLinkPath)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write("projectName: alias-name\n")
    with pytest.raises(ValueError, match="already registered"):
        registryManager.fnAddProject(sLinkPath)
    assert len(registryManager.flistGetAllProjects()) == 1


def testSamePhysicalDirectoryFallsBackToRealpathWhenAbsent(tmp_path):
    """Paths that cannot be stat'ed compare by normalized realpath."""
    sGhostPath = str(tmp_path / "ghost")
    assert registryManager._fbSamePhysicalDirectory(
        sGhostPath, sGhostPath,
    )
    assert not registryManager._fbSamePhysicalDirectory(
        sGhostPath, sGhostPath + "-other",
    )


def testIsHostProjectReadsTheModeDiscriminator():
    registryManager.fnSaveRegistry({"listProjects": [
        {"sName": "host-proj", "sDirectory": "/x", "sMode": "host"},
        {"sName": "container-proj", "sDirectory": "/y"},
    ]})
    assert registryManager.fbIsHostProject("host-proj")
    assert not registryManager.fbIsHostProject("container-proj")
    assert not registryManager.fbIsHostProject("unknown-docker-id")


# --- fbIsProject: the sandbox/Project discriminator ---

def testContainerEntryIsAlwaysAProjectRegardlessOfFlag():
    """A container is a Project by definition, whatever bIsProject says.

    The stored flag is deliberately WRONG here (``False`` on a container)
    to prove the predicate reads the mode first: a container that somehow
    carried a false flag must still read as a Project.
    """
    assert registryManager.fbIsProject(
        {"sName": "c", "sMode": "container", "bIsProject": False})
    # Absent sMode is container mode, so absent-everything is a Project.
    assert registryManager.fbIsProject({"sName": "legacy"})


def testHostEntryDefaultsToSandbox():
    """A host entry with no bIsProject key reads as a sandbox."""
    assert not registryManager.fbIsProject(
        {"sName": "h", "sMode": "host"})


def testPromotedHostEntryReadsAsProject():
    """A host entry with bIsProject true reads as a Project, staying host."""
    dictEntry = {"sName": "h", "sMode": "host", "bIsProject": True}
    assert registryManager.fbIsProject(dictEntry)
    assert dictEntry["sMode"] == "host"


def testEnrichmentCarriesTheProjectFlagForBothModes(monkeypatch):
    """The registry listing carries bIsProject so the frontend can branch.

    A host sandbox reports False, a promoted host Project reports True
    (still host), and a container reports True. Docker is stubbed so the
    container branch never reaches a live daemon.
    """
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists", lambda sTag: False)
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {"bExists": False, "bRunning": False})
    registryManager.fnSaveRegistry({"listProjects": [
        {"sName": "sandbox", "sDirectory": "/x",
         "sConfigPath": "/x/vaibify.yml", "sContainerName": "sandbox",
         "sMode": "host"},
        {"sName": "hostProject", "sDirectory": "/y",
         "sConfigPath": "/y/vaibify.yml", "sContainerName": "hostProject",
         "sMode": "host", "bIsProject": True},
        {"sName": "boxed", "sDirectory": "/z",
         "sConfigPath": "/z/vaibify.yml", "sContainerName": "boxed",
         "sMode": "container"},
    ]})
    dictByName = {
        dictEntry["sName"]: dictEntry
        for dictEntry in registryManager.flistGetAllProjectsWithStatus()
    }
    assert dictByName["sandbox"]["bIsProject"] is False
    assert dictByName["hostProject"]["bIsProject"] is True
    assert dictByName["hostProject"]["sMode"] == "host"
    assert dictByName["boxed"]["bIsProject"] is True


def testMutateRegistryLockedReadsUnderTheLock(monkeypatch):
    """An entry written just before the lock is taken must be seen.

    The pre-fix ordering read the registry before acquiring the lock,
    so two concurrent registrations could both pass their duplicate
    checks against the same stale snapshot. Planting an entry at
    lock-acquisition time and asserting the mutation callback sees it
    fails against that ordering.
    """
    fnRealOpenLock = registryManager._ffileOpenRegistryLock

    def ffilePlantEntryThenLock():
        fileHandle = fnRealOpenLock()
        registryManager._fnWriteRegistryAtomic(
            {"listProjects": [{"sName": "planted"}]},
        )
        return fileHandle

    monkeypatch.setattr(
        registryManager, "_ffileOpenRegistryLock",
        ffilePlantEntryThenLock,
    )
    listNamesSeen = []

    def fnRecordNamesSeen(dictRegistry):
        listNamesSeen.append([
            dictProject["sName"]
            for dictProject in dictRegistry["listProjects"]
        ])

    registryManager._fnMutateRegistryLocked(fnRecordNamesSeen)
    assert listNamesSeen == [["planted"]]


# --- fnRemoveProject ---

def testRemoveProjectDeletesEntry(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    registryManager.fnRemoveProject("test-project")
    assert len(registryManager.flistGetAllProjects()) == 0


def testRemoveProjectRaisesWhenNotFound():
    with pytest.raises(KeyError, match="not found"):
        registryManager.fnRemoveProject("nonexistent")


# --- fdictGetProject ---

def testGetProjectReturnsEntry(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    dictProject = registryManager.fdictGetProject("test-project")
    assert dictProject is not None
    assert dictProject["sName"] == "test-project"


def testGetProjectReturnsNoneWhenMissing():
    assert registryManager.fdictGetProject("ghost") is None


# --- flistGetAllProjectsWithStatus ---

def testGetAllProjectsWithStatusEnrichesEntries(
    tmp_path, monkeypatch,
):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        lambda sTag: False,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": False, "bRunning": False,
            "sStatus": "not found",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert len(listResult) == 1
    assert listResult[0]["bImageExists"] is False
    assert listResult[0]["bRunning"] is False
    assert listResult[0]["sStatus"] == "not built"


def testStatusRunningWhenContainerActive(tmp_path, monkeypatch):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        lambda sTag: True,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": True, "bRunning": True,
            "sStatus": "running",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "running"
    assert listResult[0]["bRunning"] is True


def testStatusStoppedWhenImageExistsButNotRunning(
    tmp_path, monkeypatch,
):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        lambda sTag: True,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": False, "bRunning": False,
            "sStatus": "not found",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "stopped"


# -----------------------------------------------------------------------
# _fnWriteRegistryAtomic — exception path
# -----------------------------------------------------------------------


def testWriteRegistryAtomicCleansUpOnReplaceError(
    tmp_path, monkeypatch,
):
    import vaibify.config.registryManager as rm
    monkeypatch.setattr(rm, "_S_REGISTRY_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(
        rm, "_S_REGISTRY_PATH", str(tmp_path / "registry.json"),
    )

    def fRaisOnReplace(sSrc, sDst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fRaisOnReplace)
    with pytest.raises(OSError, match="replace failed"):
        rm._fnWriteRegistryAtomic({"listProjects": []})
    listTmpFiles = list(tmp_path.glob("*.tmp"))
    assert len(listTmpFiles) == 0, "Temp file should be cleaned up"


# -----------------------------------------------------------------------
# fdictLoadRegistry — non-dict value in file
# -----------------------------------------------------------------------


def testLoadRegistryReturnsEmptyWhenNotDict(tmp_path):
    """Line 29: registry file contains a list instead of a dict."""
    sDir = registryManager._S_REGISTRY_DIRECTORY
    os.makedirs(sDir, exist_ok=True)
    sPath = registryManager._S_REGISTRY_PATH
    with open(sPath, "w") as fileHandle:
        fileHandle.write('["not", "a", "dict"]')
    dictResult = registryManager.fdictLoadRegistry()
    assert dictResult == {"listProjects": []}


# -----------------------------------------------------------------------
# fsGetContainerUser
# -----------------------------------------------------------------------


def testGetContainerUserReturnsResearcherWhenNotRegistered():
    """Lines 204-206: project not in registry returns fallback."""
    sResult = registryManager.fsGetContainerUser("nonexistent-container")
    assert sResult == "researcher"


def testGetContainerUserReturnsResearcherOnConfigError(
    tmp_path, monkeypatch,
):
    """Lines 207-212: config load fails returns fallback."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "user-project")
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.cli.configLoader.fconfigLoadFromPath",
        lambda sPath: (_ for _ in ()).throw(
            RuntimeError("config broken")
        ),
    )
    sResult = registryManager.fsGetContainerUser("user-project")
    assert sResult == "researcher"


def testGetContainerUserReturnsActualUser(tmp_path, monkeypatch):
    """Lines 204-210: successful path returns container user."""
    from types import SimpleNamespace
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "real-project")
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.cli.configLoader.fconfigLoadFromPath",
        lambda sPath: SimpleNamespace(sContainerUser="scientist"),
    )
    sResult = registryManager.fsGetContainerUser("real-project")
    assert sResult == "scientist"


# -----------------------------------------------------------------------
# Host entries: sMode discriminator + status enrichment (host-mode §9)
# -----------------------------------------------------------------------


def _fnPatchDockerProbesToExplode(monkeypatch):
    """Make both Docker status probes fail loudly if consulted."""

    def fnExplodeOnDockerTouch(*tArguments, **dictKeywords):
        raise AssertionError("a host entry consulted Docker")

    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists",
        fnExplodeOnDockerTouch,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        fnExplodeOnDockerTouch,
    )


def testAddProjectStoresTheHostMode(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "host-proj")
    registryManager.fnAddProject(sProjectDir, sMode="host")
    dictProject = registryManager.fdictGetProject("host-proj")
    assert dictProject["sMode"] == "host"
    assert registryManager.fbIsHostProject("host-proj")


def testAddProjectDefaultsToContainerMode(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "container-proj")
    registryManager.fnAddProject(sProjectDir)
    dictProject = registryManager.fdictGetProject("container-proj")
    assert dictProject["sMode"] == "container"
    assert not registryManager.fbIsHostProject("container-proj")


def testAddProjectRefusesAnUnknownMode(tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "odd-proj")
    with pytest.raises(ValueError, match="Unknown project mode"):
        registryManager.fnAddProject(sProjectDir, sMode="sandbox")
    assert registryManager.flistGetAllProjects() == []


def testHostEntryStatusIsReadyWithoutConsultingDocker(
    tmp_path, monkeypatch,
):
    """A host entry's status never touches Docker (host-mode plan §9).

    Both Docker probes are patched to raise, so a mode branch stuck at
    the container side cannot pass quietly. Kills: the enrichment
    dispatch reading every entry as a container entry.
    """
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "host-ready")
    registryManager.fnAddProject(sProjectDir, sMode="host")
    _fnPatchDockerProbesToExplode(monkeypatch)
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "ready"
    assert listResult[0]["bImageExists"] is False
    assert listResult[0]["bRunning"] is False


def testHostEntryStatusIsMissingWhenTheDirectoryIsGone(
    tmp_path, monkeypatch,
):
    import shutil
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "host-gone")
    registryManager.fnAddProject(sProjectDir, sMode="host")
    _fnPatchDockerProbesToExplode(monkeypatch)
    shutil.rmtree(sProjectDir)
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "missing"


def testHostEntryStatusIsMissingWhenTheConfigIsGone(
    tmp_path, monkeypatch,
):
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "host-noconf")
    registryManager.fnAddProject(sProjectDir, sMode="host")
    _fnPatchDockerProbesToExplode(monkeypatch)
    os.unlink(os.path.join(sProjectDir, "vaibify.yml"))
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "missing"


# -----------------------------------------------------------------------
# fnConvertProjectToContainer — the host->container re-registration
# -----------------------------------------------------------------------


def testConvertRewritesModeNameAndContainerNameInPlace(tmp_path):
    """A host entry becomes a container entry under the new name.

    The keys distinct on purpose (basename 'ai greenhouse' != the new
    Docker-safe name 'aiGreenhouse'), so the writer cannot pass by
    reading one field where it should read another: the lock/lease/
    journal key changes to the new name, and the directory must NOT.
    """
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "ai greenhouse",
        "sDirectory": "/home/researcher/ai greenhouse",
        "sConfigPath": "/home/researcher/ai greenhouse/vaibify.yml",
        "sContainerName": "ai greenhouse",
        "sMode": "host",
    }]})
    registryManager.fnConvertProjectToContainer(
        "ai greenhouse", "aiGreenhouse",
    )
    assert registryManager.fdictGetProject("ai greenhouse") is None
    dictConverted = registryManager.fdictGetProject("aiGreenhouse")
    assert dictConverted is not None
    assert dictConverted["sMode"] == "container"
    assert dictConverted["sName"] == "aiGreenhouse"
    assert dictConverted["sContainerName"] == "aiGreenhouse"
    assert dictConverted["sDirectory"] == (
        "/home/researcher/ai greenhouse"
    )
    assert dictConverted["sConfigPath"] == (
        "/home/researcher/ai greenhouse/vaibify.yml"
    )
    assert not registryManager.fbIsHostProject("aiGreenhouse")


def testConvertRaisesKeyErrorWhenProjectAbsent():
    with pytest.raises(KeyError, match="not found"):
        registryManager.fnConvertProjectToContainer("ghost", "ghostBox")


def testConvertRefusesANonHostProject():
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "already-container",
        "sDirectory": "/x",
        "sConfigPath": "/x/vaibify.yml",
        "sContainerName": "already-container",
        "sMode": "container",
    }]})
    with pytest.raises(ValueError, match="not a host project"):
        registryManager.fnConvertProjectToContainer(
            "already-container", "somethingElse",
        )
    dictUnchanged = registryManager.fdictGetProject("already-container")
    assert dictUnchanged["sMode"] == "container"


def testConvertRefusesANameThatCollidesWithAnotherEntry():
    """The new name must be free among the OTHER entries."""
    registryManager.fnSaveRegistry({"listProjects": [
        {"sName": "greenhouse", "sDirectory": "/a",
         "sConfigPath": "/a/vaibify.yml",
         "sContainerName": "greenhouse", "sMode": "host"},
        {"sName": "occupied", "sDirectory": "/b",
         "sConfigPath": "/b/vaibify.yml",
         "sContainerName": "occupied", "sMode": "container"},
    ]})
    with pytest.raises(ValueError, match="already registered"):
        registryManager.fnConvertProjectToContainer(
            "greenhouse", "occupied",
        )
    assert registryManager.fdictGetProject("greenhouse")["sMode"] == "host"


def testConvertToItsOwnNameIsPermittedAndDoesNotSelfCollide():
    """Skipping self by identity means the same name is not a collision.

    A host name that is already Docker-safe may be kept; the writer must
    not read the entry's own name as a duplicate of itself.
    """
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "greenhouse",
        "sDirectory": "/a",
        "sConfigPath": "/a/vaibify.yml",
        "sContainerName": "greenhouse",
        "sMode": "host",
    }]})
    registryManager.fnConvertProjectToContainer("greenhouse", "greenhouse")
    dictConverted = registryManager.fdictGetProject("greenhouse")
    assert dictConverted["sMode"] == "container"
    assert dictConverted["sContainerName"] == "greenhouse"


# -----------------------------------------------------------------------
# fnPromoteHostProject — the host-sandbox -> host-Project promotion
# -----------------------------------------------------------------------


def testPromoteFlipsTheProjectFlagAndRenamesInPlace(tmp_path):
    """A host sandbox becomes a host Project under the new name.

    Keys distinct on purpose (basename 'greenhouse sandbox' != the new
    name 'AI Greenhouse'), so the writer cannot pass by reading one field
    where it should read another: the lock/lease/journal key changes to
    the new name, the mode STAYS host, no bImageExists/bRunning appears,
    and the directory must NOT move.
    """
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "greenhouse sandbox",
        "sDirectory": "/home/researcher/greenhouse sandbox",
        "sConfigPath": "/home/researcher/greenhouse sandbox/vaibify.yml",
        "sContainerName": "greenhouse sandbox",
        "sMode": "host",
    }]})
    registryManager.fnPromoteHostProject(
        "greenhouse sandbox", "AI Greenhouse",
    )
    assert registryManager.fdictGetProject("greenhouse sandbox") is None
    dictPromoted = registryManager.fdictGetProject("AI Greenhouse")
    assert dictPromoted is not None
    assert dictPromoted["sMode"] == "host"
    assert dictPromoted["sName"] == "AI Greenhouse"
    assert dictPromoted["sContainerName"] == "AI Greenhouse"
    assert dictPromoted["bIsProject"] is True
    assert dictPromoted["sDirectory"] == (
        "/home/researcher/greenhouse sandbox"
    )
    assert dictPromoted["sConfigPath"] == (
        "/home/researcher/greenhouse sandbox/vaibify.yml"
    )
    # Still a host project, and now a Project.
    assert registryManager.fbIsHostProject("AI Greenhouse")
    assert registryManager.fbIsProject(dictPromoted)


def testPromoteRaisesKeyErrorWhenProjectAbsent():
    with pytest.raises(KeyError, match="not found"):
        registryManager.fnPromoteHostProject("ghost", "Ghost Project")


def testPromoteRefusesAContainerProject():
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "already-container",
        "sDirectory": "/x",
        "sConfigPath": "/x/vaibify.yml",
        "sContainerName": "already-container",
        "sMode": "container",
    }]})
    with pytest.raises(ValueError, match="not a host project"):
        registryManager.fnPromoteHostProject(
            "already-container", "Something Else",
        )
    dictUnchanged = registryManager.fdictGetProject("already-container")
    assert dictUnchanged["sMode"] == "container"


def testPromoteRefusesAnAlreadyPromotedHostProject():
    """Idempotency guard: a host Project cannot be promoted again."""
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "AI Greenhouse",
        "sDirectory": "/a",
        "sConfigPath": "/a/vaibify.yml",
        "sContainerName": "AI Greenhouse",
        "sMode": "host",
        "bIsProject": True,
    }]})
    with pytest.raises(ValueError, match="already a Project"):
        registryManager.fnPromoteHostProject(
            "AI Greenhouse", "AI Greenhouse Two",
        )
    assert registryManager.fdictGetProject("AI Greenhouse")["bIsProject"]


def testPromoteRefusesANameThatCollidesWithAnotherEntry():
    """The new name must be free among the OTHER entries."""
    registryManager.fnSaveRegistry({"listProjects": [
        {"sName": "greenhouse", "sDirectory": "/a",
         "sConfigPath": "/a/vaibify.yml",
         "sContainerName": "greenhouse", "sMode": "host"},
        {"sName": "occupied", "sDirectory": "/b",
         "sConfigPath": "/b/vaibify.yml",
         "sContainerName": "occupied", "sMode": "container"},
    ]})
    with pytest.raises(ValueError, match="already registered"):
        registryManager.fnPromoteHostProject("greenhouse", "occupied")
    assert registryManager.fbIsHostProject("greenhouse")
    assert not registryManager.fbIsProject(
        registryManager.fdictGetProject("greenhouse"))


def testPromoteToItsOwnNameIsPermittedAndDoesNotSelfCollide():
    """A sandbox may keep its basename and still graduate to a Project.

    The writer must skip the entry being promoted by identity, or the
    entry would collide with itself. The flag flips even when the name
    does not change.
    """
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": "greenhouse",
        "sDirectory": "/a",
        "sConfigPath": "/a/vaibify.yml",
        "sContainerName": "greenhouse",
        "sMode": "host",
    }]})
    registryManager.fnPromoteHostProject("greenhouse", "greenhouse")
    dictPromoted = registryManager.fdictGetProject("greenhouse")
    assert dictPromoted["sMode"] == "host"
    assert dictPromoted["bIsProject"] is True


def testContainerEntryStatusStillConsultsDocker(tmp_path, monkeypatch):
    """The symmetric direction: a container entry keeps its Docker truth.

    Kills: the enrichment dispatch reading every entry as a host entry,
    which would report a running container as a red host tile.
    """
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "container-live")
    registryManager.fnAddProject(sProjectDir)
    monkeypatch.setattr(
        "vaibify.docker.imageBuilder.fbImageExists", lambda sTag: True,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        lambda sName: {
            "bExists": True, "bRunning": True, "sStatus": "running",
        },
    )
    listResult = registryManager.flistGetAllProjectsWithStatus()
    assert listResult[0]["sStatus"] == "running"
    assert listResult[0]["bRunning"] is True
    assert listResult[0]["bImageExists"] is True
