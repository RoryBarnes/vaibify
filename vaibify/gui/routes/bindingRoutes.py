"""The researcher's confirmation of which remote a workflow's tokens may act on.

A token-bearing action (an Overleaf push or pull, a Zenodo deposit,
publish or Make Permanent) reads its target from the host-side binding
record (:mod:`vaibify.config.remoteBindings`), never from the
``project.json`` the in-container agent can edit. These three routes
are how the record is read, written and, when it cannot be read, moved
aside.

The GET reports the record's state, whether the environment archive
applies (read from the envelope), and every entry a workflow can hold --
Overleaf, and Zenodo's two kinds on each instance -- with what
``project.json`` names for it, what is confirmed, the notice sentence,
whether anything to compare names a record (``bHasTarget``: never for
the environment archive, which authorizes an instance only, and not for
a project record nobody has created yet; ``sAuthorizes`` then says what
confirming allows), and the exact confirm body a review sends
(``dictReview``). An entry nobody has confirmed on this computer carries
no notice, because the first action that would spend a token on it asks,
unless Auto Archive, which asks nobody, would send to it. With
``bFetchEvidence`` (the default) it also fetches, with the researcher's
own token, what the host can see: for the entries that apply, or for the
one entry ``sService``/``sRecordKind``/``sInstance`` name.

The POST confirms ONE entry, and only what was reviewed. Its body is a
review body: the workflow key, the record kind and the target. It is
refused ``BINDING-REVIEW-STALE`` when the loaded workflow or the target
it would confirm has moved since, ``ZENODO-TOKEN-MISSING`` before any
fetch when no token can be spent, ``BINDING-EVIDENCE-UNAVAILABLE`` when
the evidence could not be fetched, and ``BINDING-RECORD-INVALID`` for a
record that is not schema version 2. A refusal writes nothing. The
evidence the host can give, strongest first: a deposition the token can
read; a published record, whose title and owner are for the
researcher's eyes and prove no ownership; a token the instance accepts,
which is all a mint authorization or an environment archive can show;
and an Overleaf project the token can read (its file count and latest
commit, never a title). The POST is researcher-only twice over, in the
catalog and in the handler.

The set-aside POST renames an unreadable record beside itself, under
the record's lock, so the workflow's remotes can be confirmed again. Its
path comes from the project's identity, never from the request, and it
is refused ``BINDING-RECORD-VALID`` while the record can be read, so a
valid confirmation is never moved. It is researcher-only the same two
ways.
"""

__all__ = ["fnRegisterAll"]

import asyncio
from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config import remoteBindings
from vaibify.reproducibility.syncBookkeeping import fsDescribeZenodoService

from ..actionCatalog import ffnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..remoteBindingContext import (
    S_BINDING_EVIDENCE_UNAVAILABLE, S_BINDING_RECORD_INVALID,
    S_BINDING_RECORD_VALID, S_BINDING_REVIEW_STALE, fdictProjectTargets, fnRaiseBindingRefusal,
    fnRaiseForBindingError, fsDeclaredZenodoInstance, fsDescribeBoundEntry,
    ftBindingScope,
)
from ..routeContext import ffilesForWorkflow, fnRejectAgentTokenLane
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY, S_CARRIER_TYPED_READ, ffnDeclareCarrierMode,
)

_S_TOKEN_MISSING = "token-missing"


class BindingConfirmRequest(BaseModel):
    sService: str
    sRecordKind: Optional[str] = None
    sWorkflowKey: Optional[str] = None
    dictTarget: Optional[dict] = None
    sCarryFromWorkflowKey: str = ""


# ---------------------------------------------------------------------------
# Evidence: what the host can see with the researcher's own token
# ---------------------------------------------------------------------------


def _fdictFetchOverleafEvidence(dictCtx, sContainerId, sRecordKind, dictTarget):
    """Return whether the host's Overleaf token can read the project, and what it saw."""
    del dictCtx, sContainerId, sRecordKind
    from .. import syncDispatcher
    try:
        tRefresh = syncDispatcher.ftRefreshOverleafMirror(dictTarget["sProjectId"])
    except Exception as error:  # noqa: BLE001 -- the fetch's own words are the evidence
        return _fdictUnavailable(f"{type(error).__name__}: {error}")
    if not tRefresh[0]:
        return _fdictUnavailable(str(tRefresh[1]))
    dictMirror = dict(tRefresh[1])
    return {
        "bSucceeded": True, "sState": "readable",
        "iFileCount": int(dictMirror.get("iFileCount") or 0),
        "sHeadSha": str(dictMirror.get("sHeadSha") or ""),
        "sRefreshedAt": str(dictMirror.get("sRefreshedAt") or ""),
    }


