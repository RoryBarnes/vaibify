"""Whether each step's scan-found dependencies are reconciled, and current.

The workflow JSON is the dependency graph: a cross-step file read counts
only when a ``{step:<id>.<stem>}`` token or ``saDependencies`` declares
it. The dependency scanner reads each step's scripts and finds reads of
another step's declared output; a finding the workflow neither declares
nor the researcher has dismissed is an edge the graph is missing, so the
step is not Self-Consistent (Level 1, ``dependency-unreconciled``).

A scan is evidence only while what it examined still holds: every
script it read, byte for byte (a clean scan found nothing in them, and
an edit can add a read), and what it matched them against -- the
scripts the step's commands name and every step's output declarations,
in order. When any of these has
moved, or the step's last scan failed, the step reads "dependency scan
not current" -- unknown, never a pass and never a red verdict drawn from
old data. A step the hub has not scanned carries no annotation and no
claim is made about it.

The scan results ride on each step as the transient
``dictDependencyScan`` annotation (never persisted); dismissals are the
researcher's and persist in ``listDismissedDependencyFindings``, each
bound to the script hash it was made against, so a changed script asks
again.
"""

import hashlib
import json

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.gui.commandUtilities import ftExtractScriptPathForLanguage


__all__ = [
    "S_SCAN_ANNOTATION_KEY",
    "S_DISMISSALS_KEY",
    "S_STATE_FAILED",
    "S_STATE_SCANNED",
    "S_DEPENDENCY_UNRECONCILED",
    "S_DEPENDENCY_NOT_CURRENT",
    "fdictDependencyStatesByStep",
    "S_SCRIPT_HASHES_KEY",
    "S_INPUTS_FINGERPRINT_KEY",
    "T_SCANNED_COMMAND_KEYS",
    "fbFindingMatchesDismissal",
    "fsScanInputsFingerprint",
]

S_SCAN_ANNOTATION_KEY = "dictDependencyScan"
S_DISMISSALS_KEY = "listDismissedDependencyFindings"
S_STATE_SCANNED = "scanned"
S_STATE_FAILED = "failed"
S_DEPENDENCY_UNRECONCILED = "unreconciled"
S_DEPENDENCY_NOT_CURRENT = "not-current"
S_DEPENDENCY_RECONCILED = "reconciled"
S_SCRIPT_HASHES_KEY = "dictScriptSha256"
S_INPUTS_FINGERPRINT_KEY = "sScanInputsFingerprint"
T_SCANNED_COMMAND_KEYS = (
    "saDataCommands", "saPlotCommands", "saSetupCommands", "saCommands")


def _flistStepDeclaredOutputs(dictStep):
    """Return the output declarations a finding may have matched."""
    return list(dictStep.get("saOutputDataFiles", []) or []) + list(
        dictStep.get("saPlotFiles", []) or [])


def _fdictIndexByStepId(dictWorkflow):
    """Return ``{sStepId: iStepIndex}`` for steps that carry an id."""
    return {dictStep.get("sStepId"): iIndex for iIndex, dictStep in enumerate(
                (dictWorkflow or {}).get("listSteps", []) or [])
            if isinstance(dictStep, dict) and dictStep.get("sStepId")}


def fsScanInputsFingerprint(dictWorkflow, dictStep):
    """Return a hash of what a step's scan matched its scripts against.

    That is the scripts its commands name, never the commands' whole
    text (declaring an edge with a token changes no script), and every
    step's output declarations in step order, because a read matches
    only an EARLIER step's output.
    """
    dictInputs = {
        "sProjectRepoPath": (dictWorkflow or {}).get("sProjectRepoPath", ""),
        "sDirectory": dictStep.get("sDirectory", ""),
        "listScripts": [
            list(ftExtractScriptPathForLanguage(sCommand))
            for sKey in T_SCANNED_COMMAND_KEYS
            for sCommand in dictStep.get(sKey, []) or []],
        "listDeclarations": [
            [dictOther.get("sStepId", ""),
             _flistStepDeclaredOutputs(dictOther)]
            for dictOther in (dictWorkflow or {}).get("listSteps", []) or []
            if isinstance(dictOther, dict)],
    }
    return hashlib.sha256(json.dumps(
        dictInputs, sort_keys=True).encode("utf-8")).hexdigest()


def _fdictScriptHashesNow(dictWorkflow, filesRepo):
    """Hash every script a scan read, in one adapter call.

    A script the adapter saw missing hashes to "", which is how the scan
    records a script that was absent when it ran.
    """
    setPaths = set()
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        dictScan = (dictStep or {}).get(S_SCAN_ANNOTATION_KEY) if isinstance(
            dictStep, dict) else None
        setPaths.update((dictScan or {}).get(S_SCRIPT_HASHES_KEY) or {})
        for dictFinding in (dictScan or {}).get("listFindings", []) or []:
            if dictFinding.get("sScriptPath"):
                setPaths.add(dictFinding["sScriptPath"])
    if not setPaths:
        return {}
    try:
        dictHashed = filesRepo.fdictHashFiles(sorted(setPaths)) or {}
    except (OSError, ValueError) as error:
        # Hashes that cannot be read leave every finding unproven:
        # "not current", never a pass drawn from the old scan.
        fnReRaiseControlPlaneRefusal(error)
        return {}
    return {sPath: _fsHashOrAbsent(dictHashed.get(sPath) or {})
            for sPath in setPaths}


