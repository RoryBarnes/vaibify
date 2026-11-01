"""The PROOF receipt: one observation of Level 2, archived with the deposit.

A Zenodo deposit is a checkpoint, and the only copy with a preservation
commitment, so it carries the evidence of what was met when it was
made. That evidence is ONE observation: every verdict, requirement and
piece of evidence the receipt records is evaluated at one pinned
instant (``levelGates.fcontextPinnedEvaluation``), over one set of
inputs, and the receipt says which:

- ``one-snapshot`` -- every gate read was answered by one container
  exec's snapshot of the repository, so the reads cannot disagree;
- ``live-reads`` -- the snapshot could not be built, or did not sample
  something a gate read, or this is a host project. Each read is then
  recorded and asked again at the end; a read whose answer changed
  makes the observation INCOHERENT and names the path.

An observation is never a mixture of the two. Only a coherent one may
authorize a completion; a checkpoint carries whatever was seen, labeled.

The receipt document is built from an observation by pure builders --
no clock, no I/O -- and serialized once, so one observation always
gives the same bytes. Its schema (version 1) is permanent: it is
archived. Every catalog entry appears, required or not; requirement
states come from the gate's own answers through the catalog, and each
authority's own answer is kept verbatim as evidence. The deposit that
RECEIVES the receipt (``dictArchive``) is kept apart from the records
the evidence is about (``dictEvidence``).
"""

import hashlib
import json
from datetime import datetime, timezone

from .publicationScope import S_PROOF_RECEIPT_REPO_PATH

__all__ = [
    "I_PROOF_RECEIPT_SCHEMA_VERSION",
    "S_INPUTS_LIVE_READS",
    "S_INPUTS_ONE_SNAPSHOT",
    "S_PROOF_RECEIPT_REPO_PATH",
    "S_RECEIPT_KIND_CHECKPOINT",
    "S_RECEIPT_KIND_COMPLETION",
    "RecordingRepoFiles",
    "S_RECEIPT_DEPOSIT_KEY",
    "fdictBuildCompletes",
    "fdictBuildProofReceipt",
    "fdictDescribeArchiveReceiptState",
    "fdictDescribePublishedReceipt",
    "fdictCaptureObservation",
    "fsSerializeProofReceipt",
]


S_INPUTS_ONE_SNAPSHOT = "one-snapshot"
S_INPUTS_LIVE_READS = "live-reads"
_S_REASON_SNAPSHOT_MISSED = (
    "The repository snapshot did not sample a file the gates read, so "
    "vaibify read every file from the project one by one instead."
)
_T_RECORDED_SERVICES = ("github", "zenodo", "overleaf")


class RecordingRepoFiles:
    """Delegate every adapter method; record each READ and its answer's digest.

    The read methods are the adapter's (``repoFiles.HostRepoFiles`` and
    ``ContainerRepoFiles``): anything else -- the root, a write, a lock --
    passes straight through. A read the inner adapter could not answer
    because a snapshot did not sample it (``KeyError``, or
    ``NotImplementedError`` for a directory) is flagged, so a gate that
    swallows it still cannot turn a snapshot capture into a mixture.
    """

    TUPLE_READ_METHODS = (
        "fbIsFile", "fbIsDir", "fsReadText", "fbaReadBytes",
        "flistListJsonFilenames", "fdictReadDirJsonContents",
        "fdictStatMtimes", "fdictHashFiles", "fdictHashAbsolutePaths",
    )

    def __init__(self, filesInner):
        self._filesInner = filesInner
        self.dictReads = {}
        self.setChangedReads = set()
        self.bMissedSample = False

    def __getattr__(self, sName):
        genericAttribute = getattr(self._filesInner, sName)
        if sName not in self.TUPLE_READ_METHODS:
            return genericAttribute
        return lambda *listArgs: self._fgenericRecordRead(
            sName, genericAttribute, listArgs)

    def _fgenericRecordRead(self, sMethod, fnRead, listArgs):
        """Answer one read through the inner adapter, recording its digest."""
        tKey = (sMethod, json.dumps(listArgs, sort_keys=True, default=str))
        try:
            genericAnswer = fnRead(*listArgs)
        except (KeyError, NotImplementedError):
            self.bMissedSample = True
            raise
        except Exception as error:
            self._fnNoteAnswer(
                tKey, fnRead, listArgs, _ftDescribeRaised(error))
            raise
        self._fnNoteAnswer(tKey, fnRead, listArgs, genericAnswer)
        return genericAnswer

    def _fnNoteAnswer(self, tKey, fnRead, listArgs, genericAnswer):
        """Keep a read's first answer; a different later one is a change."""
        if tKey in self.dictReads:
            genericFirst = self.dictReads[tKey][2]
            if _fsDigestAnswer(genericFirst) != _fsDigestAnswer(genericAnswer):
                self.setChangedReads.update(
                    _flistPathsThatChanged(
                        listArgs, genericFirst, genericAnswer))
            return
        self.dictReads[tKey] = (fnRead, listArgs, genericAnswer)

    def flistReplayChangedReads(self):
        """Ask every recorded read again; return paths whose answer moved."""
        setChanged = set(self.setChangedReads)
        for fnRead, listArgs, genericFirst in list(self.dictReads.values()):
            try:
                genericAgain = fnRead(*listArgs)
            except Exception as error:  # noqa: BLE001 -- an answer, compared
                genericAgain = _ftDescribeRaised(error)
            if _fsDigestAnswer(genericAgain) != _fsDigestAnswer(genericFirst):
                setChanged.update(
                    _flistPathsThatChanged(
                        listArgs, genericFirst, genericAgain))
        return sorted(setChanged)


