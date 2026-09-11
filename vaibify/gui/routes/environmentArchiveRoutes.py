"""HTTP routes for the environment archive.

Two routes for one row, because the two halves are different questions
with different failure modes. The ANSWER route records the researcher's
decision — the Level 2 criterion, which ``declined`` satisfies. The
DEPOSIT route produces and publishes the archive — the Level 3
criterion, which never reads the answer, so declining is a decision
rather than a lock.

Both are ``bAgentSafe: False``. The second is a security decision, not
a preference: publishing to Zenodo under the researcher's credentials
is outward-facing and irreversible, so a compromised container agent
must not be able to trigger it.

Its own module rather than a section of ``reproducibilityRoutes``
because the two change for different reasons: that module owns the
readiness verifiers and the rebuild attestation, while this one owns a
deposit lane with a credential crossing, a durable task and a
progress record. The shared piece — the re-hash the attestation
records — lives in ``imageDeposit``, which both import.
"""

__all__ = ["fnRegisterAll"]

import asyncio
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from .. import archiveProgress
from ..actionCatalog import ffnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictCommitWorkflowSave,
    fdictRequireLaneTupleForCommit,
    ffilesForWorkflow,
    fgenericRunWorkerUnderTheDrain,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_MODE_C_DURABLE,
    ffnDeclareCarrierMode,
)
from ...reproducibility import imageArchive, imageDeposit
from ...reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
    fnWriteEnvironmentJson,
)
from ...reproducibility.l3Attestation import fdictReadAttestation

import logging

logger = logging.getLogger(__name__)


def _fsRequireProjectRepo(dictWorkflow):
    """Return the workflow's project repo path or raise HTTP 409."""
    sProjectRepo = (dictWorkflow or {}).get("sProjectRepoPath") or ""
    if not sProjectRepo:
        raise HTTPException(
            409,
            "This workflow has no project repository, so it has no "
            "reproducibility envelope to archive.",
        )
    return sProjectRepo


