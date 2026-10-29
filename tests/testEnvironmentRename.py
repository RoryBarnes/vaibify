"""Renaming an environment: reach, crash recovery, and the orderings.

The transaction is driven for real -- the registry, the journal, the
lifecycle and session locks, the host files and ``vaibify.yml`` all on
disk -- against an in-memory DAEMON standing in for Docker: containers,
volumes with their files and labels, and image tags by ID. The volume
copy itself, and the copier that performs it, are proven against a real
daemon in ``testVolumeCopyLive.py`` and ``testEnvironmentRenameLive.py``.

A CRASH is simulated as process death: the transaction raises a
``BaseException`` its own handlers cannot catch, nothing it would have
undone in-process is undone, and the operating system's part -- every
``flock`` released -- happens as it would. Recovery is then run as the
next hub would run it, and must leave the environment complete under
exactly one name; a second recovery must change nothing.
"""

import copy
import os
import subprocess
import sys
import threading

import pytest

from tests.lifecycleLockHolder import (
    fcontextHoldLifecycleLockInAnotherProcess, fsRepositoryRoot,
)
from vaibify.config import containerLock, pendingRenames, registryManager
from vaibify.docker import (
    containerManager, disposableContainer, dockerContext, imageBuilder,
    volumeCopy, volumeManager,
)
from vaibify.gui import environmentRename, environmentResources


S_OLD = "sample-environment"
S_NEW = "renamed-environment"
I_PORT = 8050
S_IMAGE_LATEST = "sha256:" + "1" * 64
S_IMAGE_BASE = "sha256:" + "2" * 64
S_OTHER_IMAGE = "sha256:" + "9" * 64
# A comment and an unusual key order: a re-rendered file loses both, so
# only a restore of the journaled BYTES can bring them back.
S_CONFIG = "# my own note about this environment\nprojectName: " + S_OLD + "\n"


class SimulatedCrash(BaseException):
    """Process death: no handler in the transaction may catch it."""


class FakeDaemon:
    """Containers, volumes (with files and labels) and tags, by identity."""

    def __init__(self):
        self.dictContainers = {S_OLD: {"Id": "c" * 64, "bRunning": False}}
        self.dictVolumes = {
            S_OLD + "-workspace": {
                "sCreatedAt": "t1", "dictLabels": {},
                "dictFiles": {"data/out.csv": "1,2\n", "run.sh": "echo\n"}},
            S_OLD + "-credentials": {
                "sCreatedAt": "t2", "dictLabels": {},
                "dictFiles": {"keyring": "token"}},
        }
        self.dictImages = {S_OLD + ":latest": S_IMAGE_LATEST,
                           S_OLD + ":base": S_IMAGE_BASE}

    def fdictSnapshot(self):
        return copy.deepcopy(
            (self.dictContainers, self.dictVolumes, self.dictImages))

    def fdictContainerStatus(self, sName):
        dictContainer = self.dictContainers.get(sName)
        return {"bExists": dictContainer is not None,
                "bRunning": bool(dictContainer and dictContainer["bRunning"]),
                "sStatus": "exited" if dictContainer else "not found"}

    def fjsonInspect(self, sIdentity):
        for sName, dictContainer in self.dictContainers.items():
            if sIdentity in (sName, dictContainer["Id"]):
                return {"Id": dictContainer["Id"], "Name": sName}
        return {}

    def fnRemoveStopped(self, sName):
        self.dictContainers.pop(sName, None)

    def fdictProbePresence(self, sName):
        return {"bAnswered": True, "bPresent": sName in self.dictContainers}

    def fnCreateVolume(self, sName, dictLabels):
        if sName in self.dictVolumes:
            raise RuntimeError(f"the volume {sName} already exists")
        self.dictVolumes[sName] = {"sCreatedAt": "new", "dictFiles": {},
                                   "dictLabels": dict(dictLabels)}

    def fdictInspectVolume(self, sName):
        dictVolume = self.dictVolumes.get(sName)
        if dictVolume is None:
            return None
        return {"sCreatedAt": dictVolume["sCreatedAt"],
                "dictLabels": dict(dictVolume["dictLabels"])}

    def fnDestroyVolume(self, sName):
        del self.dictVolumes[sName]

    def fdictCopy(self, dictGateway, sImageId, sFrom, sTo, sDescription):
        assert sImageId == S_IMAGE_LATEST
        self.dictVolumes[sTo]["dictFiles"] = dict(
            self.dictVolumes[sFrom]["dictFiles"])
        return {"sDigest": "d", "iEntries": 1}

    def flistReferences(self, sRepository):
        return sorted(s for s in self.dictImages
                      if s.split(":", 1)[0] == sRepository)

    def fdictInspectImage(self, dockerClient, sReference):
        sId = self.dictImages.get(sReference)
        return {"sId": sId} if sId else None

    def fnTag(self, dockerClient, sImageId, sRepository, sTag):
        self.dictImages[f"{sRepository}:{sTag}"] = sImageId

    def fnUntag(self, dockerClient, sRepository, sTag):
        self.dictImages.pop(f"{sRepository}:{sTag}", None)