def _ftDescribeRaised(error):
    """Return a raised read as a comparable answer."""
    return ("raised", type(error).__name__, str(error))


def _fsDigestAnswer(genericAnswer):
    """Return a SHA-256 of one read's answer, bytes or structure."""
    if isinstance(genericAnswer, (bytes, bytearray)):
        return hashlib.sha256(bytes(genericAnswer)).hexdigest()
    sCanonical = json.dumps(genericAnswer, sort_keys=True, default=str)
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _flistPathsThatChanged(listArgs, genericFirst, genericAgain):
    """Name the paths behind one read whose two answers differ."""
    if isinstance(genericFirst, dict) and isinstance(genericAgain, dict):
        return sorted(
            str(sKey) for sKey in set(genericFirst) | set(genericAgain)
            if _fsDigestAnswer(genericFirst.get(sKey))
            != _fsDigestAnswer(genericAgain.get(sKey))
        )
    genericSubject = listArgs[0] if listArgs else ""
    if isinstance(genericSubject, (list, tuple)):
        return [str(sPath) for sPath in genericSubject]
    return [str(genericSubject)]


# ── The capture ──────────────────────────────────────────────────────


def fdictCaptureObservation(dictCtx, sContainerId, dictWorkflow):
    """Return ONE observation of this project's Level 2, read at one instant.

    Reads the clock once, chooses the inputs (the one-exec snapshot when
    it can be built, else live reads with the reason), and evaluates the
    gate, the gaps, both authorities per service, the blockers, the
    header cell, the verify records and every arXiv answer once, under
    the pin. A snapshot that did not sample a read discards the partial
    work and the whole evaluation is repeated over live reads; live
    reads are then replayed to prove the observation coherent.
    """
    datetimeEvaluatedAt = datetime.now(timezone.utc).replace(microsecond=0)
    filesSnapshot, sInputsReason = _ftChooseSnapshot(
        dictCtx, sContainerId, dictWorkflow)
    if filesSnapshot is not None:
        dictEvaluation = _fdictEvaluateSnapshotOrNone(
            dictWorkflow, filesSnapshot, datetimeEvaluatedAt)
        if dictEvaluation is not None:
            return _fdictAssembleObservation(
                datetimeEvaluatedAt, S_INPUTS_ONE_SNAPSHOT, "", [],
                dictEvaluation, _fdictReadWorkflowIdentity(
                    dictCtx, sContainerId, dictWorkflow))
        sInputsReason = _S_REASON_SNAPSHOT_MISSED
    return _fdictCaptureOverLiveReads(
        dictCtx, sContainerId, dictWorkflow, datetimeEvaluatedAt,
        sInputsReason)


def _fdictEvaluateSnapshotOrNone(
    dictWorkflow, filesSnapshot, datetimeEvaluatedAt,
):
    """Return the evaluation over the snapshot, or None when it missed a read.

    A miss a gate swallowed counts as much as one that propagated: the
    evaluation it produced answered some question with no data.
    """
    filesRecorder = RecordingRepoFiles(filesSnapshot)
    try:
        dictEvaluation = _fdictEvaluateUnderPin(
            dictWorkflow, filesRecorder, datetimeEvaluatedAt)
    except (KeyError, NotImplementedError):
        if not filesRecorder.bMissedSample:
            raise
        return None
    return None if filesRecorder.bMissedSample else dictEvaluation


def _fdictCaptureOverLiveReads(
    dictCtx, sContainerId, dictWorkflow, datetimeEvaluatedAt, sInputsReason,
):
    """Evaluate over recorded live reads, then replay every read."""
    from vaibify.gui.routeContext import ffilesForWorkflow
    filesRecorder = RecordingRepoFiles(
        ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow))
    dictEvaluation = _fdictEvaluateUnderPin(
        dictWorkflow, filesRecorder, datetimeEvaluatedAt)
    dictIdentity = _fdictReadWorkflowIdentity(
        dictCtx, sContainerId, dictWorkflow, filesRecorder)
    listChangedReads = filesRecorder.flistReplayChangedReads()
    return _fdictAssembleObservation(
        datetimeEvaluatedAt, S_INPUTS_LIVE_READS, sInputsReason,
        listChangedReads, dictEvaluation, dictIdentity)


