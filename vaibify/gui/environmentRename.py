"""Renaming an environment: every resource its name keys, moved as one.

An environment's name keys its container, its two volumes, every tag in
the image repository named after it, its ``vaibify.yml`` ``projectName``,
its host files under ``~/.vaibify`` and its registry entry (the set is
``environmentResources``). A rename moves all of them, allowed only
while the environment is stopped (rulings D1, D2), and the dashboard
never shows a stale name (D3): the tile reads "Renaming to ..." until
everything has moved, then the new name at once.

THE TRANSACTION follows the containerization commit's precedent
(``conversionLifecycle``): a durable journal of original bytes written
before anything changes (``pendingRenames``), fixed-order non-blocking
locks held throughout, ONE atomic registry write as the commit point,
and recovery that rolls back or forward at hub startup. In order:

0. reserve the lifecycle locks, old name then new, then the session
   flocks, old then new, and re-run the checks;
1. journal;
2. create each new volume, labeled with the transaction, and copy;
3. verify each copy by its content manifest (``volumeCopy``);
4. point each new tag at the journaled image ID, never at a tag;
5. rewrite ``projectName``;
6. move each host file, its intent journaled first;
7. COMMIT: one registry write renames the entry and stamps it;
8. remove the old container, volumes and tags, each only if it is still
   what the journal recorded;
9. delete the journal, then clear the stamp;
10. release the locks.

Recovery decides committed-or-not from the REGISTRY alone -- an entry
under the new name carrying this transaction's stamp -- and reads which
host files moved from the FILESYSTEM. The journal's ``sStage`` is for
display and nothing branches on it. A rename whose state matches
neither reading is left exactly as found and reported: guessing which
way to finish it could lose a researcher's data.

A rename is refused on a Docker daemon that is not on this machine
(ruling 2026-10-28): another machine's vaibify may be using it, and
renaming here would break that hub's copy of the environment.
"""

__all__ = [
    "RenameCleanupIncompleteError",
    "RenameRefusedError",
    "S_OPERATION",
    "fbHubHoldsLiveRename",
    "fdictBeginRename",
    "fdictDescribeRename",
    "fnAnnotateListingWithPendingRenames",
    "fnRecoverPendingRenamesAtStartup",
    "fnRefuseWhileRenamePending",
    "fnRegisterHubStartup",
    "fnRunPreconditionChecks",
    "fnRunRenameTransaction",
]

import base64
import hashlib
import logging
import os
import threading

from vaibify.config import (
    containerLock, pendingConversions, pendingRenames, registryManager,
)
from vaibify.config.durableFile import fnSyncDirectory, fnWriteFileDurably
from vaibify.config.projectConfig import fbaRenderConfigWithProjectName
from vaibify.docker import (
    containerManager, disposableContainer, dockerContext, imageBuilder,
    volumeCopy, volumeManager,
)
from vaibify.gui import environmentResources

logger = logging.getLogger("vaibify")

S_OPERATION = "renamed"
S_STAGE_COPYING = "copying"
S_STAGE_FINISHING = "finishing"
_S_VOLUME_DESCRIPTIONS = ("workspace", "credentials")

_LOCK_LIVE = threading.Lock()
_DICT_LIVE = {}
_DICT_LAST_FAILURE = {}


class RenameRefusedError(Exception):
    """A rename that cannot run, with the status a route answers."""

    def __init__(self, iStatusCode, sMessage):
        super().__init__(sMessage)
        self.iStatusCode = iStatusCode


# ---------------------------------------------------------------------
# The checks: every one runs before anything is written, at the route
# and again once the locks are held.
# ---------------------------------------------------------------------


def _fbIsHost(dictProject):
    return dictProject.get("sMode") == "host"


def _fnRefuseRemoteDaemon():
    sEndpoint = dockerContext.fsReadEffectiveDockerHost()
    if dockerContext.fbEndpointIsOnThisMachine(sEndpoint):
        return
    raise RenameRefusedError(409, (
        f"This environment's Docker runs on another machine ({sEndpoint}). "
        "Another vaibify hub may be using it, and renaming here would "
        "break that hub's copy of the environment. Rename it from a hub "
        "on that machine."
    ))


