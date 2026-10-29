"""Where a route finds the trusted remote target for the workflow it serves.

The binding record (:mod:`vaibify.config.remoteBindings`) is keyed by the
registry's project identity and by the workflow; a route knows a
container id and a loaded workflow. This module makes the translation
in one place, so every token-bearing route asks the same question the
same way, and turns the record's refusals into the HTTP answer a
researcher can act on: the refusal names both values and the command
that re-confirms.
"""

__all__ = [
    "fdictProjectTargets",
    "ftBindingScope",
    "fsRequireBoundOverleafProjectId",
    "fdictRequireBoundZenodoTarget",
    "fnRecordZenodoOutcomeInBinding",
]

import posixpath

from fastapi import HTTPException

from vaibify.config import remoteBindings
from vaibify.config.registryManager import fdictGetProject, fsProjectUuidFor

from . import projectRoots
from .pipelineServer import WORKSPACE_ROOT, fsContainerNameForId
from .workflowManager import fsWorkflowLoadedFromPath


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


def _fdictRequireBinding(dictCtx, sContainerId, dictWorkflow, sService, tFields):
    """Return the bound target for ``sService`` or raise the HTTP refusal."""
    sProjectUuid, sWorkflowKey, _ = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    try:
        return remoteBindings.fdictRequireMatchingBinding(
            sProjectUuid, sWorkflowKey, sService,
            fdictProjectTargets(dictWorkflow)[sService], tFields,
        )
    except (remoteBindings.BindingRequiredError,
            remoteBindings.BindingMismatchError) as error:
        raise HTTPException(409, str(error)) from error


def fsRequireBoundOverleafProjectId(dictCtx, sContainerId, dictWorkflow):
    """Return the Overleaf project id the researcher confirmed for this workflow."""
    return _fdictRequireBinding(
        dictCtx, sContainerId, dictWorkflow,
        remoteBindings.S_SERVICE_OVERLEAF, None,
    )["sProjectId"]


def fdictRequireBoundZenodoTarget(
    dictCtx, sContainerId, dictWorkflow, sInstanceOverride=None,
    bInstanceOnly=False,
):
    """Return the Zenodo target the researcher confirmed for this workflow.

    ``sInstanceOverride`` names the instance an action will use when it
    is not the one ``project.json`` declares (a promotion publishes to
    production); the bound instance must then be that one.
    ``bInstanceOnly`` compares the instance alone, for a deposit that
    starts its own record (the environment archive).
    """
    tFields = ("sInstance",) if bInstanceOnly else None
    dictTarget = _fdictRequireBinding(
        dictCtx, sContainerId, dictWorkflow,
        remoteBindings.S_SERVICE_ZENODO, tFields,
    )
    sInstance = sInstanceOverride or dictTarget["sInstance"]
    if sInstance != dictTarget["sInstance"]:
        raise HTTPException(409, (
            f"this action publishes to Zenodo instance {sInstance!r}, but "
            f"the researcher confirmed {dictTarget['sInstance']!r}; confirm "
            f"the instance first (vaibify do confirm-remote-binding "
            f"sService=zenodo)"
        ))
    return dictTarget


def fnRecordZenodoOutcomeInBinding(
    dictCtx, sContainerId, dictWorkflow, sInstance, sConceptDoi,
    sDepositionId,
):
    """Move the confirmed Zenodo target to the deposit the hub just made."""
    sProjectUuid, sWorkflowKey, _ = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    remoteBindings.fnUpdateBoundTarget(
        sProjectUuid, sWorkflowKey, remoteBindings.S_SERVICE_ZENODO, {
            "sInstance": sInstance, "sConceptDoi": sConceptDoi,
            "sDepositionId": sDepositionId,
        },
    )
