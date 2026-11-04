"""How each output was produced, recorded only when it actually was.

``.vaibify/outputProvenance.json`` (schema 1) holds two maps:

* ``dictRunContexts``: one entry per distinct run context -- the Stage A
  decision (the processor's vendor, ``x86-64`` level and visible cores,
  and which settings applied), the Stage B probe, the execution profile
  and the settings it requested and applied, and the UTC time the run
  began. Keyed by a digest of everything but the time, so the same
  computer under the same settings is one entry however often it runs.
* ``dictOutputs``: per repository path, the output's SHA-256 when it was
  written, the section that wrote it, and its run context's ID.

A record is written only by the section that really ran (design I7):
the data outputs after the data section completed with every command
exiting 0, the plot outputs after the plot section likewise. Nothing
else relabels a file -- a tests-only run, "Run tests" from the
dashboard, a failed section and an interactive step all leave every
record as it was. A byte difference can then be set beside what was
recorded -- possible causes made inspectable, never proved, and never
predicted.

The file is tracked and never pinned: the shadow's own runner writes
it too, so pinning it would make every rerun change a pinned input.
The envelope copies a digest-bound subset instead (``dataArchiver``).
No CPU model string and no host name is ever recorded: these records
are published with the envelope.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone


__all__ = [
    "S_OUTPUT_PROVENANCE_REPO_PATH",
    "S_CONTEXT_UNKNOWN",
    "fdictBuildRunContext",
    "fnPrepareRunProvenance",
    "fsRunContextId",
    "fdictReadOutputProvenance",
    "fnRecordSectionOutputs",
    "fnRecordSectionProvenance",
    "fnRememberCurrentContext",
    "fdictCurrentContext",
    "flistDescribeContextDifferences",
    "fdictProductionContextFor",
    "fdictBuildEnvelopeOutputProvenance",
    "fdictBuildOutputContextNotes",
    "fdictExplainManifestMismatches",
    "fdictReportManifestMismatches",
]


S_OUTPUT_PROVENANCE_REPO_PATH = ".vaibify/outputProvenance.json"
S_CONTEXT_UNKNOWN = "production context unknown"
S_DIFFERENT_CONTEXT = (
    "Produced under different execution settings or hardware")
S_RERUN_HERE_OPTION = (
    "For each of these files, the recorded hardware or settings differ "
    "from those recorded for the copy the manifest pins. If you want this "
    "computer's results to be the published ones, you can rerun every "
    "step here and regenerate the manifest. Nothing requires it, and "
    "doing it replaces the published hashes, so the comparison with "
    "the published copies is lost.")
S_REGENERATE_CHOICE = (
    "Regenerate the envelope only for a change you intended to publish: "
    "it replaces the published hashes with this computer's, and the "
    "comparison with the published copies is lost. Rerunning on this "
    "computer may reproduce the same bytes and settles nothing.")
S_MANIFEST_CHECK_SCOPE = (
    "This check compares each file's bytes with the hash the manifest "
    "pins and runs none of the published tests. Verify Level 3 reruns "
    "the project and judges each difference against the published "
    "comparisons.")
S_MISSING_ON_DISK = "missing on disk"
S_SAME_RECORDED_CONTEXT = (
    "recorded under the same settings and hardware as the pinned copy")
S_DIFFERENCE_CAUTION = (
    "A difference in hardware or settings can change a file's last "
    "digits; it doesn't prove that's why these differ.")
S_NOT_MEASURED = "not measured"
S_NOT_MEASURED_IN_HOST_MODE = "not measured in host mode"
S_NOT_REPORTED_BY_MACHINE = "this machine does not report it"
I_OUTPUT_PROVENANCE_SCHEMA = 1
S_RUN_CONTEXT_KEY = "__dictRunContext"
S_TEMPLATE_VALUES_KEY = "__dictOutputTemplateValues"

# The section each output key belongs to, and the only ones recorded.
DICT_SECTION_OUTPUT_KEY = {"data": "saOutputDataFiles", "plot": "saPlotFiles"}

# The most recent run context on THIS hub, per container. The poll makes
# no container exec, so "this computer's current context" is the one the
# last run here observed; before any run here it is unknown, and nothing
# is said rather than guessed.
_DICT_CURRENT_CONTEXT_BY_CONTAINER = {}

# The facts a difference is named by, in the words a researcher reads.
_T_COMPARED_FACTS = (
    ("processor vendor", ("dictSettings", "dictCapabilities", "sVendor")),
    ("x86-64 level", ("dictSettings", "dictCapabilities", "sLevel")),
    ("visible cores", ("dictSettings", "dictCapabilities", "iVisibleCores")),
    ("execution profile", ("sProfileId",)),
    ("applied settings", ("dictApplied",)),
    ("NumPy version", ("dictProbe", "sNumpyVersion")),
    ("OpenBLAS kernel", ("dictProbe", "dictOpenblas", "sCoreName")),
    ("OpenBLAS threads", ("dictProbe", "dictOpenblas", "iThreads")),
)


def fdictBuildRunContext(dictSettings, dictProbe, sRecordedUtc=""):
    """Return one run's context from its Stage A decision and Stage B probe."""
    dictSettings = dict(dictSettings or {})
    return {
        "dictSettings": {
            "dictCapabilities": dict(dictSettings.get("dictCapabilities") or {}),
            "dictDecision": dict(dictSettings.get("dictDecision") or {}),
        },
        "dictProbe": dict(dictProbe or {}),
        "sProfileId": dictSettings.get("sProfileId", ""),
        "dictRequested": dict(dictSettings.get("dictRequested") or {}),
        "dictApplied": dict(dictSettings.get("dictApplied") or {}),
        "sRecordedUtc": sRecordedUtc or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }


