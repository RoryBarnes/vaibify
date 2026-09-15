"""Promoting a sandbox deposit to a permanent one: policy and records.

Zenodo's sandbox and its production instance are separate systems and
nothing transfers between them, so "make permanent" can only mean:
deposit again on zenodo.org under a production token, and record the
new DOI. The sandbox record stays where it is until Zenodo clears it,
which vaibify neither causes nor observes.

That operation mints a permanent DOI in the middle of a long upload,
and **a minted DOI that nobody wrote down cannot be recovered by
guessing**. So the promotion is bracketed by a record in the sync
sidecar: intent before anything starts, and the deposit id made
durable the moment the draft exists -- before the first byte goes up,
not after the publish returns. Everything else here exists to make
that record answerable later: the file set is stored with SHA-256,
MD5 and size, because Zenodo reports MD5 and size for a deposition's
files and reconciliation has to compare what it is shown against what
was intended.

The timestamp is UTC ISO, never monotonic. Monotonic time is
meaningless across the restart this record exists to survive.
"""

__all__ = [
    "PromotionRefusedError",
    "S_OUTCOME_ERRORED",
    "S_OUTCOME_GONE",
    "S_OUTCOME_MISMATCHED",
    "S_OUTCOME_PUBLISHABLE",
    "S_OUTCOME_PUBLISHED",
    "S_OUTCOME_RESUMABLE",
    "S_OUTCOME_UNKNOWN",
    "fdictReconcilePromotion",
    "flistOfferedActionsFor",
    "fsStampPromotionIdIntoDescription",
    "S_BYTES_FROM_ARCHIVE",
    "S_BYTES_FROM_LOCAL_IMAGE",
    "S_LANE_IMAGE",
    "S_LANE_PROJECT",
    "S_PHASE_ADOPTED",
    "S_PHASE_DRAFTED",
    "S_PHASE_INTENDED",
    "S_PHASE_PUBLISHED",
    "S_PHASE_UPLOADING",
    "fdictBuildPendingPromotion",
    "fnRefuseUnlessSandboxDeposit",
    "fnRefuseUnlessTokenValidates",
    "ftPromoteImageArchive",
    "fsChooseImageByteSource",
    "fdictDescribeFileForPromotion",
    "fsGeneratePromotionId",
]

import os
import uuid
from datetime import datetime, timezone

from vaibify.reproducibility._hashing import ftHashFileSha256AndMd5


S_LANE_IMAGE = "image"
S_LANE_PROJECT = "project"

# The phases a promotion passes through, and each is a different
# recovery question. "intended" means nothing remote exists yet;
# "drafted" means a deposit id was minted and a draft may hold files;
# "published" means a DOI exists and only adoption is left.
S_PHASE_INTENDED = "intended"
S_PHASE_DRAFTED = "drafted"
S_PHASE_UPLOADING = "uploading"
S_PHASE_PUBLISHED = "published"
S_PHASE_ADOPTED = "adopted"



def fsGeneratePromotionId():
    """Return an id that also rides in the remote deposit's metadata.

    Stamped into the deposit description so a remote mutation can be
    bound to the intent that started it. Without it a writable local
    file is the only thing connecting the two, and a researcher with
    two interrupted promotions has no way to tell which draft belongs
    to which.
    """
    return "promotion-" + uuid.uuid4().hex


def fdictDescribeFileForPromotion(sPath, sBasename=""):
    """Return one file's identity as reconciliation will need it.

    Zenodo reports a deposition file's ``checksum`` as MD5 and its
    ``filesize`` in bytes, so both are recorded beside the SHA-256
    vaibify uses everywhere else -- reconciliation compares what the
    archive shows against what was intended, and it can only compare
    in the vocabulary the archive speaks.

    The basename mapping is recorded because a Zenodo deposit is FLAT:
    files land under their basenames, and a deposit key is the only
    handle reconciliation has on a local path.

    Both digests come from ONE read, and the md5 is requested with
    ``usedforsecurity=False``: it is not used for security here, and
    without that flag ``hashlib.md5()`` raises outright on a
    FIPS-enabled host -- which institutional Linux builds are, so the
    failure would land on a researcher's cluster and nowhere in
    testing.
    """
    sSha256, sMd5 = ftHashFileSha256AndMd5(sPath)
    return {
        "sSourcePath": sPath,
        "sBasename": sBasename or os.path.basename(sPath),
        "sSha256": "sha256:" + sSha256,
        "sMd5": sMd5,
        "iBytes": os.path.getsize(sPath),
    }


