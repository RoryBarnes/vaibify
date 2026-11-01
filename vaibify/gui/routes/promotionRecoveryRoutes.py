"""Recovering a promotion that was interrupted after a DOI was minted.

A promotion publishes in a public archive, and the DOI is minted in
the middle of a long upload. If the process dies with the DOI minted
and nothing written down, **the DOI cannot be recovered by guessing** —
so the promotion lanes bracket themselves with a record in the sync
sidecar: intent before anything starts, and the deposit id made
durable the moment the draft exists, before the first byte goes up.

This module is what a researcher meets afterwards. It never decides
what happened; it ASKS Zenodo and reports the answer, because only
Zenodo knows whether a draft holds two files or five, and whether a
publish that never returned nevertheless succeeded.

Two distinctions the routes here refuse to collapse:

* **A 404 is an answer; a timeout is not.** ``gone`` licenses a
  discard. ``unknown`` licenses nothing at all and KEEPS the record —
  discarding on a question nobody answered throws away the only handle
  on a DOI that may exist.
* **The promotion id must be on the remote too.** Every mutating
  action re-checks that the deposit's description names this
  promotion. Without that, a writable local file is the only thing
  binding a remote mutation to the intent that started it.

Its own module rather than a section of ``environmentArchiveRoutes``
or ``syncRoutes``: it settles BOTH lanes, and a route module may not
import a sibling.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request

from ..actionCatalog import ffnAgentAction
from ..remoteBindingContext import fdictRequireBoundZenodoTarget
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import (
    fdictCommitWorkflowSave,
    ffilesForWorkflow,
    fnRequireNetworkAccess,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)
from ...reproducibility import archivePromotion, syncBookkeeping


def _fsResolveSidecarKey(dictCtx, sContainerId, dictWorkflow):
    """Return the syncStatus.json section key for this workflow."""
    from .. import stateManager
    return stateManager.fsWorkflowKeyFromPath(
        (dictCtx.get("paths") or {}).get(sContainerId, ""),
        dictWorkflow.get("sProjectRepoPath") or "",
    )


def _ftRequirePendingPromotion(dictCtx, sContainerId, sPromotionId):
    """Return ``(workflow, filesRepo, key, record)`` or raise 404."""
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId,
    )
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
    sSidecarKey = _fsResolveSidecarKey(dictCtx, sContainerId, dictWorkflow)
    for dictRecord in syncBookkeeping.flistReadPendingPromotions(
        filesRepo, sSidecarKey,
    ):
        if dictRecord.get("sPromotionId") == sPromotionId:
            return dictWorkflow, filesRepo, sSidecarKey, dictRecord
    raise HTTPException(
        404, "No in-flight promotion '" + sPromotionId + "' is on "
        "record for this project.",
    )


def _fclientForPromotion(dictCtx, sContainerId, dictRecord, dictWorkflow):
    """Return a client for the instance this promotion targeted."""
    from ...reproducibility.zenodoClient import ZenodoClient
    from .. import zenodoTokenAccess
    sService = str(dictRecord.get("sTargetService") or "zenodo")
    # Resuming spends the researcher's token on this instance, so the
    # instance must still be the one the researcher confirmed.
    fdictRequireBoundZenodoTarget(
        dictCtx, sContainerId, dictWorkflow, sService, bInstanceOnly=True,
    )
    sToken = zenodoTokenAccess.fsActiveZenodoToken(
        dictCtx["docker"], sContainerId, sService,
    )
    return ZenodoClient(sService, sToken=sToken or "")


def _fnRegisterListPending(app, dictCtx):
    """Register GET /api/workflow/{id}/promotions/pending."""

    @app.get("/api/workflow/{sContainerId}/promotions/pending")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictListPendingPromotions(sContainerId: str):
        """List in-flight promotions so the dashboard can surface one.

        Read on load rather than relying on a researcher having kept
        a failed request's toast: the record exists precisely because
        the browser that started the promotion may be gone.
        """
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        return {"listPending": syncBookkeeping.flistReadPendingPromotions(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            _fsResolveSidecarKey(dictCtx, sContainerId, dictWorkflow),
        )}


def _fnRegisterReconcile(app, dictCtx):
    """Register POST /api/workflow/{id}/promotions/{id}/reconcile."""

    @ffnAgentAction("reconcile-promotion")
    @app.post(
        "/api/workflow/{sContainerId}/promotions/{sPromotionId}"
        "/reconcile"
    )
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictReconcilePromotion(
        sContainerId: str, sPromotionId: str,
    ):
        """Ask Zenodo what happened, and offer only what it licenses."""
        dictCtx["require"](sContainerId)
        fnRequireNetworkAccess(sContainerId)
        dictWorkflow, _filesRepo, _sKey, dictRecord = (
            _ftRequirePendingPromotion(
                dictCtx, sContainerId, sPromotionId,
            )
        )
        clientZenodo = _fclientForPromotion(
            dictCtx, sContainerId, dictRecord, dictWorkflow,
        )
        dictOutcome = await asyncio.to_thread(
            _fdictReconcileForLane, dictCtx, sContainerId, dictWorkflow,
            dictRecord, clientZenodo,
        )
        dictOutcome["dictRecord"] = dictRecord
        return dictOutcome


def _fdictReconcileForLane(
    dictCtx, sContainerId, dictWorkflow, dictRecord, clientZenodo,
):
    """Reconcile one record by its lane's rules.

    A completion is judged with its STORED receipt: whether resume is
    offered depends on whether those bytes are still the journaled ones.
    """
    if dictRecord.get("sLane") != archivePromotion.S_LANE_COMPLETION:
        return archivePromotion.fdictReconcilePromotion(
            dictRecord, clientZenodo.fdictGetDeposit,
        )
    from .. import syncDispatcher
    return archivePromotion.fdictReconcileCompletion(
        dictRecord, clientZenodo.fdictGetDeposit,
        syncDispatcher.fbaReadPendingReceiptOrNone(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            dictRecord["sPromotionId"]),
    )


def _ftRequireSettleableDeposit(
    dictCtx, sContainerId, dictRecord, dictWorkflow, tAllowedOutcomes,
    sAction="",
):
    """Reconcile, then refuse unless the outcome licenses this action.

    ``sAction`` additionally requires the outcome to OFFER that action,
    which is how a completion's partial draft is resumable while the
    other lanes' never are.
    """
    clientZenodo = _fclientForPromotion(
        dictCtx, sContainerId, dictRecord, dictWorkflow,
    )
    dictOutcome = _fdictReconcileForLane(
        dictCtx, sContainerId, dictWorkflow, dictRecord, clientZenodo,
    )
    if dictOutcome["sOutcome"] not in tAllowedOutcomes or (
            sAction and sAction not in dictOutcome["listActions"]):
        raise HTTPException(409, dictOutcome["sMessage"])
    # Nothing is there to name this promotion when Zenodo has answered
    # 404, and asking again would raise where the outcome is already
    # settled. Every other outcome mutates a live deposit, so every
    # other outcome is checked.
    if dictOutcome["sOutcome"] != archivePromotion.S_OUTCOME_GONE:
        _fnRequireRemoteNamesThisPromotion(clientZenodo, dictRecord)
    return clientZenodo, dictOutcome


def _fnRequireRemoteNamesThisPromotion(clientZenodo, dictRecord):
    """Raise 409 unless the remote deposit names this promotion.

    The local record is a writable file. Without this check it is the
    only thing binding a mutation of a live archive to the intent that
    started it, and a researcher with two interrupted promotions has
    nothing telling them which draft is which.
    """
    dictDeposit = clientZenodo.fdictGetDeposit(
        int(dictRecord.get("iDepositId") or 0),
    )
    sDescription = str(
        (dictDeposit.get("metadata") or {}).get("description") or "",
    )
    if archivePromotion.fbDescriptionCarriesPromotionId(
        sDescription, dictRecord.get("sPromotionId"),
    ):
        return
    raise HTTPException(409, (
        "The deposit on Zenodo does not name this promotion, so "
        "vaibify will not act on it. Open it on Zenodo and check what "
        "it holds before doing anything else."
    ))


def _fnRegisterResume(app, dictCtx):
    """Register POST /api/workflow/{id}/promotions/{id}/resume."""

    @ffnAgentAction("resume-promotion")
    @app.post(
        "/api/workflow/{sContainerId}/promotions/{sPromotionId}/resume"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictResumePromotion(
        sContainerId: str, sPromotionId: str, requestHttp: Request,
    ):
        """Publish a draft that already holds every intended file.

        The case this lane exists for: an upload that finished and a
        publish call that never returned. It needs no bytes, which is
        why it is the only outcome resume is offered on -- see
        ``archivePromotion._DICT_OFFERED_ACTIONS`` for why a
        partly-uploaded draft is discarded and re-run instead.
        """
        dictCtx["require"](sContainerId)
        fnRequireNetworkAccess(sContainerId)
        dictWorkflow, filesRepo, sSidecarKey, dictRecord = (
            _ftRequirePendingPromotion(
                dictCtx, sContainerId, sPromotionId,
            )
        )
        if dictRecord.get("sLane") == archivePromotion.S_LANE_COMPLETION:
            return await _fdictResumeCompletion(
                dictCtx, sContainerId, dictWorkflow, dictRecord, requestHttp,
            )
        clientZenodo, _dictOutcome = await asyncio.to_thread(
            _ftRequireSettleableDeposit,
            dictCtx, sContainerId, dictRecord, dictWorkflow,
            (archivePromotion.S_OUTCOME_PUBLISHABLE,),
        )
        dictPublished = await asyncio.to_thread(
            clientZenodo.fdictPublishDraft,
            int(dictRecord["iDepositId"]),
        )
        return await _fdictAdoptForLane(
            dictCtx, sContainerId, dictWorkflow, filesRepo,
            sSidecarKey, dictRecord,
            {"sDoi": str(dictPublished.get("doi") or ""),
             "sConceptDoi": str(dictPublished.get("conceptdoi") or "")},
            requestHttp,
        )


def _fnRegisterAdopt(app, dictCtx):
    """Register POST /api/workflow/{id}/promotions/{id}/adopt."""

    @ffnAgentAction("adopt-promotion")
    @app.post(
        "/api/workflow/{sContainerId}/promotions/{sPromotionId}/adopt"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictAdoptPromotion(
        sContainerId: str, sPromotionId: str, requestHttp: Request,
    ):
        """Record a DOI that was minted while vaibify was not watching.

        Two lanes, two writers, and they must not be shared: the image
        record lives in ``environment.json`` and needs the manifest
        re-pinned; the project record lives in the sidecar. Idempotent
        on both -- adopting a settled promotion reports the same DOI
        and writes nothing new.
        """
        dictCtx["require"](sContainerId)
        fnRequireNetworkAccess(sContainerId)
        dictWorkflow, filesRepo, sSidecarKey, dictRecord = (
            _ftRequirePendingPromotion(
                dictCtx, sContainerId, sPromotionId,
            )
        )
        _clientZenodo, dictOutcome = await asyncio.to_thread(
            _ftRequireSettleableDeposit,
            dictCtx, sContainerId, dictRecord, dictWorkflow,
            (archivePromotion.S_OUTCOME_PUBLISHED,),
        )
        return await _fdictAdoptForLane(
            dictCtx, sContainerId, dictWorkflow, filesRepo,
            sSidecarKey, dictRecord, dictOutcome, requestHttp,
        )


async def _fdictAdoptForLane(
    dictCtx, sContainerId, dictWorkflow, filesRepo, sSidecarKey,
    dictRecord, dictOutcome, requestHttp,
):
    """Route one adoption to its own lane's writer, then settle."""
    if dictRecord.get("sLane") == archivePromotion.S_LANE_COMPLETION:
        return await _fdictAdoptCompletionDeposit(
            dictCtx, sContainerId, dictWorkflow, filesRepo, dictRecord,
            dictOutcome, requestHttp,
        )
    if dictRecord.get("sLane") == archivePromotion.S_LANE_PROJECT:
        _fnAdoptProjectDeposit(
            dictCtx, sContainerId, dictWorkflow, dictRecord,
            dictOutcome, requestHttp,
        )
    else:
        await asyncio.to_thread(
            _fnAdoptImageDeposit, filesRepo, dictWorkflow,
            dictRecord, dictOutcome,
        )
    await asyncio.to_thread(
        syncBookkeeping.fnRemovePendingPromotion,
        filesRepo, sSidecarKey, dictRecord["sPromotionId"],
    )
    syncBookkeeping.fnMirrorPendingPromotions(
        filesRepo, sSidecarKey, dictWorkflow,
    )
    return {"bAdopted": True, "sDoi": dictOutcome["sDoi"]}


