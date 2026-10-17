"""The Level 3 rerun's procedure: what runs in the container around the steps.

This module and one frozen comparison policy are concatenated, verbatim,
into the single file ``rerunAssessment.fsRenderRerunAssessmentScript``
renders: the project's pinned ``.vaibify/rerunAssessment.py``, which a
reader's ``reproduce.sh`` runs, and the copy the dashboard writes into a
shadow's scratch directory. A reader's script cannot import vaibify, so
the only way the two reproduction lanes run the same procedure is to run
the same text. That is why this module is a LEAF, and why
``tests/testRerunAssessmentStructure.py`` pins four things about it: it
imports only the standard library (NumPy is imported inside the probe,
because the probe must see what the steps see); it has no
``from __future__`` import and no ``if __name__`` block, because the
renderer appends its own entry point; no top-level name it defines is
also defined by a policy module; and it parses as Python 3.8, because the
interpreter that runs it is the one in the author's image.

Unlike a policy module, this one is CURRENT and may change in any
release. What it hands a policy -- ``dictObservations`` and the plan --
is that policy's contract, so its shape holds for every policy vaibify
still ships.

Every sub-command names the repository with ``--repo``: the shadow runs
the file from its scratch directory, the script from ``/work``.

* ``prepare`` hashes the pinned inputs against ``MANIFEST.sha256`` and
  sets aside every published raster figure whose bytes match their
  manifest hash, before anything is deleted (exit 3 when an input
  changed).
* ``clear`` deletes what the rerun regenerates. Every target is
  validated before any is deleted, symlinks are resolved against this
  container's own filesystem, and nothing outside the repository or
  overlapping a protected path is touched (exit 4 on a refusal).
* ``capabilities`` reads the processor in a process that has loaded no
  numerical library and decides which requested settings apply
  (Stage A); ``probe`` then loads NumPy under exactly those settings and
  records what it finds (Stage B). Neither changes a setting.
* ``assess`` hashes again and hands the observations to the policy,
  which grades them; it prints the verdict and the tally and exits with
  the policy's code.

Every exit path ends in 0, 1, 3, 4 or 5; anything unexpected is
"Could not run" (4). Exit 2 is never used.
"""

import argparse
import ctypes
import glob
import hashlib
import json
import os
import re
import shutil
import sys


__all__ = [
    "S_ASSESSMENT_RESULT_PREFIX",
    "S_CLEARING_RESULT_PREFIX",
    "S_EXECUTION_SETTING_VALUE_PATTERN",
    "S_INVOCATION_LEDGER_FILENAME",
    "TUPLE_EXECUTION_SETTING_KEYS",
    "TUPLE_INVOCATION_SECTIONS",
    "TUPLE_X86_64_LEVELS",
    "fdictCheckPinnedInputs",
    "fdictClearRegeneratedPaths",
    "fdictDecideAppliedSettings",
    "fdictJudgeNumpyDisabledFeatures",
    "fdictProbeNumericalLibraries",
    "fdictReadProcessorCapabilities",
    "fiRunMain",
    "flistDescribeProbeConcerns",
    "flistParseManifestEntries",
    "fsBuildInvocationId",
    "fsHashRepositoryFileOrNone",
    "fsReadX86Level",
]


S_CLEARING_RESULT_PREFIX = "VAIBIFY_RERUN_PREPARATION "
S_ASSESSMENT_RESULT_PREFIX = "VAIBIFY_RERUN_ASSESSMENT "
S_CAPABILITIES_RESULT_PREFIX = "VAIBIFY_RERUN_CAPABILITIES "
S_PROBE_RESULT_PREFIX = "VAIBIFY_RERUN_PROBE "
S_PROCEDURE_MANIFEST_FILENAME = "MANIFEST.sha256"
S_INVOCATION_LEDGER_FILENAME = "invocations.tsv"
S_PREPARE_RECORD_FILENAME = "prepare.json"
S_CAPABILITIES_RECORD_FILENAME = "capabilities.json"
S_PROBE_RECORD_FILENAME = "probe.json"
S_ASSESSMENT_RECORD_FILENAME = "assessment.json"
S_REFERENCE_DIRECTORY_NAME = "references"
S_POLICY_ENTRY_POINT = "fdictAssessRerunEvidence"

I_PROCEDURE_EXIT_PASSED = 0
I_PROCEDURE_EXIT_INPUTS_CHANGED = 3
I_PROCEDURE_EXIT_COULD_NOT_RUN = 4

TUPLE_INVOCATION_SECTIONS = (
    "data", "plot", "integrity", "qualitative", "quantitative", "legacy",
)
_RE_STEP_LABEL = re.compile(r"^[A-Z][0-9]{2,}$")

# The settings an execution profile may request, and the only values it
# may give them. Rendered into reproduce.sh's validation and enforced
# host-side from this one tuple (executionProfiles imports it).
TUPLE_EXECUTION_SETTING_KEYS = (
    "OPENBLAS_CORETYPE",
    "NPY_DISABLE_CPU_FEATURES",
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "GLIBC_TUNABLES",
)
S_EXECUTION_SETTING_VALUE_PATTERN = r"^[A-Za-z0-9_.,=-]{0,64}$"
TUPLE_X86_64_LEVELS = ("x86-64-v2", "x86-64-v3", "x86-64-v4")

