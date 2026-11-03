"""Where a route finds the trusted remote target for the workflow it serves.

The binding record (:mod:`vaibify.config.remoteBindings`) is keyed by the
registry's project identity and by the workflow; a route knows a
container id and a loaded workflow. This module makes the translation
in one place, so every token-bearing route asks the same question the
same way, and turns every refusal about a binding into ONE coded 409:
``{sError, sService, sRecordKind, sInstance, sMessage}``. The codes:

- ``BINDING-REQUIRED``: nothing is confirmed for this (kind, instance);
- ``BINDING-MISMATCH``: ``project.json``, or the action, names a target
  the researcher did not confirm;
- ``BINDING-REVIEW-STALE``: a confirmation was reviewed on a view that
  is no longer the loaded one;
- ``BINDING-EVIDENCE-UNAVAILABLE``: the evidence a confirmation needs
  could not be fetched;
- ``BINDING-RECORD-INVALID``: the record on disk is not schema version 2;
- ``ZENODO-TOKEN-MISSING``: no active token for the instance.

``sMessage`` is written for the researcher, in the dashboard's words.
"""

__all__ = [
    "S_BINDING_EVIDENCE_UNAVAILABLE",
    "S_BINDING_MISMATCH",
    "S_BINDING_RECORD_INVALID",
    "S_BINDING_REQUIRED",
    "S_BINDING_REVIEW_STALE",
    "fdictProjectTargets",
    "fsDeclaredZenodoInstance",
    "ftBindingScope",
    "fsDescribeBoundEntry",
    "fnRaiseBindingRefusal",
    "fnRaiseForBindingError",
    "fsRequireBoundOverleafProjectId",
    "fdictRequireBoundZenodoTarget",
    "fnRecordZenodoOutcomeInBinding",
]

import posixpath

from fastapi import HTTPException

from vaibify.config import remoteBindings
from vaibify.config.registryManager import fdictGetProject, fsProjectUuidFor
from vaibify.reproducibility.syncBookkeeping import fsDescribeZenodoService

from . import projectRoots
from .pipelineServer import WORKSPACE_ROOT, fsContainerNameForId
from .workflowManager import fsWorkflowLoadedFromPath

S_BINDING_REQUIRED = "BINDING-REQUIRED"
S_BINDING_MISMATCH = "BINDING-MISMATCH"
S_BINDING_REVIEW_STALE = "BINDING-REVIEW-STALE"
S_BINDING_EVIDENCE_UNAVAILABLE = "BINDING-EVIDENCE-UNAVAILABLE"
S_BINDING_RECORD_INVALID = "BINDING-RECORD-INVALID"

_S_HOW_TO_CONFIRM = (
    "Review and confirm it from this project's binding notice in the "
    "dashboard, or print the review and its confirm command with "
    "`vaibify bindings -p <project>`."
)
_S_IF_NOT_YOURS = "If you did not make this change, an agent edited project.json."


def fdictProjectTargets(dictWorkflow):
    """Return what ``project.json`` says the remotes are, per service."""
    return {
        remoteBindings.S_SERVICE_OVERLEAF: {
            "sProjectId": str(dictWorkflow.get("sOverleafProjectId") or ""),
        },
        remoteBindings.S_SERVICE_ZENODO: {
            "sInstance": str(dictWorkflow.get("sZenodoService") or ""),
            "sConceptDoi": str(dictWorkflow.get("sZenodoConceptDoi") or ""),
            "sDepositionId": str(dictWorkflow.get("sZenodoDepositionId") or ""),
        },
    }


def fsDeclaredZenodoInstance(dictWorkflow):
    """Return the instance ``project.json`` declares; undeclared reads as the sandbox.

    Every deposit path reads an empty ``sZenodoService`` as the
    sandbox, so the binding check does too.
    """
    return str(
        (dictWorkflow or {}).get("sZenodoService")
        or remoteBindings.S_INSTANCE_SANDBOX
    )


def ftBindingScope(dictCtx, sContainerId, dictWorkflow):
    """Return ``(sProjectUuid, sWorkflowKey, sWorkflowName)`` for a route's workflow.

    The project is the registered one serving this container; a
    container the registry does not know has no identity and no
    binding, so its token-bearing actions are refused.
    """
    sName = fsContainerNameForId(dictCtx["docker"], sContainerId)
    if not sName or fdictGetProject(sName) is None:
        raise HTTPException(409, (
            "This container is not a registered project, so no remote "
            "binding can be confirmed for it."
        ))
    sProjectRoot = projectRoots.fsResolveProjectRoot(
        sContainerId, WORKSPACE_ROOT,
    )
    sLoadedFrom = fsWorkflowLoadedFromPath(dictWorkflow)
    sRelative = (
        posixpath.relpath(sLoadedFrom, sProjectRoot)
        if sLoadedFrom and sProjectRoot else sLoadedFrom
    )
    sWorkflowName = str(
        dictWorkflow.get("sWorkflowName") or dictWorkflow.get("sName") or "",
    )
    return (
        fsProjectUuidFor(sName),
        remoteBindings.fsWorkflowBindingKey(sRelative, sWorkflowName),
        sWorkflowName,
    )


def fsDescribeBoundEntry(sService, sRecordKind="", sInstance=""):
    """Return how a researcher names one binding entry, in the dashboard's words."""
    if sService == remoteBindings.S_SERVICE_OVERLEAF:
        return "this workflow's Overleaf project"
    sSite = fsDescribeZenodoService(sInstance)
    if sRecordKind == remoteBindings.S_RECORD_KIND_ENVIRONMENT:
        return f"this workflow's environment archive on {sSite}"
    return f"this workflow's project deposits on {sSite}"


