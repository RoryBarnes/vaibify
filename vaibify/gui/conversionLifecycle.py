"""A pending containerization, owned by the hub from Convert to commit.

"Convert and obtain" writes a conversion record
(:mod:`vaibify.config.pendingConversions`) and hands it here. This
module runs the acquisition on a thread the HUB owns -- never a browser
tab: closing the tab or the browser is not a signal, the download goes
on, and a reopened tab re-attaches from the host project's tile -- and
moves the record through its states:

* ``obtaining``: the published chain is running (download, prove, stack,
  staging tag). Cancel is honored within a second.
* ``waiting``: the acquisition stopped and wants the researcher's
  decision. Try again starts a fresh retry budget and resumes the
  partial; Stay in host mode deletes it. Nothing is running.
* ``ready``: the image is staged under ``<newName>:vaibify-staging``.
  The commit runs at once unless the host project is open somewhere,
  in which case the tile offers Finish.
* ``interrupted`` (derived, never written): the record says obtaining
  or committing but no worker in this hub holds it -- the hub died.
  The partial's lock is free, so Resume adopts it.

**The commit is a transaction with one switch point.** Two ``flock``s
are taken in a fixed order (source project, then destination name) so
two commits cannot deadlock and two host projects cannot race for one
container name. Every recheck runs before the first write, because the
host project stayed editable during the download. A JOURNAL -- a random
transaction id, the image ids, the ``vaibify.yml`` bytes as found at
commit time -- is recorded durably before the first write. Then, in
order and each durable before the next: tag ``<newName>:latest`` by
image id (inert: host mode never reads it); rewrite ``vaibify.yml``;
write the origin record carrying the transaction id (inert until the
entry is a container); ONE atomic registry write that sets mode, name,
image source, trust and seed together; drop the staging tag and the
record. Recovery at hub start takes the commit lock non-blocking -- a
held lock is a commit in progress, possibly in another hub -- and only
then: an entry still host is ROLLED BACK (this transaction's artifacts
alone: the config bytes, an origin record bearing its id, a
``:latest`` tag no registry entry holds that points at its image), an
entry already converted is ROLLED FORWARD. The switch being one write
means those are the only two states.
"""

__all__ = [
    "ConversionBusyError",
    "ConversionRefusedError",
    "S_STAGING_TAG",
    "S_VIEW_INTERRUPTED",
    "fbHubHoldsLiveConversion",
    "fdictBeginPendingConversion",
    "fdictReadConversionView",
    "fdictStartConversion",
    "fnAnnotateListingWithPendingConversions",
    "fnCancelConversion",
    "fnCommitConversion",
    "fnInstallHubContext",
    "fnRecoverPendingConversionsAtStartup",
    "fnRegisterHubStartup",
    "fnRetryConversion",
    "fnStartConversionWorker",
]

import base64
import logging
import os
import secrets
import threading
from collections import deque

from vaibify.config import imageOrigins, pendingConversions, registryManager
from vaibify.config.durableFile import fnSyncDescriptor, fnSyncDirectory, fnWriteFileDurably
from vaibify.docker import disposableContainer
from vaibify.gui import acquisitionFailure
from vaibify.reproducibility import downloadPartial, resumableDownload


logger = logging.getLogger("vaibify")

S_STAGING_TAG = "vaibify-staging"
S_VIEW_INTERRUPTED = "interrupted"
_I_TAIL_LINES = 200
_F_WORKER_JOIN_SECONDS = 10.0


class ConversionRefusedError(Exception):
    """The requested step cannot be taken; the message says why."""


class ConversionBusyError(ConversionRefusedError):
    """The host project is open or held, so the switch must wait."""


_DICT_LIVE = {}
_LOCK_LIVE = threading.Lock()
_DICT_HUB = {"app": None, "dictCtx": None}


def fnInstallHubContext(app, dictCtx):
    """Give the busy check the hub's owner map and Docker connection."""
    _DICT_HUB["app"] = app
    _DICT_HUB["dictCtx"] = dictCtx


def fnRegisterHubStartup(app, dictCtx):
    """Install the hub context and recover journalled commits at startup.

    A commit the previous hub died inside left a journal; recovering it
    before any route serves is what makes the single-switch design
    hold. Registered on the lifespan like every other startup task.
    """
    import asyncio
    from . import serverLifespan
    fnInstallHubContext(app, dictCtx)

    async def fnRecoverAtStartup(app):
        del app
        await asyncio.to_thread(fnRecoverPendingConversionsAtStartup)

    async def fnNothingToStop(app):
        del app

    serverLifespan.fnRegisterLifespanTask(app, fnRecoverAtStartup, fnNothingToStop)


