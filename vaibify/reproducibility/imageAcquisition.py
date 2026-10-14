"""Obtain the image a published envelope pins, through the published chain.

``reproduce.sh`` obtains the image in one order -- registry pull, then
the Zenodo deposit, then a copy already on this daemon -- and this
module is that chain in Python, for the rerun vaibify performs on a
stranger's behalf. The order is a ruling (a registry is a convenience,
the deposit is the archive, a local copy is survivable for the author
alone) and is not reordered or extended here; what differs from the
script is only that every link reports through a callback as it
happens and every refusal names which link failed and why.

**The platform is three facts, kept apart by name.**

* ``sRequiredPlatform`` is what the ENVELOPE pins, normalized to
  ``linux/<arch>``. It is REQUESTED explicitly of the pull and of the
  create; without the request a multi-architecture reference silently
  yields this host's build.
* ``sObtainedPlatform`` is what the image the chain actually produced
  reports of itself. Differing from the required platform ALWAYS
  refuses: the chain produced the wrong bytes.
* ``sDaemonArchitecture`` is the daemon's own, asked of the daemon.
  Differing from the required architecture is EMULATION -- allowed only
  when the caller said so, and recorded either way. Inspecting the
  obtained image cannot reveal it, because an image reports its own
  architecture on any host, which is why this fact has its own name.

**The deposit is trusted through the envelope, never through the
record page.** The tarball's hash is read from the envelope the
snapshot carries and checked BEFORE anything is handed to the daemon;
a download that differs is deleted and the link reports "did not match
its hash", never a load.

**The download resumes, retries and can be cancelled**
(:mod:`resumableDownload`), and its bytes live in an acquisition
directory of their own (:mod:`downloadPartial`). A verified tarball is
removed once the daemon has loaded it. Bytes a failure left behind are
KEPT when another attempt can continue from them -- a spent retry
budget, a full disk, a daemon that would not load -- and deleted when
nothing could (the deposit changed, the researcher cancelled). Every
refusal carries ``dictFailure`` saying which, so the dashboard can
offer Try again with the right sentence.

**A deposit is downloaded once per daemon that still holds it.** After
a verified load the host notes which image ID that tarball produced
(``~/.vaibify/depositLoads``). The next time the same deposit is asked
for, a daemon still holding that exact ID serves the archive link
without a download: the ID is a content address, so it is the bytes
the hash check passed. The link still reports ``archive``, because
that is where the bytes came from; a daemon that no longer holds the
ID, or a note for another deposit, falls through to the download.

No subprocess and no Docker SDK is acquired here: the three links that
touch the daemon live in ``docker.disposableContainer``, the SDK
authority for disposable work, and this module composes them.
"""

import gzip
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import requests

from vaibify.docker import daemonCapacity
from vaibify.docker import disposableContainer
from vaibify.gui.workflowManager import fsZenodoRecordIdFromDoi
from vaibify.reproducibility import _hashing
from vaibify.reproducibility import downloadPartial
from vaibify.reproducibility import imageArchive
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility import resumableDownload
from vaibify.reproducibility import zenodoClient
from vaibify.reproducibility.reproductionSource import (
    fsRequiredPlatformFromArchitecture,
)


__all__ = [
    "ImageAcquisitionRefusedError",
    "fsDownloadVerifiedTarball",
    "S_FAILURE_DAEMON_LOAD",
    "S_LINK_ARCHIVE",
    "S_LINK_LOCAL",
    "S_LINK_REGISTRY",
    "S_OBTAINED_ARCHIVE",
    "S_OBTAINED_LOCAL",
    "S_OBTAINED_REGISTRY",
    "fdictAcquirePinnedImage",
    "fsArchitectureOfPlatform",
]


class ImageAcquisitionRefusedError(Exception):
    """No link of the chain yielded the pinned image on the pinned platform.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed by
    an ``except OSError`` is how a control decision downgrades into an
    I/O hiccup. ``dictFailure`` carries the archived-deposit link's
    download failure when there was one -- its class, whether the
    partial was kept, the byte counts -- so a caller can offer the
    right next step instead of a sentence alone.
    """

    def __init__(self, sMessage, dictFailure=None, listAttempts=None):
        super().__init__(sMessage)
        self.dictFailure = dict(dictFailure or {})
        self.listAttempts = list(listAttempts or [])


