"""The proving reconciliation transaction for the operation journal.

Design §8: reconciliation is a proving transaction, never an
acknowledgement. A human can accept corrupt state, but cannot prove an
orphaned helper stopped writing — so the transaction proves it, in
order: take the EXCLUSIVE reconciliation lock while the marker stands
(the same container flock the claim path arbitrates on, so
reconcile-versus-claim is atomic — case 35); show which operation was
mid-write, when it was abandoned, and which container; prove every
recorded holder dead by recycle-proof identity and every Docker
operation settled via the 3a probe catalog — or refuse and STAY
quarantined (case 34); do operation-specific cleanup; clear the durable
marker LAST. A stale request carrying expected operation ids that no
longer match the on-disk set is refused, so it can never clear a
successor's records (case 42).

Recovery outcomes by record kind (design v11): a valid KNOWN kind gets
the operation-specific proving above; an unknown NEWER schema version
requires upgrading — never auto-clear, never blind-break; a MALFORMED
record clears only through the documented destructive break-glass
(:func:`fdictExecuteBreakGlass`), keyed on the sha256 of the raw marker
bytes (a malformed record has no parseable operation id), which first
stops every possibly-relevant container (case 46).

Two entry paths share this core. When NO live hub holds the container's
flock, :func:`fdictReconcileCrashTimeJournal` runs the whole transaction
directly under a freshly-taken reconciliation flock. When a live hub
holds the flock, the CLI routes the request to that hub over the host
control socket (:mod:`vaibify.gui.hostControlChannel`), whose handler
runs the same prove-then-clear on the hub event loop under the
container-mutation lock and clears the in-memory ``PoisonRecord``
before the marker.
"""

__all__ = [
    "ReconciliationRefusedError",
    "flistDescribeJournalRecords",
    "fdictProveJournalRecordsSettled",
    "fnCleanupAndClearProvenRecords",
    "fdictReconcileCrashTimeJournal",
    "fdictExecuteBreakGlass",
]

import logging
import os

from vaibify.config import containerLock, operationJournal
from vaibify.config.operationJournal import (
    DICT_OPERATION_PROBE_CATALOG,
    S_OPERATION_STATE_PREPARED,
)

logger = logging.getLogger("vaibify")

# The allowlisted display fields (design §8: error messages and the
# reconcile display never leak journal contents beyond these).
_TUPLE_DISPLAY_FIELDS = (
    "sState", "sKind", "sTarget", "sPreparedIso", "sInFlightIso", "sNote",
)


class ReconciliationRefusedError(RuntimeError):
    """The transaction refused: unproven writer, stale ids, or damage.

    ``sReadState`` carries the journal read state that caused the
    refusal (``"requiresUpgrade"``, ``"malformed"``, ...) when one did,
    so callers can route to the upgrade or break-glass path without
    sniffing the message text; ``""`` otherwise.
    """

    def __init__(self, sMessage, sReadState=""):
        super().__init__(sMessage)
        self.sReadState = sReadState


def flistDescribeJournalRecords(sContainerName):
    """Return display records for a container's journal, fail closed.

    Each entry carries ``sOperationId`` plus only the allowlisted
    display fields, so the CLI can show WHICH operation was mid-write,
    WHEN it was prepared/abandoned, and WHICH container, without
    echoing anything else. A non-valid journal raises with the read
    state so the caller can route to the upgrade or break-glass path.
    """
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        sContainerName,
    )
    sReadState = dictOutcomeRead["sReadState"]
    if sReadState == "absent":
        return []
    if sReadState != "valid":
        raise ReconciliationRefusedError(
            f"The journal for container '{sContainerName}' is "
            f"{sReadState}: {dictOutcomeRead['sDetail']}",
            sReadState=sReadState,
        )
    listRecords = []
    for sOperationId in sorted(dictOutcomeRead["dictOperations"]):
        dictRecord = dictOutcomeRead["dictOperations"][sOperationId]
        dictDisplay = {"sOperationId": sOperationId}
        for sField in _TUPLE_DISPLAY_FIELDS:
            dictDisplay[sField] = dictRecord.get(sField, "")
        listRecords.append(dictDisplay)
    return listRecords