def _fnRegisterAnswerEnvironmentArchive(app, dictCtx):
    """Register POST /api/workflow/{id}/environment-archive/answer."""

    @ffnAgentAction("answer-environment-archive")
    @app.post(
        "/api/workflow/{sContainerId}/environment-archive/answer"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictAnswerEnvironmentArchive(
        sContainerId: str, dictBody: dict, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        sAnswer = _fsValidateArchiveAnswer(dictBody)
        if sAnswer == imageArchive.S_ANSWER_REFERENCED:
            await _fdictAdoptExistingDeposit(
                dictCtx, sContainerId, dictWorkflow, dictBody,
                requestHttp,
            )
        return _fdictRecordArchiveAnswer(
            dictCtx, sContainerId, dictWorkflow, sAnswer, requestHttp,
        )


# The researcher taking their answer back. Not one of
# imageArchive's three answers -- those are things a project can BE,
# and this is the absence of all of them.
S_ANSWER_CLEARED = "cleared"


def _fsValidateArchiveAnswer(dictBody):
    """Return the requested answer, or raise HTTP 422.

    ``archived`` is refused HERE and only here: it is not a claim a
    caller may assert, it is what the deposit route writes when a
    deposit has actually been published. Accepting it would let the
    Level 2 row go green over an archive that does not exist, which is
    precisely the shape this feature exists to prevent.
    """
    sAnswer = str((dictBody or {}).get("sAnswer") or "").strip()
    # Clearing is an answer the researcher may give back. Declining is
    # meant to be revocable -- the Level 3 criterion reads the deposit
    # record and never this answer, precisely so a decline is a
    # decision rather than a lock -- but a radio cannot be unselected
    # by clicking it, so without this the recorded answer was final in
    # practice (researcher-reported, 2026-09-08). It returns the
    # project to UNANSWERED, which fails Level 2 again; that is the
    # honest consequence and the researcher's own choice, not a
    # silent demotion.
    if sAnswer == S_ANSWER_CLEARED:
        return sAnswer
    if sAnswer == imageArchive.S_ANSWER_ARCHIVED:
        raise HTTPException(
            422,
            "'archived' is not an answer you can record directly — it "
            "is what vaibify writes once a deposit has been published. "
            "Use the deposit action, or answer 'referenced' with the "
            "version DOI of a deposit that already holds this image.",
        )
    if sAnswer not in (
        imageArchive.S_ANSWER_REFERENCED, imageArchive.S_ANSWER_DECLINED,
    ):
        raise HTTPException(
            422,
            "sAnswer must be 'referenced' (with sVersionDoi) or "
            "'declined'.",
        )
    return sAnswer


def _fdictRecordArchiveAnswer(
    dictCtx, sContainerId, dictWorkflow, sAnswer, requestHttp,
):
    """Persist one answer to the environment-archive question."""
    if not archiveProgress.fbDepositIsLive(sContainerId):
        # A failed attempt's reason stops being news once the
        # researcher has answered; a live attempt is still theirs to
        # watch and is left alone.
        archiveProgress.fnForgetDeposit(sContainerId)
    if sAnswer == S_ANSWER_CLEARED:
        # Removed, never stored as a value: an empty string in this
        # block would be a recorded answer that means nothing, and
        # `fbImageArchiveQuestionSettled` would have to special-case
        # it. Absence is what "unanswered" already means everywhere
        # that reads this key.
        dictWorkflow.pop(imageArchive.S_IMAGE_ARCHIVE_KEY, None)
    else:
        dictWorkflow[imageArchive.S_IMAGE_ARCHIVE_KEY] = {
            "sAnswer": sAnswer,
            "sAnsweredIso": datetime.now(timezone.utc).isoformat(),
        }
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "The environment-archive answer",
    )
    return {"sAnswer": sAnswer}


async def _fdictAdoptExistingDeposit(
    dictCtx, sContainerId, dictWorkflow, dictBody, requestHttp,
):
    """Verify a supplied DOI covers this envelope, then record it.

    The verification is not a formality. A concept DOI resolves to the
    record's newest version, so the record that comes back carries a
    different DOI than the one asked for -- and a deposit vaibify did
    not stamp cannot be read at all. Both are refused with the reason,
    because the alternative is a Level 3 criterion satisfied by a
    string nobody checked.
    """
    sVersionDoi = str((dictBody or {}).get("sVersionDoi") or "").strip()
    if not sVersionDoi:
        raise HTTPException(
            422, "Answering 'referenced' needs the deposit's version DOI.",
        )
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
    dictContainer = _fdictRequireEnvelopeContainerBlock(filesRepo)
    dictRecord = await asyncio.to_thread(
        _fdictVerifyReferencedDeposit,
        dictWorkflow, sVersionDoi, dictContainer,
    )
    await _fdictWriteArchiveRecordUnderTheDrain(
        dictCtx, sContainerId, dictWorkflow, dictRecord, requestHttp,
    )


def _fdictRequireEnvelopeContainerBlock(filesRepo):
    """Return the envelope's container block or raise HTTP 409."""
    dictContainer = (
        fdictReadEnvironmentJson(filesRepo) or {}
    ).get("dictContainer")
    if not isinstance(dictContainer, dict) or not dictContainer.get(
        "sImageDigest",
    ):
        raise HTTPException(
            409,
            "This project's environment snapshot pins no container "
            "image yet, so there is nothing an archive could cover. "
            "Regenerate the envelope while the container is running.",
        )
    # Refused BEFORE the image is saved, because the cost of learning
    # this late is the whole operation: `docker save` plus compression
    # plus the upload, minutes and hundreds of megabytes, ending in a
    # published record that can never be checked. A deposit records
    # the architecture it covers -- a manifest-list digest spans
    # several platforms and pins none of them -- so a record written
    # without one matches no envelope, and the row that should read
    # "archived" reads "could not check" forever
    # (researcher-reported, 2026-09-09).
    if not dictContainer.get("sArchitecture"):
        raise HTTPException(
            409,
            "This project's environment snapshot records the image "
            "but not the architecture it was built for, and a "
            "deposit that does not say which build it covers can "
            "never be checked against this envelope. Regenerate the "
            "envelope while the container is running, then deposit.",
        )
    return dictContainer


def _fdictVerifyReferencedDeposit(dictWorkflow, sVersionDoi, dictContainer):
    """Fetch the referenced record and build its archive record, or raise."""
    from ...gui.workflowManager import fsZenodoRecordIdFromDoi
    from ...reproducibility import zenodoClient
    sRecordId = fsZenodoRecordIdFromDoi(sVersionDoi)
    if not sRecordId:
        raise HTTPException(
            422,
            f"{sVersionDoi!r} is not a Zenodo DOI. A Zenodo DOI ends "
            "in '/zenodo.<number>'.",
        )
    clientZenodo = zenodoClient.ZenodoClient(
        dictWorkflow.get("sZenodoService") or "sandbox",
    )
    try:
        dictZenodoRecord = clientZenodo.fdictFetchPublishedRecord(sRecordId)
    except zenodoClient.ZenodoError as errorZenodo:
        raise HTTPException(502, str(errorZenodo)) from None
    listProblems = imageArchive.flistDescribeReferenceProblems(
        dictZenodoRecord, sVersionDoi,
        str(dictContainer.get("sImageDigest") or ""),
        str(dictContainer.get("sArchitecture") or ""),
    )
    if listProblems:
        raise HTTPException(409, " ".join(listProblems))
    return _fdictBuildReferencedArchiveRecord(
        dictZenodoRecord, sVersionDoi, dictContainer, clientZenodo.sService,
    )


def _fdictBuildReferencedArchiveRecord(
    dictZenodoRecord, sVersionDoi, dictContainer, sZenodoService,
):
    """Turn a verified Zenodo record into this project's archive record.

    The provenance is ``original``: a referenced deposit holds the
    same image digest and platform the envelope pins, so the archived
    environment IS the one these results were produced in. The other
    value is for the late path, where a rerun proved equivalence
    rather than identity.
    """
    dictMetadata = dictZenodoRecord.get("metadata") or {}
    dictFingerprint = imageArchive.fdictParseDepositFingerprint(
        dictMetadata.get("description")
        or dictZenodoRecord.get("description") or "",
    )
    return imageArchive.fdictBuildArchiveRecord(
        sVersionDoi=sVersionDoi,
        sConceptDoi=str(
            dictMetadata.get("conceptdoi")
            or dictZenodoRecord.get("conceptdoi") or "",
        ),
        sTarballSha256=str(dictFingerprint.get("sTarballSha256") or ""),
        iTarballBytes=int(dictFingerprint.get("iTarballBytes") or 0),
        sDepositedIso=datetime.now(timezone.utc).isoformat(),
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest=str(dictContainer.get("sImageDigest") or ""),
        sArchitecture=str(dictContainer.get("sArchitecture") or ""),
        sTarballName=str(dictFingerprint.get("sTarballName") or ""),
        # Without this the re-check on a paper 2..N clone answers
        # UNAVAILABLE forever, for a deposit that carries the hash.
        sImageStreamSha256=str(
            dictFingerprint.get("sImageStreamSha256") or "",
        ),
        sZenodoService=sZenodoService,
    )


async def _fdictWriteArchiveRecordUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, dictRecord, requestHttp,
):
    """Write one deposit record into the envelope's container block."""
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)

    def fdictStampTheRecord(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictStampArchiveRecord(
                filesRepo, dictWorkflow, dictRecord,
            ),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictStampTheRecord, "environment-archive-record",
        requestHttp,
    )


