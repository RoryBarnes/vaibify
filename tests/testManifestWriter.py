"""Tests for vaibify.reproducibility.manifestWriter."""

import hashlib
import os

import pytest

from vaibify.reproducibility.manifestWriter import (
    fnWriteManifest,
    flistVerifyManifest,
)


_MANIFEST_FILENAME = "MANIFEST.sha256"


def _fnWriteFile(pathRepo, sRelativePath, baContent):
    """Create a file inside pathRepo with the given bytes."""
    pathFile = pathRepo / sRelativePath
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(baContent, str):
        baContent = baContent.encode("utf-8")
    pathFile.write_bytes(baContent)
    return pathFile


def _fdictWorkflowFromPaths(
    saOutputFiles=None, saPlotFiles=None, saDataFiles=None,
):
    """Build a single-step workflow dict that declares the given paths."""
    dictStep = {
        "sName": "OnlyStep",
        "saOutputFiles": list(saOutputFiles or []),
        "saPlotFiles": list(saPlotFiles or []),
        "saDataFiles": list(saDataFiles or []),
    }
    return {"listSteps": [dictStep]}


# ----------------------------------------------------------------------
# 1. Round-trip
# ----------------------------------------------------------------------


def test_roundtrip_three_files_verifies_clean(tmp_path):
    _fnWriteFile(tmp_path, "out/a.csv", "alpha,beta\n1,2\n")
    _fnWriteFile(tmp_path, "plots/figure.pdf", b"%PDF-1.4 fake\n")
    _fnWriteFile(tmp_path, "data/raw.dat", b"\x00\x01\x02\x03")

    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputFiles=["out/a.csv"],
        saPlotFiles=["plots/figure.pdf"],
        saDataFiles=["data/raw.dat"],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)

    assert (tmp_path / _MANIFEST_FILENAME).is_file()
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


# ----------------------------------------------------------------------
# 2. Byte mutation
# ----------------------------------------------------------------------