def flistDescribeWhatHoldsTheName(sNewName, bHost):
    """Return every resource already keyed by ``sNewName``, described."""
    listHeld = []
    if registryManager.fdictGetProject(sNewName) is not None:
        listHeld.append("a registered project")
    for sKind, sPath in environmentResources.flistHostFilesOwnedBy(sNewName):
        if os.path.lexists(sPath):
            listHeld.append(f"the {sKind} at {sPath}")
    if bHost:
        return listHeld
    if containerManager.fdictGetContainerStatus(sNewName)["bExists"]:
        listHeld.append("a Docker container")
    for sVolume in environmentResources.flistVolumeNamesOwnedBy(sNewName):
        if volumeManager.fbVolumeExists(sVolume):
            listHeld.append(f"the volume {sVolume}")
    for sReference in imageBuilder.flistProjectImageReferences(sNewName):
        listHeld.append(f"the image tag {sReference}")
    return listHeld


def _fnRefusePendingTransactions(sOldName, sNewName):
    setNames = {sOldName, sNewName}
    for dictRecord in pendingConversions.flistReadConversionRecords():
        if setNames & {dictRecord["sHostName"], dictRecord["sNewName"]}:
            raise RenameRefusedError(409, (
                f"A containerization of '{dictRecord['sHostName']}' to "
                f"'{dictRecord['sNewName']}' has not finished; finish or "
                "cancel it first."))
    for dictJournal in pendingRenames.flistReadRenameJournals():
        if setNames & {dictJournal["sOldName"], dictJournal["sNewName"]}:
            raise RenameRefusedError(409, (
                f"A rename of '{dictJournal['sOldName']}' to "
                f"'{dictJournal['sNewName']}' has not finished."))


def fnRunPreconditionChecks(dictProject, sNewName):
    """Refuse a remote daemon, a taken name, a pending one, a running one."""
    sOldName, bHost = dictProject["sName"], _fbIsHost(dictProject)
    if sNewName == sOldName:
        raise RenameRefusedError(400, f"'{sOldName}' already has that name.")
    if not bHost:
        _fnRefuseRemoteDaemon()
    listHeld = flistDescribeWhatHoldsTheName(sNewName, bHost)
    if listHeld:
        raise RenameRefusedError(409, (
            f"The name '{sNewName}' is already taken on this machine: "
            + "; ".join(listHeld) + "."))
    _fnRefusePendingTransactions(sOldName, sNewName)
    if not bHost and containerManager.fdictGetContainerStatus(
            dictProject["sContainerName"])["bRunning"]:
        raise RenameRefusedError(409, (
            f"'{sOldName}' is running. Stop it, then rename it."))


def fnRefuseWhileRenamePending(sName):
    """Raise ``RenameRefusedError`` while an unfinished rename names it."""
    for dictJournal in pendingRenames.flistReadRenameJournals():
        if sName in (dictJournal["sOldName"], dictJournal["sNewName"]):
            raise RenameRefusedError(409, (
                f"A rename of '{dictJournal['sOldName']}' to "
                f"'{dictJournal['sNewName']}' has not finished. Restart "
                "vaibify so it can finish or undo it, or run 'vaibify "
                "reconcile'."))


# ---------------------------------------------------------------------
# What will move: the dry run, and the journal's Docker identities.
# ---------------------------------------------------------------------


def _flistVolumePairs(sOldName, sNewName):
    """Pair each existing old volume with its new name, identity and size."""
    dictSizes = volumeManager.fdictReadVolumeSizes()
    listPairs = []
    for sDescription, sFrom, sTo in zip(
            _S_VOLUME_DESCRIPTIONS,
            environmentResources.flistVolumeNamesOwnedBy(sOldName),
            environmentResources.flistVolumeNamesOwnedBy(sNewName)):
        dictVolume = volumeManager.fdictInspectVolume(sFrom)
        if dictVolume is not None:
            listPairs.append({"sDescription": sDescription, "sFrom": sFrom,
                              "sTo": sTo,
                              "sCreatedAt": dictVolume["sCreatedAt"],
                              "sSize": dictSizes.get(sFrom, "")})
    return listPairs


