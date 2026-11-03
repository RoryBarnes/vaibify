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

A workflow holds one Overleaf entry and, for Zenodo, two KINDS of
authorization, each confirmed separately and per instance (the sandbox,
``sandbox``, and zenodo.org, ``zenodo``):

- the **project record**, a target per instance (instance, concept DOI,
  deposition id). The instance ``project.json`` declares is compared
  with it; an entry on the other instance governs alone, and an entry
  with no concept yet authorizes minting a new record there;
- the **environment archive**, an instance authorization only. It is
  never compared with ``project.json`` and never touched by a project
  deposit.

Nothing writes a binding automatically. The researcher confirms one
(kind, instance) entry at a time, through a researcher-only route or
its ``vaibify do`` equivalent, after seeing the target ``project.json``
names beside what the host fetched about it with the researcher's own
token. The hub itself moves a confirmed project-record entry after a
deposit it performed, so the record follows the version chain without
the agent's hand, and creates nothing.

Schema version 2 is the only format read. A file that is not a
well-formed version 2 record raises :class:`BindingRecordInvalidError`,
naming the file and why; it is never read as unbound and never
converted. Each record is mode 600 in a mode-700 directory, written
atomically, and holds no secret. Every change is a read-modify-write
under the record's lock (:mod:`vaibify.config.lockedJsonRecord`), so a
confirmation and the hub's own update of a target cannot drop each
other.
"""

import copy
import json
import os
from datetime import datetime, timezone

from vaibify.config.durableFile import fnWriteFileDurably
from vaibify.config.lockedJsonRecord import (
    fcontextHoldRecordLock, fnMutateRecordLocked,
)

__all__ = [
    "BindingMismatchError",
    "BindingRecordInvalidError",
    "BindingRequiredError",
    "I_SCHEMA_VERSION",
    "S_INSTANCE_SANDBOX",
    "S_INSTANCE_ZENODO",
    "S_RECORD_KIND_ENVIRONMENT",
    "S_RECORD_KIND_PROJECT",
    "S_SERVICE_OVERLEAF",
    "S_SERVICE_ZENODO",
    "T_BINDING_SERVICES",
    "T_ZENODO_INSTANCES",
    "T_ZENODO_RECORD_KINDS",
    "fsBindingsDirectory",
    "fsBindingPath",
    "fsWorkflowBindingKey",
    "ftTargetFields",
    "fdictNormalizedTarget",
    "fdictReadBindings",
    "fdictLookUpEntry",
    "fdictConfirmedBinding",
    "fnConfirmBinding",
    "fnUpdateBoundTarget",
    "flistDeleteBindings",
    "fdictRequireOverleafTarget",
    "fdictRequireProjectRecordTarget",
    "fdictRequireEnvironmentArchiveTarget",
]

I_SCHEMA_VERSION = 2
S_SERVICE_OVERLEAF = "overleaf"
S_SERVICE_ZENODO = "zenodo"
T_BINDING_SERVICES = (S_SERVICE_OVERLEAF, S_SERVICE_ZENODO)
S_RECORD_KIND_PROJECT = "project"
S_RECORD_KIND_ENVIRONMENT = "environment"
T_ZENODO_RECORD_KINDS = (S_RECORD_KIND_PROJECT, S_RECORD_KIND_ENVIRONMENT)
S_INSTANCE_SANDBOX = "sandbox"
S_INSTANCE_ZENODO = "zenodo"
T_ZENODO_INSTANCES = (S_INSTANCE_SANDBOX, S_INSTANCE_ZENODO)

# The fields each target carries, by (service, record kind).
_DICT_TARGET_FIELDS = {
    (S_SERVICE_OVERLEAF, ""): ("sProjectId",),
    (S_SERVICE_ZENODO, S_RECORD_KIND_PROJECT): (
        "sInstance", "sConceptDoi", "sDepositionId",
    ),
    (S_SERVICE_ZENODO, S_RECORD_KIND_ENVIRONMENT): ("sInstance",),
}
_T_RECORD_FIELDS = ("sConceptDoi", "sDepositionId")


class BindingRequiredError(ValueError):
    """No researcher has confirmed this (service, kind, instance) for the workflow."""

    def __init__(self, sMessage, sService, sRecordKind="", sInstance=""):
        super().__init__(sMessage)
        self.sService, self.sRecordKind, self.sInstance = (
            sService, sRecordKind, sInstance,
        )


class BindingMismatchError(BindingRequiredError):
    """``project.json``, or the action, names a target the researcher did not confirm.

    ``sProjectValue`` is what ``project.json`` names ('' when the action
    itself disagrees, as a promotion over a confirmed record does) and
    ``sBoundValue`` what the researcher confirmed.
    """

    def __init__(
        self, sMessage, sService, sRecordKind="", sInstance="",
        sProjectValue="", sBoundValue="",
    ):
        super().__init__(sMessage, sService, sRecordKind, sInstance)
        self.sProjectValue, self.sBoundValue = sProjectValue, sBoundValue


class BindingRecordInvalidError(ValueError):
    """The binding record on disk is not a well-formed schema version 2 record."""

    def __init__(self, sPath, sReason):
        super().__init__(
            f"The binding record {_fsDisplayPath(sPath)} can't be read "
            f"({sReason}). Move it aside and confirm this workflow's "
            f"remotes again."
        )
        self.sPath, self.sReason = sPath, sReason


def _fsDisplayPath(sPath):
    """Return ``sPath`` with the home directory written as ``~``."""
    sHome = os.path.expanduser("~").rstrip(os.sep)
    if sHome and sPath.startswith(sHome + os.sep):
        return "~" + sPath[len(sHome):]
    return sPath


def fsBindingsDirectory():
    """Return ``~/.vaibify/bindings``, resolved when asked so tests can move HOME."""
    return os.path.join(os.path.expanduser("~"), ".vaibify", "bindings")


def fsBindingPath(sProjectUuid):
    """Return the record file of one project; only this name is ever read."""
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


def ftTargetFields(sService, sRecordKind=""):
    """Return the target fields of one (service, kind), or raise ``ValueError``."""
    _fnValidateService(sService)
    tKey = (sService, sRecordKind if sService == S_SERVICE_ZENODO else "")
    if tKey not in _DICT_TARGET_FIELDS:
        raise ValueError(
            f"{sRecordKind!r} is not a Zenodo record kind; the kinds are "
            f"{list(T_ZENODO_RECORD_KINDS)}"
        )
    return _DICT_TARGET_FIELDS[tKey]


def _fnValidateService(sService):
    if sService not in T_BINDING_SERVICES:
        raise ValueError(
            f"{sService!r} is not a bindable service; the services are "
            f"{list(T_BINDING_SERVICES)}"
        )


def _fnValidateInstance(sInstance):
    if sInstance not in T_ZENODO_INSTANCES:
        raise ValueError(
            f"a zenodo binding names the instance (sandbox or zenodo) the "
            f"token is spent on, not {sInstance!r}"
        )


# ---------------------------------------------------------------------------
# Reading: schema version 2, and nothing else
# ---------------------------------------------------------------------------


def _fdictEmptyRecord(sProjectUuid):
    return {
        "iSchemaVersion": I_SCHEMA_VERSION, "sProjectUuid": sProjectUuid,
        "dictWorkflows": {},
    }


def fdictReadBindings(sProjectUuid):
    """Return the project's whole record; an absent file is an empty record.

    Raises :class:`BindingRecordInvalidError` for a file that cannot be
    parsed or is not a well-formed schema version 2 record.
    """
    sPath = fsBindingPath(sProjectUuid)
    try:
        with open(sPath, "r", encoding="utf-8") as fileRecord:
            dictRecord = json.load(fileRecord)
    except FileNotFoundError:
        return _fdictEmptyRecord(sProjectUuid)
    except (OSError, ValueError) as error:
        raise BindingRecordInvalidError(sPath, f"it is not readable JSON: {error}") from None
    sProblem = _fsRecordProblem(dictRecord, sProjectUuid)
    if sProblem:
        raise BindingRecordInvalidError(sPath, sProblem)
    return dictRecord


def _fsRecordProblem(dictRecord, sProjectUuid):
    """Return why ``dictRecord`` is not a schema version 2 record, or ''."""
    if not isinstance(dictRecord, dict):
        return "it is not a JSON object"
    if dictRecord.get("iSchemaVersion") != I_SCHEMA_VERSION:
        return (
            f"its iSchemaVersion is {dictRecord.get('iSchemaVersion')!r}, "
            f"not {I_SCHEMA_VERSION}"
        )
    if dictRecord.get("sProjectUuid") != sProjectUuid:
        return "it names another project"
    if not isinstance(dictRecord.get("dictWorkflows"), dict):
        return "it has no dictWorkflows object"
    for sKey, dictWorkflow in dictRecord["dictWorkflows"].items():
        sProblem = _fsWorkflowProblem(dictWorkflow)
        if sProblem:
            return f"workflow {sKey!r} {sProblem}"
    return ""


def _fsWorkflowProblem(dictWorkflow):
    if not isinstance(dictWorkflow, dict) or not isinstance(
        dictWorkflow.get("sWorkflowName"), str,
    ):
        return "has no sWorkflowName"
    dictServices = dictWorkflow.get("dictServices")
    if not isinstance(dictServices, dict):
        return "has no dictServices object"
    listUnknown = sorted(set(dictServices) - set(T_BINDING_SERVICES))
    if listUnknown:
        return f"names unknown service(s) {listUnknown}"
    if S_SERVICE_OVERLEAF in dictServices:
        sProblem = _fsEntryProblem(
            dictServices[S_SERVICE_OVERLEAF], S_SERVICE_OVERLEAF, "", "",
        )
        if sProblem:
            return f"has an overleaf entry that {sProblem}"
    if S_SERVICE_ZENODO in dictServices:
        return _fsZenodoProblem(dictServices[S_SERVICE_ZENODO])
    return ""


def _fsZenodoProblem(dictZenodo):
    dictKinds = dictZenodo.get("dictRecordKinds") if isinstance(dictZenodo, dict) else None
    if not isinstance(dictKinds, dict):
        return "has a zenodo entry without dictRecordKinds"
    for sKind, dictKind in dictKinds.items():
        if sKind not in T_ZENODO_RECORD_KINDS:
            return f"names an unknown zenodo record kind {sKind!r}"
        dictInstances = dictKind.get("dictInstances") if isinstance(dictKind, dict) else None
        if not isinstance(dictInstances, dict):
            return f"has a zenodo {sKind} kind without dictInstances"
        for sInstance, dictEntry in dictInstances.items():
            sProblem = _fsEntryProblem(dictEntry, S_SERVICE_ZENODO, sKind, sInstance)
            if sProblem:
                return f"has a zenodo {sKind} entry {sInstance!r} that {sProblem}"
    return ""


def _fsEntryProblem(dictEntry, sService, sRecordKind, sInstance):
    if not isinstance(dictEntry, dict):
        return "is not an object"
    tFields = _DICT_TARGET_FIELDS[(sService, sRecordKind)]
    dictTarget = dictEntry.get("dictTarget")
    if not isinstance(dictTarget, dict) or set(dictTarget) != set(tFields) or not all(
        isinstance(sValue, str) for sValue in dictTarget.values()
    ):
        return f"has a dictTarget other than the text fields {list(tFields)}"
    if sService == S_SERVICE_ZENODO and (
        sInstance not in T_ZENODO_INSTANCES or dictTarget["sInstance"] != sInstance
    ):
        return "names an instance other than its own key"
    if not (
        isinstance(dictEntry.get("sConfirmedBy"), str)
        and isinstance(dictEntry.get("sConfirmedAt"), str)
        and isinstance(dictEntry.get("dictEvidence"), dict)
    ):
        return "lacks sConfirmedBy, sConfirmedAt or dictEvidence"
    return ""


def fdictLookUpEntry(dictServices, sService, sRecordKind, sInstance):
    """Return one entry of a workflow's ``dictServices``, or None; never a copy."""
    if sService == S_SERVICE_OVERLEAF:
        return dictServices.get(S_SERVICE_OVERLEAF)
    return (
        ((dictServices.get(S_SERVICE_ZENODO) or {}).get("dictRecordKinds") or {})
        .get(sRecordKind, {}).get("dictInstances", {}).get(sInstance)
    )