def _fnAdoptProjectDeposit(
    dictCtx, sContainerId, dictWorkflow, dictRecord, dictOutcome,
    requestHttp,
):
    """Write an adopted project DOI through the sidecar seam."""
    syncBookkeeping.fnRecordZenodoPublish(
        dictWorkflow,
        {"iDepositId": dictRecord.get("iDepositId"),
         "sDoi": dictOutcome["sDoi"],
         "sConceptDoi": dictOutcome["sConceptDoi"]},
        str(dictRecord.get("sTargetService") or "zenodo"),
    )
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "Adopting a published Zenodo deposit",
    )


def _fnAdoptImageDeposit(
    filesRepo, dictWorkflow, dictRecord, dictOutcome,
):
    """Write an adopted image DOI into the envelope, manifest and all."""
    from ...reproducibility.imageDeposit import fdictStampArchiveRecord
    from ...reproducibility.environmentSnapshot import (
        S_SUPERSEDED_ARCHIVE_KEY, fdictReadEnvironmentJson,
    )
    from ...reproducibility import imageArchive
    dictContainer = (
        fdictReadEnvironmentJson(filesRepo) or {}
    ).get("dictContainer") or {}
    dictOld = dictContainer.get(imageArchive.S_IMAGE_ARCHIVE_KEY)
    fdictStampArchiveRecord(
        filesRepo, dictWorkflow,
        _fdictBuildAdoptedImageRecord(
            dictContainer, dictRecord, dictOutcome,
        ),
        dictExtraContainerFields=(
            {S_SUPERSEDED_ARCHIVE_KEY: dictOld}
            if isinstance(dictOld, dict) else None
        ),
    )


