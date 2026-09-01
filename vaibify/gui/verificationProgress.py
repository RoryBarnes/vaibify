"""In-process record of the L3 verification running in each container.

WHY THIS IS NOT IN THE ROUTE MODULE
-----------------------------------

It was, and two lanes now need it. ``reproducibilityRoutes`` starts a
verification and reports its result; the pipeline poll has to know that
one is *running*, so the Rebuild-attestation row can pulse instead of
sitting red and still for the hours a rerun takes. A route module may
not import a sibling route module, and the concept — "a verification is
live in this container" — is real and was homeless. This is its home.

WHAT LIVES HERE, AND WHAT ITS LIFETIME MEANS
--------------------------------------------

Two records, both deliberately in-process and both lost on a hub
restart.

``DICT_VERIFY_TASKS`` is the live task plus a small status dict. It
cannot outlive the process because the ``asyncio.Task`` cannot: a
restart kills the rerun, and a record claiming one is still running
would be a lie the next poll would repeat.

``DICT_LAST_NO_VERDICT`` is the most recent verification that reached
NO verdict — refused before any step ran, or crashed. It is emphatically
**not** an attestation, and that is the whole reason it exists here
rather than on disk: an attestation is a scientific claim keyed to a
manifest digest, and a refusal establishes nothing about whether the
workflow reproduces. Writing one as ``failed`` put exactly that claim
on a researcher's disk, and destroyed an earlier passing attestation
the unchanged manifest still entitled the project to (2026-08-31).
Nothing was established, so nothing is persisted — but the researcher
is still told, which is what this record is for. It outlives its task
(a finished task is evicted at once) because the reason has to be
readable after the run is gone.
"""

__all__ = [
    "DICT_LAST_NO_VERDICT",
    "DICT_VERIFY_TASKS",
    "fbVerificationIsLive",
    "fdictReadNoVerdict",
    "fdictReadStatus",
    "fnForgetNoVerdict",
    "fnRecordNoVerdict",
    "fnRegisterTask",
]


# Keyed by container id. See the module docstring for why each is
# in-process and why they are separate.
DICT_VERIFY_TASKS = {}
DICT_LAST_NO_VERDICT = {}

# The phases a verification passes through before it settles. A phase
# outside this set is a settled one, so a caller cannot make the row
# pulse forever by inventing a name.
_T_LIVE_PHASES = ("starting", "running")


def fnRegisterTask(sContainerId, taskWorker, dictStatus):
    """Store the verify task and arrange identity-checked self-eviction.

    Mirrors ``pipelineServer._fnRegisterPipelineTask`` so completed
    verifications do not linger forever. The identity check on the
    slot's task object prevents a brand-new verification that landed in
    the same slot from being evicted by the prior task's done-callback
    firing late.
    """
    DICT_VERIFY_TASKS[sContainerId] = {
        "task": taskWorker, "dictStatus": dictStatus,
    }

    def fnEvictOnDone(taskCompleted):
        dictEntry = DICT_VERIFY_TASKS.get(sContainerId)
        if dictEntry is not None and dictEntry.get("task") is taskCompleted:
            DICT_VERIFY_TASKS.pop(sContainerId, None)
    taskWorker.add_done_callback(fnEvictOnDone)


def fdictReadStatus(sContainerId):
    """Return the in-flight status dict for a container, or ``None``."""
    return (DICT_VERIFY_TASKS.get(sContainerId) or {}).get("dictStatus")


def fbVerificationIsLive(sContainerId):
    """Return True iff a verification is running in this container.

    Read from the recorded PHASE rather than from the task's
    ``done()``, because the poll must agree with what the attestation
    endpoint reports to the PROOF tab; two derivations of "is it
    running" would let the row pulse while the card said finished.
    """
    dictStatus = fdictReadStatus(sContainerId)
    if not dictStatus:
        return False
    return dictStatus.get("sPhase") in _T_LIVE_PHASES


def fnRecordNoVerdict(sContainerId, listReasons, fDurationSeconds,
                      sManifestDigest):
    """Remember why a verification established nothing."""
    DICT_LAST_NO_VERDICT[sContainerId] = {
        "listReasons": list(listReasons or []),
        "fDurationSeconds": float(fDurationSeconds),
        "sManifestDigest": sManifestDigest,
    }


def fdictReadNoVerdict(sContainerId):
    """Return the last no-verdict record for a container, or ``None``."""
    return DICT_LAST_NO_VERDICT.get(sContainerId)


def fnForgetNoVerdict(sContainerId):
    """Drop the no-verdict record; a new attempt supersedes the old one."""
    DICT_LAST_NO_VERDICT.pop(sContainerId, None)
