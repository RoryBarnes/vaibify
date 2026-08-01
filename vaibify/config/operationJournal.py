"""Write-ahead journal of container-scoped operations.

Each container has at most one journal file at
``~/.vaibify/journal/<name>.operationJournal`` holding a SET of
operation records keyed by a stable operation id — a pipeline task,
several terminal executions, and a file write legitimately coexist as
distinct records. A record is written in two phases: ``PREPARED``
persists the durable operation id, the allowlisted operation kind, and
the intended target BEFORE anything launches; promotion to
``IN_FLIGHT`` adds the real holder identity (a recycle-proof PID plus
process group, or a Docker exec/container id, per kind). A crash at
any point naturally leaves the record behind, so the next acquisition
of the container — ``containerLock.fnAcquireContainerLock`` — refuses
a container whose past operations cannot be proven settled, even
though the crashed holder's flock was auto-released by the kernel.

Resolution is two-tier. The automatic tier
(:func:`fdictResolveContainerJournal`) clears a record only when the
holder is recycle-proof dead AND the kind-specific probe from
:data:`DICT_OPERATION_PROBE_CATALOG` shows the effect conclusively
settled, logging a note. A live holder reads as BUSY, never as
quarantined. A dead-but-indeterminate record, an unknown kind, a
missing identity, or an explicit ``NEEDS_RECONCILIATION`` marker
quarantines the whole container — every record in the set must be
settled before the container is claimable — until a reconciliation
transaction (the future ``vaibify reconcile``, host lane) proves it
safe. A verifier that is merely unavailable (no Docker connection)
quarantines transiently without persisting the promotion, so a later
probe with the daemon reachable can still settle the record.

Malformed, unreadable, and unknown-NEWER-version journal files read as
quarantined — a damaged file, or one containing only ``{}``, must
never read as "not quarantined" — and a newer version additionally
reports that an upgrade is required; it is never auto-cleared and
never rewritten. Ordinary writes refuse an unreadable journal outright
(:class:`OperationJournalUnreadableError`), so no ordinary acknowledge
path can clear a malformed marker; that clearing is reserved for the
documented destructive break-glass of the reconciliation transaction.

The file is hardened: temp-write → fsync → atomic replace → directory
fsync, ``O_NOFOLLOW`` on every open, mode 0600, bounded schema
validation, and only allowlisted metadata — never raw commands,
environment values, leases, tokens, or credentials. The
``.operationJournal`` suffix and the ``~/.vaibify/journal`` directory
are matched by no sweeper in this repository:
``ephemeralStore.fnSweepStaleEphemeralFiles`` (the age-based sweep)
deletes only inside ``~/.vaibify/tmp``, and
``pidFileRegistry.fnReapStaleFilesIn`` is invoked only for
``~/.vaibify/locks/*.lock`` and ``~/.vaibify/sessions/*.slot``. The
directory is also on ``bindMountValidator``'s denylist, so a same-UID
in-container agent cannot delete a quarantine marker.
"""

__all__ = [
    "S_OPERATION_STATE_PREPARED",
    "S_OPERATION_STATE_IN_FLIGHT",
    "S_OPERATION_STATE_CANCEL_REQUESTED",
    "S_OPERATION_STATE_NEEDS_RECONCILIATION",
    "S_RESOLUTION_SETTLED",
    "S_RESOLUTION_BUSY",
    "S_RESOLUTION_QUARANTINED",
    "DICT_OPERATION_PROBE_CATALOG",
    "OperationJournalError",
    "OperationJournalUnreadableError",
    "OperationJournalRecordError",
    "fsJournalPathFor",
    "flistJournaledContainerNames",
    "fdictReadJournalOutcome",
    "fsPrepareOperation",
    "fnPromoteOperationToInFlight",
    "fnRequestOperationCancel",
    "fnMarkOperationNeedsReconciliation",
    "fnSettleOperation",
    "fnClearOperationsReconciled",
    "fsComputeJournalFileSha256",
    "fnAssertJournalIsBreakGlassClearable",
    "fnBreakGlassClearMalformedJournal",
    "fdictResolveContainerJournal",
]

import datetime
import hashlib
import json
import logging
import os
import secrets
import shlex
import threading

from vaibify.config.pidFileRegistry import (
    fbIsSafeRegistryName,
    fnEnsureDirectory,
    fnUnlinkQuietly,
)
from vaibify.config.processLiveness import (
    fbIsProcessAliveSince,
    fbIsUsablePid,
)

logger = logging.getLogger("vaibify")

S_OPERATION_STATE_PREPARED = "PREPARED"
S_OPERATION_STATE_IN_FLIGHT = "IN_FLIGHT"
S_OPERATION_STATE_CANCEL_REQUESTED = "CANCEL_REQUESTED"
S_OPERATION_STATE_NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"

S_RESOLUTION_SETTLED = "SETTLED"
S_RESOLUTION_BUSY = "BUSY"
S_RESOLUTION_QUARANTINED = "QUARANTINED"

_S_JOURNAL_DIRECTORY = os.path.expanduser("~/.vaibify/journal")
_S_JOURNAL_SUFFIX = ".operationJournal"
_S_TEMPORARY_WRITE_SUFFIX = ".partialWrite"
_I_JOURNAL_SCHEMA_VERSION = 1
_I_MAXIMUM_JOURNAL_BYTES = 262144
_I_MAXIMUM_OPERATION_RECORDS = 128
_I_MAXIMUM_STRING_FIELD_LENGTH = 512

_TUPLE_OPERATION_STATES = (
    S_OPERATION_STATE_PREPARED,
    S_OPERATION_STATE_IN_FLIGHT,
    S_OPERATION_STATE_CANCEL_REQUESTED,
    S_OPERATION_STATE_NEEDS_RECONCILIATION,
)