def fdictBuildPendingPromotion(
    sPromotionId, sLane, sTargetService, listFiles,
    iParentDepositId=0, sPhase=S_PHASE_INTENDED,
):
    """Return the crash-recovery record for one promotion, before it starts.

    Written before anything remote happens. A record whose phase is
    still ``intended`` names no deposit and can be discarded locally;
    every later phase names one, and from there only Zenodo can say
    what really happened.
    """
    return {
        "sPromotionId": sPromotionId,
        "sLane": sLane,
        "sTargetService": sTargetService,
        "iParentDepositId": int(iParentDepositId or 0),
        "listFiles": list(listFiles or []),
        "iDepositId": 0,
        "sPhase": sPhase,
        "sStartedIso": datetime.now(timezone.utc).isoformat(),
    }


S_BYTES_FROM_LOCAL_IMAGE = "local-image"
S_BYTES_FROM_ARCHIVE = "sandbox-archive"


class PromotionRefusedError(Exception):
    """A promotion vaibify declined to start, with the reason named."""


def fnRefuseUnlessSandboxDeposit(dictRecord, sWhat):
    """Raise unless this deposit is KNOWN to be a sandbox deposit.

    Checked in the route rather than inferred from the button's
    absence: a request is not a click, and re-depositing a permanent
    record would spend a second production DOI on bytes that already
    have one. ``unknown`` is refused too -- promoting on a guess is
    the mirror of claiming permanence on one.
    """
    from vaibify.reproducibility import archivePermanence
    sPermanence = archivePermanence.fsClassifyDepositRecord(dictRecord)
    if sPermanence == archivePermanence.S_PERMANENCE_SANDBOX:
        return
    if sPermanence == archivePermanence.S_PERMANENCE_PERMANENT:
        raise PromotionRefusedError(
            sWhat + " is already on production Zenodo, so there is "
            "nothing to promote. Nothing was deposited."
        )
    raise PromotionRefusedError(
        sWhat + " does not record which Zenodo instance it lives on, "
        "so vaibify cannot say it is a sandbox deposit and will not "
        "spend a production DOI on a guess. Nothing was deposited."
    )


def fnRefuseUnlessTokenValidates(clientZenodo):
    """Raise unless the production credential actually works.

    The last of the cheap checks. Existence is not validity: a token
    that was revoked, or pasted with a character missing, fails at the
    draft -- which on this lane is AFTER a multi-gigabyte ``docker
    save``. One authenticated read costs a second and moves that
    failure to before the expensive half.
    """
    from vaibify.reproducibility import zenodoClient
    try:
        clientZenodo.flistSearchDeposits("")
    except zenodoClient.ZenodoAuthError:
        raise PromotionRefusedError(
            "Zenodo rejected the stored production token. Replace it "
            "and try again; nothing was deposited."
        ) from None
    except zenodoClient.ZenodoError as errorZenodo:
        raise PromotionRefusedError(
            "Vaibify could not reach production Zenodo to check the "
            "stored token (" + str(errorZenodo) + "), so it did not "
            "start a deposit that would have failed at the end."
        ) from None


def fsChooseImageByteSource(sImageReference, dictEnvironment):
    """Return which bytes a promotion should upload, or raise.

    The local image first, exactly as the original deposit did: it
    deposits the image the ENVELOPE pins, so the resulting record is
    built from what was actually saved.

    The fallback fires only when the image is POSITIVELY ABSENT from
    the daemon -- never merely because a save failed. A ``docker
    save`` fails for a stopped daemon, a full disk or a compression
    error, and substituting archived bytes for any of those hides a
    local problem the researcher should be told about; on a full disk
    the fallback needs the same space anyway.

    It also requires the record to cover this envelope. The fallback
    copies bytes whose only claim to identity is the record itself,
    so promoting a record that does not cover the envelope would
    spend a permanent DOI on a deposit that can never satisfy the
    gate. The local-image path needs no such check; the fallback
    cannot do without one.
    """
    from vaibify.reproducibility import imageArchive
    from vaibify.reproducibility.environmentSnapshot import (
        fbImageExistsLocally,
    )
    bPresent = fbImageExistsLocally(sImageReference)
    if bPresent is True:
        return S_BYTES_FROM_LOCAL_IMAGE
    if bPresent is None:
        raise PromotionRefusedError(
            "vaibify could not ask Docker whether this image is still "
            "on this machine, so it will not guess which bytes to "
            "deposit. Start Docker and try again. Nothing was "
            "deposited."
        )
    listMismatch = imageArchive.flistDescribeArchiveMismatch(dictEnvironment)
    if listMismatch:
        raise PromotionRefusedError(
            "The image this envelope pins is not on this machine, and "
            "the sandbox deposit cannot stand in for it: " +
            " ".join(listMismatch) + " Nothing was deposited."
        )
    return S_BYTES_FROM_ARCHIVE