def _flistImageTags(dockerClient, sOldName):
    """Return each tag of the old repository with the image it names."""
    listTags = []
    for sReference in imageBuilder.flistProjectImageReferences(sOldName):
        dictImage = disposableContainer.fdictInspectImage(
            dockerClient, sReference)
        if dictImage:
            listTags.append({"sTag": sReference.rsplit(":", 1)[1],
                             "sImageId": dictImage["sId"]})
    return listTags


def _fdictCollectDockerIdentities(dictProject, sNewName, dockerClient):
    """Every Docker object the rename moves, by identity."""
    if _fbIsHost(dictProject):
        return {}
    sOldName = dictProject["sName"]
    dictContainer = containerManager.fjsonInspectContainer(
        dictProject["sContainerName"])
    listTags = _flistImageTags(dockerClient, sOldName)
    dictLatest = next((d for d in listTags if d["sTag"] == "latest"),
                      listTags[0] if listTags else {})
    return {
        "sContainerId": str(dictContainer.get("Id") or ""),
        "sImageId": dictLatest.get("sImageId", ""),
        "listVolumes": _flistVolumePairs(sOldName, sNewName),
        "listTags": listTags,
    }


def _flistHostFileMoves(sOldName, sNewName):
    """Pair each existing old host file with the path it moves to."""
    dictNew = dict(environmentResources.flistHostFilesOwnedBy(sNewName))
    return [
        {"sKind": sKind, "sFrom": sPath, "sTo": dictNew[sKind]}
        for sKind, sPath in environmentResources.flistHostFilesOwnedBy(
            sOldName)
        if os.path.lexists(sPath) and sKind in dictNew
    ]


def fdictDescribeRename(dictProject, sNewName):
    """Return what a rename would move and how large the copies are."""
    sOldName = dictProject["sName"]
    listMoves = [{"sKind": "registry entry", "sFrom": sOldName,
                  "sTo": sNewName},
                 {"sKind": "projectName in vaibify.yml", "sFrom": sOldName,
                  "sTo": sNewName}]
    listMoves += _flistHostFileMoves(sOldName, sNewName)
    dictSizes = {}
    if not _fbIsHost(dictProject):
        dictIdentities = _fdictCollectDockerIdentities(
            dictProject, sNewName,
            disposableContainer.fdockerCreateDisposableClient())
        for dictPair in dictIdentities["listVolumes"]:
            listMoves.append({"sKind": "volume (copied, then verified)",
                              "sFrom": dictPair["sFrom"], "sTo": dictPair["sTo"]})
            dictSizes[dictPair["sFrom"]] = dictPair["sSize"]
        listMoves += [{"sKind": "image tag", "sFrom": f"{sOldName}:{d['sTag']}",
                       "sTo": f"{sNewName}:{d['sTag']}"}
                      for d in dictIdentities["listTags"]]
        if dictIdentities["sContainerId"]:
            listMoves.append({"sKind": "stopped container (removed; the "
                              "next start creates it under the new name)",
                              "sFrom": sOldName, "sTo": sNewName})
    return {"listMoves": listMoves, "dictVolumeSizes": dictSizes,
            "sTimeNote": ("Each volume is copied and then verified file by "
                          "file, which takes time in proportion to its size.")}


# ---------------------------------------------------------------------
# The transaction.
# ---------------------------------------------------------------------


def _ftReserve(sOldName, sNewName, iPort):
    """Take both lifecycle locks, then both session flocks, non-blocking."""
    listLifecycle = containerLock.flistTakeLifecycleLocks(
        [sOldName, sNewName], S_OPERATION)
    listSession = []
    try:
        for sName in (sOldName, sNewName):
            listSession.append(
                containerLock.ffileAcquireContainerLock(sName, iPort))
    except BaseException:
        _fnRelease((listLifecycle, listSession))
        raise
    return (listLifecycle, listSession)


