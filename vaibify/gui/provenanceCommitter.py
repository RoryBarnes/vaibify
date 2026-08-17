"""Commit remote-data provenance during the run, as record units.

Spec §4.5: a pull's provenance used to live in memory until run-end
(and since slice 1's state-only completion, went nowhere at all), so a
crash left remote data on disk with no record of its origin. This
module commits each step's refreshed records into the CURRENT on-disk
``project.json`` immediately after the step executes — never the run's
dispatch-time snapshot — with the record as the conflict unit:
``sSha256`` and its timestamp install together into a record whose
declared fields still match the declaration the run pulled under, and
a record edited or removed mid-run is refused rather than guessed at.
Refusals are per record and never auto-retried; the runner reports
them visibly and the run continues, degraded (ruling R3).

The commit is deliberately synchronous on the event loop: route saves
run there too, so the load-merge-save below cannot interleave with a
GUI save in-process, and a GUI save whose read predates this commit is
answered by the slice-3 compare-and-swap (its acknowledged fingerprint
no longer matches the moved record). The cross-process window (a CLI
writer between this read and write) is the accepted remainder stated
in spec §4.1.

The save goes through ``dictCtx["save"]`` — the same seam every route
save uses — so the self-write baseline and the exact-source
fingerprint move with the file and the run's own commit can never trip
the slice-3 dispatch-freshness gate or the reload detector (the §4.4
"run conflicts with itself" trap).
"""

import hashlib
import logging
import posixpath

from . import workflowManager
from .pipelineUtils import fsDescribeRemoteDataPathConflict
from .workflowMigrations import S_DIGEST_TIMESTAMP_KEY

logger = logging.getLogger("vaibify")

__all__ = [
    "ffnBuildProvenanceCommitter",
    "fdictCommitRemoteDataRecords",
]


def ffnBuildProvenanceCommitter(dictCtx, sContainerId):
    """Return the run-scoped committer the runner threads per step.

    Bound to the live context, not a workflow snapshot: the whole
    point of committing during the run is that the target document is
    whatever project.json says NOW.
    """
    def fdictCommitBoundRecords(sStepId, listRunRecords):
        return fdictCommitRemoteDataRecords(
            dictCtx, sContainerId, sStepId, listRunRecords,
        )
    return fdictCommitBoundRecords


def _fdictRefuseAll(sReason, listRunRecords):
    """Refuse every record for one shared, named reason."""
    return {
        "bCommitted": False,
        "sDetail": sReason,
        "iInstalled": 0,
        "listRefusals": [
            {"sPath": dictRecord.get("sPath", ""), "sReason": sReason}
            for dictRecord in listRunRecords
            if isinstance(dictRecord, dict)
        ],
    }


def fdictCommitRemoteDataRecords(
    dictCtx, sContainerId, sStepId, listRunRecords,
):
    """Merge one step's pulled digests into the current project.json.

    Returns ``{"bCommitted", "sDetail", "iInstalled", "listRefusals"}``.
    ``bCommitted`` means the merge ran to completion against the
    current document and any change was saved — it is True even when
    every record was already current. Per-record refusals ride in
    ``listRefusals``; a document-level failure refuses everything with
    one reason. Never raises: this runs inside a live pipeline task
    where an exception would abort the run over metadata (R3 says
    continue, visibly degraded).
    """
    listRecords = [
        dictRecord for dictRecord in (listRunRecords or [])
        if isinstance(dictRecord, dict)
    ]
    if not listRecords:
        return {
            "bCommitted": True, "sDetail": "",
            "iInstalled": 0, "listRefusals": [],
        }
    try:
        sWorkflowPath = dictCtx["paths"].get(sContainerId, "")
        if not sWorkflowPath:
            return _fdictRefuseAll(
                "no project is open in this session", listRecords,
            )
        dictWorkflow, sFreshError = _ftEnsureCacheMatchesDisk(
            dictCtx, sContainerId, sWorkflowPath,
        )
        if sFreshError:
            return _fdictRefuseAll(sFreshError, listRecords)
        sIdentityConflict = fsDescribeRemoteDataPathConflict(
            dictWorkflow,
        )
        if sIdentityConflict:
            return _fdictRefuseAll(sIdentityConflict, listRecords)
        dictStep = _fdictFindStepById(dictWorkflow, sStepId)
        if dictStep is None:
            return _fdictRefuseAll(
                f"step '{sStepId}' is no longer in the project; the "
                "pulled digests have no record to attach to",
                listRecords,
            )
        bChanged, iInstalled, listRefusals = _ftMergeRecordUnits(
            dictStep, listRecords,
        )
        if bChanged:
            dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bCommitted": True,
            "sDetail": "",
            "iInstalled": iInstalled,
            "listRefusals": listRefusals,
        }
    except Exception as errorCommit:
        logger.error(
            "Remote-data provenance commit failed for step %s: %s",
            sStepId, errorCommit, exc_info=True,
        )
        return _fdictRefuseAll(
            f"provenance commit failed: {errorCommit}", listRecords,
        )