def fnRaiseBindingRefusal(sError, sService, sRecordKind, sInstance, sMessage):
    """Raise the one coded 409 every binding refusal is."""
    raise HTTPException(409, {
        "sError": sError, "sService": sService, "sRecordKind": sRecordKind,
        "sInstance": sInstance, "sMessage": sMessage,
    })


def _fsDescribeBindingError(error):
    sWhat = fsDescribeBoundEntry(error.sService, error.sRecordKind, error.sInstance)
    if not isinstance(error, remoteBindings.BindingMismatchError):
        return f"Nothing is confirmed yet for {sWhat}. {_S_HOW_TO_CONFIRM}"
    if not error.sProjectValue:
        return (
            f"This action mints a NEW record for {sWhat}, but you confirmed "
            f"they use the record {error.sBoundValue}. {_S_HOW_TO_CONFIRM}"
        )
    return (
        f"project.json names {error.sProjectValue} for {sWhat}, but you "
        f"confirmed {error.sBoundValue}. {_S_IF_NOT_YOURS} {_S_HOW_TO_CONFIRM}"
    )


def fnRaiseForBindingError(error, sService, sRecordKind="", sInstance=""):
    """Turn an error from the binding record into its coded 409."""
    if isinstance(error, remoteBindings.BindingRecordInvalidError):
        fnRaiseBindingRefusal(
            S_BINDING_RECORD_INVALID, sService, sRecordKind, sInstance, str(error),
        )
    sCode = (
        S_BINDING_MISMATCH
        if isinstance(error, remoteBindings.BindingMismatchError)
        else S_BINDING_REQUIRED
    )
    fnRaiseBindingRefusal(
        sCode, error.sService, error.sRecordKind, error.sInstance,
        _fsDescribeBindingError(error),
    )


def _fdictCheckOrRefuse(fdictCheck, sService, sRecordKind, sInstance):
    try:
        return fdictCheck()
    except (remoteBindings.BindingRequiredError,
            remoteBindings.BindingRecordInvalidError) as error:
        fnRaiseForBindingError(error, sService, sRecordKind, sInstance)


def fsRequireBoundOverleafProjectId(dictCtx, sContainerId, dictWorkflow):
    """Return the Overleaf project id the researcher confirmed for this workflow."""
    sProjectUuid, sWorkflowKey, _ = ftBindingScope(dictCtx, sContainerId, dictWorkflow)
    dictProjectTarget = fdictProjectTargets(dictWorkflow)[remoteBindings.S_SERVICE_OVERLEAF]
    return _fdictCheckOrRefuse(
        lambda: remoteBindings.fdictRequireOverleafTarget(
            sProjectUuid, sWorkflowKey, dictProjectTarget,
        ), remoteBindings.S_SERVICE_OVERLEAF, "", "",
    )["sProjectId"]


def fdictRequireBoundZenodoTarget(
    dictCtx, sContainerId, dictWorkflow, sRecordKind, sInstance="",
    bInstanceOnly=False, bMint=False,
):
    """Return the Zenodo target the researcher confirmed for one kind and instance.

    ``sInstance`` is the instance the action spends the token on; it
    defaults to the one ``project.json`` declares. The project kind
    compares ``project.json``'s record on that instance alone, unless
    ``bInstanceOnly``; ``bMint`` is a promotion, which needs an entry
    naming no record yet. The environment kind authorizes the instance
    and compares nothing.
    """
    if sRecordKind not in remoteBindings.T_ZENODO_RECORD_KINDS:
        raise ValueError(f"{sRecordKind!r} is not a Zenodo record kind")
    sTargetInstance = sInstance or fsDeclaredZenodoInstance(dictWorkflow)
    sProjectUuid, sWorkflowKey, _ = ftBindingScope(dictCtx, sContainerId, dictWorkflow)
    if sRecordKind == remoteBindings.S_RECORD_KIND_ENVIRONMENT:
        def fdictCheck():
            return remoteBindings.fdictRequireEnvironmentArchiveTarget(
                sProjectUuid, sWorkflowKey, sTargetInstance,
            )
    else:
        dictProjectTarget = dict(
            fdictProjectTargets(dictWorkflow)[remoteBindings.S_SERVICE_ZENODO],
            sInstance=fsDeclaredZenodoInstance(dictWorkflow),
        )

        def fdictCheck():
            return remoteBindings.fdictRequireProjectRecordTarget(
                sProjectUuid, sWorkflowKey, sTargetInstance, dictProjectTarget,
                bInstanceOnly, bMint,
            )
    return _fdictCheckOrRefuse(
        fdictCheck, remoteBindings.S_SERVICE_ZENODO, sRecordKind, sTargetInstance,
    )


def fnRecordZenodoOutcomeInBinding(
    dictCtx, sContainerId, dictWorkflow, sInstance, sConceptDoi,
    sDepositionId,
):
    """Move the confirmed project-record entry to the deposit the hub just made.

    Only the project kind's entry for the instance the deposit minted
    on moves; the environment kind is never written here.
    """
    sProjectUuid, sWorkflowKey, _ = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    _fdictCheckOrRefuse(
        lambda: remoteBindings.fnUpdateBoundTarget(
            sProjectUuid, sWorkflowKey, sInstance,
            {"sConceptDoi": sConceptDoi, "sDepositionId": sDepositionId},
        ), remoteBindings.S_SERVICE_ZENODO,
        remoteBindings.S_RECORD_KIND_PROJECT, sInstance,
    )
