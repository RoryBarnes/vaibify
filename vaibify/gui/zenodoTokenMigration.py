"""Moving a project's Zenodo tokens out of its container, without losing one.

Until 2026-10-29 a container project's Zenodo token lived in the
container keyring, which the in-container agent can read and write.
Tokens now live only in per-project ACTIVE slots of the host keyring
(:mod:`vaibify.config.zenodoTokenSlots`), and every token-bearing action
reads only those. This module is the one-way move, run at every connect
until each container slot is finished, and designed so that the move
can neither lose a token nor activate one the researcher has not
confirmed:

1. **Read** the container slot (a typed read as the container user). An
   absent slot is recorded ``absent`` and is done.
2. **Stage** it on the host, in the PENDING slot, and read it back; only
   an equal read-back records ``staged``. A value the host already holds
   active is ``staged`` at once (the equality is the verification). A
   DIFFERENT value already on the host is a ``conflict``: nothing is
   written or deleted until the researcher chooses.
3. **Remove** the container copy, only once the journal records
   ``staged``; a second read confirms it is gone before ``removed`` is
   recorded. The copy adds only exposure once the host holds the value.
4. **Validate** the pending token on the host: ``accepted``, ``rejected``
   or ``undetermined``, with a timestamp. No outcome discards a token and
   no outcome activates one; undetermined is retried.
5. **The researcher decides** (:mod:`vaibify.gui.routes.zenodoTokenRoutes`):
   confirm writes the active slot, reads it back, deletes the pending
   slot and records ``activated``; decline deletes the pending slot and
   records ``declined``. A token is discarded only by that explicit act.

The journal (:mod:`vaibify.config.zenodoMigrationJournal`) is a record,
not the source of truth: every step re-derives its state from the slots
themselves, so a crash anywhere is finished by the next pass. A host
project's legacy slots live in the researcher's own keyring and are
staged but never deleted (``sourceKept``).
"""

__all__ = [
    "S_DECISION_CONFIRM",
    "S_DECISION_DECLINE",
    "S_DECISION_KEEP_HOST",
    "S_DECISION_STAGE_PENDING",
    "T_DECISIONS",
    "TokenSource",
    "ftContainerTokenSource",
    "ftHostTokenSource",
    "ftSourceFor",
    "fdictRunMigrationPass",
    "fnRunMigrationPassQuietly",
    "fdictDescribeMigration",
    "fdictDecideMigratedToken",
]

import logging
from typing import Callable, NamedTuple

from vaibify.config import zenodoMigrationJournal as journal
from vaibify.config import zenodoTokenSlots as slots
from vaibify.config.registryManager import fbIsHostProject
from vaibify.config.zenodoTokenSlots import TokenStoreError
from vaibify.reproducibility import zenodoClient

from . import zenodoTokenAccess

logger = logging.getLogger(__name__)

S_DECISION_CONFIRM = "confirm"
S_DECISION_DECLINE = "decline"
S_DECISION_KEEP_HOST = "keep-host"
S_DECISION_STAGE_PENDING = "stage-pending"
T_DECISIONS = (
    S_DECISION_CONFIRM, S_DECISION_DECLINE, S_DECISION_KEEP_HOST,
    S_DECISION_STAGE_PENDING,
)

_S_REASON_ALREADY_ACTIVE = "already active on the host"
_S_DISPOSITION_STAGED = "staged"
_S_DISPOSITION_ALREADY_ACTIVE = "alreadyActive"
_S_DISPOSITION_CONFLICT = "conflict"
_S_DISPOSITION_FAILED = "stageFailed"
_S_DISPOSITION_DISCARD = "discard"

# What a slot becomes once its container copy is confirmed gone.
_DICT_FINAL_STATE = {
    _S_DISPOSITION_STAGED: "removed",
    _S_DISPOSITION_ALREADY_ACTIVE: "activated",
    _S_DISPOSITION_DISCARD: "declined",
    _S_DISPOSITION_FAILED: "stageFailed",
}

DICT_TOKEN_PAGES = {
    "sandbox": "https://sandbox.zenodo.org/account/settings/applications/",
    "production": "https://zenodo.org/account/settings/applications/",
}


class TokenSource(NamedTuple):
    """Where a project's legacy Zenodo slots are read from, and whether they go."""

    fsRead: Callable[[str], str]
    fnDelete: Callable[[str], None]
    bRemovesSource: bool