def fnPrepareRunProvenance(sContainerId, dictWorkflow, dictVariables):
    """Fix this run's context once, before any step, and remember it here.

    Read from the Stage A decision and Stage B probe the run's prefix was
    built from, so every record names the context its section really ran
    under. The workflow's template values travel with it, so a section's
    outputs resolve to the paths the manifest pins.
    """
    from vaibify.reproducibility.manifestPaths import (
        fdictWorkflowTemplateValues,
    )
    from .determinismEnvironment import (
        S_EXECUTION_PROBE_KEY, S_EXECUTION_SETTINGS_KEY,
    )
    dictSettings = dictVariables.get(S_EXECUTION_SETTINGS_KEY)
    if not dictSettings:
        return
    dictContext = fdictBuildRunContext(
        dictSettings, dictVariables.get(S_EXECUTION_PROBE_KEY))
    dictVariables[S_RUN_CONTEXT_KEY] = dictContext
    dictVariables[S_TEMPLATE_VALUES_KEY] = fdictWorkflowTemplateValues(
        dictWorkflow)
    fnRememberCurrentContext(sContainerId, dictContext)


def fsRunContextId(dictContext):
    """Return the context's ID: a digest of everything but its time."""
    dictKeyed = {sKey: jsonValue for sKey, jsonValue in dictContext.items()
                 if sKey != "sRecordedUtc"}
    sCanonical = json.dumps(dictKeyed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()[:16]


def _fdictEmptyProvenance():
    """Return a record with no contexts and no outputs."""
    return {"iSchemaVersion": I_OUTPUT_PROVENANCE_SCHEMA,
            "dictRunContexts": {}, "dictOutputs": {}}


def fdictReadOutputProvenance(filesRepo):
    """Return the record, or an empty one when absent or unreadable."""
    from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    try:
        if not filesRepo.fbIsFile(S_OUTPUT_PROVENANCE_REPO_PATH):
            return _fdictEmptyProvenance()
        dictRecord = json.loads(filesRepo.fsReadText(
            S_OUTPUT_PROVENANCE_REPO_PATH))
    except (OSError, KeyError, ValueError) as error:
        fnReRaiseControlPlaneRefusal(error)
        return _fdictEmptyProvenance()
    if (not isinstance(dictRecord, dict)
            or not isinstance(dictRecord.get("dictRunContexts"), dict)
            or not isinstance(dictRecord.get("dictOutputs"), dict)):
        return _fdictEmptyProvenance()
    return dictRecord


def _flistSectionOutputPaths(dictStep, dictTemplateValues, sSection):
    """Return the section's declared outputs, as the manifest names them."""
    from vaibify.reproducibility.manifestPaths import (
        flistStepOutputRepoPaths,
    )
    return flistStepOutputRepoPaths(
        dictStep, dictTemplateValues, (DICT_SECTION_OUTPUT_KEY[sSection],))


def fbSectionRan(dictStep, sSection):
    """Return True iff the section has at least one command to run."""
    sCommandKey = {"data": "saDataCommands", "plot": "saPlotCommands"}[
        sSection]
    return any((sCommand or "").strip()
               for sCommand in dictStep.get(sCommandKey, []) or [])


def fnRecordSectionOutputs(filesRepo, dictStep, dictTemplateValues, sSection,
                           dictContext):
    """Record the section's outputs under ``dictContext``; prune the rest.

    An output the section did not leave behind keeps whatever record it
    had: a record describes the bytes it names, and a later reader
    compares that digest before believing it.
    """
    listPaths = _flistSectionOutputPaths(dictStep, dictTemplateValues, sSection)
    if not listPaths:
        return
    dictHashes = filesRepo.fdictHashFiles(listPaths)
    dictRecord = fdictReadOutputProvenance(filesRepo)
    sContextId = fsRunContextId(dictContext)
    bAnyRecorded = False
    for sPath in listPaths:
        sSha256 = (dictHashes.get(sPath) or {}).get("sSha256")
        if not sSha256:
            continue
        dictRecord["dictOutputs"][sPath] = {
            "sSha256": sSha256, "sSection": sSection,
            "sRunContextId": sContextId}
        bAnyRecorded = True
    if not bAnyRecorded:
        return
    # The FIRST time a context was seen is kept: rewriting it on every
    # identical rerun would change a tracked file each run, for nothing.
    dictRecord["dictRunContexts"].setdefault(sContextId, dictContext)
    setReferenced = {dictOutput.get("sRunContextId") for dictOutput in
                     dictRecord["dictOutputs"].values()}
    dictRecord["dictRunContexts"] = {
        sId: dictValue for sId, dictValue in
        dictRecord["dictRunContexts"].items() if sId in setReferenced}
    dictRecord["iSchemaVersion"] = I_OUTPUT_PROVENANCE_SCHEMA
    filesRepo.fnWriteJsonAtomic(S_OUTPUT_PROVENANCE_REPO_PATH, dictRecord)


async def fnRecordSectionProvenance(
    connectionDocker, sContainerId, dictStep, dictVariables, sSection,
):
    """Record a completed section's outputs; never stop the run doing so.

    Called by the runner only after the section really ran and every
    command exited 0; this also requires at least one command, so a
    section with nothing in it relabels nothing.
    """
    from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
    from .determinismEnvironment import _ffilesOpenProjectRepoFiles
    dictContext = dictVariables.get(S_RUN_CONTEXT_KEY)
    if not dictContext or not fbSectionRan(dictStep, sSection):
        return
    filesRepo = _ffilesOpenProjectRepoFiles(
        connectionDocker, sContainerId, dictVariables.get("sRepoRoot", ""))
    try:
        await asyncio.to_thread(
            fnRecordSectionOutputs, filesRepo, dictStep,
            dictVariables.get(S_TEMPLATE_VALUES_KEY) or {}, sSection,
            dictContext)
    except Exception as error:  # noqa: BLE001 -- a record never stops a run
        fnReRaiseControlPlaneRefusal(error)
        logging.getLogger("vaibify").warning(
            "output provenance not recorded for %s: %s",
            dictStep.get("sName", "?"), error)


def fnRememberCurrentContext(sContainerId, dictContext):
    """Keep this hub's newest run context for one container."""
    _DICT_CURRENT_CONTEXT_BY_CONTAINER[sContainerId] = dictContext


def fdictCurrentContext(sContainerId):
    """Return this hub's newest run context for a container, or None."""
    return _DICT_CURRENT_CONTEXT_BY_CONTAINER.get(sContainerId)


def _fjsonReadFact(dictContext, tPath):
    """Return the value at ``tPath`` in a context, or None."""
    jsonValue = dictContext
    for sKey in tPath:
        if not isinstance(jsonValue, dict):
            return None
        jsonValue = jsonValue.get(sKey)
    return jsonValue


def flistDescribeContextDifferences(dictRecorded, dictCurrent):
    """Return ``[{sFact, jsonThere, jsonHere}]`` for each fact that differs.

    Neutral by construction: it names what was recorded and what is
    observed here, and grades neither.
    """
    listDifferences = []
    for sFact, tPath in _T_COMPARED_FACTS:
        jsonThere = _fjsonReadFact(dictRecorded, tPath)
        jsonHere = _fjsonReadFact(dictCurrent, tPath)
        if jsonThere != jsonHere:
            listDifferences.append(
                {"sFact": sFact, "jsonThere": jsonThere, "jsonHere": jsonHere})
    return listDifferences


def _fbFactIsMeasured(jsonValue):
    return jsonValue is not None and jsonValue != ""


def _fsWhyNotMeasured(dictContext, tPath):
    """Return why a context lacks one fact, from what its record says."""
    from .determinismEnvironment import S_PROBE_NOT_RUN_IN_HOST_MODE
    if tPath[0] == "dictProbe" and _fjsonReadFact(
            dictContext, ("dictProbe", "sProbeState")) == (
            S_PROBE_NOT_RUN_IN_HOST_MODE):
        return S_NOT_MEASURED_IN_HOST_MODE
    if tPath[:2] == ("dictSettings", "dictCapabilities") and _fjsonReadFact(
            dictContext, ("dictSettings", "dictCapabilities", "bReadable")) is False:
        return S_NOT_REPORTED_BY_MACHINE
    return S_NOT_MEASURED


def _fsWordSide(dictContext, tPath, jsonValue):
    if _fbFactIsMeasured(jsonValue):
        return _fsDescribeFactValue(jsonValue)
    return _fsWhyNotMeasured(dictContext, tPath)


def _ftSplitContextDifferences(dictThere, dictHere):
    """Return ``(listObserved, listUnmeasured)``, each ``[{sFact, sThere, sHere}]``.

    Observed: both contexts measured the fact, and the values differ.
    Unmeasured: one context lacks it, worded with the reason its record
    gives. A missing value is never a difference in hardware or
    settings, because nothing was measured to differ.
    """
    listObserved, listUnmeasured = [], []
    for sFact, tPath in _T_COMPARED_FACTS:
        jsonThere = _fjsonReadFact(dictThere, tPath)
        jsonHere = _fjsonReadFact(dictHere, tPath)
        bThere, bHere = _fbFactIsMeasured(jsonThere), _fbFactIsMeasured(jsonHere)
        if bThere and bHere and jsonThere != jsonHere:
            listTarget = listObserved
        elif bThere != bHere:
            listTarget = listUnmeasured
        else:
            continue
        listTarget.append({
            "sFact": sFact, "sThere": _fsWordSide(dictThere, tPath, jsonThere),
            "sHere": _fsWordSide(dictHere, tPath, jsonHere)})
    return listObserved, listUnmeasured


def fdictProductionContextFor(dictRecord, sPath, sSha256):
    """Return the recorded context that produced these bytes, or the unknown marker.

    The record must name the very digest asked about: a record for the
    file's earlier bytes says nothing about these.
    """
    dictOutput = (dictRecord.get("dictOutputs") or {}).get(sPath) or {}
    if not sSha256 or dictOutput.get("sSha256") != sSha256:
        return {"sState": S_CONTEXT_UNKNOWN}
    dictContext = (dictRecord.get("dictRunContexts") or {}).get(
        dictOutput.get("sRunContextId"))
    if not isinstance(dictContext, dict):
        return {"sState": S_CONTEXT_UNKNOWN}
    return {"sState": "recorded", "sSection": dictOutput.get("sSection", ""),
            "sRunContextId": dictOutput.get("sRunContextId", ""),
            "dictRunContext": dictContext}


def fdictBuildEnvelopeOutputProvenance(filesRepo, dictWorkflow):
    """Return the envelope's copy: each pinned output's recorded context.

    Bound to the digest: an output whose recorded SHA-256 is not its
    current one is "production context unknown", because the record is
    about bytes that are no longer there. Each context appears once,
    referenced by ID. This changes only when an output or its record
    does, so the envelope is not rewritten by an identical rerun.
    """
    from vaibify.reproducibility.manifestPaths import (
        fdictWorkflowTemplateValues, flistStepOutputRepoPaths,
    )
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictTemplateValues = fdictWorkflowTemplateValues(dictWorkflow)
    setPaths = set()
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if isinstance(dictStep, dict):
            setPaths.update(flistStepOutputRepoPaths(
                dictStep, dictTemplateValues))
    listPaths = sorted(setPaths)
    dictHashes = filesRepo.fdictHashFiles(listPaths) if listPaths else {}
    dictRecord = fdictReadOutputProvenance(filesRepo)
    dictCopy = {"dictRunContexts": {}, "dictOutputs": {}}
    for sPath in listPaths:
        sSha256 = (dictHashes.get(sPath) or {}).get("sSha256")
        if not sSha256:
            continue
        dictFound = fdictProductionContextFor(dictRecord, sPath, sSha256)
        if dictFound["sState"] != "recorded":
            dictCopy["dictOutputs"][sPath] = {"sState": S_CONTEXT_UNKNOWN}
            continue
        dictCopy["dictRunContexts"][dictFound["sRunContextId"]] = (
            dictFound["dictRunContext"])
        dictCopy["dictOutputs"][sPath] = {
            "sState": "recorded", "sSection": dictFound["sSection"],
            "sRunContextId": dictFound["sRunContextId"]}
    return dictCopy


def _fsDescribeFactValue(jsonValue):
    """Return one recorded fact as display text."""
    if jsonValue is None or jsonValue == "":
        return "not recorded"
    if isinstance(jsonValue, dict):
        return ", ".join(f"{sKey}={sValue}" for sKey, sValue in
                         sorted(jsonValue.items())) or "none"
    return str(jsonValue)


def _flistWordDifferences(listDifferences):
    """Return the differences with their values as display text."""
    return [{"sFact": dictDifference["sFact"],
             "sThere": _fsDescribeFactValue(dictDifference["jsonThere"]),
             "sHere": _fsDescribeFactValue(dictDifference["jsonHere"])}
            for dictDifference in listDifferences]


def fdictBuildOutputContextNotes(dictWorkflow, filesPoll, dictCurrent):
    """Return ``{sStepIndex: note}`` for steps whose outputs another context made.

    A note says the step's outputs were produced under different
    execution settings or hardware, and names the facts that differ --
    never that a "different computer" made them, and never as a
    failure. Nothing is said before this hub has run anything here, or
    about an output whose record names other bytes than the file's.
    """
    from vaibify.reproducibility.manifestPaths import (
        fdictWorkflowTemplateValues, flistStepOutputRepoPaths,
    )
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    if not dictCurrent:
        return {}
    filesPoll = ffilesEnsureRepoFiles(filesPoll)
    dictRecord = fdictReadOutputProvenance(filesPoll)
    if not dictRecord["dictOutputs"]:
        return {}
    dictTemplateValues = fdictWorkflowTemplateValues(dictWorkflow)
    dictNotes = {}
    for iIndex, dictStep in enumerate(
            (dictWorkflow or {}).get("listSteps", []) or []):
        if not isinstance(dictStep, dict):
            continue
        listPaths = [sPath for sPath in flistStepOutputRepoPaths(
            dictStep, dictTemplateValues) if sPath in dictRecord["dictOutputs"]]
        dictNote = _fdictNoteForPaths(
            filesPoll, dictRecord, listPaths, dictCurrent)
        if dictNote:
            dictNotes[str(iIndex)] = dictNote
    return dictNotes


def _fdictNoteForPaths(filesRepo, dictRecord, listPaths, dictCurrent):
    """Return one step's note over its recorded outputs, or None."""
    if not listPaths:
        return None
    dictHashes = filesRepo.fdictHashFiles(listPaths)
    listDiffering, dictByFact = [], {}
    for sPath in listPaths:
        dictFound = fdictProductionContextFor(
            dictRecord, sPath, (dictHashes.get(sPath) or {}).get("sSha256"))
        if dictFound["sState"] != "recorded":
            continue
        listDifferences = flistDescribeContextDifferences(
            dictFound["dictRunContext"], dictCurrent)
        if listDifferences:
            listDiffering.append(sPath)
            for dictDifference in _flistWordDifferences(listDifferences):
                dictByFact.setdefault(dictDifference["sFact"], dictDifference)
    if not listDiffering:
        return None
    return {"sMessage": S_DIFFERENT_CONTEXT, "listPaths": listDiffering,
            "listDifferences": list(dictByFact.values())}


def _ftReadPinnedAndOnDiskRecords(filesRepo):
    """Return the envelope's provenance subset and the on-disk record."""
    from vaibify.reproducibility.environmentSnapshot import (
        fdictReadEnvironmentJson,
    )
    dictEnvelope = (fdictReadEnvironmentJson(filesRepo) or {}).get(
        "dictOutputProvenance") or {}
    return dictEnvelope, fdictReadOutputProvenance(filesRepo)


def _ftPinnedAndOnDiskContexts(dictEnvelope, dictRecord, dictMismatch):
    """Return (pinned context, on-disk context) for one mismatch, None if unrecorded."""
    sPath = dictMismatch.get("sPath", "")
    dictPinnedRef = (dictEnvelope.get("dictOutputs") or {}).get(sPath) or {}
    dictPinned = (dictEnvelope.get("dictRunContexts") or {}).get(
        dictPinnedRef.get("sRunContextId"))
    dictOnDisk = fdictProductionContextFor(
        dictRecord, sPath, dictMismatch.get("sActual") or "")
    return (dictPinned if isinstance(dictPinned, dict) else None,
            dictOnDisk.get("dictRunContext")
            if dictOnDisk["sState"] == "recorded" else None)


def _fdictExplainOneMismatch(dictEnvelope, dictRecord, dictMismatch):
    """Return one file's observed and unmeasured facts, or None if either side is unrecorded."""
    dictPinned, dictOnDisk = _ftPinnedAndOnDiskContexts(
        dictEnvelope, dictRecord, dictMismatch)
    if dictPinned is None or dictOnDisk is None:
        return None
    listObserved, listUnmeasured = _ftSplitContextDifferences(
        dictPinned, dictOnDisk)
    return {"sPath": dictMismatch.get("sPath", ""),
            "listObserved": listObserved, "listUnmeasured": listUnmeasured}


def fdictExplainManifestMismatches(filesRepo, listMismatches, iTotal):
    """Return what the records say about the manifest mismatches, and the headline.

    The envelope records the context that made the PINNED bytes, and the
    output record the one that made the bytes on disk. A fact both
    measured and found different is an observed difference, which makes
    a possible cause inspectable and proves none; a fact one side never
    measured is named as unmeasured, never as a difference. Only when
    every mismatch has an observed difference is the option to make this
    computer's results the published ones offered -- as an option,
    never as the only way forward.
    """
    dictEnvelope, dictRecord = _ftReadPinnedAndOnDiskRecords(filesRepo)
    listExplained = []
    for dictMismatch in listMismatches or []:
        dictExplained = _fdictExplainOneMismatch(
            dictEnvelope, dictRecord, dictMismatch)
        if dictExplained and (dictExplained["listObserved"]
                              or dictExplained["listUnmeasured"]):
            listExplained.append(dictExplained)
    iObserved = sum(1 for dictFile in listExplained if dictFile["listObserved"])
    bOnly = bool(listMismatches) and iObserved == len(listMismatches)
    return {"listExplained": listExplained,
            "bOnlyContextDifferences": bOnly,
            "sHeadline": _fsComposeHeadline(
                listExplained, len(listMismatches or []), iTotal),
            "sOption": S_RERUN_HERE_OPTION if bOnly else ""}


def _fsJoinFacts(listFacts):
    return "; ".join(
        f"{dictFact['sFact']}: pinned copy {dictFact['sThere']}, "
        f"this copy {dictFact['sHere']}" for dictFact in listFacts)


def _ftDescribeMismatchContext(dictEnvelope, dictRecord, dictMismatch):
    """Return (what is recorded about one differing file, an observed difference?)."""
    if dictMismatch.get("sActual") is None:
        return S_MISSING_ON_DISK, False
    dictExplained = _fdictExplainOneMismatch(
        dictEnvelope, dictRecord, dictMismatch)
    if dictExplained is None:
        return S_CONTEXT_UNKNOWN, False
    listObserved = dictExplained["listObserved"]
    listUnmeasured = dictExplained["listUnmeasured"]
    if not listObserved and not listUnmeasured:
        return S_SAME_RECORDED_CONTEXT, False
    listParts = []
    if listObserved:
        listParts.append(
            "recorded hardware or settings differ: " + _fsJoinFacts(listObserved))
    if listUnmeasured:
        listParts.append(
            "not compared, because only one computer measured them: "
            + _fsJoinFacts(listUnmeasured))
    return ". ".join(listParts), bool(listObserved)


def _fsDescribeSpeedAnswer(dictWorkflow):
    """Return the recorded "speed" answer in the question's own words, else ''."""
    from vaibify.reproducibility.determinismGate import (
        LIST_DETERMINISM_QUESTIONS, S_REPEATABILITY_ANSWER_KEY,
        S_REPEATABILITY_SPEED,
    )
    dictAnswers = (dictWorkflow or {}).get("dictDeterminism") or {}
    if dictAnswers.get(S_REPEATABILITY_ANSWER_KEY) != S_REPEATABILITY_SPEED:
        return ""
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        for dictChoice in dictQuestion.get("listChoices", ()):
            if dictChoice.get("sAnswer") == S_REPEATABILITY_SPEED:
                return (f"This project answered \"{dictChoice['sLabel']}\": "
                        f"{dictChoice['sDescription']}")
    return ""


def fdictReportManifestMismatches(filesRepo, listMismatches, dictWorkflow):
    """Return every differing file with what is recorded about it, and what follows.

    The manifest check compares bytes and nothing else, so its report
    names every differing file with its own observed and unmeasured
    facts (or "production context unknown"), says that a recorded
    difference proves no cause, says what the check cannot judge and
    which verification can, names a "speed" determinism answer, and
    offers regenerating the envelope only as a choice with its cost
    stated. Empty when nothing differs.
    """
    if not listMismatches:
        return {}
    dictEnvelope, dictRecord = _ftReadPinnedAndOnDiskRecords(filesRepo)
    listFiles, listObserved = [], []
    for dictMismatch in listMismatches:
        sDescription, bObserved = _ftDescribeMismatchContext(
            dictEnvelope, dictRecord, dictMismatch)
        listFiles.append({"sPath": dictMismatch.get("sPath", ""),
                          "sDescription": sDescription})
        listObserved.append(bObserved)
    return {"listFiles": listFiles,
            "sCaution": S_DIFFERENCE_CAUTION if any(listObserved) else "",
            "sScope": S_MANIFEST_CHECK_SCOPE,
            "sDeterminismNote": _fsDescribeSpeedAnswer(dictWorkflow),
            "sRegenerateChoice": (S_RERUN_HERE_OPTION if all(listObserved)
                                  else S_REGENERATE_CHOICE)}


def _fsComposeHeadline(listExplained, iDiffering, iTotal):
    """Return the check's one sentence: a count, and what the records show, claiming no cause."""
    if not iDiffering:
        return ""
    if iDiffering == 1:
        return _fsComposeOneFileHeadline(listExplained, iTotal)
    sHeadline = f"{iDiffering} of {iTotal} files differ from the manifest."
    iObserved = sum(1 for dictFile in listExplained if dictFile["listObserved"])
    iUnmeasured = sum(1 for dictFile in listExplained if dictFile["listUnmeasured"])
    if iObserved == iDiffering:
        sHeadline += (f" All {iDiffering} were made with different recorded "
                      "hardware or settings.")
    elif iObserved:
        sHeadline += (f" {iObserved} of them {'was' if iObserved == 1 else 'were'}"
                      " made with different recorded hardware or settings.")
    if iUnmeasured:
        sHeadline += (f" For {iUnmeasured} of them, some hardware or settings "
                      "weren't measured, so they can't be compared.")
    return sHeadline


def _fsComposeOneFileHeadline(listExplained, iTotal):
    sHeadline = f"1 of {iTotal} files differs from the manifest."
    dictFile = listExplained[0] if listExplained else {}
    if dictFile.get("listObserved"):
        sHeadline += " It was made with different recorded hardware or settings."
    if dictFile.get("listUnmeasured"):
        sHeadline += (" Some of its hardware or settings weren't measured, so "
                      "they can't be compared.")
    return sHeadline
