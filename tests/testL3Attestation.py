"""Tests for ``vaibify/reproducibility/l3Attestation.py``.

Cover the read/write/staleness/history surface end-to-end on a
temp directory so the JSON schema and the atomic-write semantics
are both exercised.
"""

import hashlib
import json
import os
import time

import pytest

from vaibify.reproducibility.l3Attestation import (
    I_SCHEMA_VERSION,
    S_ATTESTATION_FILENAME,
    S_ATTESTATION_HISTORY_DIR,
    S_STATUS_FAILED,
    S_STATUS_PASSED,
    fbL3AttestationCurrent,
    fdictBuildAttestation,
    fdictReadAttestation,
    flistReadAttestationHistory,
    fnInvalidateAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)


def _fnWriteManifest(tmp_path, sBody="# manifest\n"):
    (tmp_path / "MANIFEST.sha256").write_text(sBody)


def test_missing_attestation_reads_none(tmp_path):
    assert fdictReadAttestation(str(tmp_path)) is None
    assert not fbL3AttestationCurrent(str(tmp_path))


def test_build_attestation_returns_schema_fields():
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, "sha256:abc", "img@sha256:def",
        12.5, 47, 47, [], "/tmp/log.txt",
    )
    assert dictAtt["iSchemaVersion"] == I_SCHEMA_VERSION
    assert dictAtt["sStatus"] == S_STATUS_PASSED
    assert dictAtt["sManifestDigestAtAttestation"] == "sha256:abc"
    assert dictAtt["iOutputHashesMatched"] == 47


def test_write_persists_top_level_and_history(tmp_path):
    _fnWriteManifest(tmp_path)
    sDigest = fsCurrentManifestDigest(str(tmp_path))
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, sDigest, "img@sha256:def",
        1.0, 1, 1, [], "",
    )
    fnWriteAttestation(str(tmp_path), dictAtt)
    pathTop = tmp_path / ".vaibify" / S_ATTESTATION_FILENAME
    pathHistory = tmp_path / ".vaibify" / S_ATTESTATION_HISTORY_DIR
    assert pathTop.is_file()
    assert pathHistory.is_dir()
    assert len(list(pathHistory.glob("*.json"))) == 1


def test_attestation_current_requires_matching_manifest(tmp_path):
    _fnWriteManifest(tmp_path)
    sDigest = fsCurrentManifestDigest(str(tmp_path))
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, sDigest, "img@sha256:def",
        1.0, 1, 1, [], "",
    )
    fnWriteAttestation(str(tmp_path), dictAtt)
    assert fbL3AttestationCurrent(str(tmp_path))
    # Mutating the manifest invalidates the attestation
    _fnWriteManifest(tmp_path, "# changed\n")
    assert not fbL3AttestationCurrent(str(tmp_path))


def test_failed_attestation_is_never_current(tmp_path):
    _fnWriteManifest(tmp_path)
    sDigest = fsCurrentManifestDigest(str(tmp_path))
    dictAtt = fdictBuildAttestation(
        S_STATUS_FAILED, sDigest, "img@sha256:def",
        1.0, 0, 1, ["A10/out.txt"], "",
    )
    fnWriteAttestation(str(tmp_path), dictAtt)
    assert not fbL3AttestationCurrent(str(tmp_path))


def test_history_returns_attempts_newest_first(tmp_path):
    _fnWriteManifest(tmp_path)
    for iIndex in range(3):
        dictAtt = fdictBuildAttestation(
            S_STATUS_PASSED, "sha256:" + str(iIndex),
            "", 1.0, 1, 1, [], "",
        )
        dictAtt["sAttestedAtUtc"] = (
            f"2026-05-2{iIndex}T00:00:00Z"
        )
        fnWriteAttestation(str(tmp_path), dictAtt)
    listHistory = flistReadAttestationHistory(str(tmp_path))
    assert len(listHistory) == 3
    listTimestamps = [d["sAttestedAtUtc"] for d in listHistory]
    assert listTimestamps == sorted(listTimestamps, reverse=True)


def test_invalidate_removes_top_but_keeps_history(tmp_path):
    _fnWriteManifest(tmp_path)
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, "sha256:abc", "", 1.0, 1, 1, [], "",
    )
    fnWriteAttestation(str(tmp_path), dictAtt)
    assert fnInvalidateAttestation(str(tmp_path))
    pathHistory = tmp_path / ".vaibify" / S_ATTESTATION_HISTORY_DIR
    assert len(list(pathHistory.glob("*.json"))) == 1
    assert not (
        tmp_path / ".vaibify" / S_ATTESTATION_FILENAME
    ).is_file()


def test_current_manifest_digest_handles_missing_manifest(tmp_path):
    assert fsCurrentManifestDigest(str(tmp_path)) == ""


# ------------------------------------------------------------------
# Schema v2 (AI provenance) + migration chain
# ------------------------------------------------------------------


def test_fresh_attestation_carries_ai_provenance_key():
    dictStamp = {"listDeclaredModels": [], "sCapturedAtUtc": "t"}
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, "sha256:abc", "", 1.0, 1, 1, [], "",
        dictAiProvenance=dictStamp,
    )
    assert dictAtt["iSchemaVersion"] == 2
    assert dictAtt["dictAiProvenance"] == dictStamp


def test_v1_attestation_migrates_to_v2_with_null_provenance(tmp_path):
    pathVaibify = tmp_path / ".vaibify"
    pathVaibify.mkdir()
    dictV1 = {
        "iSchemaVersion": 1,
        "sStatus": S_STATUS_PASSED,
        "sManifestDigestAtAttestation": "sha256:abc",
        "sImageDigest": "",
        "sAttestedAtUtc": "2026-01-01T00:00:00Z",
        "fDurationSeconds": 1.0,
        "iOutputHashesMatched": 1,
        "iOutputHashesTotal": 1,
        "listDivergedHashes": [],
        "sRunLogPath": "",
    }
    (pathVaibify / S_ATTESTATION_FILENAME).write_text(
        json.dumps(dictV1),
    )
    dictRead = fdictReadAttestation(str(tmp_path))
    assert dictRead["iSchemaVersion"] == 2
    assert dictRead["dictAiProvenance"] is None
    assert dictRead["sStatus"] == S_STATUS_PASSED


def test_unknown_future_schema_version_passes_through(tmp_path):
    pathVaibify = tmp_path / ".vaibify"
    pathVaibify.mkdir()
    dictFuture = {"iSchemaVersion": 99, "sStatus": "passed"}
    (pathVaibify / S_ATTESTATION_FILENAME).write_text(
        json.dumps(dictFuture),
    )
    dictRead = fdictReadAttestation(str(tmp_path))
    assert dictRead["iSchemaVersion"] == 99