def fdictConfirmedBinding(sProjectUuid, sWorkflowKey, sService, sRecordKind="", sInstance=""):
    """Return one confirmed entry -- Overleaf's, or a Zenodo (kind, instance) -- or None."""
    ftTargetFields(sService, sRecordKind)
    if sService == S_SERVICE_ZENODO:
        _fnValidateInstance(sInstance)
    dictWorkflow = fdictReadBindings(sProjectUuid)["dictWorkflows"].get(sWorkflowKey) or {}
    dictEntry = fdictLookUpEntry(
        dictWorkflow.get("dictServices") or {}, sService, sRecordKind, sInstance,
    )
    return copy.deepcopy(dictEntry) if dictEntry else None


# ---------------------------------------------------------------------------
# Writing: one entry per confirmation, under the record's lock
# ---------------------------------------------------------------------------


def _fnWriteBindings(sProjectUuid, dictRecord):
    sDirectory = fsBindingsDirectory()
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    os.chmod(sDirectory, 0o700)
    fnWriteFileDurably(
        fsBindingPath(sProjectUuid),
        json.dumps(dictRecord, indent=2, sort_keys=True).encode("utf-8"),
        iMode=0o600,
    )


def _fnMutateBindings(sProjectUuid, fnMutate):
    """Apply ``fnMutate`` to the project's record under its lock."""
    fnMutateRecordLocked(
        fsBindingPath(sProjectUuid),
        lambda: fdictReadBindings(sProjectUuid), fnMutate,
        lambda dictRecord: _fnWriteBindings(sProjectUuid, dictRecord),
    )


