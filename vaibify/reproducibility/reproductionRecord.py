"""A reproduction recorded in a clone that carries somebody else's attestation.

An attestation is the author's claim about their own project. A
researcher who obtained the author's environment and clicked Verify
has REPRODUCED the result, or failed to -- they have not attested.
Overwriting ``.vaibify/l3_attestation.json`` in their clone would put
two people's claims under one name, and a push to a fork would publish
the stranger's run as the author's. So a verification on such a clone
writes a reproduction record under ``.vaibify/reproductions/`` instead,
in the reproduction REPORT's schema, and leaves the attestation alone.

WHEN A CLONE IS SOMEBODY ELSE'S
-------------------------------

The trigger is git evidence in the repository, never a host-side
record of how the image was obtained: an author who re-obtains their
own image is still the author. The predicate is that the attestation
is TRACKED at HEAD and its last COMMITTER differs from the identity
the receiving repository would commit under. The committer, not the
author: a rebase preserves the author of somebody else's commit and
re-stamps the committer, which is what "the person whose repository
this is" means here. An unconfigured identity counts as foreign,
because nothing says the two are the same person. This is a
conservative heuristic about who last committed one file, not proof
of authorship, and the record says so.

The predicate takes a git RUNNER rather than a path because the two
lanes ask different repositories: the dashboard asks the container's
checkout through the exec seam, the CLI's ``--repo`` asks the host
checkout through the host runner. Both hand in a callable that runs
``git <arguments>`` in the right repository with the hardening lists
already applied and returns ``(iExitCode, sOutput)``.
"""

__all__ = [
    "S_RECORD_KIND_ATTESTATION",
    "S_RECORD_KIND_REPRODUCTION",
    "S_REPRODUCTION_RECORD_NOTE",
    "RecordKindUndeterminedError",
    "S_RECORD_KIND_UNDETERMINED",
    "fbRepositoryCarriesForeignAttestation",
    "fdictBuildReproductionRecord",
    "fdictLatestReproductionRecord",
    "fdictReadBaselineEvidence",
    "flistReadReproductionRecords",
    "flistWriteVerificationOutcome",
    "fsRecordKindForRepository",
    "fsWriteReproductionRecord",
]

import json
import posixpath

from vaibify.reproducibility import imageArchive
from vaibify.reproducibility import gitEvidence
from vaibify.reproducibility.gitEvidence import (
    RecordKindUndeterminedError,
    fbRepositoryCarriesForeignTrackedFile,
)
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.l3Attestation import (
    S_ATTESTATION_FILENAME,
    S_STATUS_FAILED,
    S_STATUS_PASSED,
    _fsSanitizeTimestamp,
    fnWriteAttestation,
    fsCurrentTimestampUtc,
    fsReproducedManifestHistoryPath,
    fsWriteReproducedManifest,
)
from vaibify.reproducibility.manifestWriter import S_REPRODUCTIONS_DIR
from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)


S_RECORD_KIND_ATTESTATION = "attestation"
S_RECORD_KIND_REPRODUCTION = "reproduction"

# Written into every reproduction record, because the reader of a
# file named "reproduction" in a clone that also holds an attestation
# deserves to be told why it is not the other.
S_REPRODUCTION_RECORD_NOTE = (
    "recorded as a reproduction because the attestation was last "
    "committed by another identity"
)

_S_ATTESTATION_RELATIVE_PATH = ".vaibify/" + S_ATTESTATION_FILENAME


S_RECORD_KIND_UNDETERMINED = "undetermined"


def fbRepositoryCarriesForeignAttestation(ftRunGit):
    """True iff HEAD tracks an attestation last committed by another identity.

    ``ftRunGit(listArguments)`` runs one git command in the repository
    that will RECEIVE the record and returns ``(iExitCode, sOutput)``.
    An attestation absent from HEAD -- untracked, or not yet committed
    -- is nobody else's, so the answer is False and the ordinary
    attestation is written. Every question is settled by git's own
    exit code, measured 2026-09-12: ``rev-parse --verify --quiet HEAD``
    exits 1 with no commits and 128 outside a repository; ``ls-tree``
    exits 0 with empty output for an untracked path and 128 when HEAD
    is unusable; ``config`` exits 1 for an unset key. Any other
    answer, and a runner that raises, is UNDETERMINED and refused --
    a broken git must never decide by its silence.
    """
    return fbRepositoryCarriesForeignTrackedFile(
        ftRunGit, _S_ATTESTATION_RELATIVE_PATH, "the attestation",
    )


def fsRecordKindForRepository(ftRunGit):
    """Return which record a verification of this repository would write."""
    if fbRepositoryCarriesForeignAttestation(ftRunGit):
        return S_RECORD_KIND_REPRODUCTION
    return S_RECORD_KIND_ATTESTATION