def _fdictUnavailable(sMessage):
    return {"bSucceeded": False, "sState": "unavailable", "sMessage": sMessage}


def _fdictFetchZenodoEvidence(dictCtx, sContainerId, sRecordKind, dictTarget):
    """Return the strongest evidence the active token gives for one Zenodo entry.

    Fetched with the project's ACTIVE token for the instance; a pending
    migrated token is not spent here, because it is not yet the
    researcher's to spend.
    """
    from .. import zenodoTokenAccess
    sInstance = dictTarget["sInstance"]
    sToken = zenodoTokenAccess.fsActiveZenodoToken(
        dictCtx["docker"], sContainerId, sInstance,
    )
    if not sToken:
        return {
            "bSucceeded": False, "sState": _S_TOKEN_MISSING,
            "sMessage": (
                f"No token for {fsDescribeZenodoService(sInstance)} is "
                f"stored on this computer for this project. Store one, and "
                f"the check runs again."
            ),
        }
    try:
        return _fdictAskZenodo(sInstance, sToken, sRecordKind, dictTarget)
    except Exception as error:  # noqa: BLE001 -- the fetch's own words are the evidence
        return _fdictUnavailable(f"{type(error).__name__}: {error}")


def _fdictAskZenodo(sInstance, sToken, sRecordKind, dictTarget):
    from vaibify.reproducibility import zenodoClient
    clientZenodo = zenodoClient.ZenodoClient(sInstance, sToken=sToken)
    sDeposition = dictTarget.get("sDepositionId", "")
    sConcept = dictTarget.get("sConceptDoi", "")
    if sRecordKind == remoteBindings.S_RECORD_KIND_PROJECT and sDeposition:
        return _fdictDescribeRecord(
            "deposition", clientZenodo.fdictGetDeposit(int(sDeposition)),
        )
    if sRecordKind == remoteBindings.S_RECORD_KIND_PROJECT and sConcept:
        return _fdictDescribeRecord("published-record", clientZenodo.fdictFetchPublishedRecord(
            zenodoClient.fsExtractRecordIdFromDoi(sConcept),
        ))
    dictProbe = zenodoClient.fdictProbeToken(sInstance, sToken)
    if dictProbe["sOutcome"] != zenodoClient.S_PROBE_ACCEPTED:
        return _fdictUnavailable(
            f"Zenodo did not accept the token ({dictProbe['sOutcome']}): "
            f"{dictProbe['sDetail']}"
        )
    return {
        "bSucceeded": True, "sState": "token-accepted",
        "listDepositTitles": list(dictProbe.get("listDepositTitles") or []),
    }


def _fdictDescribeRecord(sState, dictDeposit):
    return {
        "bSucceeded": True, "sState": sState,
        "sTitle": str((dictDeposit.get("metadata") or {}).get("title") or ""),
        "sOwner": str(dictDeposit.get("owner") or dictDeposit.get("owners") or ""),
    }


_DICT_EVIDENCE_FETCHERS = {
    remoteBindings.S_SERVICE_OVERLEAF: _fdictFetchOverleafEvidence,
    remoteBindings.S_SERVICE_ZENODO: _fdictFetchZenodoEvidence,
}


# ---------------------------------------------------------------------------
# What project.json names, and what a review would confirm
# ---------------------------------------------------------------------------


# Every entry a workflow's record can hold, in the order the dashboard
# lists them: (service, record kind, instance).
_T_ENTRY_ADDRESSES = (
    (remoteBindings.S_SERVICE_OVERLEAF, "", ""),
) + tuple(
    (remoteBindings.S_SERVICE_ZENODO, sRecordKind, sInstance)
    for sRecordKind in remoteBindings.T_ZENODO_RECORD_KINDS
    for sInstance in remoteBindings.T_ZENODO_INSTANCES
)


def _fdictProjectTargetFor(dictWorkflow, sService, sRecordKind, sInstance):
    """Return what ``project.json`` says about one entry, or None when it says nothing.

    ``project.json`` names the Overleaf project, and the project record
    on the instance it declares. It says nothing about the other
    instance's project record, nor about the environment archive.
    """
    dictTargets = fdictProjectTargets(dictWorkflow)
    if sService == remoteBindings.S_SERVICE_OVERLEAF:
        return dictTargets[sService]
    if sRecordKind != remoteBindings.S_RECORD_KIND_PROJECT or (
        sInstance != fsDeclaredZenodoInstance(dictWorkflow)
    ):
        return None
    return dict(dictTargets[sService], sInstance=sInstance)


