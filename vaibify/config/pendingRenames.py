"""The durable journal of an environment rename that has not finished.

Renaming an environment moves every resource its name keys -- the
volumes (by copying), the image tags, the ``vaibify.yml`` name, the
host files under ``~/.vaibify`` -- and ends in ONE atomic registry
write that switches the entry to the new name. A hub can die anywhere
in between, so before the first change the rename writes this journal,
at ``~/.vaibify/pendingRenames/<oldName>.json``: a transaction id, both
names, the ORIGINAL bytes of ``vaibify.yml`` and of the registry entry,
and the identity of every Docker object that will move. Recovery reads
it to roll the rename back to exactly what it found, removing only this
transaction's artifacts, or forward once the registry switch landed.

Two things recovery never trusts, by design. The advisory ``sStage``
is for display only: whether a rename COMMITTED is decided by the
registry alone. And which host files have moved is read from the
filesystem, never from a mark -- the journal records only the INTENT
to move each one, written durably before the move.

Every write is durable (:mod:`vaibify.config.durableFile`), because a
journal that survives a power cut only as far as the page cache would
promise a recovery it cannot deliver.
"""

__all__ = [
    "S_RENAME_TRANSACTION_LABEL",
    "fdictBuildRenameJournal",
    "fdictReadRenameJournal",
    "flistReadRenameJournals",
    "fnAppendHostFileIntent",
    "fnRecordStage",
    "fnRemoveRenameJournal",
    "fnWriteRenameJournal",
    "fsPendingRenamesDirectory",
]

import base64
import json
import os
import re
import secrets
from datetime import datetime, timezone

from vaibify.config.durableFile import fnSyncDirectory, fnWriteFileDurably


S_SCHEMA_VERSION = "1"
# The label every volume a rename creates carries, so a rollback removes
# exactly this transaction's volumes and never one it did not make.
S_RENAME_TRANSACTION_LABEL = "vaibify-rename-transaction"
_S_PENDING_RENAMES_DIRECTORY = os.path.join(
    os.path.expanduser("~"), ".vaibify", "pendingRenames",
)
_REGEX_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_S_JOURNAL_SUFFIX = ".json"


def fsPendingRenamesDirectory():
    """Create (0700) and return the directory the journals live in."""
    os.makedirs(_S_PENDING_RENAMES_DIRECTORY, mode=0o700, exist_ok=True)
    return _S_PENDING_RENAMES_DIRECTORY


def _fsJournalPath(sOldName):
    if not _REGEX_SAFE_NAME.match(str(sOldName or "")):
        raise ValueError(f"{sOldName!r} is not a registry-safe project name")
    return os.path.join(
        fsPendingRenamesDirectory(), sOldName + _S_JOURNAL_SUFFIX)


def fdictBuildRenameJournal(
    sOldName, sNewName, sConfigPath, baConfig, sRewrittenSha256,
    dictRegistryEntry, dictDocker,
):
    """Return a fresh journal; ``dictDocker`` holds every Docker identity.

    ``dictDocker`` is ``{"sContainerId", "sImageId", "listVolumes",
    "listTags"}``: the old container (or ``""``), the image the copier
    runs, each volume pair as ``{sFrom, sTo, sCreatedAt}`` and each tag
    as ``{sTag, sImageId}``.
    """
    return {
        "sSchemaVersion": S_SCHEMA_VERSION,
        "sTransactionId": secrets.token_hex(16),
        "sOldName": str(sOldName),
        "sNewName": str(sNewName),
        "sConfigPath": str(sConfigPath),
        "sConfigBase64": base64.b64encode(baConfig).decode("ascii"),
        "sRewrittenConfigSha256": sRewrittenSha256,
        "dictRegistryEntry": dict(dictRegistryEntry),
        "sContainerId": str(dictDocker.get("sContainerId") or ""),
        "sImageId": str(dictDocker.get("sImageId") or ""),
        "listVolumes": list(dictDocker.get("listVolumes") or []),
        "listTags": list(dictDocker.get("listTags") or []),
        "listHostFileIntents": [],
        "sStage": "journaled",
        "sCreatedIso": datetime.now(timezone.utc).isoformat(),
    }


def fnWriteRenameJournal(dictJournal):
    """Write the journal durably, replacing any earlier version of it."""
    sPath = _fsJournalPath(dictJournal["sOldName"])
    baContent = (json.dumps(dictJournal, indent=2) + "\n").encode("utf-8")
    fnWriteFileDurably(sPath, baContent, iMode=0o600)


def fnRecordStage(dictJournal, sStage):
    """Record the advisory stage, for display; recovery never reads it."""
    dictJournal["sStage"] = sStage
    fnWriteRenameJournal(dictJournal)


def fnAppendHostFileIntent(dictJournal, sFrom, sTo):
    """Record, durably, the intent to move one host file BEFORE moving it."""
    dictJournal["listHostFileIntents"].append({"sFrom": sFrom, "sTo": sTo})
    fnWriteRenameJournal(dictJournal)


def fdictReadRenameJournal(sOldName):
    """Return the journal for a rename of ``sOldName``, or None."""
    try:
        with open(_fsJournalPath(sOldName), "r", encoding="utf-8") as fileJournal:
            return json.load(fileJournal)
    except FileNotFoundError:
        return None


def flistReadRenameJournals():
    """Return every journal on this machine; an unreadable one is skipped."""
    listJournals = []
    for sEntry in sorted(os.listdir(fsPendingRenamesDirectory())):
        if not sEntry.endswith(_S_JOURNAL_SUFFIX):
            continue
        try:
            dictJournal = fdictReadRenameJournal(
                sEntry[:-len(_S_JOURNAL_SUFFIX)])
        except (ValueError, OSError):
            continue
        if dictJournal is not None:
            listJournals.append(dictJournal)
    return listJournals


def fnRemoveRenameJournal(sOldName):
    """Delete the journal durably; an absent one is already removed."""
    sPath = _fsJournalPath(sOldName)
    try:
        os.unlink(sPath)
    except FileNotFoundError:
        return
    fnSyncDirectory(os.path.dirname(sPath))
