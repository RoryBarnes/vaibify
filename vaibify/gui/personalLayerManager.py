"""Personal-instruction-layer policy: commitments and the host-hash jail.

The personal layer is instruction-stack layer 4: the researcher's
private host-side agent configuration (global instruction file,
personal skills, memory, hooks). Its declaration lives in
``dictWorkflow["dictAiProvenance"]["dictPersonalLayer"]`` (statuses in
:mod:`vaibify.reproducibility.replayGate`); this module owns the pure
policy around it — validating hash commitments down to their four
public fields, validating repo-relative included paths, and computing
a commitment from a host file inside the same home-directory jail the
project-context import uses. The host path is consumed here and
appears in nothing returned or raised: error messages carry the
basename at most.

Everything here is FastAPI-free: helpers raise ``ValueError`` with a
user-facing message and the route layer maps them to HTTP statuses.
"""

__all__ = [
    "fdictComputeHashCommitment",
    "fdictValidateHashCommitment",
    "flistValidateIncludedPaths",
]

import hashlib
import os
import posixpath
import re
from datetime import datetime, timezone


_RE_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_I_HASH_READ_CHUNK_BYTES = 1024 * 1024


def fdictValidateHashCommitment(dictCommitment):
    """Return a commitment holding ONLY the four public fields.

    The whitelist copy is the persistence guarantee: whatever else the
    request body carries — a host path included — never reaches the
    saved workflow. Raises ``ValueError`` on any malformed field.
    """
    if not isinstance(dictCommitment, dict):
        raise ValueError("dictHashCommitment must be an object.")
    sLabel = str(dictCommitment.get("sLabel") or "").strip()
    if not sLabel:
        raise ValueError("A hash commitment needs a non-empty sLabel.")
    sSha256 = str(dictCommitment.get("sSha256") or "").lower()
    if not _RE_SHA256_HEX.match(sSha256):
        raise ValueError("sSha256 must be 64 hexadecimal characters.")
    iByteCount = dictCommitment.get("iByteCount")
    if (not isinstance(iByteCount, int) or isinstance(iByteCount, bool)
            or iByteCount < 0):
        raise ValueError("iByteCount must be a non-negative integer.")
    return {
        "sLabel": sLabel,
        "sSha256": sSha256,
        "iByteCount": iByteCount,
        "sDeclaredIso": str(dictCommitment.get("sDeclaredIso") or "")
        or datetime.now(timezone.utc).isoformat(),
    }


def flistValidateIncludedPaths(listPaths):
    """Return the sanitized repo-relative path list or raise ``ValueError``.

    Mirrors the workflow-load boundary rules: repo-relative only, no
    absolute paths, no ``..`` escapes, no control characters.
    """
    if not isinstance(listPaths, list):
        raise ValueError("listIncludedPaths must be a list.")
    listClean = []
    for sPath in listPaths:
        sPath = str(sPath or "").strip()
        if not sPath or "\n" in sPath or "\x00" in sPath:
            raise ValueError(
                "Included paths must be non-empty single-line "
                "strings.",
            )
        if posixpath.isabs(sPath) or ".." in sPath.split("/"):
            raise ValueError(
                f"Included path '{sPath}' must be repo-relative "
                "with no '..' segments.",
            )
        listClean.append(sPath)
    return listClean


def _fsValidatePersonalLayerHostFile(sHostPath):
    """Return the resolved host path or raise ``ValueError``.

    Same jail as the project-context import: absolute, with a
    ``realpath`` inside the user's home directory, a regular file. No
    size cap — nothing is stored or transferred, only hashed. Error
    messages never echo the full path (the basename at most), because
    route errors are user-visible and the path must not leave this
    module.
    """
    if not sHostPath or not os.path.isabs(sHostPath):
        raise ValueError("The file path must be absolute.")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sHostPath)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
        raise ValueError(
            "The file is outside the allowed root (your home "
            "directory).",
        )
    if not os.path.isfile(sResolved):
        raise ValueError(
            "No such file: " + os.path.basename(sHostPath),
        )
    return sResolved


def fdictComputeHashCommitment(sHostPath, sLabel):
    """Hash one host file into its public commitment fields.

    Returns ``{sLabel, sSha256, iByteCount, sDeclaredIso}``. The host
    path is consumed here and appears in nothing this function
    returns: the commitment reveals content digest and length only,
    yet proves — should the researcher later release the file — that
    the released version is the one that governed the work.
    """
    sResolved = _fsValidatePersonalLayerHostFile(sHostPath)
    hasher = hashlib.sha256()
    iByteCount = 0
    with open(sResolved, "rb") as fileHandle:
        while True:
            baChunk = fileHandle.read(_I_HASH_READ_CHUNK_BYTES)
            if not baChunk:
                break
            hasher.update(baChunk)
            iByteCount += len(baChunk)
    return {
        "sLabel": sLabel,
        "sSha256": hasher.hexdigest(),
        "iByteCount": iByteCount,
        "sDeclaredIso": datetime.now(timezone.utc).isoformat(),
    }