def ftContainerTokenSource(connectionDocker, sContainerId):
    """The container keyring of a container project: read as the user, then deleted."""
    from .syncDispatcher import fnDeleteCredentialFromContainer

    def fsRead(sSlot):
        return connectionDocker.fsFetchKeyringSecret(sContainerId, sSlot)

    def fnDelete(sSlot):
        fnDeleteCredentialFromContainer(connectionDocker, sContainerId, sSlot)

    return TokenSource(fsRead, fnDelete, True)


def ftHostTokenSource():
    """A host project's legacy slots: the researcher's own keyring, never deleted."""

    def fnNeverDelete(sSlot):
        raise AssertionError(
            f"a host project's slot {sSlot!r} is never deleted by migration"
        )

    return TokenSource(slots.fsReadSlot, fnNeverDelete, False)


def ftSourceFor(connectionDocker, sContainerId):
    """Return the token source a project's legacy slots are read from."""
    if fbIsHostProject(sContainerId):
        return ftHostTokenSource()
    return ftContainerTokenSource(connectionDocker, sContainerId)


def fdictRunMigrationPass(connectionDocker, sContainerId):
    """Advance every unfinished container slot as far as it can go now."""
    sProjectUuid = zenodoTokenAccess.fsProjectUuidForContainer(
        connectionDocker, sContainerId,
    )
    if not sProjectUuid:
        return {"bApplicable": False, "sProjectUuid": ""}
    tSource = ftSourceFor(connectionDocker, sContainerId)
    for sSlot in slots.LIST_CONTAINER_ZENODO_SLOTS:
        _fnMigrateOneSlot(sProjectUuid, sSlot, tSource)
    return fdictDescribeMigration(connectionDocker, sContainerId)


def fnRunMigrationPassQuietly(connectionDocker, sContainerId):
    """Run a pass at connect; a failure is logged, never the connect's."""
    try:
        fdictRunMigrationPass(connectionDocker, sContainerId)
    except Exception:  # noqa: BLE001 -- connect must not fail on a keyring
        logger.warning(
            "Zenodo token migration pass failed for %s; it is retried at "
            "the next connect", sContainerId, exc_info=True,
        )


def _fnMigrateOneSlot(sProjectUuid, sSlot, tSource):
    dictRecord = journal.fdictSlotRecord(sProjectUuid, sSlot)
    if dictRecord.get("sState") in journal.SET_FINISHED_STATES:
        return
    try:
        sValue = tSource.fsRead(sSlot)
    except LookupError as error:
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, journal.S_STATE_STAGE_FAILED,
            f"the container keyring could not be read: {error}",
        )
        return
    if not sValue:
        _fnFinishAbsentSource(sProjectUuid, sSlot, dictRecord, tSource)
        _fnProbeOrFinishPending(sProjectUuid, sSlot)
        return
    if tSource.bRemovesSource:
        journal.fnRecordRotationAdvice(sProjectUuid)
    sDisposition = _fsStage(sProjectUuid, sSlot, sValue, dictRecord)
    if sDisposition == _S_DISPOSITION_CONFLICT:
        return
    # The removal decides for itself whether the journal authorizes it
    # (staged, or the researcher's keep-host choice): a stage that
    # failed reaches it too, and must leave the copy where it is.
    _fnRemoveSourceCopy(sProjectUuid, sSlot, tSource, _DICT_FINAL_STATE[sDisposition])
    if sDisposition in (_S_DISPOSITION_STAGED, _S_DISPOSITION_ALREADY_ACTIVE):
        _fnProbeOrFinishPending(sProjectUuid, sSlot)


def _flistInstanceCandidates(sSlot):
    sInstance = slots.fsInstanceOfContainerSlot(sSlot)
    return [sInstance] if sInstance else list(slots.T_ZENODO_INSTANCES)


def _fbAlreadyActive(sProjectUuid, sSlot, sValue):
    return any(
        slots.fsReadActiveToken(sInstance, sProjectUuid) == sValue
        for sInstance in _flistInstanceCandidates(sSlot)
    )