def _ftEnsureCacheMatchesDisk(dictCtx, sContainerId, sWorkflowPath):
    """Return ``(dictWorkflow, sError)`` with the cache current on disk.

    A mid-run out-of-band edit is ACCEPTED first, through the same
    reload the dispatch-freshness gate uses, and the records then
    merge into the accepted document — the researcher's edit survives
    and the provenance still lands. A document that cannot be read or
    reloaded refuses the commit instead; the marker stays set and
    reconciliation owns the recovery.
    """
    try:
        baDiskBytes = dictCtx["docker"].fbaFetchFile(
            sContainerId, sWorkflowPath,
        )
    except Exception as errorRead:
        return None, (
            f"project.json could not be read for the provenance "
            f"commit ({errorRead})"
        )
    sDiskFingerprint = hashlib.sha256(baDiskBytes).hexdigest()
    dictCache = dictCtx["workflows"].get(sContainerId)
    if dictCache is not None and (
        dictCache.get("_sSourceFingerprint") == sDiskFingerprint
    ):
        return dictCache, ""
    from .workflowReloadDetector import fdictMaybeReloadWorkflow
    fdictMaybeReloadWorkflow(
        dictCtx, sContainerId, sWorkflowPath,
        {sWorkflowPath: "present"},
        sPolledFingerprint=sDiskFingerprint,
    )
    dictLive = dictCtx["workflows"].get(sContainerId)
    if dictLive is None or (
        dictLive.get("_sSourceFingerprint") != sDiskFingerprint
    ):
        return None, (
            "project.json changed during the run and could not be "
            "reloaded; the pulled digests were not committed"
        )
    return dictLive, ""


def _fdictFindStepById(dictWorkflow, sStepId):
    """Return the step dict carrying ``sStepId``, or None."""
    if not sStepId:
        return None
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if isinstance(dictStep, dict) and (
            dictStep.get("sStepId") == sStepId
        ):
            return dictStep
    return None


def _fbDeclarationsDiffer(dictRunRecord, dictDiskRecord):
    """True when the two records disagree outside run-produced fields.

    The run's digest was pulled under the dispatch-time declaration;
    installing it into a record whose declaration has since moved
    would MANUFACTURE an internally-consistent false record — the
    run's hash under the researcher's new ``sSourceUrl`` — with no
    symptom. The record is the conflict unit (§4.5).
    """
    def fdictDeclaredFields(dictRecord):
        return {
            sKey: dictRecord[sKey] for sKey in dictRecord
            if sKey not in
            workflowManager.T_REMOTE_DATA_RUN_PRODUCED_FIELDS
        }
    return fdictDeclaredFields(dictRunRecord) != fdictDeclaredFields(
        dictDiskRecord,
    )


def _ftMergeRecordUnits(dictStep, listRunRecords):
    """Install fresh digests record-by-record; return the outcome.

    Returns ``(bChanged, iInstalled, listRefusals)``. A record with no
    fresh digest is skipped silently — the hash step already declined
    to guess, and the marker (not this merge) accounts for it. The
    digest and its timestamp install together, never independently.
    """
    dictDiskByPath = {}
    for dictDiskRecord in dictStep.get("listRemoteData", []) or []:
        if not isinstance(dictDiskRecord, dict):
            continue
        sPath = dictDiskRecord.get("sPath", "")
        if isinstance(sPath, str) and sPath:
            dictDiskByPath[posixpath.normpath(sPath)] = dictDiskRecord
    bChanged = False
    iInstalled = 0
    listRefusals = []
    for dictRunRecord in listRunRecords:
        sPath = dictRunRecord.get("sPath", "")
        if not isinstance(sPath, str) or not sPath:
            continue
        sSha256 = dictRunRecord.get("sSha256", "")
        sTimestamp = dictRunRecord.get(S_DIGEST_TIMESTAMP_KEY, "")
        if not sSha256:
            continue
        dictDiskRecord = dictDiskByPath.get(posixpath.normpath(sPath))
        if dictDiskRecord is None:
            listRefusals.append({
                "sPath": sPath,
                "sReason": (
                    "the record was removed from the step while it "
                    "ran; the pulled digest has nothing to attach to"
                ),
            })
            continue
        if _fbDeclarationsDiffer(dictRunRecord, dictDiskRecord):
            listRefusals.append({
                "sPath": sPath,
                "sReason": (
                    "the record's declaration changed while the step "
                    "ran; installing the pulled digest under the new "
                    "declaration would manufacture a false record"
                ),
            })
            continue
        if dictDiskRecord.get("sSha256") == sSha256 and (
            dictDiskRecord.get(S_DIGEST_TIMESTAMP_KEY) == sTimestamp
        ):
            continue
        dictDiskRecord["sSha256"] = sSha256
        dictDiskRecord[S_DIGEST_TIMESTAMP_KEY] = sTimestamp
        bChanged = True
        iInstalled += 1
    return bChanged, iInstalled, listRefusals
