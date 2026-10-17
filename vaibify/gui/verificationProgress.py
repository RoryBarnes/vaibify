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

``DICT_LAST_SETTLED`` is the status of the most recent verification of
each project AS IT ENDED: the phase it settled into, its reasons and
the reproduced manifest it wrote. The task's own entry is evicted the
moment the task finishes, so without this a dashboard that watched the
run go quiet could not ask how it ended -- and could only say that it
had. A task that finished without settling its phase is recorded as
``stopped``, never as the live phase it was abandoned in.
"""

import threading
from collections import deque
from datetime import datetime, timezone

from vaibify.gui.pipelineUtils import fsLabelFromStepIndex
from vaibify.reproducibility.credentialRedactor import fsRedactCredentials

__all__ = [
    "DICT_LIVE_OUTPUT",
    "DICT_LAST_NO_VERDICT",
    "DICT_LAST_SETTLED",
    "DICT_UNSETTLED_TEARDOWN",
    "DICT_VERIFY_TASKS",
    "fbVerificationIsLive",
    "fdictReadLastSettled",
    "fdictReadLiveOutput",
    "ffnBuildRerunRecorder",
    "fdictReadNoVerdict",
    "fdictReadStatus",
    "fdictReadUnsettledTeardown",
    "fdictReadVerificationRecords",
    "fsProjectOfLiveVerification",
    "fnForgetNoVerdict",
    "fnOpenLiveOutput",
    "fnRecordRerunEvent",
    "fnRecordNoVerdict",
    "fnRecordUnsettledTeardown",
    "fnRegisterTask",
    "fsRecordKindOfLiveVerification",
]


# DICT_VERIFY_TASKS is keyed by container id: one verification holds a
# container at a time. Its status names the project it verifies
# (``sProjectRepoPath``), because a container hosts several and another
# project's verification is not this project's to show. The no-verdict
# record is keyed by (container id, project) for the same reason: a
# second project's attempt must not erase the first project's reason.
# See the module docstring for why each is in-process.
DICT_VERIFY_TASKS = {}
DICT_LAST_NO_VERDICT = {}
DICT_LAST_SETTLED = {}

# The most recent rerun whose SHADOW container could not be proven
# destroyed. In-process like the records above, but for a different
# reason: the container itself outlives any record of it, and the next
# rerun's crash-sweep is what actually reclaims it. This record exists
# so the researcher is told a container may still be running on their
# daemon, instead of the quarantine being absorbed into a passed
# attestation's response and shown nowhere (found by external review,
# 2026-09-01). Cleared when a later rerun tears down cleanly.
DICT_UNSETTLED_TEARDOWN = {}

# What the rerun is printing, per (container id, project): the last
# lines of the shadow's output and the step running, so the PROOF tab
# can show the run as it happens. Written from the rerun's worker
# thread and read by the poll on the hub's loop, hence the lock; kept
# OUT of the status dict, which is serialized raw and copied at
# settle. The record outlives its run so the last lines stay readable
# after it ends; the next launch for the project replaces it. Every
# line is redacted and capped before it is kept.
DICT_LIVE_OUTPUT = {}
_LOCK_LIVE_OUTPUT = threading.Lock()
I_LIVE_OUTPUT_LINES = 200
_I_LINE_CHARACTERS = 2000

# The phases a verification passes through before it settles. A phase
# outside this set is a settled one, so a caller cannot make the row
# pulse forever by inventing a name.
_T_LIVE_PHASES = ("starting", "running")
S_PHASE_STOPPED = "stopped"


def fnRegisterTask(sContainerId, taskWorker, dictStatus):
    """Store the verify task and arrange identity-checked self-eviction.

    Mirrors ``pipelineRunSlots.fnRegisterRun`` so completed
    verifications do not linger forever. The identity check on the
    slot's task object prevents a brand-new verification that landed in
    the same slot from being evicted by the prior task's done-callback
    firing late.
    """
    DICT_VERIFY_TASKS[sContainerId] = {
        "task": taskWorker, "dictStatus": dictStatus,
    }
    fnOpenLiveOutput(
        sContainerId, dictStatus.get("sProjectRepoPath"),
        dictStatus.get("sAttemptId", ""),
    )

    def fnEvictOnDone(taskCompleted):
        _fnRecordSettled(sContainerId, dictStatus)
        dictEntry = DICT_VERIFY_TASKS.get(sContainerId)
        if dictEntry is not None and dictEntry.get("task") is taskCompleted:
            DICT_VERIFY_TASKS.pop(sContainerId, None)
    taskWorker.add_done_callback(fnEvictOnDone)


def _fnRecordSettled(sContainerId, dictStatus):
    """Keep how a finished verification ended, after its entry is evicted."""
    dictSettled = dict(dictStatus)
    if dictSettled.get("sPhase") in _T_LIVE_PHASES:
        dictSettled["sPhase"] = S_PHASE_STOPPED
    dictSettled["sSettledIso"] = datetime.now(timezone.utc).isoformat()
    DICT_LAST_SETTLED[
        (sContainerId, dictSettled.get("sProjectRepoPath"))] = dictSettled


def fdictReadLastSettled(sContainerId, sProjectRepoPath):
    """Return how this project's most recent verification ended, or ``None``.

    The live entry answers first when its phase has already settled:
    the worker records the outcome a moment before the task finishes,
    and a poll in that window has already reported the run over.
    """
    dictStatus = fdictReadStatus(sContainerId, sProjectRepoPath)
    if dictStatus and dictStatus.get("sPhase") not in _T_LIVE_PHASES:
        return dictStatus
    return DICT_LAST_SETTLED.get((sContainerId, sProjectRepoPath))


def fdictReadStatus(sContainerId, sProjectRepoPath):
    """Return the in-flight status of this project's verification, or ``None``."""
    dictStatus = (DICT_VERIFY_TASKS.get(sContainerId) or {}).get("dictStatus")
    if not dictStatus or dictStatus.get("sProjectRepoPath") != sProjectRepoPath:
        return None
    return dictStatus


