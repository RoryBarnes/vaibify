"""Context-scoped admission gate for container-mutating primitives.

The commit-guard carrier (design §8, ``vaibify/gui/commitCarrier.py``)
is the only minter of :class:`MutationAdmission` objects — the
constructor refuses any caller that does not present the module-private
mint key, so a route cannot forge one. The real irreversible primitives
(``DockerConnection.fnWriteFileViaTar``, the streamed exec split)
consult this module before acting: inside an ENFORCED lane (an HTTP
request served by ``routeScope.ContainerAwareRoute``, or a
carrier-launched durable task) a container mutation without a live,
still-current admission raises :class:`MutationNotAdmittedError`.

Lanes that are not yet marked — background threads (the pipeline
heartbeat, the state-writer thread), CLI paths, and direct library use
— pass unexamined. That is a deliberate, named remainder: enforcement
fails closed only where the admission plumbing is wired and verified,
never silently pretending coverage it does not have.

This module lives in ``vaibify/config`` because ``vaibify/docker`` may
not import ``vaibify/gui``; it depends only on the standard library and
the operation journal (also ``vaibify/config``).
"""

__all__ = [
    "S_ADMISSION_MODE_REQUEST",
    "S_ADMISSION_MODE_ESTABLISHING",
    "S_ADMISSION_MODE_SYNCHRONOUS",
    "S_ADMISSION_MODE_LOCK_HELD",
    "S_ADMISSION_MODE_DURABLE_TASK",
    "ControlPlaneRefusalError",
    "MutationNotAdmittedError",
    "MutationAdmission",
    "fnReRaiseControlPlaneRefusal",
    "ftokenMarkEnforcedLane",
    "fnResetEnforcedLane",
    "fbLaneEnforced",
    "ftokenActivateAdmission",
    "fnDeactivateAdmission",
    "fadmissionActiveForContainerId",
    "fnAssertContainerWriteAdmitted",
    "fnAssertContainerCommandAdmitted",
    "ftokenEnterAuditedRead",
    "fnExitAuditedRead",
    "fbInsideAuditedRead",
    "fnAssertDurableExecAdmitted",
    "fnAssertOperationAdmittedByIdentity",
    "fdictBeginJournaledExec",
    "fnPromoteJournaledExec",
    "fnSettleJournaledExec",
]

import contextvars
import logging

from vaibify.config import operationJournal

logger = logging.getLogger("vaibify")

S_ADMISSION_MODE_REQUEST = "request"
S_ADMISSION_MODE_ESTABLISHING = "ownerEstablishing"
S_ADMISSION_MODE_SYNCHRONOUS = "synchronousCommit"
S_ADMISSION_MODE_LOCK_HELD = "lockHeldAsync"
S_ADMISSION_MODE_DURABLE_TASK = "durableTask"

_OBJECT_MINT_KEY = object()

# Set only by an audited read adapter, around its own exec. See
# ftokenEnterAuditedRead.
_contextAuditedRead = contextvars.ContextVar(
    "vaibifyAuditedRead", default=False,
)

_contextEnforcedLane = contextvars.ContextVar(
    "vaibifyEnforcedMutationLane", default=False,
)
_contextActiveAdmissions = contextvars.ContextVar(
    "vaibifyActiveMutationAdmissions", default=(),
)


class ControlPlaneRefusalError(Exception):
    """Base for "I declined to act", as distinct from "the I/O failed".

    Both refusals below used to subclass ``PermissionError``, which
    reads well and was wrong in the way that matters. A
    ``PermissionError`` IS an ``OSError``, so all 85 ``except OSError``
    and ``except PermissionError`` clauses under ``vaibify/`` caught
    them — including a dozen written to answer conservatively when a
    file cannot be read. A refusal delivered to one of those handlers
    does not surface as an error at all: it becomes "not verified",
    "hash unavailable", ``None``. That is how a carrier refusal came to
    silently DOWNGRADE a workflow's reproducibility badge (fixed at the
    twelve call sites in 8dcb07c; fixed at the type here).

    Deliberately NOT plain ``Exception`` on each class separately. The
    predicate that re-raises these has to name something, and naming
    two unrelated classes is a list that goes stale the moment a third
    refusal is added — the shape that produced this bug twice already.
    One base means one ``isinstance`` check that is right by default,
    and it also closed a live gap: the guard checked only
    ``MutationNotAdmittedError``, so ``CommitRefusedError`` was never
    re-raised by it.

    Nothing in production catches either class by name, and nothing
    depended on the ``OSError`` ancestry; the two host-filesystem
    handlers that catch ``PermissionError``
    (``registryRoutes._fnCreateHostFolder``, ``_flistScanHostEntries``)
    guard ``os.makedirs`` and ``os.scandir``, which cannot reach a
    container.
    """


