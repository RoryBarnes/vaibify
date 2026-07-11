"""Persist and validate per-step falsification attestations.

A falsification attestation records the outcome of mutation-testing a
deterministic Python step's code against its quantitative tests
(cosmic-ray): "would these tests notice if the step's code broke?"
The recorded kill-rate is a statement about the tests'
fault-detection *sensitivity*, never about the result's *accuracy* —
a test can be exquisitely sensitive to change while asserting a wrong
value. Equivalent mutants (mutations with no observable effect) make
a 100% kill-rate unreachable in general, so the attestation is
deliberately NON-GATING: it feeds no AICS rung and must never be
presented as a pass/fail gate.

The persisted record lives at
``<projectRepo>/.vaibify/falsification/<stepSlug>.json`` and is
digest-keyed on the step's Python scripts plus its
``quantitative_standards.json``: any edit to either invalidates the
record (mirrors ``l3Attestation.fbL3AttestationCurrent``).

Honesty rules (load-bearing):

* Applicability is recomputed live by every reader and OVERRIDES any
  record on disk. Only a step whose quantitative standards are
  classified ``deterministic`` AND whose computation is Python source
  can be mutation-tested; every other step is "not applicable" and
  must never render green — a green badge that checked nothing is a
  dashboard-honesty violation. ``fdictBuildFalsificationStatus``
  enforces this by forcing ``bRecordCurrent`` False for
  non-applicable steps regardless of what the record claims.
* The mutation run itself is on-demand only (cost = mutants × step
  runtime) with a per-mutant wall-clock timeout.

All file IO goes through the ``repoFiles`` adapter seam so the same
logic is honest on a host clone and inside a running container.
"""

import hashlib
import json
import posixpath
from datetime import datetime, timezone

from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "F_MUTANT_TIMEOUT_SECONDS",
    "I_SCHEMA_VERSION",
    "S_CLASSIFICATION_DETERMINISTIC",
    "S_FALSIFICATION_DIRECTORY",
    "S_SESSION_SUMMARY_SCRIPT",
    "S_STATUS_ATTAINED",
    "S_STATUS_ERROR",
    "fbFalsificationRecordCurrent",
    "fdictBuildFalsificationRecord",
    "fdictBuildFalsificationStatus",
    "fdictClassifyFalsificationApplicability",
    "fdictReadFalsificationRecord",
    "flistExtractPythonScriptRelPaths",
    "flistFalsificationDigestPaths",
    "fnWriteFalsificationRecord",
    "fsBuildCosmicRayConfigToml",
    "fsCurrentFalsificationDigest",
    "fsFalsificationRecordRelativePath",
    "fsFalsificationStepSlug",
]


I_SCHEMA_VERSION = 1
S_FALSIFICATION_DIRECTORY = "falsification"
S_STATUS_ATTAINED = "attained"
S_STATUS_ERROR = "error"
S_CLASSIFICATION_DETERMINISTIC = "deterministic"
F_MUTANT_TIMEOUT_SECONDS = 300.0
_S_VAIBIFY_DIRECTORY = ".vaibify"


def fsFalsificationStepSlug(sStepDirectory):
    """Return a flat filesystem-safe slug for a repo-relative step dir.

    Step directories are validated repo-relative with no ``..``
    segments at workflow load, so replacing separators is sufficient.
    The empty legacy directory maps to a stable sentinel.
    """
    sTrimmed = (sStepDirectory or "").strip("/")
    if not sTrimmed:
        return "workflowRoot"
    return sTrimmed.replace("/", "__")


def fsFalsificationRecordRelativePath(sStepDirectory):
    """Return the repo-relative path of a step's falsification record."""
    return posixpath.join(
        _S_VAIBIFY_DIRECTORY, S_FALSIFICATION_DIRECTORY,
        fsFalsificationStepSlug(sStepDirectory) + ".json",
    )