def fdictNormalizedTarget(sService, sRecordKind, dictTarget):
    """Return the target with exactly its fields, as strings."""
    tFields = ftTargetFields(sService, sRecordKind)
    dictTarget = dictTarget or {}
    listUnknown = sorted(set(dictTarget) - set(tFields))
    if listUnknown:
        raise ValueError(
            f"a {sService} {sRecordKind} target has no field(s) {listUnknown}; "
            f"the fields are {list(tFields)}"
        )
    return {sField: str(dictTarget.get(sField) or "") for sField in tFields}


def _fnStoreEntry(dictServices, sService, sRecordKind, sInstance, dictEntry):
    if sService == S_SERVICE_OVERLEAF:
        dictServices[S_SERVICE_OVERLEAF] = dictEntry
        return
    dictKinds = dictServices.setdefault(S_SERVICE_ZENODO, {}).setdefault(
        "dictRecordKinds", {},
    )
    dictKinds.setdefault(sRecordKind, {}).setdefault(
        "dictInstances", {},
    )[sInstance] = dictEntry


def _fnDropEntry(dictRecord, sWorkflowKey, sService, sRecordKind, sInstance):
    """Remove one entry, then every container it leaves empty."""
    dictWorkflow = dictRecord["dictWorkflows"].get(sWorkflowKey) or {}
    dictServices = dictWorkflow.get("dictServices") or {}
    if sService == S_SERVICE_OVERLEAF:
        dictServices.pop(S_SERVICE_OVERLEAF, None)
    else:
        dictKinds = (dictServices.get(S_SERVICE_ZENODO) or {}).get("dictRecordKinds") or {}
        (dictKinds.get(sRecordKind) or {}).get("dictInstances", {}).pop(sInstance, None)
        for sKind in [sName for sName in dictKinds if not dictKinds[sName].get("dictInstances")]:
            dictKinds.pop(sKind)
        if not dictKinds:
            dictServices.pop(S_SERVICE_ZENODO, None)
    if not dictServices:
        dictRecord["dictWorkflows"].pop(sWorkflowKey, None)


