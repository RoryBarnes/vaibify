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
its hash", never a load. The tarball is removed on every exit path.

No subprocess and no Docker SDK is acquired here: the three links that
touch the daemon live in ``docker.disposableContainer``, the SDK
authority for disposable work, and this module composes them.
"""

import gzip
import os
import shutil

import requests

from vaibify.docker import disposableContainer
from vaibify.gui.workflowManager import fsZenodoRecordIdFromDoi
from vaibify.reproducibility import _hashing
from vaibify.reproducibility import imageArchive
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility.reproductionSource import (
    fsRequiredPlatformFromArchitecture,
)


__all__ = [
    "ImageAcquisitionRefusedError",
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
    I/O hiccup.
    """


S_OBTAINED_REGISTRY = "registry"
S_OBTAINED_ARCHIVE = "archive"
S_OBTAINED_LOCAL = "local"

_S_LINK_REGISTRY = "registry pull"
_S_LINK_ARCHIVE = "archived deposit"
_S_LINK_LOCAL = "copy on this daemon"

# The DOI is FOLLOWED, exactly as the generated script follows it, so
# sandbox and production Zenodo -- different hosts, different DOI
# prefixes -- need no map that could be wrong years from now.
_S_DOI_RESOLVER = "https://doi.org/"
_T_HTTP_TIMEOUT_SECONDS = (10, 60)
_I_DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024
# A download is refused once it has grown past what the envelope
# recorded, with some slack for a record that predates exact sizes.
_F_DOWNLOAD_SIZE_SLACK = 1.25


def fsArchitectureOfPlatform(sPlatform):
    """Return the ``<arch>`` half of a ``linux/<arch>`` platform."""
    sPlatform = (sPlatform or "").strip().lower()
    if "/" in sPlatform:
        return sPlatform.split("/", 1)[1]
    return sPlatform


def fdictAcquirePinnedImage(
    dictEnvironment, sRequiredPlatform, fnStatusCallback=None,
    bAllowEmulation=False, dockerDisposable=None,
):
    """Obtain the pinned image and return where it came from and what it is.

    ``dictEnvironment`` is the staged envelope payload (the whole
    ``environment.json`` dict); the pinned reference and the deposit
    record are read from it and nowhere else. Returns::

        {"sImageReference", "sObtainedFrom", "sRequiredPlatform",
         "sObtainedPlatform", "sDaemonArchitecture", "bEmulated",
         "listAttempts"}

    ``sImageReference`` is the reference to RUN: the registry reference
    when the pull served it, the loaded image ID when the deposit did
    (a tarball saved by digest carries no tag), or the local reference.
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
        sRequiredPlatform, listAttempts, fnStatus,
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
    sDaemonArchitecture = disposableContainer.fsReadDaemonArchitecture(
        dockerDisposable,
    )
    bEmulated = _fbJudgeEmulation(
        sRequiredPlatform, sDaemonArchitecture, bAllowEmulation,
    )
    fnStatus({
        "sPhase": "acquired", "sObtainedFrom": sObtainedFrom,
        "sImageReference": sImageReference, "bEmulated": bEmulated,
    })
    return {
        "sImageReference": sImageReference,
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
    listAttempts, fnStatus,
):
    """Try the three links in the published order; return what served."""
    if _fbRegistryServes(
        dockerDisposable, sPinnedReference, sRequiredPlatform, listAttempts,
        fnStatus,
    ):
        return sPinnedReference, S_OBTAINED_REGISTRY
    sLoadedId = _fsArchiveServes(
        dockerDisposable, dictEnvironment, listAttempts, fnStatus,
    )
    if sLoadedId:
        return sLoadedId, S_OBTAINED_ARCHIVE
    if _fbLocalCopyServes(
        dockerDisposable, sPinnedReference, listAttempts, fnStatus,
    ):
        return sPinnedReference, S_OBTAINED_LOCAL
    raise ImageAcquisitionRefusedError(
        f"no link of the chain yielded {sPinnedReference}. "
        + _fsDescribeAttempts(listAttempts)
    )


def _fnRecordAttempt(listAttempts, fnStatus, sLink, bSucceeded, sDetail):
    """Append one link's outcome and report it as it happens."""
    dictAttempt = {
        "sLink": sLink, "bSucceeded": bool(bSucceeded), "sDetail": sDetail,
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
            listAttempts, fnStatus, _S_LINK_REGISTRY, False,
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
        listAttempts, fnStatus, _S_LINK_REGISTRY, dictPull["bPulled"],
        "" if dictPull["bPulled"] else dictPull["sDetail"],
    )
    return dictPull["bPulled"]