def _ftChooseSnapshot(dictCtx, sContainerId, dictWorkflow):
    """Return ``(snapshot, "")`` or ``(None, why the reads go live)``.

    ``ffilesSnapshotForWorkflow`` falls back to the live adapter with
    only a log line; here the fallback is detected and named, because
    the receipt must say what it read.
    """
    from vaibify.config.registryManager import fbIsHostProject
    from vaibify.gui.routeContext import ffilesSnapshotForWorkflow
    from .repoFiles import SnapshotRepoFiles
    if fbIsHostProject(sContainerId):
        return None, (
            "A host project is read from disk one file at a time; there "
            "is no container snapshot.")
    if not (dictWorkflow or {}).get("sProjectRepoPath") or (
            dictCtx.get("files") is None):
        return None, (
            "No container repository adapter was available, so vaibify "
            "read every file one by one.")
    filesChosen = ffilesSnapshotForWorkflow(
        dictCtx, sContainerId, dictWorkflow)
    if isinstance(filesChosen, SnapshotRepoFiles):
        return filesChosen, ""
    return None, (
        "The one-exec repository snapshot could not be built (the hub "
        "log names why), so vaibify read every file one by one.")


def _fdictEvaluateUnderPin(dictWorkflow, filesRepo, datetimeEvaluatedAt):
    """Evaluate every Level 2 question once, at the pinned instant."""
    from . import levelGates, scheduledReverify
    with levelGates.fcontextPinnedEvaluation(
            datetimeEvaluatedAt) as dictAnswers:
        dictGaps = levelGates.fdictLevel2Gaps(dictWorkflow, filesRepo)
        listBlockers = levelGates.flistLevel2Blockers(dictWorkflow, filesRepo)
        dictRecords = {
            sService: scheduledReverify.fdictReadCachedSyncStatus(
                filesRepo, sService)
            for sService in _T_RECORDED_SERVICES
        }
        dictEvaluation = {
            "dictGaps": dictGaps,
            "listLevel2Blockers": listBlockers,
            "dictHeaderCell": levelGates.fdictComputeWorkflowScopeLevelStates(
                dictWorkflow, listBlockers, [])["s2"],
            "bMarkersHoldLevelZero":
                levelGates._fbRemoteDataMarkersHoldLevelZero(dictWorkflow),
            "dictRecords": dictRecords,
            "dictRecordFresh": {
                sService: levelGates._fbCachedSyncStatusFresh(
                    dictRecords[sService], levelGates.F_MAX_STALE_HOURS)
                for sService in _T_RECORDED_SERVICES
            },
            "dictApplicability": _fdictApplicability(dictWorkflow, filesRepo),
        }
        dictEvaluation["dictManuscript"] = _fdictManuscriptEvidence(
            dictWorkflow, filesRepo, dictAnswers)
    return dictEvaluation


def _fdictApplicability(dictWorkflow, filesRepo):
    """Return which conditional Level 2 requirements apply to this project."""
    from . import levelGates
    from .environmentSnapshot import fdictReadEnvironmentJson
    dictContainer = (fdictReadEnvironmentJson(filesRepo) or {}).get(
        "dictContainer")
    return {
        "always": True,
        "overleaf-bound": levelGates.fbWorkflowHasOverleafBinding(
            dictWorkflow),
        "arxiv-recorded": levelGates.fbWorkflowHasArxivConnection(
            dictWorkflow),
        "image-pinned": isinstance(dictContainer, dict) and bool(
            dictContainer.get("sImageDigest")),
    }


def _fdictManuscriptEvidence(dictWorkflow, filesRepo, dictAnswers):
    """Return the Overleaf push list and every arXiv answer the gates got."""
    from . import levelGates
    from .overleafSync import flistOverleafPushedFiguresAt
    sCommit = levelGates._fsOverleafRecordedCommit(dictWorkflow)
    listPushed = flistOverleafPushedFiguresAt(filesRepo, sCommit) if (
        sCommit) else []
    dictArxiv = levelGates._fdictArxivConfig(dictWorkflow)
    return {
        "sLastPushCommit": sCommit,
        "listPushedFigures": listPushed,
        "sArxivId": dictArxiv.get("sArxivId") or "",
        "sRecordedVersion": dictArxiv.get("sArxivVersion") or "",
        "dictLocalFigureHashes": (levelGates._fdictLiveHashesOrNone(
            filesRepo, listPushed) or {}) if listPushed and dictArxiv.get(
            "sArxivId") else {},
        "listArxivAnswers": _flistDescribeArxivAnswers(dictAnswers),
    }


def _flistDescribeArxivAnswers(dictAnswers):
    """Return the memoized arXiv answers as plain data, errors kept as text."""
    listAnswers = []
    for tQuestion, (bAnswered, genericAnswer) in sorted(
            dictAnswers.items(), key=lambda tItem: json.dumps(
                tItem[0], default=str)):
        listAnswers.append({
            "sQuestion": str(tQuestion[0]),
            "genericAnswer": genericAnswer if bAnswered else None,
            "sError": "" if bAnswered else (
                str(genericAnswer) or "arXiv error"),
        })
    return listAnswers


