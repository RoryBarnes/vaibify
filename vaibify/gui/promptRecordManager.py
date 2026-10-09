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
- **Every agent the image carries**: Claude Code, Codex and Gemini
  each write JSONL transcripts under their own home, in their own
  shape. ``TUPLE_TRANSCRIPT_SOURCES`` names each one's root, file
  pattern and how its launch directory is recorded; one sanitizer and
  one hash chain serve all three, and every capture record names the
  provider whose transcript it holds.
- **Project scope**: a container can hold several projects, and an
  agent CLI files every session under one root. A session is captured
  only when the directory it was launched in lies inside this
  project's repository; a session launched anywhere else (the
  workspace root, a sibling project) is counted and left out, never
  published into a record it may not belong to.
- **Line-aligned incremental capture**: the sanitizer works one line
  at a time and no redacted secret contains a newline, so sanitizing
  only the complete lines appended since the last capture yields
  exactly the bytes a whole-file recapture would. A trailing partial
  line waits for the next pass, because a secret CAN span a mid-line
  append boundary. The captured raw prefix is pinned by hash; a
  transcript that was rewritten rather than appended, or a session
  file that no longer matches its record, is recaptured whole. A
  transcript is passed over only when its stat key (nanosecond mtime
  and ctime, size, inode) is the one last seen, never on its size: a
  same-size rewrite or a truncation changes the key, so it is fetched
  and its changed prefix sends it to a whole recapture.
- **Three phases, one short lock each**: listing (an exec, so it
  needs the mutation drain) and landing (a write) hold the drain;
  fetching and sanitizing, which dominate the cost, hold nothing. The
  landing phase re-checks every precondition the sanitizing phase
  assumed and drops a session whose index state moved, so a
  concurrent capture can waste work but never corrupt the chain.
"""

__all__ = [
    "S_PROMPT_RECORD_DIRECTORY",
    "S_PROMPT_RECORD_INDEX_PATH",
    "S_PROMPT_RECORD_SESSIONS_DIRECTORY",
    "S_PROVIDER_CLAUDE",
    "TUPLE_TRANSCRIPT_SOURCES",
    "fbSessionBelongsToProject",
    "fdictListContainerTranscripts",
    "fdictLoadIndex",
    "fdictSanitizeNewTranscriptLines",
    "fdictLandSanitizedSessions",
    "fbVerifyCaptureChain",
    "flistSummarizeSessions",
    "flistVerifyCapturedFiles",
]

import hashlib
import json
import posixpath
import shlex
from datetime import datetime, timezone

from vaibify.gui.transcriptSanitizer import flistSanitizeTextsInParallel


S_PROMPT_RECORD_DIRECTORY = ".vaibify/promptRecord"
S_PROMPT_RECORD_INDEX_PATH = S_PROMPT_RECORD_DIRECTORY + "/index.json"
S_PROMPT_RECORD_SESSIONS_DIRECTORY = (
    S_PROMPT_RECORD_DIRECTORY + "/sessions"
)
_S_SESSIONS_DIRECTORY = S_PROMPT_RECORD_SESSIONS_DIRECTORY
S_PROVIDER_CLAUDE = "claude"
_I_COVERAGE_MERGE_SECONDS = 60

# One entry per agent whose transcripts are captured. ``sLaunchRule``
# names the listing program's reader for the directory a session was
# launched in, which decides whether it belongs to this project.
TUPLE_TRANSCRIPT_SOURCES = (
    {"sProvider": S_PROVIDER_CLAUDE, "sRoot": "~/.claude/projects",
     "sPattern": "**/*.jsonl", "sLaunchRule": "first-cwd"},
    {"sProvider": "codex", "sRoot": "~/.codex/sessions",
     "sPattern": "**/rollout-*.jsonl", "sLaunchRule": "session-meta-cwd"},
    {"sProvider": "gemini", "sRoot": "~/.gemini/tmp",
     "sPattern": "*/chats/session-*.jsonl",
     "sLaunchRule": "project-root-marker"},
)

_S_LIST_PROGRAM = """
import glob, json, os, sys
listSources = json.loads(""" + repr(json.dumps(TUPLE_TRANSCRIPT_SOURCES)) + """)
def fsFirstCwd(sPath):
    with open(sPath, 'rb') as fileTranscript:
        for baLine in fileTranscript:
            if b'"cwd"' not in baLine:
                continue
            try:
                dictLine = json.loads(baLine.decode('utf-8', 'replace'))
            except ValueError:
                continue
            if isinstance(dictLine, dict) and dictLine.get('cwd'):
                return str(dictLine['cwd'])
    return ''