S_OBTAINED_REGISTRY = "registry"
S_OBTAINED_ARCHIVE = "archive"
S_OBTAINED_LOCAL = "local"

S_LINK_REGISTRY = "registry pull"
S_LINK_ARCHIVE = "archived deposit"
S_LINK_LOCAL = "copy on this daemon"

# The failure class of a verified tarball the daemon would not load;
# the download's own classes live in ``resumableDownload``.
S_FAILURE_DAEMON_LOAD = "daemon-load"

# The resolver is the FALLBACK for a record the client cannot address,
# and it is followed by hand: the record page and the deposit both
# come from a cloned repository, so no URL read from either is fetched
# on trust, and no redirect is taken before its target host has been
# checked against the client's table. Module-level so a test can point
# it at a loopback server.
_S_DOI_RESOLVER = zenodoClient.S_DOI_RESOLVER_BASE
_T_HTTP_TIMEOUT_SECONDS = (10, 60)
# A deposit already downloaded, verified and loaded here is not fetched
# again while the daemon still holds the image it produced. Without
# this, every retry after a later step failed (an agent overlay, a
# full disk) repeated a download of most of a gigabyte.
_S_DEPOSIT_LOADS_DIRECTORY = os.path.join(
    os.path.expanduser("~"), ".vaibify", "depositLoads",
)
_REGEX_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def fsArchitectureOfPlatform(sPlatform):
    """Return the ``<arch>`` half of a ``linux/<arch>`` platform."""
    sPlatform = (sPlatform or "").strip().lower()
    if "/" in sPlatform:
        return sPlatform.split("/", 1)[1]
    return sPlatform


def fdictAcquirePinnedImage(
    dictEnvironment, sRequiredPlatform, fnStatusCallback=None,
    bAllowEmulation=False, dockerDisposable=None, dictDownloadControl=None,
):
    """Obtain the pinned image and return where it came from and what it is.

    ``dictEnvironment`` is the staged envelope payload (the whole
    ``environment.json`` dict); the pinned reference and the deposit
    record are read from it and nowhere else. ``dictDownloadControl``
    (:func:`resumableDownload.fdictCreateDownloadControl`) lets the
    caller cancel the deposit download and read its progress; without
    one the download runs to its own retry budget. Returns::

        {"sImageReference", "sImageId", "sObtainedFrom",
         "sRequiredPlatform", "sObtainedPlatform",
         "sDaemonArchitecture", "bEmulated", "listAttempts"}

    ``sImageReference`` is the reference to RUN: the registry reference
    when the pull served it, the loaded image ID when the deposit did
    (a tarball saved by digest carries no tag), or the local reference.
    ``sImageId`` is the daemon's raw ID for it, whichever link served,
    so a caller that must name the image by identity has it beside the
    reference.
    Raises :class:`ImageAcquisitionRefusedError` naming every link tried
    when none served, when the obtained platform differs from the
    required one, or when the daemon's architecture differs and
    emulation was not allowed.
    """
    fnStatus = fnStatusCallback or _fnDiscardStatus
    sRequiredPlatform = _fsRequirePlatform(sRequiredPlatform)
    sPinnedReference = _fsPinnedReference(dictEnvironment)
    if dockerDisposable is None:
        dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    listAttempts = []
    sImageReference, sObtainedFrom = _ftFollowTheChain(
        dockerDisposable, dictEnvironment, sPinnedReference,
        sRequiredPlatform, listAttempts, fnStatus, dictDownloadControl,
    )
    dictObtained = disposableContainer.fdictInspectImage(
        dockerDisposable, sImageReference,
    )
    sObtainedPlatform = _fsObtainedPlatform(dictObtained, sImageReference)
    if sObtainedPlatform != sRequiredPlatform:
        raise ImageAcquisitionRefusedError(
            f"the image obtained from the {sObtainedFrom} is built for "
            f"{sObtainedPlatform}, and the envelope pins "
            f"{sRequiredPlatform}. The chain produced the wrong bytes; "
            "nothing was run. " + _fsDescribeAttempts(listAttempts)
        )
    sDaemonArchitecture = daemonCapacity.fsReadDaemonArchitecture(
        dockerDisposable,
    )
    bEmulated = _fbJudgeEmulation(
        sRequiredPlatform, sDaemonArchitecture, bAllowEmulation,
    )
    sImageId = str(dictObtained.get("sId") or "")
    fnStatus({
        "sPhase": "acquired", "sObtainedFrom": sObtainedFrom,
        "sImageReference": sImageReference, "sImageId": sImageId,
        "bEmulated": bEmulated,
    })
    return {
        "sImageReference": sImageReference,
        "sImageId": sImageId,
        "sPinnedImageReference": sPinnedReference,
        "sObtainedFrom": sObtainedFrom,
        "sRequiredPlatform": sRequiredPlatform,
        "sObtainedPlatform": sObtainedPlatform,
        "sDaemonArchitecture": sDaemonArchitecture,
        "bEmulated": bEmulated,
        "listAttempts": listAttempts,
    }


