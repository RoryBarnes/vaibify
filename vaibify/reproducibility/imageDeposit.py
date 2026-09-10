"""Producing and depositing the environment archive.

The policy — what a deposit record must say and whether it covers the
envelope — lives in :mod:`vaibify.reproducibility.imageArchive`. This
module is the I/O half: it saves the image the envelope pins, hashes
the bytes it is about to upload, uploads them to Zenodo, and reports
progress while it does.

WHY THIS RUNS ON THE HOST AND READS A CONTAINER CREDENTIAL
----------------------------------------------------------

``docker save`` talks to the daemon, so only the hub can produce the
tarball; vaibify stores the Zenodo token in the CONTAINER keyring,
because every other Zenodo call it makes runs as a script inside the
container. The bytes and the credential therefore start on opposite
sides of the boundary and one of them has to cross. Moving the token
is the cheaper crossing by a factor of a hundred million: it is read
through the typed-read seam, held in a local for the length of one
upload, and written to no file and no log. Moving the tarball would
mean streaming ~1 GB through an exec socket built for a terminal.

WHERE THE TARBALL GOES, AND WHY NOT THE PROJECT SCRATCH ROOT
------------------------------------------------------------

Not into the repository, and not into the project's scratch root
either: ``projectRoots.fsResolveScratchDirectory`` answers with the
CONTAINER's ephemeral root for a container project, and this write
happens on the host. It goes to a private per-deposit directory under
``~/.vaibify``, the same shape ``commandBuild.fsStageBuildContext``
uses for a build context — per operation rather than per project,
because two deposits can overlap and refreshing a shared directory
starts with an ``rmtree``. It is removed when the upload finishes,
successfully or not: a researcher's laptop must not silently gain a
gigabyte.

COMPRESSION IS CHOSEN, NOT ASSUMED
----------------------------------

zstd is preferred (measured: a 3.54 GB image compresses to 821 MB) and
is reached through the interpreter's own ``compression.zstd`` on 3.14+
or the ``zstandard`` wheel where it is installed. Neither is
guaranteed across the Python versions vaibify supports, so gzip —
always present — is the fallback. The choice is recorded in the file
NAME, which is what ``reproduce.sh`` reads to know how to unpack it;
nothing infers it.
"""

__all__ = [
    "I_PROGRESS_CHUNK_BYTES",
    "fdictDepositImageArchive",
    "fdictRecheckArchiveAgainstLocalImage",
    "fiReadImageSizeBytes",
    "fsJudgeDepositProvenance",
    "fsRecomputeImageStreamSha256",
    "fsResolveDepositScratchDirectory",
    "ftSaveAndCompressImage",
]

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

from vaibify.reproducibility import _hashing, imageArchive, zenodoClient
from vaibify.reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.l3Attestation import S_STATUS_PASSED


logger = logging.getLogger(__name__)

# Read from `docker save` in 8 MiB chunks: large enough that the
# progress callback fires on the order of a hundred times over a
# multi-gigabyte image rather than a hundred thousand.
I_PROGRESS_CHUNK_BYTES = 8 * 1024 * 1024

_S_SCRATCH_SUBDIRECTORY = "imageArchive"

# Headroom over the image's declared size. The compressed tarball is
# smaller than the image, so the uncompressed figure plus a margin is
# a deliberately conservative bound: refusing a deposit that would
# have fit costs a researcher one message, and filling their disk
# costs them the session.
_F_FREE_SPACE_MULTIPLIER = 1.25


def fsResolveDepositScratchDirectory():
    """Create and return a private directory for one deposit's tarball."""
    sRoot = os.path.join(
        os.path.expanduser("~"), ".vaibify", _S_SCRATCH_SUBDIRECTORY,
    )
    os.makedirs(sRoot, mode=0o700, exist_ok=True)
    return tempfile.mkdtemp(dir=sRoot)


def _ftChooseCodec(sScratchDirectory, sBaseName):
    """Return ``(sTarballPath, ffnOpenWriter, ffnOpenReader)``.

    The extension is the record of the choice, because the fallback
    path in ``reproduce.sh`` has to unpack these bytes on a machine
    that is not this one. The READER travels beside the writer because
    the deposit decompresses its own tarball before uploading it: an
    archive nobody can open is worse than no archive, since the row
    then says the environment is preserved.
    """
    tZstd = _ftResolveZstdCodec()
    if tZstd is not None:
        sExtension, ffnOpenWriter, ffnOpenReader = tZstd
        return (
            os.path.join(sScratchDirectory, sBaseName + sExtension),
            ffnOpenWriter, ffnOpenReader,
        )
    import gzip

    def ffnOpenGzipWriter(fileRaw):
        # mtime=0 so the container's bytes, not the clock, decide the
        # hash: two saves of one image must produce one tarball hash.
        return gzip.GzipFile(fileobj=fileRaw, mode="wb", mtime=0)

    return (
        os.path.join(sScratchDirectory, sBaseName + ".tar.gz"),
        ffnOpenGzipWriter,
        lambda fileRaw: gzip.GzipFile(fileobj=fileRaw, mode="rb"),
    )