def fdictBeginPendingConversion(
    dictProject, sHostName, sNewName, dictRequest, dictImageSource,
    dictImageTrust, dictPendingSeed,
):
    """Write the record for a new containerization and start obtaining.

    Refuses by name when this host project is already being
    containerized, or when another pending containerization has claimed
    the destination name. Returns the view a poll would read.
    """
    if pendingConversions.fdictReadConversionRecord(sHostName) is not None:
        raise ConversionRefusedError(
            f"'{sHostName}' is already being containerized. Open its tile "
            "to watch, retry or cancel that."
        )
    for dictOther in pendingConversions.flistReadConversionRecords():
        if dictOther["sNewName"] == sNewName:
            raise ConversionRefusedError(
                f"The container name '{sNewName}' is claimed by the "
                f"containerization of '{dictOther['sHostName']}', which has "
                "not finished. Choose another name."
            )
    dictRecord = pendingConversions.fdictBuildConversionRecord(
        sHostName, sNewName, dictProject, dictRequest, dictImageSource,
        dictImageTrust, dictPendingSeed,
    )
    pendingConversions.fnWriteConversionRecord(dictRecord)
    return fdictStartConversion(dictRecord)


def fnAnnotateListingWithPendingConversions(listContainers):
    """Carry each host project's pending containerization onto its tile.

    The tile reads the state the HUB holds -- obtaining, waiting, ready,
    interrupted -- and never a browser's memory of it, so a reopened
    tab shows "Containerizing…" exactly when the hub is containerizing.
    """
    for dictProject in listContainers:
        if dictProject.get("sMode") != "host":
            continue
        dictView = fdictReadConversionView(dictProject.get("sName") or "")
        if dictView is None:
            continue
        dictDownload = dictView.get("dictDownload") or {}
        dictProject["dictPendingConversion"] = {
            "sState": dictView["sState"],
            "sNewName": dictView["sNewName"],
            "bLive": dictView["bLive"],
            "iBytes": int(dictDownload.get("iBytes") or 0),
            "iTotalBytes": int(dictDownload.get("iTotalBytes") or 0),
        }


def fbConversionIsLive(sHostName):
    """True while THIS hub is obtaining or committing the named host project."""
    with _LOCK_LIVE:
        return sHostName in _DICT_LIVE


def fbHubHoldsLiveConversion():
    """True while any conversion is obtaining or committing in this hub.

    The idle watchdog's veto: a conversion holds no socket and no owned
    container, so without it a closed tab would let the hub retire
    itself in the middle of a 700 MB download.
    """
    with _LOCK_LIVE:
        return bool(_DICT_LIVE)


# ---------------------------------------------------------------------
# Starting, retrying, cancelling.
# ---------------------------------------------------------------------


def fdictStartConversion(dictRecord):
    """Start the hub-owned acquisition for a freshly written record."""
    _fnStartWorker(dictRecord["sHostName"])
    return fdictReadConversionView(dictRecord["sHostName"])


def fnRetryConversion(sHostName):
    """Try again from waiting or interrupted: a fresh budget, the partial adopted."""
    dictRecord = _fdictRequireRecord(sHostName)
    with _LOCK_LIVE:
        if sHostName in _DICT_LIVE:
            raise ConversionRefusedError(
                f"'{sHostName}' is already being containerized."
            )
    if dictRecord.get("dictJournal"):
        _fnRecoverOneRecord(dictRecord)
        dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
        if dictRecord is None:
            return
    if dictRecord["sState"] == pendingConversions.S_STATE_READY:
        _fnTryCommitQuietly(sHostName)
        return
    _fnStartWorker(sHostName)


def fnCancelConversion(sHostName):
    """Give up: stop the download, delete its bytes, drop the record.

    The researcher's explicit decision (Cancel after its warning, Stay
    in host mode, Discard). The partial is deleted; the host project is
    untouched because nothing was ever written to it.
    """
    with _LOCK_LIVE:
        dictLive = _DICT_LIVE.get(sHostName)
    if dictLive is not None:
        resumableDownload.fnRequestCancel(dictLive["dictControl"])
        dictLive["threadWorker"].join(_F_WORKER_JOIN_SECONDS)
    dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
    if dictRecord is None:
        return
    sAcquisitionId = str(dictRecord.get("sAcquisitionId") or "")
    if sAcquisitionId:
        downloadPartial.fnDeletePartial(sAcquisitionId)
    _fnDropStagingTagQuietly(dictRecord)
    pendingConversions.fnRemoveConversionRecord(sHostName)


