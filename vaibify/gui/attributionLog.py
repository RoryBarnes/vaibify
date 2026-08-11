"""Supervised-mode attribution: recorded causes and permanent flags.

The Supervised state's claim is narrow and checkable: *every detected
change to a watched path in the project repository during a supervised
interval is attributable to a recorded action channel*. "Watched" is
the declared-path set the poll already stats — an undeclared file the
poll never looks at cannot be judged, and the claim does not pretend
otherwise. Two append-only JSONL files under
``.vaibify/promptRecord/attribution/`` carry it:

- ``events.jsonl`` — one record per recorded mutation-channel event
  (pipeline dispatch, editor save, agent-lane request, terminal
  session, context write): ``{sChannel, sActor, sDetail,
  sTimestampUtc, sPreviousEventSha256}``.
- ``flags.jsonl`` — permanent findings: ``unattributed-modification``
  (files changed with no recorded cause inside the tolerance window),
  ``unsupervised-gap`` (the repo changed while the hub was not
  watching), and ``attribution-log-tampered`` (the event chain no
  longer verifies).

Both files are hash-chained, so editing or deleting a record breaks
:func:`fbVerifyFlagChain` / :func:`fbVerifyEventChain`; nothing in the
codebase ever removes a flag. Permanence is convention + git history +
the chain — a container-writable repository cannot host an unwritable
store, and the docs say so. Because a hash chain is prefix-valid,
truncation is invisible to the chain alone; the persisted flag count
is the anchor that makes it visible, which is why
:func:`fdictSummarizeSupervisionEvidence` compares the two.

Attribution granularity is the tolerance *window*, not the file path:
a change is attributed when a recorded event landed within the window
of the change's own modification time, of the moment of judgment, or
while a terminal session was open. That can under-flag concurrent
tampering during legitimate activity. It can also under-flag when a
recorded channel happens to be active for an unrelated reason — the
terminal interval is the widest such case, and is preferred over the
alternative, which systematically false-flagged ordinary work done
minutes into a terminal session.
"""

__all__ = [
    "S_ATTRIBUTION_EVENTS_PATH",
    "S_ATTRIBUTION_FLAGS_PATH",
    "S_TERMINAL_CHANNEL",
    "S_SUPERVISION_ENDED_CHANNEL",
    "S_TERMINAL_OPENED_DETAIL",
    "S_TERMINAL_CLOSED_DETAIL",
    "F_ATTRIBUTION_WINDOW_SECONDS",
    "F_ATTRIBUTION_MTIME_CUTOFF_SECONDS",
    "fbSupervisionEnabled",
    "fdatetimeParseTimestampAsUtc",
    "fnAppendAttributionEvent",
    "flistLoadAttributionEvents",
    "fbEventsAccountForChange",
    "fbAnyEventWithinWindow",
    "fbVerifyEventChain",
    "fdictAppendFlag",
    "flistLoadFlags",
    "fbVerifyFlagChain",
    "fdictSummarizeSupervisionEvidence",
]

import hashlib
import json
from datetime import datetime, timezone

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal

_S_ATTRIBUTION_DIRECTORY = ".vaibify/promptRecord/attribution"
S_ATTRIBUTION_EVENTS_PATH = _S_ATTRIBUTION_DIRECTORY + "/events.jsonl"
S_ATTRIBUTION_FLAGS_PATH = _S_ATTRIBUTION_DIRECTORY + "/flags.jsonl"

S_TERMINAL_CHANNEL = "terminal"
# The channel a workflow records when it LEAVES Supervised mode
# because it was opened on the host. Its own channel rather than a
# detail string on another: the event is the boundary of the period
# the log's claim covers, and a reader has to be able to find it
# without parsing prose.
S_SUPERVISION_ENDED_CHANNEL = "supervision-ended"
S_TERMINAL_OPENED_DETAIL = "session-opened"
S_TERMINAL_CLOSED_DETAIL = "session-closed"

F_ATTRIBUTION_WINDOW_SECONDS = 60.0
# The watchdog judges only changes newer than this cutoff, and each
# change is judged against a window anchored on its own mtime. The
# cutoff must therefore be at least as wide as the window, or the
# watchdog would judge changes whose explaining event is already out
# of reach. Derived, never written as an independent literal.
F_ATTRIBUTION_MTIME_CUTOFF_SECONDS = 1.5 * F_ATTRIBUTION_WINDOW_SECONDS

