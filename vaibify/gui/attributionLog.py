"""Supervised-mode attribution: recorded causes and permanent flags.

The Supervised state's claim is narrow and checkable: *every detected
change in the project repository during a supervised interval is
attributable to a recorded action channel*. Two append-only JSONL
files under ``.vaibify/promptRecord/attribution/`` carry it:

- ``events.jsonl`` — one record per recorded mutation-channel event
  (pipeline dispatch, editor save, agent-lane request, terminal
  session, context write): ``{sChannel, sActor, sDetail,
  sTimestampUtc}``.
- ``flags.jsonl`` — permanent findings: ``unattributed-modification``
  (files changed with no recorded cause inside the tolerance window)
  and ``unsupervised-gap`` (the repo changed while the hub was not
  watching). Flag records are hash-chained like Prompt Record
  captures, so deleting or editing one breaks
  :func:`fbVerifyFlagChain`; nothing in the codebase ever removes a
  flag. Permanence is convention + git history + the chain — a
  container-writable repository cannot host an unwritable store, and
  the docs say so.

Attribution granularity is the tolerance *window*, not the file
path: a change is attributed when any recorded event landed within
the window (a step run legitimately touches many files). That can
under-flag concurrent tampering during legitimate activity; it never
false-positives, and the docstring states the limit honestly.
"""

__all__ = [
    "S_ATTRIBUTION_EVENTS_PATH",
    "S_ATTRIBUTION_FLAGS_PATH",
    "F_ATTRIBUTION_WINDOW_SECONDS",
    "fbSupervisionEnabled",
    "fnAppendAttributionEvent",
    "fbAnyEventWithinWindow",
    "fnAppendFlag",
    "flistLoadFlags",
    "fbVerifyFlagChain",
]

import hashlib
import json
from datetime import datetime, timezone

_S_ATTRIBUTION_DIRECTORY = ".vaibify/promptRecord/attribution"
S_ATTRIBUTION_EVENTS_PATH = _S_ATTRIBUTION_DIRECTORY + "/events.jsonl"
S_ATTRIBUTION_FLAGS_PATH = _S_ATTRIBUTION_DIRECTORY + "/flags.jsonl"
F_ATTRIBUTION_WINDOW_SECONDS = 60.0


def _fsCurrentTimestamp():
    return datetime.now(timezone.utc).isoformat()


def fbSupervisionEnabled(dictWorkflow):
    """Return True iff the workflow opted into Supervised mode."""
    dictSupervision = (
        ((dictWorkflow or {}).get("dictAiProvenance") or {})
        .get("dictSupervision") or {}
    )
    return dictSupervision.get("bEnabled") is True


def _flistLoadJsonlRecords(filesRepo, sRelPath):
    """Parse one JSONL file into record dicts (missing file → [])."""
    if not filesRepo.fbIsFile(sRelPath):
        return []
    try:
        sText = filesRepo.fsReadText(sRelPath)
    except (OSError, FileNotFoundError):
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


def fnAppendAttributionEvent(
    filesRepo, dictWorkflow, sChannel, sActor, sDetail,
):
    """Record one mutation-channel event; no-op unless supervised."""
    if not fbSupervisionEnabled(dictWorkflow):
        return
    _fnAppendJsonlRecord(filesRepo, S_ATTRIBUTION_EVENTS_PATH, {
        "sChannel": sChannel,
        "sActor": sActor,
        "sDetail": sDetail,
        "sTimestampUtc": _fsCurrentTimestamp(),
    })


def fbAnyEventWithinWindow(
    filesRepo, fWindowSeconds=F_ATTRIBUTION_WINDOW_SECONDS,
):
    """Return True iff a recorded event landed inside the window."""
    dtNow = datetime.now(timezone.utc)
    for dictRecord in _flistLoadJsonlRecords(
        filesRepo, S_ATTRIBUTION_EVENTS_PATH,
    ):
        try:
            dtEvent = datetime.fromisoformat(
                dictRecord.get("sTimestampUtc") or "",
            )
        except ValueError:
            continue
        if (dtNow - dtEvent).total_seconds() <= fWindowSeconds:
            return True
    return False


def _fsHashFlagRecord(dictFlag):
    return hashlib.sha256(
        json.dumps(dictFlag, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def fnAppendFlag(filesRepo, sFlagKind, sDetail):
    """Append one permanent, chained flag record and return it."""
    listFlags = flistLoadFlags(filesRepo)
    dictFlag = {
        "sFlagKind": sFlagKind,
        "sDetail": sDetail,
        "sTimestampUtc": _fsCurrentTimestamp(),
        "sPreviousFlagSha256": (
            _fsHashFlagRecord(listFlags[-1]) if listFlags else ""
        ),
    }
    _fnAppendJsonlRecord(filesRepo, S_ATTRIBUTION_FLAGS_PATH, dictFlag)
    return dictFlag


def flistLoadFlags(filesRepo):
    """Return every recorded flag, oldest first."""
    return _flistLoadJsonlRecords(filesRepo, S_ATTRIBUTION_FLAGS_PATH)


def fbVerifyFlagChain(listFlags):
    """Return True iff the flag hash chain is intact."""
    sExpected = ""
    for dictFlag in listFlags:
        if dictFlag.get("sPreviousFlagSha256", "") != sExpected:
            return False
        sExpected = _fsHashFlagRecord(dictFlag)
    return True