def _fnInstallDaemon(monkeypatch, daemonFake):
    for objModule, sAttribute, fnFake in (
        (containerManager, "fdictGetContainerStatus",
         daemonFake.fdictContainerStatus),
        (containerManager, "fjsonInspectContainer", daemonFake.fjsonInspect),
        (containerManager, "fnRemoveStopped", daemonFake.fnRemoveStopped),
        (containerManager, "fdictProbeContainerPresence",
         daemonFake.fdictProbePresence),
        (volumeManager, "fbVolumeExists",
         lambda sName: sName in daemonFake.dictVolumes),
        (volumeManager, "fdictInspectVolume", daemonFake.fdictInspectVolume),
        (volumeManager, "fnCreateLabeledVolume", daemonFake.fnCreateVolume),
        (volumeManager, "fnDestroyVolume", daemonFake.fnDestroyVolume),
        (volumeManager, "fdictReadVolumeSizes", lambda: {}),
        (volumeCopy, "fdictCopyVolumeAndVerify", daemonFake.fdictCopy),
        (imageBuilder, "flistProjectImageReferences",
         daemonFake.flistReferences),
        (disposableContainer, "fdockerCreateDisposableClient",
         lambda *aArgs: object()),
        (disposableContainer, "fdictCreateDisposableGateway",
         lambda *aArgs: {}),
        (disposableContainer, "fdictInspectImage",
         daemonFake.fdictInspectImage),
        (disposableContainer, "fnTagImage", daemonFake.fnTag),
        (disposableContainer, "fnRemoveImageTag", daemonFake.fnUntag),
        (dockerContext, "fsReadEffectiveDockerHost", lambda: ""),
    ):
        monkeypatch.setattr(objModule, sAttribute, fnFake)


def _fnRedirectHostState(monkeypatch, sHome):
    """Every directory a name's state lives in, under this world's home.

    Subdirectories of the test's own temporary directory, never a
    second temporary directory: each extra one costs every later test.
    """
    from vaibify.cli import commandBuild
    from vaibify.config import (
        containerConfig, imageOrigins, keepAliveManager, operationJournal,
    )
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sHome)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_PATH",
                        os.path.join(sHome, "registry.json"))
    monkeypatch.setattr(registryManager, "_S_LOCK_PATH",
                        os.path.join(sHome, "registry.lock"))
    sOrigins = os.path.join(sHome, "imageOrigins")
    monkeypatch.setattr(imageOrigins, "fsOriginsDirectory", lambda: (
        os.makedirs(sOrigins, exist_ok=True) or sOrigins))
    for objModule, sAttribute, sSubdirectory in (
        (operationJournal, "_S_JOURNAL_DIRECTORY", "journal"),
        (containerConfig, "_S_REPOSITORY_LISTS_DIRECTORY", "repositoryLists"),
        (commandBuild, "_S_BUILD_HASH_DIRECTORY", "cache"),
        (keepAliveManager, "_S_PID_DIRECTORY", "caffeinate"),
        (containerLock, "_S_LOCK_DIRECTORY", "locks"),
        (containerLock, "_S_LIFECYCLE_LOCK_DIRECTORY", "lifecycleLocks"),
        (pendingRenames, "_S_PENDING_RENAMES_DIRECTORY", "pendingRenames"),
    ):
        monkeypatch.setattr(
            objModule, sAttribute, os.path.join(sHome, sSubdirectory))