def _fdictReviewTarget(dictWorkflow, sService, sRecordKind, sInstance):
    """Return the one target a review of this entry may confirm.

    ``project.json``'s own target where it names one; elsewhere the
    instance alone, which for the project kind authorizes a new record.
    """
    dictProjectTarget = _fdictProjectTargetFor(dictWorkflow, sService, sRecordKind, sInstance)
    if dictProjectTarget is not None:
        return dictProjectTarget
    return remoteBindings.fdictNormalizedTarget(
        sService, sRecordKind, {"sInstance": sInstance},
    )


def _fsAgreementState(dictProjectTarget, dictConfirmed):
    """Return ``unconfirmed``, ``confirmed``, ``agrees`` or ``differs``."""
    if dictConfirmed is None:
        return "unconfirmed"
    if dictProjectTarget is None:
        return "confirmed"
    bAgrees = all(
        not sValue or dictConfirmed["dictTarget"].get(sField) == sValue
        for sField, sValue in dictProjectTarget.items()
    )
    return "agrees" if bAgrees else "differs"


def _fbNamesARecord(dictTarget):
    """True when a target names a record or a project, not only an instance."""
    return any(sValue for sField, sValue in (dictTarget or {}).items()
               if sField != "sInstance")


def _fbAutoArchiveSendsTo(dictWorkflow, tAddress):
    """True when Auto Archive would spend a token on this entry with nobody watching."""
    return bool(dictWorkflow.get("bAutoArchive")) and (
        tAddress[1] != remoteBindings.S_RECORD_KIND_ENVIRONMENT)


def _fsNoticeFor(tAddress, sState, dictProjectTarget, dictConfirmed, bAutoArchived):
    """Return the sentence a notice shows for one entry, or '' when nothing is wrong.

    An entry nobody has confirmed on this computer is silent: every
    action that would spend a token on it is refused first, and that
    refusal opens the review. Auto Archive is the exception, because it
    runs with nobody watching and its refusal reaches only the hub's log.
    """
    sWhat = fsDescribeBoundEntry(*tAddress)
    if sState == "unconfirmed":
        if not bAutoArchived:
            return ""
        return (f"Not confirmed: {sWhat}. Auto Archive sends nothing there "
                f"until you confirm it.")
    if sState != "differs":
        return ""
    listDiffering = [
        (sValue, dictConfirmed["dictTarget"].get(sField, ""))
        for sField, sValue in dictProjectTarget.items()
        if sValue and dictConfirmed["dictTarget"].get(sField) != sValue
    ]
    sNamed = ", ".join(sValue for sValue, _ in listDiffering)
    sBound = ", ".join(sBoundValue or "nothing" for _, sBoundValue in listDiffering)
    return f"project.json names {sNamed} for {sWhat}; you confirmed {sBound}."


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def _fsEnvironmentArchiveInstance(dictCtx, sContainerId, dictWorkflow):
    """Return the instance the envelope's environment archive lives on, or ''."""
    from vaibify.reproducibility import imageArchive, zenodoClient
    from vaibify.reproducibility.environmentSnapshot import fdictReadEnvironmentJson
    dictEnvelope = fdictReadEnvironmentJson(
        ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
    ) or {}
    dictArchive = (dictEnvelope.get("dictContainer") or {}).get(
        imageArchive.S_IMAGE_ARCHIVE_KEY,
    )
    if not isinstance(dictArchive, dict):
        return ""
    sService = str(dictArchive.get("sZenodoService") or "") or (
        zenodoClient.fsServiceForDoi(str(dictArchive.get("sVersionDoi") or ""))
    )
    return sService if sService in remoteBindings.T_ZENODO_INSTANCES else ""


def _fbEntryApplies(dictWorkflow, tAddress, sEnvironmentInstance):
    sService, sRecordKind, sInstance = tAddress
    if sService == remoteBindings.S_SERVICE_OVERLEAF:
        return bool(dictWorkflow.get("sOverleafProjectId"))
    if sRecordKind == remoteBindings.S_RECORD_KIND_ENVIRONMENT:
        return sInstance == sEnvironmentInstance
    return bool(dictWorkflow.get("sZenodoService")) and (
        sInstance == fsDeclaredZenodoInstance(dictWorkflow)
    )


