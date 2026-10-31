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
"""

import hashlib
import json
from datetime import datetime, timezone

__all__ = [
    "S_INPUTS_LIVE_READS",
    "S_INPUTS_ONE_SNAPSHOT",
    "RecordingRepoFiles",
    "fdictCaptureObservation",
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
        """Ask every recorded read again; return the paths whose answer moved."""
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
        **dictEvaluation,
    }