def flistExtractPythonScriptRelPaths(dictStep):
    """Return repo-relative Python scripts from the step's data commands.

    Only plain, step-directory-relative ``.py`` paths qualify: a
    token-bearing or absolute script path cannot be hashed against the
    project repo, so it is excluded (and the step then classifies as
    not applicable rather than silently mutating the wrong file).
    """
    from vaibify.gui.commandUtilities import flistExtractScripts
    sDirectory = dictStep.get("sDirectory", "")
    listRelPaths = []
    for sScript in flistExtractScripts(dictStep.get("saDataCommands", [])):
        if "{" in sScript or posixpath.isabs(sScript):
            continue
        if not sScript.endswith(".py"):
            continue
        listRelPaths.append(
            posixpath.normpath(posixpath.join(sDirectory, sScript)),
        )
    return listRelPaths


def _fdictParseQuantitativeStandards(filesRepo, sStandardsRelPath):
    """Return the parsed standards dict, or None on any read failure."""
    try:
        dictStandards = json.loads(filesRepo.fsReadText(sStandardsRelPath))
    except (OSError, ValueError):
        return None
    if not isinstance(dictStandards, dict):
        return None
    return dictStandards


def fdictClassifyFalsificationApplicability(dictStep, filesRepo):
    """Return the live applicability verdict for mutation-testing a step.

    A step qualifies only when its computation is Python source, its
    quantitative standards exist and are classified ``deterministic``
    with at least one benchmark, and its quantitative test file
    exists. The returned ``sReason`` names the first disqualifier so
    the dashboard can state exactly why the check is not applicable.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sDirectory = dictStep.get("sDirectory", "")
    dictResult = {
        "bApplicable": False,
        "sReason": "",
        "sClassification": "",
        "listScriptRelPaths": flistExtractPythonScriptRelPaths(dictStep),
        "sStandardsRelPath": posixpath.join(
            sDirectory, "tests", "quantitative_standards.json"),
        "sQuantitativeTestRelPath": posixpath.join(
            sDirectory, "tests", "test_quantitative.py"),
    }
    dictResult["sReason"] = _fsDescribeApplicabilityGap(
        dictResult, filesRepo,
    )
    dictResult["bApplicable"] = dictResult["sReason"] == ""
    return dictResult


def _fsDescribeApplicabilityGap(dictResult, filesRepo):
    """Return the first disqualifier for mutation testing, or ``""``.

    Side effect: fills ``dictResult["sClassification"]`` once the
    standards file has been read, so callers can report the
    classification even for non-applicable steps.
    """
    if not dictResult["listScriptRelPaths"]:
        return (
            "step computation is not Python source; there is nothing "
            "for mutation testing to mutate"
        )
    if not filesRepo.fbIsFile(dictResult["sStandardsRelPath"]):
        return "no quantitative standards are recorded for this step"
    dictStandards = _fdictParseQuantitativeStandards(
        filesRepo, dictResult["sStandardsRelPath"],
    )
    if dictStandards is None:
        return "the quantitative standards file is unreadable"
    sClassification = dictStandards.get(
        "sStochasticityClassification", "")
    dictResult["sClassification"] = sClassification
    if sClassification != S_CLASSIFICATION_DETERMINISTIC:
        return (
            f"step is classified '{sClassification or 'unknown'}'; "
            "mutation testing is only honest for deterministic steps"
        )
    if not dictStandards.get("listStandards"):
        return "the quantitative standards contain no benchmarks"
    if not filesRepo.fbIsFile(dictResult["sQuantitativeTestRelPath"]):
        return (
            "no quantitative test file (tests/test_quantitative.py) "
            "exists for this step"
        )
    return ""


def flistFalsificationDigestPaths(dictApplicability):
    """Return the sorted repo-relative paths the record digest covers."""
    listPaths = list(dictApplicability.get("listScriptRelPaths", []))
    sStandardsRelPath = dictApplicability.get("sStandardsRelPath", "")
    if sStandardsRelPath:
        listPaths.append(sStandardsRelPath)
    return sorted(listPaths)


def fsCurrentFalsificationDigest(filesRepo, listRelPaths):
    """Return the combined SHA-256 digest of the given repo files.

    Any missing or unhashable file collapses the digest to ``""`` so
    staleness checks fail closed: an unverifiable identity can never
    match a recorded one.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not listRelPaths:
        return ""
    listSorted = sorted(listRelPaths)
    dictHashed = filesRepo.fdictHashFiles(listSorted)
    listLines = []
    for sRelPath in listSorted:
        sHash = (dictHashed.get(sRelPath) or {}).get("sSha256")
        if not sHash:
            return ""
        listLines.append(f"{sRelPath}:{sHash}")
    sJoined = "\n".join(listLines)
    return "sha256:" + hashlib.sha256(
        sJoined.encode("utf-8")).hexdigest()