def _fbEntryHasTarget(dictProjectTarget, dictConfirmed, dictReviewTarget):
    """Return False when nothing a review would compare names a record.

    Decided by what is there, not by the kind: the environment archive
    never names one, and a project record nobody has created yet names
    none either. Comparing three empty values reads as broken.
    """
    return any(_fbNamesARecord(dictTarget) for dictTarget in (
        dictProjectTarget, (dictConfirmed or {}).get("dictTarget"),
        dictReviewTarget))


def _fsDescribeWhatConfirmingAllows(tAddress):
    """Return what confirming an entry that names no record would allow."""
    sService, sRecordKind, sInstance = tAddress
    if sService == remoteBindings.S_SERVICE_OVERLEAF:
        return ("project.json names no Overleaf project for this workflow, so "
                "there is nothing to confirm yet.")
    sSite = fsDescribeZenodoService(sInstance)
    if sRecordKind == remoteBindings.S_RECORD_KIND_PROJECT:
        return (f"This lets vaibify create a new record on {sSite} for this "
                f"workflow's project deposits, with your token.")
    return (
        f"This lets vaibify deposit this workflow's container images on "
        f"{sSite} with your token. Each deposit records its own DOI in "
        f".vaibify/environment.json."
    )


def _fdictDescribeEntry(dictWorkflow, sWorkflowKey, dictServices, tAddress, bApplies):
    sService, sRecordKind, sInstance = tAddress
    dictProjectTarget = _fdictProjectTargetFor(dictWorkflow, *tAddress)
    dictConfirmed = remoteBindings.fdictLookUpEntry(dictServices, *tAddress)
    sState = _fsAgreementState(dictProjectTarget, dictConfirmed)
    dictReviewTarget = _fdictReviewTarget(dictWorkflow, *tAddress)
    bHasTarget = _fbEntryHasTarget(dictProjectTarget, dictConfirmed, dictReviewTarget)
    return {
        "sService": sService, "sRecordKind": sRecordKind, "sInstance": sInstance,
        "sLabel": fsDescribeBoundEntry(*tAddress),
        "bApplies": bApplies, "dictProjectTarget": dictProjectTarget,
        "dictConfirmed": dictConfirmed, "sState": sState,
        "sNotice": _fsNoticeFor(
            tAddress, sState, dictProjectTarget, dictConfirmed,
            _fbAutoArchiveSendsTo(dictWorkflow, tAddress)),
        "bHasTarget": bHasTarget,
        "sAuthorizes": "" if bHasTarget else _fsDescribeWhatConfirmingAllows(tAddress),
        "dictReview": {
            "sService": sService, "sRecordKind": sRecordKind,
            "sWorkflowKey": sWorkflowKey, "dictTarget": dictReviewTarget,
        },
    }


def _fbEvidenceWanted(dictEntry, dictQuery):
    if not dictQuery["bFetchEvidence"]:
        return False
    if not dictQuery["sService"]:
        return dictEntry["bApplies"]
    return (dictEntry["sService"], dictEntry["sRecordKind"], dictEntry["sInstance"]) == (
        dictQuery["sService"], dictQuery["sRecordKind"], dictQuery["sInstance"],
    )


def _fnAttachEvidence(dictCtx, sContainerId, listEntries, dictQuery):
    for dictEntry in listEntries:
        if _fbEvidenceWanted(dictEntry, dictQuery):
            dictEntry["dictEvidence"] = _DICT_EVIDENCE_FETCHERS[dictEntry["sService"]](
                dictCtx, sContainerId, dictEntry["sRecordKind"],
                dictEntry["dictReview"]["dictTarget"],
            )


def _fdictInvalidRecordState(error):
    return {
        "bValid": False, "sError": S_BINDING_RECORD_INVALID,
        "sReason": error.sReason, "sMessage": str(error),
    }


