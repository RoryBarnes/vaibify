"""Falsification tests for the post-rerun hash compare and its callers.

``testL3AttestationMutationCoverage`` already proves that
``fdictBuildAttestation`` keeps matched and total distinct. That guard
watches the builder, and a builder cannot see a caller that hands it the
wrong number: ``vaibify reproduce --rerun`` passed
``iTotalEntries if bRerunPassed else 0`` for months and every builder
test stayed green, because the substitution happened one frame up the
stack.

So the mutations here are sited at the call sites — one per lane that
can write an L3 attestation — plus the shared derivation both lanes now
consume. Together they make "N of N matched" unwriteable unless N files
were actually re-hashed and actually matched.
"""

import hashlib
import json

import pytest

from vaibify.cli import commandReproduce
from vaibify.gui.routes.reproducibilityRoutes import _fnPersistAttestation
from vaibify.reproducibility.rerunVerification import (
    fdictVerifyRerunOutputs,
)


pytestmark = pytest.mark.falsification


def _fnSeedManifestOverThreeFiles(pathRepo):
    """Pin three files in MANIFEST.sha256 and return their repo paths."""
    listPaths = []
    for iIndex, sName in enumerate(("alpha.txt", "beta.txt", "gamma.txt")):
        pathFile = pathRepo / sName
        pathFile.write_text(f"content {iIndex}\n")
        listPaths.append(pathFile)
    (pathRepo / "MANIFEST.sha256").write_text("".join(
        f"{hashlib.sha256(pathFile.read_bytes()).hexdigest()}  "
        f"{pathFile.name}\n"
        for pathFile in listPaths
    ))
    return listPaths


def test_shared_hash_compare_excludes_mismatches_from_matched(tmp_path):
    """One of three pinned files changed; the matched count must read 2.

    The rerun exited zero, so the only evidence of the divergence is the
    re-hash. A matched count sourced from the entry total instead of the
    total-minus-mismatches would report a full match over changed bytes.

    Kills: In fdictVerifyRerunOutputs, source "iOutputHashesMatched"
    from iTotalEntries instead of
    max(iTotalEntries - len(listMismatches), 0).
    """
    listPaths = _fnSeedManifestOverThreeFiles(tmp_path)
    listPaths[1].write_text("tampered\n")
    dictOutcome = fdictVerifyRerunOutputs(str(tmp_path), True)
    assert dictOutcome["iOutputHashesTotal"] == 3
    assert dictOutcome["iOutputHashesMatched"] == 2
    assert dictOutcome["listDivergedHashes"] == ["beta.txt"]


def test_zero_exit_rerun_with_changed_bytes_does_not_pass(tmp_path):
    """A zero-exit rerun over changed artefacts is not a reproduction.

    This is the release-blocking case: without the re-hash conjunct, a
    step that succeeds while writing one different byte attests as a
    passing L3 reproduction.

    Kills: In fdictVerifyRerunOutputs, source "bPassed" from
    bool(bRerunSucceeded) alone, dropping the "and not listMismatches"
    conjunct.
    """
    listPaths = _fnSeedManifestOverThreeFiles(tmp_path)
    listPaths[1].write_text("tampered\n")
    dictOutcome = fdictVerifyRerunOutputs(str(tmp_path), True)
    assert dictOutcome["bPassed"] is False


def test_cli_attestation_matched_count_comes_from_the_rehash(tmp_path):
    """The CLI must record the re-hash's matched count, not the total.

    Kills: In commandReproduce._fdictBuildRerunAttestation, pass
    dictOutcome["iOutputHashesTotal"] as iOutputHashesMatched.
    """
    dictOutcome = {
        "bPassed": False,
        "iOutputHashesMatched": 2,
        "iOutputHashesTotal": 3,
        "listDivergedHashes": ["beta.txt"],
    }
    dictAttestation = commandReproduce._fdictBuildRerunAttestation(
        str(tmp_path), dictOutcome, 4.0,
    )
    assert dictAttestation["iOutputHashesMatched"] == 2
    assert dictAttestation["iOutputHashesTotal"] == 3
    assert dictAttestation["sStatus"] == "failed"


def test_route_attestation_matched_count_comes_from_the_rehash(tmp_path):
    """The dashboard lane must record the re-hash's matched count too.

    Kills: In reproducibilityRoutes._fnPersistAttestation, pass
    dictResult["iOutputHashesTotal"] as iOutputHashesMatched.
    """
    dictResult = {
        "bPassed": False,
        "iOutputHashesMatched": 4,
        "iOutputHashesTotal": 5,
        "listDivergedHashes": ["beta.txt"],
        "sImageDigest": "img@sha256:" + "a" * 64,
        "sRunLogPath": "",
    }
    _fnPersistAttestation(
        str(tmp_path), "sha256:" + "b" * 64, dictResult, 9.0,
    )
    dictAttestation = json.loads(
        (tmp_path / ".vaibify" / "l3_attestation.json").read_text()
    )
    assert dictAttestation["iOutputHashesMatched"] == 4
    assert dictAttestation["iOutputHashesTotal"] == 5