def _fdictBuildAdoptedImageRecord(
    dictContainer, dictRecord, dictOutcome,
):
    """Return the deposit record for an adopted image promotion."""
    from ...reproducibility import imageArchive
    from datetime import datetime, timezone
    dictOld = dictContainer.get(imageArchive.S_IMAGE_ARCHIVE_KEY) or {}
    dictFile = (dictRecord.get("listFiles") or [{}])[0]
    return imageArchive.fdictBuildArchiveRecord(
        sVersionDoi=dictOutcome["sDoi"],
        sConceptDoi=dictOutcome["sConceptDoi"],
        sTarballSha256=str(dictFile.get("sSha256") or ""),
        # The pending record carries the md5 too, and dropping it here
        # would leave an adopted deposit permanently un-re-checkable:
        # Zenodo publishes md5 and nothing else, so a record without
        # one can be asked what it holds and the answer compared to
        # nothing.
        sTarballMd5=str(dictFile.get("sMd5") or ""),
        iTarballBytes=int(dictFile.get("iBytes") or 0),
        sDepositedIso=datetime.now(timezone.utc).isoformat(),
        sProvenance=str(dictOld.get("sProvenance") or ""),
        sImageDigest=str(dictContainer.get("sImageDigest") or ""),
        sArchitecture=str(dictContainer.get("sArchitecture") or ""),
        sTarballName=str(dictFile.get("sBasename") or ""),
        sImageStreamSha256=str(dictOld.get("sImageStreamSha256") or ""),
        sZenodoService=str(dictRecord.get("sTargetService") or "zenodo"),
    )