def _fdictRequireRecord(sHostName):
    dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
    if dictRecord is None:
        raise LookupError(f"no containerization is pending for '{sHostName}'")
    return dictRecord


def _fnStartWorker(sHostName):
    """The seam a unit test stubs so a route test starts no real worker."""
    fnStartConversionWorker(sHostName)


def fnStartConversionWorker(sHostName):
    """Register the live record and start the acquisition thread."""
    with _LOCK_LIVE:
        if sHostName in _DICT_LIVE:
            raise ConversionRefusedError(
                f"'{sHostName}' is already being containerized."
            )
        dictLive = {
            "dictControl": resumableDownload.fdictCreateDownloadControl({
                "sKind": "containerize", "sHostName": sHostName,
            }),
            "dequeTail": deque(maxlen=_I_TAIL_LINES),
            "threadWorker": None,
        }
        _DICT_LIVE[sHostName] = dictLive
    threadWorker = threading.Thread(
        target=_fnRunAcquisition, args=(sHostName,), daemon=True,
        name=f"vaibify-containerize-{sHostName}",
    )
    dictLive["threadWorker"] = threadWorker
    threadWorker.start()


# ---------------------------------------------------------------------
# The worker.
# ---------------------------------------------------------------------


def _fnRunAcquisition(sHostName):
    """Obtain, prove, stack and stage; then commit or wait for a decision."""
    from vaibify.cli.configLoader import fsDockerDir
    from vaibify.docker import pinnedImageAcquisition
    dictLive = _DICT_LIVE[sHostName]
    dictControl = dictLive["dictControl"]

    def fnReport(sLine):
        dictLive["dequeTail"].append(str(sLine).rstrip("\n"))

    try:
        dictRecord = _fdictRequireRecord(sHostName)
        _fnMoveRecord(dictRecord, pendingConversions.S_STATE_OBTAINING, dictFailure=None)
        dictStaged = pinnedImageAcquisition.fdictObtainAndStage(
            _fdictProjectLike(dictRecord), dictRecord["dictImageSource"],
            bool(dictRecord["dictImageSource"].get("bAllowEmulation")),
            fnReport, disposableContainer.fdockerCreateDisposableClient(),
            fsDockerDir(), S_STAGING_TAG, dictControl,
        )
        _fnRecordStaged(sHostName, dictStaged, dictControl)
    except Exception as error:  # noqa: BLE001 -- every outcome is recorded
        _fnRecordFailure(sHostName, error, dictControl, fnReport)
        return
    finally:
        with _LOCK_LIVE:
            _DICT_LIVE.pop(sHostName, None)
    _fnTryCommitQuietly(sHostName)


def _fdictProjectLike(dictRecord):
    """The entry-shaped dict the acquisition lane reads its facts from."""
    return {
        "sName": dictRecord["sNewName"],
        "sDirectory": dictRecord["sDirectory"],
        "sConfigPath": dictRecord["sConfigPath"],
        registryManager.S_IMAGE_SOURCE_KEY: dict(dictRecord["dictImageSource"]),
    }


def _fnMoveRecord(dictRecord, sState, **dictFields):
    dictRecord["sState"] = sState
    dictRecord.update(dictFields)
    pendingConversions.fnWriteConversionRecord(dictRecord)


def _fnRecordStaged(sHostName, dictStaged, dictControl):
    dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
    if dictRecord is None:
        return
    dictAcquired = dictStaged["dictAcquired"]
    _fnMoveRecord(
        dictRecord, pendingConversions.S_STATE_READY,
        sAcquisitionId=_fsAcquisitionIdOf(dictControl, dictRecord),
        dictFailure=None,
        dictStaged={
            "sBaseImageId": dictStaged["sBaseImageId"],
            "sRunningImageId": dictStaged["sRunningImageId"],
            "listResolvedOverlays": list(dictStaged["listResolvedOverlays"]),
            "listProven": list(dictStaged["listProven"]),
            "listChain": list(dictStaged["listChain"]),
            "sObtainedFrom": dictAcquired.get("sObtainedFrom", ""),
            "sRequiredPlatform": dictAcquired.get("sRequiredPlatform", ""),
            "sObtainedPlatform": dictAcquired.get("sObtainedPlatform", ""),
            "bEmulated": bool(dictAcquired.get("bEmulated")),
            "sZenodoService": dictStaged["dictPinned"].get("sZenodoService", ""),
            "sDepositVersionDoi": dictStaged["dictPinned"].get("sDepositVersionDoi", ""),
            "sPinnedImageReference": dictStaged["dictPinned"].get("sPinnedImageReference", ""),
        },
    )