def _fnDiscardStatus(dictEvent):
    """Drop a status event for callers that report nothing."""
    del dictEvent


def _fsRequirePlatform(sRequiredPlatform):
    """Return the normalized required platform, refusing an empty one."""
    sNormalized = fsRequiredPlatformFromArchitecture(sRequiredPlatform)
    if not sNormalized:
        raise ImageAcquisitionRefusedError(
            "no required platform was given, so the pinned build cannot "
            "be requested. The staged snapshot records it from the "
            "envelope's architecture; a snapshot without one is refused "
            "at staging."
        )
    return sNormalized


def _fsPinnedReference(dictEnvironment):
    """Return the content-pinned image reference the envelope carries."""
    from vaibify.reproducibility.shadowRerun import (
        ShadowRerunRefusedError,
        fsResolvePinnedImageReference,
    )
    try:
        return fsResolvePinnedImageReference(dictEnvironment)
    except ShadowRerunRefusedError as error:
        raise ImageAcquisitionRefusedError(str(error)) from error


def _ftFollowTheChain(
    dockerDisposable, dictEnvironment, sPinnedReference, sRequiredPlatform,
    listAttempts, fnStatus, dictDownloadControl=None,
):
    """Try the three links in the published order; return what served."""
    if _fbRegistryServes(
        dockerDisposable, sPinnedReference, sRequiredPlatform, listAttempts,
        fnStatus,
    ):
        return sPinnedReference, S_OBTAINED_REGISTRY
    sLoadedId = _fsArchiveServes(
        dockerDisposable, dictEnvironment, listAttempts, fnStatus,
        dictDownloadControl,
    )
    if sLoadedId:
        return sLoadedId, S_OBTAINED_ARCHIVE
    if _fbLocalCopyServes(
        dockerDisposable, sPinnedReference, listAttempts, fnStatus,
    ):
        return sPinnedReference, S_OBTAINED_LOCAL
    raise ImageAcquisitionRefusedError(
        f"no link of the chain yielded {sPinnedReference}. "
        + _fsDescribeAttempts(listAttempts),
        dictFailure=_fdictArchiveLinkFailure(listAttempts),
        listAttempts=listAttempts,
    )


def _fdictArchiveLinkFailure(listAttempts):
    """Return the archived link's download failure, or ``{}``."""
    for dictAttempt in listAttempts:
        if dictAttempt["sLink"] == S_LINK_ARCHIVE and dictAttempt.get("dictFailure"):
            return dict(dictAttempt["dictFailure"])
    return {}


def _fnRecordAttempt(
    listAttempts, fnStatus, sLink, bSucceeded, sDetail, dictFailure=None,
):
    """Append one link's outcome and report it as it happens."""
    dictAttempt = {
        "sLink": sLink, "bSucceeded": bool(bSucceeded), "sDetail": sDetail,
        "dictFailure": dict(dictFailure or {}),
    }
    listAttempts.append(dictAttempt)
    fnStatus({"sPhase": "attempt", **dictAttempt})


def _fsDescribeAttempts(listAttempts):
    """Return one line per link tried, for a refusal that names them all."""
    return "Links tried: " + "; ".join(
        f"{dictAttempt['sLink']}: "
        + ("served" if dictAttempt["bSucceeded"] else "failed")
        + (f" ({dictAttempt['sDetail']})" if dictAttempt["sDetail"] else "")
        for dictAttempt in listAttempts
    ) + "."