def _fsStage(sProjectUuid, sSlot, sValue, dictRecord):
    """Put the value in the pending slot, or say why not; never overwrite silently."""
    sPending = slots.fsPendingSlot(sSlot, sProjectUuid)
    sChoice = dictRecord.get("sConflictChoice", "")
    if _fbAlreadyActive(sProjectUuid, sSlot, sValue):
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, journal.S_STATE_STAGED, _S_REASON_ALREADY_ACTIVE,
        )
        return _S_DISPOSITION_ALREADY_ACTIVE
    if sChoice == S_DECISION_KEEP_HOST:
        return _S_DISPOSITION_DISCARD
    sPendingValue = slots.fsReadSlot(sPending)
    if sPendingValue == sValue:
        journal.fnRecordSlotState(sProjectUuid, sSlot, journal.S_STATE_STAGED)
        return _S_DISPOSITION_STAGED
    sConflict = _fsDescribeConflict(sProjectUuid, sSlot, sPendingValue)
    if sConflict and sChoice != S_DECISION_STAGE_PENDING:
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, journal.S_STATE_CONFLICT, sConflict,
        )
        return _S_DISPOSITION_CONFLICT
    try:
        slots.fnStoreSlotVerified(sPending, sValue)
    except TokenStoreError as error:
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, journal.S_STATE_STAGE_FAILED, str(error),
        )
        return _S_DISPOSITION_FAILED
    journal.fnRecordSlotState(sProjectUuid, sSlot, journal.S_STATE_STAGED)
    return _S_DISPOSITION_STAGED


def _fsDescribeConflict(sProjectUuid, sSlot, sPendingValue):
    """Return why staging would overwrite a host value, or '' when it would not."""
    sInstance = slots.fsInstanceOfContainerSlot(sSlot)
    if sInstance and slots.fsReadActiveToken(sInstance, sProjectUuid):
        return (
            f"the host already holds a different active {sInstance} token "
            f"for this project"
        )
    if sPendingValue:
        return "the host already holds a different pending token for this slot"
    return ""


def _fnRemoveSourceCopy(sProjectUuid, sSlot, tSource, sFinalState):
    """Delete the container copy, only when the journal authorizes it.

    The authorization is the journal recording ``staged`` (the value is
    durably on the host) or the researcher's explicit ``keep-host``
    choice (they discarded it). Anything else leaves the copy where it
    is: a delete before the stage is confirmed is the one way this move
    can lose a token.
    """
    dictRecord = journal.fdictSlotRecord(sProjectUuid, sSlot)
    bAuthorized = (
        dictRecord.get("sState") == journal.S_STATE_STAGED
        or dictRecord.get("sConflictChoice") == S_DECISION_KEEP_HOST
    )
    if not bAuthorized:
        return
    if not tSource.bRemovesSource:
        journal.fnRecordSlotState(sProjectUuid, sSlot, (
            sFinalState if sFinalState != journal.S_STATE_REMOVED
            else journal.S_STATE_SOURCE_KEPT
        ))
        return
    try:
        tSource.fnDelete(sSlot)
        sRemaining = tSource.fsRead(sSlot)
    except (LookupError, RuntimeError) as error:
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, dictRecord.get("sState", ""),
            f"the container copy could not be removed yet: {error}",
        )
        return
    if sRemaining:
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, dictRecord.get("sState", ""),
            "the container copy is still present after the delete",
        )
        return
    journal.fnRecordSlotState(sProjectUuid, sSlot, sFinalState)


def _fnFinishAbsentSource(sProjectUuid, sSlot, dictRecord, tSource):
    """Settle a slot whose tSource copy is gone, from what the host holds."""
    sState = dictRecord.get("sState", "")
    if not sState:
        journal.fnRecordSlotState(sProjectUuid, sSlot, journal.S_STATE_ABSENT)
        return
    if sState == journal.S_STATE_STAGED and tSource.bRemovesSource:
        journal.fnRecordSlotState(sProjectUuid, sSlot, (
            journal.S_STATE_ACTIVATED
            if dictRecord.get("sReason") == _S_REASON_ALREADY_ACTIVE
            else journal.S_STATE_REMOVED
        ))
        return
    sPending = slots.fsPendingSlot(sSlot, sProjectUuid)
    if sState in (journal.S_STATE_CONFLICT, journal.S_STATE_STAGE_FAILED) and (
        not slots.fsReadSlot(sPending)
    ):
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, journal.S_STATE_ABSENT,
            "the tSource slot emptied before anything was staged",
        )