def _fnRegisterDiscard(app, dictCtx):
    """Register DELETE /api/workflow/{id}/promotions/{id}."""

    @ffnAgentAction("discard-promotion")
    @app.delete(
        "/api/workflow/{sContainerId}/promotions/{sPromotionId}"
    )
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictDiscardPromotion(
        sContainerId: str, sPromotionId: str, requestHttp: Request,
    ):
        """Delete an unwanted draft and forget the record.

        Refuses on ``published`` and on ``unknown``: the first would
        throw away a real DOI, and the second a question nobody has
        answered.
        """
        dictCtx["require"](sContainerId)
        dictWorkflow, filesRepo, sSidecarKey, dictRecord = (
            _ftRequirePendingPromotion(
                dictCtx, sContainerId, sPromotionId,
            )
        )
        if dictRecord.get("sLane") == archivePromotion.S_LANE_COMPLETION:
            await _fnDiscardCompletionUnderTheDrain(
                dictCtx, sContainerId, dictWorkflow, filesRepo, sSidecarKey,
                dictRecord, requestHttp,
            )
            return {"bDiscarded": True}
        await asyncio.to_thread(
            _fnDiscardDraftAndRecord, dictCtx, sContainerId,
            dictWorkflow, filesRepo, sSidecarKey, dictRecord,
        )
        return {"bDiscarded": True}


