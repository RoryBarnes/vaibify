"""The reproduction report: the reproducer's own artefact, never the author's.

A third party who re-runs a published project produces EVIDENCE, and
this module gives that evidence a home of its own. It is deliberately
not an attestation: it is never written into any repository, it is
never read by ``levelGates`` (an architectural test pins that), and it
carries its own schema rather than borrowing the attestation's --
because an attestation is the author's claim about their project, and
a report is a stranger's record of what happened when they tried.

Reports are DURABLE and live apart from staging: the staged snapshot
is scratch, deleted after every run whatever the outcome, and a report
that lived inside it would vanish with the thing it describes. They
sit under ``~/.vaibify/reproductions/reports/<sReportId>.json`` with
their own retention.

**What a report may carry, and what it may not.** Source facts come
from ``reproductionSource.fdictDescribeStagedSource`` only -- the
source kind, the resolved commit, the remote with userinfo stripped,
the workflow name -- never a path on the reproducer's machine, because
the reproducer may one day deposit the report publicly. The verdict
vocabulary is ``reproduced``, ``reproduced under emulation``,
``diverged`` and ``no verdict``; it is never ``attested``, because
vaibify offers no attesting, publishing or depositing action on a
reproduction.
"""

import json
import os
import secrets
import time
from datetime import datetime, timezone

from vaibify import __version__
from vaibify.reproducibility import comparisonPolicyV1
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility import imageArchive
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility import reproductionSource
from vaibify.reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "fdictRecheckObtainedImage",
    "F_REPORT_RETENTION_SECONDS",
    "I_REPORT_SCHEMA_VERSION",
    "I_EXIT_COULD_NOT_RUN",
    "SET_VERDICTS_THAT_DO_NOT_REPRODUCE",
    "SET_VERDICTS_THAT_REPRODUCE",
    "S_VERDICT_BYTE_IDENTICAL",
    "S_VERDICT_COMPARISONS_PASSED",
    "S_VERDICT_DIVERGED",
    "S_VERDICT_INPUTS_CHANGED",
    "S_VERDICT_INSUFFICIENT_EVIDENCE",
    "S_VERDICT_NO_VERDICT",
    "S_VERDICT_NOT_REPRODUCED",
    "S_VERDICT_REPRODUCED",
    "fdictBuildReproductionReport",
    "fiExitCodeOfOutcome",
    "flistDecisiveVerdicts",
    "fsVerdictOfOutcome",
    "fdictReadReproductionReport",
    "flistSweepExpiredReports",
    "fsReadReproducedManifestText",
    "fsRenderVerdict",
    "fsReportsDirectory",
    "fsWriteReproductionReport",
]


# v2 (2026-09-11) added the per-file outcomes, the provenance block
# and the name of the reproduced manifest written beside the report.
# v3 (2026-09-30): the output counts cover only regenerated outputs;
# pinned inputs are counted apart and the pre-run deletions are named.
# v4 (2026-10-17): the verdict is the assessed rerun's, with its
# wording, tally, comparison policy and exit code.
I_REPORT_SCHEMA_VERSION = 4
_S_REPRODUCED_MANIFEST_SUFFIX = ".sha256"

# ``reproduced`` and ``diverged`` are the byte-only verifier's verdicts,
# kept so a report written before schema 4 still reads as what it said.
S_VERDICT_REPRODUCED = "reproduced"
S_VERDICT_DIVERGED = "diverged"
S_VERDICT_NO_VERDICT = "no-verdict"
# The assessed rerun's verdicts are the comparison policy's own words,
# never retyped here: a second copy of a verdict string is how two
# readers of one report come to disagree.
S_VERDICT_BYTE_IDENTICAL = comparisonPolicyV1.S_VERDICT_BYTE_IDENTICAL
S_VERDICT_COMPARISONS_PASSED = comparisonPolicyV1.S_VERDICT_COMPARISONS_PASSED
S_VERDICT_NOT_REPRODUCED = comparisonPolicyV1.S_VERDICT_NOT_REPRODUCED
S_VERDICT_INSUFFICIENT_EVIDENCE = (
    comparisonPolicyV1.S_VERDICT_INSUFFICIENT_EVIDENCE)
S_VERDICT_INPUTS_CHANGED = comparisonPolicyV1.S_VERDICT_INPUTS_CHANGED
SET_VERDICTS_THAT_REPRODUCE = frozenset({
    S_VERDICT_REPRODUCED, S_VERDICT_BYTE_IDENTICAL,
    S_VERDICT_COMPARISONS_PASSED,
})
SET_VERDICTS_THAT_DO_NOT_REPRODUCE = frozenset({
    S_VERDICT_DIVERGED, S_VERDICT_NOT_REPRODUCED,
    S_VERDICT_INSUFFICIENT_EVIDENCE, S_VERDICT_INPUTS_CHANGED,
})
I_EXIT_COULD_NOT_RUN = 4