_S_PREVIOUS_FLAG_KEY = "sPreviousFlagSha256"
_S_PREVIOUS_EVENT_KEY = "sPreviousEventSha256"


def _fsCurrentTimestamp():
    return datetime.now(timezone.utc).isoformat()


def fbSupervisionEnabled(dictWorkflow):
    """Return True iff the workflow opted into Supervised mode."""
    dictSupervision = (
        ((dictWorkflow or {}).get("dictAiProvenance") or {})
        .get("dictSupervision") or {}
    )
    return dictSupervision.get("bEnabled") is True


def fdatetimeParseTimestampAsUtc(sTimestamp):
    """Return an aware UTC datetime, or ``None`` when unparseable.

    A timezone-less stamp is read as UTC rather than allowed to
    escape: ``datetime.fromisoformat`` accepts it happily and the
    subsequent subtraction against an aware "now" raises TypeError,
    which used to reach the status-poll response builder and 500 the
    whole dashboard for that container. Every writer here stamps UTC,
    so reading a naive value as UTC is the honest interpretation.
    """
    try:
        dtParsed = datetime.fromisoformat(str(sTimestamp or ""))
    except (TypeError, ValueError):
        return None
    if dtParsed.tzinfo is None:
        return dtParsed.replace(tzinfo=timezone.utc)
    return dtParsed.astimezone(timezone.utc)


def _flistLoadJsonlRecords(filesRepo, sRelPath):
    """Parse one JSONL file into record dicts (missing file → [])."""
    if not filesRepo.fbIsFile(sRelPath):
        return []
    try:
        sText = filesRepo.fsReadText(sRelPath)
    except (OSError, FileNotFoundError) as error:
        fnReRaiseControlPlaneRefusal(error)
        return []
    listRecords = []
    for sLine in sText.splitlines():
        try:
            dictRecord = json.loads(sLine)
        except ValueError:
            continue
        if isinstance(dictRecord, dict):
            listRecords.append(dictRecord)
    return listRecords


def _fnAppendJsonlRecord(filesRepo, sRelPath, dictRecord):
    """Append one record (read + rewrite atomically via the adapter)."""
    listRecords = _flistLoadJsonlRecords(filesRepo, sRelPath)
    listRecords.append(dictRecord)
    filesRepo.fnWriteTextAtomic(
        sRelPath,
        "\n".join(
            json.dumps(dictExisting, sort_keys=True)
            for dictExisting in listRecords
        ) + "\n",
    )


