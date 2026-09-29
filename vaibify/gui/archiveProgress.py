"""How far this container's environment-archive deposit has got.

A deposit saves a multi-gigabyte image, compresses it, and uploads it.
That is minutes, and a silent minute reads as a hang from the chair --
the same lesson the Level 3 verification learned. The row pulses while
this says a deposit is live and shows the bytes it has moved.

In-process and lost on a hub restart, deliberately, for the reason
:mod:`vaibify.gui.verificationProgress` gives: the work is an
``asyncio.Task`` that cannot outlive the process, so a record claiming
one is still running would be a lie the next poll would repeat.

The FAILURE reason outlives its task. A deposit that could not
complete has established nothing -- no DOI, no record on disk -- so
there is nothing to persist, and the researcher still has to be told
why. That is what the settled entry is for.

Keyed by container, because one deposit holds a container at a time,
and each record names the project it deposits for: a container hosts
several projects, and another project's deposit is neither this
project's row to pulse nor this project's answer to erase.
"""

__all__ = [
    "DICT_DEPOSITS",
    "DepositStoppedError",
    "S_PHASE_PUBLISHING",
    "S_PHASE_STOPPED",
    "S_PHASE_CHECKING_AGENTS",
    "S_PHASE_FAILED",
    "S_PHASE_PREPARING_DRAFT",
    "S_PHASE_SAVING",
    "S_PHASE_SETTLED",
    "S_PHASE_STARTING",
    "S_PHASE_UPLOADING",
    "S_PHASE_VERIFYING",
    "fbDepositIsLive",
    "fbEnterPublishingUnlessStopped",
    "fbRequestStop",
    "fnRaiseIfStopRequested",
    "fnRecordStopped",
    "fbPhaseIsLive",
    "fdictReadDeposit",
    "fnForgetDeposit",
    "fnRecordFailure",
    "fnRecordProgress",
    "fnRecordUploadAttemptFailed",
    "fnRegisterDeposit",
    "fnSettleDeposit",
]

import threading


S_PHASE_STARTING = "starting"
# Reading the whole image to prove the coding agents' layers leave the
# environment alone -- tens of seconds that used to show as "starting".
S_PHASE_CHECKING_AGENTS = "checking-agents"
S_PHASE_SAVING = "saving"
# Creating the Zenodo draft (or the new version of the earlier record),
# clearing the inherited image out of it and describing this one.
S_PHASE_PREPARING_DRAFT = "preparing-draft"
S_PHASE_UPLOADING = "uploading"
# Asking the archive what it stored, after the upload and before the
# record is called good. Seconds, not minutes: Zenodo reports the MD5
# it computed server-side, so the check is one small request rather
# than a re-download.
S_PHASE_VERIFYING = "verifying"
# The publish mints a permanent DOI, so from here on the deposit can no
# longer be stopped; entering this phase and honoring a stop request
# are decided under one lock, so neither can slip past the other.
S_PHASE_PUBLISHING = "publishing"
S_PHASE_SETTLED = "settled"
S_PHASE_FAILED = "failed"
# The researcher stopped the deposit before the publish: nothing was
# published. A phase of its own because "failed" would be a false
# account of a decision.
S_PHASE_STOPPED = "stopped"

# The phases during which the row pulses. A phase outside this set is
# a settled one, so a caller cannot make the row pulse forever by
# inventing a name.
_T_LIVE_PHASES = (
    S_PHASE_STARTING, S_PHASE_CHECKING_AGENTS, S_PHASE_SAVING,
    S_PHASE_PREPARING_DRAFT, S_PHASE_UPLOADING, S_PHASE_VERIFYING,
    S_PHASE_PUBLISHING,
)


class DepositStoppedError(Exception):
    """The researcher stopped the deposit; raised at its next checkpoint.

    Not an ``OSError``: every ``except OSError`` on the upload path --
    and the retry, which catches connection errors -- must let it
    through, or a stop would read as a dropped connection and be retried.
    """


def fbPhaseIsLive(sPhase):
    """True when ``sPhase`` names a deposit still under way."""
    return sPhase in _T_LIVE_PHASES

DICT_DEPOSITS = {}
_LOCK_DEPOSITS = threading.Lock()


def fnRegisterDeposit(
    sContainerId, taskWorker, sProjectRepoPath, bStoppable=False,
):
    """Record that a project's deposit has started in this container.

    ``bStoppable`` is true only for work whose checkpoints honor a stop:
    a promotion keeps a crash-recovery record of its draft, and
    stopping it would leave that record naming a discarded draft.
    """
    with _LOCK_DEPOSITS:
        DICT_DEPOSITS[sContainerId] = {
            "sProjectRepoPath": sProjectRepoPath,
            "task": taskWorker,
            "sPhase": S_PHASE_STARTING,
            "iBytesRead": 0,
            "iBytesTotal": 0,
            "sReason": "",
            "listAttempts": [],
            "bStoppable": bool(bStoppable),
            "bStopRequested": False,
        }


