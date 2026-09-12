"""The origin record: which image is this project's, and where it came from.

A project containerized from the author's PINNED image did not build
``<projectName>:latest``; it obtained a base through the published
chain and may have stacked agent overlays on it. Nothing else on this
host says so -- the registry entry says only that the source was the
archive, and the image store holds a tag that anything could have
moved. This record is the single authority on which image is the
project's and where it came from, and it is the COMMIT POINT of an
acquisition: the launch guard admits a start only when a good record
exists, so a crash between the tag and the record leaves an image
nobody can start and a config recoverable by clicking again, never a
running container the config does not describe.

TWO IMAGE IDS, KEPT APART BY NAME
---------------------------------

``sBaseImageId`` is the obtained image -- what the shadow runs and what
the attestation names. ``sRunningImageId`` is the overlay result the
researcher sits in; equal to the base when no overlay was stacked. A
derived image is never reported as the pin: ``Dockerfile.node`` runs
``apt-get`` as root, so an overlay can change the base filesystem.

STALENESS
---------

A record is STALE when ``<projectName>:latest`` does not resolve to its
``sRunningImageId``, or the running image is neither the base nor
labelled ``vaibify.pinnedBaseImageId`` = the base. A stale record is
treated as absent everywhere, so a ``docker build`` outside vaibify
cannot inherit the archive's provenance and cannot start. The daemon
question -- what does the tag resolve to, and with which labels -- is
asked by the caller through the SDK gateway; this module is pure and
judges what it is handed.

LIFECYCLE
---------

Created by the acquisition's last step. Removed by exactly three
transitions: switching the project to building (which clears the
registry's image source in the same locked mutation), un-registering
the project, and a rename (the record follows the entry). Stopping the
container is NOT a lifecycle event; provenance survives stop and start.
Host-side only, mode 0700 directory and 0600 files, never in git.
"""

__all__ = [
    "S_PINNED_BASE_LABEL",
    "fdictBuildOriginRecord",
    "fdictJudgeOriginRecord",
    "fdictReadLiveOriginRecord",
    "fdictReadOriginRecord",
    "fnRemoveOriginRecord",
    "fnRenameOriginRecord",
    "fnWriteOriginRecord",
    "fsOriginsDirectory",
]

import json
import os
import re
import tempfile
from datetime import datetime, timezone


S_PINNED_BASE_LABEL = "vaibify.pinnedBaseImageId"

_S_ORIGINS_SUBDIRECTORY = "imageOrigins"
_I_DIRECTORY_MODE = 0o700
_I_FILE_MODE = 0o600
_REGEX_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def fsOriginsDirectory():
    """Return the private directory the records live in, creating it."""
    sDirectory = os.path.join(
        os.path.expanduser("~"), ".vaibify", _S_ORIGINS_SUBDIRECTORY,
    )
    os.makedirs(sDirectory, mode=_I_DIRECTORY_MODE, exist_ok=True)
    os.chmod(sDirectory, _I_DIRECTORY_MODE)
    return sDirectory


def _fsRecordPath(sProjectName):
    """Return the record's path; refuse a name that could leave the directory."""
    if not _REGEX_PROJECT_NAME.match(str(sProjectName or "")):
        raise ValueError(
            f"{sProjectName!r} is not a registry-safe project name"
        )
    return os.path.join(fsOriginsDirectory(), f"{sProjectName}.json")


def fdictBuildOriginRecord(
    sPinnedImageReference, sBaseImageId, sRunningImageId,
    listResolvedOverlays, sObtainedFrom, sZenodoService, sVersionDoi,
    sRequiredPlatform, sObtainedPlatform, bEmulated,
):
    """Return one origin record, stamped with the moment it was built."""
    return {
        "sPinnedImageReference": str(sPinnedImageReference or ""),
        "sBaseImageId": str(sBaseImageId or ""),
        "sRunningImageId": str(sRunningImageId or ""),
        "listResolvedOverlays": list(listResolvedOverlays or []),
        "sObtainedFrom": str(sObtainedFrom or ""),
        "sZenodoService": str(sZenodoService or ""),
        "sVersionDoi": str(sVersionDoi or ""),
        "sRequiredPlatform": str(sRequiredPlatform or ""),
        "sObtainedPlatform": str(sObtainedPlatform or ""),
        "bEmulated": bool(bEmulated),
        "sAcquiredIso": datetime.now(timezone.utc).isoformat(),
    }


