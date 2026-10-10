"""Prevent macOS sleep while specific containers are running.

This is the canonical home for the keep-alive (``caffeinate``) PID
registry; ``vaibify/docker/keepAliveManager.py`` remains as a thin
re-export shim. Like ``containerLock`` and ``sessionRegistry`` it is a
schema-divergent view of the shared ``pidFileRegistry`` mechanism: its
directory creation and pid-file IO route through that module, so the
caffeinate directory is created at ``0o700`` and its file is opened
``O_NOFOLLOW`` exactly like every other host registry. The recycle-proof
kill (start-clock gated ``SIGTERM``) and the legacy bare-int payload
support stay here because they are this registry's own divergent schema.
"""

import fcntl
import os
import signal
import subprocess
import sys
import json
import time

from vaibify.config import pidFileRegistry
from vaibify.config.processLiveness import (
    fbIsProcessAlive, fbIsProcessAliveSince,
    fbStartClockIsConsistentWithClaim, fdictEnumerateStartClocks,
    flistEnumerateProcessesNamed, fsNowClaimIso,
)


_S_PID_DIRECTORY = os.path.expanduser("~/.vaibify/caffeinate")

# Every caffeinate this host's vaibify ever launched, by pid, with the
# registry name it served and the instant the spawn returned. A pid
# file names the keep-alive a registry CURRENTLY holds; the ledger is
# what lets vaibify PROVE that a caffeinate no registry holds is one it
# launched (pid plus start clock) rather than the researcher's own. Not
# a *.pid file, so flistKeepAliveNames never lists it.
_S_SPAWN_LEDGER_NAME = "spawnLedger.json"
_I_SPAWN_LEDGER_CAP = 512
S_KEEP_ALIVE_COMMAND = "caffeinate"

# How long a removal waits for a signalled caffeinate to leave the
# process table before answering "still running".
_F_EXIT_CONFIRM_SECONDS = 2.0
_F_EXIT_POLL_SECONDS = 0.05


def fnStartKeepAlive(sContainerName):
    """Spawn a caffeinate process tied to the given container.

    Parameters
    ----------
    sContainerName : str
        The Docker container name used to locate the PID file.
    """
    if not fbPlatformSupportsKeepAlive():
        return
    fnStopKeepAlive(sContainerName)
    pidFileRegistry.fnEnsureDirectory(_S_PID_DIRECTORY)
    iPid = _fiSpawnCaffeinate()
    if iPid:
        sStartedIso = fsNowClaimIso()
        _fnWritePidFile(sContainerName, iPid, sStartedIso)
        _fnRecordSpawnInLedger(sContainerName, iPid, sStartedIso)