def fsSessionMetaCwd(sPath):
    with open(sPath, 'rb') as fileTranscript:
        for iLine, baLine in enumerate(fileTranscript):
            if iLine >= 20:
                break
            try:
                dictLine = json.loads(baLine.decode('utf-8', 'replace'))
            except ValueError:
                continue
            if not isinstance(dictLine, dict):
                continue
            dictPayload = dictLine.get('payload')
            if dictLine.get('type') == 'session_meta' and isinstance(
                    dictPayload, dict) and dictPayload.get('cwd'):
                return str(dictPayload['cwd'])
    return ''
def fsProjectRootMarker(sPath):
    sSlugDirectory = os.path.dirname(os.path.dirname(sPath))
    try:
        with open(os.path.join(sSlugDirectory, '.project_root')) as fileRoot:
            sRoot = fileRoot.read().strip()
        if sRoot:
            return sRoot
    except OSError:
        pass
    try:
        with open(os.path.expanduser('~/.gemini/projects.json')) as fileMap:
            dictProjects = json.load(fileMap).get('projects') or {}
    except (OSError, ValueError, AttributeError):
        return ''
    sSlug = os.path.basename(sSlugDirectory)
    for sProjectPath, sProjectSlug in dictProjects.items():
        if sProjectSlug == sSlug:
            return str(sProjectPath)
    return ''
dictRules = {'first-cwd': fsFirstCwd,
             'session-meta-cwd': fsSessionMetaCwd,
             'project-root-marker': fsProjectRootMarker}
dictListing = {}
for dictSource in listSources:
    sRoot = os.path.expanduser(dictSource['sRoot'])
    for sPath in glob.glob(os.path.join(sRoot, dictSource['sPattern']),
                           recursive=True):
        try:
            statFile = os.stat(sPath)
            sLaunchDirectory = dictRules[dictSource['sLaunchRule']](sPath)
        except OSError:
            continue
        dictListing[sPath] = {
            'iSizeBytes': statFile.st_size,
            'listStatKey': [statFile.st_mtime_ns, statFile.st_ctime_ns,
                            statFile.st_size, statFile.st_ino],
            'sLaunchDirectory': sLaunchDirectory,
            'sProvider': dictSource['sProvider'],
        }