def _fnDiscardDraftAndRecord(
    dictCtx, sContainerId, dictWorkflow, filesRepo, sSidecarKey,
    dictRecord,
):
    """Delete the remote draft when there is one, then drop the record."""
    if int(dictRecord.get("iDepositId") or 0):
        clientZenodo, dictOutcome = _ftRequireSettleableDeposit(
            dictCtx, sContainerId, dictRecord, dictWorkflow,
            (archivePromotion.S_OUTCOME_RESUMABLE,
             archivePromotion.S_OUTCOME_PUBLISHABLE,
             archivePromotion.S_OUTCOME_GONE),
        )
        if dictOutcome["sOutcome"] != archivePromotion.S_OUTCOME_GONE:
            clientZenodo.fnDeleteDraft(int(dictRecord["iDepositId"]))
    if dictRecord.get("sLane") == archivePromotion.S_LANE_COMPLETION:
        from .. import syncDispatcher
        syncDispatcher.fnSettleCompletion(
            dictCtx, sContainerId, dictWorkflow, dictRecord["sPromotionId"])
        return
    syncBookkeeping.fnRemovePendingPromotion(
        filesRepo, sSidecarKey, dictRecord["sPromotionId"],
    )
    syncBookkeeping.fnMirrorPendingPromotions(
        filesRepo, sSidecarKey, dictWorkflow,
    )


# ── The completion lane ──
#
# A completion is a project archive's second version: inherited files
# and one receipt replaced. Its recovery differs from the other lanes'
# in two ways, and both are why it has branches of its own. A partial
# draft can be RESUMED, because the one file that can be missing is the
# stored receipt. And adoption writes the deposit record and the
# repository receipt, never environment.json -- sending this lane
# through the image adopter would stamp an image record.


async def _fdictResumeCompletion(
    dictCtx, sContainerId, dictWorkflow, dictRecord, requestHttp,
):
    """Publish an interrupted completion's draft, then settle it."""
    from .. import syncDispatcher
    from ..routeContext import (
        fdictCarryARefusalBackInsteadOfRaising, fgenericRunWorkerUnderTheDrain,
    )
    clientZenodo, _dictOutcome = await asyncio.to_thread(
        _ftRequireSettleableDeposit,
        dictCtx, sContainerId, dictRecord, dictWorkflow,
        (archivePromotion.S_OUTCOME_PUBLISHABLE,
         archivePromotion.S_OUTCOME_RESUMABLE), archivePromotion.S_ACTION_RESUME,
    )
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
    baReceipt = syncDispatcher.fbaReadPendingReceiptOrNone(
        filesRepo, dictRecord["sPromotionId"])

    def fdictFinish(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: syncDispatcher.ftResultFinishCompletionDraft(
                clientZenodo, dictRecord, baReceipt,
                syncDispatcher.fdictCompletionJournalHooks(
                    filesRepo, syncDispatcher.fsResolveSidecarKey(
                        dictCtx, sContainerId, dictWorkflow),
                    dictRecord["sPromotionId"])))

    iExit, sOut = await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictFinish, "zenodo-completion-resume", requestHttp)
    if iExit != 0:
        raise HTTPException(409, sOut)
    # The repository copy: the stored bytes when they are still the
    # journaled ones, else the published version's own, checked.
    baForRepository = await asyncio.to_thread(
        _fbaRestoreCompletionReceiptOrNone, dictCtx, sContainerId,
        dictWorkflow, filesRepo, dictRecord,
    )
    return await syncDispatcher.fdictSettlePublishedCompletion(
        dictCtx, sContainerId, dictWorkflow, requestHttp, dictRecord,
        _fdictParsePublished(sOut), baForRepository,
    )


