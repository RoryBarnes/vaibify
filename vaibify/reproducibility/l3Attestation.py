"""Persist and validate AICS L3 reproduction attestations.

The L3 gate is the only AICS rung gated on a *recorded* outcome of
an expensive operation (rebuild + hash compare). The persisted
record lives at ``<projectRepo>/.vaibify/l3_attestation.json``; every
attempt is also archived to
``<projectRepo>/.vaibify/l3_attestations/<timestamp>.json`` so the
researcher can audit history without losing the most-recent record.

The attestation is keyed against the live MANIFEST digest: if
``MANIFEST.sha256`` changes after attestation, the gate falls back
to L2 and the stale-attestation banner surfaces in the dashboard.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


__all__ = [
    "I_SCHEMA_VERSION",
    "S_ATTESTATION_FILENAME",
    "S_ATTESTATION_HISTORY_DIR",
    "S_STATUS_PASSED",
    "S_STATUS_FAILED",
    "fbL3AttestationCurrent",
    "fdictBuildAttestation",
    "fdictReadAttestation",
    "flistReadAttestationHistory",
    "fnInvalidateAttestation",
    "fnWriteAttestation",
    "fsCurrentManifestDigest",
]


I_SCHEMA_VERSION = 1
S_ATTESTATION_FILENAME = "l3_attestation.json"
S_ATTESTATION_HISTORY_DIR = "l3_attestations"
S_STATUS_PASSED = "passed"
S_STATUS_FAILED = "failed"
_S_MANIFEST_FILENAME = "MANIFEST.sha256"


def fsCurrentManifestDigest(sProjectRepo):
    """Return the SHA-256 of ``MANIFEST.sha256`` or the empty string.

    The digest is the byte-exact identity of the L3 envelope: when
    it changes (re-run, new step, edited standards) the L3
    attestation is considered stale. Returns ``""`` when the manifest
    is absent so callers can short-circuit cleanly.
    """
    pathManifest = Path(sProjectRepo) / _S_MANIFEST_FILENAME
    if not pathManifest.is_file():
        return ""
    from vaibify.reproducibility._hashing import fsHashFileSha256
    return "sha256:" + fsHashFileSha256(str(pathManifest))


def fdictReadAttestation(sProjectRepo):
    """Return the parsed top-level attestation file or ``None``.

    Missing file, malformed JSON, and non-dict payload all map to
    ``None`` so the L3 gate (and the dashboard banner) treat them
    uniformly as "no attestation on file".
    """
    pathFile = _fpathAttestationFile(sProjectRepo)
    if not pathFile.is_file():
        return None
    try:
        with open(pathFile, "r", encoding="utf-8") as fileHandle:
            dictPayload = json.load(fileHandle)
    except (OSError, ValueError):
        return None
    if not isinstance(dictPayload, dict):
        return None
    return dictPayload


def fbL3AttestationCurrent(sProjectRepo):
    """Return True iff an L3 attestation exists, passed, and is not stale.

    Staleness is keyed against ``fsCurrentManifestDigest`` so any
    workflow edit that re-generates the manifest invalidates the
    attestation without requiring a separate timestamp.
    """
    dictPayload = fdictReadAttestation(sProjectRepo)
    if dictPayload is None:
        return False
    if dictPayload.get("sStatus") != S_STATUS_PASSED:
        return False
    sRecorded = dictPayload.get("sManifestDigestAtAttestation") or ""
    if not sRecorded:
        return False
    return sRecorded == fsCurrentManifestDigest(sProjectRepo)


def fdictBuildAttestation(
    sStatus, sManifestDigest, sImageDigest,
    fDurationSeconds, iOutputHashesMatched, iOutputHashesTotal,
    listDivergedHashes=None, sRunLogPath="",
):
    """Return a fully-populated attestation dict (no file IO).

    The shape is fixed by the schema documented in the AICS plan;
    extracting it as a pure builder keeps the writer thin and lets
    tests assert payload contents without round-tripping through
    disk.
    """
    return {
        "iSchemaVersion": I_SCHEMA_VERSION,
        "sStatus": sStatus,
        "sManifestDigestAtAttestation": sManifestDigest,
        "sImageDigest": sImageDigest or "",
        "sAttestedAtUtc": _fsCurrentTimestamp(),
        "fDurationSeconds": float(fDurationSeconds),
        "iOutputHashesMatched": int(iOutputHashesMatched),
        "iOutputHashesTotal": int(iOutputHashesTotal),
        "listDivergedHashes": list(listDivergedHashes or []),
        "sRunLogPath": sRunLogPath,
    }


def fnWriteAttestation(sProjectRepo, dictAttestation):
    """Persist the attestation and archive a timestamped copy.

    The atomic write uses a sibling ``.tmp`` file followed by
    ``os.replace`` so a crash during the write cannot leave a
    half-written attestation that would be silently treated as
    "passed". A copy is also written to the history directory; the
    history filename embeds the attestation timestamp for sortability.
    """
    pathDir = _fpathVaibifyDir(sProjectRepo)
    pathDir.mkdir(parents=True, exist_ok=True)
    pathHistoryDir = pathDir / S_ATTESTATION_HISTORY_DIR
    pathHistoryDir.mkdir(parents=True, exist_ok=True)
    _fnAtomicWriteJson(
        _fpathAttestationFile(sProjectRepo), dictAttestation,
    )
    sHistoryName = _fsHistoryFilenameFor(dictAttestation)
    _fnAtomicWriteJson(
        pathHistoryDir / sHistoryName, dictAttestation,
    )


def fnInvalidateAttestation(sProjectRepo):
    """Remove the top-level attestation file (history is preserved).

    Used by the dashboard when a researcher explicitly clears an
    attestation. Returns True iff a file was actually removed.
    """
    pathFile = _fpathAttestationFile(sProjectRepo)
    if not pathFile.is_file():
        return False
    try:
        os.remove(str(pathFile))
    except OSError:
        return False
    return True


def flistReadAttestationHistory(sProjectRepo):
    """Return all archived attestations newest-first.

    Returns an empty list when the history directory is absent or
    every file fails to parse. Each entry is the parsed JSON dict;
    callers render the columns the UI needs (timestamp, status,
    manifest digest, duration).
    """
    pathDir = _fpathVaibifyDir(sProjectRepo) / S_ATTESTATION_HISTORY_DIR
    if not pathDir.is_dir():
        return []
    listEntries = []
    for pathFile in sorted(pathDir.glob("*.json"), reverse=True):
        try:
            with open(pathFile, "r", encoding="utf-8") as fileHandle:
                dictPayload = json.load(fileHandle)
        except (OSError, ValueError):
            continue
        if isinstance(dictPayload, dict):
            listEntries.append(dictPayload)
    return listEntries


def _fnAtomicWriteJson(pathOutput, dictPayload):
    """Write JSON atomically via a temp file + os.replace."""
    pathTemp = pathOutput.with_suffix(pathOutput.suffix + ".tmp")
    try:
        with open(pathTemp, "w", encoding="utf-8") as fileHandle:
            json.dump(dictPayload, fileHandle, indent=2, sort_keys=True)
        os.replace(str(pathTemp), str(pathOutput))
    except OSError:
        try:
            os.remove(str(pathTemp))
        except OSError:
            pass
        raise


def _fpathVaibifyDir(sProjectRepo):
    """Return the ``<repo>/.vaibify`` Path."""
    return Path(sProjectRepo) / ".vaibify"


def _fpathAttestationFile(sProjectRepo):
    """Return the top-level attestation file Path."""
    return _fpathVaibifyDir(sProjectRepo) / S_ATTESTATION_FILENAME


def _fsCurrentTimestamp():
    """Return the current UTC time as an ISO 8601 ``Z``-suffixed string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fsHistoryFilenameFor(dictAttestation):
    """Return the per-attempt archive filename derived from the timestamp."""
    sTimestamp = dictAttestation.get("sAttestedAtUtc") or _fsCurrentTimestamp()
    sSanitized = (
        sTimestamp.replace(":", "").replace("-", "").replace("Z", "Z")
    )
    sStatus = dictAttestation.get("sStatus", "unknown")
    return f"{sSanitized}_{sStatus}.json"