sys.stdout.write(json.dumps(dictListing))
"""
_S_LIST_SCRIPT = "python3 -c " + shlex.quote(_S_LIST_PROGRAM)


def fdictListContainerTranscripts(connectionDocker, sContainerId):
    """Return ``{sContainerPath: {iSizeBytes, listStatKey, ...}}``.

    Each entry also carries ``sProvider`` and ``sLaunchDirectory``: the
    directory the agent CLI recorded the session was launched in, read
    by that provider's rule, or ``''`` when it recorded none.
    """
    tExecResult = connectionDocker.ftRunInContainerStreamed(
        sContainerId, _S_LIST_SCRIPT,
    )
    if tExecResult.iExitCode != 0:
        raise RuntimeError(
            "Transcript listing failed: " + tExecResult.sStderr,
        )
    dictListing = json.loads(tExecResult.sStdout or "{}")
    return {
        sPath: {
            "iSizeBytes": int(dictEntry["iSizeBytes"]),
            "listStatKey": [
                int(iPart) for iPart in dictEntry.get("listStatKey") or []
            ],
            "sLaunchDirectory": str(dictEntry.get("sLaunchDirectory") or ""),
            "sProvider": str(dictEntry.get("sProvider") or S_PROVIDER_CLAUDE),
        }
        for sPath, dictEntry in dictListing.items()
    }


def fbSessionBelongsToProject(sLaunchDirectory, sProjectRepoPath):
    """Return True iff a session launched here is inside the project."""
    if not sLaunchDirectory or not sProjectRepoPath:
        return False
    sProjectRoot = posixpath.normpath(sProjectRepoPath)
    sDirectory = posixpath.normpath(sLaunchDirectory)
    return (
        sDirectory == sProjectRoot
        or sDirectory.startswith(sProjectRoot.rstrip("/") + "/")
    )


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
    dictIndex.setdefault("dictSessionRawSha256", {})
    dictIndex.setdefault("dictSessionStatKeys", {})
    dictIndex.setdefault("iSessionsOutsideProject", 0)
    return dictIndex


def _fdictEmptyIndex():
    return {
        "listCaptures": [],
        "listCoverageIntervals": [],
        "dictSessionBytes": {},
        "dictSessionRawSha256": {},
        "dictSessionStatKeys": {},
        "iSessionsOutsideProject": 0,
    }


def _fsSha256Hex(baContent):
    return hashlib.sha256(baContent).hexdigest()


def _fsHashRecord(dictRecord):
    """Return the canonical SHA-256 of one capture record."""
    return hashlib.sha256(
        json.dumps(dictRecord, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def _fsSessionFileName(sContainerPath, sProvider=S_PROVIDER_CLAUDE):
    """Flatten a container transcript path into a safe basename.

    Different agent sessions share basenames across project
    directories, so the parent directory joins the name. Claude's
    names are kept exactly as they always were, because existing
    records name their sessions by them; every other provider's name
    starts with the provider, and Gemini's carries its project folder
    rather than the ``chats`` directory every one of its sessions
    shares.
    """
    listParts = [sPart for sPart in sContainerPath.split("/") if sPart]
    if sProvider == S_PROVIDER_CLAUDE:
        listName = listParts[-2:]
    elif len(listParts) >= 3 and listParts[-2] == "chats":
        listName = [sProvider, listParts[-3], listParts[-1]]
    else:
        listName = [sProvider, listParts[-1]]
    return "__".join(listName).replace(":", "_")


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


def _fsLatestSessionSha256(dictIndex, sFileName):
    """Return the content hash the newest record pins for a session."""
    sSha256 = ""
    for dictRecord in dictIndex["listCaptures"]:
        if dictRecord["sSessionFileName"] == sFileName:
            sSha256 = dictRecord["sSha256"]
    return sSha256


def _fsPriorSanitizedTextOrNone(filesRepo, dictIndex, sFileName):
    """Return the landed session text when it still matches its record.

    ``None`` means the append precondition fails -- no record, a
    missing file, or a file edited after capture -- and the session
    must be recaptured whole.
    """
    sExpectedSha256 = _fsLatestSessionSha256(dictIndex, sFileName)
    if not sExpectedSha256:
        return None
    try:
        sPriorText = filesRepo.fsReadText(
            posixpath.join(_S_SESSIONS_DIRECTORY, sFileName),
        )
    except (OSError, ValueError):
        return None
    if _fsSha256Hex(sPriorText.encode("utf-8")) != sExpectedSha256:
        return None
    return sPriorText


def _fbCapturedPrefixUnchanged(dictIndex, sContainerPath, baRaw):
    """Return True iff the raw bytes already captured are still there."""
    iCapturedBytes = dictIndex["dictSessionBytes"].get(sContainerPath, -1)
    sCapturedSha256 = dictIndex["dictSessionRawSha256"].get(
        sContainerPath, "",
    )
    if iCapturedBytes < 0 or not sCapturedSha256:
        return False
    if len(baRaw) < iCapturedBytes:
        return False
    return _fsSha256Hex(baRaw[:iCapturedBytes]) == sCapturedSha256


def _fbListingUnchangedSinceCapture(dictIndex, sContainerPath, dictEntry):
    """Return True iff the transcript's stat key is the one last seen.

    A listing with no stat key is never treated as unchanged: the
    fetch and the prefix hash then decide, which costs a read and can
    never miss a rewrite.
    """
    listSeen = dictIndex["dictSessionStatKeys"].get(sContainerPath)
    listNow = dictEntry.get("listStatKey") or []
    return bool(listSeen) and bool(listNow) and list(listSeen) == listNow


def _fdictPlanOneSession(
    connectionDocker, sContainerId, filesRepo, sContainerPath,
    listExactSecrets, dictIndex, dictEntry,
):
    """Fetch a transcript and decide which of its bytes need sanitizing.

    Returns ``None`` when no complete line is new; otherwise a pending
    record carrying the raw ``sNewText`` and the landed ``sPriorText``,
    which :func:`_fnCompletePendingSession` turns into the sanitized
    session once the batch has been scanned.
    """
    baRaw = connectionDocker.fbaFetchFile(sContainerId, sContainerPath)
    iCompleteBytes = baRaw.rfind(b"\n") + 1
    sProvider = dictEntry.get("sProvider") or S_PROVIDER_CLAUDE
    sFileName = _fsSessionFileName(sContainerPath, sProvider)
    sPriorText = None
    iStartBytes = 0
    bAnySecretSpansLines = any(
        "\n" in sSecret for sSecret in listExactSecrets or []
    )
    if (not bAnySecretSpansLines
            and _fbCapturedPrefixUnchanged(dictIndex, sContainerPath, baRaw)):
        sPriorText = _fsPriorSanitizedTextOrNone(
            filesRepo, dictIndex, sFileName,
        )
        if sPriorText is not None:
            iStartBytes = dictIndex["dictSessionBytes"][sContainerPath]
    # A transcript captured before whose captured prefix is gone -- a
    # rewrite, or a truncation down to nothing -- is recaptured whole
    # even when it now holds no complete line: keeping the old text
    # would publish what the transcript no longer says.
    bPreviouslyCaptured = (
        dictIndex["dictSessionBytes"].get(sContainerPath, -1) > 0)
    if iCompleteBytes <= iStartBytes and (
            sPriorText is not None or not bPreviouslyCaptured):
        return None
    return {
        "sContainerPath": sContainerPath,
        "sSessionFileName": sFileName,
        "sProvider": sProvider,
        "listStatKey": list(dictEntry.get("listStatKey") or []),
        "iCapturedBytesBefore": dictIndex["dictSessionBytes"].get(
            sContainerPath, -1,
        ),
        "sRawSha256Before": dictIndex["dictSessionRawSha256"].get(
            sContainerPath, "",
        ),
        "sCaptureKind": "whole" if sPriorText is None else "appended",
        "sPriorSha256": (
            "" if sPriorText is None
            else _fsSha256Hex(sPriorText.encode("utf-8"))
        ),
        "sPriorText": sPriorText or "",
        "sNewText": baRaw[iStartBytes:iCompleteBytes].decode(
            "utf-8", errors="replace",
        ),
        "iCapturedBytesAfter": iCompleteBytes,
        "sRawSha256After": _fsSha256Hex(baRaw[:iCompleteBytes]),
    }


def _fnCompletePendingSession(dictPending, tSanitized):
    """Replace a plan's raw text with its sanitized session and counts."""
    sSanitized, dictCounts = tSanitized
    dictPending["sSanitizedText"] = dictPending.pop("sPriorText") + sSanitized
    del dictPending["sNewText"]
    dictPending["dictRedactionsByCategory"] = dictCounts