def _fbRegistryServes(
    dockerDisposable, sPinnedReference, sRequiredPlatform, listAttempts,
    fnStatus,
):
    """Link one: pull the reference for the required platform."""
    if "@sha256:" not in sPinnedReference:
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_REGISTRY, False,
            "the envelope pins a local-only image ID, which no registry "
            "serves",
        )
        return False
    fnStatus({"sPhase": "pulling", "sImageReference": sPinnedReference,
              "sPlatform": sRequiredPlatform})
    dictPull = disposableContainer.fdictPullImage(
        dockerDisposable, sPinnedReference, sRequiredPlatform,
    )
    _fnRecordAttempt(
        listAttempts, fnStatus, S_LINK_REGISTRY, dictPull["bPulled"],
        "" if dictPull["bPulled"] else dictPull["sDetail"],
    )
    return dictPull["bPulled"]


def _fsArchiveServes(
    dockerDisposable, dictEnvironment, listAttempts, fnStatus,
    dictDownloadControl=None,
):
    """Link two: fetch the deposit, verify it, load it; return the ID."""
    dictRecord = imageArchive.fdictReadArchiveRecord(dictEnvironment)
    if dictRecord is None:
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_ARCHIVE, False,
            "no deposit on record",
        )
        return ""
    dictPreviousLoad = _fdictPreviousLoadStillHeld(dockerDisposable, dictRecord)
    if dictPreviousLoad is not None:
        fnStatus({"sPhase": "reused", **dictPreviousLoad})
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_ARCHIVE, True,
            dictPreviousLoad["sLoadedImageId"],
        )
        return dictPreviousLoad["sLoadedImageId"]
    try:
        _fnRefuseWithoutACodecFor(str(dictRecord.get("sTarballName") or ""))
    except ImageAcquisitionRefusedError as error:
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_ARCHIVE, False, str(error),
        )
        return ""
    dictControl = (
        dictDownloadControl or resumableDownload.fdictCreateDownloadControl()
    )
    try:
        dictAcquisition = downloadPartial.fdictOpenAcquisition(
            dictRecord, dictControl["dictPurpose"],
        )
    except OSError as error:
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_ARCHIVE, False,
            f"no directory could be made for the download: {error}",
        )
        return ""
    except ValueError as error:
        # The record's file name cannot be stored under: the archive
        # link is refused by name, and no directory was made.
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_ARCHIVE, False, str(error),
        )
        return ""
    dictOutcome = {"bKeepPartial": False}
    try:
        sLoadedId = _fsDownloadAndLoad(
            dockerDisposable, dictRecord, dictAcquisition, fnStatus,
            dictControl, dictOutcome,
        )
    except ImageAcquisitionRefusedError as error:
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_ARCHIVE, False, str(error),
            error.dictFailure,
        )
        if error.dictFailure.get("sFailureClass") == (
            resumableDownload.S_FAILURE_CANCELLED
        ):
            # The researcher gave up: the chain stops here rather than
            # offering a local copy they did not ask about.
            raise
        return ""
    finally:
        # Give-up and failure-without-a-resume delete under the lock;
        # resumable bytes are released with the lock freed, which is
        # what marks them interrupted for the next acquisition.
        if dictOutcome["bKeepPartial"]:
            downloadPartial.fnReleaseAcquisition(dictAcquisition)
        else:
            downloadPartial.fnDiscardAcquisition(dictAcquisition)
    _fnRecordAttempt(listAttempts, fnStatus, S_LINK_ARCHIVE, True, sLoadedId)
    return sLoadedId


def _fsDownloadAndLoad(
    dockerDisposable, dictRecord, dictAcquisition, fnStatus, dictControl,
    dictOutcome,
):
    """Download or resume, verify, load; say in ``dictOutcome`` what to keep."""
    iOnDisk = downloadPartial.fiBytesOnDisk(dictAcquisition)
    if dictAcquisition["bAdopted"] and iOnDisk:
        fnStatus({
            "sPhase": "resuming", "iBytes": iOnDisk,
            "iTotalBytes": int(dictRecord.get("iTarballBytes") or 0),
        })
    try:
        sTarballPath = fsDownloadVerifiedTarball(
            dictRecord, dictAcquisition, fnStatus, dictControl,
        )
    except ImageAcquisitionRefusedError as error:
        dictOutcome["bKeepPartial"] = bool(error.dictFailure.get("bPartialKept"))
        raise
    # A complete, verified tarball is kept until the daemon has loaded
    # it: a daemon that is down costs a retry, never a second download.
    dictOutcome["bKeepPartial"] = True
    try:
        sLoadedId = _fsLoadTarball(dockerDisposable, sTarballPath, fnStatus)
    except ImageAcquisitionRefusedError as error:
        error.dictFailure.setdefault("sFailureClass", S_FAILURE_DAEMON_LOAD)
        error.dictFailure.setdefault("bPartialKept", True)
        error.dictFailure.setdefault("sSentence", str(error))
        error.dictFailure.setdefault("sAcquisitionId", dictAcquisition["sAcquisitionId"])
        raise
    _fnRecordDepositLoad(dictRecord, sLoadedId)
    dictOutcome["bKeepPartial"] = False
    return sLoadedId