def fbRequestStop(sContainerId, sProjectRepoPath):
    """Ask this project's live deposit to stop; False when it cannot.

    It cannot when no stoppable deposit of this project is live, or
    when the publish has begun -- a DOI is being minted and a stop now
    would be a promise nothing can keep.
    """
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId) or {}
        if (
            dictEntry.get("sProjectRepoPath") != sProjectRepoPath
            or not dictEntry.get("bStoppable")
            or dictEntry.get("sPhase") not in _T_LIVE_PHASES
            or dictEntry.get("sPhase") == S_PHASE_PUBLISHING
        ):
            return False
        dictEntry["bStopRequested"] = True
        return True


def fnRaiseIfStopRequested(sContainerId):
    """Raise ``DepositStoppedError`` when the researcher asked to stop."""
    with _LOCK_DEPOSITS:
        bStop = (DICT_DEPOSITS.get(sContainerId) or {}).get("bStopRequested")
    if bStop:
        raise DepositStoppedError("You stopped the deposit.")


def fbEnterPublishingUnlessStopped(sContainerId):
    """Enter the publish phase, or return False when a stop came first."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId)
        if dictEntry is None:
            return True
        if dictEntry.get("bStopRequested"):
            return False
        dictEntry["sPhase"] = S_PHASE_PUBLISHING
        return True


def fnRecordProgress(
    sContainerId, sPhase, iBytesRead=0, iBytesTotal=0, iAttempt=0,
):
    """Update one live deposit's phase, byte counters and upload attempt.

    ``iAttempt`` is the upload attempt under way, 0 when none is: a
    dropped connection restarts the upload from zero, and a byte
    counter that goes backwards unexplained reads as a fault.
    """
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId)
        if dictEntry is None:
            return
        dictEntry["sPhase"] = sPhase
        dictEntry["iBytesRead"] = iBytesRead
        dictEntry["iBytesTotal"] = iBytesTotal
        dictEntry["iAttempt"] = iAttempt


def fnRecordUploadAttemptFailed(sContainerId, dictAttempt):
    """Keep one upload attempt that ended without Zenodo's answer.

    Kept past the attempts after it and past the deposit's failure: a
    researcher deciding whether to try again needs to see how far each
    attempt got and how long it ran, not only the last one's reason.
    Only the named fields are copied, so nothing else the upload knew
    can reach the poll.
    """
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId)
        if dictEntry is None:
            return
        dictEntry.setdefault("listAttempts", []).append({
            sKey: dictAttempt.get(sKey) for sKey in (
                "iAttempt", "iBytesSent", "iBytesTotal", "fSeconds",
                "sCause", "iStatus",
            )
        })


def fnSettleDeposit(sContainerId):
    """Mark a deposit finished; the row stops pulsing."""
    fnRecordProgress(sContainerId, S_PHASE_SETTLED)


def fnRecordFailure(sContainerId, sProjectRepoPath, sReason):
    """Mark a project's deposit failed and keep the reason."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.setdefault(sContainerId, {})
        dictEntry["sProjectRepoPath"] = sProjectRepoPath
        dictEntry["task"] = None
        dictEntry["sPhase"] = S_PHASE_FAILED
        dictEntry["sReason"] = sReason


def fnRecordStopped(sContainerId, sProjectRepoPath):
    """Mark a project's deposit stopped by the researcher."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.setdefault(sContainerId, {})
        dictEntry["sProjectRepoPath"] = sProjectRepoPath
        dictEntry["task"] = None
        dictEntry["sPhase"] = S_PHASE_STOPPED
        dictEntry["sReason"] = ""


def fnForgetDeposit(sContainerId, sProjectRepoPath):
    """Drop the container's deposit record if it is this project's."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId) or {}
        if dictEntry.get("sProjectRepoPath") == sProjectRepoPath:
            DICT_DEPOSITS.pop(sContainerId, None)


def fbDepositIsLive(sContainerId):
    """Return True iff a deposit, for any project, runs in this container."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId) or {}
        return dictEntry.get("sPhase") in _T_LIVE_PHASES


def fdictReadDeposit(sContainerId, sProjectRepoPath):
    """Return this project's wire-shaped deposit record, or ``None``."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId)
        if not dictEntry or (
            dictEntry.get("sProjectRepoPath") != sProjectRepoPath
        ):
            return None
        return {
            "sPhase": dictEntry.get("sPhase") or "",
            "iBytesRead": dictEntry.get("iBytesRead") or 0,
            "iBytesTotal": dictEntry.get("iBytesTotal") or 0,
            "iAttempt": dictEntry.get("iAttempt") or 0,
            "sReason": dictEntry.get("sReason") or "",
            "bStoppable": bool(dictEntry.get("bStoppable")),
            "bStopRequested": bool(dictEntry.get("bStopRequested")),
            "listAttempts": [
                dict(dictAttempt)
                for dictAttempt in dictEntry.get("listAttempts") or []
            ],
        }