class MutationNotAdmittedError(ControlPlaneRefusalError):
    """A container mutation was attempted without a carrier admission."""


def fnReRaiseControlPlaneRefusal(error):
    """Re-raise ``error`` if it is a refusal rather than an I/O outcome.

    :class:`MutationNotAdmittedError` subclasses ``PermissionError`` and
    is therefore an ``OSError``, so EVERY ``except OSError`` in the
    codebase catches it — not only the broad ``except Exception`` ones.
    That is the whole hazard: a caller written to answer conservatively
    when a file cannot be read ("not verified", "hash unavailable")
    gives exactly that answer to a refusal, and the refusal disappears.
    No exception, no log line, a wrong answer on the dashboard.

    It has bitten twice already in different shapes. ``fileRoutes`` and
    ``draftRoutes`` each hand-wrote ``except PermissionError: raise``
    ahead of their generic handler, and :mod:`levelGates` swallowed it
    at nine sites while three more sat in its callees
    (``scheduledReverify``, ``environmentSnapshot``, ``attributionLog``)
    — where a swallowed refusal downgraded a workflow's reproducibility
    level silently.

    Call this FIRST in any handler that catches ``OSError`` or broader
    around a call that can reach a container primitive::

        except (OSError, ValueError) as error:
            fnReRaiseControlPlaneRefusal(error)
            return None

    KEPT AS DEFENCE IN DEPTH, not because it is load-bearing. Since
    :class:`ControlPlaneRefusalError` no longer descends from
    ``OSError``, an ``except OSError`` clause does not catch a refusal
    at all and these calls are redundant for the handlers that catch
    ``OSError``/``ValueError``. They stay because they still bite on a
    genuinely broad handler — ``except Exception``, or a bare
    ``except:`` — which the type change cannot help with, and because
    they document at each site that a refusal must not be absorbed
    there. ``tests/testTypeAloneStopsTheSwallow.py`` proves the TYPE
    closes the ``except OSError`` case on its own, so nobody has to
    trust that these calls are doing it.
    """
    if isinstance(error, ControlPlaneRefusalError):
        raise error


class MutationAdmission:
    """One carrier-minted permission to mutate a single container.

    ``fnRevalidate`` is the commit-time currency check: it is invoked
    again at the mutation funnel, synchronously, immediately before the
    effect — so an admission whose owner record was released, rotated
    to a new generation, or rebound to another session refuses the
    write even though the request that minted it was authorized.
    ``dictLiveState`` is shared mutable state (e.g. the durable task's
    active exec operation id) readable by the carrier's cancel plane.
    """

    __slots__ = (
        "sContainerName", "sContainerId", "sMode", "dictLaneTuple",
        "fnRevalidate", "bDurable", "dictLiveState",
    )

    def __init__(
        self, objectMintKey, sContainerName, sContainerId, sMode,
        dictLaneTuple=None, fnRevalidate=None, bDurable=False,
    ):
        if objectMintKey is not _OBJECT_MINT_KEY:
            raise MutationNotAdmittedError(
                "A MutationAdmission can only be minted by the "
                "commit-guard carrier (vaibify.gui.commitCarrier); "
                "routes must request one, never construct one."
            )
        self.sContainerName = sContainerName
        self.sContainerId = sContainerId
        self.sMode = sMode
        self.dictLaneTuple = dict(dictLaneTuple or {})
        self.fnRevalidate = fnRevalidate
        self.bDurable = bDurable
        self.dictLiveState = {"sActiveExecOperationId": ""}


def _fadmissionMintForCommitCarrier(
    sContainerName, sContainerId, sMode, dictLaneTuple=None,
    fnRevalidate=None, bDurable=False,
):
    """Mint an admission — importable ONLY by the commit-guard carrier.

    The leading underscore is the contract: a structural test fails if
    any module other than ``commitCarrier`` (and this one) references
    this name, which is what makes the admission unforgeable in
    practice rather than by convention alone.
    """
    return MutationAdmission(
        _OBJECT_MINT_KEY, sContainerName, sContainerId, sMode,
        dictLaneTuple=dictLaneTuple, fnRevalidate=fnRevalidate,
        bDurable=bDurable,
    )