def _fdictReadWorkflowIdentity(
    dictCtx, sContainerId, dictWorkflow, filesRepo=None,
):
    """Return the definition's path and SHA-256 and the repository HEAD.

    Identity BESIDE the observation, read through the live adapter (the
    snapshot samples neither the definition's bytes nor ``.git``); over
    live reads the recorder passed in also covers them by the replay.
    """
    from vaibify.gui.routeContext import ffilesForWorkflow
    import posixpath
    filesRepo = filesRepo or ffilesForWorkflow(
        dictCtx, sContainerId, dictWorkflow)
    sRepo = (dictWorkflow or {}).get("sProjectRepoPath") or ""
    sDefinition = (dictCtx.get("paths") or {}).get(sContainerId, "") or ""
    if sRepo and sDefinition.startswith(sRepo.rstrip("/") + "/"):
        sDefinition = posixpath.relpath(sDefinition, sRepo)
    return {
        "sName": str((dictWorkflow or {}).get("sWorkflowName")
                     or (dictWorkflow or {}).get("sName") or ""),
        "sDefinitionPath": sDefinition,
        "sDefinitionSha256": _fsHashOrEmpty(filesRepo, sDefinition),
        "sRepositoryHead": _fsReadRepositoryHead(filesRepo),
    }


def _fsHashOrEmpty(filesRepo, sRelPath):
    """Return one file's SHA-256 through the adapter, or ``""``."""
    if not sRelPath or sRelPath.startswith("/"):
        return ""
    try:
        dictEntry = filesRepo.fdictHashFiles([sRelPath]).get(sRelPath) or {}
    except (OSError, ValueError, KeyError):
        return ""
    return str(dictEntry.get("sSha256") or "")


def _fsReadRepositoryHead(filesRepo):
    """Return the commit HEAD names, read as files, or ``""``."""
    try:
        sHead = filesRepo.fsReadText(".git/HEAD").strip()
        if sHead.startswith("ref: "):
            sRef = sHead[len("ref: "):].strip()
            return _fsResolveReference(filesRepo, sRef)
    except (OSError, ValueError, KeyError, NotImplementedError):
        return ""
    return sHead if _fbIsCommitHash(sHead) else ""


def _fsResolveReference(filesRepo, sRef):
    """Return the commit one ref names, loose or packed, or ``""``."""
    if ".." in sRef.split("/") or not sRef.startswith("refs/"):
        return ""
    try:
        sCommit = filesRepo.fsReadText(".git/" + sRef).strip()
        return sCommit if _fbIsCommitHash(sCommit) else ""
    except (OSError, ValueError, KeyError):
        pass
    for sLine in filesRepo.fsReadText(".git/packed-refs").splitlines():
        listParts = sLine.strip().split(" ")
        if len(listParts) == 2 and listParts[1] == sRef and _fbIsCommitHash(
                listParts[0]):
            return listParts[0]
    return ""


def _fbIsCommitHash(sText):
    """Return True for a 40- or 64-character lowercase hex object name."""
    return len(sText) in (40, 64) and all(
        sCharacter in "0123456789abcdef" for sCharacter in sText)


def _fdictAssembleObservation(
    datetimeEvaluatedAt, sInputs, sInputsReason, listChangedReads,
    dictEvaluation, dictWorkflowIdentity,
):
    """Return the observation: its labels, then everything it evaluated."""
    from vaibify import versionIdentity
    from . import levelGates, proofCatalog, publicationScope
    dictGaps = dictEvaluation["dictGaps"]
    bHeldAtZero = dictEvaluation["bMarkersHoldLevelZero"]
    return {
        "sEvaluatedAtUtc": datetimeEvaluatedAt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sInputs": sInputs,
        "sInputsReason": sInputsReason,
        "bCoherent": not listChangedReads,
        "listChangedReads": list(listChangedReads),
        "iPublicationScopeVersion":
            publicationScope.I_PUBLICATION_SCOPE_VERSION,
        "iCriteriaVersion": proofCatalog.I_PROOF_CATALOG_VERSION,
        "fMaxStaleHours": float(levelGates.F_MAX_STALE_HOURS),
        "dictVerdict": {
            "bAtLeastLevel1": bool(dictGaps["bAtLeastLevel1"])
            and not bHeldAtZero,
            "bAtLeastLevel2": bool(dictGaps["bAtLeastLevel2"])
            and not bHeldAtZero,
        },
        "dictWorkflowIdentity": dictWorkflowIdentity,
        "dictVaibify": versionIdentity.fdictDescribeRunningVaibify(),
        **dictEvaluation,
    }


# ── The receipt document (schema version 1) ──────────────────────────

I_PROOF_RECEIPT_SCHEMA_VERSION = 1
# The receipt's key in a deposit, which is flat: the repository copy's
# basename, so a selected file of that name collides and is refused.
S_RECEIPT_DEPOSIT_KEY = "proofReceipt.json"
S_RECEIPT_KIND_CHECKPOINT = "checkpoint"
S_RECEIPT_KIND_COMPLETION = "completion"
S_WHAT_THIS_RECORDS = (
    "vaibify's Level 2 verdicts at the moment in "
    "dictObservation.sEvaluatedAtUtc. A record of what was observed, not "
    "a claim about now.")