def _fsHashChainedRecord(dictRecord):
    return hashlib.sha256(
        json.dumps(dictRecord, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def _fbVerifyHashChain(listRecords, sPreviousKey):
    """Return True iff each record names its predecessor's hash."""
    sExpected = ""
    for dictRecord in listRecords:
        if dictRecord.get(sPreviousKey, "") != sExpected:
            return False
        sExpected = _fsHashChainedRecord(dictRecord)
    return True


def fnAppendAttributionEvent(
    filesRepo, dictWorkflow, sChannel, sActor, sDetail,
):
    """Record one mutation-channel event; no-op unless supervised."""
    if not fbSupervisionEnabled(dictWorkflow):
        return
    listEvents = flistLoadAttributionEvents(filesRepo)
    _fnAppendJsonlRecord(filesRepo, S_ATTRIBUTION_EVENTS_PATH, {
        "sChannel": sChannel,
        "sActor": sActor,
        "sDetail": sDetail,
        "sTimestampUtc": _fsCurrentTimestamp(),
        _S_PREVIOUS_EVENT_KEY: (
            _fsHashChainedRecord(listEvents[-1]) if listEvents else ""
        ),
    })


def flistLoadAttributionEvents(filesRepo):
    """Return every recorded attribution event, oldest first."""
    return _flistLoadJsonlRecords(filesRepo, S_ATTRIBUTION_EVENTS_PATH)


def fbVerifyEventChain(listEvents):
    """Return True iff the attribution-event hash chain is intact."""
    return _fbVerifyHashChain(listEvents, _S_PREVIOUS_EVENT_KEY)


def _flistTimestampedEvents(listEvents, fNowEpoch):
    """Return ``(fEpoch, dictEvent)`` for credibly-stamped events.

    Unparseable stamps are dropped, and so are future-dated ones: the
    events file lives in the container-writable repository and the
    supervised party has a shell, so one forward-dated line would
    otherwise attribute every later change forever — silently, because
    a permanently-satisfied watchdog writes nothing at all.
    """
    listTimestamped = []
    for dictEvent in listEvents:
        dtEvent = fdatetimeParseTimestampAsUtc(dictEvent.get("sTimestampUtc"))
        if dtEvent is None:
            continue
        fEpoch = dtEvent.timestamp()
        if fEpoch > fNowEpoch:
            continue
        listTimestamped.append((fEpoch, dictEvent))
    return sorted(listTimestamped, key=lambda tRecord: tRecord[0])


def _fbInsideTerminalSession(listTimestamped, fAnchorEpoch, fNowEpoch):
    """Return True iff the anchor falls inside an open terminal span.

    A terminal is a channel that stays open: it emits one event when
    the session opens and one when it closes. Judging it as two
    instants flags every ordinary edit made minutes into a session, so
    the span between the two events is the attributive interval, and a
    session with no close yet runs to now. Concurrent sessions are
    counted, so the interval closes only when the last one ends.
    """
    iOpenCount = 0
    fSpanStart = 0.0
    for fEpoch, dictEvent in listTimestamped:
        if dictEvent.get("sChannel") != S_TERMINAL_CHANNEL:
            continue
        sDetail = dictEvent.get("sDetail")
        if sDetail == S_TERMINAL_OPENED_DETAIL:
            if iOpenCount == 0:
                fSpanStart = fEpoch
            iOpenCount += 1
        elif sDetail == S_TERMINAL_CLOSED_DETAIL and iOpenCount > 0:
            iOpenCount -= 1
            if iOpenCount == 0 and fSpanStart <= fAnchorEpoch <= fEpoch:
                return True
    return iOpenCount > 0 and fSpanStart <= fAnchorEpoch <= fNowEpoch


def _flistAttributionAnchors(fNowEpoch, fChangeEpoch):
    """Return the epochs a recorded event may be measured against."""
    if fChangeEpoch is None:
        return [fNowEpoch]
    try:
        return [fNowEpoch, float(fChangeEpoch)]
    except (TypeError, ValueError):
        return [fNowEpoch]


def fbEventsAccountForChange(
    listEvents, fChangeEpoch=None,
    fWindowSeconds=F_ATTRIBUTION_WINDOW_SECONDS,
):
    """Return True iff a recorded channel accounts for one change.

    The single derivation of the attribution rule; every production
    judgment routes through it. A change is attributed when a recorded
    event landed within ``fWindowSeconds`` of the change's own
    modification time, or of the moment of judgment, or while a
    terminal session was open. The union of the two point anchors is
    deliberate: anchoring only on "now" false-flags a change judged by
    a tick that arrived late, and anchoring only on the mtime would
    make attribution hostage to clock skew between the container's
    filesystem and the host that stamps the events.

    Ages are bounded below as well as above — see
    :func:`_flistTimestampedEvents` for why a future-dated event is
    never attributive.
    """
    fNowEpoch = datetime.now(timezone.utc).timestamp()
    listTimestamped = _flistTimestampedEvents(listEvents, fNowEpoch)
    listAnchors = _flistAttributionAnchors(fNowEpoch, fChangeEpoch)
    for fEpoch, _dictEvent in listTimestamped:
        for fAnchorEpoch in listAnchors:
            if abs(fAnchorEpoch - fEpoch) <= fWindowSeconds:
                return True
    return any(
        _fbInsideTerminalSession(listTimestamped, fAnchorEpoch, fNowEpoch)
        for fAnchorEpoch in listAnchors
    )


def fbAnyEventWithinWindow(
    filesRepo, fWindowSeconds=F_ATTRIBUTION_WINDOW_SECONDS,
    fChangeEpoch=None,
):
    """Load the events file and judge one change against it."""
    return fbEventsAccountForChange(
        flistLoadAttributionEvents(filesRepo), fChangeEpoch,
        fWindowSeconds,
    )


def fdictAppendFlag(filesRepo, sFlagKind, sDetail):
    """Append one permanent, chained flag record and return it."""
    listFlags = flistLoadFlags(filesRepo)
    dictFlag = {
        "sFlagKind": sFlagKind,
        "sDetail": sDetail,
        "sTimestampUtc": _fsCurrentTimestamp(),
        _S_PREVIOUS_FLAG_KEY: (
            _fsHashChainedRecord(listFlags[-1]) if listFlags else ""
        ),
    }
    _fnAppendJsonlRecord(filesRepo, S_ATTRIBUTION_FLAGS_PATH, dictFlag)
    return dictFlag


def flistLoadFlags(filesRepo):
    """Return every recorded flag, oldest first."""
    return _flistLoadJsonlRecords(filesRepo, S_ATTRIBUTION_FLAGS_PATH)


def fbVerifyFlagChain(listFlags):
    """Return True iff the flag hash chain is intact."""
    return _fbVerifyHashChain(listFlags, _S_PREVIOUS_FLAG_KEY)


def _fiPersistedFlagCount(dictSupervision):
    """Return the persisted flag count, ``-1`` when it is not a number."""
    try:
        return int(dictSupervision.get("iUnattributedFlagCount") or 0)
    except (TypeError, ValueError):
        return -1


def fdictSummarizeSupervisionEvidence(filesRepo, dictWorkflow=None):
    """Recompute the supervision verdict from the append-only files.

    The persisted ``iUnattributedFlagCount`` never grades anything —
    the in-container agent edits ``project.json`` routinely, so a gate
    that reads it is gradeable on self-report. It is used only as an
    anchor: both chains are prefix-valid, so deleting trailing records
    (or the whole file, which verifies "intact" with count zero) is
    invisible to the chain but visible as a disagreement with the
    count the hub last persisted.
    """
    listFlags = flistLoadFlags(filesRepo)
    dictSupervision = (
        ((dictWorkflow or {}).get("dictAiProvenance") or {})
        .get("dictSupervision") or {}
    )
    return {
        "iFlagCount": len(listFlags),
        "bFlagChainIntact": fbVerifyFlagChain(listFlags),
        "bEventChainIntact": fbVerifyEventChain(
            flistLoadAttributionEvents(filesRepo),
        ),
        "bPersistedFlagCountMatches": (
            dictWorkflow is None
            or _fiPersistedFlagCount(dictSupervision) == len(listFlags)
        ),
        "bHostAnchorConsistent": _fbHostAnchorConsistent(
            listFlags, dictWorkflow,
        ),
        "listFlags": listFlags,
    }


def _fsFlagChainHead(listFlags):
    """Return the digest of the last flag record, or '' when empty."""
    if not listFlags:
        return ""
    return _fsHashChainedRecord(listFlags[-1])


def _fbHostAnchorConsistent(listFlags, dictWorkflow):
    """Return False only when the host anchor is actively contradicted.

    ``bPersistedFlagCountMatches`` compares two artifacts that live in
    the same container-writable repository, so an agent that truncates
    ``flags.jsonl`` *and* zeroes ``project.json``'s count leaves them
    agreeing. The host anchor is the half that closes: it lives under
    the researcher's home directory, outside the container the
    supervised party is confined to.

    Returns True when the anchor is absent (nothing to contradict) —
    absence of evidence is not evidence of tampering, and the anchor
    seeds itself on the first observation.

    Nothing is read or written unless supervision is actually enabled
    for this workflow. Anchoring every polled project would accumulate
    a file per repository forever, and would make a legitimately
    deleted-and-recreated repository read as tampered for the life of
    the stale anchor.
    """
    from vaibify.gui import supervisionAnchor

    if not fbSupervisionEnabled(dictWorkflow or {}):
        return True
    sKey = supervisionAnchor.fsAnchorKeyFor(
        (dictWorkflow or {}).get("sProjectRepoPath") or "",
    )
    if not sKey:
        return True
    sHead = _fsFlagChainHead(listFlags)
    dictAnchor = supervisionAnchor.fdictReadAnchor(sKey)
    if supervisionAnchor.fbAnchorContradictedBy(
        dictAnchor, listFlags, sHead,
    ):
        return False
    supervisionAnchor.fnRecordAnchor(sKey, len(listFlags), sHead)
    return True