def _fiSpawnCaffeinate():
    """Launch 'caffeinate -s' in the background and return its pid."""
    try:
        processResult = subprocess.Popen(
            ["caffeinate", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return processResult.pid
    except FileNotFoundError:
        return 0


def _fnWritePidFile(sContainerName, iPid, sStartedIso=None):
    """Record the caffeinate pid and its claim time for a container."""
    sPath = _fsPidFilePath(sContainerName)
    dictPayload = {
        "iPid": iPid,
        "sStartedIso": sStartedIso or fsNowClaimIso(),
    }
    with pidFileRegistry.ffileOpenNoFollow(sPath) as fileHandle:
        pidFileRegistry.fnWritePayload(fileHandle, dictPayload)


def fbPlatformSupportsKeepAlive():
    """Return True where a keep-alive can actually be held.

    ``caffeinate`` is macOS-only, so on every other platform
    :func:`fnStartKeepAlive` silently declines. A caller that asserts
    keep-alives on a timer must ask this first — otherwise it announces
    "holding the machine awake" once per tick for a process that was
    never spawned, which is the dashboard misreporting its own state.
    """
    return sys.platform == "darwin"


def fbKeepAliveIsLive(sContainerName):
    """Return True when this name's caffeinate is recorded and still alive.

    The idempotence predicate. :func:`fnStartKeepAlive` deliberately
    kills and respawns, so a caller that re-asserts a keep-alive on a
    timer would churn a process every tick; it asks this first and
    starts only when the answer is False. The same start-clock gate the
    kill uses answers it, so a recycled PID reads as not live.
    """
    sPath = _fsPidFilePath(sContainerName)
    if not os.path.isfile(sPath):
        return False
    dictPayload = _fdictReadPidPayload(sPath)
    iPid = dictPayload.get("iPid", 0)
    if not iPid:
        return False
    return fbIsProcessAliveSince(iPid, dictPayload.get("sStartedIso"))


def flistKeepAliveNames():
    """Return every name this host currently records a keep-alive for.

    Reads the registry directory rather than any in-process map, so a
    hub that restarted still finds the keep-alives an earlier hub
    started — the caffeinate outlives its hub (it is spawned into its
    own session), so a registry nobody re-reads is how one leaks.
    """
    try:
        listEntries = os.listdir(_S_PID_DIRECTORY)
    except OSError:
        return []
    return [
        sEntry[: -len(".pid")] for sEntry in listEntries
        if sEntry.endswith(".pid")
    ]


def fnStopKeepAlive(sContainerName):
    """Kill the caffeinate process associated with a container."""
    sPath = _fsPidFilePath(sContainerName)
    if not os.path.isfile(sPath):
        return
    dictPayload = _fdictReadPidPayload(sPath)
    iPid = dictPayload.get("iPid", 0)
    if iPid:
        _fnKillIfRunning(iPid, dictPayload.get("sStartedIso"))
    _fnRemovePidFile(sPath)


def _fdictReadPidPayload(sPath):
    """Read the caffeinate pid payload, tolerating a legacy bare int.

    New pid files hold JSON with the pid and its claim time; older
    files held a single integer. Both map to a payload dict so the
    recycled-PID guard applies uniformly. Returns {} on any error.
    """
    try:
        with open(sPath, "r", encoding="utf-8") as fileHandle:
            sContent = fileHandle.read().strip()
    except OSError:
        return {}
    return _fdictParsePidContent(sContent)


def _fdictParsePidContent(sContent):
    """Map pid-file text (JSON or a legacy bare int) to a payload dict."""
    if not sContent:
        return {}
    try:
        objParsed = json.loads(sContent)
    except json.JSONDecodeError:
        return {}
    if isinstance(objParsed, dict):
        return objParsed
    if isinstance(objParsed, int) and not isinstance(objParsed, bool):
        return {"iPid": objParsed}
    return {}


def _fnKillIfRunning(iPid, sStartedIso):
    """SIGTERM a process only when its start time matches the claim.

    The start-time gate keeps a recycled PID — one the kernel reused
    after caffeinate exited — from being killed. A legacy payload with
    no claim time falls back to the bare PID-existence check.
    """
    if not fbIsProcessAliveSince(iPid, sStartedIso):
        return
    try:
        os.kill(iPid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _fnRemovePidFile(sPath):
    """Remove a PID file, ignoring errors."""
    pidFileRegistry.fnUnlinkQuietly(sPath)


def _fsPidFilePath(sContainerName):
    """Return the PID file path for a container."""
    return os.path.join(_S_PID_DIRECTORY, f"{sContainerName}.pid")


def _fsSpawnLedgerPath():
    """Return the ledger's path inside the keep-alive registry directory."""
    return os.path.join(_S_PID_DIRECTORY, _S_SPAWN_LEDGER_NAME)


def _fnRecordSpawnInLedger(sContainerName, iPid, sStartedIso):
    """Append one spawn to the ledger under its flock, pruning the dead."""
    pidFileRegistry.fnEnsureDirectory(_S_PID_DIRECTORY)
    with pidFileRegistry.ffileOpenNoFollow(_fsSpawnLedgerPath()) as fileHandle:
        fcntl.flock(fileHandle, fcntl.LOCK_EX)
        dictLedger = _fdictPruneLedger(
            pidFileRegistry.fdictReadPayloadFromHandle(fileHandle),
            fdictEnumerateStartClocks(),
        )
        dictLedger[str(iPid)] = {
            "sName": sContainerName, "sStartedIso": sStartedIso,
        }
        pidFileRegistry.fnWritePayload(
            fileHandle, _fdictCapLedger(dictLedger),
        )


def _fdictPruneLedger(dictLedger, dictStartClocks):
    """Keep only the entries whose process is still the one recorded.

    ``dictStartClocks`` is one ``ps`` listing of every pid's start
    clock, taken once per prune, so a ledger of any size costs one
    spawn; ``None`` (the listing failed) falls back to the spawn-free
    existence check, which keeps rather than kills.
    """
    return {
        sPid: dictEntry for sPid, dictEntry in dictLedger.items()
        if _fbLedgerEntryIsWellFormed(sPid, dictEntry)
        and _fbLedgerEntryStillRuns(int(sPid), dictEntry["sStartedIso"], dictStartClocks)
    }


def _fbLedgerEntryStillRuns(iPid, sStartedIso, dictStartClocks):
    """Return True unless the pid is gone or was recycled after the spawn."""
    if dictStartClocks is None:
        return fbIsProcessAlive(iPid)
    if iPid not in dictStartClocks:
        return False
    return fbStartClockIsConsistentWithClaim(dictStartClocks[iPid], sStartedIso)


def _fbLedgerEntryIsWellFormed(sPid, dictEntry):
    """Return True for a ``{sName, sStartedIso}`` entry under a pid key."""
    return (
        sPid.isdigit() and isinstance(dictEntry, dict)
        and isinstance(dictEntry.get("sName"), str)
        and isinstance(dictEntry.get("sStartedIso"), str)
    )


def _fdictCapLedger(dictLedger):
    """Bound the ledger as a backstop: drop the oldest beyond the cap."""
    if len(dictLedger) <= _I_SPAWN_LEDGER_CAP:
        return dictLedger
    listOrdered = sorted(
        dictLedger.items(), key=lambda tItem: tItem[1]["sStartedIso"],
    )
    return dict(listOrdered[-_I_SPAWN_LEDGER_CAP:])


def fdictReadSpawnLedger():
    """Return ``{iPid: {sName, sStartedIso}}`` for every live ledgered spawn.

    Pruned on read, never written here: a scan must not take the
    ledger's write lock to answer a question.
    """
    dictLedger = _fdictPruneLedger(
        pidFileRegistry.fdictReadPayload(_fsSpawnLedgerPath()),
        fdictEnumerateStartClocks(),
    )
    return {int(sPid): dictEntry for sPid, dictEntry in dictLedger.items()}


def fdictEnumerateKeepAlivesByPid():
    """Return ``{iPid: row}`` for this user's live caffeinates, or None."""
    listRows = flistEnumerateProcessesNamed(S_KEEP_ALIVE_COMMAND)
    if listRows is None:
        return None
    return {dictRow["iPid"]: dictRow for dictRow in listRows}


def fbCaffeinateIsProvablyOurs(iPid, dictLedger=None, dictRunningByPid=None):
    """Return True only for a ledgered pid that is still that caffeinate.

    The kill rule for any later removal: the pid is in the ledger, the
    host lists it NOW as a ``caffeinate`` with a readable start clock,
    and that clock is consistent with the ledger's spawn instant. An
    unreadable clock is enough to leave a process alone and not enough
    to kill it. A caller judging many pids passes the ledger and the
    enumeration it already holds, so the answer costs no spawn.
    """
    if dictLedger is None:
        dictLedger = fdictReadSpawnLedger()
    if dictRunningByPid is None:
        dictRunningByPid = fdictEnumerateKeepAlivesByPid() or {}
    dictEntry = dictLedger.get(iPid)
    dictProcess = dictRunningByPid.get(iPid)
    if dictEntry is None or dictProcess is None or dictProcess["datetimeStart"] is None:
        return False
    return fbStartClockIsConsistentWithClaim(
        dictProcess["datetimeStart"], dictEntry["sStartedIso"],
    )


def fbStopProvablyOursKeepAlive(iPid):
    """SIGTERM a ledgered caffeinate no registry holds; refuse any other.

    Returns True once the process has left the process table, False
    when it was signalled and is still listed when the wait ends.
    """
    dictLedger = fdictReadSpawnLedger()
    if not fbCaffeinateIsProvablyOurs(iPid, dictLedger=dictLedger):
        raise ValueError(
            f"pid {iPid} is not a caffeinate this vaibify launched; it is "
            "left alone"
        )
    _fnKillIfRunning(iPid, dictLedger[iPid]["sStartedIso"])
    return _fbAwaitProcessExit(iPid)


def _fbAwaitProcessExit(iPid):
    """Return True once the pid is gone, False when the wait runs out."""
    fDeadline = time.monotonic() + _F_EXIT_CONFIRM_SECONDS
    while fbIsProcessAlive(iPid):
        if time.monotonic() >= fDeadline:
            return False
        time.sleep(_F_EXIT_POLL_SECONDS)
    return True


def fdictReadKeepAliveRecord(sContainerName):
    """Return a registry name's ``{iPid, sStartedIso}`` record, or {}."""
    return _fdictReadPidPayload(_fsPidFilePath(sContainerName))


def fbStopKeepAliveProcess(iPid, sStartedIso):
    """SIGTERM one caffeinate the researcher chose, start-clock gated.

    The exit for a keep-alive the scanner listed as "possibly" ours:
    not in the ledger, so nothing proves vaibify launched it, which is
    why the researcher confirms it. The gate is the one every kill in
    this module uses: a pid whose start clock is later than the
    instant recorded is a recycled pid, refused with ``ValueError``
    rather than silently skipped. Returns True once the process has
    left the process table, False when it is still listed after the
    wait, so the caller never reports an ending it did not see.
    """
    if not fbIsProcessAliveSince(iPid, sStartedIso):
        raise ValueError(
            f"pid {iPid} is already gone, or has been replaced since it "
            "was listed"
        )
    _fnKillIfRunning(iPid, sStartedIso)
    return _fbAwaitProcessExit(iPid)