def _fsAcquisitionIdOf(dictControl, dictRecord):
    with dictControl["lockLive"]:
        sFromDownload = str(dictControl["dictLive"].get("sAcquisitionId") or "")
    return sFromDownload or str(dictRecord.get("sAcquisitionId") or "")


def _fnRecordFailure(sHostName, error, dictControl, fnReport):
    """Say what stopped the acquisition; a cancel writes nothing."""
    if dictControl["eventCancel"].is_set():
        return
    dictFailure = acquisitionFailure.fdictDescribeAcquisitionFailure(error)
    fnReport("error: " + dictFailure["sSentence"])
    logger.warning(
        "Containerizing %r stopped: %s", sHostName, dictFailure["sTechnicalDetail"],
    )
    dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
    if dictRecord is None:
        return
    _fnMoveRecord(
        dictRecord, pendingConversions.S_STATE_WAITING,
        dictFailure=dictFailure,
        sAcquisitionId=dictFailure.get("sAcquisitionId")
        or _fsAcquisitionIdOf(dictControl, dictRecord),
    )


def _fnTryCommitQuietly(sHostName):
    """Commit now if nobody holds the project; otherwise leave it ready."""
    try:
        fnCommitConversion(sHostName)
    except ConversionBusyError as error:
        logger.info("Containerizing %r waits to finish: %s", sHostName, error)
    except (ConversionRefusedError, pendingConversions.ConversionLockHeldError) as error:
        logger.warning("Containerizing %r could not finish: %s", sHostName, error)
        _fnRecordCommitRefusal(sHostName, str(error))
    except Exception as error:  # noqa: BLE001 -- reported, never lost
        logger.exception("Containerizing %r failed while committing", sHostName)
        _fnRecordCommitRefusal(sHostName, f"finishing the conversion failed: {error}")


def _fnRecordCommitRefusal(sHostName, sSentence):
    dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
    if dictRecord is None or dictRecord.get("dictJournal"):
        return
    _fnMoveRecord(
        dictRecord, pendingConversions.S_STATE_WAITING,
        dictFailure={
            "sFailureClass": "commit-refused", "sSentence": sSentence,
            "sHeld": "The downloaded image is kept on this machine; Try "
                     "again does not download it a second time.",
            "bPartialKept": True, "bRetryable": True, "bDiagnosable": False,
            "sAlsoChecked": "", "sTechnicalDetail": sSentence,
            "iBytesOnDisk": 0, "iExpectedBytes": 0,
            "sAcquisitionId": str(dictRecord.get("sAcquisitionId") or ""),
        },
    )


# ---------------------------------------------------------------------
# The view a poll reads.
# ---------------------------------------------------------------------


def fdictReadConversionView(sHostName):
    """Return where the containerization stands, or ``None`` when none is pending."""
    dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
    if dictRecord is None:
        return None
    with _LOCK_LIVE:
        dictLive = _DICT_LIVE.get(sHostName)
    sState = dictRecord["sState"]
    if dictLive is None and sState in (
        pendingConversions.S_STATE_OBTAINING, pendingConversions.S_STATE_COMMITTING,
    ):
        sState = S_VIEW_INTERRUPTED
    return {
        "sHostName": dictRecord["sHostName"],
        "sNewName": dictRecord["sNewName"],
        "sState": sState,
        "bLive": dictLive is not None,
        "dictDownload": (
            resumableDownload.fdictReadDownloadProgress(dictLive["dictControl"])
            if dictLive is not None else _fdictDownloadFromPartial(dictRecord)
        ),
        "dictFailure": dictRecord.get("dictFailure"),
        "dictStaged": dictRecord.get("dictStaged"),
        "saTailLines": list(dictLive["dequeTail"]) if dictLive is not None else [],
        "bJournalled": bool(dictRecord.get("dictJournal")),
        "sStartedIso": dictRecord.get("sStartedIso", ""),
        "sUpdatedIso": dictRecord.get("sUpdatedIso", ""),
    }