S_CIRCUMSTANCE_COMPLETION_ALONE = (
    "Level 2 was met against the declared records named in "
    "dictEvidence.zenodo, and the checkpoint version alone holds every "
    "Level 2 file.")
S_CIRCUMSTANCE_COMPLETION_TOGETHER = (
    "Level 2 was met against the declared records together; the "
    "checkpoint version alone lacks the files listed in "
    "dictCompletes.listCheckpointLacks.")
S_CIRCUMSTANCE_CHECKPOINT_FIRST = (
    "No archive existed before this one. Level 2 requires the files to "
    "match an archive, which this deposit is the first to provide; a "
    "completion version records whether they do.")
S_CIRCUMSTANCE_CHECKPOINT_LATER = (
    "Level 2 was checked against the previously declared records (see "
    "dictEvidence.zenodo). This version is what the next verify compares "
    "the files with.")
_T_OBSERVATION_FIELDS = (
    "sEvaluatedAtUtc", "sInputs", "sInputsReason", "bCoherent",
    "listChangedReads", "iPublicationScopeVersion", "iCriteriaVersion",
    "fMaxStaleHours")
_T_LIST_SORT_KEYS = (
    "sId", "sKey", "sPath", "sRecordId", "sStepLabel", "sQuestion")


def fdictBuildProofReceipt(dictObservation, sKind, dictArchive, dictCompletes):
    """Return the receipt document for one observation; pure, no clock, no I/O.

    ``dictArchive`` describes the deposit RECEIVING the receipt, built by
    the caller from the staged files; ``dictCompletes`` is ``None`` for a
    checkpoint and :func:`fdictBuildCompletes`'s answer for a completion.
    """
    return {
        "iSchemaVersion": I_PROOF_RECEIPT_SCHEMA_VERSION,
        "sKind": sKind,
        "sWhatThisRecords": S_WHAT_THIS_RECORDS,
        "sCircumstance": _fsChooseCircumstance(
            sKind, dictArchive, dictCompletes),
        "dictObservation": {
            sField: dictObservation[sField]
            for sField in _T_OBSERVATION_FIELDS},
        "dictVaibify": dict(dictObservation.get("dictVaibify") or {}),
        "dictWorkflow": dict(dictObservation["dictWorkflowIdentity"]),
        "dictVerdict": dict(dictObservation["dictVerdict"]),
        "listRequirements": _flistBuildRequirements(dictObservation),
        "dictEvidence": _fdictBuildEvidence(dictObservation),
        "dictArchive": _fdictBuildArchiveElement(dictArchive),
        "dictCompletes": None if dictCompletes is None else dict(
            dictCompletes),
    }


def fsSerializeProofReceipt(dictReceipt):
    """Return the receipt's bytes as text: sorted keys, every list sorted."""
    return json.dumps(
        _fgenericSortLists(dictReceipt), indent=2, sort_keys=True) + "\n"


def _fgenericSortLists(genericValue):
    """Return a copy with every list in one deterministic order."""
    if isinstance(genericValue, dict):
        return {sKey: _fgenericSortLists(genericItem)
                for sKey, genericItem in genericValue.items()}
    if not isinstance(genericValue, list):
        return genericValue
    listSorted = [
        _fgenericSortLists(genericItem) for genericItem in genericValue]
    return sorted(listSorted, key=_fsListSortKey)


def _fsListSortKey(genericItem):
    """Order dicts by their identifying field, everything else canonically."""
    if isinstance(genericItem, dict):
        for sKey in _T_LIST_SORT_KEYS:
            if sKey in genericItem:
                return "0" + str(genericItem[sKey])
    return "1" + json.dumps(genericItem, sort_keys=True, default=str)


def _fsChooseCircumstance(sKind, dictArchive, dictCompletes):
    """Return the one sentence that says what this version records."""
    if sKind == S_RECEIPT_KIND_COMPLETION:
        if (dictCompletes or {}).get("bCheckpointAloneSuffices"):
            return S_CIRCUMSTANCE_COMPLETION_ALONE
        return S_CIRCUMSTANCE_COMPLETION_TOGETHER
    if not (dictArchive or {}).get("sFollowsRecordId"):
        return S_CIRCUMSTANCE_CHECKPOINT_FIRST
    return S_CIRCUMSTANCE_CHECKPOINT_LATER


def _flistBuildRequirements(dictObservation):
    """Return every catalog entry, derived from the gate's own answers."""
    from . import proofCatalog
    dictGaps = dict(
        dictObservation["dictGaps"],
        bAtLeastLevel1=dictObservation["dictVerdict"]["bAtLeastLevel1"])
    listRequirements = proofCatalog.flistDeriveLevel2Requirements(
        dictGaps, dictObservation["listLevel2Blockers"],
        dictObservation["dictApplicability"])
    return listRequirements