def _fnRelease(tLocks):
    """Release the session flocks, then the lifecycle locks."""
    listLifecycle, listSession = tLocks
    for fileHandle in reversed(listSession):
        containerLock.fnReleaseContainerLock(fileHandle)
    containerLock.fnReleaseLifecycleLocks(listLifecycle)


def _fdictWriteJournal(dictProject, sNewName, dockerClient):
    """Step 1: record the original bytes and every identity, durably."""
    with open(dictProject["sConfigPath"], "rb") as fileConfig:
        baConfig = fileConfig.read()
    baRewritten = fbaRenderConfigWithProjectName(baConfig, sNewName)
    dictJournal = pendingRenames.fdictBuildRenameJournal(
        dictProject["sName"], sNewName, dictProject["sConfigPath"], baConfig,
        hashlib.sha256(baRewritten).hexdigest(), dictProject,
        _fdictCollectDockerIdentities(dictProject, sNewName, dockerClient))
    pendingRenames.fnWriteRenameJournal(dictJournal)
    return dictJournal


def _fnCopyVolumes(dictJournal, dockerClient):
    """Steps 2-3: create each labeled volume, copy it, verify it."""
    if dictJournal["listVolumes"] and not dictJournal["sImageId"]:
        raise RenameRefusedError(409, (
            f"'{dictJournal['sOldName']}' has volumes but no image to copy "
            "them with; build it once, then rename it."))
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerClient, dictJournal["sOldName"])
    for dictPair in dictJournal["listVolumes"]:
        volumeManager.fnCreateLabeledVolume(dictPair["sTo"], {
            pendingRenames.S_RENAME_TRANSACTION_LABEL:
                dictJournal["sTransactionId"]})
        volumeCopy.fdictCopyVolumeAndVerify(
            dictGateway, dictJournal["sImageId"], dictPair["sFrom"],
            dictPair["sTo"], dictPair["sDescription"])


def _fnTagNewImages(dictJournal, dockerClient):
    """Step 4: each new tag names the journaled image ID."""
    for dictTag in dictJournal["listTags"]:
        disposableContainer.fnTagImage(
            dockerClient, dictTag["sImageId"], dictJournal["sNewName"],
            dictTag["sTag"])


def _fnRewriteConfig(dictJournal):
    """Step 5: write the rewrite whose digest the journal recorded."""
    baOriginal = base64.b64decode(dictJournal["sConfigBase64"])
    baRewritten = fbaRenderConfigWithProjectName(
        baOriginal, dictJournal["sNewName"])
    iMode = os.stat(dictJournal["sConfigPath"]).st_mode & 0o777
    fnWriteFileDurably(dictJournal["sConfigPath"], baRewritten, iMode=iMode)


def _fnMoveOneHostFile(sFrom, sTo):
    os.makedirs(os.path.dirname(sTo), exist_ok=True)
    os.rename(sFrom, sTo)
    fnSyncDirectory(os.path.dirname(sFrom))
    fnSyncDirectory(os.path.dirname(sTo))


def _fnMoveHostFiles(dictJournal, sFromName, sToName):
    """Step 6: for each file, the intent durably first, then the move."""
    for dictMove in _flistHostFileMoves(sFromName, sToName):
        pendingRenames.fnAppendHostFileIntent(
            dictJournal, dictMove["sFrom"], dictMove["sTo"])
        _fnMoveOneHostFile(dictMove["sFrom"], dictMove["sTo"])