def _fsDepositLoadPath(dictRecord):
    """Return where this host notes loading a deposit, or ``""``.

    One file per tarball hash, under ``~/.vaibify``, where nothing in a
    container reaches. A record with no well-formed hash has no note.
    """
    sHex = str(dictRecord.get("sTarballSha256") or "").partition(":")[2]
    if not _REGEX_SHA256_HEX.fullmatch(sHex):
        return ""
    os.makedirs(_S_DEPOSIT_LOADS_DIRECTORY, mode=0o700, exist_ok=True)
    return os.path.join(_S_DEPOSIT_LOADS_DIRECTORY, sHex + ".json")


def _fnRecordDepositLoad(dictRecord, sLoadedId):
    """Note that this deposit's verified tarball loaded as ``sLoadedId``.

    Written only after the hash check passed and the daemon loaded the
    bytes. A note that cannot be written costs one future download and
    nothing else, so the failure is not raised.
    """
    try:
        sPath = _fsDepositLoadPath(dictRecord)
        if not sPath:
            return
        iHandle, sTemporary = tempfile.mkstemp(
            dir=os.path.dirname(sPath), suffix=".json")
        with os.fdopen(iHandle, "w", encoding="utf-8") as fileNote:
            json.dump({
                "sTarballSha256": str(dictRecord["sTarballSha256"]),
                "sVersionDoi": str(dictRecord.get("sVersionDoi") or ""),
                "sLoadedImageId": str(sLoadedId),
                "sLoadedIso": datetime.now(timezone.utc).isoformat(),
            }, fileNote)
        os.chmod(sTemporary, 0o600)
        os.replace(sTemporary, sPath)
    except OSError:
        return


def _fdictPreviousLoadStillHeld(dockerDisposable, dictRecord):
    """Return this host's note of loading the deposit, if its image is still held.

    The note binds the envelope's tarball hash and DOI to the image ID
    the verified tarball produced. An image ID is a content address, so
    a daemon that still holds that exact ID holds the bytes the deposit
    delivered and the download would only repeat itself. ``None`` when
    there is no note, the note names another deposit, or the daemon no
    longer holds the image -- every case the download then decides.
    """
    try:
        with open(_fsDepositLoadPath(dictRecord), "r", encoding="utf-8") as fileNote:
            dictNote = json.load(fileNote)
    except (OSError, ValueError):
        return None
    if not isinstance(dictNote, dict):
        return None
    if dictNote.get("sTarballSha256") != dictRecord.get("sTarballSha256"):
        return None
    if dictNote.get("sVersionDoi") != str(dictRecord.get("sVersionDoi") or ""):
        return None
    sLoadedId = str(dictNote.get("sLoadedImageId") or "")
    if not sLoadedId:
        return None
    dictHeld = disposableContainer.fdictInspectImage(dockerDisposable, sLoadedId)
    if not dictHeld or dictHeld.get("sId") != sLoadedId:
        return None
    return dictNote


