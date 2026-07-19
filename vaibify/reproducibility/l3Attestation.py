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

Schema versioning
-----------------
``I_SCHEMA_VERSION`` is the version every fresh attestation carries;
``fdictReadAttestation`` migrates older records forward through the
``_LIST_ATTESTATION_MIGRATORS`` chain before returning, so callers
always see the current shape. The list is empty at v1 because no
migration is needed yet. Two extension points are anticipated:

* **L4 ("Archived")** will add input-provenance keys
  (e.g. ``listInputDigests``, ``sCommitSha``) that pin not just
  outputs but also the inputs that produced them. An L4 record bumps
  ``iSchemaVersion`` to 2; the migrator at index 0 fills the new
  keys with safe defaults so L1-L3 readers can still parse a v2
  record without crashing.
* **L5 ("Attested")** will generalize the single-attestor block
  (``sStatus``, ``sAttestedAtUtc``) to ``listAttestations: [
  {sAttestor, sAttestedAtUtc, sStatus}, ...]`` so external auditors
  can co-sign. An L5 record bumps ``iSchemaVersion`` to 3; the
  migrator at index 1 wraps the legacy single-attestor fields into
  the new list shape.

Adding a future migrator is one tuple append: see
``_fdictMigrateAttestation`` for the contract.
"""

import json
import posixpath
from datetime import datetime, timezone

from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


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


I_SCHEMA_VERSION = 2
S_ATTESTATION_FILENAME = "l3_attestation.json"
S_ATTESTATION_HISTORY_DIR = "l3_attestations"
S_STATUS_PASSED = "passed"
S_STATUS_FAILED = "failed"
_S_MANIFEST_FILENAME = "MANIFEST.sha256"


def _fdictMigrateAttestationV1ToV2(dictPayload):
    """Bring a v1 record to v2: AI provenance was not captured then."""
    dictMigrated = dict(dictPayload)
    dictMigrated["iSchemaVersion"] = 2
    dictMigrated["dictAiProvenance"] = None
    return dictMigrated


# Forward-migration chain for older attestation records. Each entry is
# (iFromVersion, fnMigrate) where fnMigrate(dictPayload) transforms a
# v=iFromVersion record into v=iFromVersion+1 form. Future L4 / L5 work
# appends one tuple each. See module docstring.
_LIST_ATTESTATION_MIGRATORS = [
    (1, _fdictMigrateAttestationV1ToV2),
]


def fsCurrentManifestDigest(filesRepo):
    """Return the SHA-256 of ``MANIFEST.sha256`` or the empty string.

    The digest is the byte-exact identity of the L3 envelope: when
    it changes (re-run, new step, edited standards) the L3
    attestation is considered stale. Returns ``""`` when the manifest
    is absent so callers can short-circuit cleanly.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictHashed = filesRepo.fdictHashFiles([_S_MANIFEST_FILENAME])
    sHash = (dictHashed.get(_S_MANIFEST_FILENAME) or {}).get("sSha256")
    if not sHash:
        return ""
    return "sha256:" + sHash


def fdictReadAttestation(filesRepo):
    """Return the parsed top-level attestation file or ``None``.

    Missing file, malformed JSON, and non-dict payload all map to
    ``None`` so the L3 gate (and the dashboard banner) treat them
    uniformly as "no attestation on file". A successfully-parsed
    record is walked through ``_LIST_ATTESTATION_MIGRATORS`` so
    callers always see the current schema shape.
    """
    dictRaw = _fdictReadRaw(filesRepo)
    if dictRaw is None:
        return None
    return _fdictMigrateAttestation(dictRaw)


