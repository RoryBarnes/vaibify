"""The record of moving a project's Zenodo tokens out of its container.

One file per project, ``~/.vaibify/migrations/zenodo-<sProjectUuid>.json``,
mode 600 in a mode-700 directory, written atomically. It holds slot
names and states only, never a value and never a digest of one. It is a
RECORD, not the source of truth: every migration step re-derives its
state from the slots themselves and uses the journal to know which
container slots are finished and what the researcher has decided.

Per container slot, the states are:

- ``absent``: the container slot held nothing; finished.
- ``conflict``: the host already holds a different value for this slot's
  instance; nothing was written or deleted, and the researcher decides.
- ``staged``: the value is durably in the host's pending slot (verified by
  read-back, or equal to what the host already held).
- ``stageFailed``: the host keyring did not take the value; nothing in
  the container was touched; retried at the next connect.
- ``removed``: the container copy is gone (confirmed by a second read).
- ``sourceKept``: a host project's legacy slot is the researcher's own
  keyring and is not deleted.
- ``activated``: the researcher confirmed the token; it is active.
- ``declined``: the researcher discarded the token.

``dictValidation`` records the host-side probe of a pending token:
``accepted``, ``rejected`` or ``undetermined``, with when. No outcome
discards a token and no outcome activates one.
"""

__all__ = [
    "S_STATE_ABSENT",
    "S_STATE_ACTIVATED",
    "S_STATE_CONFLICT",
    "S_STATE_DECLINED",
    "S_STATE_REMOVED",
    "S_STATE_SOURCE_KEPT",
    "S_STATE_STAGED",
    "S_STATE_STAGE_FAILED",
    "SET_FINISHED_STATES",
    "fsMigrationsDirectory",
    "fdictReadJournal",
    "fdictSlotRecord",
    "fnRecordSlotState",
    "fnRecordValidation",
    "fnRecordConflictChoice",
    "fnRecordRotationAdvice",
]

import json
import os
from datetime import datetime, timezone

from vaibify.config.durableFile import fnWriteFileDurably

S_STATE_ABSENT = "absent"
S_STATE_CONFLICT = "conflict"
S_STATE_STAGED = "staged"
S_STATE_STAGE_FAILED = "stageFailed"
S_STATE_REMOVED = "removed"
S_STATE_SOURCE_KEPT = "sourceKept"
S_STATE_ACTIVATED = "activated"
S_STATE_DECLINED = "declined"

SET_FINISHED_STATES = frozenset({
    S_STATE_ABSENT, S_STATE_ACTIVATED, S_STATE_DECLINED,
})


def fsMigrationsDirectory():
    """Return ``~/.vaibify/migrations``, resolved when asked so tests can move HOME."""
    return os.path.join(os.path.expanduser("~"), ".vaibify", "migrations")


def _fsJournalPath(sProjectUuid):
    if not sProjectUuid or "/" in sProjectUuid or sProjectUuid.startswith("."):
        raise ValueError(f"not a project identity: {sProjectUuid!r}")
    return os.path.join(fsMigrationsDirectory(), f"zenodo-{sProjectUuid}.json")


def _fsNow():
    return datetime.now(timezone.utc).isoformat()


def fdictReadJournal(sProjectUuid):
    """Return the project's journal; an absent file is an empty journal."""
    sPath = _fsJournalPath(sProjectUuid)
    try:
        with open(sPath, "r", encoding="utf-8") as fileJournal:
            dictJournal = json.load(fileJournal)
    except FileNotFoundError:
        return {"sProjectUuid": sProjectUuid, "dictSlots": {}}
    if not isinstance(dictJournal, dict) or not isinstance(
        dictJournal.get("dictSlots"), dict,
    ):
        raise ValueError(f"the migration journal {sPath} is not well formed")
    return dictJournal


def _fnWriteJournal(sProjectUuid, dictJournal):
    sDirectory = fsMigrationsDirectory()
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    os.chmod(sDirectory, 0o700)
    fnWriteFileDurably(
        _fsJournalPath(sProjectUuid),
        json.dumps(dictJournal, indent=2, sort_keys=True).encode("utf-8"),
        iMode=0o600,
    )


def fdictSlotRecord(sProjectUuid, sContainerSlot):
    """Return one container slot's record, or an empty one."""
    dictRecord = fdictReadJournal(sProjectUuid)["dictSlots"].get(sContainerSlot)
    return dict(dictRecord) if isinstance(dictRecord, dict) else {}


def _fnUpdateSlot(sProjectUuid, sContainerSlot, dictFields):
    dictJournal = fdictReadJournal(sProjectUuid)
    dictSlot = dictJournal["dictSlots"].setdefault(sContainerSlot, {})
    dictSlot.update(dictFields)
    dictSlot["sUpdatedAt"] = _fsNow()
    _fnWriteJournal(sProjectUuid, dictJournal)


def fnRecordSlotState(sProjectUuid, sContainerSlot, sState, sReason=""):
    """Record where one container slot's migration has got to."""
    _fnUpdateSlot(sProjectUuid, sContainerSlot, {
        "sState": sState, "sReason": str(sReason or ""),
    })


def fnRecordValidation(
    sProjectUuid, sContainerSlot, sOutcome, sDetail, sAcceptedInstance,
):
    """Record the host-side probe of the pending token, with its time."""
    _fnUpdateSlot(sProjectUuid, sContainerSlot, {
        "dictValidation": {
            "sOutcome": sOutcome, "sDetail": str(sDetail or ""),
            "sAt": _fsNow(),
        },
        "sProposedInstance": str(sAcceptedInstance or ""),
    })


def fnRecordConflictChoice(sProjectUuid, sContainerSlot, sChoice):
    """Record the researcher's resolution of a conflict for one slot."""
    _fnUpdateSlot(sProjectUuid, sContainerSlot, {"sConflictChoice": sChoice})


def fnRecordRotationAdvice(sProjectUuid):
    """Note that a token was found in the container, so rotation is advised."""
    dictJournal = fdictReadJournal(sProjectUuid)
    if dictJournal.get("bRotationAdvised"):
        return
    dictJournal["bRotationAdvised"] = True
    dictJournal["sRotationAdvisedAt"] = _fsNow()
    _fnWriteJournal(sProjectUuid, dictJournal)