def _fsTargetInstance(sService, dictTarget):
    """Return the Zenodo instance a normalized target names, validated; '' for Overleaf."""
    if sService != S_SERVICE_ZENODO:
        if not dictTarget["sProjectId"]:
            raise ValueError(f"a {sService} binding needs a target to confirm")
        return ""
    _fnValidateInstance(dictTarget["sInstance"])
    return dictTarget["sInstance"]


def fnConfirmBinding(
    sProjectUuid, sWorkflowKey, sWorkflowName, sService, sRecordKind,
    dictTarget, sConfirmedBy, dictEvidence=None, sCarryFromWorkflowKey="",
):
    """Record the researcher's confirmation of ONE entry, and change nothing else.

    ``sRecordKind`` is "" for Overleaf. ``sCarryFromWorkflowKey`` carries
    a moved workflow's entry across: the old key's same entry is removed
    once the new one is written, so one confirmation never authorizes
    two workflows.
    """
    dictTargetNormalized = fdictNormalizedTarget(sService, sRecordKind, dictTarget)
    sInstance = _fsTargetInstance(sService, dictTargetNormalized)
    dictEntry = {
        "dictTarget": dictTargetNormalized,
        "sConfirmedBy": str(sConfirmedBy or ""),
        "sConfirmedAt": datetime.now(timezone.utc).isoformat(),
        "dictEvidence": dict(dictEvidence or {}),
    }

    def fnRecordConfirmation(dictRecord):
        dictWorkflow = dictRecord["dictWorkflows"].setdefault(sWorkflowKey, {})
        dictWorkflow["sWorkflowName"] = sWorkflowName or ""
        _fnStoreEntry(
            dictWorkflow.setdefault("dictServices", {}), sService,
            sRecordKind, sInstance, dictEntry,
        )
        if sCarryFromWorkflowKey and sCarryFromWorkflowKey != sWorkflowKey:
            _fnDropEntry(dictRecord, sCarryFromWorkflowKey, sService, sRecordKind, sInstance)

    _fnMutateBindings(sProjectUuid, fnRecordConfirmation)


