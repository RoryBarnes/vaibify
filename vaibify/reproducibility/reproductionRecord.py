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
    "flistReadReproductionRecords",
    "flistWriteVerificationOutcome",
    "fsRecordKindForRepository",
    "fsWriteReproductionRecord",
]

import json
import posixpath

from vaibify.reproducibility import imageArchive
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


class RecordKindUndeterminedError(Exception):
    """Git could not say whose attestation the repository carries.

    Raised instead of answering, because either answer written on a
    guess is wrong in a way that cannot be undone: an attestation
    would overwrite somebody else's tracked claim, and a reproduction
    record would file the author's own verification as a stranger's.
    Both lanes refuse the write -- before any step runs, where they
    can.
    """


def _ftAskGit(ftRunGit, listArguments, sQuestion):
    """Run one git question; a runner that cannot run it is undetermined."""
    try:
        iExitCode, sOutput = ftRunGit(listArguments)
    except Exception as error:  # noqa: BLE001 -- turned into a refusal
        raise RecordKindUndeterminedError(
            f"git could not be asked {sQuestion}: {error}"
        ) from error
    return int(iExitCode), (sOutput or "").strip()


def _fbRepositoryHasACommit(ftRunGit):
    """True iff HEAD names a commit; False for an initialised, empty repository."""
    iExitCode, _sHead = _ftAskGit(
        ftRunGit, ["rev-parse", "--verify", "--quiet", "HEAD"],
        "whether the repository has a commit",
    )
    if iExitCode == 0:
        return True
    # Exit 1 is git's own "no such revision" -- but a runner that
    # chains ``cd`` before git exits 1 for a missing directory too, so
    # the repository is asked to confirm it is one before an empty
    # history is believed.
    iExitCode, sInside = _ftAskGit(
        ftRunGit, ["rev-parse", "--is-inside-work-tree"],
        "whether the directory is a repository",
    )
    if iExitCode == 0 and sInside == "true":
        return False
    raise RecordKindUndeterminedError(
        "the repository that would receive the record could not be "
        f"read by git (exit {iExitCode})"
    )


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
    if not _fbRepositoryHasACommit(ftRunGit):
        return False
    iExitCode, sTracked = _ftAskGit(
        ftRunGit, ["ls-tree", "--name-only", "HEAD", "--", _S_ATTESTATION_RELATIVE_PATH],
        "whether the attestation is tracked",
    )
    if iExitCode != 0:
        raise RecordKindUndeterminedError(
            f"git could not list HEAD to see whether the attestation is "
            f"tracked (exit {iExitCode})"
        )
    if not sTracked:
        return False
    iExitCode, sCommitter = _ftAskGit(
        ftRunGit, ["log", "-1", "--format=%ce", "--", _S_ATTESTATION_RELATIVE_PATH],
        "who committed the attestation",
    )
    if iExitCode != 0 or not sCommitter:
        raise RecordKindUndeterminedError(
            "the attestation is tracked at HEAD but git could not say who "
            f"committed it (exit {iExitCode})"
        )
    iExitCode, sOwnEmail = _ftAskGit(
        ftRunGit, ["config", "user.email"], "for the receiving identity",
    )
    if iExitCode == 1 or (iExitCode == 0 and not sOwnEmail):
        return True
    if iExitCode != 0:
        raise RecordKindUndeterminedError(
            f"git could not read the receiving identity (exit {iExitCode})"
        )
    return sCommitter.lower() != sOwnEmail.lower()


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
            sReproducedPath or None,
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


def fdictBuildReproductionRecord(
    dictOutcome, dictEnvironment, dictWorkflow, sRepositoryRoot,
    fDurationSeconds, sReproducedManifestPath,
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
    return dictRecord


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
