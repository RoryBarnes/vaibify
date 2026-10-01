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

Both clocks are UTC instants. A claim is written by ``fsNowClaimIso``
and the start is computed as now minus the elapsed time ``ps`` reports,
so a change of time zone (travel, daylight-saving fall-back) between
the claim and the check cannot make a live holder look recycled.
"""

__all__ = [
    "fbIsUsablePid",
    "fbIsProcessAlive",
    "fbIsProcessAliveSince",
    "ftEnumerateSessionMembers",
    "fdatetimeReadProcessStartClock",
    "fdatetimeReadProcessStartClockCached",
    "fdatetimeParseClaimIso",
    "fsNowClaimIso",
    "fdParseElapsedSeconds",
]

import datetime
import os
import re
import subprocess


_F_RECYCLE_TOLERANCE_SECONDS = 2.0


def fsNowClaimIso():
    """Return the current instant as a UTC ISO string, for a holder claim."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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
    datetimeStart = fdatetimeReadProcessStartClockCached(iPid, dictStartClockCache)
    datetimeClaim = fdatetimeParseClaimIso(sClaimIso)
    if datetimeStart is None or datetimeClaim is None:
        return True
    fSecondsStartedAfterClaim = (datetimeStart - datetimeClaim).total_seconds()
    return fSecondsStartedAfterClaim <= _F_RECYCLE_TOLERANCE_SECONDS


def fdatetimeReadProcessStartClockCached(iPid, dictStartClockCache):
    """Return a PID's start clock, memoizing per refresh to batch ps spawns.

    With ``dictStartClockCache`` ``None`` the probe runs on every call
    (the historical behavior). With a dict supplied, each PID's result
    is stored and reused, so repeated liveness checks across one
    registry refresh spawn ``ps`` at most once per distinct PID.
    """
    if dictStartClockCache is None:
        return fdatetimeReadProcessStartClock(iPid)
    if iPid not in dictStartClockCache:
        dictStartClockCache[iPid] = fdatetimeReadProcessStartClock(iPid)
    return dictStartClockCache[iPid]


def fdatetimeReadProcessStartClock(iPid):
    """Return a PID's start instant (UTC) from ``ps``, or None on any failure.

    ``ps -o etime=`` is POSIX and exists on macOS and Linux (``etimes``
    does not exist on macOS). The start is now minus the elapsed time,
    so no local time zone enters the answer.
    """
    if not fbIsUsablePid(iPid):
        return None
    sElapsed = _fsReadElapsedTimeFromProcessStatus(iPid)
    dElapsedSeconds = fdParseElapsedSeconds(sElapsed)
    if dElapsedSeconds is None:
        return None
    datetimeNow = datetime.datetime.now(datetime.timezone.utc)
    return datetimeNow - datetime.timedelta(seconds=dElapsedSeconds)


def fdParseElapsedSeconds(sElapsed):
    """Return seconds for a ``ps`` elapsed time ``[[dd-]hh:]mm:ss``, else None."""
    matchElapsed = re.fullmatch(
        r"(?:(?:(\d+)-)?(\d+):)?(\d+):(\d{2})", (sElapsed or "").strip(),
    )
    if matchElapsed is None:
        return None
    iDays, iHours, iMinutes, iSeconds = (
        int(sPart or 0) for sPart in matchElapsed.groups()
    )
    return float(((iDays * 24 + iHours) * 60 + iMinutes) * 60 + iSeconds)


def _fsReadElapsedTimeFromProcessStatus(iPid):
    """Return ``ps -o etime=`` output for a PID, or '' on any failure."""
    try:
        processResult = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(iPid)],
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return processResult.stdout.strip()


def ftEnumerateSessionMembers(iSessionLeader):
    """Return ``(bConclusive, listMemberPids)`` for one terminal session.

    Every live process whose SESSION or process group is the recorded
    leader — session-wide on purpose, because a shell's job control
    moves children to new process groups within its session (verified
    live: a backgrounded ``disown``ed job wears its own pgid), so a
    ``killpg``-shaped check misses exactly the strays this sweep
    exists to find. Enumeration is ``ps`` because macOS has no /proc;
    the session ids come from ``os.getsid``, because macOS
    ``ps -o sess=`` prints 0.

    This is a PROBE PRIMITIVE beside the start-clock read above, and
    deliberately NOT a journaled host launch: the journal's own
    resolver calls it while holding the journal write lock, so a
    journaled sweep deadlocks by construction — and, like the signal-0
    probes, a synchronously reaped read-only ``ps`` can neither
    mutate anything nor outlive vaibify, so there is nothing for the
    quiescence claim to lose. The sweep cannot count itself: its
    ``ps`` child lives in the CALLER's session, never the probed one.

    ZOMBIES ARE EXCLUDED, and on Linux this is load-bearing: the
    drained shell is the hub's own ``Popen`` child, which stays a
    zombie — still listed by ``ps``, still answering ``os.getsid`` —
    until the session's close path reaps it, and the drain proves
    BEFORE the close. A zombie can run nothing, mutate nothing, and
    survives exactly one ``wait``; counting it as a live member made
    every Linux drain quarantine over a corpse (found by CI — macOS
    happened not to surface it).

    ``(False, [])`` means the enumeration itself failed — no proof,
    which callers must treat as "cannot settle", never as empty.
    """
    if not fbIsUsablePid(iSessionLeader):
        return (False, [])
    try:
        processResult = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return (False, [])
    if processResult.returncode != 0:
        return (False, [])
    listMemberPids = []
    for sLine in processResult.stdout.splitlines():
        tParts = sLine.split()
        if len(tParts) < 3:
            continue
        try:
            iPid, iProcessGroup = int(tParts[0]), int(tParts[1])
        except ValueError:
            continue
        if tParts[2].startswith("Z"):
            continue
        try:
            iSessionId = os.getsid(iPid)
        except (ProcessLookupError, PermissionError):
            continue
        if iSessionId == iSessionLeader or iProcessGroup == iSessionLeader:
            listMemberPids.append(iPid)
    return (True, listMemberPids)


def fdatetimeParseClaimIso(sClaimIso):
    """Return a claim ISO string as an aware UTC datetime, or None.

    A claim without an offset was written by an older vaibify as local
    wall-clock time; it is read as local time, the only meaning it had.
    """
    if not isinstance(sClaimIso, str) or not sClaimIso:
        return None
    try:
        datetimeClaim = datetime.datetime.fromisoformat(sClaimIso)
    except ValueError:
        return None
    return datetimeClaim.astimezone(datetime.timezone.utc)