def fsProjectOfLiveVerification(sContainerId):
    """Return the project whose verification holds the container, or ""."""
    dictEntry = DICT_VERIFY_TASKS.get(sContainerId) or {}
    taskExisting = dictEntry.get("task")
    if taskExisting is None or taskExisting.done():
        return ""
    return (dictEntry.get("dictStatus") or {}).get("sProjectRepoPath", "")


def fbVerificationIsLive(sContainerId, sProjectRepoPath):
    """Return True iff this project's verification is running.

    Read from the recorded PHASE rather than from the task's
    ``done()``, because the poll must agree with what the attestation
    endpoint reports to the PROOF tab; two derivations of "is it
    running" would let the row pulse while the card said finished.
    """
    dictStatus = fdictReadStatus(sContainerId, sProjectRepoPath)
    if not dictStatus:
        return False
    return dictStatus.get("sPhase") in _T_LIVE_PHASES


def fsRecordKindOfLiveVerification(sContainerId, sProjectRepoPath):
    """Return the record this project's live verification writes, or "".

    "" when nothing is running, and in the run's first moment, before
    it has asked git whose attestation this clone carries. The worker
    asks before any step runs, so the answer arrives with the rerun.
    """
    if not fbVerificationIsLive(sContainerId, sProjectRepoPath):
        return ""
    return fdictReadStatus(sContainerId, sProjectRepoPath).get(
        "sRecordKindInFlight", "")


def fnRecordNoVerdict(sContainerId, sProjectRepoPath, listReasons,
                      fDurationSeconds, sManifestDigest):
    """Remember why a project's verification established nothing."""
    DICT_LAST_NO_VERDICT[(sContainerId, sProjectRepoPath)] = {
        "listReasons": list(listReasons or []),
        "fDurationSeconds": float(fDurationSeconds),
        "sManifestDigest": sManifestDigest,
        "sRecordedIso": datetime.now(timezone.utc).isoformat(),
    }


def fdictReadNoVerdict(sContainerId, sProjectRepoPath):
    """Return the project's last no-verdict record, or ``None``."""
    return DICT_LAST_NO_VERDICT.get((sContainerId, sProjectRepoPath))


def fnForgetNoVerdict(sContainerId, sProjectRepoPath):
    """Drop the project's no-verdict record; a new attempt supersedes it."""
    DICT_LAST_NO_VERDICT.pop((sContainerId, sProjectRepoPath), None)


def fnRecordUnsettledTeardown(sContainerId, sOutcome, sReason):
    """Remember, or clear, whether the last shadow was proven destroyed.

    Called after EVERY rerun that reached the teardown: a clean
    destruction clears the record, because the standing warning is
    about the researcher's daemon now, not about history.
    """
    if not sReason:
        DICT_UNSETTLED_TEARDOWN.pop(sContainerId, None)
        return
    DICT_UNSETTLED_TEARDOWN[sContainerId] = {
        "sOutcome": sOutcome,
        "sReason": sReason,
    }


def fdictReadUnsettledTeardown(sContainerId):
    """Return the unsettled-teardown record for a container, or ``None``."""
    return DICT_UNSETTLED_TEARDOWN.get(sContainerId)