def _fdictDownloadFromPartial(dictRecord):
    """What is on disk for a download nobody is running right now."""
    sAcquisitionId = str(dictRecord.get("sAcquisitionId") or "")
    for dictPartial in downloadPartial.flistDescribePartials():
        if dictPartial["sAcquisitionId"] != sAcquisitionId:
            continue
        dictSidecar = dictPartial.get("dictSidecar") or {}
        return {
            "iBytes": dictPartial["iBytesOnDisk"],
            "iTotalBytes": int(dictSidecar.get("iExpectedBytes") or 0),
            "sLastByteIso": str(dictSidecar.get("sLastByteIso") or ""),
            "sPartialState": dictPartial["sState"],
        }
    return {"iBytes": 0, "iTotalBytes": 0, "sLastByteIso": "", "sPartialState": ""}


# ---------------------------------------------------------------------
# The commit.
# ---------------------------------------------------------------------


def fnCommitConversion(sHostName):
    """Switch the host project to a container, under both locks, journalled.

    Raises :class:`ConversionBusyError` when the project is open or
    held (the staged image is kept and Finish retries),
    :class:`ConversionRefusedError` when a recheck fails, and
    ``pendingConversions.ConversionLockHeldError`` when another commit
    of this project is under way.
    """
    dictRecord = _fdictRequireRecord(sHostName)
    fileSource = pendingConversions.ffileTakeSourceLock(sHostName)
    fileDestination = None
    try:
        fileDestination = pendingConversions.ffileTakeDestinationLock(dictRecord["sNewName"])
        dictRecord = _fdictRequireRecord(sHostName)
        if dictRecord.get("dictJournal"):
            _fnRecoverJournalLocked(dictRecord)
            dictRecord = pendingConversions.fdictReadConversionRecord(sHostName)
            if dictRecord is None:
                return
        if dictRecord["sState"] != pendingConversions.S_STATE_READY:
            raise ConversionRefusedError(
                "the author's image has not been obtained yet, so there is "
                "nothing to finish."
            )
        dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
        _fnRecheckBeforeCommit(dictRecord, dockerDisposable)
        _fnRefuseIfBusy(sHostName)
        _fnWriteInOrder(dictRecord, dockerDisposable)
    finally:
        pendingConversions.fnReleaseLock(fileDestination)
        pendingConversions.fnReleaseLock(fileSource)


def _fnRecheckBeforeCommit(dictRecord, dockerDisposable):
    """Every fact the download ran on, asked again before the first write."""
    import yaml
    from vaibify.reproducibility.reproductionSource import (
        fdictDescribePinnedEnvironment,
    )
    sHostName, sNewName = dictRecord["sHostName"], dictRecord["sNewName"]
    dictEntry = registryManager.fdictGetProject(sHostName)
    if dictEntry is None or dictEntry.get("sMode") != "host" or (
        dictEntry.get("sDirectory") != dictRecord["sDirectory"]
    ):
        raise ConversionRefusedError(
            f"'{sHostName}' is no longer the host project this "
            "containerization started from."
        )
    if registryManager.fdictGetProject(sNewName) is not None or any(
        dictOther["sNewName"] == sNewName and dictOther["sHostName"] != sHostName
        for dictOther in pendingConversions.flistReadConversionRecords()
    ):
        raise ConversionRefusedError(
            f"the container name '{sNewName}' is taken now; choose another "
            "name and convert again (the downloaded image is kept)."
        )
    dictPinned = fdictDescribePinnedEnvironment(dictRecord["sDirectory"])
    if not dictPinned["bObtainable"] or dictPinned["sPinnedImageReference"] != (
        dictRecord["dictImageSource"].get("sPinnedImageReference")
    ):
        raise ConversionRefusedError(
            "the project's files changed during the download: the envelope "
            "now pins a different image. Convert again so the choice is "
            "made against the current files (the downloaded image is kept)."
        )
    try:
        with open(dictRecord["sConfigPath"], "r", encoding="utf-8") as fileConfig:
            bParses = isinstance(yaml.safe_load(fileConfig), dict)
    except (OSError, yaml.YAMLError):
        bParses = False
    if not bParses:
        raise ConversionRefusedError(
            "vaibify.yml cannot be read as a configuration any more; fix "
            "the file, then finish containerizing."
        )
    dictStaging = disposableContainer.fdictInspectImage(
        dockerDisposable, f"{sNewName}:{S_STAGING_TAG}",
    )
    if not dictStaging or dictStaging.get("sId") != dictRecord["dictStaged"]["sRunningImageId"]:
        raise ConversionRefusedError(
            "the obtained image is no longer on this machine; Try again "
            "obtains it once more."
        )


