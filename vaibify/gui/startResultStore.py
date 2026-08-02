"""The bounded, in-memory ledger of start outcomes (design §10b).

A start reservation carries no outcome. This ledger does, and it
deliberately OUTLIVES the reservation: without a record that survives
the reservation's clear, a lost response could never learn that a start
FAILED once ownership was released — a success can be reconstructed from
the resulting ``OwnerRecord``, a failure leaves nothing at all.

It is a ledger, not an authority: it decides what outcomes exist and for
how long, never who may read one. That question — success delivery bound
to the live owner record, failure delivery bound to a browser session and
conveying no container authority — belongs to ``startReservation``, which
changes for entirely different reasons.

"Durable" here means in-memory across the reservation's clear, NEVER on
disk. Nothing in a record is a credential: a lease is derived at delivery
time from the live owner record, so there is none stored here to leak,
persist, or replay.
"""

__all__ = [
    "S_RESULT_PENDING",
    "S_RESULT_SUCCEEDED",
    "S_RESULT_FAILED",
    "S_RESULT_OWNED",
    "F_RESULT_TTL_SECONDS",
    "F_FAILED_RESULT_WINDOW_SECONDS",
    "I_RESULT_CAP_TOTAL",
    "I_RESULT_CAP_PER_SESSION",
    "StartResultRecord",
    "fdictCreateStartResultStore",
    "fnOpenStartResult",
    "fnCloseStartResult",
    "frecordLatestResultForContainer",
    "frecordStartResultById",
    "fnAcknowledgeStartResult",
    "fnRebindStartResultsForTransfer",
]

import time
from dataclasses import dataclass, field

from . import containerOwnership

S_RESULT_PENDING = "PENDING"
S_RESULT_SUCCEEDED = "SUCCEEDED"
S_RESULT_FAILED = "FAILED"
# Not an outcome, and deliberately not one of the three above: it is the
# answer to "there is no start outcome on record, but you own this
# container". Reporting that as SUCCEEDED would invent a start that
# never happened in this window; reporting it as 404 stranded an owner
# who reloaded after a long start.
S_RESULT_OWNED = "OWNED"

# The record outlives its reservation, but not indefinitely: an explicit
# lifetime plus per-session and hub-wide caps, so a hub that starts
# containers all day cannot accumulate outcomes without bound. The
# lifetime is measured from SETTLEMENT, and a PENDING record is exempt:
# a cold multi-gigabyte pull legitimately outlives any of these windows,
# and expiring the record of a start that is still running is how a
# researcher who reloads mid-start ends up with no way to learn what
# happened.
F_RESULT_TTL_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_START_RESULT_TTL_SECONDS", 900.0,
)

# A FAILED record blocks the next start until it is acknowledged, so it
# needs its own, longer window after which it clears itself. Without
# one, a browser that failed a start and never came back would leave the
# container permanently unstartable from any session.
F_FAILED_RESULT_WINDOW_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_FAILED_RESULT_WINDOW_SECONDS", 1800.0,
    )
)
I_RESULT_CAP_TOTAL = 64
I_RESULT_CAP_PER_SESSION = 8


@dataclass(eq=False)
class StartResultRecord:
    """One start's outcome, keyed by the reservation that produced it."""

    sReservationId: str
    sContainerName: str
    sInitiatingSessionId: str
    sState: str = S_RESULT_PENDING
    sSafeError: str = ""
    bQuarantined: bool = False
    sContainerId: str = ""
    fCreatedMonotonic: float = field(default_factory=time.monotonic)
    fSettledMonotonic: float = 0.0


def fdictCreateStartResultStore():
    """Return the empty ``{sReservationId: StartResultRecord}`` ledger."""
    return {}


def _fdictStoreFor(appState):
    """Return the app's ledger, creating it when absent."""
    dictStore = getattr(appState, "dictStartResults", None)
    if dictStore is None:
        dictStore = fdictCreateStartResultStore()
        appState.dictStartResults = dictStore
    return dictStore


def fnOpenStartResult(appState, sReservationId, sName, sInitiatingSessionId):
    """Open the PENDING record for a freshly minted reservation."""
    dictStore = _fdictStoreFor(appState)
    _fnPruneStartResults(dictStore)
    dictStore[sReservationId] = StartResultRecord(
        sReservationId=sReservationId, sContainerName=sName,
        sInitiatingSessionId=sInitiatingSessionId,
    )