# Only allowlisted metadata may be journaled: identities and targets,
# never raw commands, environment values, leases, tokens, or
# credentials. Any other key on disk reads as malformed (fail closed).
_SET_ALLOWED_TOP_LEVEL_KEYS = frozenset({
    "iSchemaVersion", "sContainerName", "dictOperations",
})
_SET_ALLOWED_RECORD_STRING_KEYS = frozenset({
    "sState", "sKind", "sTarget", "sPreparedIso", "sInFlightIso",
    "sDockerExecId", "sDockerContainerId", "sReservationLabel",
    "sExpectedSha256", "sPriorSha256", "sNote",
})
_SET_ALLOWED_RECORD_INTEGER_KEYS = frozenset({
    "iHolderPid", "iHolderProcessGroup",
})
_SET_HOLDER_IDENTITY_KEYS = frozenset({
    "iHolderPid", "iHolderProcessGroup", "sDockerExecId",
    "sDockerContainerId", "sReservationLabel", "sExpectedSha256",
    "sPriorSha256",
})

_LOCK_JOURNAL_WRITE = threading.Lock()


class OperationJournalError(RuntimeError):
    """Base error for operation-journal failures."""


class OperationJournalUnreadableError(OperationJournalError):
    """Raised when an ordinary write meets a malformed/newer journal.

    Ordinary API calls must never rewrite (and thereby clear) a
    quarantined journal file; only the reconciliation transaction's
    documented break-glass may.
    """


class OperationJournalRecordError(OperationJournalError):
    """Raised for an unknown operation id, kind, or invalid transition."""


def _fnValidateContainerName(sContainerName):
    """Raise when the container name is unsafe for a journal file path."""
    if not fbIsSafeRegistryName(sContainerName):
        raise OperationJournalError(
            f"Invalid container name for the operation journal: "
            f"{sContainerName!r}"
        )


def fsJournalPathFor(sContainerName):
    """Return the journal file path for a container name."""
    _fnValidateContainerName(sContainerName)
    return os.path.join(
        _S_JOURNAL_DIRECTORY, f"{sContainerName}{_S_JOURNAL_SUFFIX}",
    )


def flistJournaledContainerNames():
    """Return the container names that currently have a journal file.

    Temporary write artifacts (``*.partialWrite.*``) never match the
    journal suffix, so a torn write cannot masquerade as a journal.
    """
    try:
        listEntries = os.listdir(_S_JOURNAL_DIRECTORY)
    except OSError:
        return []
    return sorted(
        sEntry[: -len(_S_JOURNAL_SUFFIX)]
        for sEntry in listEntries
        if sEntry.endswith(_S_JOURNAL_SUFFIX)
    )


def _fsNowIso():
    """Return the current local time as an ISO string."""
    return datetime.datetime.now().isoformat()


# ---------------------------------------------------------------------
# Hardened read: bounded, O_NOFOLLOW, fail closed.
# ---------------------------------------------------------------------

def _fdictReadOutcome(sReadState, dictOperations=None, sDetail=""):
    """Return the uniform read-outcome shape."""
    return {
        "sReadState": sReadState,
        "dictOperations": dictOperations or {},
        "sDetail": sDetail,
    }