def fsDownloadVerifiedTarball(
    dictRecord, dictAcquisition, fnStatus, dictControl=None,
):
    """Bring the acquisition's file to the recorded size; refuse it unless it hashes right.

    The hash and the size come from the ENVELOPE's record, never from
    the record page: the page is what a download is being checked
    against, not the authority on what it should contain. Returns the
    path of the verified file inside the acquisition directory. A file
    that hashes wrong is thrown away and downloaded ONCE more from
    zero, because a resumed file can carry a torn byte the checkpoint
    missed; a second mismatch is refused with the partial deleted.

    Shared with the promotion lane, which falls back to the sandbox
    copy when the image is positively absent from the daemon. The
    alternative there -- ``ZenodoClient.fnDownloadFile`` -- writes
    with no size ceiling and computes no digest, so a promotion built
    on it would upload whatever the sandbox served.
    """
    sDoi = str(dictRecord.get("sVersionDoi") or "")
    sName = str(dictRecord.get("sTarballName") or "")
    sExpectedSha = str(dictRecord.get("sTarballSha256") or "")
    if not sDoi or not sName or not sExpectedSha.startswith("sha256:"):
        raise ImageAcquisitionRefusedError(
            "the deposit record names no DOI, tarball or sha256, so "
            "nothing could be fetched and checked"
        )
    if not fsZenodoRecordIdFromDoi(sDoi):
        raise ImageAcquisitionRefusedError(
            f"{sDoi!r} is not a Zenodo DOI, so no deposit was fetched"
        )
    iExpectedBytes = int(dictRecord.get("iTarballBytes") or 0)
    if iExpectedBytes <= 0:
        raise ImageAcquisitionRefusedError(
            "the envelope records no size for the archived image, so the "
            "download could not be bounded; nothing was fetched"
        )
    dictControl = dictControl or resumableDownload.fdictCreateDownloadControl()
    sFileUrl = _fsResolveDepositFileUrl(dictRecord, sDoi, sName)
    fnStatus({
        "sPhase": "downloading", "sDoi": sDoi,
        "iBytes": downloadPartial.fiBytesOnDisk(dictAcquisition),
        "iTotalBytes": iExpectedBytes,
    })
    for bLastChance in (False, True):
        if downloadPartial.fbIsComplete(dictAcquisition):
            # A file verified earlier whose load did not happen (the
            # daemon was down) is checked again, never downloaded again.
            sActualSha = "sha256:" + _hashing.fsHashFileSha256(
                downloadPartial.fsCompletePath(dictAcquisition),
            )
        else:
            sActualSha = _fsDownloadOrRefuse(
                dictAcquisition, sFileUrl, fnStatus, dictControl,
            )
            if sActualSha == sExpectedSha:
                downloadPartial.fnMarkComplete(dictAcquisition)
        if sActualSha == sExpectedSha:
            return downloadPartial.fsCompletePath(dictAcquisition)
        downloadPartial.fnTruncatePart(dictAcquisition, 0)
        downloadPartial.fnRecordSidecar(
            dictAcquisition, {"iDurableBytes": 0, "iHighWaterBytes": 0},
        )
        if not bLastChance:
            fnStatus({
                "sPhase": "downloadRestarting",
                "sSentence": (
                    "Starting over: the downloaded file did not match the "
                    "hash the project records"
                ),
            })
    raise ImageAcquisitionRefusedError(
        "the deposit did not match its hash: the envelope records "
        f"{sExpectedSha[:19]}... and the download hashed to "
        f"{sActualSha[:19]}... twice; nothing was loaded",
        dictFailure={
            "sFailureClass": "hash-mismatch", "bPartialKept": False,
            "sAcquisitionId": dictAcquisition["sAcquisitionId"],
            "iBytesOnDisk": 0, "iExpectedBytes": iExpectedBytes,
        },
    )


def _fsDownloadOrRefuse(dictAcquisition, sFileUrl, fnStatus, dictControl):
    """Run the resumable download; translate its refusal into the chain's."""
    try:
        return resumableDownload.fsDownloadIntoAcquisition(
            dictAcquisition, sFileUrl, zenodoClient.flistAllowedZenodoOrigins(),
            fnStatus, dictControl,
        )
    except resumableDownload.DownloadRefusedError as error:
        raise ImageAcquisitionRefusedError(
            str(error),
            dictFailure={
                "sFailureClass": error.sFailureClass,
                "bPartialKept": error.bPartialKept,
                "sSentence": str(error),
                **error.dictDetail,
            },
        ) from error


