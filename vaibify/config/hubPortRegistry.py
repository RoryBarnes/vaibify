"""Persisted preferred port for ``vaibify`` (hub mode).

The hub is project-agnostic, so it cannot store its preferred port
inside any one project's ``vaibify.yml`` the way ``vaibify start
--gui`` does. Instead the hub keeps a single per-user file at
``~/.vaibify/hub-port.json`` recording the port chosen on first
launch. On subsequent launches the allocator prefers that port so
the browser tab the user opened from the previous run still resolves
after a Ctrl-C/restart cycle.

Persistence is best-effort: read errors return ``0`` (the
"unassigned" sentinel) and write errors are swallowed with a stderr
warning. A failure to persist must never prevent the hub from
launching — the user's primary task is to start the dashboard.
"""

__all__ = [
    "S_HUB_PORT_FILENAME",
    "fiReadPersistedHubPort",
    "fnPersistHubPort",
    "fsHubPortPath",
]

import datetime
import json
import os
import sys


S_HUB_PORT_FILENAME = "hub-port.json"
_S_VAIBIFY_DIRECTORY = os.path.expanduser("~/.vaibify")


def fsHubPortPath():
    """Return the absolute path to the hub-port persistence file."""
    return os.path.join(_S_VAIBIFY_DIRECTORY, S_HUB_PORT_FILENAME)


def fiReadPersistedHubPort():
    """Return the persisted hub port, or 0 when unavailable.

    Returns 0 for any failure mode (missing file, unreadable, invalid
    JSON, out-of-range port). The allocator interprets 0 as "no prior
    assignment" and triggers the scan-and-persist path.
    """
    sPath = fsHubPortPath()
    if not os.path.isfile(sPath):
        return 0
    try:
        with open(sPath, "r") as fileHandle:
            dictPayload = json.load(fileHandle)
    except (OSError, json.JSONDecodeError):
        return 0
    iPort = dictPayload.get("iPort", 0)
    if not isinstance(iPort, int) or isinstance(iPort, bool):
        return 0
    if not 1024 <= iPort <= 65535:
        return 0
    return iPort


def fnPersistHubPort(iPort):
    """Write iPort to the hub-port file; warn on failure but never raise."""
    if not isinstance(iPort, int) or not 1024 <= iPort <= 65535:
        return
    try:
        _fnEnsureVaibifyDirectory()
    except OSError as errorDirectory:
        _fnWarnPersistFailure(iPort, errorDirectory)
        return
    try:
        _fnAtomicWriteHubPort(iPort)
    except OSError as errorWrite:
        _fnWarnPersistFailure(iPort, errorWrite)


def _fnEnsureVaibifyDirectory():
    """Create ~/.vaibify/ with mode 0o700 if missing."""
    os.makedirs(_S_VAIBIFY_DIRECTORY, mode=0o700, exist_ok=True)


def _fnAtomicWriteHubPort(iPort):
    """Write the hub-port payload via a sibling .tmp + rename."""
    sPath = fsHubPortPath()
    sTempPath = sPath + ".tmp"
    dictPayload = {
        "iPort": iPort,
        "iPid": os.getpid(),
        "sStartedIso": datetime.datetime.now().isoformat(),
    }
    with open(sTempPath, "w") as fileHandle:
        json.dump(dictPayload, fileHandle, indent=2)
    os.replace(sTempPath, sPath)


def _fnWarnPersistFailure(iPort, errorAny):
    """Tell the user the port couldn't be persisted, but proceed."""
    print(
        f"Warning: could not persist hub port {iPort} to "
        f"{fsHubPortPath()}: {errorAny}",
        file=sys.stderr,
    )
