"""The durable record of a proof somebody chose to abandon.

A container quarantine has a way out that proves something: stop the
container, and the writer the malformed marker described is
demonstrably gone. A host project has no such lever — there is no
container to stop, and a marker too damaged to parse names no process
anyone can probe. The only remaining exit is for a human to assert that
nothing survives, which proves nothing at all.

So the exit exists, and it is recorded. An assertion that cannot be
verified can still be **attributed**: who made it, about which project,
against exactly which marker bytes, and when. That is what this module
writes, and why it writes it BEFORE the marker is unlinked — an audit
appended afterwards is one a crash can lose, leaving a project whose
proof was abandoned with nothing on disk saying so.

The file sits beside the journal it is about, in ``~/.vaibify/journal``,
which the journal contract keeps free of every sweeper in this
repository and which ``bindMountValidator`` denies to bind mounts. It is
append-only, one JSON object per line, and keyed for idempotency by the
marker's sha256: a crash between the append and the unlink re-runs to
completion instead of writing a second entry, and "an abandoned marker
with no audit record" is unreachable.
"""

__all__ = [
    "S_ABANDONMENT_SUFFIX",
    "fsAbandonmentAuditPathFor",
    "fbHasRecordedAbandonment",
    "fnRecordAbandonment",
    "flistReadAbandonments",
]

import datetime
import json
import logging
import os

from vaibify.config.operationJournal import fsJournalPathFor


logger = logging.getLogger("vaibify")

S_ABANDONMENT_SUFFIX = ".abandonments.jsonl"

_I_AUDIT_FILE_MODE = 0o600
_S_AUDIT_SCHEMA_VERSION = "1"


def fsAbandonmentAuditPathFor(sContainerName):
    """Return the abandonment audit path beside a project's journal.

    Derived from :func:`fsJournalPathFor` rather than from a second
    copy of the directory constant, so a redirected journal directory
    takes the audit with it — an audit written to the real home
    directory during a test would be exactly the host-state leak the
    journal fixture exists to prevent.
    """
    sJournalPath = fsJournalPathFor(sContainerName)
    return os.path.join(
        os.path.dirname(sJournalPath),
        f"{sContainerName}{S_ABANDONMENT_SUFFIX}",
    )


def fdictBuildAbandonmentEntry(
    sContainerName, sProjectDirectory, sMarkerSha256,
):
    """Return the audit entry for one abandonment.

    Every field answers a question a reader will have months later:
    which project (by BOTH registered name and canonical directory,
    because a name can be reused and a directory cannot), which marker
    exactly, when in UTC, and on whose authority. The principal is the
    host uid and the OS session — vaibify has no other identity to
    offer for a command run on the host lane, and claiming one it does
    not have would be worse than the truth.
    """
    return {
        "sSchemaVersion": _S_AUDIT_SCHEMA_VERSION,
        "sContainerName": sContainerName,
        "sProjectDirectory": sProjectDirectory,
        "sMarkerSha256": sMarkerSha256,
        "sAbandonedIso": datetime.datetime.now(
            datetime.timezone.utc,
        ).isoformat(),
        "iPrincipalUid": os.getuid(),
        "iPrincipalSessionId": _fiReadSessionIdQuietly(),
    }


def _fiReadSessionIdQuietly():
    """Return the caller's OS session id, or 0 where there is none."""
    try:
        return os.getsid(0)
    except OSError:
        return 0


def fnRecordAbandonment(dictEntry):
    """Append one audit entry and force it to disk before returning.

    Both the file and its directory are fsynced: without the directory
    fsync a freshly created audit file can be lost by a crash that
    keeps the unlink, which is the one ordering this record exists to
    make impossible. ``O_NOFOLLOW`` matches the journal's own hardening
    — nothing in this directory may be redirected through a link.
    """
    sPath = fsAbandonmentAuditPathFor(dictEntry["sContainerName"])
    sDirectory = os.path.dirname(sPath)
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    iDescriptor = os.open(
        sPath,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        _I_AUDIT_FILE_MODE,
    )
    try:
        os.write(
            iDescriptor,
            (json.dumps(dictEntry, sort_keys=True) + "\n").encode("utf-8"),
        )
        os.fsync(iDescriptor)
    finally:
        os.close(iDescriptor)
    _fnSyncDirectory(sDirectory)


def _fnSyncDirectory(sDirectory):
    """Force a directory entry to disk; a failure here is not fatal."""
    try:
        iDescriptor = os.open(sDirectory, os.O_RDONLY)
    except OSError as error:
        logger.debug("Could not open %s to fsync: %s", sDirectory, error)
        return
    try:
        os.fsync(iDescriptor)
    except OSError as error:
        logger.debug("Could not fsync %s: %s", sDirectory, error)
    finally:
        os.close(iDescriptor)


def fbHasRecordedAbandonment(sContainerName, sMarkerSha256):
    """Return True when this exact marker has already been abandoned.

    The idempotency key, and it is the marker HASH rather than the
    project: a project abandoned twice for two different damaged
    markers is two events and deserves two entries, while one event
    interrupted between its append and its unlink is one event and must
    not become two.
    """
    return any(
        dictEntry.get("sMarkerSha256") == sMarkerSha256
        for dictEntry in flistReadAbandonments(sContainerName)
    )


def flistReadAbandonments(sContainerName):
    """Return every parseable audit entry for a project, oldest first.

    Unparseable lines are skipped rather than raising. The direction of
    that leniency is deliberate: the cost of missing an entry is one
    duplicate append, and the cost of raising is a project that cannot
    complete an abandonment its audit file already half-records.
    """
    try:
        with open(
            fsAbandonmentAuditPathFor(sContainerName), "r",
        ) as fileAudit:
            listLines = fileAudit.readlines()
    except OSError:
        return []
    listEntries = []
    for sLine in listLines:
        try:
            jsonEntry = json.loads(sLine)
        except ValueError:
            continue
        if isinstance(jsonEntry, dict):
            listEntries.append(jsonEntry)
    return listEntries