def _fdictBuildArchiveElement(dictArchive):
    """Return the receiving deposit: where, after what, and what it holds."""
    dictArchive = dictArchive or {}
    return {
        "sEndpoint": str(dictArchive.get("sEndpoint") or ""),
        "sConceptDoi": str(dictArchive.get("sConceptDoi") or ""),
        "sFollowsRecordId": str(dictArchive.get("sFollowsRecordId") or ""),
        "listContents": [
            {sField: dictItem.get(sField, "" if sField != "iSizeBytes" else 0)
             for sField in ("sKey", "sRepoPath", "sSha256", "sMd5",
                            "iSizeBytes")}
            for dictItem in dictArchive.get("listContents") or []
        ],
    }


def _fdictBuildEvidence(dictObservation):
    """Return one evidence entry per applicable service, each its own shape."""
    dictApplicability = dictObservation["dictApplicability"]
    dictEvidence = {
        sService: _fdictBuildRemoteEvidence(dictObservation, sService)
        for sService in ("github", "zenodo")
    }
    if dictApplicability.get("overleaf-bound"):
        dictEvidence["overleaf"] = _fdictBuildOverleafEvidence(dictObservation)
    if dictApplicability.get("arxiv-recorded"):
        dictEvidence["arxiv"] = _fdictBuildArxivEvidence(dictObservation)
    return dictEvidence


def _fdictBuildRemoteEvidence(dictObservation, sService):
    """Return GitHub's or Zenodo's evidence: remote, authorities, files."""
    dictRecord = dictObservation["dictRecords"].get(sService) or {}
    dictGaps = dictObservation["dictGaps"]
    dictAccess = dict(dictGaps["dictPublicAccessStates"][sService])
    dictEvidence = {
        "dictRemote": _fdictRemoteIdentity(dictRecord, sService),
        "sCheckedAtUtc": str(dictRecord.get("sLastVerified") or ""),
        "dictPublicAccess": dictAccess,
        "dictPublishedCopy": dict(
            dictGaps["dictPublishedCopyStates"][sService]),
        "dictComparison": _fdictBuildComparison(dictRecord, sService),
    }
    if sService == "github":
        dictEvidence["sCommittedShaVerified"] = str(
            dictRecord.get("sCommittedShaVerified") or "")
        return dictEvidence
    dictAccess["listRecords"] = [
        {sField: str((dictItem or {}).get(sField) or "")
         for sField in ("sRecordId", "sState", "sReason")}
        for dictItem in (dictRecord.get("dictPublicAccess") or {}).get(
            "listRecords") or []
    ]
    dictEvidence["sDoi"] = str(dictRecord.get("sZenodoDoi") or "")
    return dictEvidence


def _fdictRemoteIdentity(dictRecord, sService):
    """Return the remote a record is about, ``""`` where it says nothing."""
    dictIdentity = dictRecord.get("dictRemoteIdentity") or {}
    if sService == "github":
        return {sField: str(dictIdentity.get(sField) or "")
                for sField in ("sOwner", "sRepo", "sBranch")}
    return {"sEndpoint": str(dictIdentity.get("sEndpoint") or ""),
            "listRecordIds": [str(sId) for sId in dictIdentity.get(
                "listRecordIds") or []]}


def _fdictBuildComparison(dictRecord, sService):
    """Return the per-file Level 2 outcomes the record itself carries."""
    from .publicationScope import fsetSelectLevel2Paths
    dictExpected = dictRecord.get("dictComparedHashes") or {}
    dictDiverged = {
        dictEntry.get("sPath"): dictEntry
        for dictEntry in dictRecord.get("listDiverged") or []
        if isinstance(dictEntry, dict)
    }
    listRecordIds = (dictRecord.get("dictRemoteIdentity") or {}).get(
        "listRecordIds") or []
    listOutcomes = []
    for sPath in sorted(fsetSelectLevel2Paths(
            dictRecord.get("listComparedPaths") or [])):
        dictOutcome = _fdictOneOutcome(
            sPath, str(dictExpected.get(sPath) or ""), dictDiverged.get(sPath))
        if sService == "zenodo":
            dictOutcome["sMatchedInRecordId"] = str(listRecordIds[0]) if (
                dictOutcome["sOutcome"] == "matched"
                and len(listRecordIds) == 1) else ""
        listOutcomes.append(dictOutcome)
    return {"bWithheld": bool(dictRecord.get("bComparisonWithheld")),
            "listOutcomes": listOutcomes}


def _fdictOneOutcome(sPath, sExpected, dictDiverged):
    """Return one file's outcome: a match observed its expected bytes."""
    if dictDiverged is None:
        return {"sPath": sPath, "sExpectedSha256": sExpected,
                "sObservedSha256": sExpected, "sOutcome": "matched"}
    sObserved = str(dictDiverged.get("sActual") or "")
    return {"sPath": sPath,
            "sExpectedSha256": sExpected or str(
                dictDiverged.get("sExpected") or ""),
            "sObservedSha256": sObserved,
            "sOutcome": "differs" if sObserved else "absent"}