def _fnRemoveOldDockerObjects(dictJournal, dockerClient):
    """Step 8: container, then volumes, then tags, each only if unchanged."""
    if dictJournal["sContainerId"]:
        _fnRemoveContainerIfStillTheJournaledOne(
            dictJournal["sOldName"], dictJournal["sContainerId"])
    for dictPair in dictJournal["listVolumes"]:
        dictVolume = volumeManager.fdictInspectVolume(dictPair["sFrom"])
        if dictVolume and dictVolume["sCreatedAt"] == dictPair["sCreatedAt"]:
            volumeManager.fnDestroyVolume(dictPair["sFrom"])
    for dictTag in dictJournal["listTags"]:
        _fnRemoveTagIfStillOurs(
            dockerClient, dictJournal["sOldName"], dictTag)


def _fnRemoveContainerIfStillTheJournaledOne(sOldName, sContainerId):
    """Remove the old container only if it is the incarnation journaled.

    The removal itself is deletion's, which proves the container gone
    rather than trusting the daemon's exit code; the identity check by
    id is what keeps it from removing a container made since.
    """
    from vaibify.gui import environmentDeletion
    dictContainer = containerManager.fjsonInspectContainer(sOldName)
    if dictContainer.get("Id") != sContainerId:
        return
    listFailures = []
    if not environmentDeletion._fbRemoveContainer(sOldName, listFailures):
        raise RuntimeError("; ".join(listFailures))


def _fnRemoveTagIfStillOurs(dockerClient, sRepository, dictTag):
    """Untag only a tag still naming the journaled image; report one moved."""
    sReference = f"{sRepository}:{dictTag['sTag']}"
    dictImage = disposableContainer.fdictInspectImage(dockerClient, sReference)
    if not dictImage:
        return
    if dictImage["sId"] != dictTag["sImageId"]:
        logger.warning(
            "Rename left %s alone: it was repointed after the rename began",
            sReference)
        return
    disposableContainer.fnRemoveImageTag(
        dockerClient, sRepository, dictTag["sTag"])


def _fnFinish(dictJournal, dockerClient):
    """Steps 8-9, which a roll-forward repeats safely."""
    pendingRenames.fnRecordStage(dictJournal, S_STAGE_FINISHING)
    if not _fbIsHostJournal(dictJournal):
        _fnRemoveOldDockerObjects(dictJournal, dockerClient)
    pendingRenames.fnRemoveRenameJournal(dictJournal["sOldName"])
    registryManager.fnClearRenameCommit(
        dictJournal["sNewName"], dictJournal["sTransactionId"])


def _fbIsHostJournal(dictJournal):
    return dictJournal["dictRegistryEntry"].get("sMode") == "host"


class RenameCleanupIncompleteError(Exception):
    """The rename COMMITTED; removing the old name's objects did not finish."""


def fnRunRenameTransaction(sOldName, sNewName, iPort):
    """Run steps 0-10; undo this transaction's work if it fails before commit.

    The registry entry is read only once the locks are held, so the
    rename acts on the environment as it is, not as a request saw it.
    """
    tLocks = _ftReserve(sOldName, sNewName, iPort)
    try:
        dictProject = registryManager.fdictGetProject(sOldName)
        if dictProject is None:
            raise RenameRefusedError(404, f"'{sOldName}' is not registered.")
        fnRunPreconditionChecks(dictProject, sNewName)
        dockerClient = (None if _fbIsHost(dictProject) else
                        disposableContainer.fdockerCreateDisposableClient())
        dictJournal = _fdictWriteJournal(dictProject, sNewName, dockerClient)
        _fnRunUpToTheCommit(dictJournal, dockerClient)
        try:
            _fnFinish(dictJournal, dockerClient)
        except Exception as error:
            raise RenameCleanupIncompleteError(str(error)) from error
    finally:
        _fnRelease(tLocks)