def _fsResolveDepositFileUrl(dictRecord, sDoi, sTarballName):
    """Name the tarball's URL under the record the DOI identifies.

    The record page is fetched through the BOUNDED client
    (``zenodoClient``), not with a bare request: it is an untrusted
    document served by a third party, and the client is where the
    timeouts, the response check, the size discipline for Zenodo JSON
    and the host allowlist already live. WHICH Zenodo is asked comes
    from the record's own service name, or from the DOI prefix for a
    record written before that field existed -- never from a URL in
    the record, and never by following the DOI first. The file URL
    comes from the record's own ``files`` list, so a record that
    renames or moves its files is followed rather than guessed at.

    The DOI-follow remains as the FALLBACK, and is what the generated
    shell script does for a record with no service. It covers a record
    the client cannot address -- a DOI whose id is not a Zenodo record
    id -- and it is why this function takes a DOI rather than a record
    id. It is followed by hand: each hop's host is checked before the
    request for it is sent, so a DOI that resolves somewhere other than
    Zenodo is refused by name rather than fetched.

    What is NOT delegated is the download itself. The client's
    ``fnDownloadFile`` writes with no size ceiling, computes no
    digest, and draws a terminal progress bar from whatever thread
    calls it; the download here is bounded against the envelope's
    recorded size, hashed as it lands, and reported to the card. Using
    the client for it would be a downgrade, so the split is: the
    client fetches the untrusted JSON, this module fetches the bytes.
    """
    sFileUrl = _fsFileUrlFromPublishedRecord(dictRecord, sDoi, sTarballName)
    if sFileUrl:
        return sFileUrl
    try:
        responseDoi = zenodoClient.fresponseGetWithinAllowlist(
            _S_DOI_RESOLVER + sDoi, _flistDoiFollowOrigins(),
            bStream=True, tTimeout=_T_HTTP_TIMEOUT_SECONDS,
        )
        sRecordUrl = responseDoi.url
        responseDoi.close()
    except zenodoClient.ZenodoRedirectRefusedError as error:
        raise ImageAcquisitionRefusedError(
            f"the DOI {sDoi} resolves outside Zenodo, so no deposit was "
            f"fetched: {error}"
        ) from error
    except requests.RequestException as error:
        raise ImageAcquisitionRefusedError(
            f"the DOI {sDoi} could not be resolved: {error}"
        ) from error
    return f"{sRecordUrl.rstrip('/')}/files/{sTarballName}?download=1"


def _flistDoiFollowOrigins():
    """Return the origins a DOI-follow may touch: the resolver and Zenodo."""
    return zenodoClient.flistAllowedZenodoOrigins() + [
        zenodoClient.fsOriginOfUrl(_S_DOI_RESOLVER),
    ]


def _fsDepositService(dictRecord, sDoi):
    """Return the Zenodo service the record names, or its DOI prefix implies.

    The record's own ``sZenodoService`` is the authority when present.
    It is validated through the client's table, and any other value
    REFUSES the link rather than being guessed around: a record that
    names a service this vaibify does not know is a record it cannot
    fetch from without inventing a host. Records written before the
    field existed are classified by DOI prefix.
    """
    sRecorded = str(dictRecord.get("sZenodoService") or "").strip()
    if not sRecorded:
        return zenodoClient.fsServiceForDoi(sDoi)
    try:
        zenodoClient.fsResolveServiceBaseUrl(sRecorded)
    except ValueError as error:
        raise ImageAcquisitionRefusedError(
            "the deposit record names a Zenodo service this vaibify "
            f"does not know ({sRecorded!r}), so no deposit was fetched"
        ) from error
    return sRecorded


def _fsFileUrlFromPublishedRecord(dictRecord, sDoi, sTarballName):
    """Return the tarball's URL from the bounded record fetch, or "".

    Empty rather than raising: a record the client cannot fetch is not
    an error here, it is the case the DOI-follow above exists for. The
    one exception is a fetch REFUSED at the allowlist -- that names a
    record page redirecting off Zenodo, which no fallback should paper
    over. The client is built with an EMPTY token so no host keyring is
    touched -- a published record is public, and a reproduction is
    somebody reading a stranger's deposit.
    """
    sRecordId = fsZenodoRecordIdFromDoi(sDoi)
    if not sRecordId:
        return ""
    clientZenodo = zenodoClient.ZenodoClient(
        sService=_fsDepositService(dictRecord, sDoi), sToken="",
    )
    try:
        dictPublished = clientZenodo.fdictFetchPublishedRecord(sRecordId)
    except zenodoClient.ZenodoRedirectRefusedError as error:
        raise ImageAcquisitionRefusedError(
            f"the record page for {sDoi} could not be fetched: {error}"
        ) from error
    except Exception:  # noqa: BLE001 - the DOI-follow is the fallback
        return ""
    return zenodoClient._fsFindFileUrlOrNone(dictPublished, sTarballName)