def _fnWriteHostFiles(sName):
    """One file of every kind the name keys (a settled journal is absent)."""
    for sKind, sPath in environmentResources.flistHostFilesOwnedBy(sName):
        if sKind == "operation journal":
            continue
        os.makedirs(os.path.dirname(sPath), exist_ok=True)
        with open(sPath, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(f"{sKind} of {sName}\n")


class World:
    """One registered, stopped container environment and its daemon."""

    def __init__(self, sRoot, monkeypatch):
        self.monkeypatch = monkeypatch
        _fnRedirectHostState(monkeypatch, os.path.join(sRoot, "home"))
        os.makedirs(os.path.join(sRoot, "home"), exist_ok=True)
        self.sDirectory = os.path.join(sRoot, "project")
        os.makedirs(self.sDirectory)
        self.sConfigPath = os.path.join(self.sDirectory, "vaibify.yml")
        with open(self.sConfigPath, "w", encoding="utf-8") as fileConfig:
            fileConfig.write(S_CONFIG)
        registryManager.fnAddProject(self.sDirectory, sMode="container")
        _fnWriteHostFiles(S_OLD)
        self.daemon = FakeDaemon()
        _fnInstallDaemon(monkeypatch, self.daemon)

    def fsConfigBytes(self):
        with open(self.sConfigPath, encoding="utf-8") as fileConfig:
            return fileConfig.read()

    def fdictHostFiles(self):
        dictFiles = {}
        for sName in (S_OLD, S_NEW):
            for sKind, sPath in environmentResources.flistHostFilesOwnedBy(
                    sName):
                if os.path.exists(sPath):
                    with open(sPath, encoding="utf-8") as fileHandle:
                        dictFiles[(sName, sKind)] = fileHandle.read()
        return dictFiles

    def ftSnapshot(self):
        return (self.daemon.fdictSnapshot(), self.fdictHostFiles(),
                self.fsConfigBytes(), registryManager.flistGetAllProjects(),
                pendingRenames.flistReadRenameJournals())


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(str(tmp_path), monkeypatch)


def _fnRename():
    environmentRename.fnRunRenameTransaction(S_OLD, S_NEW, I_PORT)


def _fnAssertCompleteUnder(world, sName):
    """Everything under ``sName``; nothing under the other name."""
    sOther = S_NEW if sName == S_OLD else S_OLD
    listNames = [d["sName"] for d in registryManager.flistGetAllProjects()]
    assert listNames == [sName]
    dictEntry = registryManager.fdictGetProject(sName)
    assert registryManager.S_RENAME_COMMIT_KEY not in dictEntry
    assert dictEntry["sContainerName"] == sName
    assert pendingRenames.flistReadRenameJournals() == []
    dictVolumes = world.daemon.dictVolumes
    assert dictVolumes[sName + "-workspace"]["dictFiles"] == {
        "data/out.csv": "1,2\n", "run.sh": "echo\n"}
    assert dictVolumes[sName + "-credentials"]["dictFiles"] == {
        "keyring": "token"}
    assert not any(s.startswith(sOther + "-") for s in dictVolumes)
    assert world.daemon.dictImages == {sName + ":latest": S_IMAGE_LATEST,
                                       sName + ":base": S_IMAGE_BASE}
    assert sOther not in world.daemon.dictContainers
    dictFiles = world.fdictHostFiles()
    assert {sKind for (sOwner, sKind) in dictFiles if sOwner == sName} == {
        sKind for sKind, _ in environmentResources.flistHostFilesOwnedBy(
            S_OLD) if sKind != "operation journal"}
    assert not [k for k in dictFiles if k[0] == sOther]
    assert world.fsConfigBytes() == (
        S_CONFIG if sName == S_OLD else world.fsConfigBytes())
    assert f"projectName: {sName}" in world.fsConfigBytes()


# ---------------------------------------------------------------------
# Reach: one list, every resource moved.
# ---------------------------------------------------------------------


def testARenameMovesEveryResourceTheNameOwns(world):
    """Nothing is left under the old name; everything is under the new.

    The resource list is the one deletion reads
    (``environmentResources``), so the two cannot disagree about what
    an environment owns.
    """
    _fnRename()
    _fnAssertCompleteUnder(world, S_NEW)
    assert S_OLD not in world.daemon.dictContainers


def testADryRunListsEveryMoveAndWritesNothing(world):
    tBefore = world.ftSnapshot()
    dictPlan = environmentRename.fdictDescribeRename(
        registryManager.fdictGetProject(S_OLD), S_NEW)
    assert world.ftSnapshot() == tBefore
    setKinds = {d["sKind"] for d in dictPlan["listMoves"]}
    assert {"registry entry", "projectName in vaibify.yml", "image tag",
            "volume (copied, then verified)"} <= setKinds
    assert {sKind for sKind, _ in environmentResources.flistHostFilesOwnedBy(
        S_OLD) if sKind != "operation journal"} <= setKinds


@pytest.mark.parametrize("sWhy,fnBreak", [
    ("running", lambda w: w.daemon.dictContainers[S_OLD].update(
        bRunning=True)),
    ("volume taken", lambda w: w.daemon.dictVolumes.update(
        {S_NEW + "-workspace": {"sCreatedAt": "x", "dictLabels": {},
                                "dictFiles": {}}})),
    ("tag taken", lambda w: w.daemon.dictImages.update(
        {S_NEW + ":latest": S_OTHER_IMAGE})),
    ("host file taken", lambda w: _fnWriteHostFiles(S_NEW)),
])
def testARefusedRenameChangesNothing(world, sWhy, fnBreak):
    fnBreak(world)
    tBefore = world.ftSnapshot()
    with pytest.raises(environmentRename.RenameRefusedError):
        _fnRename()
    assert world.ftSnapshot() == tBefore, sWhy


# ---------------------------------------------------------------------
# Crash injection, on both sides of every operation and bookkeeping write.
# ---------------------------------------------------------------------

LIST_CRASH_TARGETS = [
    (pendingRenames, "fnRecordStage"),
    (volumeManager, "fnCreateLabeledVolume"),
    (volumeCopy, "fdictCopyVolumeAndVerify"),
    (disposableContainer, "fnTagImage"),
    (environmentRename, "_fnRewriteConfig"),
    (pendingRenames, "fnAppendHostFileIntent"),
    (environmentRename, "_fnMoveOneHostFile"),
    (registryManager, "fnCommitEnvironmentRename"),
    (containerManager, "fnRemoveStopped"),
    (volumeManager, "fnDestroyVolume"),
    (disposableContainer, "fnRemoveImageTag"),
    (pendingRenames, "fnRemoveRenameJournal"),
    (registryManager, "fnClearRenameCommit"),
]


def _fiCountCalls(tmp_path, monkeypatch, objModule, sAttribute):
    """How many times an uninterrupted rename calls one operation."""
    with monkeypatch.context() as patchCount:
        World(str(tmp_path / "count"), patchCount)
        listCalls = []
        fnReal = getattr(objModule, sAttribute)
        patchCount.setattr(objModule, sAttribute, lambda *a, **k: (
            listCalls.append(1), fnReal(*a, **k))[1])
        _fnRename()
    return len(listCalls)


def _fnCrashOnCall(monkeypatch, objModule, sAttribute, iCall, bAfter):
    fnReal = getattr(objModule, sAttribute)
    listSeen = []

    def fCrashing(*aArgs, **dictKeywords):
        listSeen.append(1)
        if len(listSeen) == iCall and not bAfter:
            raise SimulatedCrash(sAttribute)
        valueReturned = fnReal(*aArgs, **dictKeywords)
        if len(listSeen) == iCall:
            raise SimulatedCrash(sAttribute)
        return valueReturned
    monkeypatch.setattr(objModule, sAttribute, fCrashing)
    # Process death runs no handler: the in-process rollback never runs.
    monkeypatch.setattr(environmentRename, "_fnRollBack", lambda *a: None)


def _fsSettledName(world):
    listNames = [d["sName"] for d in registryManager.flistGetAllProjects()]
    assert len(listNames) == 1, listNames
    return listNames[0]


@pytest.mark.parametrize(
    "objModule,sAttribute", LIST_CRASH_TARGETS,
    ids=[s for _, s in LIST_CRASH_TARGETS])
def testACrashOnEitherSideOfEveryStepRecoversToOneCompleteName(
    tmp_path, monkeypatch, objModule, sAttribute,
):
    iCalls = _fiCountCalls(tmp_path, monkeypatch, objModule, sAttribute)
    assert iCalls >= 1, f"an uninterrupted rename never calls {sAttribute}"
    for iCall in range(1, iCalls + 1):
        for bAfter in (False, True):
            sLabel = f"{sAttribute} call {iCall} {'after' if bAfter else 'before'}"
            with monkeypatch.context() as patchRun:
                world = World(str(tmp_path / f"{iCall}{bAfter}"), patchRun)
                with patchRun.context() as patchCrash:
                    _fnCrashOnCall(patchCrash, objModule, sAttribute,
                                   iCall, bAfter)
                    with pytest.raises(SimulatedCrash):
                        _fnRename()
                environmentRename.fnRecoverPendingRenamesAtStartup()
                _fnAssertCompleteUnder(world, _fsSettledName(world))
                tSettled = world.ftSnapshot()
                environmentRename.fnRecoverPendingRenamesAtStartup()
                assert world.ftSnapshot() == tSettled, sLabel


# ---------------------------------------------------------------------
# The orderings and identities, each kill-confirmed.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testACrashAfterTheRegistrySwitchRollsForward(world, monkeypatch):
    """Kills: recovery deciding committed-or-not from the journal's stage.

    The crash lands after the switch and BEFORE the stage is written,
    so a stage-reading recovery sees "copying" and cannot finish.
    """
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, registryManager,
                       "fnCommitEnvironmentRename", 1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()
    assert pendingRenames.fdictReadRenameJournal(S_OLD)["sStage"] == (
        "copying")
    environmentRename.fnRecoverPendingRenamesAtStartup()
    _fnAssertCompleteUnder(world, S_NEW)


@pytest.mark.falsification
def testACrashWhileHostFilesMoveRollsBackToTheOldName(world, monkeypatch):
    """Kills: the registry switch moved ahead of the host-file moves.

    The switch is the commit point, so nothing after it may be undone;
    a crash on the first move must therefore find the rename
    uncommitted and put every file back.
    """
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, environmentRename,
                       "_fnMoveOneHostFile", 1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()
    environmentRename.fnRecoverPendingRenamesAtStartup()
    _fnAssertCompleteUnder(world, S_OLD)


@pytest.mark.falsification
def testARollbackRestoresTheExactConfigBytes(world, monkeypatch):
    """Kills: a rollback that skips restoring vaibify.yml.

    The original carries a comment the re-render drops, so only the
    journaled bytes bring it back.
    """
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, environmentRename,
                       "_fnRewriteConfig", 1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()
    assert world.fsConfigBytes() != S_CONFIG
    environmentRename.fnRecoverPendingRenamesAtStartup()
    assert world.fsConfigBytes() == S_CONFIG


def testARollbackRefusesAConfigEditedDuringTheRename(world, monkeypatch):
    """A file neither the original nor the rename's rewrite is left alone."""
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, environmentRename,
                       "_fnRewriteConfig", 1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()
    with open(world.sConfigPath, "a", encoding="utf-8") as fileConfig:
        fileConfig.write("pythonVersion: '3.12'\n")
    sEdited = world.fsConfigBytes()
    environmentRename.fnRecoverPendingRenamesAtStartup()
    assert world.fsConfigBytes() == sEdited
    assert pendingRenames.fdictReadRenameJournal(S_OLD) is not None


@pytest.mark.falsification
def testATagRepointedAfterItWasJournaledSurvivesCleanup(world, monkeypatch):
    """Kills: cleanup that untags the old name's tags by name alone."""
    fnRealTag = disposableContainer.fnTagImage

    def fnTagThenRepoint(dockerClient, sImageId, sRepository, sTag):
        fnRealTag(dockerClient, sImageId, sRepository, sTag)
        if sTag == "base":
            world.daemon.dictImages[S_OLD + ":base"] = S_OTHER_IMAGE
    monkeypatch.setattr(disposableContainer, "fnTagImage", fnTagThenRepoint)
    _fnRename()
    assert world.daemon.dictImages[S_OLD + ":base"] == S_OTHER_IMAGE
    assert S_OLD + ":latest" not in world.daemon.dictImages


@pytest.mark.falsification
def testTheOldNamesLockFilesOutliveTheRename(world):
    """Kills: a cleanup that unlinks the old name's lock files.

    Unlinking a lock file whose flock is held lets another process lock
    a new inode at the same path, so no new code ever unlinks one.
    """
    _fnRename()
    assert os.path.exists(containerLock.fsLockPathFor(S_OLD))
    assert os.path.exists(containerLock.fsLifecycleLockPathFor(S_OLD))


@pytest.mark.falsification
def testAnSshEndpointIsTreatedAsAnotherMachine():
    """Kills: a locality predicate that answers "this machine" for all."""
    assert not dockerContext.fbEndpointIsOnThisMachine("ssh://researcher@lab")
    assert not dockerContext.fbEndpointIsOnThisMachine("tcp://10.1.2.3:2376")
    assert dockerContext.fbEndpointIsOnThisMachine("tcp://127.0.0.1:2375")
    assert dockerContext.fbEndpointIsOnThisMachine(
        "unix:///Users/researcher/.colima/default/docker.sock")


@pytest.mark.falsification
def testARemoteDaemonRefusesTheRenameNamingTheEndpoint(world, monkeypatch):
    """Kills: the remote-daemon refusal returning without asking."""
    monkeypatch.setattr(dockerContext, "fsReadEffectiveDockerHost",
                        lambda: "ssh://researcher@lab")
    tBefore = world.ftSnapshot()
    with pytest.raises(environmentRename.RenameRefusedError,
                       match="runs on another machine"):
        _fnRename()
    assert world.ftSnapshot() == tBefore


# ---------------------------------------------------------------------
# Locks: held throughout, never unlinked, respected by recovery.
# ---------------------------------------------------------------------

S_PROBE_PROGRAM = """
import sys
sys.path.insert(0, sys.argv[1])
from vaibify.config import containerLock
containerLock._S_LIFECYCLE_LOCK_DIRECTORY = sys.argv[2]
containerLock._S_LOCK_DIRECTORY = sys.argv[3]
listRefused = []
try:
    containerLock.ffileTakeLifecycleLock(sys.argv[4], "started")
except containerLock.LifecycleLockedError as error:
    listRefused.append(str(error))
try:
    containerLock.ffileAcquireReconciliationLock(sys.argv[4], 1)
except containerLock.ContainerLockedError:
    listRefused.append("session")
print(len(listRefused), listRefused[0] if listRefused else "")
"""


def _fsProbeFromAnotherProcess(sName):
    processProbe = subprocess.run(
        [sys.executable, "-c", S_PROBE_PROGRAM, fsRepositoryRoot(),
         containerLock.fsGetLifecycleLockDirectory(),
         containerLock.fsGetLockDirectory(), sName],
        capture_output=True, text=True, check=True)
    return processProbe.stdout.strip()


def testWhileARenameRunsAnotherProcessCanTakeNeitherLock(world, monkeypatch):
    eventCopying, eventRelease = threading.Event(), threading.Event()
    fnRealCopy = volumeCopy.fdictCopyVolumeAndVerify

    def fdictBlockingCopy(*aArgs):
        eventCopying.set()
        assert eventRelease.wait(30)
        return fnRealCopy(*aArgs)
    monkeypatch.setattr(volumeCopy, "fdictCopyVolumeAndVerify",
                        fdictBlockingCopy)
    threadRename = threading.Thread(target=_fnRename)
    threadRename.start()
    try:
        assert eventCopying.wait(30)
        sProbe = _fsProbeFromAnotherProcess(S_OLD)
        assert sProbe.startswith("2 "), sProbe
        assert "is being renamed by another vaibify process" in sProbe
        assert os.path.exists(containerLock.fsLockPathFor(S_OLD))
        assert os.path.exists(containerLock.fsLifecycleLockPathFor(S_OLD))
    finally:
        eventRelease.set()
        threadRename.join(30)
    _fnAssertCompleteUnder(world, S_NEW)
    assert _fsProbeFromAnotherProcess(S_OLD) == "0"


def testRecoveryLeavesARenameAnotherProcessHoldsAlone(world, monkeypatch):
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, volumeCopy, "fdictCopyVolumeAndVerify",
                       1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()
    tCrashed = world.ftSnapshot()
    with fcontextHoldLifecycleLockInAnotherProcess(S_OLD, "renamed"):
        environmentRename.fnRecoverPendingRenamesAtStartup()
        assert world.ftSnapshot() == tCrashed
    environmentRename.fnRecoverPendingRenamesAtStartup()
    _fnAssertCompleteUnder(world, S_OLD)


def testAStampWithNoJournalIsCleared(world, monkeypatch):
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, pendingRenames, "fnRemoveRenameJournal",
                       1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()
    assert registryManager.S_RENAME_COMMIT_KEY in (
        registryManager.fdictGetProject(S_NEW))
    environmentRename.fnRecoverPendingRenamesAtStartup()
    _fnAssertCompleteUnder(world, S_NEW)


# ---------------------------------------------------------------------
# The refusals, each driven through the entry point that carries it.
# ---------------------------------------------------------------------


def _fnLeaveAnUnfinishedRename(monkeypatch):
    with monkeypatch.context() as patchCrash:
        _fnCrashOnCall(patchCrash, volumeCopy, "fdictCopyVolumeAndVerify",
                       1, True)
        with pytest.raises(SimulatedCrash):
            _fnRename()


@pytest.mark.falsification
def testAnUnfinishedRenameRefusesBothNames(world, monkeypatch):
    """Kills: the pending-rename refusal matching no journal."""
    _fnLeaveAnUnfinishedRename(monkeypatch)
    for sName in (S_OLD, S_NEW):
        with pytest.raises(environmentRename.RenameRefusedError,
                           match="has not finished"):
            environmentRename.fnRefuseWhileRenamePending(sName)
    environmentRename.fnRefuseWhileRenamePending("unrelated-environment")


@pytest.fixture
def clientHub(world):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = I_PORT
    fnRegisterRegistryRoutes(app, {"require": lambda *a: None, "docker": None})
    return TestClient(app)


@pytest.mark.falsification
def testTheGuiStartRefusesWhileARenameIsUnfinished(
    world, clientHub, monkeypatch,
):
    """Kills: the start route skipping the pending-rename refusal."""
    from vaibify.gui import startReservation
    listBegun = []

    async def ftRecordBegin(*aArgs, **dictKeywords):
        listBegun.append(aArgs)
        return 202, {}
    monkeypatch.setattr(startReservation, "ftBeginStart", ftRecordBegin)
    _fnLeaveAnUnfinishedRename(monkeypatch)
    response = clientHub.post(f"/api/containers/{S_OLD}/start")
    assert response.status_code == 409
    assert "has not finished" in response.json()["detail"]["sMessage"]
    assert listBegun == []


@pytest.mark.falsification
def testTheCliStartRefusesWhileARenameIsUnfinished(
    world, monkeypatch, tmp_path,
):
    """Kills: CLI start skipping the pending-rename refusal."""
    from click.testing import CliRunner
    from tests.testLifecycleLock import _fnPrepareCliStart
    from vaibify.cli.commandStart import fnStartCommand
    _fnLeaveAnUnfinishedRename(monkeypatch)
    listStarted = _fnPrepareCliStart(monkeypatch, tmp_path)
    result = CliRunner().invoke(fnStartCommand, ["--detach"])
    assert result.exit_code == 1, result.output
    assert "has not finished" in result.output
    assert listStarted == []


@pytest.mark.falsification
def testARenameClaimingTheSameNewNameIsRefused(world):
    """Kills: the check for another unfinished transaction on either name."""
    dictOther = pendingRenames.fdictBuildRenameJournal(
        "another-environment", S_NEW, "/nowhere/vaibify.yml", b"", "",
        {"sMode": "container"}, {})
    pendingRenames.fnWriteRenameJournal(dictOther)
    tBefore = world.ftSnapshot()
    with pytest.raises(environmentRename.RenameRefusedError,
                       match="has not finished"):
        _fnRename()
    assert world.ftSnapshot() == tBefore


def _fdictPostRename(clientHub, dictBody, dictHeaders=None):
    return clientHub.post(f"/api/registry/{S_OLD}/rename", json=dictBody,
                          headers=dictHeaders or {})


def testTheRouteRefusesTheAgentLane(world, clientHub):
    response = _fdictPostRename(
        clientHub, {"sNewName": S_NEW, "bDryRun": True},
        {"X-Vaibify-Session": "an-agent-token"})
    assert response.status_code == 403


def testTheRouteRefusesANameDockerCannotCarry(world, clientHub):
    response = _fdictPostRename(
        clientHub, {"sNewName": "has a space", "bDryRun": True})
    assert response.status_code == 400


def testTheRouteRefusesARunningEnvironment(world, clientHub):
    world.daemon.dictContainers[S_OLD]["bRunning"] = True
    response = _fdictPostRename(clientHub, {"sNewName": S_NEW})
    assert response.status_code == 409
    assert "Stop it, then rename it" in response.json()["detail"]["sMessage"]


def testTheRouteDryRunListsTheMovesAndChangesNothing(world, clientHub):
    tBefore = world.ftSnapshot()
    response = _fdictPostRename(clientHub, {"sNewName": S_NEW})
    assert response.status_code == 200, response.text
    assert response.json()["listMoves"]
    assert world.ftSnapshot() == tBefore


def testTheRouteApplyRunsTheRenameOnTheHubAndAnswers202(
    world, clientHub, monkeypatch,
):
    listBegun = []
    monkeypatch.setattr(
        environmentRename, "fdictBeginRename",
        lambda sOld, sNew, iPort: listBegun.append((sOld, sNew, iPort))
        or {"sOldName": sOld, "sNewName": sNew, "bLive": True})
    response = _fdictPostRename(
        clientHub, {"sNewName": S_NEW, "bDryRun": False})
    assert response.status_code == 202
    assert listBegun == [(S_OLD, S_NEW, I_PORT)]


def testAWorkerRunsTheRenameAndTheListingFollowsIt(world, monkeypatch):
    """The hub-owned worker renames, and the live view clears after."""
    environmentRename.fdictBeginRename(S_OLD, S_NEW, I_PORT)
    for _ in range(300):
        if not environmentRename.fbHubHoldsLiveRename():
            break
        threading.Event().wait(0.02)
    assert not environmentRename.fbHubHoldsLiveRename()
    _fnAssertCompleteUnder(world, S_NEW)
    listContainers = [{"sName": S_NEW}]
    environmentRename.fnAnnotateListingWithPendingRenames(listContainers)
    assert listContainers == [{"sName": S_NEW}]