def fdictBuildFalsificationRecord(
    sStatus, sScriptDigest, sClassification,
    iMutantsTotal, iMutantsKilled, iMutantsSurvived,
    listSurvivors=None, sCosmicRayVersion="",
    fDurationSeconds=0.0, sReason="",
):
    """Return a fully-populated falsification record dict (no file IO).

    ``fKillRate`` is killed over graded total (0.0 when nothing was
    graded); the raw counts are all persisted so the manuscript can
    cite the exact denominator, including mutants that were neither
    killed nor survived (incompetent test runs).
    """
    iTotal = int(iMutantsTotal)
    fKillRate = float(int(iMutantsKilled)) / iTotal if iTotal > 0 else 0.0
    return {
        "iSchemaVersion": I_SCHEMA_VERSION,
        "sStatus": sStatus,
        "bApplicable": True,
        "sReason": sReason,
        "sClassification": sClassification,
        "fKillRate": fKillRate,
        "iMutantsTotal": iTotal,
        "iMutantsKilled": int(iMutantsKilled),
        "iMutantsSurvived": int(iMutantsSurvived),
        "listSurvivors": list(listSurvivors or []),
        "sScriptDigest": sScriptDigest,
        "sAttestedAtUtc": _fsCurrentTimestamp(),
        "sCosmicRayVersion": sCosmicRayVersion,
        "fDurationSeconds": float(fDurationSeconds),
    }


def fdictReadFalsificationRecord(filesRepo, sStepDirectory):
    """Return the parsed record for a step or ``None``.

    Missing file, malformed JSON, and non-dict payload all map to
    ``None`` so every reader treats them uniformly as "never run".
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = fsFalsificationRecordRelativePath(sStepDirectory)
    if not filesRepo.fbIsFile(sRelPath):
        return None
    try:
        dictPayload = json.loads(filesRepo.fsReadText(sRelPath))
    except (OSError, ValueError):
        return None
    if not isinstance(dictPayload, dict):
        return None
    return dictPayload


def fnWriteFalsificationRecord(filesRepo, sStepDirectory, dictRecord):
    """Persist the record atomically at the step's canonical path."""
    ffilesEnsureRepoFiles(filesRepo).fnWriteJsonAtomic(
        fsFalsificationRecordRelativePath(sStepDirectory), dictRecord,
    )


def fbFalsificationRecordCurrent(filesRepo, dictRecord, listRelPaths):
    """Return True iff a record exists, attained, and is not stale.

    Staleness is keyed against ``fsCurrentFalsificationDigest`` over
    the step's live scripts + standards, so any edit to either
    invalidates the record without a separate timestamp. An empty
    recorded digest can never attest as current — the live digest is
    also ``""`` when files are missing, and ``"" == ""`` must not
    read as fresh.
    """
    if dictRecord is None:
        return False
    if dictRecord.get("sStatus") != S_STATUS_ATTAINED:
        return False
    sRecorded = dictRecord.get("sScriptDigest") or ""
    if not sRecorded:
        return False
    return sRecorded == fsCurrentFalsificationDigest(
        filesRepo, listRelPaths,
    )