def fnUpdateBoundTarget(sProjectUuid, sWorkflowKey, sInstance, dictUpdate):
    """Let the hub move a CONFIRMED project-record entry after a deposit it made.

    A Zenodo deposit mints a deposition id and, the first time, a
    concept DOI; the entry for the instance it minted on follows them,
    so the next action still matches. Only the project kind is written,
    only that instance's entry, and never its instance; nothing here can
    create an entry, which stays the researcher's act.
    """
    _fnValidateInstance(sInstance)
    listUnknown = sorted(set(dictUpdate) - set(_T_RECORD_FIELDS))
    if listUnknown:
        raise ValueError(f"a deposit moves only {list(_T_RECORD_FIELDS)}, not {listUnknown}")

    def fnMoveTarget(dictRecord):
        dictWorkflow = dictRecord["dictWorkflows"].get(sWorkflowKey) or {}
        dictEntry = fdictLookUpEntry(
            dictWorkflow.get("dictServices") or {}, S_SERVICE_ZENODO,
            S_RECORD_KIND_PROJECT, sInstance,
        )
        if dictEntry is None:
            raise BindingRequiredError(
                f"no confirmed zenodo project-record entry for {sInstance!r} "
                f"to update for this workflow", S_SERVICE_ZENODO,
                S_RECORD_KIND_PROJECT, sInstance,
            )
        for sField, sValue in dictUpdate.items():
            if sValue:
                dictEntry["dictTarget"][sField] = str(sValue)
        dictEntry["sUpdatedAt"] = datetime.now(timezone.utc).isoformat()

    _fnMutateBindings(sProjectUuid, fnMoveTarget)


def flistDeleteBindings(sProjectUuid):
    """Delete the project's record; return the services it bound.

    A project that never had a record has nothing to delete, and no
    lock file is created for it. A record that cannot be read is
    deleted too, because the project it belonged to is going away; it
    names no services.
    """
    sPath = fsBindingPath(sProjectUuid)
    if not os.path.exists(sPath):
        return []
    with fcontextHoldRecordLock(sPath + ".lock"):
        try:
            dictWorkflows = fdictReadBindings(sProjectUuid)["dictWorkflows"]
        except BindingRecordInvalidError:
            dictWorkflows = {}
        listServices = sorted({
            sService for dictWorkflow in dictWorkflows.values()
            for sService in dictWorkflow["dictServices"]
        })
        try:
            os.remove(sPath)
        except FileNotFoundError:
            pass
    return listServices


# ---------------------------------------------------------------------------
# Checking an action against what the researcher confirmed
# ---------------------------------------------------------------------------