def fdictSanitizeNewTranscriptLines(
    connectionDocker, sContainerId, filesRepo, dictListing,
    sProjectRepoPath, listExactSecrets,
):
    """Fetch and sanitize every in-project transcript's new lines.

    Holds no lock: it only reads (typed reads of the transcripts and of
    the landed record) and computes. Every session is planned first and
    the new text is then scanned as one batch, so a large first pass
    spreads across worker processes. Returns ``{"listPending",
    "listOutsideProject"}``; nothing is written until
    :func:`fdictLandSanitizedSessions` applies ``listPending``.
    """
    dictIndex = fdictLoadIndex(filesRepo)
    listPending = []
    listOutsideProject = []
    listUnchanged = []
    for sContainerPath in sorted(dictListing):
        dictEntry = dictListing[sContainerPath]
        if not fbSessionBelongsToProject(
            dictEntry["sLaunchDirectory"], sProjectRepoPath,
        ):
            listOutsideProject.append(sContainerPath)
            continue
        if _fbListingUnchangedSinceCapture(
                dictIndex, sContainerPath, dictEntry):
            continue
        dictPending = _fdictPlanOneSession(
            connectionDocker, sContainerId, filesRepo, sContainerPath,
            listExactSecrets, dictIndex, dictEntry,
        )
        if dictPending is not None:
            listPending.append(dictPending)
        else:
            listUnchanged.append(_fdictUnchangedObservation(
                dictIndex, sContainerPath, dictEntry))
    listSanitized = flistSanitizeTextsInParallel(
        [dictPending["sNewText"] for dictPending in listPending],
        listExactSecrets,
    )
    for dictPending, tSanitized in zip(listPending, listSanitized):
        _fnCompletePendingSession(dictPending, tSanitized)
    return {
        "listPending": listPending,
        "listOutsideProject": listOutsideProject,
        "listUnchanged": listUnchanged,
    }