def _fdictProvenOutcome(bProven, sRefusalReason="", sReadState="valid",
                        listClearableOperationIds=(), listRecordNotes=()):
    """Return the uniform proving-outcome shape."""
    return {
        "bProven": bProven,
        "sRefusalReason": sRefusalReason,
        "sReadState": sReadState,
        "listClearableOperationIds": list(listClearableOperationIds),
        "listRecordNotes": list(listRecordNotes),
    }


def fdictProveJournalRecordsSettled(
    sContainerName, connectionDocker, setExpectedOperationIds,
    fbSelfHolderProvenIdle=None,
):
    """Prove every journal record's writer dead and effect settled.

    The proving half of the transaction; it clears nothing. The caller
    must hold the exclusive reconciliation lock across this call AND
    the subsequent clear. ``setExpectedOperationIds`` is the ABA guard
    (case 42): the on-disk record ids must match it EXACTLY, so a stale
    request can neither clear a replacement record nor act on a set
    that grew since it was inspected. ``fbSelfHolderProvenIdle`` is the
    live-hub augmentation: a record whose holder PID is THIS process
    (a mode-(b) worker journaled under the hub's own pid) is proven
    dead not by ``os.kill`` — the hub is alive — but by the hub's own
    supervisor registry reporting no live guarded work.
    """
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        sContainerName,
    )
    sReadState = dictOutcomeRead["sReadState"]
    if sReadState == "absent":
        return _fdictProvenOutcome(True, sReadState="absent")
    if sReadState == "requiresUpgrade":
        return _fdictProvenOutcome(
            False, sReadState=sReadState,
            sRefusalReason=dictOutcomeRead["sDetail"],
        )
    if sReadState != "valid":
        return _fdictProvenOutcome(
            False, sReadState=sReadState,
            sRefusalReason=(
                "the journal is malformed and only the documented "
                f"break-glass may clear it ({dictOutcomeRead['sDetail']})"
            ),
        )
    dictOperations = dictOutcomeRead["dictOperations"]
    if set(dictOperations) != set(setExpectedOperationIds or ()):
        return _fdictProvenOutcome(
            False,
            sRefusalReason=(
                "the journal record set changed since it was inspected; "
                "a stale reconciliation may not act on a successor "
                "operation — inspect the container again"
            ),
        )
    return _fdictProveEachRecord(
        dictOperations, connectionDocker, fbSelfHolderProvenIdle,
    )


def _fdictProveEachRecord(
    dictOperations, connectionDocker, fbSelfHolderProvenIdle,
):
    """Fold per-record verdicts into one all-or-nothing proving outcome."""
    listClearable, listNotes = [], []
    for sOperationId in sorted(dictOperations):
        bClearable, sDetail = _ftReconcileRecordVerdict(
            dictOperations[sOperationId], connectionDocker,
            fbSelfHolderProvenIdle,
        )
        if not bClearable:
            return _fdictProvenOutcome(
                False,
                sRefusalReason=f"operation {sOperationId}: {sDetail}",
            )
        listClearable.append(sOperationId)
        listNotes.append(f"{sOperationId}: {sDetail}")
    return _fdictProvenOutcome(
        True, listClearableOperationIds=listClearable,
        listRecordNotes=listNotes,
    )