def _fnRunUpToTheCommit(dictJournal, dockerClient):
    """Steps 2-7; any failure rolls this transaction back, then raises."""
    try:
        if not _fbIsHostJournal(dictJournal):
            pendingRenames.fnRecordStage(dictJournal, S_STAGE_COPYING)
            _fnCopyVolumes(dictJournal, dockerClient)
            _fnTagNewImages(dictJournal, dockerClient)
        _fnRewriteConfig(dictJournal)
        _fnMoveHostFiles(
            dictJournal, dictJournal["sOldName"], dictJournal["sNewName"])
        registryManager.fnCommitEnvironmentRename(
            dictJournal["sOldName"], dictJournal["sNewName"],
            dictJournal["sTransactionId"])
    except BaseException:
        if not _fbCommitted(dictJournal):
            _fnRollBack(dictJournal, dockerClient)
        raise


# ---------------------------------------------------------------------
# Recovery: roll back what did not commit, roll forward what did.
# ---------------------------------------------------------------------


class _RenameNeedsAttention(Exception):
    """A state recovery cannot settle without guessing; left as found."""


def _fbCommitted(dictJournal):
    dictEntry = registryManager.fdictGetProject(dictJournal["sNewName"]) or {}
    dictStamp = dictEntry.get(registryManager.S_RENAME_COMMIT_KEY) or {}
    return dictStamp.get("sTransactionId") == dictJournal["sTransactionId"]


def _fbNotCommitted(dictJournal):
    dictEntry = registryManager.fdictGetProject(dictJournal["sOldName"])
    if dictEntry is None:
        return False
    dictStamp = dictEntry.get(registryManager.S_RENAME_COMMIT_KEY) or {}
    return dictStamp.get("sTransactionId") != dictJournal["sTransactionId"]


def _fsHostFileState(sPath, sOtherPath):
    """Read a move from the filesystem: moved, not moved, conflict, lost."""
    bHere, bThere = os.path.lexists(sPath), os.path.lexists(sOtherPath)
    if bHere and bThere:
        return "conflict"
    if not bHere and not bThere:
        return "lost"
    return "not moved" if bHere else "moved"


def _fnUndoHostFileMoves(dictJournal):
    for dictIntent in reversed(dictJournal["listHostFileIntents"]):
        sState = _fsHostFileState(dictIntent["sFrom"], dictIntent["sTo"])
        if sState == "moved":
            _fnMoveOneHostFile(dictIntent["sTo"], dictIntent["sFrom"])
        elif sState != "not moved":
            raise _RenameNeedsAttention(
                f"the host file {dictIntent['sFrom']} is {sState}")


def _fnRestoreConfig(dictJournal):
    """Put back the journaled bytes, only over the rename's own rewrite."""
    baOriginal = base64.b64decode(dictJournal["sConfigBase64"])
    with open(dictJournal["sConfigPath"], "rb") as fileConfig:
        baCurrent = fileConfig.read()
    if baCurrent == baOriginal:
        return
    if hashlib.sha256(baCurrent).hexdigest() != (
            dictJournal["sRewrittenConfigSha256"]):
        raise _RenameNeedsAttention(
            f"{dictJournal['sConfigPath']} was edited during the rename")
    iMode = os.stat(dictJournal["sConfigPath"]).st_mode & 0o777
    fnWriteFileDurably(dictJournal["sConfigPath"], baOriginal, iMode=iMode)


def _fnRemoveThisTransactionsDockerObjects(dictJournal, dockerClient):
    """Remove the volumes this transaction labeled and the tags it made."""
    for dictPair in dictJournal["listVolumes"]:
        dictVolume = volumeManager.fdictInspectVolume(dictPair["sTo"])
        if dictVolume and dictVolume["dictLabels"].get(
                pendingRenames.S_RENAME_TRANSACTION_LABEL) == (
                dictJournal["sTransactionId"]):
            volumeManager.fnDestroyVolume(dictPair["sTo"])
    for dictTag in dictJournal["listTags"]:
        _fnRemoveTagIfStillOurs(
            dockerClient, dictJournal["sNewName"], dictTag)


def _fnRollBack(dictJournal, dockerClient):
    """Undo exactly this transaction's artifacts; the journal goes last."""
    _fnUndoHostFileMoves(dictJournal)
    _fnRestoreConfig(dictJournal)
    if not _fbIsHostJournal(dictJournal):
        _fnRemoveThisTransactionsDockerObjects(dictJournal, dockerClient)
    pendingRenames.fnRemoveRenameJournal(dictJournal["sOldName"])


