"""Host-global user preferences at ~/.vaibify/preferences.json.

Preferences are keyed by canonical directory identity
(``os.path.realpath`` of the project directory), never by display
name: a reused project name must not suppress a warning for a
different directory.
"""

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone

_S_PREFERENCES_DIRECTORY = os.path.expanduser("~/.vaibify")
_S_PREFERENCES_PATH = os.path.join(
    _S_PREFERENCES_DIRECTORY, "preferences.json",
)
_S_LOCK_PATH = os.path.join(
    _S_PREFERENCES_DIRECTORY, "preferences.lock",
)


def fdictLoadPreferences():
    """Read the preferences file and return its contents.

    A missing, corrupt, or wrongly-typed file reads as empty —
    preferences are a convenience record, so a damaged file must
    never block the dashboard.

    Returns
    -------
    dict
        Preferences dict with key ``dictHostWarningAcknowledged``.
    """
    if not os.path.isfile(_S_PREFERENCES_PATH):
        return {"dictHostWarningAcknowledged": {}}
    try:
        with open(_S_PREFERENCES_PATH, "r") as fileHandle:
            dictPreferences = json.load(fileHandle)
    except (json.JSONDecodeError, OSError):
        return {"dictHostWarningAcknowledged": {}}
    if not isinstance(dictPreferences, dict):
        return {"dictHostWarningAcknowledged": {}}
    if not isinstance(
        dictPreferences.get("dictHostWarningAcknowledged"), dict,
    ):
        dictPreferences["dictHostWarningAcknowledged"] = {}
    return dictPreferences


def fbHostWarningAcknowledged(sProjectDirectory):
    """Return True when the host warning was acknowledged for a directory.

    Parameters
    ----------
    sProjectDirectory : str
        Path to the project directory; resolved with
        ``os.path.realpath`` so a symlinked alias of an acknowledged
        directory reads as acknowledged.
    """
    sCanonicalDirectory = os.path.realpath(sProjectDirectory)
    dictAcknowledged = (
        fdictLoadPreferences()["dictHostWarningAcknowledged"]
    )
    return sCanonicalDirectory in dictAcknowledged


def fnRecordHostWarningAcknowledged(sProjectDirectory):
    """Record the host-warning acknowledgement for a directory.

    Stamps the canonical directory path with an ISO-8601 UTC
    timestamp recording when the researcher acknowledged.

    Parameters
    ----------
    sProjectDirectory : str
        Path to the project directory.
    """
    sCanonicalDirectory = os.path.realpath(sProjectDirectory)
    sTimestampIso = datetime.now(timezone.utc).isoformat()

    def fnStampAcknowledgement(dictPreferences):
        dictPreferences["dictHostWarningAcknowledged"][
            sCanonicalDirectory
        ] = sTimestampIso

    _fnMutatePreferencesLocked(fnStampAcknowledgement)


def _ffileOpenPreferencesLock():
    """Open and acquire an exclusive lock for preferences writes."""
    fileHandle = open(_S_LOCK_PATH, "w")
    fcntl.flock(fileHandle, fcntl.LOCK_EX)
    return fileHandle


def _fnMutatePreferencesLocked(fnMutatePreferences):
    """Run a read-modify-write of the preferences under the exclusive lock.

    The read, the mutation, and the write all happen while the lock is
    held, so two concurrent writers cannot both mutate the same stale
    snapshot and silently drop one update (the
    ``registryManager._fnMutateRegistryLocked`` pattern).
    ``fnMutatePreferences`` may raise to abandon the write; the lock is
    released either way.
    """
    os.makedirs(_S_PREFERENCES_DIRECTORY, exist_ok=True)
    with _ffileOpenPreferencesLock():
        dictPreferences = fdictLoadPreferences()
        fnMutatePreferences(dictPreferences)
        _fnWritePreferencesAtomic(dictPreferences)


def _fnWritePreferencesAtomic(dictPreferences):
    """Write preferences content to a temp file and replace."""
    sContent = json.dumps(dictPreferences, indent=2) + "\n"
    iFileDescriptor, sTempPath = tempfile.mkstemp(
        dir=_S_PREFERENCES_DIRECTORY, suffix=".tmp",
    )
    bClosed = False
    try:
        os.write(iFileDescriptor, sContent.encode("utf-8"))
        os.close(iFileDescriptor)
        bClosed = True
        os.replace(sTempPath, _S_PREFERENCES_PATH)
    except Exception:
        if not bClosed:
            os.close(iFileDescriptor)
        _fnSilentRemovePreferencesTemp(sTempPath)
        raise


def _fnSilentRemovePreferencesTemp(sPath):
    """Remove a file, ignoring errors if it does not exist."""
    try:
        os.unlink(sPath)
    except OSError:
        pass