def fnOpenLiveOutput(sContainerId, sProjectRepoPath, sAttemptId):
    """Start an empty output record for a verification about to launch."""
    with _LOCK_LIVE_OUTPUT:
        DICT_LIVE_OUTPUT[(sContainerId, sProjectRepoPath)] = {
            "sAttemptId": sAttemptId,
            "dequeLines": deque(maxlen=I_LIVE_OUTPUT_LINES),
            "iLineCount": 0, "sStepLabel": "", "sStepName": "",
        }


def ffnBuildRerunRecorder(sContainerId, sProjectRepoPath, dictWorkflow):
    """Return the status callback that keeps what one rerun prints.

    SYNCHRONOUS on purpose: the shadow lane runs in a worker thread and
    calls it straight; the record it writes takes a threading lock.
    """
    def fnRecordEvent(dictEvent):
        fnRecordRerunEvent(sContainerId, sProjectRepoPath, dictEvent, dictWorkflow)
    return fnRecordEvent


def fnRecordRerunEvent(sContainerId, sProjectRepoPath, dictEvent, dictWorkflow):
    """Fold one rerun status event into the project's output record.

    SYNCHRONOUS: the rerun calls it from its own worker thread.
    """
    sType = str((dictEvent or {}).get("sType") or "")
    listLines = _flistLinesOfEvent(sType, dictEvent, dictWorkflow)
    with _LOCK_LIVE_OUTPUT:
        dictRecord = DICT_LIVE_OUTPUT.get((sContainerId, sProjectRepoPath))
        if dictRecord is None:
            return
        if sType == "stepStarted":
            dictRecord.update(_fdictDescribeStep(dictEvent, dictWorkflow))
        for sLine in listLines:
            dictRecord["dequeLines"].append(
                fsRedactCredentials(sLine)[:_I_LINE_CHARACTERS],
            )
            dictRecord["iLineCount"] += 1


def _flistLinesOfEvent(sType, dictEvent, dictWorkflow):
    """The display lines one event contributes; most events contribute none."""
    if sType == "outputBatch":
        return [str(sLine) for sLine in dictEvent.get("listLines") or []]
    if sType == "output":
        return [str(dictEvent.get("sLine") or "")]
    if sType == "stepStarted":
        dictStep = _fdictDescribeStep(dictEvent, dictWorkflow)
        return [f"--- Step {dictStep['sStepLabel']}: {dictStep['sStepName']} ---"]
    if sType == "comparingOutputs":
        return ["--- Every step has run; comparing the outputs with the manifest ---"]
    if sType == "tearingDownShadow":
        return ["--- Destroying the throwaway container ---"]
    return []


def _fdictDescribeStep(dictEvent, dictWorkflow):
    """The label and name of the step a ``stepStarted`` event names."""
    iStepIndex = max(int(dictEvent.get("iStepNumber") or 1) - 1, 0)
    listSteps = (dictWorkflow or {}).get("listSteps") or []
    if iStepIndex >= len(listSteps):
        return {"sStepLabel": str(iStepIndex + 1), "sStepName": ""}
    return {
        "sStepLabel": fsLabelFromStepIndex(dictWorkflow, iStepIndex),
        "sStepName": str(listSteps[iStepIndex].get("sName") or ""),
    }


def fdictReadLiveOutput(sContainerId, sProjectRepoPath):
    """Return a copy of this project's rerun output record, or ``None``."""
    with _LOCK_LIVE_OUTPUT:
        dictRecord = DICT_LIVE_OUTPUT.get((sContainerId, sProjectRepoPath))
        if dictRecord is None:
            return None
        return {
            "sAttemptId": dictRecord["sAttemptId"],
            "saTailLines": list(dictRecord["dequeLines"]),
            "iLineCount": dictRecord["iLineCount"],
            "sStepLabel": dictRecord["sStepLabel"],
            "sStepName": dictRecord["sStepName"],
        }


def fdictReadVerificationRecords(sContainerId, sProjectRepoPath):
    """Return every in-process record of this project's verifications.

    The attestation poll's view of them, under the keys the PROOF tab
    reads: the run in flight, how the last one settled, why the last one
    reached no verdict, a shadow not proven destroyed, and the output
    the latest run printed.
    """
    return {
        "dictInFlight": fdictReadStatus(sContainerId, sProjectRepoPath),
        "dictLastNoVerdict": fdictReadNoVerdict(sContainerId, sProjectRepoPath),
        "dictLastSettled": fdictReadLastSettled(sContainerId, sProjectRepoPath),
        "dictUnsettledTeardown": fdictReadUnsettledTeardown(sContainerId),
        "dictLiveOutput": fdictReadLiveOutput(sContainerId, sProjectRepoPath),
    }