def ftPromoteImageArchive(
    dictContainer, sToken, filesRepo, sSidecarKey, dictMetadata,
    dictProgressHooks,
):
    """Re-deposit one image archive on production; return record and id.

    The whole promotion, minus the HTTP and the progress record: the
    byte source is chosen, the intent is written down, the upload runs
    against an explicitly-production client, and the deposit id is
    made durable the moment the draft exists.

    The pending record deliberately OUTLIVES this call. It is settled
    once the new record has been stamped AND the manifest re-pinned,
    because the re-pin returns False rather than raising -- so there
    is an interior state in which the DOI is recorded and the envelope
    is not yet coherent, and that is precisely the state recovery must
    still be able to see.
    """
    import shutil
    from vaibify.reproducibility import imageArchive, imageDeposit
    from vaibify.reproducibility.environmentSnapshot import (
        fdictReadEnvironmentJson,
    )
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    dictOld = dictContainer.get(imageArchive.S_IMAGE_ARCHIVE_KEY) or {}
    sImageReference = str(dictContainer.get("sImageDigest") or "")
    sSource = fsChooseImageByteSource(
        sImageReference, fdictReadEnvironmentJson(filesRepo),
    )
    sPromotionId = fsGeneratePromotionId()
    sScratchDirectory = imageDeposit.fsResolveDepositScratchDirectory()
    try:
        tTarball = _ftObtainPromotionBytes(
            sSource, sImageReference, dictOld, sScratchDirectory,
            dictProgressHooks,
        )
        _fnRecordPromotionIntent(
            filesRepo, sSidecarKey, sPromotionId, tTarball,
        )
        return imageDeposit.fdictUploadAndPublishImageArchive(
            ZenodoClient("zenodo", sToken=sToken),
            sImageReference,
            str(dictContainer.get("sArchitecture") or ""),
            _fdictStampPromotionIntoMetadata(dictMetadata, sPromotionId),
            tTarball,
            fnReportUploadStarted=dictProgressHooks.get(
                "fnReportUploadStarted",
            ),
            fnReportVerifying=dictProgressHooks.get("fnReportVerifying"),
            fnReportDraftCreated=lambda iDepositId: (
                _fnRecordDraftCreated(
                    filesRepo, sSidecarKey, sPromotionId, iDepositId,
                )
            ),
            # Carried forward VERBATIM. `original` and
            # `verified-equivalent` are distinct scientific claims and
            # a re-deposit establishes neither, so the judgement is
            # made only for a record that carries none.
            sProvenance=str(dictOld.get("sProvenance") or ""),
        ), sPromotionId
    finally:
        shutil.rmtree(sScratchDirectory, ignore_errors=True)


def _fdictStampPromotionIntoMetadata(dictMetadata, sPromotionId):
    """Return the deposit metadata carrying this promotion's id.

    Rides in the description, the same way the image deposit's
    fingerprint does, so recovery can bind a remote mutation to the
    intent that started it rather than to a writable local file.
    """
    dictStamped = dict(dictMetadata or {})
    dictStamped["sDescription"] = fsStampPromotionIdIntoDescription(
        dictStamped.get("sDescription"), sPromotionId,
    )
    return dictStamped


def _ftObtainPromotionBytes(
    sSource, sImageReference, dictOldRecord, sScratchDirectory,
    dictProgressHooks,
):
    """Return the save-tuple for whichever byte source was chosen."""
    from vaibify.reproducibility import imageAcquisition, imageDeposit
    if sSource == S_BYTES_FROM_LOCAL_IMAGE:
        return imageDeposit.ftSaveAndCompressImage(
            sImageReference, sScratchDirectory,
            dictProgressHooks.get("fnReportSaveProgress"),
        )
    fnReportSave = dictProgressHooks.get("fnReportSaveProgress")
    sTarballPath = imageAcquisition.fsDownloadVerifiedTarball(
        dictOldRecord, sScratchDirectory,
        lambda dictStatus: fnReportSave and fnReportSave(
            int(dictStatus.get("iBytes") or 0),
            int(dictStatus.get("iTotalBytes") or 0),
        ),
    )
    # The md5 comes from the RECORD, like the hashes beside it: these
    # are the sandbox deposit's own bytes, verified against that
    # record on the way down. A record predating the field carries no
    # md5, and the post-deposit check abstains rather than refusing.
    return (
        sTarballPath,
        str(dictOldRecord.get("sTarballSha256") or ""),
        int(dictOldRecord.get("iTarballBytes") or 0),
        str(dictOldRecord.get("sImageStreamSha256") or ""),
        str(dictOldRecord.get("sTarballMd5") or ""),
    )