def _fsArchiveServes(dockerDisposable, dictEnvironment, listAttempts, fnStatus):
    """Link two: fetch the deposit, verify it, load it; return the ID."""
    dictRecord = imageArchive.fdictReadArchiveRecord(dictEnvironment)
    if dictRecord is None:
        _fnRecordAttempt(
            listAttempts, fnStatus, _S_LINK_ARCHIVE, False,
            "no deposit on record",
        )
        return ""
    sScratchDirectory = imageDeposit.fsResolveDepositScratchDirectory()
    try:
        sTarballPath = _fsDownloadVerifiedTarball(
            dictRecord, sScratchDirectory, fnStatus,
        )
        sLoadedId = _fsLoadTarball(dockerDisposable, sTarballPath, fnStatus)
    except ImageAcquisitionRefusedError as error:
        _fnRecordAttempt(
            listAttempts, fnStatus, _S_LINK_ARCHIVE, False, str(error),
        )
        return ""
    finally:
        shutil.rmtree(sScratchDirectory, ignore_errors=True)
    _fnRecordAttempt(listAttempts, fnStatus, _S_LINK_ARCHIVE, True, sLoadedId)
    return sLoadedId


def _fsDownloadVerifiedTarball(dictRecord, sScratchDirectory, fnStatus):
    """Stream the deposit into scratch and refuse it unless it hashes right.

    The hash and the size come from the ENVELOPE's record, never from
    the record page: the page is what a download is being checked
    against, not the authority on what it should contain.
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
    imageDeposit._fnRefuseWithoutRoomOnDisk(sScratchDirectory, iExpectedBytes)
    sTarballPath = os.path.join(sScratchDirectory, os.path.basename(sName))
    sFileUrl = _fsResolveDepositFileUrl(sDoi, sName)
    fnStatus({"sPhase": "downloading", "sDoi": sDoi, "iBytes": 0,
              "iTotalBytes": iExpectedBytes})
    sActualSha = _fsStreamDownload(
        sFileUrl, sTarballPath, iExpectedBytes, fnStatus,
    )
    if sActualSha != sExpectedSha:
        os.remove(sTarballPath)
        raise ImageAcquisitionRefusedError(
            "the deposit did not match its hash: the envelope records "
            f"{sExpectedSha[:19]}... and the download hashed to "
            f"{sActualSha[:19]}...; nothing was loaded"
        )
    return sTarballPath


def _fsResolveDepositFileUrl(sDoi, sTarballName):
    """Follow the DOI to its record and name the tarball beneath it.

    The same two moves the generated script makes (``curl -L`` on the
    DOI, then ``<record>/files/<name>?download=1``), so the two lanes
    fetch from one place rather than two derivations of it.
    """
    try:
        responseDoi = requests.get(
            _S_DOI_RESOLVER + sDoi, allow_redirects=True,
            timeout=_T_HTTP_TIMEOUT_SECONDS, stream=True,
        )
        sRecordUrl = responseDoi.url
        responseDoi.close()
    except requests.RequestException as error:
        raise ImageAcquisitionRefusedError(
            f"the DOI {sDoi} could not be resolved: {error}"
        ) from error
    return f"{sRecordUrl.rstrip('/')}/files/{sTarballName}?download=1"


def _fsStreamDownload(sFileUrl, sTarballPath, iExpectedBytes, fnStatus):
    """Stream a URL to disk, hashing as it lands; return the sha256."""
    try:
        with requests.get(
            sFileUrl, stream=True, timeout=_T_HTTP_TIMEOUT_SECONDS,
        ) as responseFile:
            responseFile.raise_for_status()
            with open(sTarballPath, "wb") as fileTarball:
                sHexDigest = _hashing.fsHashChunkIteratorSha256(
                    _fiterWriteBoundedChunks(
                        responseFile.iter_content(_I_DOWNLOAD_CHUNK_BYTES),
                        fileTarball, iExpectedBytes, fnStatus,
                    ),
                )
    except requests.RequestException as error:
        raise ImageAcquisitionRefusedError(
            f"the archived image could not be fetched: {error}"
        ) from error
    return "sha256:" + sHexDigest


def _fiterWriteBoundedChunks(iterChunks, fileTarball, iExpectedBytes, fnStatus):
    """Write each chunk to disk, report it, and yield it on for hashing.

    Refuses once the download has grown past what the envelope
    recorded (with slack for a record that predates exact sizes), so a
    hostile or broken server cannot fill the disk.
    """
    iCeiling = int(max(iExpectedBytes, 1) * _F_DOWNLOAD_SIZE_SLACK)
    iReceived = 0
    for baChunk in iterChunks:
        iReceived += len(baChunk)
        if iExpectedBytes and iReceived > iCeiling:
            raise ImageAcquisitionRefusedError(
                "the download grew past the size the envelope records "
                "and was stopped"
            )
        fileTarball.write(baChunk)
        fnStatus({"sPhase": "downloading", "iBytes": iReceived,
                  "iTotalBytes": iExpectedBytes})
        yield baChunk


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


def _ffileOpenByCodec(sTarballPath):
    """Open a tarball for reading through the codec its name implies."""
    if sTarballPath.endswith(".zst"):
        tZstd = imageDeposit._ftResolveZstdCodec()
        if tZstd is None:
            raise ImageAcquisitionRefusedError(
                "the deposit is zstd-compressed and no zstd codec is "
                "available on this host"
            )
        return tZstd[2](open(sTarballPath, "rb"))
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
            listAttempts, fnStatus, _S_LINK_LOCAL, False,
            "no copy of the pinned reference is on this daemon",
        )
        return False
    _fnRecordAttempt(
        listAttempts, fnStatus, _S_LINK_LOCAL, True,
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