def ftokenMarkEnforcedLane():
    """Mark the current context as an enforced lane; return the token."""
    return _contextEnforcedLane.set(True)


def fnResetEnforcedLane(token):
    """Restore the enforced-lane flag to its pre-mark value."""
    _contextEnforcedLane.reset(token)


def fbLaneEnforced():
    """Return True inside an enforced (request or durable-task) lane."""
    return _contextEnforcedLane.get()


def ftokenEnterAuditedRead():
    """Mark the calling context as serving an AUDITED READ.

    A typed read may use the raw executor internally -- fetching a file
    means running a program in the container -- but only an audited
    adapter may CONSTRUCT that program, and an adapter's exec is not a
    mutation. Without this distinction, guarding the exec primitive
    would refuse every read that happens to be implemented with one,
    and the reflex fix would be to stop guarding the primitive.

    The flag is set by the adapter, immediately around its own exec,
    never by a caller: an adapter that wrapped its whole body would let
    anything it called in between inherit the exemption.
    """
    return _contextAuditedRead.set(True)


def fnExitAuditedRead(token):
    """Leave the audited-read context."""
    _contextAuditedRead.reset(token)


def fbInsideAuditedRead():
    """Return True while an audited read adapter is running its exec."""
    return _contextAuditedRead.get()


def fnAssertContainerCommandAdmitted(sContainerId, sPrimitiveName):
    """Refuse arbitrary command execution without a carrier admission.

    Arbitrary command execution is ALWAYS mutating: the primitive
    cannot know whether the text it is handed reads or deletes, so it
    must assume the worse. The audited-read exemption is the one
    carve-out, and it is narrow by construction -- an adapter marks the
    context around its own exec, having built the command itself.

    Outside an enforced lane (background threads, CLI, direct library
    use) this is a no-op, the same deliberate remainder the write gate
    carries.
    """
    if fbInsideAuditedRead():
        return
    fnAssertContainerWriteAdmitted(sContainerId, sPrimitiveName)


def ftokenActivateAdmission(admission):
    """Push a minted admission onto the context; return the token."""
    if not isinstance(admission, MutationAdmission):
        raise MutationNotAdmittedError(
            "Only a carrier-minted MutationAdmission can be activated; "
            f"got {type(admission).__name__}."
        )
    return _contextActiveAdmissions.set(
        _contextActiveAdmissions.get() + (admission,),
    )


def fnDeactivateAdmission(token):
    """Pop an activated admission by restoring the context token."""
    _contextActiveAdmissions.reset(token)


def fadmissionActiveForContainerId(sContainerId, bRequireDurable=False):
    """Return the innermost live admission for a container id, or None."""
    for admission in reversed(_contextActiveAdmissions.get()):
        if admission.sContainerId != sContainerId:
            continue
        if bRequireDurable and not admission.bDurable:
            continue
        return admission
    return None


def _fnAssertAdmissionCurrent(admission, sContainerId, sPrimitiveName):
    """Raise when an admission's commit-time revalidation refuses it."""
    if admission.fnRevalidate is None:
        return
    if admission.fnRevalidate():
        return
    raise MutationNotAdmittedError(
        f"The admission for container '{sContainerId}' is stale at the "
        f"commit point of {sPrimitiveName}: its owner record was "
        "released, rebound, or rotated to a new generation after the "
        "request was authorized. Nothing was written."
    )


def fnAssertContainerWriteAdmitted(sContainerId, sPrimitiveName):
    """Refuse a container write in an enforced lane without admission.

    Outside an enforced lane (background threads, CLI, direct library
    use) this is a no-op — the deliberate, documented remainder.
    """
    if not fbLaneEnforced():
        return
    admission = fadmissionActiveForContainerId(sContainerId)
    if admission is None:
        raise MutationNotAdmittedError(
            f"{sPrimitiveName} on container '{sContainerId}' was "
            "attempted from a request lane without a commit-guard "
            "admission. Container mutations must go through the "
            "commit-guard carrier (design §8), never directly from a "
            "route."
        )
    _fnAssertAdmissionCurrent(admission, sContainerId, sPrimitiveName)


def fnAssertDurableExecAdmitted(sContainerId, sPrimitiveName):
    """Refuse a durable-task exec launch without a mode-(c) guard."""
    if not fbLaneEnforced():
        return
    admission = fadmissionActiveForContainerId(
        sContainerId, bRequireDurable=True,
    )
    if admission is None:
        raise MutationNotAdmittedError(
            f"{sPrimitiveName} on container '{sContainerId}' launches a "
            "durable container-side execution and was attempted without "
            "a carrier-minted mode-(c) durable-task guard (design §8)."
        )
    _fnAssertAdmissionCurrent(admission, sContainerId, sPrimitiveName)