def _ftResolveZstdCodec():
    """Return ``(sExtension, ffnOpenWriter, ffnOpenReader)`` or ``None``."""
    try:
        from compression import zstd as moduleZstd
    except ImportError:
        pass
    else:
        return (
            ".tar.zst",
            lambda fileRaw: moduleZstd.ZstdFile(fileRaw, "wb"),
            lambda fileRaw: moduleZstd.ZstdFile(fileRaw, "rb"),
        )
    try:
        import zstandard
    except ImportError:
        return None
    return (
        ".tar.zst",
        lambda fileRaw: zstandard.ZstdCompressor().stream_writer(fileRaw),
        lambda fileRaw: zstandard.ZstdDecompressor().stream_reader(fileRaw),
    )


def fiReadImageSizeBytes(sImageReference):
    """Return the daemon's declared size for one image, or 0."""
    from vaibify.reproducibility.environmentSnapshot import (
        _fsRunCheckedCommand,
    )
    try:
        return int(_fsRunCheckedCommand([
            "docker", "image", "inspect", "--format", "{{.Size}}",
            sImageReference,
        ]).strip())
    except Exception:  # noqa: BLE001 — unknown size skips the guard
        return 0


def _fnRefuseWithoutRoomOnDisk(sScratchDirectory, iImageBytes):
    """Raise ``OSError`` when the deposit would not fit on this disk."""
    if iImageBytes <= 0:
        return
    iFree = shutil.disk_usage(sScratchDirectory).free
    iNeeded = int(iImageBytes * _F_FREE_SPACE_MULTIPLIER)
    if iFree >= iNeeded:
        return
    raise OSError(
        "Not enough free space to save the container image: this "
        f"needs about {iNeeded // (1024 ** 3)} GB and "
        f"{iFree // (1024 ** 3)} GB is free. Free some space and "
        "start the deposit again."
    )


def ftSaveAndCompressImage(
    sImageReference, sScratchDirectory, fnReportProgress=None,
):
    """Save one image to a compressed tarball; return its four facts.

    Returns ``(sTarballPath, sTarballSha256, iTarballBytes,
    sImageStreamSha256)``. TWO hashes, over one artefact, because they
    answer different questions and neither can stand in for the other.
    The TARBALL hash covers the bytes actually uploaded, so a
    downloader can verify what they fetched. The IMAGE STREAM hash
    covers ``docker save``'s uncompressed output, so a later re-check
    on a different machine compares the IMAGE rather than the
    compressor: zstd and gzip give different bytes for one image, and
    so can two builds of one codec, which would report an identical
    image as diverged.

    The stream hash is taken by DECOMPRESSING the finished tarball
    rather than by tapping the save. That costs one pass and buys a
    guarantee worth more than it: a tarball that cannot be unpacked is
    never uploaded.

    ``fnReportProgress`` is called with ``(iUncompressedBytesRead,
    iImageSizeBytes)`` roughly once per chunk. A silent multi-minute
    save reads as a hang from the chair, and the researcher is the
    only one who can tell the difference.
    """
    iImageBytes = fiReadImageSizeBytes(sImageReference)
    _fnRefuseWithoutRoomOnDisk(sScratchDirectory, iImageBytes)
    sTarballPath, ffnOpenWriter, ffnOpenReader = _ftChooseCodec(
        sScratchDirectory, "environment-image",
    )
    _fnStreamSaveIntoTarball(
        sImageReference, sTarballPath, ffnOpenWriter, iImageBytes,
        fnReportProgress,
    )
    return (
        sTarballPath,
        "sha256:" + _hashing.fsHashFileSha256(sTarballPath),
        os.path.getsize(sTarballPath),
        "sha256:" + _fsHashDecompressedTarball(
            sTarballPath, ffnOpenReader,
        ),
    )