def _fdictDescribeBindings(dictCtx, sContainerId, dictWorkflow, dictQuery):
    """Return the record's state and every entry, with what project.json names."""
    sProjectUuid, sWorkflowKey, sWorkflowName = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    sEnvironmentInstance = _fsEnvironmentArchiveInstance(dictCtx, sContainerId, dictWorkflow)
    dictDescribed = {
        "sProjectUuid": sProjectUuid, "sWorkflowKey": sWorkflowKey,
        "sWorkflowName": sWorkflowName, "listEntries": [],
        "bEnvironmentArchiveInUse": bool(sEnvironmentInstance),
        "dictRecordState": {"bValid": True},
    }
    try:
        dictRecord = remoteBindings.fdictReadBindings(sProjectUuid)
    except remoteBindings.BindingRecordInvalidError as error:
        dictDescribed["dictRecordState"] = _fdictInvalidRecordState(error)
        return dictDescribed
    dictServices = (dictRecord["dictWorkflows"].get(sWorkflowKey) or {}).get("dictServices") or {}
    dictDescribed["listEntries"] = [
        _fdictDescribeEntry(
            dictWorkflow, sWorkflowKey, dictServices, tAddress,
            _fbEntryApplies(dictWorkflow, tAddress, sEnvironmentInstance),
        ) for tAddress in _T_ENTRY_ADDRESSES
    ]
    _fnAttachEvidence(dictCtx, sContainerId, dictDescribed["listEntries"], dictQuery)
    return dictDescribed


