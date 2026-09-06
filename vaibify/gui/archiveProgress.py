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
"""

__all__ = [
    "DICT_DEPOSITS",
    "S_PHASE_FAILED",
    "S_PHASE_SAVING",
    "S_PHASE_SETTLED",
    "S_PHASE_STARTING",
    "S_PHASE_UPLOADING",
    "fbDepositIsLive",
    "fdictReadDeposit",
    "fnForgetDeposit",
    "fnRecordFailure",
    "fnRecordProgress",
    "fnRegisterDeposit",
    "fnSettleDeposit",
]

import threading


S_PHASE_STARTING = "starting"
S_PHASE_SAVING = "saving"
S_PHASE_UPLOADING = "uploading"
S_PHASE_SETTLED = "settled"
S_PHASE_FAILED = "failed"

# The phases during which the row pulses. A phase outside this set is
# a settled one, so a caller cannot make the row pulse forever by
# inventing a name.
_T_LIVE_PHASES = (S_PHASE_STARTING, S_PHASE_SAVING, S_PHASE_UPLOADING)

DICT_DEPOSITS = {}
_LOCK_DEPOSITS = threading.Lock()


def fnRegisterDeposit(sContainerId, taskWorker):
    """Record that a deposit has started in this container."""
    with _LOCK_DEPOSITS:
        DICT_DEPOSITS[sContainerId] = {
            "task": taskWorker,
            "sPhase": S_PHASE_STARTING,
            "iBytesRead": 0,
            "iBytesTotal": 0,
            "sReason": "",
        }


def fnRecordProgress(sContainerId, sPhase, iBytesRead=0, iBytesTotal=0):
    """Update one live deposit's phase and byte counters."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId)
        if dictEntry is None:
            return
        dictEntry["sPhase"] = sPhase
        dictEntry["iBytesRead"] = iBytesRead
        dictEntry["iBytesTotal"] = iBytesTotal


def fnSettleDeposit(sContainerId):
    """Mark a deposit finished; the row stops pulsing."""
    fnRecordProgress(sContainerId, S_PHASE_SETTLED)


def fnRecordFailure(sContainerId, sReason):
    """Mark a deposit failed and keep the reason for the researcher."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.setdefault(sContainerId, {})
        dictEntry["task"] = None
        dictEntry["sPhase"] = S_PHASE_FAILED
        dictEntry["sReason"] = sReason


def fnForgetDeposit(sContainerId):
    """Drop this container's deposit record entirely."""
    with _LOCK_DEPOSITS:
        DICT_DEPOSITS.pop(sContainerId, None)


def fbDepositIsLive(sContainerId):
    """Return True iff a deposit is running in this container."""
    return (fdictReadDeposit(sContainerId) or {}).get(
        "sPhase",
    ) in _T_LIVE_PHASES


def fdictReadDeposit(sContainerId):
    """Return the wire-shaped deposit record, or ``None``."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get(sContainerId)
        if not dictEntry:
            return None
        return {
            "sPhase": dictEntry.get("sPhase") or "",
            "iBytesRead": dictEntry.get("iBytesRead") or 0,
            "iBytesTotal": dictEntry.get("iBytesTotal") or 0,
            "sReason": dictEntry.get("sReason") or "",
        }
