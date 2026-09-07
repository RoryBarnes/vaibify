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
from vaibify.reproducibility import reproductionSource


__all__ = [
    "F_REPORT_RETENTION_SECONDS",
    "I_REPORT_SCHEMA_VERSION",
    "S_VERDICT_DIVERGED",
    "S_VERDICT_NO_VERDICT",
    "S_VERDICT_REPRODUCED",
    "fdictBuildReproductionReport",
    "fdictReadReproductionReport",
    "flistSweepExpiredReports",
    "fsRenderVerdict",
    "fsReportsDirectory",
    "fsWriteReproductionReport",
]


I_REPORT_SCHEMA_VERSION = 1

S_VERDICT_REPRODUCED = "reproduced"
S_VERDICT_DIVERGED = "diverged"
S_VERDICT_NO_VERDICT = "no-verdict"

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
    if not bRerunAttempted:
        sVerdict = S_VERDICT_NO_VERDICT
    elif dictOutcome.get("bPassed"):
        sVerdict = S_VERDICT_REPRODUCED
    else:
        sVerdict = S_VERDICT_DIVERGED
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
        "sVerdict": sVerdict,
        "bRerunAttempted": bRerunAttempted,
        "iOutputHashesMatched": int(dictOutcome.get("iOutputHashesMatched", 0)),
        "iOutputHashesTotal": int(dictOutcome.get("iOutputHashesTotal", 0)),
        "listDivergedHashes": list(dictOutcome.get("listDivergedHashes") or []),
        "listCarriedPaths": list(dictOutcome.get("listCarriedPaths") or []),
        "dictRerunFailure": dict(dictOutcome.get("dictRerunFailure") or {}),
        "dictImageRecheck": {
            "sVerdict": str((dictImageRecheck or {}).get("sVerdict") or ""),
            "sReason": str((dictImageRecheck or {}).get("sReason") or ""),
            "bVacuous": bool((dictImageRecheck or {}).get("bVacuous")),
        },
        "sShadowTeardown": str(dictOutcome.get("sShadowTeardown") or ""),
        "fDurationSeconds": float(fDurationSeconds),
    }


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


def fsRenderVerdict(dictReport):
    """Return the one-line, researcher-facing verdict of a report."""
    sVerdict = dictReport.get("sVerdict", "")
    dictPlatform = dictReport.get("dictPlatform") or {}
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
    return "no verdict: " + (listReasons[0] if listReasons else "the rerun never started")


def fsWriteReproductionReport(dictReport):
    """Persist a report under its id and return the file path written."""
    sDirectory = fsReportsDirectory()
    _fnEnsurePrivateDirectory(sDirectory)
    flistSweepExpiredReports()
    sPath = os.path.join(sDirectory, f"{dictReport['sReportId']}.json")
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictReport, fileHandle, indent=2, sort_keys=True)
        fileHandle.write("\n")
    return sPath


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
        listRemoved.append(sName[:-len(".json")])
    return listRemoved


def _fnEnsurePrivateDirectory(sPath):
    """Create ``sPath`` and any missing ancestor at mode 0700."""
    if os.path.isdir(sPath):
        return
    sParent = os.path.dirname(sPath)
    if sParent and sParent != sPath:
        _fnEnsurePrivateDirectory(sParent)
    os.makedirs(sPath, mode=_I_PRIVATE_DIRECTORY_MODE, exist_ok=True)
    os.chmod(sPath, _I_PRIVATE_DIRECTORY_MODE)
