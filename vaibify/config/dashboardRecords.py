"""How a researcher last arranged the dashboard, kept on the host.

Two scopes of record, both under ``~/.vaibify/dashboardState/<name>/``
with files at mode 600 and directories at mode 700:

- the ENVIRONMENT record (``environment.json``): the terminal panes,
  their tabs and the chat each tab held, and the Agent Council modal.
  Terminals belong to the environment, not to a project.
- one PROJECT record per project file (``projects/<digest>.json``),
  keyed by the project repository AND the project file's repo-relative
  path, because one repository can hold several project files and each
  keeps its own view.

A record describes how a human looks at the container, so it is never
written into the container-writable ``.vaibify/`` and never holds a
status, verdict or badge: :data:`DICT_FIELDS_BY_SCOPE` names the only
top-level fields a scope accepts, and anything else is refused.

Every write is a compare-and-write under the record's ``flock``
(:mod:`vaibify.config.lockedJsonRecord`): a write names the revision it
was based on and is accepted only when that is still the stored one,
which then becomes revision + 1. A refused write changes nothing and
raises :class:`RecordRevisionConflictError` carrying the stored
revision, so a page that lost the race adopts it and never retries the
stale snapshot.

The environment's directory is one of the host files its NAME owns
(:mod:`vaibify.gui.environmentResources`), so a rename moves it with
everything else and a deletion removes it.
"""

__all__ = [
    "DICT_FIELDS_BY_SCOPE",
    "I_MAXIMUM_RECORD_BYTES",
    "RecordContentError",
    "RecordRevisionConflictError",
    "S_SCOPE_ENVIRONMENT",
    "S_SCOPE_PROJECT",
    "fbRemoveEnvironmentRecords",
    "fbRemoveProjectRecord",
    "fdictReadRecord",
    "fdictWriteRecordIfCurrent",
    "fsDashboardStateRoot",
    "fsEnvironmentDirectoryFor",
    "fsEnvironmentRecordPath",
    "fsProjectRecordPath",
]

import hashlib
import json
import os
import posixpath
import shutil
from datetime import datetime, timezone

from vaibify.config.durableFile import fnWriteFileDurably
from vaibify.config.lockedJsonRecord import fcontextHoldRecordLock
from vaibify.config.pidFileRegistry import fbIsSafeRegistryName

S_SCOPE_ENVIRONMENT = "environment"
S_SCOPE_PROJECT = "project"
I_SCHEMA_VERSION = 1
I_MAXIMUM_RECORD_BYTES = 256 * 1024
I_MAXIMUM_PATH_LENGTH = 4096
_I_DIRECTORY_MODE = 0o700
_S_ENVIRONMENT_RECORD_NAME = "environment.json"
_S_PROJECTS_DIRECTORY_NAME = "projects"

DICT_FIELDS_BY_SCOPE = {
    S_SCOPE_ENVIRONMENT: frozenset({"dictTerminals", "dictAgentCouncil"}),
    S_SCOPE_PROJECT: frozenset({
        "sLeftTab", "sSelectedStepId", "dictExpanded", "dictSplitters",
        "dictViewers", "sFilesDirectory",
    }),
}


class RecordRevisionConflictError(Exception):
    """A write was based on a revision that is no longer the stored one."""

    def __init__(self, iStoredRevision):
        super().__init__(
            f"the stored layout is at revision {iStoredRevision}; this "
            "write was based on an older one and was not saved"
        )
        self.iStoredRevision = iStoredRevision


class RecordContentError(ValueError):
    """A record's content is not something a layout record may hold."""


def fsDashboardStateRoot():
    """Return ``~/.vaibify/dashboardState``, resolved when asked."""
    return os.path.join(
        os.path.expanduser("~"), ".vaibify", "dashboardState",
    )


def fsEnvironmentDirectoryFor(sEnvironmentName):
    """Return the directory one environment's records live in.

    Raises ``ValueError`` for a name that cannot be a path component,
    which is also how :mod:`vaibify.gui.environmentResources` learns
    that no such directory can exist for it.
    """
    if not fbIsSafeRegistryName(sEnvironmentName):
        raise ValueError(
            f"not an environment name: {sEnvironmentName!r}"
        )
    return os.path.join(fsDashboardStateRoot(), sEnvironmentName)


def fsEnvironmentRecordPath(sEnvironmentName):
    """Return the environment record's file path."""
    return os.path.join(
        fsEnvironmentDirectoryFor(sEnvironmentName),
        _S_ENVIRONMENT_RECORD_NAME,
    )


def _fnValidateContainerPath(sLabel, sPath):
    if (not isinstance(sPath, str) or not sPath.startswith("/")
            or len(sPath) > I_MAXIMUM_PATH_LENGTH or "\x00" in sPath
            or posixpath.normpath(sPath) != sPath):
        raise RecordContentError(f"{sLabel} is not a normalized absolute path")