def _fdictReadRaw(filesRepo):
    """Return the parsed attestation file as-written, or ``None``."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = _fsAttestationRelativePath()
    if not filesRepo.fbIsFile(sRelPath):
        return None
    try:
        dictPayload = json.loads(filesRepo.fsReadText(sRelPath))
    except (OSError, ValueError):
        return None
    if not isinstance(dictPayload, dict):
        return None
    return dictPayload


def _fdictMigrateAttestation(dictPayload):
    """Walk ``_LIST_ATTESTATION_MIGRATORS`` to bring a record current.

    Each migrator transforms a v=iFromVersion record into v=iFromVersion+1
    form. Records already at the head version pass through untouched
    (no migrator matches). An unknown future version (e.g. v=99 from a
    newer client) is returned as-is rather than dropped, so the gate
    falls back to "unknown shape" handling instead of silently losing
    the attestation.
    """
    iCurrent = dictPayload.get("iSchemaVersion", 1)
    for iFrom, fnMigrate in _LIST_ATTESTATION_MIGRATORS:
        if iCurrent == iFrom:
            dictPayload = fnMigrate(dictPayload)
            iCurrent = dictPayload.get("iSchemaVersion", iFrom + 1)
    return dictPayload


def fbL3AttestationCurrent(filesRepo):
    """Return True iff an L3 attestation exists, passed, and is not stale.

    Staleness is keyed against ``fsCurrentManifestDigest`` so any
    workflow edit that re-generates the manifest invalidates the
    attestation without requiring a separate timestamp.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictPayload = fdictReadAttestation(filesRepo)
    if dictPayload is None:
        return False
    if dictPayload.get("sStatus") != S_STATUS_PASSED:
        return False
    sRecorded = dictPayload.get("sManifestDigestAtAttestation") or ""
    if not sRecorded:
        return False
    return sRecorded == fsCurrentManifestDigest(filesRepo)


def fdictBuildAttestation(
    sStatus, sManifestDigest, sImageDigest,
    fDurationSeconds, iOutputHashesMatched, iOutputHashesTotal,
    listDivergedHashes=None, sRunLogPath="", dictAiProvenance=None,
):
    """Return a fully-populated attestation dict (no file IO).

    The shape is fixed by the schema documented in the AICS plan;
    extracting it as a pure builder keeps the writer thin and lets
    tests assert payload contents without round-tripping through
    disk. ``dictAiProvenance`` is the machine-captured Replay-axis
    stamp (:mod:`vaibify.reproducibility.aiProvenanceStamp`), rebuilt
    fresh at attestation time; ``None`` records that no capture was
    possible, never that provenance was clean.
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
        "dictAiProvenance": dictAiProvenance,
    }


def fnWriteAttestation(filesRepo, dictAttestation):
    """Persist the attestation and archive a timestamped copy.

    The adapter's atomic write (sibling temp file + rename) ensures a
    crash during the write cannot leave a half-written attestation
    that would be silently treated as "passed". A copy is also
    written to the history directory; the history filename embeds the
    attestation timestamp for sortability.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    filesRepo.fnWriteJsonAtomic(
        _fsAttestationRelativePath(), dictAttestation,
    )
    sHistoryName = _fsHistoryFilenameFor(dictAttestation)
    filesRepo.fnWriteJsonAtomic(
        posixpath.join(
            _S_VAIBIFY_DIRECTORY, S_ATTESTATION_HISTORY_DIR, sHistoryName,
        ),
        dictAttestation,
    )


def fnInvalidateAttestation(filesRepo):
    """Remove the top-level attestation file (history is preserved).

    Used by the dashboard when a researcher explicitly clears an
    attestation. Returns True iff a file was actually removed.
    """
    return ffilesEnsureRepoFiles(filesRepo).fbRemoveFile(
        _fsAttestationRelativePath(),
    )


def flistReadAttestationHistory(filesRepo):
    """Return all archived attestations newest-first.

    Returns an empty list when the history directory is absent or
    every file fails to parse. Each entry is the parsed JSON dict;
    callers render the columns the UI needs (timestamp, status,
    manifest digest, duration). Container adapters batch the reads in
    one exec via ``fdictReadDirJsonContents``.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelDir = posixpath.join(
        _S_VAIBIFY_DIRECTORY, S_ATTESTATION_HISTORY_DIR,
    )
    dictContents = filesRepo.fdictReadDirJsonContents(sRelDir)
    listEntries = []
    for sFilename in sorted(dictContents, reverse=True):
        try:
            dictPayload = json.loads(dictContents[sFilename])
        except ValueError:
            continue
        if isinstance(dictPayload, dict):
            listEntries.append(dictPayload)
    return listEntries


_S_VAIBIFY_DIRECTORY = ".vaibify"


def _fsAttestationRelativePath():
    """Return the repo-relative path of the top-level attestation file."""
    return posixpath.join(_S_VAIBIFY_DIRECTORY, S_ATTESTATION_FILENAME)


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