_S_REPORTS_SUBDIRECTORY = "reports"
_I_PRIVATE_DIRECTORY_MODE = 0o700

# A report older than this is swept when a new one is written. Half a
# year outlives any reproduction a reviewer is still reading; a report
# somebody wants kept longer is theirs to copy out, and the sweep only
# ever removes files this module wrote (the ``.json`` under its own
# directory).
F_REPORT_RETENTION_SECONDS = 183 * 24 * 60 * 60


def fsReportsDirectory():
    """Return the reports directory, beside staging and never inside it."""
    return os.path.join(
        reproductionSource._S_REPRODUCTIONS_DIRECTORY,
        _S_REPORTS_SUBDIRECTORY,
    )


def fdictBuildReproductionReport(
    dictSource, dictAcquired, dictOutcome, dictImageRecheck,
    fDurationSeconds,
):
    """Return the report for one rerun of one staged snapshot.

    ``dictSource`` is exactly :func:`fdictDescribeStagedSource`'s
    answer; ``dictAcquired`` is ``imageAcquisition``'s; ``dictOutcome``
    is the shadow rerun's; ``dictImageRecheck`` is the archive
    re-check's verdict. Every count and every diverged path comes from
    the outcome, nothing is inferred from an exit code, and the carried
    paths ride beside the counts because a ratio displayed without them
    turns a narrow true statement into a broad false one.
    """
    bRerunAttempted = bool(dictOutcome.get("bRerunAttempted", True))
    return {
        "sReportId": secrets.token_hex(8),
        "iSchemaVersion": I_REPORT_SCHEMA_VERSION,
        "sVaibifyVersion": str(__version__),
        "sCreatedAtIso": datetime.now(timezone.utc).isoformat(),
        "dictSource": _fdictSourceFacts(dictSource),
        "sManifestDigest": str(
            dictOutcome.get("sManifestDigest")
            or dictSource.get("sManifestDigest") or ""
        ),
        "dictPlatform": {
            "sRequiredPlatform": dictAcquired.get("sRequiredPlatform", ""),
            "sObtainedPlatform": dictAcquired.get("sObtainedPlatform", ""),
            "sDaemonArchitecture": dictAcquired.get(
                "sDaemonArchitecture", "",
            ),
            "bEmulated": bool(dictAcquired.get("bEmulated")),
        },
        "sObtainedFrom": dictAcquired.get("sObtainedFrom", ""),
        "sImageReferenceRun": dictAcquired.get("sImageReference", ""),
        "listAcquisitionAttempts": list(dictAcquired.get("listAttempts") or []),
        "sVerdict": fsVerdictOfOutcome(dictOutcome),
        "sVerdictRendered": str(dictOutcome.get("sVerdictRendered") or ""),
        "sTallyRendered": str(dictOutcome.get("sTallyRendered") or ""),
        "dictTally": dict(dictOutcome.get("dictTally") or {}),
        "sComparisonPolicy": str(dictOutcome.get("sComparisonPolicy") or ""),
        "iExitCode": fiExitCodeOfOutcome(dictOutcome),
        "dictAssessmentReport": (
            dict(dictOutcome["dictAssessmentReport"])
            if isinstance(dictOutcome.get("dictAssessmentReport"), dict)
            else None),
        "bRerunAttempted": bRerunAttempted,
        "iOutputHashesMatched": int(dictOutcome.get("iOutputHashesMatched", 0)),
        "iOutputHashesTotal": int(dictOutcome.get("iOutputHashesTotal", 0)),
        "iPinnedInputsUnchanged": int(
            dictOutcome.get("iPinnedInputsUnchanged") or 0),
        "iPinnedInputsTotal": int(dictOutcome.get("iPinnedInputsTotal") or 0),
        "dictPreRerunClearing": dict(
            dictOutcome.get("dictPreRerunClearing") or {}),
        "listDivergedHashes": list(dictOutcome.get("listDivergedHashes") or []),
        "listMatchedPaths": list(dictOutcome.get("listMatchedPaths") or []),
        "listCarriedPaths": list(dictOutcome.get("listCarriedPaths") or []),
        # The per-file hashes, kept whole: the file table and the
        # reproduced manifest are both rendered from this list and
        # nothing is re-hashed after the shadow is destroyed.
        "listFileOutcomes": [
            dict(dictFile)
            for dictFile in (dictOutcome.get("listFileOutcomes") or [])
        ],
        "dictReproductionProvenance": (
            dict(dictOutcome["dictReproductionProvenance"])
            if isinstance(
                dictOutcome.get("dictReproductionProvenance"), dict,
            ) else None
        ),
        # Filled by the writer once the companion file exists.
        "sReproducedManifestPath": None,
        "dictRerunFailure": dict(dictOutcome.get("dictRerunFailure") or {}),
        "dictImageRecheck": {
            "sVerdict": str((dictImageRecheck or {}).get("sVerdict") or ""),
            "sReason": str((dictImageRecheck or {}).get("sReason") or ""),
            "bVacuous": bool((dictImageRecheck or {}).get("bVacuous")),
        },
        "sShadowTeardown": str(dictOutcome.get("sShadowTeardown") or ""),
        "fDurationSeconds": float(fDurationSeconds),
    }


