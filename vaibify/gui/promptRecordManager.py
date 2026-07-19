"""Prompt Record capture: sanitized agent transcripts, hash-chained.

The Prompt Record is the "Recorded" state of the Replay axis: the
in-container agent's session transcripts (the JSONL files the agent
CLI writes under its own home) are copied into the project repository
at ``.vaibify/promptRecord/sessions/``, sanitized at capture, and
indexed in ``.vaibify/promptRecord/index.json``.

Honesty properties, in order of importance:

- **Sanitize-at-capture**: nothing lands in the (public) repository
  unscanned; refusal to sanitize is refusal to capture.
- **Tamper evidence, not proven completeness**: each capture record
  embeds the SHA-256 of the previous record, so removing or editing
  a record breaks the chain (:func:`fbVerifyCaptureChain`), and each
  record pins its session file's content hash
  (:func:`flistVerifyCapturedFiles`). What no mechanism can prove is
  that every prompt was recorded — coverage intervals make the
  monitored windows explicit, and gaps render as gaps.
- **Whole-file recapture**: a grown transcript is re-fetched and
  re-sanitized in full — sanitization is not prefix-stable (a secret
  can span an append boundary), so byte-appending sanitized suffixes
  would be wrong.
"""

__all__ = [
    "S_PROMPT_RECORD_DIRECTORY",
    "S_PROMPT_RECORD_INDEX_PATH",
    "S_PROMPT_RECORD_SESSIONS_DIRECTORY",
    "S_CONTAINER_TRANSCRIPT_ROOT",
    "fdictListContainerTranscripts",
    "fdictLoadIndex",
    "fdictRunCapturePass",
    "fbVerifyCaptureChain",
    "flistVerifyCapturedFiles",
]

import hashlib
import json
import posixpath
from datetime import datetime, timezone

from vaibify.gui.transcriptSanitizer import ftResultSanitizeText


S_PROMPT_RECORD_DIRECTORY = ".vaibify/promptRecord"
S_PROMPT_RECORD_INDEX_PATH = S_PROMPT_RECORD_DIRECTORY + "/index.json"
S_PROMPT_RECORD_SESSIONS_DIRECTORY = (
    S_PROMPT_RECORD_DIRECTORY + "/sessions"
)
_S_SESSIONS_DIRECTORY = S_PROMPT_RECORD_SESSIONS_DIRECTORY
S_CONTAINER_TRANSCRIPT_ROOT = "~/.claude/projects"
_I_COVERAGE_MERGE_SECONDS = 60

_S_LIST_SCRIPT = (
    "python3 -c \"import glob,json,os,sys;"
    "sRoot=os.path.expanduser('" + S_CONTAINER_TRANSCRIPT_ROOT + "');"
    "listPaths=glob.glob(sRoot+'/**/*.jsonl',recursive=True);"
    "sys.stdout.write(json.dumps({sPath:os.path.getsize(sPath) "
    "for sPath in listPaths}))\""
)


def fdictListContainerTranscripts(connectionDocker, sContainerId):
    """Return ``{sContainerPath: iSizeBytes}`` for agent transcripts."""
    resultExec = connectionDocker.texecRunInContainerStreamed(
        sContainerId, _S_LIST_SCRIPT,
    )
    if resultExec.iExitCode != 0:
        raise RuntimeError(
            "Transcript listing failed: " + resultExec.sStderr,
        )
    dictSizes = json.loads(resultExec.sStdout or "{}")
    return {
        sPath: int(iSize) for sPath, iSize in dictSizes.items()
    }


def fdictLoadIndex(filesRepo):
    """Return the parsed capture index, or a fresh empty one."""
    if not filesRepo.fbIsFile(S_PROMPT_RECORD_INDEX_PATH):
        return _fdictEmptyIndex()
    try:
        dictIndex = json.loads(
            filesRepo.fsReadText(S_PROMPT_RECORD_INDEX_PATH),
        )
    except (OSError, ValueError):
        return _fdictEmptyIndex()
    if not isinstance(dictIndex, dict):
        return _fdictEmptyIndex()
    dictIndex.setdefault("listCaptures", [])
    dictIndex.setdefault("listCoverageIntervals", [])
    dictIndex.setdefault("dictSessionBytes", {})
    return dictIndex


def _fdictEmptyIndex():
    return {
        "listCaptures": [],
        "listCoverageIntervals": [],
        "dictSessionBytes": {},
    }


