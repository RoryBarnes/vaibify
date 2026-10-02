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

Keyed by (container, project): one deposit holds a container at a
time, but a container hosts several projects, and another project's
deposit is neither this project's row to pulse nor this project's
answer to erase.
"""

__all__ = [
    "DICT_DEPOSITS",
    "S_PHASE_FAILED",
    "S_PHASE_SAVING",
    "S_PHASE_SETTLED",
    "S_PHASE_STARTING",
    "S_PHASE_UPLOADING",
    "S_PHASE_VERIFYING",
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
# Asking the archive what it stored, after the upload and before the
# record is called good. Seconds, not minutes: Zenodo reports the MD5
# it computed server-side, so the check is one small request rather
# than a re-download.
S_PHASE_VERIFYING = "verifying"
S_PHASE_SETTLED = "settled"
S_PHASE_FAILED = "failed"

# The phases during which the row pulses. A phase outside this set is
# a settled one, so a caller cannot make the row pulse forever by
# inventing a name.
_T_LIVE_PHASES = (
    S_PHASE_STARTING, S_PHASE_SAVING, S_PHASE_UPLOADING,
    S_PHASE_VERIFYING,
)

# Keyed by (container id, project repo path): a container hosts several
# projects, and a second project's deposit must not erase the first's
# settled or failed record. At most one entry per container is LIVE.
DICT_DEPOSITS = {}
_LOCK_DEPOSITS = threading.Lock()


def _ftKeyOfLiveDeposit(sContainerId):
    """Return the key of the container's live deposit, or ``None``.

    Callers hold ``_LOCK_DEPOSITS``. The progress calls name only the
    container, because one deposit holds a container at a time.
    """
    for tKey, dictEntry in DICT_DEPOSITS.items():
        if tKey[0] == sContainerId and (
            dictEntry.get("sPhase") in _T_LIVE_PHASES
        ):
            return tKey
    return None


def fnRegisterDeposit(sContainerId, taskWorker, sProjectRepoPath):
    """Record that a project's deposit has started in this container."""
    with _LOCK_DEPOSITS:
        DICT_DEPOSITS[(sContainerId, sProjectRepoPath)] = {
            "sProjectRepoPath": sProjectRepoPath,
            "task": taskWorker,
            "sPhase": S_PHASE_STARTING,
            "iBytesRead": 0,
            "iBytesTotal": 0,
            "sReason": "",
        }


def fnRecordProgress(sContainerId, sPhase, iBytesRead=0, iBytesTotal=0):
    """Update the container's live deposit's phase and byte counters."""
    with _LOCK_DEPOSITS:
        tKey = _ftKeyOfLiveDeposit(sContainerId)
        if tKey is None:
            return
        dictEntry = DICT_DEPOSITS[tKey]
        dictEntry["sPhase"] = sPhase
        dictEntry["iBytesRead"] = iBytesRead
        dictEntry["iBytesTotal"] = iBytesTotal


def fnSettleDeposit(sContainerId):
    """Mark a deposit finished; the row stops pulsing."""
    fnRecordProgress(sContainerId, S_PHASE_SETTLED)


def fnRecordFailure(sContainerId, sProjectRepoPath, sReason):
    """Mark a project's deposit failed and keep the reason."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.setdefault(
            (sContainerId, sProjectRepoPath), {},
        )
        dictEntry["sProjectRepoPath"] = sProjectRepoPath
        dictEntry["task"] = None
        dictEntry["sPhase"] = S_PHASE_FAILED
        dictEntry["sReason"] = sReason


def fnForgetDeposit(sContainerId, sProjectRepoPath):
    """Drop this project's deposit record in the container."""
    with _LOCK_DEPOSITS:
        DICT_DEPOSITS.pop((sContainerId, sProjectRepoPath), None)


def fbDepositIsLive(sContainerId):
    """Return True iff a deposit, for any project, runs in this container."""
    with _LOCK_DEPOSITS:
        return _ftKeyOfLiveDeposit(sContainerId) is not None


def fdictReadDeposit(sContainerId, sProjectRepoPath):
    """Return this project's wire-shaped deposit record, or ``None``."""
    with _LOCK_DEPOSITS:
        dictEntry = DICT_DEPOSITS.get((sContainerId, sProjectRepoPath))
        if not dictEntry:
            return None
        return {
            "sPhase": dictEntry.get("sPhase") or "",
            "iBytesRead": dictEntry.get("iBytesRead") or 0,
            "iBytesTotal": dictEntry.get("iBytesTotal") or 0,
            "sReason": dictEntry.get("sReason") or "",
        }