def _fsHashOrAbsent(dictEntry):
    """Return the file's hash, "" when seen missing, None when unknown."""
    if dictEntry.get("sSha256"):
        return dictEntry["sSha256"]
    return "" if dictEntry.get("bMissing") else None


def fbFindingMatchesDismissal(dictFinding, dictDismissal, sScriptSha256):
    """True when a dismissal covers this finding at the script's current hash."""
    return (dictDismissal.get("sScannedPath") == dictFinding.get("sScannedPath")
            and dictDismissal.get("sUpstreamStepId")
            == dictFinding.get("sUpstreamStepId")
            and bool(sScriptSha256)
            and dictDismissal.get("sScriptSha256") == sScriptSha256)


def _fsWhyNotCurrent(dictFinding, dictWorkflow, dictIndexById):
    """Return why a finding's upstream declaration no longer holds, or ''.

    Its script is checked with every other script the scan read, in
    ``_fsWhyScanNotCurrent``.
    """
    sUpstreamId = dictFinding.get("sUpstreamStepId", "")
    if sUpstreamId not in dictIndexById:
        return f"step '{sUpstreamId}' no longer exists"
    dictUpstream = dictWorkflow["listSteps"][dictIndexById[sUpstreamId]]
    if dictFinding.get("sMatchedOutput") not in _flistStepDeclaredOutputs(
            dictUpstream):
        return (f"step '{sUpstreamId}' no longer declares "
                f"{dictFinding.get('sMatchedOutput')}")
    return ""


def _fsWhyScanNotCurrent(dictStep, dictScan, dictContext):
    """Return why a scan no longer describes what it examined, or ''."""
    for sScript, sSha256 in sorted(
            (dictScan.get(S_SCRIPT_HASHES_KEY) or {}).items()):
        if dictContext["dictHashes"].get(sScript) != sSha256:
            return f"{sScript} changed since the dependency scan"
    if dictScan.get(S_INPUTS_FINGERPRINT_KEY) != fsScanInputsFingerprint(
            dictContext["dictWorkflow"], dictStep):
        return ("the step's scripts, or the workflow's steps and their "
                "declared outputs, changed since the dependency scan")
    return ""


def _fdictStateForStep(iStepIndex, dictStep, dictContext):
    """Return one scanned step's dependency state."""
    dictScan = dictStep[S_SCAN_ANNOTATION_KEY]
    if dictScan.get("sState") == S_STATE_FAILED:
        return {"sState": S_DEPENDENCY_NOT_CURRENT, "listFindings": [],
                "sReason": "the last dependency scan of this step failed ("
                + (dictScan.get("sError") or "no reason recorded") + ")"}
    listFindings = list(dictScan.get("listFindings", []) or [])
    for dictFinding in listFindings:
        sWhy = _fsWhyNotCurrent(
            dictFinding, dictContext["dictWorkflow"],
            dictContext["dictIndexById"])
        if sWhy:
            return {"sState": S_DEPENDENCY_NOT_CURRENT, "listFindings": [],
                    "sReason": sWhy}
    sWhy = _fsWhyScanNotCurrent(dictStep, dictScan, dictContext)
    if sWhy:
        return {"sState": S_DEPENDENCY_NOT_CURRENT, "listFindings": [],
                "sReason": sWhy}
    setDeclared = set(dictContext["dictUpstreamByStep"].get(iStepIndex, []))
    listDismissals = dictStep.get(S_DISMISSALS_KEY, []) or []
    listUnreconciled = [
        dictFinding for dictFinding in listFindings
        if dictContext["dictIndexById"][dictFinding["sUpstreamStepId"]]
        not in setDeclared
        and not any(fbFindingMatchesDismissal(
            dictFinding, dictDismissal,
            dictContext["dictHashes"].get(dictFinding.get("sScriptPath")))
            for dictDismissal in listDismissals if isinstance(
                dictDismissal, dict))]
    if listUnreconciled:
        return {"sState": S_DEPENDENCY_UNRECONCILED,
                "listFindings": listUnreconciled, "sReason": ""}
    return {"sState": S_DEPENDENCY_RECONCILED, "listFindings": [],
            "sReason": ""}


def fdictDependencyStatesByStep(dictWorkflow, filesRepo, dictUpstreamByStep):
    """Return ``{iStepIndex: {sState, listFindings, sReason}}`` for scanned steps.

    ``dictUpstreamByStep`` is the DECLARED graph, ``{iConsumer: [iUpstream]}``
    from tokens and ``saDependencies``. A step without a scan annotation
    is absent from the answer.
    """
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    if not any(isinstance(dictStep, dict)
               and isinstance(dictStep.get(S_SCAN_ANNOTATION_KEY), dict)
               for dictStep in listSteps):
        return {}
    dictContext = {
        "dictWorkflow": dictWorkflow,
        "dictIndexById": _fdictIndexByStepId(dictWorkflow),
        "dictHashes": _fdictScriptHashesNow(dictWorkflow, filesRepo),
        "dictUpstreamByStep": dictUpstreamByStep or {},
    }
    dictStates = {}
    for iStepIndex, dictStep in enumerate(listSteps):
        if isinstance(dictStep, dict) and isinstance(
                dictStep.get(S_SCAN_ANNOTATION_KEY), dict):
            dictStates[iStepIndex] = _fdictStateForStep(
                iStepIndex, dictStep, dictContext)
    return dictStates