def _fdictUnchangedObservation(dictIndex, sContainerPath, dictEntry):
    """Return the stat key of a transcript with nothing new to capture.

    The fetch found its captured prefix intact and no complete new
    line, so the key it was listed under can spare the next pass the
    fetch -- recorded only if the capture state is still as it was.
    """
    return {
        "sContainerPath": sContainerPath,
        "listStatKey": list(dictEntry.get("listStatKey") or []),
        "iCapturedBytesBefore": dictIndex["dictSessionBytes"].get(
            sContainerPath, -1),
        "sRawSha256Before": dictIndex["dictSessionRawSha256"].get(
            sContainerPath, ""),
    }


def _fbPendingStillApplies(filesRepo, dictIndex, dictPending):
    """Return True iff nothing moved since the session was sanitized."""
    sContainerPath = dictPending["sContainerPath"]
    if dictIndex["dictSessionBytes"].get(sContainerPath, -1) != (
        dictPending["iCapturedBytesBefore"]
    ):
        return False
    if dictIndex["dictSessionRawSha256"].get(sContainerPath, "") != (
        dictPending["sRawSha256Before"]
    ):
        return False
    if dictPending["sCaptureKind"] != "appended":
        return True
    sRelPath = posixpath.join(
        _S_SESSIONS_DIRECTORY, dictPending["sSessionFileName"],
    )
    dictHashes = filesRepo.fdictHashFiles([sRelPath])
    sActual = (dictHashes.get(sRelPath) or {}).get("sSha256") or ""
    return sActual == dictPending["sPriorSha256"]


def _fdictLandOneSession(filesRepo, dictIndex, dictPending):
    """Write one sanitized session and chain its capture record."""
    sFileName = dictPending["sSessionFileName"]
    sSanitizedText = dictPending["sSanitizedText"]
    filesRepo.fnWriteTextAtomic(
        posixpath.join(_S_SESSIONS_DIRECTORY, sFileName), sSanitizedText,
    )
    listCaptures = dictIndex["listCaptures"]
    dictCounts = dictPending["dictRedactionsByCategory"]
    dictRecord = {
        "sSessionFileName": sFileName,
        "sProvider": dictPending["sProvider"],
        "sCaptureKind": dictPending["sCaptureKind"],
        "iBytesCaptured": dictPending["iCapturedBytesAfter"],
        "sSha256": _fsSha256Hex(sSanitizedText.encode("utf-8")),
        "sPreviousRecordSha256": (
            _fsHashRecord(listCaptures[-1]) if listCaptures else ""
        ),
        "sCapturedAtUtc": _fsCurrentTimestamp(),
        "iRedactionCount": sum(dictCounts.values()),
        "dictRedactionsByCategory": dictCounts,
    }
    listCaptures.append(dictRecord)
    sContainerPath = dictPending["sContainerPath"]
    dictIndex["dictSessionBytes"][sContainerPath] = (
        dictPending["iCapturedBytesAfter"]
    )
    dictIndex["dictSessionRawSha256"][sContainerPath] = (
        dictPending["sRawSha256After"]
    )
    dictIndex["dictSessionStatKeys"][sContainerPath] = (
        dictPending["listStatKey"]
    )
    return dictRecord