def fdictRequireOverleafTarget(sProjectUuid, sWorkflowKey, dictProjectTarget):
    """Return the bound Overleaf target, or refuse naming the disagreement.

    ``dictProjectTarget`` is what ``project.json`` says now. No binding
    at all is a refusal too, never a default: binding from
    ``project.json`` is exactly the confused deputy this record closes.
    """
    dictBound = fdictConfirmedBinding(sProjectUuid, sWorkflowKey, S_SERVICE_OVERLEAF)
    if dictBound is None:
        raise BindingRequiredError(
            "this workflow has no confirmed overleaf binding", S_SERVICE_OVERLEAF,
        )
    sProjectValue = str(dictProjectTarget.get("sProjectId") or "")
    sBoundValue = dictBound["dictTarget"]["sProjectId"]
    if sProjectValue and sProjectValue != sBoundValue:
        raise BindingMismatchError(
            f"project.json names overleaf sProjectId {sProjectValue!r}, but "
            f"the researcher confirmed {sBoundValue!r}", S_SERVICE_OVERLEAF,
            sProjectValue=sProjectValue, sBoundValue=sBoundValue,
        )
    return dict(dictBound["dictTarget"])


def fdictRequireProjectRecordTarget(
    sProjectUuid, sWorkflowKey, sInstance, dictProjectTarget,
    bInstanceOnly=False, bMint=False,
):
    """Return the project-record target bound on ``sInstance``, or refuse.

    The rules, in order: no entry for the instance refuses; a
    promotion (``bMint``) needs an entry naming no record yet, the
    authorization to mint one; and when ``sInstance`` is the one
    ``project.json`` declares (``dictProjectTarget["sInstance"]``),
    every concept or deposition field it names must equal the bound
    one, unless the action uses the instance alone. An entry on the
    other instance is one ``project.json`` says nothing about, and it
    governs alone.
    """
    dictEntry = fdictConfirmedBinding(
        sProjectUuid, sWorkflowKey, S_SERVICE_ZENODO, S_RECORD_KIND_PROJECT, sInstance,
    )
    if dictEntry is None:
        raise BindingRequiredError(
            f"this workflow has no confirmed zenodo project-record entry "
            f"for {sInstance!r}", S_SERVICE_ZENODO, S_RECORD_KIND_PROJECT, sInstance,
        )
    dictTarget = dictEntry["dictTarget"]
    if bMint and dictTarget["sConceptDoi"]:
        raise BindingMismatchError(
            f"a promotion mints a new record on {sInstance!r}, but the "
            f"researcher confirmed this workflow's project deposits there use "
            f"the record {dictTarget['sConceptDoi']!r}", S_SERVICE_ZENODO,
            S_RECORD_KIND_PROJECT, sInstance, sBoundValue=dictTarget["sConceptDoi"],
        )
    if not bInstanceOnly and dictProjectTarget.get("sInstance") == sInstance:
        _fnRequireRecordAgreement(dictProjectTarget, dictTarget, sInstance)
    return dict(dictTarget)


def _fnRequireRecordAgreement(dictProjectTarget, dictBoundTarget, sInstance):
    for sField in _T_RECORD_FIELDS:
        sProjectValue = str(dictProjectTarget.get(sField) or "")
        if sProjectValue and sProjectValue != dictBoundTarget[sField]:
            raise BindingMismatchError(
                f"project.json names zenodo {sField} {sProjectValue!r}, but "
                f"the researcher confirmed {dictBoundTarget[sField]!r}",
                S_SERVICE_ZENODO, S_RECORD_KIND_PROJECT, sInstance,
                sProjectValue=sProjectValue, sBoundValue=dictBoundTarget[sField],
            )


def fdictRequireEnvironmentArchiveTarget(sProjectUuid, sWorkflowKey, sInstance):
    """Return the environment-archive authorization on ``sInstance``, or refuse.

    The environment kind authorizes an instance and nothing more; it is
    never compared with ``project.json``.
    """
    dictAuthorization = fdictConfirmedBinding(
        sProjectUuid, sWorkflowKey, S_SERVICE_ZENODO, S_RECORD_KIND_ENVIRONMENT,
        sInstance,
    )
    if dictAuthorization is None:
        raise BindingRequiredError(
            f"this workflow has no confirmed zenodo environment-archive "
            f"authorization for {sInstance!r}", S_SERVICE_ZENODO,
            S_RECORD_KIND_ENVIRONMENT, sInstance,
        )
    return dict(dictAuthorization["dictTarget"])