def _fnProbeOrFinishPending(sProjectUuid, sSlot):
    """Validate a pending token, or finish a confirmation a crash interrupted."""
    sPending = slots.fsPendingSlot(sSlot, sProjectUuid)
    sPendingValue = slots.fsReadSlot(sPending)
    if not sPendingValue:
        return
    if _fbAlreadyActive(sProjectUuid, sSlot, sPendingValue):
        slots.fnDeleteSlot(sPending)
        journal.fnRecordSlotState(
            sProjectUuid, sSlot, journal.S_STATE_ACTIVATED, "confirmed",
        )
        return
    dictValidation = journal.fdictSlotRecord(sProjectUuid, sSlot).get(
        "dictValidation",
    ) or {}
    if dictValidation.get("sOutcome") in (
        zenodoClient.S_PROBE_ACCEPTED, zenodoClient.S_PROBE_REJECTED,
    ):
        return
    _fnProbePending(sProjectUuid, sSlot, sPendingValue)


def _fnProbePending(sProjectUuid, sSlot, sPendingValue):
    """Ask each candidate instance about the token; record, never act."""
    sOutcome = zenodoClient.S_PROBE_REJECTED
    sDetail = ""
    for sInstance in _flistInstanceCandidates(sSlot):
        dictProbe = zenodoClient.fdictProbeToken(
            zenodoTokenAccess.fsServiceForInstance(sInstance), sPendingValue,
        )
        if dictProbe["sOutcome"] == zenodoClient.S_PROBE_ACCEPTED:
            journal.fnRecordValidation(
                sProjectUuid, sSlot, zenodoClient.S_PROBE_ACCEPTED, "",
                sInstance,
            )
            return
        if dictProbe["sOutcome"] == zenodoClient.S_PROBE_UNDETERMINED:
            sOutcome = zenodoClient.S_PROBE_UNDETERMINED
        sDetail = dictProbe["sDetail"]
    journal.fnRecordValidation(sProjectUuid, sSlot, sOutcome, sDetail, "")


def fdictDescribeMigration(connectionDocker, sContainerId):
    """Return what the dashboard shows: slots, states, validation, evidence."""
    sProjectUuid = zenodoTokenAccess.fsProjectUuidForContainer(
        connectionDocker, sContainerId,
    )
    if not sProjectUuid:
        return {"bApplicable": False, "sProjectUuid": ""}
    dictJournal = journal.fdictReadJournal(sProjectUuid)
    listSlots = [
        _fdictDescribeSlot(sProjectUuid, sSlot, dictJournal["dictSlots"].get(sSlot) or {})
        for sSlot in slots.LIST_CONTAINER_ZENODO_SLOTS
        if dictJournal["dictSlots"].get(sSlot)
    ]
    return {
        "bApplicable": True,
        "sProjectUuid": sProjectUuid,
        "dictActive": {
            sInstance: slots.fbHasActiveToken(sInstance, sProjectUuid)
            for sInstance in slots.T_ZENODO_INSTANCES
        },
        "listSlots": listSlots,
        "bAwaitingDecision": any(dictSlot["bAwaitingDecision"] for dictSlot in listSlots),
        "bRotationAdvised": bool(dictJournal.get("bRotationAdvised")),
        "dictTokenPages": dict(DICT_TOKEN_PAGES),
    }


def _fdictDescribeSlot(sProjectUuid, sSlot, dictRecord):
    sPending = slots.fsPendingSlot(sSlot, sProjectUuid)
    sPendingValue = slots.fsReadSlot(sPending)
    sState = dictRecord.get("sState", "")
    dictValidation = dict(dictRecord.get("dictValidation") or {})
    sProposedInstance = dictRecord.get("sProposedInstance", "") or (
        slots.fsInstanceOfContainerSlot(sSlot)
    )
    return {
        "sSlot": sSlot,
        "sState": sState,
        "sReason": dictRecord.get("sReason", ""),
        "sInstance": slots.fsInstanceOfContainerSlot(sSlot),
        "sProposedInstance": sProposedInstance,
        "bPending": bool(sPendingValue),
        "dictValidation": dictValidation,
        "listDepositTitles": _flistTitlesForPending(
            sPendingValue, sProposedInstance, dictValidation,
        ),
        "sConflictChoice": dictRecord.get("sConflictChoice", ""),
        "bAwaitingDecision": bool(sPendingValue) or sState == journal.S_STATE_CONFLICT,
    }