def _fnRefuseIfBusy(sHostName):
    """The switch renames the lock, lease and journal key: nobody may hold it."""
    from vaibify.config import operationJournal
    from vaibify.config.containerLock import fdictReadLockHolder
    app = _DICT_HUB["app"]
    if app is not None and getattr(app.state, "dictContainerOwners", {}).get(sHostName):
        raise ConversionBusyError(
            f"'{sHostName}' is open in a browser session. Close the project "
            "to finish containerizing it."
        )
    if fdictReadLockHolder(sHostName):
        raise ConversionBusyError(
            f"'{sHostName}' is in use by another vaibify session. Close it "
            "there to finish containerizing it."
        )
    dictCtx = _DICT_HUB["dictCtx"] or {}
    dictResolution = operationJournal.fdictResolveContainerJournal(
        sHostName, dictCtx.get("docker"), bPersistResolution=False,
    )
    if dictResolution["sResolution"] != operationJournal.S_RESOLUTION_SETTLED:
        raise ConversionBusyError(
            f"'{sHostName}' has operations that are not settled; reconcile "
            "it to finish containerizing."
        )


def _fnWriteInOrder(dictRecord, dockerDisposable):
    """The journal, then the five writes, each durable before the next."""
    sNewName = dictRecord["sNewName"]
    dictStaged = dictRecord["dictStaged"]
    with open(dictRecord["sConfigPath"], "rb") as fileConfig:
        baConfig = fileConfig.read()
    dictJournal = {
        "sTransactionId": secrets.token_hex(8),
        "sNewName": sNewName,
        "sBaseImageId": dictStaged["sBaseImageId"],
        "sRunningImageId": dictStaged["sRunningImageId"],
        "sConfigBase64": base64.b64encode(baConfig).decode("ascii"),
    }
    _fnMoveRecord(dictRecord, pendingConversions.S_STATE_COMMITTING, dictJournal=dictJournal)
    disposableContainer.fnTagImage(
        dockerDisposable, dictStaged["sRunningImageId"], sNewName, "latest",
    )
    _fnRewriteConfigDurably(dictRecord)
    imageOrigins.fnWriteOriginRecord(sNewName, _fdictOriginRecord(dictRecord, dictJournal))
    dictImageSource = dict(dictRecord["dictImageSource"])
    dictImageSource["listResolvedOverlays"] = list(dictStaged["listResolvedOverlays"])
    registryManager.fnConvertProjectToContainer(
        dictRecord["sHostName"], sNewName, dictImageSource,
        dictRecord.get("dictImageTrust"), dictRecord.get("dictPendingSeed"),
    )
    _fnDropStagingTagQuietly(dictRecord, dockerDisposable)
    pendingConversions.fnRemoveConversionRecord(dictRecord["sHostName"])


def _fnRewriteConfigDurably(dictRecord):
    """Runtime fields and agent install keys onto the author's file, synced."""
    from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
    from vaibify.docker.pinnedImageAcquisition import _fnWriteAgentInstallKeys
    from vaibify.gui.pinnedEnvironmentConversion import fnRewriteConfigForObtainedImage
    from vaibify.gui.registryRoutes import ConvertToContainerRequest
    sConfigPath = dictRecord["sConfigPath"]
    fnRewriteConfigForObtainedImage(
        sConfigPath, ConvertToContainerRequest(**dictRecord["dictRequest"]),
    )
    _fnWriteAgentInstallKeys(sConfigPath, [
        sOverlay for sOverlay in dictRecord["dictStaged"]["listResolvedOverlays"]
        if sOverlay in T_AGENT_OVERLAY_NAMES
    ])
    iDescriptor = os.open(sConfigPath, os.O_RDONLY)
    try:
        fnSyncDescriptor(iDescriptor)
    finally:
        os.close(iDescriptor)
    fnSyncDirectory(os.path.dirname(sConfigPath))