def _fsHashRecord(dictRecord):
    """Return the canonical SHA-256 of one capture record."""
    return hashlib.sha256(
        json.dumps(dictRecord, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def _fsSessionFileName(sContainerPath):
    """Flatten a container transcript path into a safe basename.

    Different agent sessions share basenames across project
    directories, so the parent directory joins the name.
    """
    listParts = [
        sPart for sPart in sContainerPath.split("/") if sPart
    ][-2:]
    return "__".join(listParts).replace(":", "_")


def _fsCurrentTimestamp():
    return datetime.now(timezone.utc).isoformat()


def _fnExtendCoverage(dictIndex, iPollSeconds):
    """Extend the live coverage interval or open a new one.

    An interval keeps extending while capture passes arrive within
    twice the poll period (plus a merge margin); a longer silence —
    hub down, recording paused — closes it, and the gap between
    intervals is the honest record of unmonitored time.
    """
    sNow = _fsCurrentTimestamp()
    listIntervals = dictIndex["listCoverageIntervals"]
    if listIntervals:
        dictLast = listIntervals[-1]
        fSilence = (
            datetime.fromisoformat(sNow)
            - datetime.fromisoformat(dictLast["sEndUtc"])
        ).total_seconds()
        if fSilence <= 2 * iPollSeconds + _I_COVERAGE_MERGE_SECONDS:
            dictLast["sEndUtc"] = sNow
            return
    listIntervals.append({"sStartUtc": sNow, "sEndUtc": sNow})


def _fdictCaptureOneSession(
    connectionDocker, sContainerId, filesRepo, sContainerPath,
    iSizeBytes, listExactSecrets, dictIndex,
):
    """Fetch, sanitize, land, and record one transcript file."""
    baRaw = connectionDocker.fbaFetchFile(sContainerId, sContainerPath)
    sSanitized, dictCounts = ftResultSanitizeText(
        baRaw.decode("utf-8", errors="replace"), listExactSecrets,
    )
    sFileName = _fsSessionFileName(sContainerPath)
    filesRepo.fnWriteTextAtomic(
        posixpath.join(_S_SESSIONS_DIRECTORY, sFileName), sSanitized,
    )
    listCaptures = dictIndex["listCaptures"]
    dictRecord = {
        "sSessionFileName": sFileName,
        "iBytesCaptured": iSizeBytes,
        "sSha256": hashlib.sha256(
            sSanitized.encode("utf-8"),
        ).hexdigest(),
        "sPreviousRecordSha256": (
            _fsHashRecord(listCaptures[-1]) if listCaptures else ""
        ),
        "sCapturedAtUtc": _fsCurrentTimestamp(),
        "iRedactionCount": sum(dictCounts.values()),
        "dictRedactionsByCategory": dictCounts,
    }
    listCaptures.append(dictRecord)
    dictIndex["dictSessionBytes"][sContainerPath] = iSizeBytes
    return dictRecord


def fdictRunCapturePass(
    connectionDocker, sContainerId, filesRepo, listExactSecrets,
    iPollSeconds=30,
):
    """Capture every new/grown transcript; return a pass summary."""
    dictSizes = fdictListContainerTranscripts(
        connectionDocker, sContainerId,
    )
    dictIndex = fdictLoadIndex(filesRepo)
    listCapturedNames = []
    iRedactionTotal = 0
    for sContainerPath in sorted(dictSizes):
        iSizeBytes = dictSizes[sContainerPath]
        if iSizeBytes <= dictIndex["dictSessionBytes"].get(
            sContainerPath, -1,
        ):
            continue
        dictRecord = _fdictCaptureOneSession(
            connectionDocker, sContainerId, filesRepo, sContainerPath,
            iSizeBytes, listExactSecrets, dictIndex,
        )
        listCapturedNames.append(dictRecord["sSessionFileName"])
        iRedactionTotal += dictRecord["iRedactionCount"]
    _fnExtendCoverage(dictIndex, iPollSeconds)
    filesRepo.fnWriteJsonAtomic(S_PROMPT_RECORD_INDEX_PATH, dictIndex)
    return {
        "listCapturedSessions": listCapturedNames,
        "iRedactionCount": iRedactionTotal,
        "iSessionCount": len({
            dictRecord["sSessionFileName"]
            for dictRecord in dictIndex["listCaptures"]
        }),
    }


def fbVerifyCaptureChain(dictIndex):
    """Return True iff the capture-record hash chain is intact."""
    sExpected = ""
    for dictRecord in dictIndex.get("listCaptures", []):
        if dictRecord.get("sPreviousRecordSha256", "") != sExpected:
            return False
        sExpected = _fsHashRecord(dictRecord)
    return True


def flistVerifyCapturedFiles(filesRepo, dictIndex):
    """Return the session files whose content no longer matches.

    Only each session's most recent capture record pins its current
    content; earlier records describe superseded captures.
    """
    dictLatestByName = {}
    for dictRecord in dictIndex.get("listCaptures", []):
        dictLatestByName[dictRecord["sSessionFileName"]] = dictRecord
    listMismatched = []
    for sFileName, dictRecord in sorted(dictLatestByName.items()):
        sRelPath = posixpath.join(_S_SESSIONS_DIRECTORY, sFileName)
        dictHashes = filesRepo.fdictHashFiles([sRelPath])
        sActual = (dictHashes.get(sRelPath) or {}).get("sSha256") or ""
        if sActual != dictRecord.get("sSha256"):
            listMismatched.append(sFileName)
    return listMismatched
