"""Which remote a token-bearing action may act on: a record only the researcher writes.

Overleaf and Zenodo targets live in ``project.json`` as fields of the
workflow, and the in-container agent can edit that file. A token-bearing
action that read its target from there was a confused deputy: the agent
could point the researcher's Overleaf token at another project, or the
Zenodo token at another record, by editing a string. The trusted copy of
each target therefore lives on the HOST, under ``~/.vaibify/bindings``,
in a record keyed by a project identity the agent cannot rename into or
out of (the registry's random ``sProjectUuid``) and by the workflow
(its file path relative to the project root, plus its name).

Nothing writes a binding automatically. The researcher confirms one,
through a researcher-only route or its ``vaibify do`` equivalent, after
seeing the target ``project.json`` names beside what the host fetched
about it with the researcher's own token. Every token-bearing action
then reads the record and REQUIRES ``project.json`` to agree with it; a
disagreement refuses, naming both values, and a workflow that moved or
was renamed has no binding until the researcher re-binds, optionally
carrying the old one across. The hub itself updates a bound Zenodo
target after a deposit it performed, so the record follows the version
chain without the agent's hand.

Each record is mode 600 in a mode-700 directory, written atomically,
and holds no secret: targets, who confirmed them and when.
"""

import json
import os
from datetime import datetime, timezone

from vaibify.config.durableFile import fnWriteFileDurably

__all__ = [
    "BindingMismatchError",
    "BindingRequiredError",
    "S_SERVICE_OVERLEAF",
    "S_SERVICE_ZENODO",
    "T_BINDING_SERVICES",
    "fsBindingsDirectory",
    "fsWorkflowBindingKey",
    "fdictReadBindings",
    "fdictConfirmedBinding",
    "fnConfirmBinding",
    "fnUpdateBoundTarget",
    "flistDeleteBindings",
    "fdictRequireMatchingBinding",
]

S_SERVICE_OVERLEAF = "overleaf"
S_SERVICE_ZENODO = "zenodo"
T_BINDING_SERVICES = (S_SERVICE_OVERLEAF, S_SERVICE_ZENODO)

# The fields a target of each service carries. Every non-empty field
# ``project.json`` names must equal the bound one; an empty field in
# ``project.json`` (a Zenodo concept not yet minted) constrains nothing.
DICT_TARGET_FIELDS = {
    S_SERVICE_OVERLEAF: ("sProjectId",),
    S_SERVICE_ZENODO: ("sInstance", "sConceptDoi", "sDepositionId"),
}


class BindingRequiredError(ValueError):
    """No researcher has confirmed a target for this workflow and service."""


class BindingMismatchError(ValueError):
    """``project.json`` names a target the researcher did not confirm."""


def fsBindingsDirectory():
    """Return ``~/.vaibify/bindings``, resolved when asked so tests can move HOME."""
    return os.path.join(os.path.expanduser("~"), ".vaibify", "bindings")


def _fsBindingPath(sProjectUuid):
    if not sProjectUuid or "/" in sProjectUuid or sProjectUuid.startswith("."):
        raise ValueError(f"not a project identity: {sProjectUuid!r}")
    return os.path.join(fsBindingsDirectory(), sProjectUuid + ".json")


def fsWorkflowBindingKey(sWorkflowRelativePath, sWorkflowName):
    """Return the record key for one workflow: its relative path and its name.

    A moved or renamed workflow gets a different key, so its bindings
    no longer match and its token-bearing actions refuse until the
    researcher re-binds.
    """
    return f"{sWorkflowRelativePath or ''}::{sWorkflowName or ''}"


def fdictReadBindings(sProjectUuid):
    """Return the project's whole record; an absent file is an empty record."""
    sPath = _fsBindingPath(sProjectUuid)
    try:
        with open(sPath, "r", encoding="utf-8") as fileRecord:
            dictRecord = json.load(fileRecord)
    except FileNotFoundError:
        return {"sProjectUuid": sProjectUuid, "dictWorkflows": {}}
    if not isinstance(dictRecord, dict) or not isinstance(
        dictRecord.get("dictWorkflows"), dict,
    ):
        raise ValueError(f"the binding record {sPath} is not well formed")
    return dictRecord


def _fnWriteBindings(sProjectUuid, dictRecord):
    sDirectory = fsBindingsDirectory()
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    os.chmod(sDirectory, 0o700)
    fnWriteFileDurably(
        _fsBindingPath(sProjectUuid),
        json.dumps(dictRecord, indent=2, sort_keys=True).encode("utf-8"),
        iMode=0o600,
    )


def _fnValidateService(sService):
    if sService not in T_BINDING_SERVICES:
        raise ValueError(
            f"{sService!r} is not a bindable service; the services are "
            f"{list(T_BINDING_SERVICES)}"
        )


def _fdictNormalizedTarget(sService, dictTarget):
    """Return the target with exactly the service's fields, as strings."""
    _fnValidateService(sService)
    dictTarget = dictTarget or {}
    listUnknown = sorted(set(dictTarget) - set(DICT_TARGET_FIELDS[sService]))
    if listUnknown:
        raise ValueError(
            f"a {sService} target has no field(s) {listUnknown}; the fields "
            f"are {list(DICT_TARGET_FIELDS[sService])}"
        )
    return {
        sField: str(dictTarget.get(sField) or "")
        for sField in DICT_TARGET_FIELDS[sService]
    }