def fnWriteOriginRecord(sProjectName, dictRecord):
    """Write the record atomically at mode 0600."""
    sPath = _fsRecordPath(sProjectName)
    iHandle, sTemporary = tempfile.mkstemp(
        dir=os.path.dirname(sPath), prefix=".origin-", suffix=".json",
    )
    try:
        with os.fdopen(iHandle, "w", encoding="utf-8") as fileHandle:
            json.dump(dictRecord, fileHandle, indent=2, sort_keys=True)
            fileHandle.write("\n")
        os.chmod(sTemporary, _I_FILE_MODE)
        os.replace(sTemporary, sPath)
    except BaseException:
        try:
            os.remove(sTemporary)
        except OSError:
            pass
        raise


def fdictReadOriginRecord(sProjectName):
    """Return the record as written, or ``None`` when there is none."""
    try:
        with open(_fsRecordPath(sProjectName), "r", encoding="utf-8") as fileHandle:
            dictRecord = json.load(fileHandle)
    except (OSError, ValueError):
        return None
    return dictRecord if isinstance(dictRecord, dict) else None


def fnRemoveOriginRecord(sProjectName):
    """Remove the record; an absent record is not an error."""
    try:
        os.remove(_fsRecordPath(sProjectName))
    except FileNotFoundError:
        return


def fnRenameOriginRecord(sOldName, sNewName):
    """Move the record with its entry; nothing to move is not an error."""
    try:
        os.replace(_fsRecordPath(sOldName), _fsRecordPath(sNewName))
    except FileNotFoundError:
        return


def fdictJudgeOriginRecord(dictRecord, dictRunningImage):
    """Return ``{bStale, sReason}`` for a record against the daemon's answer.

    ``dictRunningImage`` is what ``<projectName>:latest`` resolves to --
    ``{sId, dictLabels}`` -- or ``None`` when the tag resolves to
    nothing. The three ways a record goes stale are the three ways a
    build outside vaibify could otherwise inherit the archive's
    provenance.
    """
    if not isinstance(dictRecord, dict):
        return {"bStale": True, "sReason": "no origin record exists"}
    if not dictRunningImage:
        return {
            "bStale": True,
            "sReason": "the project's image tag resolves to no image",
        }
    sBase = str(dictRecord.get("sBaseImageId") or "")
    sRunning = str(dictRecord.get("sRunningImageId") or "")
    if str(dictRunningImage.get("sId") or "") != sRunning:
        return {
            "bStale": True,
            "sReason": (
                "the project's image tag no longer resolves to the image "
                "the acquisition recorded"
            ),
        }
    if sRunning == sBase:
        return {"bStale": False, "sReason": ""}
    dictLabels = dictRunningImage.get("dictLabels") or {}
    if str(dictLabels.get(S_PINNED_BASE_LABEL) or "") == sBase:
        return {"bStale": False, "sReason": ""}
    return {
        "bStale": True,
        "sReason": (
            "the running image is neither the obtained base nor "
            "labelled as derived from it"
        ),
    }


def fdictReadLiveOriginRecord(sProjectName, dictRunningImage):
    """Return the record only when it is present and NOT stale."""
    dictRecord = fdictReadOriginRecord(sProjectName)
    if dictRecord is None:
        return None
    if fdictJudgeOriginRecord(dictRecord, dictRunningImage)["bStale"]:
        return None
    return dictRecord
