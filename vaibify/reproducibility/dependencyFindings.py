"""Whether each step's scan-found dependencies are reconciled, and current.

The workflow JSON is the dependency graph: a cross-step file read counts
only when a ``{step:<id>.<stem>}`` token or ``saDependencies`` declares
it. The dependency scanner reads each step's scripts and finds reads of
another step's declared output; a finding the workflow neither declares
nor the researcher has dismissed is an edge the graph is missing, so the
step is not Self-Consistent (Level 1, ``dependency-unreconciled``).

A finding is evidence only while what it was found against still holds:
the script it was found in, byte for byte, and the upstream step's
output declaration it matched. When either has moved, or the step's last
scan failed, the step reads "dependency scan not current" -- unknown,
never a pass and never a red verdict drawn from old data. A step the hub
has not scanned carries no annotation and no claim is made about it.

The scan results ride on each step as the transient
``dictDependencyScan`` annotation (never persisted); dismissals are the
researcher's and persist in ``listDismissedDependencyFindings``, each
bound to the script hash it was made against, so a changed script asks
again.
"""

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal


__all__ = [
    "S_SCAN_ANNOTATION_KEY",
    "S_DISMISSALS_KEY",
    "S_STATE_FAILED",
    "S_STATE_SCANNED",
    "S_DEPENDENCY_UNRECONCILED",
    "S_DEPENDENCY_NOT_CURRENT",
    "fdictDependencyStatesByStep",
    "fbFindingMatchesDismissal",
]

S_SCAN_ANNOTATION_KEY = "dictDependencyScan"
S_DISMISSALS_KEY = "listDismissedDependencyFindings"
S_STATE_SCANNED = "scanned"
S_STATE_FAILED = "failed"
S_DEPENDENCY_UNRECONCILED = "unreconciled"
S_DEPENDENCY_NOT_CURRENT = "not-current"
S_DEPENDENCY_RECONCILED = "reconciled"


def _flistStepDeclaredOutputs(dictStep):
    """Return the output declarations a finding may have matched."""
    return list(dictStep.get("saOutputDataFiles", []) or []) + list(
        dictStep.get("saPlotFiles", []) or [])


def _fdictIndexByStepId(dictWorkflow):
    """Return ``{sStepId: iStepIndex}`` for steps that carry an id."""
    return {dictStep.get("sStepId"): iIndex for iIndex, dictStep in enumerate(
                (dictWorkflow or {}).get("listSteps", []) or [])
            if isinstance(dictStep, dict) and dictStep.get("sStepId")}


def _fdictScriptHashesNow(dictWorkflow, filesRepo):
    """Hash every script a finding names, in one adapter call."""
    setPaths = set()
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        dictScan = (dictStep or {}).get(S_SCAN_ANNOTATION_KEY) if isinstance(
            dictStep, dict) else None
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
    return {sPath: (dictHashed.get(sPath) or {}).get("sSha256")
            for sPath in setPaths}


def fbFindingMatchesDismissal(dictFinding, dictDismissal, sScriptSha256):
    """True when a dismissal covers this finding at the script's current hash."""
    return (dictDismissal.get("sScannedPath") == dictFinding.get("sScannedPath")
            and dictDismissal.get("sUpstreamStepId")
            == dictFinding.get("sUpstreamStepId")
            and bool(sScriptSha256)
            and dictDismissal.get("sScriptSha256") == sScriptSha256)


def _fsWhyNotCurrent(dictFinding, dictWorkflow, dictIndexById, dictHashes):
    """Return why a finding no longer describes the files, or ''."""
    sScript = dictFinding.get("sScriptPath", "")
    sHashNow = dictHashes.get(sScript)
    if not sHashNow or sHashNow != dictFinding.get("sScriptSha256"):
        return f"{sScript} changed since the dependency scan"
    sUpstreamId = dictFinding.get("sUpstreamStepId", "")
    if sUpstreamId not in dictIndexById:
        return f"step '{sUpstreamId}' no longer exists"
    dictUpstream = dictWorkflow["listSteps"][dictIndexById[sUpstreamId]]
    if dictFinding.get("sMatchedOutput") not in _flistStepDeclaredOutputs(
            dictUpstream):
        return (f"step '{sUpstreamId}' no longer declares "
                f"{dictFinding.get('sMatchedOutput')}")
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
            dictContext["dictIndexById"], dictContext["dictHashes"])
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