def fnAssertOperationAdmittedByIdentity(
    sContainerName, sOperationId, dictHolderIdentity,
):
    """Gate one operation by IDENTITY against its own journal record.

    The rule (design §8, v10/v13): an operation proceeds iff its OWN
    operation id and holder identity match its record in the
    per-container set AND no record in the set is
    ``NEEDS_RECONCILIATION``. A different operation or holder, a
    malformed journal, or any poisoned record refuses it; the mere
    presence of OTHER records never refuses the operation's own record,
    so the carrier cannot deadlock on the record it just created — but
    a sitting owner cannot resume past a quarantined operation either.
    """
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        sContainerName,
    )
    if dictOutcomeRead["sReadState"] != "valid":
        raise MutationNotAdmittedError(
            f"The operation journal for container '{sContainerName}' is "
            f"{dictOutcomeRead['sReadState']} at the commit point "
            f"({dictOutcomeRead['sDetail'] or 'no record set exists'}); "
            "the operation refuses to proceed."
        )
    dictOperations = dictOutcomeRead["dictOperations"]
    for sRecordId, dictRecord in dictOperations.items():
        if dictRecord["sState"] == (
            operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
        ):
            raise MutationNotAdmittedError(
                f"Container '{sContainerName}' has a quarantined journal "
                f"record ({sRecordId}) that needs reconciliation; no new "
                "operation may commit until it is reconciled."
            )
    dictOwnRecord = dictOperations.get(sOperationId)
    if dictOwnRecord is None:
        raise MutationNotAdmittedError(
            f"Operation {sOperationId} has no journal record in "
            f"container '{sContainerName}'; the commit point refuses an "
            "operation whose write-ahead record is missing."
        )
    for sIdentityKey, valueExpected in (dictHolderIdentity or {}).items():
        if dictOwnRecord.get(sIdentityKey) != valueExpected:
            raise MutationNotAdmittedError(
                f"Operation {sOperationId} presented a holder identity "
                f"({sIdentityKey}) that does not match its journal "
                f"record in container '{sContainerName}'; a different "
                "holder may not commit under this record."
            )


# ---------------------------------------------------------------------
# Journaled Docker exec glue for the create -> journal -> start split.
# ---------------------------------------------------------------------

def fdictBeginJournaledExec(sContainerId):
    """Prepare an exec journal record under the active durable guard.

    Returns ``None`` when no durable admission is active for the
    container (an unenforced lane), so unwired callers journal nothing.
    """
    admission = fadmissionActiveForContainerId(
        sContainerId, bRequireDurable=True,
    )
    if admission is None:
        return None
    sOperationId = operationJournal.fsPrepareOperation(
        admission.sContainerName, "exec", sContainerId,
    )
    return {
        "sContainerName": admission.sContainerName,
        "sOperationId": sOperationId,
        "admission": admission,
    }


def fnPromoteJournaledExec(dictExecHandle, sExecId):
    """Persist the real exec id, then identity-gate before exec_start."""
    if dictExecHandle is None:
        return
    dictIdentity = {"sDockerExecId": sExecId}
    operationJournal.fnPromoteOperationToInFlight(
        dictExecHandle["sContainerName"],
        dictExecHandle["sOperationId"], dictIdentity,
    )
    fnAssertOperationAdmittedByIdentity(
        dictExecHandle["sContainerName"],
        dictExecHandle["sOperationId"], dictIdentity,
    )
    dictExecHandle["admission"].dictLiveState[
        "sActiveExecOperationId"
    ] = dictExecHandle["sOperationId"]


def fnSettleJournaledExec(dictExecHandle):
    """Settle the exec record once the daemon reports the exec ended."""
    if dictExecHandle is None:
        return
    dictLiveState = dictExecHandle["admission"].dictLiveState
    if dictLiveState.get("sActiveExecOperationId") == (
        dictExecHandle["sOperationId"]
    ):
        dictLiveState["sActiveExecOperationId"] = ""
    try:
        operationJournal.fnSettleOperation(
            dictExecHandle["sContainerName"],
            dictExecHandle["sOperationId"],
        )
    except operationJournal.OperationJournalError as error:
        logger.warning(
            "Could not settle exec journal record %s for container "
            "'%s': %s",
            dictExecHandle["sOperationId"],
            dictExecHandle["sContainerName"], error,
        )