def _fsHashDecompressedTarball(sTarballPath, ffnOpenReader):
    """Return the sha256 of the tarball's UNCOMPRESSED contents."""
    with open(sTarballPath, "rb") as fileRaw:
        fileDecompressed = ffnOpenReader(fileRaw)
        try:
            return _hashing.fsHashFileObjectSha256(fileDecompressed)
        finally:
            fileDecompressed.close()


def _fnStreamSaveIntoTarball(
    sImageReference, sTarballPath, ffnOpenWriter, iImageBytes,
    fnReportProgress,
):
    """Save one image into the compressed tarball, or raise."""
    processSave = subprocess.Popen(
        ["docker", "save", sImageReference],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _fnCopyStreamThroughCompressor(
            processSave, sTarballPath, ffnOpenWriter, iImageBytes,
            fnReportProgress,
        )
        sStderr = (processSave.stderr.read() or b"").decode(
            "utf-8", "replace",
        )
    finally:
        processSave.stderr.close()
    if processSave.wait() != 0:
        raise subprocess.CalledProcessError(
            processSave.returncode, ["docker", "save", sImageReference],
            stderr=sStderr,
        )


def _fnCopyStreamThroughCompressor(
    processSave, sTarballPath, ffnOpenWriter, iImageBytes,
    fnReportProgress,
):
    """Copy the save stream into the compressed tarball, reporting bytes."""
    iRead = 0
    with open(sTarballPath, "wb") as fileRaw:
        fileCompressed = ffnOpenWriter(fileRaw)
        try:
            while True:
                baChunk = processSave.stdout.read(I_PROGRESS_CHUNK_BYTES)
                if not baChunk:
                    break
                fileCompressed.write(baChunk)
                iRead += len(baChunk)
                if fnReportProgress is not None:
                    fnReportProgress(iRead, iImageBytes)
        finally:
            fileCompressed.close()
            processSave.stdout.close()


def fsRecomputeImageStreamSha256(sImageReference):
    """Return the sha256 of ``docker save``'s output, or ``""``.

    Compression-free on purpose: this is the number the attestation
    re-check compares, and it must depend on the image alone. Empty
    means the image is not on this machine or the daemon is
    unreachable -- which the caller reports as UNAVAILABLE, never as a
    difference.
    """
    processSave = subprocess.Popen(
        ["docker", "save", sImageReference],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        sDigest = _hashing.fsHashFileObjectSha256(processSave.stdout)
    finally:
        processSave.stdout.close()
    if processSave.wait() != 0:
        return ""
    return "sha256:" + sDigest


def fsJudgeDepositProvenance(sImageReference, dictAttestation):
    """Return whether this deposit IS the environment or REPRODUCES it.

    Two claims that must not be conflated. ``original`` says the
    archived environment is the one that produced these results;
    ``verified-equivalent`` says it reproduces them. Both are good
    evidence, and only the second is true on the LATE-ARCHIVE path --
    the researcher whose original image was pruned, who rebuilt one,
    reran, and is depositing the rebuild.

    The distinction is drawn from a recorded fact rather than asked
    about: a PASSING attestation names the image its rerun ran under,
    so a deposit of a DIFFERENT image than that one is by definition
    covering something that reproduced the manifest rather than
    something that produced it. Only a passing rerun proves
    equivalence: a failed or unfinished one naming another image
    proves nothing, and promoting the claim on it would record an
    equivalence nobody demonstrated. With no attestation -- or none
    that passed -- the envelope's pin is simply the image the
    container ran, and ``original`` is the accurate claim.
    """
    dictAttestation = dictAttestation or {}
    if dictAttestation.get("sStatus") != S_STATUS_PASSED:
        return imageArchive.S_PROVENANCE_ORIGINAL
    sAttested = str(dictAttestation.get("sImageDigest") or "")
    if not sAttested or sAttested == sImageReference:
        return imageArchive.S_PROVENANCE_ORIGINAL
    return imageArchive.S_PROVENANCE_VERIFIED_EQUIVALENT


# A container image is software, not a dataset. Zenodo asks for no
# extra field for this type, unlike "publication" or "image", so it
# costs nothing and describes the artefact honestly.
S_IMAGE_UPLOAD_TYPE = "software"


def fdictDepositImageArchive(
    clientZenodo, sImageReference, sArchitecture, sScratchDirectory,
    dictMetadata, fnReportProgress=None, dictAttestation=None,
    fnReportUploadStarted=None,
):
    """Save, upload and publish one image; return its deposit record.

    The order matters. The tarball is hashed once it is written and
    nothing touches the file before it is uploaded, so the recorded
    sha256 is of the bytes that went up. The draft is published only after every
    byte is uploaded, because an unpublished draft can be discarded
    and a published version cannot. And the VERSION doi is taken from
    the publish response, never the concept doi beside it: the concept
    doi always resolves to the newest version, so recording it would
    quietly repoint this result at whatever environment is deposited
    last.

    ``fnReportUploadStarted`` is called once with the tarball's size
    as the upload begins, because the save's byte counter stops
    moving at that moment and the upload of those bytes is the longer
    half from a laptop.
    """
    sTarballPath, sSha256, iBytes, sStreamSha256 = ftSaveAndCompressImage(
        sImageReference, sScratchDirectory, fnReportProgress,
    )
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi="", sConceptDoi="", sTarballSha256=sSha256,
        iTarballBytes=iBytes, sDepositedIso="",
        sProvenance=fsJudgeDepositProvenance(
            sImageReference, dictAttestation,
        ),
        sImageDigest=sImageReference, sArchitecture=sArchitecture,
        sTarballName=os.path.basename(sTarballPath),
        sImageStreamSha256=sStreamSha256,
    )
    # Stamped BEFORE the draft is created, because Zenodo's metadata
    # has no field for "which image is this" and a later reference to
    # this record has nothing else to check itself against.
    # Stamp in the vaibify shape, THEN translate. The fingerprint is
    # appended to `sDescription`, and it is read back out of the
    # published record's `description` when a researcher references an
    # existing deposit -- so translating first would drop the one
    # field that lets a reference be verified at all.
    dictDraft = clientZenodo.fdictCreateDraft(
        zenodoClient.fdictBuildApiMetadata(
            imageArchive.fdictStampDepositMetadata(
                dictMetadata, dictRecord,
            ),
            S_IMAGE_UPLOAD_TYPE,
        ),
    )
    iDepositId = dictDraft["id"]
    try:
        if fnReportUploadStarted is not None:
            fnReportUploadStarted(iBytes)
        clientZenodo.fnUploadToBucket(
            dictDraft["links"]["bucket"], sTarballPath,
        )
        dictPublished = clientZenodo.fdictPublishDraft(iDepositId)
    except Exception:
        _fnDiscardDraft(clientZenodo, iDepositId)
        raise
    dictRecord["sVersionDoi"] = dictPublished.get("doi") or ""
    dictRecord["sConceptDoi"] = dictPublished.get("conceptdoi") or ""
    dictRecord["sDepositedIso"] = datetime.now(timezone.utc).isoformat()
    return dictRecord


def _fnDiscardDraft(clientZenodo, iDepositId):
    """Delete a draft whose upload or publish failed, best effort.

    A draft left behind is a half-finished deposit the researcher will
    find later and cannot explain. Failing to delete it is logged with
    the id, so they can, rather than being swallowed into the original
    error.
    """
    try:
        clientZenodo.fnDeleteDraft(iDepositId)
    except Exception:  # noqa: BLE001 — the original failure is the news
        logger.warning(
            "Zenodo draft %s could not be discarded after a failed "
            "environment-archive deposit; discard it manually.",
            iDepositId, exc_info=True,
        )


def fdictRecheckArchiveAgainstLocalImage(filesRepo, dictEnvironment=None):
    """Return the attestation-time verdict on the deposited environment.

    ``dictEnvironment`` is read from ``filesRepo`` when not supplied.
    Costs one full ``docker save`` of the pinned image and no network,
    and only when there is something to compare: an absent record and
    an image known to have been LOADED from the deposit are both
    answered without touching the daemon. The second is the one that
    matters -- re-hashing a download against itself matches always,
    and reporting that as a pass would put a comparison nobody made
    into a scientific record.
    """
    if dictEnvironment is None:
        dictEnvironment = fdictReadEnvironmentJson(filesRepo)
    dictRecord = imageArchive.fdictReadArchiveRecord(dictEnvironment)
    dictContainer = (dictEnvironment or {}).get("dictContainer") or {}
    bLoaded = imageArchive.fbImageWasLoadedFromArchive(filesRepo)
    if dictRecord is None or bLoaded:
        return imageArchive.fdictJudgeArchiveRecheck(
            dictEnvironment, "", bLoaded,
        )
    return imageArchive.fdictJudgeArchiveRecheck(
        dictEnvironment,
        fsRecomputeImageStreamSha256(
            str(dictContainer.get("sImageDigest") or ""),
        ),
        False,
    )