# The x86-64 psABI micro-architecture levels, as Linux spells their
# instructions in /proc/cpuinfo. Each level requires every one before it.
TUPLE_X86_64_LEVEL_FLAGS = (
    ("x86-64-v1", ("cmov", "cx8", "fpu", "fxsr", "mmx", "sse", "sse2")),
    ("x86-64-v2", (
        "cx16", "lahf_lm", "popcnt", "pni", "sse4_1", "sse4_2", "ssse3",
    )),
    ("x86-64-v3", (
        "avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe",
        "xsave",
    )),
    ("x86-64-v4", (
        "avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl",
    )),
)
S_FLOOR_NOT_REQUESTED = "not requested"
S_FLOOR_APPLIED = "applied"
S_FLOOR_NOT_APPLIED = "not applied"
# What became of each CPU feature NumPy was asked to disable (design F7).
S_FEATURE_APPLIED = "applied"
S_FEATURE_NOT_APPLIED = "not applied"
S_FEATURE_FIXED_AT_BUILD = "fixed at build"
S_FEATURE_NOT_CONFIRMED = "not confirmed"
S_NUMPY_FEATURE_SETTING = "NPY_DISABLE_CPU_FEATURES"
I_MAXIMUM_REFUSAL_CHARACTERS = 400

_T_OPENBLAS_CORENAME_SYMBOLS = (
    "scipy_openblas_get_corename64_",
    "openblas_get_corename64_",
    "openblas_get_corename",
)
_T_OPENBLAS_THREAD_SYMBOLS = (
    "scipy_openblas_get_num_threads64_",
    "openblas_get_num_threads64_",
    "openblas_get_num_threads",
)
_I_COPY_CHUNK_BYTES = 1 << 16


# ----------------------------------------------------------------------
# Invocation identity
# ----------------------------------------------------------------------


def fsBuildInvocationId(sStepLabel, sSection, iPosition):
    """Return ``<step label>.<section>.<1-based position>``, validated.

    The ONE builder of an invocation's identity. The runner, the plan and
    ``reproduce.sh`` all name a command through it, so a report can never
    be filed under one name and looked for under another.
    """
    if not isinstance(sStepLabel, str) or not _RE_STEP_LABEL.match(
            sStepLabel):
        raise ValueError(f"invalid step label {sStepLabel!r}")
    if sSection not in TUPLE_INVOCATION_SECTIONS:
        raise ValueError(f"invalid invocation section {sSection!r}")
    if (not isinstance(iPosition, int) or isinstance(iPosition, bool)
            or iPosition < 1):
        raise ValueError(f"invalid invocation position {iPosition!r}")
    return f"{sStepLabel}.{sSection}.{iPosition}"


# ----------------------------------------------------------------------
# Reading repository files the way the dashboard's typed hash read does
# ----------------------------------------------------------------------


def fsResolveWithinRepository(sRepoRoot, sRelativePath):
    """Return the real path of a repo-relative file, or "" if it escapes."""
    if not sRelativePath or os.path.isabs(sRelativePath):
        return ""
    sRootReal = os.path.realpath(sRepoRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRelativePath))
    if sReal == sRootReal or not sReal.startswith(sRootReal + os.sep):
        return ""
    return sReal