def flistWriteVerificationOutcome(
    filesRepo, sRecordKind, dictOutcome, fDurationSeconds, dictWorkflow,
    fdictBuildAttestationAt,
):
    """Write a verification's files in the only safe order; return their paths.

    The reproduced manifest lands first -- its timestamped copy, then
    the root ``REPRODUCED.sha256`` -- and only THEN the record that
    names it, so a record can never point at a manifest that was not
    written. Which record is decided by ``sRecordKind``: the author's
    attestation, built by the lane through ``fdictBuildAttestationAt(
    sTimestampUtc, sReproducedManifestPath)``, or a reproduction record
    in the report schema for a clone whose attestation is somebody
    else's. Both lanes -- the dashboard and ``vaibify reproduce
    --rerun`` -- write through here, because they write the same files
    and two writers of one file drift.

    Returns every repo-relative path written, for the commit that
    follows. Raises ``OSError`` as the adapters do; the lane decides
    what a failed write means to its caller.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    # Read BEFORE anything is written: HEAD as it stands now is the
    # baseline the exported tree is compared with, and the files this
    # call writes would otherwise show up as differences from it.
    dictBaseline = (
        fdictReadBaselineEvidence(filesRepo)
        if sRecordKind == S_RECORD_KIND_REPRODUCTION else None
    )
    sStatus = S_STATUS_PASSED if dictOutcome.get("bPassed") else S_STATUS_FAILED
    sTimestampUtc = fsCurrentTimestampUtc()
    sReproducedPath = fsWriteReproducedManifest(
        filesRepo, dictOutcome.get("listFileOutcomes") or [],
        sTimestampUtc, sStatus,
    )
    listPaths = []
    if sReproducedPath:
        listPaths.append(fsReproducedManifestHistoryPath(sTimestampUtc, sStatus))
        listPaths.append(sReproducedPath)
    if sRecordKind == S_RECORD_KIND_REPRODUCTION:
        dictRecord = fdictBuildReproductionRecord(
            dictOutcome, fdictReadEnvironmentJson(filesRepo) or {},
            dictWorkflow, fsRepoRootOf(filesRepo), fDurationSeconds,
            fsReproducedManifestHistoryPath(sTimestampUtc, sStatus)
            if sReproducedPath else None,
            dictBaseline,
        )
        listPaths.append(
            fsWriteReproductionRecord(filesRepo, dictRecord, sTimestampUtc),
        )
        return listPaths
    fnWriteAttestation(
        filesRepo,
        fdictBuildAttestationAt(sTimestampUtc, sReproducedPath or None),
    )
    listPaths.append(_S_ATTESTATION_RELATIVE_PATH)
    return listPaths


def fdictReadBaselineEvidence(filesRepo):
    """Return what HEAD says about the tree a reproduction exported.

    ``sResolvedCommit`` is HEAD as the BASELINE, and
    ``listPathsDifferingFromBaseline`` the tracked paths whose bytes
    differ from it plus the untracked ones: empty means the tree was
    clean. ``sManifestOwnershipAtRun`` is the answer git gave about
    whose manifest this is, kept on the record because the file-status
    poll cannot run git and must not guess. A git that cannot answer
    leaves ``bBaselineKnown`` False -- never an empty list that would
    read as a clean tree.
    """
    ftRunGit = gitEvidence.ffnBuildGitRunnerForRepoFiles(filesRepo)
    dictEvidence = {
        "sResolvedCommit": "", "listPathsDifferingFromBaseline": None,
        "bBaselineKnown": False,
        "sManifestOwnershipAtRun": gitEvidence.fsManifestOwnershipForRepoFiles(
            filesRepo),
    }
    try:
        iHeadCode, sHead = ftRunGit(["rev-parse", "--verify", "--quiet", "HEAD"])
        iDiffCode, sDiffering = ftRunGit(
            ["-c", "core.quotepath=off", "diff", "--name-only", "HEAD"])
        iOtherCode, sUntracked = ftRunGit(
            ["-c", "core.quotepath=off", "ls-files", "--others",
             "--exclude-standard"])
    except Exception as error:  # noqa: BLE001 -- a git that cannot answer is unknown
        gitEvidence.fnReRaiseControlPlaneRefusal(error)
        return dictEvidence
    if iHeadCode != 0 or iDiffCode != 0 or iOtherCode != 0:
        return dictEvidence
    dictEvidence.update({
        "sResolvedCommit": (sHead or "").strip(),
        "listPathsDifferingFromBaseline": sorted(set(
            (sDiffering or "").split("\n") + (sUntracked or "").split("\n")
        ) - {""}),
        "bBaselineKnown": True,
    })
    return dictEvidence


def fdictBuildReproductionRecord(
    dictOutcome, dictEnvironment, dictWorkflow, sRepositoryRoot,
    fDurationSeconds, sReproducedManifestPath, dictBaseline=None,
):
    """Return a reproduction record for a verification of one's own clone.

    The report schema, filled from what this lane knows: the source is
    the checkout itself (its basename only -- a record committed into
    a repository carries no host path), the acquisition facts come
    from the outcome's provenance block, and the archive re-check is
    the one the lane already computed.
    """
    dictProvenance = dictOutcome.get("dictReproductionProvenance") or {}
    dictContainer = (dictEnvironment or {}).get("dictContainer") or {}
    dictDeposit = imageArchive.fdictReadArchiveRecord(dictEnvironment) or {}
    sPlatform = str(dictProvenance.get("sPlatform") or "")
    dictSource = {
        "sKind": "checkout",
        "sRepositoryName": posixpath.basename(
            str(sRepositoryRoot or "").rstrip("/"),
        ),
        "sResolvedCommit": "",
        "sRemoteUrl": "",
        "sWorkflowName": str((dictWorkflow or {}).get("sWorkflowName") or ""),
        "sWorkflowPath": "",
        "sPinnedImageReference": (
            str(dictProvenance.get("sImageDigestPinned") or "")
            or str(dictContainer.get("sImageDigest") or "")
        ),
        "sRequiredArchitecture": str(dictContainer.get("sArchitecture") or ""),
        "bDepositOnRecord": bool(dictDeposit),
        "sDepositVersionDoi": str(dictDeposit.get("sVersionDoi") or ""),
    }
    dictAcquired = {
        "sRequiredPlatform": sPlatform,
        "sObtainedPlatform": sPlatform,
        "sDaemonArchitecture": "",
        "bEmulated": bool(dictProvenance.get("bEmulated")),
        "sObtainedFrom": str(dictProvenance.get("sObtainedFrom") or ""),
        "sImageReference": str(dictProvenance.get("sImageReference") or ""),
        "listAttempts": [],
    }
    dictRecheck = dict(dictOutcome.get("dictImageArchiveCheck") or {})
    dictRecheck["bVacuous"] = (
        dictRecheck.get("sVerdict") == imageArchive.S_RECHECK_VACUOUS
    )
    dictRecord = reproductionReport.fdictBuildReproductionReport(
        dictSource, dictAcquired, dictOutcome, dictRecheck, fDurationSeconds,
    )
    dictRecord["sReproducedManifestPath"] = sReproducedManifestPath
    _fnBindEvidence(dictRecord, dictOutcome, dictBaseline or {})
    return dictRecord


def _fnBindEvidence(dictRecord, dictOutcome, dictBaseline):
    """Write onto a record the evidence that makes its verdict checkable.

    Every digest comes from the EXPORTED SNAPSHOT the rerun froze
    before any step ran, never from a file read afterwards; the
    baseline comes from HEAD read before the record's own commit.
    """
    dictRecord["dictSource"]["sResolvedCommit"] = str(
        dictBaseline.get("sResolvedCommit") or "")
    dictRecord["dictSource"]["sWorkflowPath"] = str(
        dictOutcome.get("sWorkflowRelativePath") or "")
    dictRecord["sWorkflowRelativePath"] = str(
        dictOutcome.get("sWorkflowRelativePath") or "")
    dictRecord["sWorkflowDigest"] = str(
        dictOutcome.get("sWorkflowDigest") or "")
    dictRecord["listPathsDifferingFromBaseline"] = dictBaseline.get(
        "listPathsDifferingFromBaseline")
    dictRecord["bBaselineKnown"] = bool(dictBaseline.get("bBaselineKnown"))
    dictRecord["sManifestOwnershipAtRun"] = str(
        dictBaseline.get("sManifestOwnershipAtRun") or "")


def fsWriteReproductionRecord(filesRepo, dictRecord, sTimestampUtc):
    """Write one reproduction record; return its repo-relative path.

    ``dictRecord`` is a reproduction report
    (:func:`~vaibify.reproducibility.reproductionReport.fdictBuildReproductionReport`)
    with ``sReproducedManifestPath`` already naming the manifest
    written beside it; the caller writes that manifest first. The
    filename carries the same sanitized timestamp as the manifest
    copy and the verdict, so the two sort together.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictStamped = dict(dictRecord)
    dictStamped["sRecordedNote"] = S_REPRODUCTION_RECORD_NOTE
    sRelPath = posixpath.join(
        S_REPRODUCTIONS_DIR,
        _fsSanitizeTimestamp(sTimestampUtc)
        + "_" + str(dictStamped.get("sVerdict") or "unknown") + ".json",
    )
    filesRepo.fnWriteJsonAtomic(sRelPath, dictStamped)
    return sRelPath


def flistReadReproductionRecords(filesRepo):
    """Return every reproduction record in the clone, newest first.

    Enumerated through ``fdictReadDirJsonContents`` exactly as the
    attestation history is, and for the same reason it is called from
    the attestation GET only: the file-status poll's snapshot adapter
    cannot enumerate a directory, and a gate that tried would 500 the
    poll.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictContents = filesRepo.fdictReadDirJsonContents(S_REPRODUCTIONS_DIR)
    listRecords = []
    for sFilename in sorted(dictContents, reverse=True):
        try:
            dictPayload = json.loads(dictContents[sFilename])
        except ValueError:
            continue
        if isinstance(dictPayload, dict):
            listRecords.append(dictPayload)
    return listRecords


def fdictLatestReproductionRecord(filesRepo):
    """Return the newest reproduction record, or ``None``."""
    listRecords = flistReadReproductionRecords(filesRepo)
    return listRecords[0] if listRecords else None