def fsProjectRecordPath(sEnvironmentName, sProjectRepoPath, sWorkflowKey):
    """Return one project file's record path.

    The file name is a digest of the repository and the project file's
    repo-relative key, so no caller-supplied text becomes a path
    component. An empty key means the project file cannot be
    attributed, and nothing may be saved for it.
    """
    _fnValidateContainerPath("the project repository", sProjectRepoPath)
    if (not isinstance(sWorkflowKey, str) or not sWorkflowKey
            or sWorkflowKey.startswith("/") or ".." in sWorkflowKey.split("/")):
        raise RecordContentError("the project file cannot be attributed")
    sDigest = hashlib.sha256(
        json.dumps([sProjectRepoPath, sWorkflowKey]).encode("utf-8"),
    ).hexdigest()[:40]
    return os.path.join(
        fsEnvironmentDirectoryFor(sEnvironmentName),
        _S_PROJECTS_DIRECTORY_NAME, sDigest + ".json",
    )


def _fdictEmptyRecord():
    return {"iRevision": 0, "dictState": {}, "bUnreadable": False}


def fdictReadRecord(sPath):
    """Return ``{iRevision, dictState, bUnreadable}`` for one record.

    An absent record is revision 0 with nothing in it. A record that
    cannot be parsed is reported as ``bUnreadable`` with revision 0, so
    the page says the saved layout was lost instead of restoring part
    of it, and the next save replaces it.
    """
    try:
        with open(sPath, "rb") as fileRecord:
            baContent = fileRecord.read(I_MAXIMUM_RECORD_BYTES * 2)
    except FileNotFoundError:
        return _fdictEmptyRecord()
    try:
        dictRecord = json.loads(baContent.decode("utf-8"))
        iRevision = dictRecord["iRevision"]
        dictState = dictRecord["dictState"]
        if not isinstance(iRevision, int) or not isinstance(dictState, dict):
            raise ValueError("wrong field types")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        dictEmpty = _fdictEmptyRecord()
        dictEmpty["bUnreadable"] = True
        return dictEmpty
    return {"iRevision": iRevision, "dictState": dictState,
            "bUnreadable": False}


def _fbaEncodeState(sScope, dictState):
    """Return the state's JSON bytes, or raise naming what is refused."""
    if not isinstance(dictState, dict):
        raise RecordContentError("a layout record holds an object")
    setUnknown = set(dictState) - DICT_FIELDS_BY_SCOPE[sScope]
    if setUnknown:
        raise RecordContentError(
            "a layout record does not hold " + ", ".join(sorted(setUnknown))
        )
    baState = json.dumps(dictState, sort_keys=True).encode("utf-8")
    if len(baState) > I_MAXIMUM_RECORD_BYTES:
        raise RecordContentError(
            f"the layout is {len(baState)} bytes; at most "
            f"{I_MAXIMUM_RECORD_BYTES} are kept"
        )
    return baState


def _fnEnsurePrivateDirectories(sPath):
    sRoot = fsDashboardStateRoot()
    sDirectory = os.path.dirname(sPath)
    os.makedirs(sDirectory, mode=_I_DIRECTORY_MODE, exist_ok=True)
    while sDirectory.startswith(sRoot):
        os.chmod(sDirectory, _I_DIRECTORY_MODE)
        if sDirectory == sRoot:
            break
        sDirectory = os.path.dirname(sDirectory)


def fdictWriteRecordIfCurrent(
    sPath, sScope, iBaseRevision, dictState, dictIdentity,
):
    """Store ``dictState`` if ``iBaseRevision`` is the stored revision.

    Returns ``{"iRevision": n}``, the new revision. The comparison and
    the write happen under one lock, so two writers based on the same
    revision cannot both succeed.
    """
    if not isinstance(iBaseRevision, int) or iBaseRevision < 0:
        raise RecordContentError("the base revision is not a count")
    baState = _fbaEncodeState(sScope, dictState)
    _fnEnsurePrivateDirectories(sPath)
    with fcontextHoldRecordLock(sPath + ".lock"):
        iStoredRevision = fdictReadRecord(sPath)["iRevision"]
        if iBaseRevision != iStoredRevision:
            raise RecordRevisionConflictError(iStoredRevision)
        dictRecord = {
            "iSchemaVersion": I_SCHEMA_VERSION,
            "iRevision": iStoredRevision + 1,
            "sScope": sScope,
            "dictIdentity": dictIdentity,
            "sSavedAtUtc": datetime.now(timezone.utc).isoformat(),
            "dictState": json.loads(baState.decode("utf-8")),
        }
        fnWriteFileDurably(
            sPath, json.dumps(dictRecord, sort_keys=True).encode("utf-8"),
            iMode=0o600,
        )
    return {"iRevision": iStoredRevision + 1}


def fbRemoveProjectRecord(sEnvironmentName, sProjectRepoPath, sWorkflowKey):
    """Delete one project file's record; True when one was removed.

    A sibling project file in the same repository keeps its own record.
    """
    sPath = fsProjectRecordPath(
        sEnvironmentName, sProjectRepoPath, sWorkflowKey,
    )
    try:
        os.unlink(sPath)
    except FileNotFoundError:
        return False
    return True


def fbRemoveEnvironmentRecords(sEnvironmentName):
    """Delete every record an environment holds; True when any existed."""
    try:
        sDirectory = fsEnvironmentDirectoryFor(sEnvironmentName)
    except ValueError:
        return False
    if os.path.islink(sDirectory):
        os.unlink(sDirectory)
        return True
    if not os.path.isdir(sDirectory):
        return False
    shutil.rmtree(sDirectory)
    return True
