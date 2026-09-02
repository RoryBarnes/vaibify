"""Host-global user preferences at ~/.vaibify/preferences.json.

Preferences are keyed by canonical directory identity
(``os.path.realpath`` of the project directory), never by display
name: a reused project name must not suppress a warning for a
different directory.
"""

import fcntl
import json
import math
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


_S_IDLE_TIMEOUT_KEY = "sIdleTimeoutSeconds"
_S_SESSION_CAP_KEY = "sSessionCapSeconds"
_S_SLIDING_IDLE_KEY = "sSlidingIdleSeconds"


# Tokens (case-insensitive) that select "no bound at all" in an env
# override, a stored preference, or the Settings API. There is no finite
# sentinel for never: 0 keeps its historical meaning ("as soon as the
# window is entered"), so the disabled case is carried as ``math.inf``
# and a never-window is one no finite span can ever reach. An explicit
# named choice is also the honest way to write "no expiry" down — a
# 30-day number outlives every hub process, so it would never fire while
# the dashboard still claimed a bound existed.
SET_NEVER_TOKENS = frozenset({"never", "off", "none", "disabled"})


def ffParseTimeoutSeconds(sValue):
    """Parse a timeout string to seconds; math.inf for never, None if invalid.

    The single vocabulary behind every host-global timeout: the hub idle
    timeout, the absolute session cap, and the sliding-idle window all
    accept the same strings from the same three tiers (environment
    override, stored preference, built-in default), so they cannot drift
    into meaning different things by "never".

    A never token yields ``math.inf`` (disabled). A finite value must be
    a non-negative, non-NaN number of seconds. Empty, malformed,
    negative, or NaN input returns ``None`` so the caller falls through
    to the next precedence tier rather than adopting a garbage timeout.
    """
    sNormalized = (sValue or "").strip().lower()
    if not sNormalized:
        return None
    if sNormalized in SET_NEVER_TOKENS:
        return math.inf
    try:
        fSeconds = float(sNormalized)
    except ValueError:
        return None
    if math.isnan(fSeconds) or fSeconds < 0:
        return None
    return fSeconds


def _fsTimeoutPreference(sKey):
    """Return one stored host-global timeout preference string, or empty.

    The value is a string ("never" or a number of seconds) so a single
    parser serves the env override, this preference, and the Settings
    API. A missing or non-string value reads as empty, meaning "unset" —
    the next precedence tier then applies.
    """
    jsonStored = fdictLoadPreferences().get(sKey, "")
    return jsonStored if isinstance(jsonStored, str) else ""


def _fnRecordTimeoutPreference(sKey, sValue):
    """Persist one host-global timeout preference string under its key."""
    def fnStampTimeout(dictPreferences):
        dictPreferences[sKey] = sValue

    _fnMutatePreferencesLocked(fnStampTimeout)


def fsIdleTimeoutPreference():
    """Return the stored hub idle-timeout preference, or empty."""
    return _fsTimeoutPreference(_S_IDLE_TIMEOUT_KEY)


def fnRecordIdleTimeoutPreference(sValue):
    """Persist the hub idle-timeout preference, already validated."""
    _fnRecordTimeoutPreference(_S_IDLE_TIMEOUT_KEY, sValue)


def fsSessionCapPreference():
    """Return the stored absolute session-cap preference, or empty."""
    return _fsTimeoutPreference(_S_SESSION_CAP_KEY)


def fnRecordSessionCapPreference(sValue):
    """Persist the absolute session-cap preference, already validated."""
    _fnRecordTimeoutPreference(_S_SESSION_CAP_KEY, sValue)


def fsSlidingIdlePreference():
    """Return the stored sliding-idle preference, or empty."""
    return _fsTimeoutPreference(_S_SLIDING_IDLE_KEY)


def fnRecordSlidingIdlePreference(sValue):
    """Persist the sliding-idle preference, already validated."""
    _fnRecordTimeoutPreference(_S_SLIDING_IDLE_KEY, sValue)


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