def _fdictStampArchiveRecord(filesRepo, dictWorkflow, dictRecord):
    """Merge the deposit record into ``.vaibify/environment.json``.

    Read-modify-write rather than a rebuild, so the capture beside it
    is untouched. The envelope's own regeneration is what later
    decides whether this record still applies -- it carries the record
    forward only while the fresh capture names the same image and
    platform.

    THE MANIFEST IS RE-PINNED IN THE SAME BREATH, and forgetting that
    would make depositing an image DROP the project out of Level 3:
    ``.vaibify/environment.json`` is pinned in ``MANIFEST.sha256``, so
    writing the record changes a file the manifest claims to know the
    hash of. The researcher would have archived their environment and
    watched the ladder fall, with the two events looking unrelated.
    Every other envelope writer on this ladder re-pins for the same
    reason.
    """
    dictPayload = fdictReadEnvironmentJson(filesRepo) or {}
    dictContainer = dict(dictPayload.get("dictContainer") or {})
    dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY] = dictRecord
    dictPayload["dictContainer"] = dictContainer
    fnWriteEnvironmentJson(filesRepo, dictPayload)
    return {
        "dictImageArchive": dictRecord,
        "bManifestRefreshed": _fbRepinManifestOrWarn(
            filesRepo, dictWorkflow,
        ),
    }


def _fbRepinManifestOrWarn(filesRepo, dictWorkflow):
    """Re-pin MANIFEST.sha256; return False (never raise) on failure.

    A failed re-pin degrades to a flag because the record itself did
    land and the researcher can regenerate the envelope. A carrier
    REFUSAL is not that: it means this lane's carrier call was
    forgotten, and answering with a soft flag would hide the
    migration's only proof behind a checkbox.
    """
    from ...config.mutationAdmission import fnReRaiseControlPlaneRefusal
    from ...reproducibility import manifestWriter
    try:
        manifestWriter.fnWriteManifest(filesRepo, dictWorkflow)
    except Exception as errorCaught:  # noqa: BLE001 — reported as a flag
        fnReRaiseControlPlaneRefusal(errorCaught)
        logger.warning(
            "The environment-archive record landed but the manifest "
            "re-pin failed: %s", errorCaught,
        )
        return False
    return True