def _fnRecordPromotionIntent(
    filesRepo, sSidecarKey, sPromotionId, tTarball,
):
    """Write the crash-recovery record before anything remote happens."""
    from vaibify.reproducibility import syncBookkeeping
    syncBookkeeping.fnUpdatePendingPromotion(
        filesRepo, sSidecarKey, sPromotionId,
        fdictBuildPendingPromotion(
            sPromotionId, S_LANE_IMAGE, "zenodo",
            [fdictDescribeFileForPromotion(tTarball[0])],
        ),
    )


def _fnRecordDraftCreated(
    filesRepo, sSidecarKey, sPromotionId, iDepositId,
):
    """Make the deposit id durable before the first byte goes up."""
    from vaibify.reproducibility import syncBookkeeping
    syncBookkeeping.fnUpdatePendingPromotion(
        filesRepo, sSidecarKey, sPromotionId,
        {"iDepositId": int(iDepositId), "sPhase": S_PHASE_DRAFTED},
    )


# ── Reconciliation: ask Zenodo, and let it answer ──
#
# Seven outcomes, because "the draft has some files", "the draft has
# all of them", "it was published", "it holds something else", "it is
# not there", "Zenodo says it errored" and "nobody could ask" call for
# different things. Collapsing any pair loses a remedy.
#
# `unknown` and `gone` are the pair that matters most: a positive 404
# is an ANSWER, and a timeout is not. Discarding a record on a timeout
# throws away the only handle on a DOI that may exist.

S_OUTCOME_RESUMABLE = "resumable"
S_OUTCOME_PUBLISHABLE = "publishable"
S_OUTCOME_PUBLISHED = "published"
S_OUTCOME_MISMATCHED = "mismatched"
S_OUTCOME_GONE = "gone"
S_OUTCOME_ERRORED = "errored"
S_OUTCOME_UNKNOWN = "unknown"

S_ACTION_RESUME = "resume"
S_ACTION_ADOPT = "adopt"
S_ACTION_DISCARD = "discard"

# `resumable` deliberately offers DISCARD ONLY, and that is a
# narrowing worth stating rather than a gap. Resuming a partly-uploaded
# draft means uploading bytes again, and the bytes the promotion
# prepared did not survive the interruption -- the scratch directory is
# removed when the operation that made it ends. Re-producing them and
# uploading those instead would make the draft hold bytes whose hashes
# no longer match the record reconciliation compares against, which is
# "upload whatever is there now" wearing a hash check. Discarding and
# promoting again is the honest remedy, and it costs nothing but time:
# the draft was never published, so no DOI is lost.
#
# `publishable` DOES offer resume, because there the remaining step
# needs no bytes at all: every intended file is already up and matching,
# and only the publish call is missing. That is the case this lane
# exists for -- an upload that finished and a publish that never
# returned.
_DICT_OFFERED_ACTIONS = {
    S_OUTCOME_RESUMABLE: (S_ACTION_DISCARD,),
    S_OUTCOME_PUBLISHABLE: (S_ACTION_RESUME, S_ACTION_DISCARD),
    S_OUTCOME_PUBLISHED: (S_ACTION_ADOPT,),
    S_OUTCOME_MISMATCHED: (),
    S_OUTCOME_GONE: (S_ACTION_DISCARD,),
    S_OUTCOME_ERRORED: (),
    S_OUTCOME_UNKNOWN: (),
}

# The promotion id rides in the deposit's description, the same way
# the image deposit's fingerprint does. Without it a writable local
# file is the only thing binding a remote mutation to the intent that
# started it, and a researcher with two interrupted promotions has no
# way to tell which draft belongs to which.
_S_STAMP_PREFIX = "vaibify-promotion: "


def fsStampPromotionIdIntoDescription(sDescription, sPromotionId):
    """Return the description carrying this promotion's id."""
    return (
        str(sDescription or "").rstrip() + "\n\n" +
        _S_STAMP_PREFIX + str(sPromotionId or "")
    ).strip()


def fbDescriptionCarriesPromotionId(sDescription, sPromotionId):
    """Return True iff a remote deposit names this promotion."""
    if not sPromotionId:
        return False
    return (_S_STAMP_PREFIX + str(sPromotionId)) in str(sDescription or "")


def flistOfferedActionsFor(sOutcome):
    """Return the actions one outcome licenses, and no others."""
    return list(_DICT_OFFERED_ACTIONS.get(sOutcome, ()))