def test_byte_mutation_reports_actual_differs_from_expected(tmp_path):
    pathFile = _fnWriteFile(tmp_path, "out/a.csv", "alpha,beta\n1,2\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputFiles=["out/a.csv"],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)

    pathFile.write_text("alpha,beta\n1,3\n")

    listMismatches = flistVerifyManifest(str(tmp_path))
    assert len(listMismatches) == 1
    dictMismatch = listMismatches[0]
    assert dictMismatch["sPath"] == "out/a.csv"
    assert dictMismatch["sActual"] != dictMismatch["sExpected"]
    assert dictMismatch["sActual"] is not None


# ----------------------------------------------------------------------
# 3. Missing file
# ----------------------------------------------------------------------


def test_missing_file_reports_actual_none(tmp_path):
    pathFile = _fnWriteFile(tmp_path, "out/a.csv", "alpha\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputFiles=["out/a.csv"],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)
    pathFile.unlink()

    listMismatches = flistVerifyManifest(str(tmp_path))
    assert len(listMismatches) == 1
    assert listMismatches[0]["sPath"] == "out/a.csv"
    assert listMismatches[0]["sActual"] is None
    assert listMismatches[0]["sExpected"]


# ----------------------------------------------------------------------
# 4. Binary file (fake PDF with null bytes)
# ----------------------------------------------------------------------


def test_binary_file_hash_is_stable(tmp_path):
    baContent = b"%PDF-1.4\n" + bytes(range(256)) + b"\x00\x00\nEOF\n"
    pathFile = _fnWriteFile(tmp_path, "plots/binary.pdf", baContent)
    dictWorkflow = _fdictWorkflowFromPaths(
        saPlotFiles=["plots/binary.pdf"],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)

    sExpectedHash = hashlib.sha256(baContent).hexdigest()
    sManifestText = (tmp_path / _MANIFEST_FILENAME).read_text(
        encoding="utf-8",
    )
    assert sExpectedHash in sManifestText

    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []

    # Re-read and re-verify to confirm hashing is deterministic.
    pathFile.write_bytes(baContent)
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


# ----------------------------------------------------------------------
# 5. Symlink rejection
# ----------------------------------------------------------------------


def test_symlink_in_outputs_raises_value_error(tmp_path):
    pathTarget = _fnWriteFile(tmp_path, "real/target.csv", "x\n")
    pathLink = tmp_path / "out" / "linked.csv"
    pathLink.parent.mkdir(parents=True, exist_ok=True)
    pathLink.symlink_to(pathTarget)

    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputFiles=["out/linked.csv"],
    )

    with pytest.raises(ValueError) as excInfo:
        fnWriteManifest(str(tmp_path), dictWorkflow)
    assert "out/linked.csv" in str(excInfo.value)


# ----------------------------------------------------------------------
# 6. CRLF vs LF byte-exactness
# ----------------------------------------------------------------------


def test_crlf_vs_lf_produce_different_hashes(tmp_path):
    pathLfRepo = tmp_path / "lf"
    pathCrlfRepo = tmp_path / "crlf"
    _fnWriteFile(pathLfRepo, "out/a.csv", b"a\nb\nc\n")
    _fnWriteFile(pathCrlfRepo, "out/a.csv", b"a\r\nb\r\nc\r\n")

    dictWorkflow = _fdictWorkflowFromPaths(saOutputFiles=["out/a.csv"])

    fnWriteManifest(str(pathLfRepo), dictWorkflow)
    fnWriteManifest(str(pathCrlfRepo), dictWorkflow)

    sLfManifest = (pathLfRepo / _MANIFEST_FILENAME).read_text(
        encoding="utf-8",
    )
    sCrlfManifest = (pathCrlfRepo / _MANIFEST_FILENAME).read_text(
        encoding="utf-8",
    )
    assert sLfManifest != sCrlfManifest


# ----------------------------------------------------------------------
# 7. Unicode path
# ----------------------------------------------------------------------


def test_unicode_path_is_hashed_and_verified(tmp_path):
    sRelativePath = "data/résumé.csv"
    _fnWriteFile(tmp_path, sRelativePath, "name\nAda\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saDataFiles=[sRelativePath],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)

    sManifestText = (tmp_path / _MANIFEST_FILENAME).read_text(
        encoding="utf-8",
    )
    assert sRelativePath in sManifestText

    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


# ----------------------------------------------------------------------
# 8. Large file (10 MB)
# ----------------------------------------------------------------------


def test_large_file_hash_is_deterministic(tmp_path):
    baContent = os.urandom(10 * 1024 * 1024)
    _fnWriteFile(tmp_path, "data/big.bin", baContent)
    dictWorkflow = _fdictWorkflowFromPaths(
        saDataFiles=["data/big.bin"],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)
    sManifestFirst = (tmp_path / _MANIFEST_FILENAME).read_text(
        encoding="utf-8",
    )

    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []

    # Rewriting the same bytes should yield the same hash.
    _fnWriteFile(tmp_path, "data/big.bin", baContent)
    fnWriteManifest(str(tmp_path), dictWorkflow)
    sManifestSecond = (tmp_path / _MANIFEST_FILENAME).read_text(
        encoding="utf-8",
    )
    assert sManifestFirst == sManifestSecond


# ----------------------------------------------------------------------
# 9. Empty workflow
# ----------------------------------------------------------------------


def test_empty_workflow_writes_empty_manifest(tmp_path):
    dictWorkflow = {"listSteps": []}

    fnWriteManifest(str(tmp_path), dictWorkflow)

    pathManifest = tmp_path / _MANIFEST_FILENAME
    assert pathManifest.is_file()
    sContent = pathManifest.read_text(encoding="utf-8")
    assert sContent.startswith("#")
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


# ----------------------------------------------------------------------
# 10. Stable ordering (byte-identical writes)
# ----------------------------------------------------------------------


def test_stable_ordering_byte_identical_writes(tmp_path):
    _fnWriteFile(tmp_path, "z/last.csv", "z\n")
    _fnWriteFile(tmp_path, "a/first.csv", "a\n")
    _fnWriteFile(tmp_path, "m/middle.csv", "m\n")

    dictWorkflow = {
        "listSteps": [
            {
                "sName": "S1",
                "saOutputFiles": ["z/last.csv", "a/first.csv"],
                "saPlotFiles": [],
                "saDataFiles": ["m/middle.csv"],
            },
            {
                "sName": "S2",
                "saOutputFiles": [],
                "saPlotFiles": ["a/first.csv"],
                "saDataFiles": [],
            },
        ],
    }

    fnWriteManifest(str(tmp_path), dictWorkflow)
    baFirst = (tmp_path / _MANIFEST_FILENAME).read_bytes()

    fnWriteManifest(str(tmp_path), dictWorkflow)
    baSecond = (tmp_path / _MANIFEST_FILENAME).read_bytes()

    assert baFirst == baSecond

    listLines = [
        sLine for sLine in baFirst.decode("utf-8").splitlines()
        if sLine and not sLine.startswith("#")
    ]
    listPaths = [sLine.split("  ", 1)[1] for sLine in listLines]
    assert listPaths == sorted(listPaths)
    # Duplicate (a/first.csv across two steps) should appear once.
    assert len(listPaths) == len(set(listPaths))