def _fdictOriginRecord(dictRecord, dictJournal):
    dictStaged = dictRecord["dictStaged"]
    dictOrigin = imageOrigins.fdictBuildOriginRecord(
        dictStaged["sPinnedImageReference"], dictStaged["sBaseImageId"],
        dictStaged["sRunningImageId"], dictStaged["listResolvedOverlays"],
        dictStaged["sObtainedFrom"], dictStaged["sZenodoService"],
        dictStaged["sDepositVersionDoi"], dictStaged["sRequiredPlatform"],
        dictStaged["sObtainedPlatform"], dictStaged["bEmulated"],
    )
    dictOrigin["sTransactionId"] = dictJournal["sTransactionId"]
    return dictOrigin


def _fnDropStagingTagQuietly(dictRecord, dockerDisposable=None):
    """Remove ``<newName>:vaibify-staging``; a daemon that cannot is logged."""
    if not dictRecord.get("dictStaged"):
        return
    try:
        disposableContainer.fnRemoveImageTag(
            dockerDisposable or disposableContainer.fdockerCreateDisposableClient(),
            dictRecord["sNewName"], S_STAGING_TAG,
        )
    except Exception as error:  # noqa: BLE001 -- a leftover tag, not a failure
        logger.warning(
            "The staging tag of %r could not be removed: %s",
            dictRecord["sNewName"], error,
        )


# ---------------------------------------------------------------------
# Recovery.
# ---------------------------------------------------------------------


def fnRecoverPendingConversionsAtStartup():
    """Roll every journalled commit back or forward before anything else runs."""
    for dictRecord in pendingConversions.flistReadConversionRecords():
        if not dictRecord.get("dictJournal"):
            continue
        try:
            _fnRecoverOneRecord(dictRecord)
        except pendingConversions.ConversionLockHeldError:
            logger.info(
                "Containerization of %r is being committed elsewhere; left alone",
                dictRecord["sHostName"],
            )
        except Exception:  # noqa: BLE001 -- one record must not stop the hub
            logger.exception(
                "Could not recover the containerization of %r", dictRecord["sHostName"],
            )


def _fnRecoverOneRecord(dictRecord):
    """Take both locks non-blocking, then recover; a held lock is left alone."""
    fileSource = pendingConversions.ffileTakeSourceLock(dictRecord["sHostName"])
    fileDestination = None
    try:
        fileDestination = pendingConversions.ffileTakeDestinationLock(dictRecord["sNewName"])
        dictFresh = pendingConversions.fdictReadConversionRecord(dictRecord["sHostName"])
        if dictFresh is not None and dictFresh.get("dictJournal"):
            _fnRecoverJournalLocked(dictFresh)
    finally:
        pendingConversions.fnReleaseLock(fileDestination)
        pendingConversions.fnReleaseLock(fileSource)


def _fnRecoverJournalLocked(dictRecord):
    """Roll back an abandoned commit, or forward one past its switch."""
    dictJournal = dictRecord["dictJournal"]
    sHostName, sNewName = dictRecord["sHostName"], dictRecord["sNewName"]
    dictEntry = registryManager.fdictGetProject(sHostName)
    bSwitched = dictEntry is None or dictEntry.get("sMode") != "host"
    dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    if bSwitched:
        logger.info("Containerization of %r had switched; rolling forward", sHostName)
        _fnDropStagingTagQuietly(dictRecord, dockerDisposable)
        pendingConversions.fnRemoveConversionRecord(sHostName)
        return
    logger.info("Containerization of %r was cut short; rolling back", sHostName)
    fnWriteFileDurably(
        dictRecord["sConfigPath"], base64.b64decode(dictJournal["sConfigBase64"]),
        iMode=0o644,
    )
    dictOrigin = imageOrigins.fdictReadOriginRecord(sNewName)
    if dictOrigin and dictOrigin.get("sTransactionId") == dictJournal["sTransactionId"]:
        imageOrigins.fnRemoveOriginRecord(sNewName)
    if registryManager.fdictGetProject(sNewName) is None:
        dictLatest = disposableContainer.fdictInspectImage(dockerDisposable, f"{sNewName}:latest")
        if dictLatest and dictLatest.get("sId") == dictJournal["sRunningImageId"]:
            disposableContainer.fnRemoveImageTag(dockerDisposable, sNewName, "latest")
    _fnMoveRecord(dictRecord, pendingConversions.S_STATE_READY, dictJournal=None)