def fdictReconcilePromotion(dictRecord, fdictGetDeposit):
    """Ask Zenodo what really happened to one promotion.

    ``fdictGetDeposit`` is the client call, injected so this stays a
    pure decision over one response: the caller owns which instance is
    asked and which token is used.

    Comparison is on MD5 and size against the record, and on deposit
    KEYS against the recorded basename mapping -- never on filenames
    alone, which a flat deposit makes ambiguous the moment two source
    paths share one.
    """
    from vaibify.reproducibility import zenodoClient
    if not int(dictRecord.get("iDepositId") or 0):
        # Nothing remote was ever created, so nothing remote can be
        # asked: the record is purely local and discardable.
        return _fdictOutcome(S_OUTCOME_GONE, "No deposit was created.")
    try:
        dictDeposit = fdictGetDeposit(int(dictRecord["iDepositId"]))
    except zenodoClient.ZenodoNotFoundError:
        return _fdictOutcome(
            S_OUTCOME_GONE,
            "Zenodo answered 404: this deposit no longer exists.",
        )
    except Exception as errorAsked:  # noqa: BLE001 — unreadable, not absent
        return _fdictOutcome(
            S_OUTCOME_UNKNOWN,
            "Vaibify could not ask Zenodo what happened to this "
            "deposit (" + str(errorAsked) + "), so the record is "
            "kept. A question nobody answered is not a 'no'.",
        )
    return _fdictJudgeDeposit(dictRecord, dictDeposit)


def _fdictJudgeDeposit(dictRecord, dictDeposit):
    """Return the outcome one Zenodo deposition response licenses."""
    sState = str(dictDeposit.get("state") or "")
    listMissing = _flistFilesNotMatching(dictRecord, dictDeposit)
    if sState == "error":
        return _fdictOutcome(
            S_OUTCOME_ERRORED,
            "Zenodo reports this deposit as errored. Vaibify will "
            "neither resume nor discard it; open it on Zenodo and "
            "follow their guidance.",
        )
    if sState == "done":
        if listMissing:
            return _fdictOutcome(
                S_OUTCOME_MISMATCHED,
                "This published record does not hold the files this "
                "promotion intended: " + ", ".join(listMissing) + ".",
            )
        return _fdictOutcome(
            S_OUTCOME_PUBLISHED,
            "This promotion completed: the record is published and "
            "holds the intended files.",
            sDoi=str(dictDeposit.get("doi") or ""),
            sConceptDoi=str(dictDeposit.get("conceptdoi") or ""),
        )
    if listMissing:
        return _fdictOutcome(
            S_OUTCOME_RESUMABLE,
            "This deposit is still a draft and is missing " +
            str(len(listMissing)) + " of its files.",
        )
    return _fdictOutcome(
        S_OUTCOME_PUBLISHABLE,
        "This deposit is still a draft and already holds every "
        "intended file; only the publish is left.",
    )


def _flistFilesNotMatching(dictRecord, dictDeposit):
    """Return the intended basenames the deposit does not serve intact.

    Zenodo reports a deposition file's ``checksum`` as MD5 and its
    ``filesize`` in bytes. Both are compared: a same-size different
    file is exactly what a filename-only check would call a match.
    """
    dictRemote = {
        str(dictFile.get("filename") or dictFile.get("key") or ""): dictFile
        for dictFile in dictDeposit.get("files") or []
        if isinstance(dictFile, dict)
    }
    listMissing = []
    for dictIntended in dictRecord.get("listFiles") or []:
        sBasename = str(dictIntended.get("sBasename") or "")
        dictFound = dictRemote.get(sBasename)
        if not dictFound or not _fbFileAgrees(dictIntended, dictFound):
            listMissing.append(sBasename or "(unnamed file)")
    return listMissing


def _fbFileAgrees(dictIntended, dictRemote):
    """Return True iff one remote file matches the intended bytes."""
    sRemoteMd5 = str(dictRemote.get("checksum") or "").split(":")[-1]
    iRemoteBytes = dictRemote.get("filesize")
    if sRemoteMd5 != str(dictIntended.get("sMd5") or ""):
        return False
    return int(iRemoteBytes or -1) == int(dictIntended.get("iBytes") or 0)


def _fdictOutcome(sOutcome, sMessage, sDoi="", sConceptDoi=""):
    """Return one reconciliation verdict with the actions it licenses."""
    return {
        "sOutcome": sOutcome,
        "sMessage": sMessage,
        "listActions": flistOfferedActionsFor(sOutcome),
        "sDoi": sDoi,
        "sConceptDoi": sConceptDoi,
    }