def _fnRegisterDepositEnvironmentArchive(app, dictCtx):
    """Register POST /api/workflow/{id}/environment-archive/deposit."""

    @ffnAgentAction("deposit-environment-archive")
    @app.post(
        "/api/workflow/{sContainerId}/environment-archive/deposit"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_C_DURABLE,
    )
    async def fdictDepositEnvironmentArchive(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        _fnRefuseIfDepositInFlight(sContainerId)
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictContainer = _fdictRequireEnvelopeContainerBlock(filesRepo)
        sToken = await asyncio.to_thread(
            _fsReadZenodoTokenFromContainer,
            dictCtx["docker"], sContainerId, dictWorkflow,
        )
        return await _fdictLaunchDepositDurably(
            dictCtx, sContainerId, dictWorkflow, dictContainer,
            sToken, requestHttp,
        )


def _fnRefuseIfDepositInFlight(sContainerId):
    """Raise 409 when a deposit is already running for this container."""
    if archiveProgress.fbDepositIsLive(sContainerId):
        raise HTTPException(
            409,
            "An environment-archive deposit is already running for "
            "this container.",
        )


def _fsReadZenodoTokenFromContainer(
    connectionDocker, sContainerId, dictWorkflow,
):
    """Return the researcher's Zenodo token, or raise HTTP 409.

    Vaibify stores this token in the CONTAINER keyring, because every
    other Zenodo call it makes runs as a script inside the container.
    ``docker save`` can only run on the host, so for this one
    operation the token crosses -- read through the typed-read seam,
    held in a local for the length of one upload, written to no file
    and no log. The alternative was streaming a gigabyte the other
    way through an exec socket built for a terminal.

    An absent token is a 409 with an instruction, never a 500: the
    researcher has simply not connected Zenodo yet.
    """
    from ...reproducibility.zenodoClient import fsZenodoTokenName
    sSlot = fsZenodoTokenName(
        dictWorkflow.get("sZenodoService") or "sandbox",
    )
    try:
        sToken = connectionDocker.fsFetchKeyringSecret(sContainerId, sSlot)
    except LookupError as errorLookup:
        raise HTTPException(409, str(errorLookup)) from None
    if not sToken:
        raise HTTPException(
            409,
            "No Zenodo token is stored for this project, so vaibify "
            "cannot publish the environment archive. Connect Zenodo "
            "from the Repos panel and try again.",
        )
    return sToken


async def _fdictLaunchDepositDurably(
    dictCtx, sContainerId, dictWorkflow, dictContainer, sToken,
    requestHttp,
):
    """Launch the deposit as REGISTERED durable work (mode c).

    Mode (c) because the response returns while the work continues: a
    multi-gigabyte save and upload runs for minutes. Registering it
    under the briefly-held mutation lock is what makes it VISIBLE to
    the ownership hand-over, the shutdown drain and the idle watchdog,
    which would otherwise see an idle container.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The environment-archive deposit",
    )
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)

    def ftaskStartDeposit():
        taskWorker = asyncio.create_task(_fnRunDepositWorker(
            sContainerId, dictWorkflow, dictContainer, sToken, filesRepo,
        ))
        archiveProgress.fnRegisterDeposit(sContainerId, taskWorker)
        return taskWorker

    dictLaunched = await commitCarrier.fdictLaunchDurableTask(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, ftaskStartDeposit,
        sOperation="the environment-archive deposit",
    )
    if not dictLaunched["bLaunched"]:
        raise HTTPException(
            409,
            "This container is busy: " + dictLaunched["sReason"] + ".",
        )
    return {"bAccepted": True, "sPhase": archiveProgress.S_PHASE_STARTING}


async def _fnRunDepositWorker(
    sContainerId, dictWorkflow, dictContainer, sToken, filesRepo,
):
    """Save, upload, publish, then stamp the record onto the envelope.

    Runs as the durable task the carrier launched, so it inherits that
    launch's admission -- which is what lets its envelope write and
    manifest re-pin reach the container at all. A worker that opened
    none would raise ``MutationNotAdmittedError`` from inside a
    background task, and the researcher would see an unexplained
    deposit failure.
    """
    try:
        dictRecord = await asyncio.to_thread(
            _fdictDepositSynchronously,
            sContainerId, dictWorkflow, dictContainer, sToken,
            fdictReadAttestation(filesRepo),
        )
        await asyncio.to_thread(
            _fdictStampArchiveRecord, filesRepo, dictWorkflow, dictRecord,
        )
        archiveProgress.fnSettleDeposit(sContainerId)
    except Exception as errorDeposit:  # noqa: BLE001 — reported, not raised
        # The researcher is the only one who can act on this, and the
        # task's exception would otherwise be readable nowhere: the
        # response returned minutes ago.
        logger.warning(
            "Environment-archive deposit failed for %s",
            sContainerId, exc_info=True,
        )
        archiveProgress.fnRecordFailure(
            sContainerId, _fsDescribeDepositFailure(errorDeposit),
        )


def _fsDescribeDepositFailure(errorDeposit):
    """Return a researcher-facing reason, with no credential in it.

    ``zenodoClient`` redacts tokens out of its own messages, and
    nothing here adds one back: the token never appears in any value
    this module formats.
    """
    sReason = str(errorDeposit).strip()
    return sReason or errorDeposit.__class__.__name__


def _fdictDepositSynchronously(
    sContainerId, dictWorkflow, dictContainer, sToken, dictAttestation,
):
    """Run the whole deposit on a worker thread; return the record."""
    import shutil
    from ...reproducibility.zenodoClient import ZenodoClient
    sScratchDirectory = imageDeposit.fsResolveDepositScratchDirectory()

    def fnReportSaveProgress(iBytesRead, iBytesTotal):
        archiveProgress.fnRecordProgress(
            sContainerId, archiveProgress.S_PHASE_SAVING,
            iBytesRead, iBytesTotal,
        )

    def fnReportUploadStarted(iTarballBytes):
        archiveProgress.fnRecordProgress(
            sContainerId, archiveProgress.S_PHASE_UPLOADING,
            0, iTarballBytes,
        )

    try:
        return imageDeposit.fdictDepositImageArchive(
            ZenodoClient(
                dictWorkflow.get("sZenodoService") or "sandbox",
                sToken=sToken,
            ),
            str(dictContainer.get("sImageDigest") or ""),
            str(dictContainer.get("sArchitecture") or ""),
            sScratchDirectory,
            _fdictBuildArchiveDepositMetadata(dictWorkflow),
            fnReportSaveProgress,
            dictAttestation,
            fnReportUploadStarted=fnReportUploadStarted,
        )
    finally:
        # 800 MB must not survive the operation that made it, whether
        # or not the upload succeeded.
        shutil.rmtree(sScratchDirectory, ignore_errors=True)


def _fdictBuildArchiveDepositMetadata(dictWorkflow):
    """Return the vaibify-shaped metadata for the image deposit.

    A SEPARATE record from the science deposit, not a version of it,
    because Zenodo versions are self-contained and carry no files
    forward: one image used for N papers is one large upload plus N
    small science records that reference it, and versioning would mean
    re-uploading the image every time.
    """
    from .. import workflowManager
    dictScience = dict(workflowManager.fdictGetZenodoMetadata(dictWorkflow))
    sProjectTitle = (
        dictScience.get("sTitle")
        or dictWorkflow.get("sProjectTitle")
        or dictWorkflow.get("sWorkflowName")
        or "this project"
    )
    return {
        "sTitle": "Container image for " + sProjectTitle,
        "sDescription": (
            "The container image these results were produced in, "
            "saved with 'docker save' and compressed. Load it with "
            "'docker load' to obtain the exact compiler, numeric "
            "libraries, interpreter and installed packages the "
            "original run used."
        ),
        "listCreators": dictScience.get("listCreators") or [],
        "sLicense": dictScience.get("sLicense") or "",
        "listKeywords": dictScience.get("listKeywords") or [],
    }


# The `archived` answer is NOT recorded in the workflow, and the
# absence is the point. It gated nothing -- Level 2 reads the deposit
# record on disk through `fbImageArchiveQuestionSettled`, and the
# answer route refuses `archived` as something a caller may assert --
# so its only effect was to write a cosmetic field into
# `project.json`, which is a file the Level 2 verifies COMPARE against
# GitHub and Zenodo. Depositing therefore dropped the project a level
# for a field nothing reads: a researcher followed the dashboard from
# Level 2 toward Level 3, deposited the image, and landed back at
# Level 1 with a file they had already published now diverged
# (researcher-reported, 2026-09-09). The deposit record in
# `environment.json` is the durable evidence, and the row renders the
# DOI from it.

def fnRegisterAll(app, dictCtx):
    """Register every environment-archive endpoint."""
    _fnRegisterAnswerEnvironmentArchive(app, dictCtx)
    _fnRegisterDepositEnvironmentArchive(app, dictCtx)