def _ftReconcileRecordVerdict(
    dictRecord, connectionDocker, fbSelfHolderProvenIdle,
):
    """Return ``(bClearable, sDetail)`` for one record.

    A live recorded writer, an indeterminate probe (unreachable
    verifier), or lingering residue refuses the whole transaction
    (case 34): reconciliation proves, it never assumes. The one
    acceptance beyond the probe's own "settled": a file-write whose
    writer is proven dead but whose target bytes are torn — nobody is
    still writing, and accepting the named corrupt state is exactly
    the judgement the reconciling human is making.
    """
    if dictRecord["sState"] == S_OPERATION_STATE_PREPARED:
        return (True, "prepared but never launched; nothing ever acted")
    dictHandler = DICT_OPERATION_PROBE_CATALOG.get(dictRecord["sKind"])
    if dictHandler is None:
        return (
            False,
            f"unknown operation kind {dictRecord['sKind']!r}; upgrade "
            "vaibify to a build that understands it",
        )
    dictEffectiveRecord = _fdictRecordForProbe(
        dictRecord, fbSelfHolderProvenIdle,
    )
    dictProbe = dictHandler["fdictProbe"](
        dictEffectiveRecord, connectionDocker,
    )
    if dictProbe["bHolderAlive"]:
        return (
            False,
            f"the recorded writer is still alive ({dictProbe['sDetail']}); "
            "reconciliation cannot clear a quarantine over a live writer",
        )
    if dictProbe["bSettled"]:
        return (True, dictProbe["sDetail"])
    if dictProbe["bIndeterminate"]:
        return (
            False,
            f"the operation cannot be proven settled "
            f"({dictProbe['sDetail']}); the container stays quarantined",
        )
    if dictRecord["sKind"] == "file-write":
        return (
            True,
            f"writer proven dead; {dictProbe['sDetail']} — reconciliation "
            "accepts the target's current content",
        )
    return (
        False,
        f"{dictProbe['sDetail']}; remove the residue, then reconcile "
        "again",
    )


def _fdictRecordForProbe(dictRecord, fbSelfHolderProvenIdle):
    """Substitute the in-process liveness proof for a self-held record.

    A mode-(b) worker journals the hub's own PID, which ``os.kill``
    reports alive for as long as the hub lives — so the live hub, and
    only the live hub, may substitute its supervisor registry's verdict:
    when it reports no live guarded work, the worker THREAD has
    terminated, and the record is probed with the process identity
    dropped so the kind-specific effect check still runs. Without the
    callable (the crash path) the record is probed unchanged.
    """
    if fbSelfHolderProvenIdle is None:
        return dictRecord
    if dictRecord.get("iHolderPid") != os.getpid():
        return dictRecord
    if not fbSelfHolderProvenIdle():
        return dictRecord
    return {
        sKey: valueField for sKey, valueField in dictRecord.items()
        if sKey not in ("iHolderPid", "iHolderProcessGroup")
    }


def fdictReconcileCrashTimeJournal(
    sContainerName, connectionDocker=None, setExpectedOperationIds=None,
    iPort=0,
):
    """Run the whole proving transaction under a fresh reconciliation lock.

    The crash-time path — no live hub holds the container. Acquires the
    exclusive reconciliation lock (the container flock, the same
    primitive claims arbitrate on), proves every record settled, runs
    each kind's cleanup handler, and clears the durable marker LAST,
    while the flock is still held — so a concurrent claim either loses
    the flock race outright or finds the journal already settled
    (case 35). Raises :class:`ReconciliationRefusedError` when a live
    hub holds the flock (route over its host control socket instead)
    or when any record cannot be proven settled.
    """
    try:
        fileHandleLock = containerLock.ffileAcquireReconciliationLock(
            sContainerName, iPort,
        )
    except containerLock.ContainerLockedError as error:
        raise ReconciliationRefusedError(
            f"A live vaibify process (pid={error.iHolderPid}, "
            f"port={error.iHolderPort}) holds container "
            f"'{sContainerName}'; route the reconciliation to that hub "
            "with 'vaibify reconcile' while it runs, or stop it first."
        )
    try:
        dictProven = fdictProveJournalRecordsSettled(
            sContainerName, connectionDocker, setExpectedOperationIds,
        )
        if not dictProven["bProven"]:
            raise ReconciliationRefusedError(
                dictProven["sRefusalReason"],
                sReadState=dictProven["sReadState"],
            )
        fnCleanupAndClearProvenRecords(
            sContainerName, dictProven["listClearableOperationIds"],
            connectionDocker,
        )
    finally:
        containerLock.fnReleaseContainerLock(fileHandleLock)
    logger.info(
        "Reconciled container '%s': cleared %d journal record(s)",
        sContainerName, len(dictProven["listClearableOperationIds"]),
    )
    return dictProven