def flistDecisiveVerdicts():
    """Return every verdict a reader's label may be decided by, sorted.

    The poll's snapshot program keeps the newest record per workflow
    whose verdict is one of these, so it is handed this list rather than
    spelling verdicts of its own: a copy there kept only the two
    byte-only verdicts, and a reader's assessed reproduction was dropped
    before the label could read it.
    """
    return sorted(SET_VERDICTS_THAT_REPRODUCE
                  | SET_VERDICTS_THAT_DO_NOT_REPRODUCE)


def fsVerdictOfOutcome(dictOutcome):
    """Return the verdict an outcome reached; the assessment's when it has one."""
    if not dictOutcome.get("bRerunAttempted", True):
        return S_VERDICT_NO_VERDICT
    if dictOutcome.get("sVerdict"):
        return str(dictOutcome["sVerdict"])
    return S_VERDICT_REPRODUCED if dictOutcome.get("bPassed") else (
        S_VERDICT_DIVERGED)


def fiExitCodeOfOutcome(dictOutcome):
    """Return the shared exit code (design D6) a lane exits with.

    The assessment's own code when it reached a verdict; "Could not run"
    (4) for a rerun that was refused, or could not be graded.
    """
    if not dictOutcome.get("bRerunAttempted", True):
        return I_EXIT_COULD_NOT_RUN
    iExitCode = dictOutcome.get("iAssessmentExitCode")
    if isinstance(iExitCode, int):
        return iExitCode
    return 0 if dictOutcome.get("bPassed") else 1


def _fdictSourceFacts(dictSource):
    """Copy the redacted source facts a report may carry, and only those."""
    return {
        sKey: dictSource.get(sKey, "")
        for sKey in (
            "sKind", "sRepositoryName", "sResolvedCommit", "sRemoteUrl",
            "sWorkflowName", "sWorkflowPath", "sPinnedImageReference",
            "sRequiredArchitecture", "bDepositOnRecord",
            "sDepositVersionDoi",
        )
    }


def fdictRecheckObtainedImage(sToken, dictAcquired):
    """Judge the obtained image against the deposit on record, if any.

    Vacuous by construction when the chain LOADED the deposit: a
    download re-hashed against itself matches always, and the report
    says so rather than recording a comparison nobody made. Shared by
    the CLI and the dashboard job because both write the same report,
    and two derivations of one verdict are the drift this module's
    attestation-writing cousins already suffered once.
    """
    dictEnvironment = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(reproductionSource.fsStagedClonePath(sToken)),
    ) or {}
    bLoadedFromArchive = (
        dictAcquired.get("sObtainedFrom") == imageAcquisition.S_OBTAINED_ARCHIVE
    )
    sLocalStreamSha = ""
    if imageArchive.fdictReadArchiveRecord(dictEnvironment) and not (
        bLoadedFromArchive
    ):
        sLocalStreamSha = imageDeposit.fsRecomputeImageStreamSha256(
            dictAcquired["sImageReference"],
        )
    dictVerdict = imageArchive.fdictJudgeArchiveRecheck(
        dictEnvironment, sLocalStreamSha, bLoadedFromArchive,
    )
    dictVerdict["bVacuous"] = (
        dictVerdict.get("sVerdict") == imageArchive.S_RECHECK_VACUOUS
    )
    return dictVerdict


def fsRenderVerdict(dictReport):
    """Return the one-line, researcher-facing verdict of a report."""
    sVerdict = dictReport.get("sVerdict", "")
    dictPlatform = dictReport.get("dictPlatform") or {}
    sRendered = str(dictReport.get("sVerdictRendered") or "")
    if sRendered:
        if sVerdict in SET_VERDICTS_THAT_REPRODUCE and dictPlatform.get(
                "bEmulated"):
            return (
                f"{sRendered} (under emulation: "
                f"{dictPlatform.get('sRequiredPlatform')} image on a "
                f"{dictPlatform.get('sDaemonArchitecture')} host)")
        return sRendered
    if sVerdict == S_VERDICT_REPRODUCED:
        if dictPlatform.get("bEmulated"):
            return (
                "reproduced under emulation ("
                f"{dictPlatform.get('sRequiredPlatform')} image on "
                f"a {dictPlatform.get('sDaemonArchitecture')} host)"
            )
        return "reproduced"
    if sVerdict == S_VERDICT_DIVERGED:
        return "diverged"
    listReasons = dictReport.get("listDivergedHashes") or []
    return "Could not run: " + (
        listReasons[0] if listReasons else "the rerun never started")