def _fdictBuildOverleafEvidence(dictObservation):
    """Return vaibify's Overleaf comparison only; never a project id."""
    from .publicationScope import fbCachedScopeIsCurrent
    dictRecord = dictObservation["dictRecords"].get("overleaf") or {}
    dictManuscript = dictObservation["dictManuscript"]
    return {
        "sCheckedAtUtc": str(dictRecord.get("sLastVerified") or ""),
        "bFresh": bool(dictObservation["dictRecordFresh"].get("overleaf")),
        "bScopeCurrent": fbCachedScopeIsCurrent(dictRecord),
        "iMatching": int(dictRecord.get("iMatching") or 0),
        "iTotalFiles": int(dictRecord.get("iTotalFiles") or 0),
        "listDivergedPaths": sorted(
            str(dictEntry.get("sPath") or "")
            for dictEntry in dictRecord.get("listDiverged") or []
            if isinstance(dictEntry, dict)),
        "sLastPushCommit": dictManuscript["sLastPushCommit"],
        "listPushedFigures": list(dictManuscript["listPushedFigures"]),
        "listNotFrozen": [
            {"sStepLabel": str(dictBlocker.get("sStepLabel") or ""),
             "listFigures": list(dictBlocker.get("listOffendingFiles") or [])}
            for dictBlocker in dictObservation["listLevel2Blockers"]
            if dictBlocker.get("sCriterion") == "figure-not-frozen"
        ],
    }


def _fdictBuildArxivEvidence(dictObservation):
    """Return the arXiv answers the gates got, errors kept as errors."""
    dictManuscript = dictObservation["dictManuscript"]
    dictLatest = _fdictArxivAnswer(dictManuscript, "latest-version")
    dictRemote = _fdictArxivAnswer(dictManuscript, "remote-hashes")
    dictObserved = dictRemote["genericAnswer"] or {}
    dictExpected = dictManuscript["dictLocalFigureHashes"]
    return {
        "sArxivId": dictManuscript["sArxivId"],
        "sRecordedVersion": dictManuscript["sRecordedVersion"],
        "dictLatestVersion": {
            "sAnswer": str(dictLatest["genericAnswer"] or ""),
            "sError": dictLatest["sError"]},
        "bVersionCurrent": bool(dictLatest["genericAnswer"]) and str(
            dictLatest["genericAnswer"]) == dictManuscript["sRecordedVersion"],
        "dictFigureComparison": {
            "listOutcomes": [_fdictFigureOutcome(
                sPath, dictExpected, dictObserved) for sPath in sorted(
                dictManuscript["listPushedFigures"])] if not dictRemote[
                "sError"] and dictRemote["genericAnswer"] is not None else [],
            "sError": dictRemote["sError"]},
        "sLastPushCommit": dictManuscript["sLastPushCommit"],
    }


def _fdictArxivAnswer(dictManuscript, sQuestion):
    """Return one memoized arXiv answer, or an unasked one."""
    for dictAnswer in dictManuscript["listArxivAnswers"]:
        if dictAnswer["sQuestion"] == sQuestion:
            return dictAnswer
    return {"sQuestion": sQuestion, "genericAnswer": None, "sError": ""}


def _fdictFigureOutcome(sPath, dictExpected, dictObserved):
    """Return one pushed figure: its local hash beside the e-print's."""
    sExpected = str(dictExpected.get(sPath) or "")
    sObserved = str(dictObserved.get(sPath) or "")
    if not sObserved:
        sOutcome = "absent"
    else:
        sOutcome = "matched" if sObserved == sExpected else "differs"
    return {"sPath": sPath, "sExpectedSha256": sExpected,
            "sObservedSha256": sObserved, "sOutcome": sOutcome}


def fdictBuildCompletes(
    dictObservation, sCheckpointRecordId, sCheckpointDoi,
    sCheckpointReceiptSha256, listCheckpointInventory,
):
    """Return what a completion claims about its checkpoint.

    The verify accepts a file from ANY declared record, so the claim is
    "met against the declared record set" and, separately, whether the
    checkpoint ALONE holds every Level 2 file: a Level 2 path lacks
    when no checkpoint item has its repository path and observed bytes.
    """
    from .publicationScope import fsetSelectLevel2Paths
    dictRecord = dictObservation["dictRecords"].get("zenodo") or {}
    dictObserved = dictRecord.get("dictComparedHashes") or {}
    setHeld = {(str(dictItem.get("sRepoPath") or ""),
                str(dictItem.get("sSha256") or ""))
               for dictItem in listCheckpointInventory or []}
    listLacks = sorted(
        sPath for sPath in fsetSelectLevel2Paths(
            dictRecord.get("listComparedPaths") or [])
        if (sPath, str(dictObserved.get(sPath) or "")) not in setHeld)
    return {
        "sCheckpointRecordId": str(sCheckpointRecordId or ""),
        "sCheckpointDoi": str(sCheckpointDoi or ""),
        "sCheckpointReceiptSha256": str(sCheckpointReceiptSha256 or ""),
        "bCheckpointAloneSuffices": not listLacks,
        "listCheckpointLacks": listLacks,
    }