def fdictReadJournalOutcome(sContainerName):
    """Read and validate a container's journal file, failing closed.

    ``sReadState`` is one of ``absent`` (no file — the only empty state
    that is not quarantined), ``valid``, ``malformed`` (unreadable,
    oversized, torn, wrong shape, or non-allowlisted content), or
    ``requiresUpgrade`` (a NEWER schema version this build does not
    understand). Everything except ``absent`` and ``valid`` must be
    treated as quarantined by callers.
    """
    sPath = fsJournalPathFor(sContainerName)
    try:
        iDescriptor = os.open(sPath, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return _fdictReadOutcome("absent")
    except OSError as error:
        return _fdictReadOutcome(
            "malformed", sDetail=f"journal file unreadable: {error}",
        )
    try:
        with os.fdopen(iDescriptor, "rb") as fileHandle:
            if os.fstat(fileHandle.fileno()).st_size > _I_MAXIMUM_JOURNAL_BYTES:
                return _fdictReadOutcome(
                    "malformed",
                    sDetail="journal file exceeds the bounded size",
                )
            byteContent = fileHandle.read()
    except OSError as error:
        return _fdictReadOutcome(
            "malformed", sDetail=f"journal file unreadable: {error}",
        )
    return _fdictValidateJournalBytes(byteContent)


def _fdictValidateJournalBytes(byteContent):
    """Validate raw journal bytes against the bounded schema."""
    try:
        dictPayload = json.loads(byteContent.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        return _fdictReadOutcome(
            "malformed", sDetail=f"journal is not valid JSON: {error}",
        )
    if not isinstance(dictPayload, dict):
        return _fdictReadOutcome(
            "malformed", sDetail="journal top level is not an object",
        )
    iSchemaVersion = dictPayload.get("iSchemaVersion")
    if not isinstance(iSchemaVersion, int) or isinstance(iSchemaVersion, bool):
        return _fdictReadOutcome(
            "malformed", sDetail="journal has no integer iSchemaVersion",
        )
    if iSchemaVersion > _I_JOURNAL_SCHEMA_VERSION:
        return _fdictReadOutcome(
            "requiresUpgrade",
            sDetail=(
                f"journal schema version {iSchemaVersion} is newer than "
                f"this build understands ({_I_JOURNAL_SCHEMA_VERSION}); "
                "upgrade vaibify before reconciling this container"
            ),
        )
    if iSchemaVersion < _I_JOURNAL_SCHEMA_VERSION:
        return _fdictReadOutcome(
            "malformed",
            sDetail=f"journal schema version {iSchemaVersion} is unknown",
        )
    return _fdictValidateJournalShape(dictPayload)


def _fdictValidateJournalShape(dictPayload):
    """Validate the top-level keys and every operation record."""
    if not set(dictPayload) <= _SET_ALLOWED_TOP_LEVEL_KEYS:
        return _fdictReadOutcome(
            "malformed", sDetail="journal carries non-allowlisted keys",
        )
    dictOperations = dictPayload.get("dictOperations")
    if not isinstance(dictOperations, dict):
        return _fdictReadOutcome(
            "malformed", sDetail="journal has no dictOperations object",
        )
    if len(dictOperations) > _I_MAXIMUM_OPERATION_RECORDS:
        return _fdictReadOutcome(
            "malformed", sDetail="journal exceeds the bounded record count",
        )
    for sOperationId, dictRecord in dictOperations.items():
        sProblem = _fsValidateOperationRecord(sOperationId, dictRecord)
        if sProblem:
            return _fdictReadOutcome("malformed", sDetail=sProblem)
    return _fdictReadOutcome("valid", dictOperations=dictOperations)


def _fsValidateOperationRecord(sOperationId, dictRecord):
    """Return '' for a valid record, or the problem description."""
    if not isinstance(sOperationId, str) or not (
        0 < len(sOperationId) <= _I_MAXIMUM_STRING_FIELD_LENGTH
    ):
        return "journal record has an invalid operation id"
    if not isinstance(dictRecord, dict):
        return f"record {sOperationId} is not an object"
    sProblem = _fsValidateRecordFieldTypes(sOperationId, dictRecord)
    if sProblem:
        return sProblem
    for sRequiredKey in ("sState", "sKind", "sTarget", "sPreparedIso"):
        if sRequiredKey not in dictRecord:
            return f"record {sOperationId} is missing {sRequiredKey}"
    if dictRecord["sState"] not in _TUPLE_OPERATION_STATES:
        return f"record {sOperationId} has an unknown state"
    if dictRecord["sState"] in (
        S_OPERATION_STATE_IN_FLIGHT, S_OPERATION_STATE_CANCEL_REQUESTED,
    ) and "sInFlightIso" not in dictRecord:
        return f"record {sOperationId} is in flight without sInFlightIso"
    return ""


def _fsValidateRecordFieldTypes(sOperationId, dictRecord):
    """Return '' when every record field is allowlisted and bounded."""
    for sKey, valueField in dictRecord.items():
        if sKey in _SET_ALLOWED_RECORD_STRING_KEYS:
            if not isinstance(valueField, str) or (
                len(valueField) > _I_MAXIMUM_STRING_FIELD_LENGTH
            ):
                return f"record {sOperationId} field {sKey} is not a bounded string"
        elif sKey in _SET_ALLOWED_RECORD_INTEGER_KEYS:
            if not isinstance(valueField, int) or isinstance(valueField, bool):
                return f"record {sOperationId} field {sKey} is not an integer"
        else:
            return f"record {sOperationId} carries non-allowlisted key {sKey}"
    return ""


# ---------------------------------------------------------------------
# Hardened write: temp-write -> fsync -> atomic replace -> dir fsync.
# ---------------------------------------------------------------------

def _fdictBuildEmptyPayload(sContainerName):
    """Return a fresh journal payload with no operation records."""
    return {
        "iSchemaVersion": _I_JOURNAL_SCHEMA_VERSION,
        "sContainerName": sContainerName,
        "dictOperations": {},
    }


def _fdictLoadPayloadForWrite(sContainerName):
    """Load the journal for a read-modify-write, refusing damaged files.

    An absent file yields a fresh payload. A malformed, unreadable, or
    newer-version file raises: no ordinary write may replace (and so
    silently clear) a journal that reads as quarantined.
    """
    dictOutcomeRead = fdictReadJournalOutcome(sContainerName)
    if dictOutcomeRead["sReadState"] == "absent":
        return _fdictBuildEmptyPayload(sContainerName)
    if dictOutcomeRead["sReadState"] != "valid":
        raise OperationJournalUnreadableError(
            f"The operation journal for container '{sContainerName}' is "
            f"{dictOutcomeRead['sReadState']} and refuses ordinary writes "
            f"({dictOutcomeRead['sDetail']}); reconciliation is required."
        )
    return {
        "iSchemaVersion": _I_JOURNAL_SCHEMA_VERSION,
        "sContainerName": sContainerName,
        "dictOperations": dict(dictOutcomeRead.get("dictOperations", {})),
    }


def _fnStoreJournalPayload(sContainerName, dictPayload):
    """Persist a payload, unlinking the file once no records remain."""
    sPath = fsJournalPathFor(sContainerName)
    if not dictPayload["dictOperations"]:
        fnUnlinkQuietly(sPath)
        return
    fnEnsureDirectory(_S_JOURNAL_DIRECTORY)
    _fnWriteJournalBytesAtomically(
        sPath, json.dumps(dictPayload, indent=2, sort_keys=True).encode("utf-8"),
    )


def _fnWriteJournalBytesAtomically(sPath, byteContent):
    """Write journal bytes crash-durably: temp, fsync, replace, dir fsync."""
    sTemporaryPath = f"{sPath}{_S_TEMPORARY_WRITE_SUFFIX}.{os.getpid()}"
    fnUnlinkQuietly(sTemporaryPath)
    iDescriptor = os.open(
        sTemporaryPath,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(iDescriptor, "wb") as fileHandle:
            fileHandle.write(byteContent)
            fileHandle.flush()
            os.fsync(fileHandle.fileno())
        os.replace(sTemporaryPath, sPath)
    except OSError:
        fnUnlinkQuietly(sTemporaryPath)
        raise
    _fnSyncDirectory(os.path.dirname(sPath))


def _fnSyncDirectory(sDirectory):
    """fsync a directory so an atomic replace survives power loss."""
    try:
        iDescriptor = os.open(sDirectory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(iDescriptor)
    except OSError:
        pass
    finally:
        os.close(iDescriptor)


# ---------------------------------------------------------------------
# Two-phase write API.
# ---------------------------------------------------------------------

def fsPrepareOperation(sContainerName, sOperationKind, sTarget):
    """Persist a PREPARED record before any launch; return its id.

    The durable operation id, allowlisted kind, and intended target are
    on disk BEFORE the effect exists, so a crash at any later point
    leaves an identifiable record. The holder identity is added by
    :func:`fnPromoteOperationToInFlight` once the effect's identity
    (PID/process group, exec id, container id) is known.
    """
    _fnValidateContainerName(sContainerName)
    if sOperationKind not in DICT_OPERATION_PROBE_CATALOG:
        raise OperationJournalRecordError(
            f"Unknown operation kind {sOperationKind!r}; allowlisted kinds "
            f"are {sorted(DICT_OPERATION_PROBE_CATALOG)}"
        )
    if not isinstance(sTarget, str) or not (
        0 < len(sTarget) <= _I_MAXIMUM_STRING_FIELD_LENGTH
    ):
        raise OperationJournalRecordError(
            "The intended target must be a non-empty bounded string"
        )
    with _LOCK_JOURNAL_WRITE:
        dictPayload = _fdictLoadPayloadForWrite(sContainerName)
        if len(dictPayload["dictOperations"]) >= _I_MAXIMUM_OPERATION_RECORDS:
            raise OperationJournalError(
                f"The operation journal for '{sContainerName}' is full"
            )
        sOperationId = secrets.token_hex(16)
        dictRecord = {
            "sState": S_OPERATION_STATE_PREPARED,
            "sKind": sOperationKind,
            "sTarget": sTarget,
            "sPreparedIso": _fsNowIso(),
        }
        dictPayload["dictOperations"][sOperationId] = dictRecord
        _fnStoreJournalPayload(sContainerName, dictPayload)
    return sOperationId


def fnPromoteOperationToInFlight(
    sContainerName, sOperationId, dictHolderIdentity,
):
    """Promote a PREPARED record to IN_FLIGHT with its holder identity.

    ``dictHolderIdentity`` carries only allowlisted identity fields
    (recycle-proof ``iHolderPid``/``iHolderProcessGroup``, or the
    Docker ``sDockerExecId``/``sDockerContainerId``/
    ``sReservationLabel``, or the file-write hash identity) and must
    name at least one, so an IN_FLIGHT record can never exist without
    an identity to probe.
    """
    _fnValidateHolderIdentity(dictHolderIdentity)
    with _LOCK_JOURNAL_WRITE:
        dictPayload = _fdictLoadPayloadForWrite(sContainerName)
        dictRecord = _fdictRequireOperationRecord(
            dictPayload, sContainerName, sOperationId,
        )
        if dictRecord["sState"] != S_OPERATION_STATE_PREPARED:
            raise OperationJournalRecordError(
                f"Operation {sOperationId} is {dictRecord['sState']}, not "
                f"{S_OPERATION_STATE_PREPARED}; only a prepared operation "
                "can be promoted to in-flight"
            )
        dictRecord.update(dictHolderIdentity)
        dictRecord["sState"] = S_OPERATION_STATE_IN_FLIGHT
        dictRecord["sInFlightIso"] = _fsNowIso()
        _fnStoreJournalPayload(sContainerName, dictPayload)


def _fnValidateHolderIdentity(dictHolderIdentity):
    """Raise unless the identity names allowlisted, typed fields."""
    if not isinstance(dictHolderIdentity, dict) or not dictHolderIdentity:
        raise OperationJournalRecordError(
            "Promotion to in-flight requires at least one holder-identity "
            "field"
        )
    setUnknownKeys = set(dictHolderIdentity) - _SET_HOLDER_IDENTITY_KEYS
    if setUnknownKeys:
        raise OperationJournalRecordError(
            f"Non-allowlisted holder-identity fields: {sorted(setUnknownKeys)}"
        )
    sProblem = _fsValidateRecordFieldTypes("holder-identity", dictHolderIdentity)
    if sProblem:
        raise OperationJournalRecordError(sProblem)


def _fdictRequireOperationRecord(dictPayload, sContainerName, sOperationId):
    """Return the named record or raise a helpful error."""
    dictRecord = dictPayload["dictOperations"].get(sOperationId)
    if dictRecord is None:
        raise OperationJournalRecordError(
            f"No journal record {sOperationId!r} exists for container "
            f"'{sContainerName}'"
        )
    return dictRecord


def fnRequestOperationCancel(sContainerName, sOperationId):
    """Mark an IN_FLIGHT record CANCEL_REQUESTED (cancellation pending)."""
    with _LOCK_JOURNAL_WRITE:
        dictPayload = _fdictLoadPayloadForWrite(sContainerName)
        dictRecord = _fdictRequireOperationRecord(
            dictPayload, sContainerName, sOperationId,
        )
        if dictRecord["sState"] != S_OPERATION_STATE_IN_FLIGHT:
            raise OperationJournalRecordError(
                f"Operation {sOperationId} is {dictRecord['sState']}; only "
                "an in-flight operation can have cancellation requested"
            )
        dictRecord["sState"] = S_OPERATION_STATE_CANCEL_REQUESTED
        _fnStoreJournalPayload(sContainerName, dictPayload)


def fnMarkOperationNeedsReconciliation(sContainerName, sOperationId, sNote=""):
    """Poison one record so the container quarantines until reconciled."""
    with _LOCK_JOURNAL_WRITE:
        dictPayload = _fdictLoadPayloadForWrite(sContainerName)
        dictRecord = _fdictRequireOperationRecord(
            dictPayload, sContainerName, sOperationId,
        )
        dictRecord["sState"] = S_OPERATION_STATE_NEEDS_RECONCILIATION
        if sNote:
            dictRecord["sNote"] = sNote[:_I_MAXIMUM_STRING_FIELD_LENGTH]
        _fnStoreJournalPayload(sContainerName, dictPayload)


def fnSettleOperation(sContainerName, sOperationId):
    """Remove one record after its worker died and its effect settled.

    The normal-completion clear. Callers must hold the container flock
    while settling so acquisition (which also examines the journal
    under the flock) can never race this removal. A record marked
    ``NEEDS_RECONCILIATION`` refuses this ordinary clear: only the
    reconciliation transaction may release a poisoned record. The
    journal file is unlinked once its last record settles.
    """
    with _LOCK_JOURNAL_WRITE:
        dictPayload = _fdictLoadPayloadForWrite(sContainerName)
        dictRecord = _fdictRequireOperationRecord(
            dictPayload, sContainerName, sOperationId,
        )
        if dictRecord["sState"] == S_OPERATION_STATE_NEEDS_RECONCILIATION:
            raise OperationJournalRecordError(
                f"Operation {sOperationId} needs reconciliation and cannot "
                "be settled by the ordinary completion path; run the "
                "reconciliation transaction instead"
            )
        del dictPayload["dictOperations"][sOperationId]
        _fnStoreJournalPayload(sContainerName, dictPayload)


def fnClearOperationsReconciled(sContainerName, listOperationIds):
    """Remove records the reconciliation transaction has proven settled.

    The one clear that may release a ``NEEDS_RECONCILIATION`` record —
    callable only from the reconciliation transaction
    (:mod:`vaibify.config.reconciliation`), which proves the holder
    dead and the effect settled BEFORE calling this, and which holds
    the exclusive reconciliation lock (the container flock, or the live
    hub's mutation lock) so the clear is the LAST step of the
    transaction. Every named id must exist; a missing id means the
    record set changed since it was proven, and nothing is cleared.
    """
    with _LOCK_JOURNAL_WRITE:
        dictPayload = _fdictLoadPayloadForWrite(sContainerName)
        for sOperationId in listOperationIds:
            _fdictRequireOperationRecord(
                dictPayload, sContainerName, sOperationId,
            )
        for sOperationId in listOperationIds:
            del dictPayload["dictOperations"][sOperationId]
        _fnStoreJournalPayload(sContainerName, dictPayload)


def fsComputeJournalFileSha256(sContainerName):
    """Return the sha256 of the raw journal bytes, '' when absent.

    The break-glass identity (design §6b): a malformed record has no
    parseable operation id, so the hash of its raw marker bytes is the
    only ABA guard a destructive clear can carry.
    """
    return _fsComputeHostFileSha256(fsJournalPathFor(sContainerName))


def fnAssertJournalIsBreakGlassClearable(sContainerName, sExpectedSha256):
    """Raise unless the journal is a MALFORMED marker with matching bytes.

    Refuses a ``valid`` journal (ordinary reconciliation owns those),
    an unknown-NEWER version (upgrading is required — never
    blind-break), an absent marker, and raw bytes that no longer hash
    to ``sExpectedSha256`` — a replacement marker written since the
    caller inspected the file is never the record it named. Callers
    run this BEFORE any destructive preparation (stopping containers),
    so a stale or misdirected break-glass has no side effect at all;
    :func:`fnBreakGlassClearMalformedJournal` re-runs it under the
    journal lock as the authoritative check.
    """
    dictOutcomeRead = fdictReadJournalOutcome(sContainerName)
    sReadState = dictOutcomeRead["sReadState"]
    if sReadState == "absent":
        raise OperationJournalRecordError(
            f"Container '{sContainerName}' has no journal marker; "
            "there is nothing to break-glass"
        )
    if sReadState == "requiresUpgrade":
        raise OperationJournalUnreadableError(
            f"The journal for container '{sContainerName}' uses a "
            "NEWER schema version; upgrade vaibify instead of "
            "breaking the glass — a readable record is never "
            "destroyed blind"
        )
    if sReadState == "valid":
        raise OperationJournalRecordError(
            f"The journal for container '{sContainerName}' is valid; "
            "run the ordinary reconciliation transaction instead of "
            "the break-glass"
        )
    sActualSha256 = fsComputeJournalFileSha256(sContainerName)
    if sActualSha256 != sExpectedSha256:
        raise OperationJournalRecordError(
            f"The journal marker for container '{sContainerName}' "
            "does not match the presented hash; it was replaced "
            "since it was inspected, and the break-glass clears "
            "only the record it names"
        )


def fnBreakGlassClearMalformedJournal(sContainerName, sExpectedSha256):
    """Destructively unlink a MALFORMED journal whose bytes hash-match.

    The documented break-glass (design §8, case 46). The clearability
    assertion runs under the journal write lock, so a marker replaced
    after the caller's side-effect-free pre-check is still refused
    here. Callers must first stop every possibly-relevant container
    and helper; this function clears only the marker.
    """
    with _LOCK_JOURNAL_WRITE:
        fnAssertJournalIsBreakGlassClearable(
            sContainerName, sExpectedSha256,
        )
        fnUnlinkQuietly(fsJournalPathFor(sContainerName))


# ---------------------------------------------------------------------
# Probe catalog: {kind -> probe + cleanup}. Every kind the write API
# accepts has a handler; unknown kinds on disk quarantine.
# ---------------------------------------------------------------------

def _fdictProbeOutcome(bHolderAlive, bSettled, bIndeterminate, sDetail):
    """Return the uniform probe-outcome shape."""
    return {
        "bHolderAlive": bHolderAlive,
        "bSettled": bSettled,
        "bIndeterminate": bIndeterminate,
        "sDetail": sDetail,
    }


def _fbProcessGroupIsEmpty(iProcessGroup):
    """Return True only when no process remains in the group."""
    try:
        os.killpg(iProcessGroup, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _fdictProbeHelperOperation(dictRecord, connectionDocker):
    """Probe a helper record: recycle-proof PID plus empty process group."""
    del connectionDocker
    iHolderPid = dictRecord.get("iHolderPid")
    iHolderProcessGroup = dictRecord.get("iHolderProcessGroup")
    if not fbIsUsablePid(iHolderPid) or not fbIsUsablePid(iHolderProcessGroup):
        return _fdictProbeOutcome(
            False, False, False,
            "helper record is missing its recycle-proof PID/process-group "
            "identity",
        )
    if fbIsProcessAliveSince(iHolderPid, dictRecord.get("sInFlightIso")):
        return _fdictProbeOutcome(
            True, False, False, f"holder pid {iHolderPid} is still alive",
        )
    if _fbProcessGroupIsEmpty(iHolderProcessGroup):
        return _fdictProbeOutcome(
            False, True, False,
            "holder is dead and its process group is empty",
        )
    return _fdictProbeOutcome(
        False, False, False,
        f"process group {iHolderProcessGroup} still has live members",
    )


def _fdictProbeExecOperation(dictRecord, connectionDocker):
    """Probe a Docker exec record by its recorded exec id."""
    sDockerExecId = dictRecord.get("sDockerExecId")
    if not sDockerExecId:
        return _fdictProbeOutcome(
            False, False, False, "exec record is missing its exec id",
        )
    if connectionDocker is None:
        return _fdictProbeOutcome(
            False, False, True,
            "no Docker connection is available to verify the exec settled",
        )
    if not hasattr(connectionDocker, "fdictInspectExec"):
        return _fdictProbeOutcome(
            False, False, False,
            "this Docker connection cannot inspect execs; the verifier is "
            "unsupported and reconciliation is required",
        )
    try:
        dictInspect = connectionDocker.fdictInspectExec(sDockerExecId)
    except Exception as error:
        return _fdictProbeOutcome(
            False, False, True, f"exec inspect failed: {error}",
        )
    if dictInspect.get("Running") is True:
        return _fdictProbeOutcome(
            True, False, False, f"exec {sDockerExecId} is still running",
        )
    if dictInspect.get("Running") is False:
        return _fdictProbeOutcome(
            False, True, False, "the daemon reports the exec exited",
        )
    return _fdictProbeOutcome(
        False, False, True, "the daemon gave no conclusive exec state",
    )


def _fdictProbeStartOperation(dictRecord, connectionDocker):
    """Probe a container start/create record by its recorded target."""
    iHolderPid = dictRecord.get("iHolderPid")
    if fbIsUsablePid(iHolderPid) and fbIsProcessAliveSince(
        iHolderPid, dictRecord.get("sInFlightIso"),
    ):
        return _fdictProbeOutcome(
            True, False, False,
            f"launching process {iHolderPid} is still alive",
        )
    sDockerContainerId = dictRecord.get("sDockerContainerId")
    if not sDockerContainerId and not dictRecord.get("sReservationLabel"):
        return _fdictProbeOutcome(
            False, False, False,
            "start record has neither a container id nor a reservation "
            "label",
        )
    if connectionDocker is None:
        return _fdictProbeOutcome(
            False, False, True,
            "no Docker connection is available to verify the start settled",
        )
    if not sDockerContainerId:
        return _fdictProbeOutcome(
            False, False, False,
            "label-only start probing is not supported; the verifier is "
            "unsupported and reconciliation is required",
        )
    return _fdictProbeStartContainerById(sDockerContainerId, connectionDocker)


def _fdictProbeStartContainerById(sDockerContainerId, connectionDocker):
    """Settle a start record only on a definitive absent answer."""
    if not hasattr(connectionDocker, "fdictInspectContainerIfPresent"):
        return _fdictProbeOutcome(
            False, False, False,
            "this Docker connection cannot inspect containers; the "
            "verifier is unsupported and reconciliation is required",
        )
    try:
        dictInspect = connectionDocker.fdictInspectContainerIfPresent(
            sDockerContainerId,
        )
    except Exception as error:
        return _fdictProbeOutcome(
            False, False, True, f"container inspect failed: {error}",
        )
    if dictInspect is None:
        return _fdictProbeOutcome(
            False, True, False,
            "the recorded container is definitively absent; nothing was "
            "left behind",
        )
    return _fdictProbeOutcome(
        False, False, False,
        f"the recorded container {sDockerContainerId} still exists; its "
        "removal requires reconciliation",
    )


def _fdictProbeFileWriteOperation(dictRecord, connectionDocker):
    """Probe a file-write record by its atomic-write hash postcondition."""
    iHolderPid = dictRecord.get("iHolderPid")
    if fbIsUsablePid(iHolderPid) and fbIsProcessAliveSince(
        iHolderPid, dictRecord.get("sInFlightIso"),
    ):
        return _fdictProbeOutcome(
            True, False, False, f"writer pid {iHolderPid} is still alive",
        )
    sTarget = dictRecord.get("sTarget", "")
    sExpectedSha256 = dictRecord.get("sExpectedSha256")
    if not sTarget or sExpectedSha256 is None:
        return _fdictProbeOutcome(
            False, False, False,
            "file-write record is missing its target or expected hash",
        )
    if dictRecord.get("sDockerContainerId"):
        return _fdictProbeContainerFileHash(dictRecord, connectionDocker)
    return _fdictCompareHostFileHash(dictRecord)


def _fdictCompareHostFileHash(dictRecord):
    """Settle a host-side file write when its bytes are conclusive.

    An atomic write leaves the target either fully new (matches
    ``sExpectedSha256``) or fully old (matches ``sPriorSha256``, with
    the empty string recording that the file did not exist before).
    Anything else is torn or foreign and requires reconciliation.
    """
    try:
        sActualSha256 = _fsComputeHostFileSha256(dictRecord["sTarget"])
    except OSError as error:
        return _fdictProbeOutcome(
            False, False, True, f"target file unreadable: {error}",
        )
    return _fdictCompareShaAgainstRecord(sActualSha256, dictRecord)


def _fsComputeHostFileSha256(sTargetPath):
    """Return the sha256 of a host file, '' when it does not exist."""
    try:
        iDescriptor = os.open(sTargetPath, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ""
    hashDigest = hashlib.sha256()
    with os.fdopen(iDescriptor, "rb") as fileHandle:
        for byteChunk in iter(lambda: fileHandle.read(65536), b""):
            hashDigest.update(byteChunk)
    return hashDigest.hexdigest()


def _fdictProbeContainerFileHash(dictRecord, connectionDocker):
    """Settle a container-side file write via an in-container hash."""
    if connectionDocker is None:
        return _fdictProbeOutcome(
            False, False, True,
            "no Docker connection is available to verify the container "
            "file write",
        )
    if not hasattr(connectionDocker, "ftResultExecuteCommand"):
        return _fdictProbeOutcome(
            False, False, False,
            "this Docker connection cannot execute the hash probe; the "
            "verifier is unsupported and reconciliation is required",
        )
    sCommand = "sha256sum -- " + shlex.quote(dictRecord["sTarget"])
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            dictRecord["sDockerContainerId"], sCommand,
        )
    except Exception as error:
        return _fdictProbeOutcome(
            False, False, True, f"container hash probe failed: {error}",
        )
    sActualSha256 = sOutput.split()[0] if iExitCode == 0 and sOutput else ""
    return _fdictCompareShaAgainstRecord(sActualSha256, dictRecord)


def _fdictCompareShaAgainstRecord(sActualSha256, dictRecord):
    """Return the settled/quarantine verdict for a computed hash."""
    if sActualSha256 == dictRecord.get("sExpectedSha256"):
        return _fdictProbeOutcome(
            False, True, False, "the target matches the expected content",
        )
    sPriorSha256 = dictRecord.get("sPriorSha256")
    if sPriorSha256 is not None and sActualSha256 == sPriorSha256:
        return _fdictProbeOutcome(
            False, True, False,
            "the write never landed; the target still matches its prior "
            "content",
        )
    return _fdictProbeOutcome(
        False, False, False,
        "the target matches neither the expected nor the prior content",
    )


def _fnCleanupSettledOperationNoResidue(dictRecord, connectionDocker):
    """Confirm no residue removal is needed for a settled record.

    The automatic tier only reports ``bSettled`` when the probe proved
    nothing was left behind (dead holder, empty process group, exited
    exec, absent container, conclusive file bytes), so its cleanup has
    nothing to remove. Destructive cleanup of residue (for example, a
    partial container that still exists) belongs to the reconciliation
    transaction, whose records never reach this handler because their
    probes refuse to report settled.
    """
    del dictRecord, connectionDocker


DICT_OPERATION_PROBE_CATALOG = {
    "start": {
        "fdictProbe": _fdictProbeStartOperation,
        "fnCleanupAfterSettledProbe": _fnCleanupSettledOperationNoResidue,
    },
    "exec": {
        "fdictProbe": _fdictProbeExecOperation,
        "fnCleanupAfterSettledProbe": _fnCleanupSettledOperationNoResidue,
    },
    "helper": {
        "fdictProbe": _fdictProbeHelperOperation,
        "fnCleanupAfterSettledProbe": _fnCleanupSettledOperationNoResidue,
    },
    "file-write": {
        "fdictProbe": _fdictProbeFileWriteOperation,
        "fnCleanupAfterSettledProbe": _fnCleanupSettledOperationNoResidue,
    },
}


# ---------------------------------------------------------------------
# Two-tier resolution.
# ---------------------------------------------------------------------

def fdictResolveContainerJournal(
    sContainerName, connectionDocker=None, bPersistResolution=True,
):
    """Resolve every record of a container's journal set.

    The automatic tier: each record is probed by its kind; a record
    whose holder is recycle-proof dead AND whose effect is conclusively
    settled is auto-cleared with a logged note; a live holder makes the
    container BUSY (never quarantined); everything else quarantines it.
    A dead-but-determinate negative (residue, non-empty process group,
    torn bytes, unknown kind, missing identity) is persisted as
    ``NEEDS_RECONCILIATION``; a merely-unavailable verifier quarantines
    transiently without persisting, so a later probe with the daemon
    reachable can still settle the record. The container is claimable
    only when EVERY record in the set is settled.

    Callers that can hold the container flock must (acquisition and the
    stale-lock reaper do), so this never races a normal completion that
    is clearing its own record. ``bPersistResolution=False`` gives a
    read-only view for display surfaces.
    """
    _fnValidateContainerName(sContainerName)
    with _LOCK_JOURNAL_WRITE:
        return _fdictResolveJournalLocked(
            sContainerName, connectionDocker, bPersistResolution,
        )


def _fdictResolutionResult(
    sResolution, sQuarantineReason="", bRequiresUpgrade=False,
    listAutoClearedOperationIds=(), listBusyOperationIds=(),
    listQuarantinedOperationIds=(),
):
    """Return the uniform resolution-result shape."""
    return {
        "sResolution": sResolution,
        "sQuarantineReason": sQuarantineReason,
        "bRequiresUpgrade": bRequiresUpgrade,
        "listAutoClearedOperationIds": list(listAutoClearedOperationIds),
        "listBusyOperationIds": list(listBusyOperationIds),
        "listQuarantinedOperationIds": list(listQuarantinedOperationIds),
    }


def _fdictResolveJournalLocked(
    sContainerName, connectionDocker, bPersistResolution,
):
    """Resolve under the module write lock; see the public docstring."""
    dictOutcomeRead = fdictReadJournalOutcome(sContainerName)
    if dictOutcomeRead["sReadState"] == "absent":
        return _fdictResolutionResult(S_RESOLUTION_SETTLED)
    if dictOutcomeRead["sReadState"] == "requiresUpgrade":
        return _fdictResolutionResult(
            S_RESOLUTION_QUARANTINED,
            sQuarantineReason=dictOutcomeRead["sDetail"],
            bRequiresUpgrade=True,
        )
    if dictOutcomeRead["sReadState"] == "malformed":
        return _fdictResolutionResult(
            S_RESOLUTION_QUARANTINED,
            sQuarantineReason=(
                "the operation journal is malformed or unreadable "
                f"({dictOutcomeRead['sDetail']}); it never auto-clears"
            ),
        )
    return _fdictResolveValidRecords(
        sContainerName, dictOutcomeRead["dictOperations"],
        connectionDocker, bPersistResolution,
    )


def _fdictResolveValidRecords(
    sContainerName, dictOperations, connectionDocker, bPersistResolution,
):
    """Probe every record and persist auto-clears and poison promotions."""
    dictRemaining = {
        sOperationId: dict(dictOperations[sOperationId])
        for sOperationId in dictOperations
    }
    listAutoCleared, listBusy, listQuarantined, listDetails = [], [], [], []
    bChanged = False
    for sOperationId in sorted(dictOperations):
        dictRecord = dictOperations[sOperationId]
        sVerdict, sDetail = _ftResolveOperationRecord(
            dictRecord, connectionDocker,
        )
        if sVerdict == "settled":
            _fnAutoClearRecord(
                sContainerName, sOperationId, dictRecord, sDetail,
                dictRemaining, connectionDocker,
            )
            listAutoCleared.append(sOperationId)
            bChanged = True
        elif sVerdict == "busy":
            listBusy.append(sOperationId)
        else:
            listQuarantined.append(sOperationId)
            listDetails.append(f"{sOperationId}: {sDetail}")
            if sVerdict == "quarantinePermanent" and (
                dictRemaining[sOperationId]["sState"]
                != S_OPERATION_STATE_NEEDS_RECONCILIATION
            ):
                _fnPromoteRecordToNeedsReconciliation(
                    dictRemaining[sOperationId], sDetail,
                )
                bChanged = True
    if bPersistResolution and bChanged:
        _fnStoreJournalPayload(sContainerName, {
            "iSchemaVersion": _I_JOURNAL_SCHEMA_VERSION,
            "sContainerName": sContainerName,
            "dictOperations": dictRemaining,
        })
    return _fdictSummarizeResolution(
        listAutoCleared, listBusy, listQuarantined, listDetails,
    )


def _fdictSummarizeResolution(
    listAutoCleared, listBusy, listQuarantined, listDetails,
):
    """Fold per-record verdicts into the container-level resolution."""
    if listQuarantined:
        return _fdictResolutionResult(
            S_RESOLUTION_QUARANTINED,
            sQuarantineReason="; ".join(listDetails),
            listAutoClearedOperationIds=listAutoCleared,
            listBusyOperationIds=listBusy,
            listQuarantinedOperationIds=listQuarantined,
        )
    if listBusy:
        return _fdictResolutionResult(
            S_RESOLUTION_BUSY,
            listAutoClearedOperationIds=listAutoCleared,
            listBusyOperationIds=listBusy,
        )
    return _fdictResolutionResult(
        S_RESOLUTION_SETTLED,
        listAutoClearedOperationIds=listAutoCleared,
    )


def _fnAutoClearRecord(
    sContainerName, sOperationId, dictRecord, sDetail, dictRemaining,
    connectionDocker,
):
    """Run the kind's cleanup handler, drop the record, and log the note."""
    dictHandler = DICT_OPERATION_PROBE_CATALOG[dictRecord["sKind"]]
    dictHandler["fnCleanupAfterSettledProbe"](dictRecord, connectionDocker)
    del dictRemaining[sOperationId]
    logger.info(
        "Auto-cleared settled journal record %s (kind=%s, target=%s) for "
        "container '%s': %s",
        sOperationId, dictRecord["sKind"], dictRecord.get("sTarget", ""),
        sContainerName, sDetail,
    )


def _fnPromoteRecordToNeedsReconciliation(dictRecord, sDetail):
    """Poison a record in place with the probe's finding as its note."""
    dictRecord["sState"] = S_OPERATION_STATE_NEEDS_RECONCILIATION
    dictRecord["sNote"] = sDetail[:_I_MAXIMUM_STRING_FIELD_LENGTH]


def _ftResolveOperationRecord(dictRecord, connectionDocker):
    """Return one record's verdict as ``(sVerdict, sDetail)``.

    ``sVerdict`` is ``settled`` (auto-clear), ``busy`` (live holder),
    ``quarantinePermanent`` (a determinate negative — persist the
    poison), or ``quarantineTransient`` (the verifier was unavailable —
    refuse now, but leave the record probeable).
    """
    if dictRecord["sState"] == S_OPERATION_STATE_NEEDS_RECONCILIATION:
        return (
            "quarantinePermanent",
            dictRecord.get("sNote", "explicitly marked for reconciliation"),
        )
    dictHandler = DICT_OPERATION_PROBE_CATALOG.get(dictRecord["sKind"])
    if dictHandler is None:
        return (
            "quarantinePermanent",
            f"unknown operation kind {dictRecord['sKind']!r}",
        )
    if dictRecord["sState"] == S_OPERATION_STATE_PREPARED:
        # Two-phase contract: the effect launches only AFTER promotion
        # persists IN_FLIGHT (the helper blocks on a parent-held gate),
        # so a leftover PREPARED record proves nothing ever acted.
        return ("settled", "prepared but never launched")
    dictProbe = dictHandler["fdictProbe"](dictRecord, connectionDocker)
    return _ftVerdictFromProbe(dictRecord, dictProbe)


def _ftVerdictFromProbe(dictRecord, dictProbe):
    """Map a probe outcome onto the record's verdict."""
    if dictProbe["bHolderAlive"]:
        return ("busy", dictProbe["sDetail"])
    if dictRecord["sState"] == S_OPERATION_STATE_CANCEL_REQUESTED:
        # Cancellation cleanup is owned by the carrier/reconciliation;
        # a dead cancel-requested worker's cleanup is unproven here.
        return (
            "quarantinePermanent",
            "cancellation was requested but its cleanup is unproven",
        )
    if dictProbe["bSettled"]:
        return ("settled", dictProbe["sDetail"])
    if dictProbe["bIndeterminate"]:
        return ("quarantineTransient", dictProbe["sDetail"])
    return ("quarantinePermanent", dictProbe["sDetail"])