def _fnRecordUnchangedStatKeys(dictIndex, listUnchanged):
    """Record the stat keys of transcripts verified to hold nothing new."""
    for dictObservation in listUnchanged:
        sContainerPath = dictObservation["sContainerPath"]
        if dictIndex["dictSessionBytes"].get(sContainerPath, -1) != (
            dictObservation["iCapturedBytesBefore"]
        ) or dictIndex["dictSessionRawSha256"].get(sContainerPath, "") != (
            dictObservation["sRawSha256Before"]
        ):
            continue
        dictIndex["dictSessionStatKeys"][sContainerPath] = (
            dictObservation["listStatKey"]
        )


def fdictLandSanitizedSessions(filesRepo, dictSanitized, iPollSeconds=30):
    """Land every still-valid pending session; return a pass summary.

    Runs under the mutation drain. A session whose index state moved
    after it was sanitized is dropped and reported in
    ``listDeferredSessions``; the next pass sanitizes it afresh. The
    number of sessions left out as outside the project is kept in the
    index -- a count only, because the record is public and a session's
    path names the directory, and so the project, it was launched in.
    """
    dictIndex = fdictLoadIndex(filesRepo)
    listCapturedNames = []
    listDeferredNames = []
    iRedactionTotal = 0
    for dictPending in dictSanitized["listPending"]:
        if not _fbPendingStillApplies(filesRepo, dictIndex, dictPending):
            listDeferredNames.append(dictPending["sSessionFileName"])
            continue
        dictRecord = _fdictLandOneSession(filesRepo, dictIndex, dictPending)
        listCapturedNames.append(dictRecord["sSessionFileName"])
        iRedactionTotal += dictRecord["iRedactionCount"]
    _fnRecordUnchangedStatKeys(
        dictIndex, dictSanitized.get("listUnchanged") or [])
    iSessionsOutsideProject = len(dictSanitized["listOutsideProject"])
    dictIndex["iSessionsOutsideProject"] = iSessionsOutsideProject
    _fnExtendCoverage(dictIndex, iPollSeconds)
    filesRepo.fnWriteJsonAtomic(S_PROMPT_RECORD_INDEX_PATH, dictIndex)
    return {
        "listCapturedSessions": listCapturedNames,
        "listDeferredSessions": listDeferredNames,
        "iSessionsOutsideProject": iSessionsOutsideProject,
        "iRedactionCount": iRedactionTotal,
        "iSessionCount": len({
            dictRecord["sSessionFileName"]
            for dictRecord in dictIndex["listCaptures"]
        }),
    }


def flistSummarizeSessions(dictIndex):
    """Return one summary per captured session, in first-capture order.

    Each carries the session's CURRENT redaction total, which is not the
    sum of its records: a whole recapture re-counts everything it lands,
    so it resets the tally, and an appended capture adds only its new
    lines' redactions. Summing every record counted a recaptured
    session twice.
    """
    dictSummaries = {}
    for dictRecord in dictIndex.get("listCaptures", []):
        sFileName = dictRecord["sSessionFileName"]
        dictSummary = dictSummaries.setdefault(sFileName, {
            "sSessionFileName": sFileName, "iRedactionCount": 0,
        })
        # A record written before providers were named is Claude's:
        # it was the only transcript capture read.
        dictSummary["sProvider"] = dictRecord.get(
            "sProvider") or S_PROVIDER_CLAUDE
        iCount = int(dictRecord.get("iRedactionCount") or 0)
        if dictRecord.get("sCaptureKind") == "appended":
            dictSummary["iRedactionCount"] += iCount
        else:
            dictSummary["iRedactionCount"] = iCount
        dictSummary["iBytesCaptured"] = dictRecord.get("iBytesCaptured", 0)
        dictSummary["sLastCapturedAtUtc"] = dictRecord.get(
            "sCapturedAtUtc", "",
        )
        dictSummary["sSha256"] = dictRecord.get("sSha256", "")
    return list(dictSummaries.values())


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