def _fnRollForward(dictJournal, dockerClient):
    """Finish a committed rename: unmoved files, then steps 8-9."""
    for dictIntent in dictJournal["listHostFileIntents"]:
        sState = _fsHostFileState(dictIntent["sFrom"], dictIntent["sTo"])
        if sState == "not moved":
            _fnMoveOneHostFile(dictIntent["sFrom"], dictIntent["sTo"])
        elif sState != "moved":
            raise _RenameNeedsAttention(
                f"the host file {dictIntent['sFrom']} is {sState}")
    _fnMoveHostFiles(dictJournal, dictJournal["sOldName"],
                     dictJournal["sNewName"])
    _fnFinish(dictJournal, dockerClient)


def _ftTakeRecoveryLocks(dictJournal):
    """The transaction's four locks, without the quarantine gate."""
    sOldName, sNewName = dictJournal["sOldName"], dictJournal["sNewName"]
    listLifecycle = containerLock.flistTakeLifecycleLocks(
        [sOldName, sNewName], S_OPERATION)
    listSession = []
    try:
        for sName in (sOldName, sNewName):
            listSession.append(
                containerLock.ffileAcquireReconciliationLock(sName, 0))
    except BaseException:
        _fnRelease((listLifecycle, listSession))
        raise
    return (listLifecycle, listSession)


def _fnRecoverOneJournal(dictJournal):
    """Leave a live rename alone; settle a dead one or report it."""
    try:
        tLocks = _ftTakeRecoveryLocks(dictJournal)
    except (containerLock.LifecycleLockedError,
            containerLock.ContainerLockedError):
        return
    try:
        dictFresh = pendingRenames.fdictReadRenameJournal(
            dictJournal["sOldName"])
        if dictFresh is None:
            return
        _fnSettleUnderLocks(dictFresh)
    finally:
        _fnRelease(tLocks)


def _fnSettleUnderLocks(dictJournal):
    dockerClient = (None if _fbIsHostJournal(dictJournal) else
                    disposableContainer.fdockerCreateDisposableClient())
    if _fbCommitted(dictJournal):
        _fnRollForward(dictJournal, dockerClient)
    elif _fbNotCommitted(dictJournal):
        _fnRollBack(dictJournal, dockerClient)
    else:
        raise _RenameNeedsAttention(
            "the registry holds neither the old entry nor the committed one")


def _fnClearStampsWithoutAJournal():
    for dictEntry in registryManager.flistGetAllProjects():
        dictStamp = dictEntry.get(registryManager.S_RENAME_COMMIT_KEY)
        if dictStamp and pendingRenames.fdictReadRenameJournal(
                dictStamp["sFromName"]) is None:
            registryManager.fnClearRenameCommit(
                dictEntry["sName"], dictStamp["sTransactionId"])


def fnRecoverPendingRenamesAtStartup():
    """Settle every rename a dead hub left; idempotent; never raises."""
    for dictJournal in pendingRenames.flistReadRenameJournals():
        try:
            _fnRecoverOneJournal(dictJournal)
        except _RenameNeedsAttention as error:
            logger.error(
                "The rename of %r to %r needs attention and was left as "
                "found: %s", dictJournal["sOldName"], dictJournal["sNewName"],
                error)
        except Exception:  # noqa: BLE001 -- one journal must not stop the hub
            logger.exception(
                "Could not recover the rename of %r", dictJournal["sOldName"])
    _fnClearStampsWithoutAJournal()


# ---------------------------------------------------------------------
# The hub: a worker that outlives the tab, the listing, the veto.
# ---------------------------------------------------------------------