def fnCleanupAndClearProvenRecords(
    sContainerName, listClearableOperationIds, connectionDocker,
):
    """Run each proven record's cleanup, then clear the marker LAST.

    Shared by BOTH reconciliation paths — the crash-time transaction
    and the live-hub socket handler — so the operation-specific cleanup
    can never diverge between them. Callers must hold the exclusive
    reconciliation arbitration (the container flock, or the live hub's
    container-mutation lock over its held flock) across this call.
    """
    if not listClearableOperationIds:
        return
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        sContainerName,
    )
    for sOperationId in listClearableOperationIds:
        dictRecord = dictOutcomeRead["dictOperations"].get(sOperationId)
        if dictRecord is None:
            continue
        dictHandler = DICT_OPERATION_PROBE_CATALOG.get(dictRecord["sKind"])
        if dictHandler is not None:
            dictHandler["fnCleanupAfterSettledProbe"](
                dictRecord, connectionDocker,
            )
    operationJournal.fnClearOperationsReconciled(
        sContainerName, listClearableOperationIds,
    )


def fdictExecuteBreakGlass(
    sContainerName, sExpectedSha256, fnStopContainerByName=None,
    iPort=0, bHoldFlock=True,
):
    """Destructively clear a MALFORMED marker whose bytes hash-match.

    Design §8/§6b, case 46: the break-glass first stops every possibly-
    relevant container — with a malformed record no helper identity is
    parseable, so the container bearing the journal's name is the
    reachable blast surface — then unlinks the marker only when its raw
    bytes still hash to ``sExpectedSha256``. ``bHoldFlock=False`` is for
    the live-hub handler, which already holds the container flock
    through its owner record.
    """
    fileHandleLock = None
    if bHoldFlock:
        try:
            fileHandleLock = containerLock.ffileAcquireReconciliationLock(
                sContainerName, iPort,
            )
        except containerLock.ContainerLockedError:
            raise ReconciliationRefusedError(
                f"A live vaibify process holds container "
                f"'{sContainerName}'; route the break-glass to that hub "
                "with 'vaibify reconcile' instead."
            )
    try:
        try:
            operationJournal.fnAssertJournalIsBreakGlassClearable(
                sContainerName, sExpectedSha256,
            )
        except operationJournal.OperationJournalError as error:
            # The side-effect-free pre-check: a stale hash or a journal
            # the break-glass does not own refuses BEFORE any container
            # is stopped, so a misdirected request acts on nothing.
            raise ReconciliationRefusedError(str(error))
        if fnStopContainerByName is not None:
            _fnStopContainerQuietly(fnStopContainerByName, sContainerName)
        try:
            operationJournal.fnBreakGlassClearMalformedJournal(
                sContainerName, sExpectedSha256,
            )
        except operationJournal.OperationJournalError as error:
            raise ReconciliationRefusedError(str(error))
    finally:
        if fileHandleLock is not None:
            containerLock.fnReleaseContainerLock(fileHandleLock)
    logger.warning(
        "Break-glass cleared the malformed journal marker for container "
        "'%s'", sContainerName,
    )
    return {"bCleared": True, "sContainerName": sContainerName}


def _fnStopContainerQuietly(fnStopContainerByName, sContainerName):
    """Stop the possibly-relevant container; a not-running one is fine."""
    try:
        fnStopContainerByName(sContainerName)
    except Exception as error:
        logger.warning(
            "Break-glass container stop for '%s' reported: %s",
            sContainerName, error,
        )