def _fnRegisterRead(app, dictCtx):
    """Register GET /api/bindings/{sContainerId}."""

    @app.get("/api/bindings/{sContainerId}")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictDescribeRemoteBindings(
        sContainerId: str, requestHttp: Request, bFetchEvidence: bool = True,
        sService: str = "", sRecordKind: str = "", sInstance: str = "",
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        dictQuery = {
            "bFetchEvidence": bFetchEvidence, "sService": sService,
            "sRecordKind": sRecordKind, "sInstance": sInstance,
        }
        return await asyncio.to_thread(
            _fdictDescribeBindings, dictCtx, sContainerId, dictWorkflow, dictQuery,
        )


# ---------------------------------------------------------------------------
# The confirmation
# ---------------------------------------------------------------------------


def _fnRequireReviewBody(request):
    """Refuse a confirmation that does not carry what was reviewed."""
    if None in (request.sRecordKind, request.sWorkflowKey, request.dictTarget):
        raise HTTPException(400, (
            "A confirmation carries what you reviewed: sWorkflowKey, "
            "sRecordKind and dictTarget. Review it in the dashboard, or "
            "run `vaibify bindings -p <project>`, which prints the exact "
            "confirm command."
        ))
    try:
        remoteBindings.ftTargetFields(request.sService, request.sRecordKind)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _ftRequireTheReviewIsCurrent(dictCtx, sContainerId, dictWorkflow, request):
    """Return ``(sProjectUuid, sWorkflowName, dictTarget, sInstance)``, or refuse a stale review."""
    sProjectUuid, sWorkflowKey, sWorkflowName = ftBindingScope(dictCtx, sContainerId, dictWorkflow)
    try:
        dictTarget = remoteBindings.fdictNormalizedTarget(
            request.sService, request.sRecordKind, request.dictTarget,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    sInstance = dictTarget.get("sInstance", "")
    if request.sService == remoteBindings.S_SERVICE_ZENODO and (
        sInstance not in remoteBindings.T_ZENODO_INSTANCES
    ):
        raise HTTPException(400, (
            f"a zenodo target names the instance, sandbox or zenodo, not {sInstance!r}"
        ))
    if request.sWorkflowKey != sWorkflowKey:
        fnRaiseBindingRefusal(
            S_BINDING_REVIEW_STALE, request.sService, request.sRecordKind, sInstance,
            "The workflow you reviewed is no longer the one loaded here, so "
            "nothing was confirmed. Review it again in this workflow.",
        )
    if dictTarget != _fdictReviewTarget(
        dictWorkflow, request.sService, request.sRecordKind, sInstance,
    ):
        fnRaiseBindingRefusal(
            S_BINDING_REVIEW_STALE, request.sService, request.sRecordKind, sInstance,
            f"What you reviewed for {fsDescribeBoundEntry(request.sService, request.sRecordKind, sInstance)} "
            f"is not what a review shows now (project.json may have changed), "
            f"so nothing was confirmed. Review it again.",
        )
    return sProjectUuid, sWorkflowName, dictTarget, sInstance


def _fdictRequireEvidence(dictCtx, sContainerId, request, dictTarget, sInstance):
    """Return the entry's evidence, or refuse when it did not succeed."""
    from .. import zenodoTokenAccess
    dictEvidence = _DICT_EVIDENCE_FETCHERS[request.sService](
        dictCtx, sContainerId, request.sRecordKind, dictTarget,
    )
    if dictEvidence.get("bSucceeded"):
        return dictEvidence
    sError = (
        zenodoTokenAccess.S_ZENODO_TOKEN_MISSING
        if dictEvidence.get("sState") == _S_TOKEN_MISSING
        else S_BINDING_EVIDENCE_UNAVAILABLE
    )
    fnRaiseBindingRefusal(
        sError, request.sService, request.sRecordKind, sInstance,
        dictEvidence.get("sMessage") or "The evidence could not be fetched.",
    )


def _fdictRecordConfirmation(dictCtx, sContainerId, dictWorkflow, request):
    """Pin the review, read the record, fetch the evidence, then write ONE entry."""
    sProjectUuid, sWorkflowName, dictTarget, sInstance = _ftRequireTheReviewIsCurrent(
        dictCtx, sContainerId, dictWorkflow, request,
    )
    try:
        remoteBindings.fdictReadBindings(sProjectUuid)
    except remoteBindings.BindingRecordInvalidError as error:
        fnRaiseForBindingError(error, request.sService, request.sRecordKind, sInstance)
    dictEvidence = _fdictRequireEvidence(dictCtx, sContainerId, request, dictTarget, sInstance)
    remoteBindings.fnConfirmBinding(
        sProjectUuid, request.sWorkflowKey, sWorkflowName, request.sService,
        request.sRecordKind, dictTarget, "researcher", dictEvidence=dictEvidence,
        sCarryFromWorkflowKey=request.sCarryFromWorkflowKey,
    )
    return {
        "bConfirmed": True, "sWorkflowKey": request.sWorkflowKey,
        "dictTarget": dictTarget, "dictEvidence": dictEvidence,
    }


def _fnRegisterConfirm(app, dictCtx):
    """Register POST /api/bindings/{sContainerId}/confirm."""

    @ffnAgentAction("confirm-remote-binding")
    @app.post("/api/bindings/{sContainerId}/confirm")
    # separate-authority: the write is a host file under ~/.vaibify, and
    # the container is neither read nor written.
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictConfirmBinding(
        sContainerId: str, request: BindingConfirmRequest,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        _fnRequireReviewBody(request)
        try:
            return await asyncio.to_thread(
                _fdictRecordConfirmation, dictCtx, sContainerId, dictWorkflow,
                request,
            )
        except remoteBindings.BindingRecordInvalidError as error:
            fnRaiseForBindingError(error, request.sService, request.sRecordKind)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error


# ---------------------------------------------------------------------------
# Moving an unreadable record aside
# ---------------------------------------------------------------------------


def _fdictRecordStateNow(sProjectUuid):
    try:
        remoteBindings.fdictReadBindings(sProjectUuid)
    except remoteBindings.BindingRecordInvalidError as error:
        return _fdictInvalidRecordState(error)
    return {"bValid": True}


def _fdictSetAsideRecord(dictCtx, sContainerId, dictWorkflow):
    """Rename the project's unreadable record aside; answer with its state now."""
    sProjectUuid = ftBindingScope(dictCtx, sContainerId, dictWorkflow)[0]
    try:
        sSetAsideName = remoteBindings.fsSetAsideUnreadableRecord(sProjectUuid)
    except remoteBindings.BindingRecordReadableError as error:
        fnRaiseBindingRefusal(S_BINDING_RECORD_VALID, "", "", "", str(error))
    except OSError as error:
        raise HTTPException(409, (
            f"The binding record was not moved aside: {error}. Nothing "
            f"changed."
        )) from error
    return {
        "bSetAside": True, "sSetAsideName": sSetAsideName,
        "dictRecordState": _fdictRecordStateNow(sProjectUuid),
    }


def _fnRegisterSetAside(app, dictCtx):
    """Register POST /api/bindings/{sContainerId}/set-aside."""

    @ffnAgentAction("set-aside-binding-record")
    @app.post("/api/bindings/{sContainerId}/set-aside")
    # separate-authority: one rename of a host file under ~/.vaibify,
    # and the container is neither read nor written.
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictSetAsideBindingRecord(sContainerId: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        return await asyncio.to_thread(
            _fdictSetAsideRecord, dictCtx, sContainerId, dictWorkflow,
        )


def fnRegisterAll(app, dictCtx):
    """Register the remote-binding routes."""
    _fnRegisterRead(app, dictCtx)
    _fnRegisterConfirm(app, dictCtx)
    _fnRegisterSetAside(app, dictCtx)