def fdictBuildFalsificationStatus(dictStep, filesRepo, dictInFlight=None):
    """Return the falsification payload shape consumed by the dashboard.

    The applicability gate is recomputed live and OVERRIDES any record
    on disk: a "not applicable" step must never present a current
    (green) attestation, even if a stale or hand-edited record claims
    one. This is the single choke point for the N/A-never-green
    honesty invariant.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictApplicability = fdictClassifyFalsificationApplicability(
        dictStep, filesRepo,
    )
    dictRecord = fdictReadFalsificationRecord(
        filesRepo, dictStep.get("sDirectory", ""),
    )
    bRecordCurrent = False
    if dictApplicability["bApplicable"]:
        bRecordCurrent = fbFalsificationRecordCurrent(
            filesRepo, dictRecord,
            flistFalsificationDigestPaths(dictApplicability),
        )
    return {
        "dictApplicability": dictApplicability,
        "dictRecord": dictRecord,
        "bRecordCurrent": bRecordCurrent,
        "dictInFlight": dictInFlight,
    }


def fsBuildCosmicRayConfigToml(
    listModulePaths, sTestCommand,
    fTimeoutSeconds=F_MUTANT_TIMEOUT_SECONDS,
):
    """Return cosmic-ray TOML text for one step's mutation session.

    String values are serialized with ``json.dumps`` — JSON string
    escaping is a subset of TOML basic-string escaping, so arbitrary
    quotes and backslashes in paths or commands cannot break the
    config. The per-mutant ``timeout`` bounds total cost; a hanging
    mutant is counted as killed by cosmic-ray itself.
    """
    sModuleArray = ", ".join(
        json.dumps(sPath) for sPath in listModulePaths
    )
    return (
        "[cosmic-ray]\n"
        f"module-path = [{sModuleArray}]\n"
        f"timeout = {float(fTimeoutSeconds)}\n"
        "excluded-modules = []\n"
        f"test-command = {json.dumps(sTestCommand)}\n"
        "\n"
        "[cosmic-ray.distributor]\n"
        "name = \"local\"\n"
    )


# Executed INSIDE the container (which has cosmic-ray but not vaibify)
# to summarize a finished mutation session as one JSON line. It must
# therefore be dependency-free apart from cosmic_ray itself — mirror
# of the reader in .github/workflows/mutation.yml. Counting rules:
# only WorkerOutcome.NORMAL results are graded; KILLED and SURVIVED
# are counted explicitly, so incompetent test runs inflate neither
# the kill count nor the survivor list.
S_SESSION_SUMMARY_SCRIPT = '''\
import json
import sys

from cosmic_ray.work_db import use_db, WorkDB
from cosmic_ray.work_item import TestOutcome, WorkerOutcome

iMutantsTotal = 0
iMutantsKilled = 0
listSurvivors = []
with use_db(sys.argv[1], WorkDB.Mode.open) as db:
    for workItem, workResult in db.completed_work_items:
        if workResult.worker_outcome != WorkerOutcome.NORMAL:
            continue
        iMutantsTotal += 1
        if workResult.test_outcome == TestOutcome.KILLED:
            iMutantsKilled += 1
        elif workResult.test_outcome == TestOutcome.SURVIVED:
            mutation = workItem.mutations[0]
            listSurvivors.append({
                "sModulePath": str(mutation.module_path),
                "iLine": mutation.start_pos[0],
                "sOperator": mutation.operator_name,
                "sFunction": mutation.definition_name or "",
            })
print(json.dumps({
    "iMutantsTotal": iMutantsTotal,
    "iMutantsKilled": iMutantsKilled,
    "iMutantsSurvived": len(listSurvivors),
    "listSurvivors": listSurvivors,
}))
'''


def _fsCurrentTimestamp():
    """Return the current UTC time as an ISO 8601 ``Z``-suffixed string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