def fdictConfirmedBinding(sProjectUuid, sWorkflowKey, sService):
    """Return the confirmed binding for one workflow and service, or None."""
    _fnValidateService(sService)
    dictWorkflow = fdictReadBindings(sProjectUuid)["dictWorkflows"].get(
        sWorkflowKey,
    )
    if not isinstance(dictWorkflow, dict):
        return None
    dictBinding = (dictWorkflow.get("dictServices") or {}).get(sService)
    return dict(dictBinding) if isinstance(dictBinding, dict) else None


def fnConfirmBinding(
    sProjectUuid, sWorkflowKey, sWorkflowName, sService, dictTarget,
    sConfirmedBy, dictEvidence=None, sCarryFromWorkflowKey="",
):
    """Record the researcher's confirmation of one target.

    ``sCarryFromWorkflowKey`` carries a moved workflow's binding across:
    the old key's entry for this service is removed once the new one is
    written, so one confirmation never authorizes two workflows.
    """
    dictTargetNormalized = _fdictNormalizedTarget(sService, dictTarget)
    if not any(dictTargetNormalized.values()):
        raise ValueError(f"a {sService} binding needs a target to confirm")
    if sService == S_SERVICE_ZENODO and not dictTargetNormalized["sInstance"]:
        raise ValueError(
            "a zenodo binding names the instance (sandbox or zenodo) the "
            "token is spent on"
        )
    dictRecord = fdictReadBindings(sProjectUuid)
    dictWorkflow = dictRecord["dictWorkflows"].setdefault(sWorkflowKey, {})
    dictWorkflow["sWorkflowName"] = sWorkflowName or ""
    dictWorkflow.setdefault("dictServices", {})[sService] = {
        "dictTarget": dictTargetNormalized,
        "sConfirmedBy": str(sConfirmedBy or ""),
        "sConfirmedAt": datetime.now(timezone.utc).isoformat(),
        "dictEvidence": dict(dictEvidence or {}),
    }
    if sCarryFromWorkflowKey and sCarryFromWorkflowKey != sWorkflowKey:
        _fnDropService(dictRecord, sCarryFromWorkflowKey, sService)
    _fnWriteBindings(sProjectUuid, dictRecord)


def _fnDropService(dictRecord, sWorkflowKey, sService):
    dictWorkflow = dictRecord["dictWorkflows"].get(sWorkflowKey) or {}
    (dictWorkflow.get("dictServices") or {}).pop(sService, None)
    if not dictWorkflow.get("dictServices"):
        dictRecord["dictWorkflows"].pop(sWorkflowKey, None)


def fnUpdateBoundTarget(sProjectUuid, sWorkflowKey, sService, dictUpdate):
    """Let the hub move a CONFIRMED target after an action it performed.

    A Zenodo deposit mints a deposition id and, the first time, a
    concept DOI; the record follows them so the next action still
    matches. Only fields of an existing binding change; nothing here
    can create a binding, which stays the researcher's act.
    """
    dictRecord = fdictReadBindings(sProjectUuid)
    dictBinding = (
        (dictRecord["dictWorkflows"].get(sWorkflowKey) or {})
        .get("dictServices", {}).get(sService)
    )
    if dictBinding is None:
        raise BindingRequiredError(
            f"no confirmed {sService} binding to update for this workflow"
        )
    for sField, sValue in _fdictNormalizedTarget(sService, dictUpdate).items():
        if sValue:
            dictBinding["dictTarget"][sField] = sValue
    dictBinding["sUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    _fnWriteBindings(sProjectUuid, dictRecord)


def flistDeleteBindings(sProjectUuid):
    """Delete the project's record; return the services it bound."""
    dictRecord = fdictReadBindings(sProjectUuid)
    listServices = sorted({
        sService
        for dictWorkflow in dictRecord["dictWorkflows"].values()
        for sService in (dictWorkflow.get("dictServices") or {})
    })
    try:
        os.remove(_fsBindingPath(sProjectUuid))
    except FileNotFoundError:
        pass
    return listServices


def fdictRequireMatchingBinding(
    sProjectUuid, sWorkflowKey, sService, dictProjectTarget,
    tFieldsThatMustMatch=None,
):
    """Return the bound target, or refuse naming the disagreement.

    ``dictProjectTarget`` is what ``project.json`` says now. Every
    non-empty field of it must equal the bound value; a field this
    action does not use may be excluded with ``tFieldsThatMustMatch``.
    No binding at all is a refusal too, never a default: binding from
    ``project.json`` is exactly the confused deputy this record exists
    to close.
    """
    dictBound = fdictConfirmedBinding(sProjectUuid, sWorkflowKey, sService)
    if dictBound is None:
        raise BindingRequiredError(
            f"this workflow has no confirmed {sService} binding; the "
            f"researcher confirms one (vaibify do confirm-remote-binding "
            f"sService={sService}) before any {sService} action runs"
        )
    dictProject = _fdictNormalizedTarget(sService, dictProjectTarget)
    tFields = tFieldsThatMustMatch or DICT_TARGET_FIELDS[sService]
    for sField in tFields:
        sProjectValue = dictProject.get(sField, "")
        sBoundValue = dictBound["dictTarget"].get(sField, "")
        if sProjectValue and sProjectValue != sBoundValue:
            raise BindingMismatchError(
                f"project.json names {sService} {sField} "
                f"{sProjectValue!r}, but the researcher confirmed "
                f"{sBoundValue!r}; the action is refused until the two "
                f"agree (re-confirm with vaibify do confirm-remote-binding "
                f"sService={sService} if the change is yours)"
            )
    return dict(dictBound["dictTarget"])