def fnCloseStartResult(
    appState, sReservationId, sState, sSafeError="", bQuarantined=False,
    sContainerId="",
):
    """Settle a record in place, so a poller ever sees exactly one outcome."""
    recordResult = _fdictStoreFor(appState).get(sReservationId)
    if recordResult is None:
        return
    recordResult.sState = sState
    recordResult.sSafeError = sSafeError
    recordResult.bQuarantined = bQuarantined
    recordResult.sContainerId = sContainerId
    recordResult.fSettledMonotonic = time.monotonic()


def frecordStartResultById(appState, sReservationId):
    """Return one record by reservation id, or None."""
    return _fdictStoreFor(appState).get(sReservationId)


def frecordLatestResultForContainer(appState, sName):
    """Return the newest unexpired record for a container, or None."""
    dictStore = _fdictStoreFor(appState)
    _fnPruneStartResults(dictStore)
    listRecords = [
        recordResult for recordResult in dictStore.values()
        if recordResult.sContainerName == sName
    ]
    if not listRecords:
        return None
    return max(listRecords, key=lambda record: record.fCreatedMonotonic)


def fnAcknowledgeStartResult(
    appState, sName, sBrowserSessionId, sReservationId,
):
    """Delete one settled record the caller proves it has actually read.

    Naming the reservation id IS the acknowledgement: a client that never
    polled cannot guess it, so an automatic retry cannot silently clear a
    failure the researcher was never shown. A PENDING record is never
    acknowledgeable — there is nothing yet to have seen.
    """
    if not sReservationId:
        return
    dictStore = _fdictStoreFor(appState)
    recordResult = dictStore.get(sReservationId)
    if recordResult is None or recordResult.sContainerName != sName:
        return
    if recordResult.sInitiatingSessionId != sBrowserSessionId:
        return
    if recordResult.sState == S_RESULT_PENDING:
        return
    dictStore.pop(sReservationId, None)


def fnRebindStartResultsForTransfer(appState, sName, sNewSessionId):
    """Rebind a container's outstanding records to a successor session.

    Called inside the host transfer's synchronous commit (design §10b).
    Success delivery needs no rebinding — it derives from the owner
    record the same commit has just rebound — but the FAILURE entitlement
    is bound to a browser session, and that commit is about to revoke the
    old one. Rebinding conveys nothing but the right to read an outcome.
    """
    dictStore = getattr(appState, "dictStartResults", None)
    if not dictStore or not sNewSessionId:
        return
    for recordResult in dictStore.values():
        if recordResult.sContainerName == sName:
            recordResult.sInitiatingSessionId = sNewSessionId


def _fnPruneStartResults(dictStore):
    """Drop expired SETTLED records, then the oldest beyond the caps."""
    fNow = time.monotonic()
    for sReservationId in list(dictStore):
        if _fbResultRecordHasExpired(dictStore[sReservationId], fNow):
            dictStore.pop(sReservationId, None)
    _fnEnforceStartResultCaps(dictStore)


def _fbResultRecordHasExpired(recordResult, fNow):
    """Return True when a record's own window has closed.

    A PENDING record never expires: the start it describes is still
    running, and a pull can legitimately take longer than any window
    here. It leaves the ledger when it settles, or with the hub.
    """
    if recordResult.sState == S_RESULT_PENDING:
        return False
    fWindow = (
        F_FAILED_RESULT_WINDOW_SECONDS
        if recordResult.sState == S_RESULT_FAILED
        else F_RESULT_TTL_SECONDS
    )
    return fNow - recordResult.fSettledMonotonic >= fWindow


def _fnEnforceStartResultCaps(dictStore):
    """Keep the ledger bounded per session and hub-wide, oldest out first."""
    dictCountBySession = {}
    for recordResult in sorted(
        dictStore.values(), key=lambda record: -record.fCreatedMonotonic,
    ):
        sSessionId = recordResult.sInitiatingSessionId
        dictCountBySession[sSessionId] = dictCountBySession.get(
            sSessionId, 0,
        ) + 1
        if dictCountBySession[sSessionId] > I_RESULT_CAP_PER_SESSION:
            dictStore.pop(recordResult.sReservationId, None)
    while len(dictStore) > I_RESULT_CAP_TOTAL:
        sOldestId = min(
            dictStore, key=lambda sKey: dictStore[sKey].fCreatedMonotonic,
        )
        dictStore.pop(sOldestId, None)