def _flistTitlesForPending(sPendingValue, sInstance, dictValidation):
    """Fetch what Zenodo reports for an accepted pending token, for the researcher's eyes."""
    if not sPendingValue or not sInstance or (
        dictValidation.get("sOutcome") != zenodoClient.S_PROBE_ACCEPTED
    ):
        return []
    return zenodoClient.fdictProbeToken(
        zenodoTokenAccess.fsServiceForInstance(sInstance), sPendingValue,
    )["listDepositTitles"]


def fdictDecideMigratedToken(
    connectionDocker, sContainerId, sSlot, sDecision, sInstance="",
    bReplaceActive=False,
):
    """Apply the researcher's decision about one slot; return the new description.

    Raises ``ValueError`` with the sentence to show when the decision
    cannot be applied as asked. ``keep-host`` and ``stage-pending``
    resolve a conflict and are carried out by the pass that follows;
    ``confirm`` and ``decline`` act on the pending slot directly.
    """
    if sDecision not in T_DECISIONS:
        raise ValueError(
            f"{sDecision!r} is not a decision; the decisions are {list(T_DECISIONS)}"
        )
    if sSlot not in slots.LIST_CONTAINER_ZENODO_SLOTS:
        raise ValueError(f"{sSlot!r} is not a container Zenodo slot")
    sProjectUuid = zenodoTokenAccess.fsProjectUuidForContainer(
        connectionDocker, sContainerId,
    )
    if not sProjectUuid:
        raise ValueError("This container is not a registered project.")
    if sDecision == S_DECISION_CONFIRM:
        _fnConfirmPending(sProjectUuid, sSlot, sInstance, bReplaceActive)
    elif sDecision == S_DECISION_DECLINE:
        _fnDeclinePending(sProjectUuid, sSlot)
    else:
        journal.fnRecordConflictChoice(sProjectUuid, sSlot, sDecision)
    return fdictRunMigrationPass(connectionDocker, sContainerId)


def _fsResolveConfirmInstance(sProjectUuid, sSlot, sInstance):
    sKnown = slots.fsInstanceOfContainerSlot(sSlot)
    if sKnown and sInstance and sInstance != sKnown:
        raise ValueError(
            f"slot {sSlot!r} holds a {sKnown} token; it cannot be "
            f"confirmed for {sInstance}"
        )
    sChosen = sKnown or sInstance or (
        journal.fdictSlotRecord(sProjectUuid, sSlot).get("sProposedInstance", "")
    )
    if sChosen not in slots.T_ZENODO_INSTANCES:
        raise ValueError(
            "this token's instance is not known; say which instance "
            "(sandbox or production) it belongs to"
        )
    return sChosen


def _fnConfirmPending(sProjectUuid, sSlot, sInstance, bReplaceActive):
    sPending = slots.fsPendingSlot(sSlot, sProjectUuid)
    sPendingValue = slots.fsReadSlot(sPending)
    if not sPendingValue:
        raise ValueError(f"nothing is pending for slot {sSlot!r}")
    sChosen = _fsResolveConfirmInstance(sProjectUuid, sSlot, sInstance)
    sActive = slots.fsReadActiveToken(sChosen, sProjectUuid)
    if sActive and sActive != sPendingValue and not bReplaceActive:
        raise ValueError(
            f"an active {sChosen} token is already stored for this project; "
            f"confirm with bReplaceActive to replace it, or decline the "
            f"migrated one"
        )
    slots.fnStoreActiveToken(sChosen, sProjectUuid, sPendingValue)
    slots.fnDeleteSlot(sPending)
    journal.fnRecordSlotState(
        sProjectUuid, sSlot, journal.S_STATE_ACTIVATED, f"confirmed for {sChosen}",
    )


def _fnDeclinePending(sProjectUuid, sSlot):
    """Delete ONLY the pending slot; the active slots are untouched."""
    slots.fnDeleteSlot(slots.fsPendingSlot(sSlot, sProjectUuid))
    journal.fnRecordSlotState(
        sProjectUuid, sSlot, journal.S_STATE_DECLINED, "declined by the researcher",
    )