def fdictDescribePublishedReceipt(dictReceipt, baReceipt, dictPublished):
    """Return the deposit record's note of the receipt a version carries.

    Bookkeeping, kept in the sync sidecar: which kind of receipt the
    version holds, its bytes' identity in the vocabulary Zenodo reports
    (MD5 and size) beside vaibify's SHA-256, and the inventory the
    receipt lists -- the files a completion version must inherit.
    """
    return {
        "sKind": dictReceipt["sKind"],
        "sRecordId": str(dictPublished.get("iDepositId") or ""),
        "sDoi": str(dictPublished.get("sDoi") or ""),
        "sKey": S_RECEIPT_DEPOSIT_KEY,
        "sSha256": hashlib.sha256(baReceipt).hexdigest(),
        "sMd5": hashlib.md5(baReceipt, usedforsecurity=False).hexdigest(),
        "iSizeBytes": len(baReceipt),
        "listContents": [dict(dictItem) for dictItem in (
            dictReceipt["dictArchive"]["listContents"])],
    }


# ── What the dashboard says about the archive's receipt ──
#
# A verdict the Zenodo row renders verbatim. "Verification pending" is
# true exactly while the Zenodo record on file is evidence about a
# record other than the completion just published: publishing it
# changed the declared record, so #208's identity check reads unknown
# until a verify of the new record succeeds.

S_RECEIPT_STATE_NONE = "none"
S_RECEIPT_STATE_CHECKPOINT = "checkpoint"
S_RECEIPT_STATE_IN_FLIGHT = "completion-in-flight"
S_RECEIPT_STATE_PENDING = "completion-pending"
S_RECEIPT_STATE_VERIFY_FAILED = "completion-verify-failed"
S_RECEIPT_STATE_COMPLETION = "completion"


def fdictDescribeArchiveReceiptState(dictWorkflow, filesRepo):
    """Return ``{sState, sMessage, bOfferCompletion}`` for the Zenodo row."""
    dictZenodo = ((dictWorkflow or {}).get("dictRemotes") or {}).get(
        "zenodo") or {}
    dictNote = dictZenodo.get("dictReceipt") or {}
    listInFlight = [
        dictRecord for dictRecord in
        (dictWorkflow or {}).get("listPendingPromotions") or []
        if isinstance(dictRecord, dict) and dictRecord.get("sLane") == (
            "completion")]
    if listInFlight:
        return _fdictReceiptState(S_RECEIPT_STATE_IN_FLIGHT, (
            "A completion version is in flight ("
            + str(listInFlight[0].get("sPromotionId")) + "); settle it "
            "from its row below."))
    if not dictNote:
        return _fdictReceiptState(S_RECEIPT_STATE_NONE, (
            "The latest archive version carries no PROOF receipt; the "
            "next version you archive will."))
    sRecordId = str(dictNote.get("sRecordId") or "")
    if dictNote.get("sKind") == S_RECEIPT_KIND_CHECKPOINT:
        return _fdictReceiptState(S_RECEIPT_STATE_CHECKPOINT, (
            "The latest version, record " + sRecordId + ", carries a "
            "checkpoint receipt. Once a verify establishes Level 2, "
            "archive the completion receipt: a new version with the "
            "same files that records it."), bOfferCompletion=True)
    return _fdictCompletionReceiptState(dictWorkflow, filesRepo, dictNote)


def _fdictCompletionReceiptState(dictWorkflow, filesRepo, dictNote):
    """Return the state of a published completion: verified, or not yet."""
    from . import levelGates, scheduledReverify
    sRecordId = str(dictNote.get("sRecordId") or "")
    try:
        dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
            filesRepo, "zenodo")
    except (OSError, ValueError, KeyError):
        dictStatus = None
    if dictStatus and not levelGates._fsDescribeUncoveredRemote(
            dictWorkflow, dictStatus, "zenodo"):
        return _fdictReceiptState(S_RECEIPT_STATE_COMPLETION, (
            "The latest version, record " + sRecordId + ", carries a "
            "completion receipt: it records that Level 2 was met."))
    if dictNote.get("sVerifyWarning"):
        return _fdictReceiptState(S_RECEIPT_STATE_VERIFY_FAILED, (
            "Completion archived as record " + sRecordId + "; its "
            "verification did not complete: " + dictNote["sVerifyWarning"]
            + " Verify now to try again."))
    return _fdictReceiptState(S_RECEIPT_STATE_PENDING, (
        "Completion archived as record " + sRecordId + "; verification "
        "pending."))


def _fdictReceiptState(sState, sMessage, bOfferCompletion=False):
    """Return one receipt-state verdict in its wire shape."""
    return {"sState": sState, "sMessage": sMessage,
            "bOfferCompletion": bool(bOfferCompletion)}