def _fsLoadTarball(dockerDisposable, sTarballPath, fnStatus):
    """Decompress by the codec the name implies and hand it to the daemon."""
    fnStatus({"sPhase": "loading"})
    try:
        with _ffileOpenByCodec(sTarballPath) as fileStream:
            return disposableContainer.fsLoadImageFromStream(
                dockerDisposable, fileStream,
            )
    except Exception as error:  # noqa: BLE001 -- the link reports it
        raise ImageAcquisitionRefusedError(
            f"the daemon could not load the archived image: "
            f"{type(error).__name__}: {error}"
        ) from error


def _fnRefuseWithoutACodecFor(sTarballName):
    """Refuse a zstd deposit this Python cannot decompress -- before fetching it.

    Asked before the download as well as at the load, because the
    answer does not change in between: a hub with no zstd codec used
    to fetch the whole archive, then refuse to open it.
    """
    if not sTarballName.endswith(".zst"):
        return
    if imageDeposit._ftResolveZstdCodec() is None:
        raise ImageAcquisitionRefusedError(
            "the deposit is zstd-compressed and this Python cannot "
            "decompress zstd, so it was not downloaded. Install the codec "
            "with `python3 -m pip install zstandard` in the Python that "
            "runs vaibify, restart vaibify, then choose Re-obtain the "
            "pinned image from the environment's menu"
        )


def _ffileOpenByCodec(sTarballPath):
    """Open a tarball for reading through the codec its name implies."""
    if sTarballPath.endswith(".zst"):
        _fnRefuseWithoutACodecFor(sTarballPath)
        return imageDeposit._ftResolveZstdCodec()[2](open(sTarballPath, "rb"))
    if sTarballPath.endswith(".gz"):
        return gzip.open(sTarballPath, "rb")
    return open(sTarballPath, "rb")


def _fbLocalCopyServes(dockerDisposable, sPinnedReference, listAttempts, fnStatus):
    """Link three: a copy already on this daemon, survivable for the author only."""
    dictHeld = disposableContainer.fdictInspectImage(
        dockerDisposable, sPinnedReference,
    )
    if dictHeld is None:
        _fnRecordAttempt(
            listAttempts, fnStatus, S_LINK_LOCAL, False,
            "no copy of the pinned reference is on this daemon",
        )
        return False
    _fnRecordAttempt(
        listAttempts, fnStatus, S_LINK_LOCAL, True,
        "the registry did not serve it and no archived copy could be "
        "loaded, so a reproducer without this copy cannot obtain the "
        "image; only the author can take this path",
    )
    return True


def _fsObtainedPlatform(dictObtained, sImageReference):
    """Return ``linux/<arch>`` for the image the chain produced, or refuse."""
    if dictObtained is None or not dictObtained.get("sArchitecture"):
        raise ImageAcquisitionRefusedError(
            f"the image {sImageReference} could not be inspected after "
            "it was obtained, so its platform is unknown; nothing was run"
        )
    sOs = dictObtained.get("sOs") or "linux"
    return f"{sOs}/{dictObtained['sArchitecture']}".lower()


def _fbJudgeEmulation(sRequiredPlatform, sDaemonArchitecture, bAllowEmulation):
    """Return whether the run would be emulated; refuse it unless allowed."""
    if not sDaemonArchitecture:
        raise ImageAcquisitionRefusedError(
            "the daemon did not report its architecture, so whether the "
            "pinned build would run natively or under emulation cannot "
            "be known; nothing was run"
        )
    bEmulated = (
        fsArchitectureOfPlatform(sRequiredPlatform)
        != sDaemonArchitecture.strip().lower()
    )
    if bEmulated and not bAllowEmulation:
        raise ImageAcquisitionRefusedError(
            f"the envelope pins {sRequiredPlatform} and this daemon is "
            f"{sDaemonArchitecture}, so the run would be emulated. Pass "
            "--allow-emulation to accept that; the report will say the "
            "result was reproduced under emulation."
        )
    return bEmulated
