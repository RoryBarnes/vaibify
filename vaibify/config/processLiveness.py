"""Safe PID-liveness probe shared by the lock and slot registries.

``os.kill(iPid, 0)`` delivers no signal but reports whether the
target process exists. ``PermissionError`` means the process exists
under another user, so it counts as alive. This probe is the
fallback that breaks claims whose flock outlived the recorded
holder (for example, a lock file descriptor leaked into a
surviving descendant of a killed vaibify server).

``os.kill(pid, 0)`` alone is fooled by PID reuse: after a holder
exits, the kernel may hand its PID to an unrelated process, and the
bare existence check then reports the stale claim as live forever.
``fbIsProcessAliveSince`` closes that gap by comparing the holder's
recorded claim time against the live process's start time read from
``ps``. A process that started after the claim is a recycled PID and
is treated as dead, while any unreadable start time or absent claim
falls back to the PID-only check (conservative: never reaps a live
genuine holder).
"""

__all__ = [
    "fbIsUsablePid",
    "fbIsProcessAlive",
    "fbIsProcessAliveSince",
    "fdtReadProcessStartClock",
    "fdtReadProcessStartClockCached",
    "fdtParseClaimIso",
]

import datetime
import os
import subprocess


_F_RECYCLE_TOLERANCE_SECONDS = 2.0


def fbIsUsablePid(iPid):
    """Return True when iPid is a usable positive process id (never a bool)."""
    return isinstance(iPid, int) and not isinstance(iPid, bool) and iPid > 0


def fbIsProcessAlive(iPid):
    """Return True when a process with the given PID currently exists."""
    if not fbIsUsablePid(iPid):
        return False
    try:
        os.kill(iPid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def fbIsProcessAliveSince(iPid, sClaimIso, dictStartClockCache=None):
    """Return True unless the PID was recycled after the recorded claim.

    ``False`` when the PID does not exist. When either the live start
    time or the recorded claim cannot be resolved, fall back to the
    PID-only check (return ``True``) so old payloads and unreadable
    start times behave exactly like ``fbIsProcessAlive``. Otherwise a
    start time later than the claim (within tolerance) marks a
    recycled PID as dead. ``dictStartClockCache``, when supplied,
    memoizes the ``ps`` start-clock probe per PID so one registry
    refresh spawns at most one ``ps`` per distinct live PID.
    """
    if not fbIsProcessAlive(iPid):
        return False
    dtStart = fdtReadProcessStartClockCached(iPid, dictStartClockCache)
    dtClaim = fdtParseClaimIso(sClaimIso)
    if dtStart is None or dtClaim is None:
        return True
    dtTolerance = datetime.timedelta(seconds=_F_RECYCLE_TOLERANCE_SECONDS)
    return dtStart <= dtClaim + dtTolerance


def fdtReadProcessStartClockCached(iPid, dictStartClockCache):
    """Return a PID's start clock, memoizing per refresh to batch ps spawns.

    With ``dictStartClockCache`` ``None`` the probe runs on every call
    (the historical behavior). With a dict supplied, each PID's result
    is stored and reused, so repeated liveness checks across one
    registry refresh spawn ``ps`` at most once per distinct PID.
    """
    if dictStartClockCache is None:
        return fdtReadProcessStartClock(iPid)
    if iPid not in dictStartClockCache:
        dictStartClockCache[iPid] = fdtReadProcessStartClock(iPid)
    return dictStartClockCache[iPid]


def fdtReadProcessStartClock(iPid):
    """Return a PID's start time from ``ps``, or None on any failure."""
    if not fbIsUsablePid(iPid):
        return None
    sStarted = _fsReadStartTimeFromProcessStatus(iPid)
    if not sStarted:
        return None
    try:
        # ``ps`` emits C-locale names (forced via LC_ALL=C); strptime parses
        # with the Python process's LC_TIME, which vaibify never changes from
        # the default C. A locale mismatch only raises ValueError here, which
        # degrades to conservative-alive below (never a false reap).
        return datetime.datetime.strptime(sStarted, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


def _fsReadStartTimeFromProcessStatus(iPid):
    """Return ``ps -o lstart=`` output for a PID, or '' on any failure."""
    try:
        resultProcess = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(iPid)],
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return resultProcess.stdout.strip()


def fdtParseClaimIso(sClaimIso):
    """Return a claim ISO string as a naive-local datetime, or None."""
    if not isinstance(sClaimIso, str) or not sClaimIso:
        return None
    try:
        dtClaim = datetime.datetime.fromisoformat(sClaimIso)
    except ValueError:
        return None
    return _fdtNormalizeToNaiveLocal(dtClaim)


def _fdtNormalizeToNaiveLocal(dtValue):
    """Drop tzinfo, converting an aware datetime to local naive time."""
    if dtValue.tzinfo is None:
        return dtValue
    return dtValue.astimezone().replace(tzinfo=None)