def fdictBeginRename(sOldName, sNewName, iPort):
    """Start the rename on a hub-owned thread; return its listing view.

    Hub-owned because a copy can take a long time and must not stop
    when the researcher closes the tab that asked for it.
    """
    with _LOCK_LIVE:
        _DICT_LIVE[sOldName] = {"sNewName": sNewName}
        _DICT_LAST_FAILURE.pop(sOldName, None)
    threading.Thread(
        target=_fnRunRenameWorker, args=(sOldName, sNewName, iPort),
        name=f"vaibifyRename-{sOldName}", daemon=True,
    ).start()
    return {"sOldName": sOldName, "sNewName": sNewName, "bLive": True}


def _fsDescribeFailure(sOldName, sNewName, error):
    """Say what a failed rename left, which depends on how it failed."""
    if isinstance(error, RenameCleanupIncompleteError):
        return (f"'{sOldName}' is now '{sNewName}', but removing the old "
                f"name's copies did not finish ({error}); vaibify finishes "
                "that the next time it starts.")
    if isinstance(error, _RenameNeedsAttention):
        return (f"Renaming '{sOldName}' to '{sNewName}' failed and could not "
                f"be undone safely ({error}); it was left as found. Run "
                "'vaibify reconcile' to settle it.")
    return (f"Renaming '{sOldName}' to '{sNewName}' did not finish, and "
            f"nothing was changed: {error}")


def _fnRunRenameWorker(sOldName, sNewName, iPort):
    try:
        fnRunRenameTransaction(sOldName, sNewName, iPort)
    except Exception as error:  # noqa: BLE001 -- reported on the tile
        logger.exception("Renaming %r to %r failed", sOldName, sNewName)
        with _LOCK_LIVE:
            _DICT_LAST_FAILURE[sOldName] = _fsDescribeFailure(
                sOldName, sNewName, error)
    finally:
        with _LOCK_LIVE:
            _DICT_LIVE.pop(sOldName, None)


def fbHubHoldsLiveRename():
    """True while this hub is renaming any environment: the idle veto."""
    with _LOCK_LIVE:
        return bool(_DICT_LIVE)


def _fdictPendingRenameViews():
    """Map every name a rename touches to what its tile should say."""
    dictViews = {}
    with _LOCK_LIVE:
        setLive = set(_DICT_LIVE)
    for dictJournal in pendingRenames.flistReadRenameJournals():
        dictView = {
            "sOldName": dictJournal["sOldName"],
            "sNewName": dictJournal["sNewName"],
            "sStage": dictJournal.get("sStage", ""),
            "bLive": dictJournal["sOldName"] in setLive,
            "listCopySizes": [dictPair.get("sSize", "") for dictPair in
                              dictJournal.get("listVolumes", [])],
        }
        for sName in (dictJournal["sOldName"], dictJournal["sNewName"]):
            dictViews[sName] = dictView
    return dictViews


def fnAnnotateListingWithPendingRenames(listContainers):
    """Carry each live or unfinished rename, and a last failure, onto tiles."""
    dictViews = _fdictPendingRenameViews()
    with _LOCK_LIVE:
        dictFailures = dict(_DICT_LAST_FAILURE)
        dictStarting = {sOld: dict(d) for sOld, d in _DICT_LIVE.items()}
    for dictProject in listContainers:
        sName = dictProject.get("sName") or ""
        if sName in dictViews:
            dictProject["dictPendingRename"] = dictViews[sName]
        elif sName in dictStarting:
            dictProject["dictPendingRename"] = {
                "sOldName": sName, "sNewName": dictStarting[sName]["sNewName"],
                "sStage": "starting", "bLive": True}
        if sName in dictFailures:
            dictProject["sRenameFailure"] = dictFailures[sName]


def fnRegisterHubStartup(app, dictCtx):
    """Recover unfinished renames before any tile is offered."""
    import asyncio
    from . import serverLifespan
    del dictCtx

    async def fnRecoverAtStartup(app):
        del app
        await asyncio.to_thread(fnRecoverPendingRenamesAtStartup)

    async def fnNothingToStop(app):
        del app

    serverLifespan.fnRegisterLifespanTask(
        app, fnRecoverAtStartup, fnNothingToStop)
