"""Mutation-coverage tests for ``vaibify/reproducibility/l3Attestation.py``.

Each test closes a specific coverage hole found by mutation testing:
the empty-digest attestation guard, the matched-vs-total output
counts, the non-dict payload guard, the invalidate return truth, and
the ``sha256:`` digest prefix. They assert the guarantee, so they pass
on the unmutated module and fail under the corresponding mutation.
"""

import hashlib
import json

import pytest

from vaibify.reproducibility.l3Attestation import (
    S_ATTESTATION_FILENAME,
    S_STATUS_PASSED,
    fbL3AttestationCurrent,
    fdictBuildAttestation,
    fdictReadAttestation,
    fnInvalidateAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)

pytestmark = pytest.mark.falsification


def _fnWriteManifest(tmp_path, sBody="# manifest\n"):
    (tmp_path / "MANIFEST.sha256").write_text(sBody)


def _fnWriteRawAttestation(tmp_path, sContent):
    pathVaibify = tmp_path / ".vaibify"
    pathVaibify.mkdir(parents=True, exist_ok=True)
    (pathVaibify / S_ATTESTATION_FILENAME).write_text(sContent)


def test_empty_digest_attestation_not_current_without_manifest(tmp_path):
    """A passed attestation with an empty recorded digest and NO
    MANIFEST must not attest as current. Without the empty-digest
    guard, '' == '' (live digest also '') would falsely return True.

    Kills: Delete the empty-digest guard `if not sRecorded: return
    False` in fbL3AttestationCurrent (lines 154-155).
    """
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, "", "img@sha256:def", 1.0, 1, 1, [], "",
    )
    fnWriteAttestation(str(tmp_path), dictAtt)
    # No MANIFEST.sha256 written -> live digest is also "".
    assert fsCurrentManifestDigest(str(tmp_path)) == ""
    assert fbL3AttestationCurrent(str(tmp_path)) is False


def test_build_attestation_matched_distinct_from_total():
    """Matched and total counts are sourced independently; a record
    with diverged outputs must show M of N, not N of N.

    Kills: In fdictBuildAttestation, source iOutputHashesMatched from
    int(iOutputHashesTotal) instead of int(iOutputHashesMatched)
    (line 178).
    """
    dictAtt = fdictBuildAttestation(
        S_STATUS_PASSED, "sha256:abc", "img@sha256:def",
        12.5, 45, 47, [], "",
    )
    assert dictAtt["iOutputHashesMatched"] == 45
    assert dictAtt["iOutputHashesTotal"] == 47


def test_non_dict_payload_reads_none_and_not_current(tmp_path):
    """A syntactically-valid non-dict attestation file (top-level
    list) maps to None and the L3 gate stays False without raising.

    Kills: In _fdictReadRaw delete `if not isinstance(dictPayload,
    dict): return None` (lines 117-118).
    """
    _fnWriteRawAttestation(tmp_path, "[]")
    assert fdictReadAttestation(str(tmp_path)) is None
    assert fbL3AttestationCurrent(str(tmp_path)) is False


def test_invalidate_returns_false_when_no_file(tmp_path):
    """Invalidating when no attestation exists must report False so
    the dashboard does not claim it cleared something.

    Kills: Make fnInvalidateAttestation always return True regardless
    of fbRemoveFile's result (lines 213-215).
    """
    assert fnInvalidateAttestation(str(tmp_path)) is False


def test_current_manifest_digest_has_sha256_prefix(tmp_path):
    """The persisted digest carries the ``sha256:`` algorithm prefix
    and its suffix is the hex sha256 of the manifest bytes.

    Kills: In fsCurrentManifestDigest return `sHash` instead of
    `'sha256:' + sHash` (line 89).
    """
    sBody = "# manifest body\n"
    _fnWriteManifest(tmp_path, sBody)
    sDigest = fsCurrentManifestDigest(str(tmp_path))
    assert sDigest.startswith("sha256:")
    sExpectedHex = hashlib.sha256(sBody.encode("utf-8")).hexdigest()
    assert sDigest[len("sha256:"):] == sExpectedHex