def fsHashRepositoryFileOrNone(sRepoRoot, sRelativePath):
    """Return the file's SHA-256, or None when it is absent or unsafe to read.

    The semantics of the dashboard's typed hash read, preserved so the
    host's cross-check compares like with like: an absolute path, or one
    whose real path leaves the repository, has no hash, and the content
    is opened with ``O_NOFOLLOW`` on the resolved path, so the file
    hashed is the file checked.
    """
    sReal = fsResolveWithinRepository(sRepoRoot, sRelativePath)
    if not sReal:
        return None
    hasherFile = hashlib.sha256()
    try:
        iDescriptor = os.open(
            sReal, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(iDescriptor, "rb") as fileIn:
            for baChunk in iter(
                    lambda: fileIn.read(_I_COPY_CHUNK_BYTES), b""):
                hasherFile.update(baChunk)
    except OSError:
        return None
    return hasherFile.hexdigest()


def fbCopyFileIfHashMatches(sRepoRoot, sRelativePath, sExpected, sTarget):
    """Copy the file to ``sTarget`` iff the copied bytes hash to sExpected.

    Hashing the bytes as they are copied, rather than hashing and then
    copying, is what makes the reference the bytes the manifest pins.
    Returns True when the copy was kept.
    """
    sReal = fsResolveWithinRepository(sRepoRoot, sRelativePath)
    if not sReal:
        return False
    hasherFile = hashlib.sha256()
    try:
        iDescriptor = os.open(
            sReal, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(iDescriptor, "rb") as fileIn, \
                open(sTarget, "xb") as fileOut:
            for baChunk in iter(
                    lambda: fileIn.read(_I_COPY_CHUNK_BYTES), b""):
                hasherFile.update(baChunk)
                fileOut.write(baChunk)
    except OSError:
        if os.path.isfile(sTarget):
            os.remove(sTarget)
        return False
    if hasherFile.hexdigest() == sExpected:
        return True
    os.remove(sTarget)
    return False


def fnWriteJsonRecord(sPath, jsonValue):
    """Write one record into a vaibify-created scratch directory."""
    with open(sPath, "w", encoding="utf-8") as fileOut:
        json.dump(jsonValue, fileOut, sort_keys=True)


def fjsonReadJsonRecordOrNone(sPath):
    """Return a scratch record's decoded value, or None when unreadable."""
    try:
        with open(sPath, encoding="utf-8") as fileIn:
            return json.load(fileIn)
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------------
# Integrity: the pinned inputs against MANIFEST.sha256
# ----------------------------------------------------------------------


def _fsUnescapeManifestEntryPath(sEscaped):
    """Reverse GNU sha256sum's escaping of a backslash and a newline."""
    listChars = []
    iIndex = 0
    while iIndex < len(sEscaped):
        sChar = sEscaped[iIndex]
        sNext = sEscaped[iIndex + 1] if iIndex + 1 < len(sEscaped) else ""
        if sChar == "\\" and sNext in ("n", "\\"):
            listChars.append("\n" if sNext == "n" else "\\")
            iIndex += 2
            continue
        listChars.append(sChar)
        iIndex += 1
    return "".join(listChars)


def flistParseManifestEntries(sManifestText):
    """Return ``[{sPath, sExpected}]`` from manifest text, in order.

    A deliberate copy of ``manifestWriter.flistParseManifestText``: this
    file runs where vaibify cannot be imported.
    ``tests/testRerunAssessmentStructure.py`` holds the two parsers to
    the same answers. Raises ``ValueError`` on a malformed line.
    """
    listEntries = []
    for iLineNumber, sLine in enumerate(
            sManifestText.splitlines(True), start=1):
        sStripped = sLine.rstrip("\n")
        if not sStripped or sStripped.startswith("#"):
            continue
        bEscaped = sStripped.startswith("\\")
        sBody = sStripped[1:] if bEscaped else sStripped
        iSplit = sBody.find("  ")
        if iSplit < 0:
            raise ValueError(
                f"malformed manifest line {iLineNumber}: {sLine!r}")
        sStoredPath = sBody[iSplit + 2:]
        listEntries.append({
            "sPath": (_fsUnescapeManifestEntryPath(sStoredPath)
                      if bEscaped else sStoredPath),
            "sExpected": sBody[:iSplit],
        })
    return listEntries


def flistSelectPinnedInputs(listEntryPaths, dictPlan):
    """Return the manifest paths the rerun must find UNCHANGED.

    Every pinned file that is neither regenerated by the rerun nor
    carried as a human step's output: scripts, tests, standards and
    their tolerances, input data, and the environment records.
    """
    setRegenerated = set(dictPlan["dictClearingJob"]["listProduced"])
    setCarried = set(dictPlan.get("listCarriedPaths") or [])
    return [
        sPath for sPath in listEntryPaths
        if sPath not in setRegenerated and sPath not in setCarried
    ]


def fdictReadManifestExpectedHashes(sRepoRoot):
    """Return ``{sPath: sExpected}`` from MANIFEST.sha256, or None."""
    sReal = fsResolveWithinRepository(sRepoRoot, S_PROCEDURE_MANIFEST_FILENAME)
    if not sReal:
        return None
    try:
        with open(sReal, encoding="utf-8") as fileIn:
            listEntries = flistParseManifestEntries(fileIn.read())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return {
        dictEntry["sPath"]: dictEntry["sExpected"] for dictEntry in listEntries
    }


def fdictCheckPinnedInputs(sRepoRoot, dictPlan, dictFrozenExpected=None):
    """Return the integrity half of the assessment, observed now.

    ``{bManifestReadable, sManifestSha256, dictExpectedHashes,
    listChangedInputs, listMissingInputs}``. Judged against
    ``dictFrozenExpected`` when given -- the hashes ``prepare`` froze --
    so a step that rewrites the manifest cannot move the expected side.
    An unreadable manifest is reported as such, never as an empty
    expectation.
    """
    dictResult = {
        "bManifestReadable": False,
        "sManifestSha256": fsHashRepositoryFileOrNone(
            sRepoRoot, S_PROCEDURE_MANIFEST_FILENAME),
        "dictExpectedHashes": {}, "listChangedInputs": [],
        "listMissingInputs": [],
    }
    dictExpected = dictFrozenExpected
    if dictExpected is None:
        dictExpected = fdictReadManifestExpectedHashes(sRepoRoot)
    if dictExpected is None:
        return dictResult
    dictResult["bManifestReadable"] = True
    dictResult["dictExpectedHashes"] = dictExpected
    for sPath in flistSelectPinnedInputs(list(dictExpected), dictPlan):
        sObserved = fsHashRepositoryFileOrNone(sRepoRoot, sPath)
        if sObserved is None:
            dictResult["listMissingInputs"].append(sPath)
        elif sObserved != dictExpected[sPath]:
            dictResult["listChangedInputs"].append(sPath)
    return dictResult


# ----------------------------------------------------------------------
# prepare: integrity before the run, and the published figures set aside
# ----------------------------------------------------------------------


def fdictCaptureReferences(sRepoRoot, sScratch, dictPlan, dictExpected):
    """Set aside each published raster figure whose bytes match the manifest.

    Read before anything is deleted, and only bytes: nothing is decoded
    here. A figure whose bytes already differ from its manifest hash is
    not set aside, so the image check can never compare a rerun with a
    file the manifest does not vouch for. The scratch directory is
    vaibify's, created fresh by the lane; it is never created here, and
    a reference directory already in it is a stale run, refused.
    """
    sDirectory = os.path.join(sScratch, S_REFERENCE_DIRECTORY_NAME)
    os.mkdir(sDirectory)
    dictReferences = {}
    for dictStep in dictPlan["listSteps"]:
        for dictOutput in dictStep["listOutputs"]:
            sPath = dictOutput["sPath"]
            if (dictOutput["sKind"] != "raster" or sPath not in dictExpected
                    or sPath in dictReferences):
                continue
            sTarget = os.path.join(
                sDirectory, f"{len(dictReferences):05d}"
                + os.path.splitext(sPath)[1].lower())
            if fbCopyFileIfHashMatches(
                    sRepoRoot, sPath, dictExpected[sPath], sTarget):
                dictReferences[sPath] = sTarget
    return dictReferences


def fiRunPrepare(sRepoRoot, sScratch, dictPlan):
    """Record integrity before the run and set the figures aside."""
    dictIntegrity = fdictCheckPinnedInputs(sRepoRoot, dictPlan)
    if not dictIntegrity["bManifestReadable"]:
        print("Could not run: MANIFEST.sha256 missing or unreadable",
              file=sys.stderr)
        return I_PROCEDURE_EXIT_COULD_NOT_RUN
    dictReferences = fdictCaptureReferences(
        sRepoRoot, sScratch, dictPlan, dictIntegrity["dictExpectedHashes"])
    dictIntegrity["dictReferencePaths"] = dictReferences
    fnWriteJsonRecord(
        os.path.join(sScratch, S_PREPARE_RECORD_FILENAME), dictIntegrity)
    listChanged = (dictIntegrity["listChangedInputs"]
                   + dictIntegrity["listMissingInputs"])
    if listChanged:
        print("Published inputs differ from MANIFEST.sha256: "
              + ", ".join(listChanged), file=sys.stderr)
        return I_PROCEDURE_EXIT_INPUTS_CHANGED
    return I_PROCEDURE_EXIT_PASSED


# ----------------------------------------------------------------------
# clear: delete what the rerun regenerates
# ----------------------------------------------------------------------


def fsLocateClearingTarget(sRootReal, sRelative):
    """Resolve a target's parent against THIS filesystem, keeping its name.

    The final component is not resolved, so a target that is itself a
    symlink is removed as a link and never followed.
    """
    sJoined = os.path.join(sRootReal, sRelative)
    return os.path.join(
        os.path.realpath(os.path.dirname(sJoined)), os.path.basename(sJoined))


def fbPathIsWithin(sInner, sOuter):
    """Return True when sInner is sOuter or lies beneath it."""
    return sInner == sOuter or sInner.startswith(sOuter + os.sep)


def flistFindClearingRefusals(sKind, sRelative, sLocated, sRootReal,
                              dictProtectedNames):
    """Return why deleting this target could reach a file it must not."""
    if sLocated == sRootReal or not fbPathIsWithin(sLocated, sRootReal):
        return [f"{sKind} {sRelative!r} is not inside the repository"]
    listRefusals = []
    for sProtected, sName in dictProtectedNames.items():
        if fbPathIsWithin(sProtected, sLocated):
            listRefusals.append(
                f"{sKind} {sRelative!r} is or contains the protected "
                f"file {sName!r}")
        elif fbPathIsWithin(sLocated, sProtected):
            listRefusals.append(
                f"{sKind} {sRelative!r} lies inside the protected path "
                f"{sName!r}")
    return listRefusals


def fbRemoveClearingTarget(sLocated):
    """Remove one validated target; return False when nothing was there."""
    if os.path.islink(sLocated) or os.path.isfile(sLocated):
        os.remove(sLocated)
    elif os.path.isdir(sLocated):
        shutil.rmtree(sLocated)
    else:
        return False
    return True


def fdictClearRegeneratedPaths(sRepoRoot, dictJob):
    """Validate every target, then delete; return what was removed.

    ``{listRefusals, listDeletedOutputs, listClearedScratch}``. When any
    target is refused, nothing is deleted at all.
    """
    sRootReal = os.path.realpath(sRepoRoot)
    dictProtectedNames = {
        fsLocateClearingTarget(sRootReal, sPath): sPath
        for sPath in dictJob["listProtected"]
    }
    listTargets = [
        ("output", sPath) for sPath in dictJob["listProduced"]
    ] + [("scratch directory", sPath) for sPath in dictJob["listScratch"]]
    dictResult = {
        "listRefusals": [], "listDeletedOutputs": [],
        "listClearedScratch": [],
    }
    for sKind, sRelative in listTargets:
        dictResult["listRefusals"].extend(flistFindClearingRefusals(
            sKind, sRelative, fsLocateClearingTarget(sRootReal, sRelative),
            sRootReal, dictProtectedNames))
    if dictResult["listRefusals"]:
        return dictResult
    for sKind, sRelative in listTargets:
        if fbRemoveClearingTarget(
                fsLocateClearingTarget(sRootReal, sRelative)):
            sKey = ("listDeletedOutputs" if sKind == "output"
                    else "listClearedScratch")
            dictResult[sKey].append(sRelative)
    return dictResult


def fiRunClear(sRepoRoot, sJobPath, dictPlan):
    """Clear with the plan's job, or with a job file the shadow wrote."""
    dictJob = dictPlan.get("dictClearingJob")
    if sJobPath:
        with open(sJobPath, encoding="utf-8") as fileJob:
            dictJob = json.load(fileJob)
        os.remove(sJobPath)
    dictResult = fdictClearRegeneratedPaths(sRepoRoot, dictJob)
    print(S_CLEARING_RESULT_PREFIX + json.dumps(dictResult))
    if dictResult["listRefusals"]:
        print("Could not run: the rerun was refused before any file was "
              "deleted: " + "; ".join(dictResult["listRefusals"]),
              file=sys.stderr)
        return I_PROCEDURE_EXIT_COULD_NOT_RUN
    return I_PROCEDURE_EXIT_PASSED


# ----------------------------------------------------------------------
# Stage A: the processor's capabilities, read before any library loads
# ----------------------------------------------------------------------


def fsReadX86Level(setFlags):
    """Return the highest x86-64 level every flag of which is present."""
    sLevel = ""
    for sCandidate, tFlags in TUPLE_X86_64_LEVEL_FLAGS:
        if not all(sFlag in setFlags for sFlag in tFlags):
            break
        sLevel = sCandidate
    return sLevel


def fiCountVisibleCores():
    """Return how many cores this process may run on."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 0


def fdictReadProcessorCapabilities(sCpuinfoPath="/proc/cpuinfo"):
    """Return vendor, x86-64 level and visible cores; never a model name.

    Only what explains a difference is kept. The model string and the
    host name identify a machine, and these records are published.
    """
    dictCapabilities = {
        "sArchitecture": os.uname().machine, "sVendor": "", "sLevel": "",
        "iVisibleCores": fiCountVisibleCores(), "bReadable": False,
    }
    try:
        with open(sCpuinfoPath, encoding="utf-8") as fileIn:
            listLines = fileIn.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return dictCapabilities
    setFlags = set()
    for sLine in listLines:
        sKey, _, sValue = sLine.partition(":")
        if sKey.strip() == "vendor_id" and not dictCapabilities["sVendor"]:
            dictCapabilities["sVendor"] = sValue.strip()
        if sKey.strip() == "flags" and not setFlags:
            setFlags = set(sValue.split())
    dictCapabilities["bReadable"] = bool(setFlags)
    dictCapabilities["sLevel"] = fsReadX86Level(setFlags)
    return dictCapabilities


def fdictDecideAppliedSettings(dictCapabilities, sRequiredLevel,
                               listFloorKeys):
    """Stage A's decision: which of the requested settings apply here.

    A processor below the profile's required level cannot honor its
    instruction floor, so the floor keys are dropped and the thread pins
    kept. A processor whose capabilities could not be read is treated the
    same way: the floor is not applied, and the reason says why.
    """
    listFloorKeys = list(listFloorKeys or [])
    if not sRequiredLevel or not listFloorKeys:
        return {"sFloorState": S_FLOOR_NOT_REQUESTED, "listDroppedKeys": [],
                "sReason": ""}
    sLevel = dictCapabilities.get("sLevel") or ""
    if not dictCapabilities.get("bReadable"):
        sReason = "floor not applied: capabilities unreadable on this host"
    elif (sLevel in TUPLE_X86_64_LEVELS
          and TUPLE_X86_64_LEVELS.index(sLevel)
          >= TUPLE_X86_64_LEVELS.index(sRequiredLevel)):
        return {"sFloorState": S_FLOOR_APPLIED, "listDroppedKeys": [],
                "sReason": ""}
    else:
        sReason = f"floor not applied: this processor lacks {sRequiredLevel}"
    return {"sFloorState": S_FLOOR_NOT_APPLIED,
            "listDroppedKeys": sorted(listFloorKeys), "sReason": sReason}


def flistValidateFloorRequest(sRequiredLevel, listFloorKeys):
    """Return the problems with a requested floor; empty when it is valid."""
    listProblems = []
    if sRequiredLevel and sRequiredLevel not in TUPLE_X86_64_LEVELS:
        listProblems.append(f"unknown required level {sRequiredLevel!r}")
    for sKey in listFloorKeys:
        if sKey not in TUPLE_EXECUTION_SETTING_KEYS:
            listProblems.append(f"floor key {sKey!r} is not allowed")
    return listProblems


def fiRunCapabilities(sScratch, sRequiredLevel, sFloorKeys, bMachine=False):
    """Record Stage A and print one key to unset per line.

    With ``bMachine`` the record follows on one prefixed line, for a lane
    that reads it from the output rather than from a scratch directory.
    """
    listFloorKeys = [sKey for sKey in (sFloorKeys or "").split(",") if sKey]
    listProblems = flistValidateFloorRequest(sRequiredLevel, listFloorKeys)
    if listProblems:
        print("Could not run: " + "; ".join(listProblems), file=sys.stderr)
        return I_PROCEDURE_EXIT_COULD_NOT_RUN
    dictCapabilities = fdictReadProcessorCapabilities()
    dictDecision = fdictDecideAppliedSettings(
        dictCapabilities, sRequiredLevel, listFloorKeys)
    dictRecord = {
        "dictCapabilities": dictCapabilities, "dictDecision": dictDecision,
        "sRequiredLevel": sRequiredLevel or "",
        "listFloorKeys": listFloorKeys,
        "listSettingsPresent": sorted(
            sKey for sKey in TUPLE_EXECUTION_SETTING_KEYS
            if sKey in os.environ),
    }
    if sScratch:
        fnWriteJsonRecord(
            os.path.join(sScratch, S_CAPABILITIES_RECORD_FILENAME), dictRecord)
    if dictDecision["sReason"]:
        print("vaibify: " + dictDecision["sReason"], file=sys.stderr)
    for sKey in dictDecision["listDroppedKeys"]:
        print(sKey)
    if bMachine:
        print(S_CAPABILITIES_RESULT_PREFIX
              + json.dumps(dictRecord, sort_keys=True))
    return I_PROCEDURE_EXIT_PASSED


# ----------------------------------------------------------------------
# Stage B: what NumPy and its OpenBLAS report under the applied settings
# ----------------------------------------------------------------------


def ffnLookUpFirstSymbol(sLibraryPath, tSymbols):
    """Return the first exported function of tSymbols, or None.

    The library is the one NumPy already loaded; opening it again by
    path returns the same instance, so what it reports is what NumPy
    uses.
    """
    for sSymbol in tSymbols:
        try:
            fgenericSymbol = getattr(ctypes.CDLL(sLibraryPath), sSymbol)
        except (AttributeError, OSError):
            continue
        fgenericSymbol.argtypes = []
        return fgenericSymbol
    return None


def fdictProbeOpenblas(moduleNumpy):
    """Return the bundled OpenBLAS's core name and thread count."""
    sPackages = os.path.dirname(os.path.dirname(moduleNumpy.__file__))
    listLibraries = sorted(glob.glob(
        os.path.join(sPackages, "numpy.libs", "*openblas*")))
    if not listLibraries:
        return {"sState": "OpenBLAS not detected",
                "sReportedBlas": fsReadNumpyBlasName(moduleNumpy)}
    sLibrary = listLibraries[0]
    fbaReadCorename = ffnLookUpFirstSymbol(
        sLibrary, _T_OPENBLAS_CORENAME_SYMBOLS)
    if fbaReadCorename is not None:
        fbaReadCorename.restype = ctypes.c_char_p
    fiReadThreads = ffnLookUpFirstSymbol(sLibrary, _T_OPENBLAS_THREAD_SYMBOLS)
    if fiReadThreads is not None:
        fiReadThreads.restype = ctypes.c_int
    return {
        "sState": "detected",
        "sLibrary": os.path.basename(sLibrary),
        "sCoreName": (fbaReadCorename().decode("ascii", "replace").strip()
                      if fbaReadCorename else "not readable"),
        "iThreads": fiReadThreads() if fiReadThreads else None,
    }


def fsReadNumpyBlasName(moduleNumpy):
    """Return the BLAS NumPy says it was built against, or ""."""
    try:
        return str(moduleNumpy.__config__.CONFIG[
            "Build Dependencies"]["blas"]["name"])
    except (AttributeError, KeyError, TypeError):
        return ""


def fdictReadNumpyCpuFeatures():
    """Return NumPy's active, baseline and dispatched CPU features.

    NumPy 2 keeps them in ``numpy._core``; NumPy 1 in ``numpy.core``.
    """
    try:
        from numpy._core import _multiarray_umath
    except ImportError:
        try:
            from numpy.core import _multiarray_umath
        except ImportError:
            return {"sState": "not readable"}
    dictFeatures = getattr(_multiarray_umath, "__cpu_features__", None)
    if not isinstance(dictFeatures, dict):
        return {"sState": "not readable"}
    return {
        "listActive": sorted(
            sName for sName, bOn in dictFeatures.items() if bOn),
        "listBaseline": list(
            getattr(_multiarray_umath, "__cpu_baseline__", []) or []),
        "listDispatch": list(
            getattr(_multiarray_umath, "__cpu_dispatch__", []) or []),
    }


def fdictJudgeNumpyDisabledFeatures(sRequested, dictCpuFeatures):
    """Return what became of each CPU feature NumPy was asked to disable.

    A name outside this NumPy's dispatch list is one its build does not
    know: NumPy ignores it with an ImportWarning that Python hides by
    default, so the request silently does nothing. NumPy before 2.4 does
    not know ``X86_V4``, and keeps dispatching AVX-512 under it.
    """
    listNames = [sName for sName in (sRequested or "").split(",") if sName]
    if "listDispatch" not in dictCpuFeatures:
        return {sName: {"sStatus": S_FEATURE_NOT_CONFIRMED,
                        "sReason": "NumPy's CPU features are not readable"}
                for sName in listNames}
    setBaseline = set(dictCpuFeatures["listBaseline"])
    setDispatch = set(dictCpuFeatures["listDispatch"])
    setActive = set(dictCpuFeatures["listActive"])
    dictJudged = {}
    for sName in listNames:
        if sName in setBaseline:
            dictJudged[sName] = {"sStatus": S_FEATURE_FIXED_AT_BUILD,
                                 "sReason": "it is in this NumPy's baseline"}
        elif sName not in setDispatch:
            dictJudged[sName] = {
                "sStatus": S_FEATURE_NOT_APPLIED,
                "sReason": "this NumPy does not know that feature name"}
        elif sName in setActive:
            dictJudged[sName] = {"sStatus": S_FEATURE_NOT_APPLIED,
                                 "sReason": "NumPy still reports it active"}
        else:
            dictJudged[sName] = {"sStatus": S_FEATURE_APPLIED, "sReason": ""}
    return dictJudged


def fdictProbeNumericalLibraries():
    """Load NumPy under the applied settings and report what it chose.

    Observations only: the probe never changes a setting, and what it
    sees is what a probe process saw, not what every step did. NumPy
    refuses to import at all when asked to disable a feature in its
    baseline, and that refusal is recorded in NumPy's own words.
    """
    dictProbe = {
        "dictSettingsSeen": {
            sKey: os.environ[sKey] for sKey in TUPLE_EXECUTION_SETTING_KEYS
            if sKey in os.environ},
    }
    try:
        import numpy
    except ImportError:
        dictProbe["sNumpyState"] = "NumPy not installed"
        return dictProbe
    except Exception as error:  # noqa: BLE001 -- recorded, never raised
        dictProbe["sNumpyState"] = (
            "NumPy refused to load under these settings: "
            + " ".join(str(error).split())[:I_MAXIMUM_REFUSAL_CHARACTERS])
        return dictProbe
    dictProbe["sNumpyState"] = "installed"
    dictProbe["sNumpyVersion"] = str(getattr(numpy, "__version__", ""))
    dictProbe["dictNumpyCpuFeatures"] = fdictReadNumpyCpuFeatures()
    dictProbe["dictNumpyDisabledFeatures"] = fdictJudgeNumpyDisabledFeatures(
        dictProbe["dictSettingsSeen"].get(S_NUMPY_FEATURE_SETTING, ""),
        dictProbe["dictNumpyCpuFeatures"])
    dictProbe["dictOpenblas"] = fdictProbeOpenblas(numpy)
    return dictProbe


def flistDescribeProbeConcerns(dictProbe):
    """Return one sentence per requested setting the probe saw fail."""
    sState = dictProbe.get("sNumpyState", "")
    if sState.startswith("NumPy refused"):
        return [sState + ". Every step that imports NumPy will fail."]
    return [
        f"NumPy {dictProbe.get('sNumpyVersion', '')} did not apply "
        f"{S_NUMPY_FEATURE_SETTING}={sName}: {dictJudged['sReason']}. "
        "Outputs from NumPy's vectorized math may differ from a computer "
        "whose NumPy honors the setting."
        for sName, dictJudged in sorted(
            (dictProbe.get("dictNumpyDisabledFeatures") or {}).items())
        if dictJudged["sStatus"] != S_FEATURE_APPLIED
    ]


def fiRunProbe(sScratch, bMachine=False):
    """Record Stage B's observations; with ``bMachine``, print them too."""
    dictProbe = fdictProbeNumericalLibraries()
    if sScratch:
        fnWriteJsonRecord(
            os.path.join(sScratch, S_PROBE_RECORD_FILENAME), dictProbe)
    for sConcern in flistDescribeProbeConcerns(dictProbe):
        print("vaibify: " + sConcern, file=sys.stderr)
    if bMachine:
        print(S_PROBE_RESULT_PREFIX + json.dumps(dictProbe, sort_keys=True))
    return I_PROCEDURE_EXIT_PASSED


# ----------------------------------------------------------------------
# assess: integrity again, then the policy grades
# ----------------------------------------------------------------------


def fdictReadRunContexts(sScratch):
    """Return the Stage A and Stage B records, copied verbatim."""
    return {
        "dictCapabilities": fjsonReadJsonRecordOrNone(
            os.path.join(sScratch, S_CAPABILITIES_RECORD_FILENAME)),
        "dictProbe": fjsonReadJsonRecordOrNone(
            os.path.join(sScratch, S_PROBE_RECORD_FILENAME)),
    }


def fdictBuildIntegrityAfterRun(dictPrepared, dictNow):
    """Combine the integrity seen before the run with the one seen now.

    Both are judged against the manifest ``prepare`` froze. An input that
    matched before the steps and differs after them changed while the
    rerun ran, which is the same verdict as a changed input.
    """
    setBefore = set(dictPrepared["listChangedInputs"]
                    + dictPrepared["listMissingInputs"])
    listChanged = sorted(set(dictPrepared["listChangedInputs"])
                         | set(dictNow["listChangedInputs"]))
    listMissing = sorted(set(dictPrepared["listMissingInputs"])
                         | set(dictNow["listMissingInputs"]))
    setAfter = set(dictNow["listChangedInputs"] + dictNow["listMissingInputs"])
    bManifestMoved = (
        dictNow["sManifestSha256"] != dictPrepared["sManifestSha256"])
    return {
        "bInputsUnchanged": not (listChanged or listMissing or bManifestMoved),
        "listChangedInputs": listChanged,
        "listMissingInputs": listMissing,
        "bManifestMoved": bManifestMoved,
        "bChangedDuringRun": bool(setAfter - setBefore) or bManifestMoved,
    }


def fdictGatherProcedureObservations(sRepoRoot, sScratch, sEvidence,
                                     dictPlan):
    """Return what the policy needs that the procedure observes.

    The expected hashes are the ones ``prepare`` froze before any step
    ran, so a step that rewrites the manifest cannot move the expected
    side of its own comparison.
    """
    dictPrepared = fjsonReadJsonRecordOrNone(
        os.path.join(sScratch, S_PREPARE_RECORD_FILENAME))
    if not isinstance(dictPrepared, dict):
        return {"sManifestState": "preparation record missing"}
    dictExpected = dictPrepared["dictExpectedHashes"]
    dictNow = fdictCheckPinnedInputs(sRepoRoot, dictPlan, dictExpected)
    setGraded = {
        dictOutput["sPath"] for dictStep in dictPlan["listSteps"]
        for dictOutput in dictStep["listOutputs"]
    } | set(dictPlan.get("listCarriedPaths") or [])
    return {
        "sManifestState": "read",
        "sRepoRoot": sRepoRoot,
        "sEvidenceDirectory": sEvidence,
        "dictIntegrity": fdictBuildIntegrityAfterRun(dictPrepared, dictNow),
        "dictExpectedHashes": {
            sPath: dictExpected[sPath] for sPath in sorted(setGraded)
            if sPath in dictExpected},
        "dictObservedHashes": {
            sPath: fsHashRepositoryFileOrNone(sRepoRoot, sPath)
            for sPath in sorted(setGraded) if sPath in dictExpected},
        "dictReferencePaths": dict(
            dictPrepared.get("dictReferencePaths") or {}),
        "dictContexts": fdictReadRunContexts(sScratch),
    }


def fiRunAssess(namespaceArguments, dictPlan, fdictAssessEvidence):
    """Grade the rerun, print the verdict and tally, return the exit code."""
    dictObservations = fdictGatherProcedureObservations(
        namespaceArguments.repo, namespaceArguments.scratch,
        namespaceArguments.evidence, dictPlan)
    dictObservations["sComparisonPolicySource"] = (
        "current; none recorded" if namespaceArguments.no_recorded_policy
        else "recorded")
    dictReport = fdictAssessEvidence(dictPlan, dictObservations)
    fnWriteJsonRecord(os.path.join(
        namespaceArguments.scratch, S_ASSESSMENT_RECORD_FILENAME), dictReport)
    print(dictReport["sVerdictRendered"])
    print(dictReport["sTallyRendered"])
    if namespaceArguments.machine:
        print(S_ASSESSMENT_RESULT_PREFIX
              + json.dumps(dictReport, sort_keys=True))
    return dictReport["iExitCode"]


# ----------------------------------------------------------------------
# The dispatcher
# ----------------------------------------------------------------------


def fparserBuildProcedureArguments():
    """Return the sub-command parser; every one names the repository."""
    parserMain = argparse.ArgumentParser(prog="rerunAssessment.py")
    parserSubcommands = parserMain.add_subparsers(dest="command")
    parserSubcommands.required = True
    parserPrepare = parserSubcommands.add_parser("prepare")
    parserPrepare.add_argument("--repo", required=True)
    parserPrepare.add_argument("--scratch", required=True)
    parserClear = parserSubcommands.add_parser("clear")
    parserClear.add_argument("--repo", required=True)
    parserClear.add_argument("--job", default="")
    parserCapabilities = parserSubcommands.add_parser("capabilities")
    parserCapabilities.add_argument("--scratch", default="")
    parserCapabilities.add_argument(
        "--required-level",
        default=os.environ.get("VAIBIFY_REQUIRED_LEVEL", ""))
    parserCapabilities.add_argument(
        "--floor-keys", default=os.environ.get("VAIBIFY_FLOOR_KEYS", ""))
    parserCapabilities.add_argument("--machine", action="store_true")
    parserProbe = parserSubcommands.add_parser("probe")
    parserProbe.add_argument("--scratch", default="")
    parserProbe.add_argument("--machine", action="store_true")
    parserAssess = parserSubcommands.add_parser("assess")
    parserAssess.add_argument("--repo", required=True)
    parserAssess.add_argument("--scratch", required=True)
    parserAssess.add_argument("--evidence", required=True)
    parserAssess.add_argument("--machine", action="store_true")
    parserAssess.add_argument("--no-recorded-policy", action="store_true")
    return parserMain


def fiDispatchProcedureCommand(namespaceArguments, dictPlan,
                               fdictAssessEvidence):
    """Run one parsed sub-command and return its exit code."""
    sCommand = namespaceArguments.command
    if sCommand == "prepare":
        return fiRunPrepare(
            namespaceArguments.repo, namespaceArguments.scratch, dictPlan)
    if sCommand == "clear":
        return fiRunClear(
            namespaceArguments.repo, namespaceArguments.job, dictPlan)
    if sCommand == "capabilities":
        return fiRunCapabilities(
            namespaceArguments.scratch, namespaceArguments.required_level,
            namespaceArguments.floor_keys, namespaceArguments.machine)
    if sCommand == "probe":
        return fiRunProbe(
            namespaceArguments.scratch, namespaceArguments.machine)
    if fdictAssessEvidence is None:
        print("Could not run: no comparison policy is attached to this "
              "procedure", file=sys.stderr)
        return I_PROCEDURE_EXIT_COULD_NOT_RUN
    return fiRunAssess(namespaceArguments, dictPlan, fdictAssessEvidence)


def fiRunMain(listArguments, dictPlan, fdictAssessEvidence=None):
    """Run one sub-command; every exit path is one of 0, 1, 3, 4 or 5.

    In the rendered file the policy's entry point is a global beside
    this function, so it is found by name. Imported on its own, this
    module has no policy, and a caller passes one.
    """
    if fdictAssessEvidence is None:
        fdictAssessEvidence = globals().get(S_POLICY_ENTRY_POINT)
    try:
        namespaceArguments = fparserBuildProcedureArguments().parse_args(
            listArguments)
    except SystemExit as errorExit:
        if errorExit.code in (0, None):
            return I_PROCEDURE_EXIT_PASSED
        return I_PROCEDURE_EXIT_COULD_NOT_RUN
    try:
        return fiDispatchProcedureCommand(
            namespaceArguments, dictPlan, fdictAssessEvidence)
    except Exception as error:
        print(f"Could not run: {type(error).__name__}: {error}",
              file=sys.stderr)
        return I_PROCEDURE_EXIT_COULD_NOT_RUN