def fsWriteReproductionReport(dictReport):
    """Persist a report under its id and return the file path written.

    The reproduced manifest lands FIRST as ``<id>.sha256`` beside the
    JSON, rendered from the report's own per-file outcomes, and the
    report is written naming it only after -- so a report never points
    at a companion that does not exist. A report whose rerun compared
    nothing has no companion and names none.
    """
    from vaibify.reproducibility.manifestWriter import (
        fsRenderReproducedManifest,
    )
    sDirectory = fsReportsDirectory()
    _fnEnsurePrivateDirectory(sDirectory)
    flistSweepExpiredReports()
    sReportId = dictReport["sReportId"]
    listFileOutcomes = dictReport.get("listFileOutcomes") or []
    if listFileOutcomes:
        sManifestName = sReportId + _S_REPRODUCED_MANIFEST_SUFFIX
        with open(
            os.path.join(sDirectory, sManifestName), "w", encoding="utf-8",
        ) as fileManifest:
            fileManifest.write(fsRenderReproducedManifest(listFileOutcomes))
        dictReport["sReproducedManifestPath"] = sManifestName
    sPath = os.path.join(sDirectory, f"{sReportId}.json")
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictReport, fileHandle, indent=2, sort_keys=True)
        fileHandle.write("\n")
    return sPath


def fsReadReproducedManifestText(sReportId):
    """Return a stored report's reproduced manifest; ``LookupError`` if none."""
    dictReport = fdictReadReproductionReport(sReportId)
    sManifestName = dictReport.get("sReproducedManifestPath") or ""
    if not sManifestName or os.path.basename(sManifestName) != sManifestName:
        raise LookupError(f"report {sReportId!r} names no reproduced manifest")
    try:
        with open(
            os.path.join(fsReportsDirectory(), sManifestName), "r",
            encoding="utf-8",
        ) as fileHandle:
            return fileHandle.read()
    except OSError as error:
        raise LookupError(
            f"the reproduced manifest of report {sReportId!r} is gone"
        ) from error


def fdictReadReproductionReport(sReportId):
    """Return a stored report by id; ``LookupError`` when there is none."""
    if not sReportId or os.path.basename(sReportId) != sReportId:
        raise LookupError(f"report id {sReportId!r} is not a bare name")
    sPath = os.path.join(fsReportsDirectory(), f"{sReportId}.json")
    try:
        with open(sPath, "r", encoding="utf-8") as fileHandle:
            return json.load(fileHandle)
    except (OSError, ValueError) as error:
        raise LookupError(f"no reproduction report {sReportId!r}") from error


def flistSweepExpiredReports(fMaxAgeSeconds=F_REPORT_RETENTION_SECONDS):
    """Delete reports past retention; return the ids removed."""
    sDirectory = fsReportsDirectory()
    if not os.path.isdir(sDirectory):
        return []
    fCutoff = time.time() - fMaxAgeSeconds
    listRemoved = []
    for sName in sorted(os.listdir(sDirectory)):
        if not sName.endswith(".json"):
            continue
        sPath = os.path.join(sDirectory, sName)
        try:
            if os.path.getmtime(sPath) > fCutoff:
                continue
            os.remove(sPath)
        except OSError:
            continue
        sReportId = sName[:-len(".json")]
        _fnRemoveCompanionManifest(sDirectory, sReportId)
        listRemoved.append(sReportId)
    return listRemoved


def _fnRemoveCompanionManifest(sDirectory, sReportId):
    """Remove the ``<id>.sha256`` written beside a swept report, if any."""
    try:
        os.remove(os.path.join(
            sDirectory, sReportId + _S_REPRODUCED_MANIFEST_SUFFIX,
        ))
    except OSError:
        return


def _fnEnsurePrivateDirectory(sPath):
    """Create ``sPath`` and any missing ancestor at mode 0700."""
    if os.path.isdir(sPath):
        return
    sParent = os.path.dirname(sPath)
    if sParent and sParent != sPath:
        _fnEnsurePrivateDirectory(sParent)
    os.makedirs(sPath, mode=_I_PRIVATE_DIRECTORY_MODE, exist_ok=True)
    os.chmod(sPath, _I_PRIVATE_DIRECTORY_MODE)