def _fdictParsePublished(sOut):
    """Return the publish record a completion step printed."""
    import json
    sJson = sOut.split("ZENODO_RESULT=", 1)[1].strip().splitlines()[0]
    return json.loads(sJson)


async def _fdictAdoptCompletionDeposit(
    dictCtx, sContainerId, dictWorkflow, filesRepo, dictRecord, dictOutcome,
    requestHttp,
):
    """Record a completion published while vaibify was not watching."""
    from .. import syncDispatcher
    baReceipt = await asyncio.to_thread(
        _fbaRestoreCompletionReceiptOrNone, dictCtx, sContainerId,
        dictWorkflow, filesRepo, dictRecord,
    )
    dictSettled = await syncDispatcher.fdictSettlePublishedCompletion(
        dictCtx, sContainerId, dictWorkflow, requestHttp, dictRecord,
        {"iDepositId": dictRecord.get("iDepositId"),
         "sDoi": dictOutcome["sDoi"], "sConceptDoi": dictOutcome["sConceptDoi"]},
        baReceipt,
    )
    return dict(dictSettled, bAdopted=True)


def _fbaRestoreCompletionReceiptOrNone(
    dictCtx, sContainerId, dictWorkflow, filesRepo, dictRecord,
):
    """Return the published receipt's bytes: stored, else downloaded, else None.

    Either source must hash to the journaled SHA-256; bytes that do not
    are never written into the repository.
    """
    import os
    import tempfile
    from .. import syncDispatcher
    baStored = syncDispatcher.fbaReadPendingReceiptOrNone(
        filesRepo, dictRecord["sPromotionId"])
    if not archivePromotion.fsDescribeStoredReceiptMismatch(dictRecord, baStored):
        return baStored
    sKey = (dictRecord.get("dictCompletionReceipt") or {}).get("sKey") or ""
    with tempfile.TemporaryDirectory() as sScratch:
        try:
            _fclientForPromotion(
                dictCtx, sContainerId, dictRecord, dictWorkflow,
            ).fnDownloadFile(int(dictRecord["iDepositId"]), sKey,
                             os.path.join(sScratch, sKey))
            with open(os.path.join(sScratch, sKey), "rb") as fileDownloaded:
                baDownloaded = fileDownloaded.read()
        except Exception:  # noqa: BLE001 -- the DOI is recorded regardless
            return None
    if archivePromotion.fsDescribeStoredReceiptMismatch(dictRecord, baDownloaded):
        return None
    return baDownloaded


async def _fnDiscardCompletionUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, filesRepo, sSidecarKey, dictRecord,
    requestHttp,
):
    """Discard a completion's draft, stored receipt and entry, admitted."""
    from ..routeContext import (
        fdictCarryARefusalBackInsteadOfRaising, fgenericRunWorkerUnderTheDrain,
    )

    def fdictDiscard(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fnDiscardDraftAndRecord(
                dictCtx, sContainerId, dictWorkflow, filesRepo, sSidecarKey,
                dictRecord))

    await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictDiscard, "zenodo-completion-discard", requestHttp)


def fnRegisterAll(app, dictCtx):
    """Register every promotion-recovery endpoint."""
    _fnRegisterListPending(app, dictCtx)
    _fnRegisterReconcile(app, dictCtx)
    _fnRegisterResume(app, dictCtx)
    _fnRegisterAdopt(app, dictCtx)
    _fnRegisterDiscard(app, dictCtx)
