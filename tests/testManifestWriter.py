"""Tests for vaibify.reproducibility.manifestWriter."""

import hashlib
import os
import shutil
import subprocess

import pytest

from vaibify.reproducibility.manifestWriter import (
    fnWriteManifest,
    flistVerifyManifest,
    flistParseManifestLines,
    flistDeclaredButMissingFromManifest,
    fiCountManifestEntries,
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
    saOutputDataFiles=None, saPlotFiles=None,
):
    """Build a single-step workflow dict that declares the given paths."""
    dictStep = {
        "sName": "OnlyStep",
        "saPlotFiles": list(saPlotFiles or []),
        "saOutputDataFiles": list(saOutputDataFiles or []),
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
        saOutputDataFiles=["out/a.csv", "data/raw.dat"],
        saPlotFiles=["plots/figure.pdf"],
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
        saOutputDataFiles=["out/a.csv"],
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
        saOutputDataFiles=["out/a.csv"],
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
# 5. Symlink containment policy
# ----------------------------------------------------------------------


def test_in_root_symlink_hashes_target_under_symlink_path(tmp_path):
    pathTarget = _fnWriteFile(tmp_path, "real/target.csv", "x\n")
    pathLink = tmp_path / "out" / "linked.csv"
    pathLink.parent.mkdir(parents=True, exist_ok=True)
    pathLink.symlink_to(pathTarget)

    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=["out/linked.csv"],
    )

    fnWriteManifest(str(tmp_path), dictWorkflow)
    dictByPath = {
        dictEntry["sPath"]: dictEntry["sExpected"]
        for dictEntry in flistParseManifestLines(str(tmp_path))
    }
    sTargetHash = hashlib.sha256(b"x\n").hexdigest()
    assert dictByPath["out/linked.csv"] == sTargetHash
    assert flistVerifyManifest(str(tmp_path)) == []


# ----------------------------------------------------------------------
# 6. CRLF vs LF byte-exactness
# ----------------------------------------------------------------------


def test_crlf_vs_lf_produce_different_hashes(tmp_path):
    pathLfRepo = tmp_path / "lf"
    pathCrlfRepo = tmp_path / "crlf"
    _fnWriteFile(pathLfRepo, "out/a.csv", b"a\nb\nc\n")
    _fnWriteFile(pathCrlfRepo, "out/a.csv", b"a\r\nb\r\nc\r\n")

    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["out/a.csv"])

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
        saOutputDataFiles=[sRelativePath],
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
        saOutputDataFiles=["data/big.bin"],
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
                "saPlotFiles": [],
                "saOutputDataFiles": [
                    "z/last.csv", "a/first.csv", "m/middle.csv",
                ],
            },
            {
                "sName": "S2",
                "saPlotFiles": ["a/first.csv"],
                "saOutputDataFiles": [],
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


# ----------------------------------------------------------------------
# 11. Symlinked parent / intermediate directory containment
# ----------------------------------------------------------------------


def test_in_root_symlinked_parent_directory_hashes_target(tmp_path):
    pathTargetDir = tmp_path / "real_dir"
    pathTargetDir.mkdir()
    (pathTargetDir / "a.csv").write_bytes(b"hello\n")
    pathParent = tmp_path / "out"
    pathParent.symlink_to(pathTargetDir, target_is_directory=True)
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["out/a.csv"])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    dictByPath = {
        dictEntry["sPath"]: dictEntry["sExpected"]
        for dictEntry in flistParseManifestLines(str(tmp_path))
    }
    assert dictByPath["out/a.csv"] == hashlib.sha256(
        b"hello\n",
    ).hexdigest()


def test_in_root_symlinked_intermediate_directory_hashes_target(tmp_path):
    pathRealDeep = tmp_path / "real_a" / "real_b"
    pathRealDeep.mkdir(parents=True)
    (pathRealDeep / "deep.csv").write_bytes(b"deep\n")
    pathTopLevel = tmp_path / "a"
    pathTopLevel.mkdir()
    pathIntermediate = pathTopLevel / "b"
    pathIntermediate.symlink_to(
        pathRealDeep, target_is_directory=True,
    )
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=["a/b/deep.csv"],
    )
    fnWriteManifest(str(tmp_path), dictWorkflow)
    dictByPath = {
        dictEntry["sPath"]: dictEntry["sExpected"]
        for dictEntry in flistParseManifestLines(str(tmp_path))
    }
    assert dictByPath["a/b/deep.csv"] == hashlib.sha256(
        b"deep\n",
    ).hexdigest()


# ----------------------------------------------------------------------
# 12. GNU-escape round-trip for paths with backslash and newline
# ----------------------------------------------------------------------


def test_path_containing_backslash_round_trips(tmp_path):
    sRelativePath = "data/weird\\name.csv"
    _fnWriteFile(tmp_path, sRelativePath, b"col\n1\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=[sRelativePath])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []
    sManifest = (tmp_path / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert "\\\\name" in sManifest, (
        "backslash in path must be encoded as \\\\ per GNU spec"
    )


def test_path_containing_newline_round_trips(tmp_path):
    sRelativePath = "data/weird\nname.csv"
    try:
        _fnWriteFile(tmp_path, sRelativePath, b"col\n1\n")
    except (OSError, ValueError):
        pytest.skip("filesystem rejects newline in filename")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=[sRelativePath])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []
    sManifest = (tmp_path / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    iLineCount = len([s for s in sManifest.splitlines() if s.strip()])
    assert iLineCount == 2, (
        "header + one entry; the newline must not split the entry"
    )


def test_escaped_manifest_format_matches_gnu_sha256sum(tmp_path):
    sShasumExe = shutil.which("sha256sum") or shutil.which("shasum")
    if sShasumExe is None:
        pytest.skip("no sha256sum or shasum available")
    sRelativePath = "data/weird\\name.csv"
    _fnWriteFile(tmp_path, sRelativePath, b"data\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=[sRelativePath])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listArgs = [sShasumExe]
    if sShasumExe.endswith("shasum"):
        listArgs += ["-a", "256"]
    listArgs += ["-c", _MANIFEST_FILENAME]
    completed = subprocess.run(
        listArgs, cwd=str(tmp_path),
        capture_output=True, timeout=10,
    )
    assert completed.returncode == 0, (
        f"external shasum -c rejected the manifest: "
        f"{completed.stdout!r} {completed.stderr!r}"
    )


# ----------------------------------------------------------------------
# 13. flistParseManifestLines + fiCountManifestEntries
# ----------------------------------------------------------------------


def test_flistParseManifestLines_happy_path(tmp_path):
    _fnWriteFile(tmp_path, "a.csv", b"x\n")
    _fnWriteFile(tmp_path, "b.csv", b"y\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["a.csv", "b.csv"])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    assert len(listEntries) == 2
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {"a.csv", "b.csv"}
    for dictEntry in listEntries:
        assert len(dictEntry["sExpected"]) == 64


def test_flistParseManifestLines_skips_comments_and_blanks(tmp_path):
    pathManifest = tmp_path / _MANIFEST_FILENAME
    pathManifest.write_text(
        "# a comment\n"
        "\n"
        f"{hashlib.sha256(b'x').hexdigest()}  a.csv\n"
        "# another comment\n",
        encoding="utf-8",
    )
    listEntries = flistParseManifestLines(str(tmp_path))
    assert len(listEntries) == 1
    assert listEntries[0]["sPath"] == "a.csv"


def test_flistParseManifestLines_handles_escaped_paths(tmp_path):
    sRelativePath = "data/weird\\name.csv"
    _fnWriteFile(tmp_path, sRelativePath, b"d\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=[sRelativePath])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    assert len(listEntries) == 1
    assert listEntries[0]["sPath"] == sRelativePath


def test_flistParseManifestLines_raises_on_malformed_line(tmp_path):
    pathManifest = tmp_path / _MANIFEST_FILENAME
    pathManifest.write_text(
        "# header\n"
        "this line is malformed and has no two-space separator\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excInfo:
        flistParseManifestLines(str(tmp_path))
    sMessage = str(excInfo.value)
    assert "2" in sMessage, "error must name the offending line number"


def test_flistParseManifestLines_raises_FileNotFoundError(tmp_path):
    with pytest.raises(FileNotFoundError):
        flistParseManifestLines(str(tmp_path))


def test_fiCountManifestEntries_counts_only_real_entries(tmp_path):
    pathManifest = tmp_path / _MANIFEST_FILENAME
    pathManifest.write_text(
        "# header\n"
        "\n"
        f"{hashlib.sha256(b'x').hexdigest()}  a.csv\n"
        f"{hashlib.sha256(b'y').hexdigest()}  b.csv\n",
        encoding="utf-8",
    )
    assert fiCountManifestEntries(str(tmp_path)) == 2


def test_fiCountManifestEntries_zero_for_empty_manifest(tmp_path):
    pathManifest = tmp_path / _MANIFEST_FILENAME
    pathManifest.write_text(
        "# only the header\n", encoding="utf-8",
    )
    assert fiCountManifestEntries(str(tmp_path)) == 0


# ----------------------------------------------------------------------
# 14. Path-traversal rejection (../ and absolute paths)
# ----------------------------------------------------------------------


def test_path_escape_via_dotdot_is_rejected(tmp_path):
    """A manifest containing ``../etc/passwd`` is refused at verify time."""
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    pathManifest = pathRepo / _MANIFEST_FILENAME
    sFakeHash = hashlib.sha256(b"x").hexdigest()
    pathManifest.write_text(
        "# header\n"
        f"{sFakeHash}  ../etc/passwd\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excInfo:
        flistVerifyManifest(str(pathRepo))
    assert "../etc/passwd" in str(excInfo.value)


def test_absolute_path_in_workflow_is_rejected(tmp_path):
    """An absolute path declared in the workflow is refused at write time."""
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=["/etc/passwd"],
    )
    with pytest.raises(ValueError) as excInfo:
        fnWriteManifest(str(tmp_path), dictWorkflow)
    assert "/etc/passwd" in str(excInfo.value)


def test_legal_path_inside_repo_still_accepted(tmp_path):
    """The new escape guard does not false-positive on normal paths."""
    _fnWriteFile(tmp_path, "out/normal.csv", b"x\n")
    _fnWriteFile(tmp_path, "deep/nested/dir/file.csv", b"y\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=[
            "out/normal.csv", "deep/nested/dir/file.csv",
        ],
    )
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


# ----------------------------------------------------------------------
# 15. Adversarial: deep-component symlink to outside the repo
# ----------------------------------------------------------------------


def test_deep_component_symlink_outside_repo_skips_single_entry(tmp_path):
    """An out-of-root symlink is a per-file gap, never a full abort.

    Threat model: workflow declares ``a/b/c.csv`` where ``a`` is a
    real directory and ``a/b`` is a symlink to a directory outside
    the repo. The outside target must never be opened or hashed —
    but the *other* declared outputs must still land in the
    manifest, and the skipped path must stay visible through
    ``flistDeclaredButMissingFromManifest`` so the
    manifest-completeness blocker reports the gap honestly.
    """
    pathOutside = tmp_path / "outside"
    pathOutside.mkdir()
    (pathOutside / "c.csv").write_bytes(b"loot\n")
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    (pathRepo / "a").mkdir()
    pathLink = pathRepo / "a" / "b"
    pathLink.symlink_to(pathOutside, target_is_directory=True)
    _fnWriteFile(pathRepo, "out/good.csv", "good\n")

    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=["a/b/c.csv", "out/good.csv"],
    )
    fnWriteManifest(str(pathRepo), dictWorkflow)
    setManifestPaths = {
        dictEntry["sPath"]
        for dictEntry in flistParseManifestLines(str(pathRepo))
    }
    assert "out/good.csv" in setManifestPaths
    assert "a/b/c.csv" not in setManifestPaths
    assert flistDeclaredButMissingFromManifest(
        str(pathRepo), dictWorkflow,
    ) == ["a/b/c.csv"]


def test_loot_content_never_appears_in_manifest_for_escape(tmp_path):
    """The out-of-root target's content hash must never be recorded."""
    pathOutside = tmp_path / "outside.csv"
    pathOutside.write_bytes(b"loot\n")
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    (pathRepo / "alias.csv").symlink_to(pathOutside)
    _fnWriteFile(pathRepo, "out/good.csv", "good\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=["alias.csv", "out/good.csv"],
    )
    fnWriteManifest(str(pathRepo), dictWorkflow)
    sLootHash = hashlib.sha256(b"loot\n").hexdigest()
    sManifestBody = (pathRepo / _MANIFEST_FILENAME).read_text()
    assert sLootHash not in sManifestBody
    assert "alias.csv" not in sManifestBody


def test_symlinked_leaf_pointing_inside_repo_hashes_target(tmp_path):
    """An in-repo symlink leaf records the target content's hash."""
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    (pathRepo / "real.csv").write_bytes(b"data\n")
    pathLink = pathRepo / "alias.csv"
    pathLink.symlink_to(pathRepo / "real.csv")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["alias.csv"])
    fnWriteManifest(str(pathRepo), dictWorkflow)
    dictByPath = {
        dictEntry["sPath"]: dictEntry["sExpected"]
        for dictEntry in flistParseManifestLines(str(pathRepo))
    }
    assert dictByPath["alias.csv"] == hashlib.sha256(
        b"data\n",
    ).hexdigest()
    assert flistVerifyManifest(str(pathRepo)) == []


# ----------------------------------------------------------------------
# 16. Step scripts are hashed into the manifest
# ----------------------------------------------------------------------


def test_step_scripts_are_hashed_into_manifest(tmp_path):
    """Scripts referenced by saDataCommands / saPlotCommands appear in manifest."""
    _fnWriteFile(tmp_path, "scripts/dataMakeFoo.py", "print('hi')\n")
    _fnWriteFile(tmp_path, "scripts/plotFoo.py", "import sys\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "scripts",
        "saDataCommands": ["python dataMakeFoo.py"],
        "saPlotCommands": ["python3 plotFoo.py --opt 1"],
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert "scripts/dataMakeFoo.py" in setPaths
    assert "scripts/plotFoo.py" in setPaths
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


def test_step_scripts_with_workspace_prefix_resolve_to_repo_relative(tmp_path):
    """A ``/workspace/`` script path is rewritten to repo-relative."""
    _fnWriteFile(tmp_path, "src/runner.py", "x = 1\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "irrelevant",
        "saDataCommands": ["python /workspace/src/runner.py"],
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert "src/runner.py" in setPaths


def test_step_with_bare_py_command_is_hashed(tmp_path):
    """A bare ``foo.py`` token (no python prefix) is still extracted."""
    _fnWriteFile(tmp_path, "scripts/exec.py", "z = 0\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "scripts",
        "saPlotCommands": ["exec.py --flag"],
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert "scripts/exec.py" in setPaths


# ----------------------------------------------------------------------
# 17. Test standards are hashed into the manifest
# ----------------------------------------------------------------------


def test_test_standards_are_hashed_into_manifest(tmp_path):
    """Every dictTests[*].sStandardsPath appears in the manifest."""
    _fnWriteFile(tmp_path, "tests/standards/qual.json", "{}\n")
    _fnWriteFile(tmp_path, "tests/standards/quant.json", '{"a": 1}\n')
    _fnWriteFile(tmp_path, "tests/standards/integ.json", '{"b": 2}\n')
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "dictTests": {
            "dictQualitative": {
                "sStandardsPath": "tests/standards/qual.json"},
            "dictQuantitative": {
                "sStandardsPath": "tests/standards/quant.json"},
            "dictIntegrity": {
                "sStandardsPath": "tests/standards/integ.json"},
        },
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert "tests/standards/qual.json" in setPaths
    assert "tests/standards/quant.json" in setPaths
    assert "tests/standards/integ.json" in setPaths
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


def test_standards_path_with_workspace_prefix_is_repo_relative(tmp_path):
    """A ``/workspace/...`` standards path is rewritten correctly."""
    _fnWriteFile(tmp_path, "ref/golden.json", "{}\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "dictTests": {
            "dictQuantitative": {
                "sStandardsPath": "/workspace/ref/golden.json"},
        },
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert "ref/golden.json" in setPaths


# ----------------------------------------------------------------------
# 18. Regression guard: outputs-only workflow unchanged from before
# ----------------------------------------------------------------------


def test_outputs_only_workflow_manifest_size_unchanged(tmp_path):
    """A workflow with no scripts or tests still produces the prior manifest.

    Adding scripts and standards to the envelope must not silently
    pull extra rows into a workflow that declares neither. Three
    declared outputs must still produce exactly three entries.
    """
    _fnWriteFile(tmp_path, "out/a.csv", b"a\n")
    _fnWriteFile(tmp_path, "plots/b.pdf", b"b\n")
    _fnWriteFile(tmp_path, "data/c.dat", b"c\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputDataFiles=["out/a.csv", "data/c.dat"],
        saPlotFiles=["plots/b.pdf"],
    )
    fnWriteManifest(str(tmp_path), dictWorkflow)
    assert fiCountManifestEntries(str(tmp_path)) == 3


# ----------------------------------------------------------------------
# 19. Mixed workflow: outputs + scripts + standards together
# ----------------------------------------------------------------------


def test_mixed_workflow_hashes_all_categories(tmp_path):
    """Outputs, scripts, and standards co-exist in the manifest correctly."""
    _fnWriteFile(tmp_path, "src/dataBuild.py", "pass\n")
    _fnWriteFile(tmp_path, "src/plotShow.py", "pass\n")
    _fnWriteFile(tmp_path, "src/out/result.csv", "x\n")
    _fnWriteFile(tmp_path, "src/out/figure.pdf", b"%PDF\n")
    _fnWriteFile(tmp_path, "ref/quant.json", '{"k": 1}\n')
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "src",
        "saDataCommands": ["python dataBuild.py"],
        "saPlotCommands": ["python plotShow.py"],
        "saOutputDataFiles": ["out/result.csv"],
        "saPlotFiles": ["out/figure.pdf"],
        "dictTests": {
            "dictQuantitative": {"sStandardsPath": "ref/quant.json"},
        },
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {
        "src/dataBuild.py",
        "src/plotShow.py",
        "src/out/result.csv",
        "src/out/figure.pdf",
        "ref/quant.json",
    }
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


def test_duplicate_script_across_two_steps_appears_once(tmp_path):
    """The same script path declared by two steps is hashed once."""
    _fnWriteFile(tmp_path, "src/shared.py", "shared = True\n")
    dictWorkflow = {"listSteps": [
        {
            "sName": "S1",
            "sDirectory": "src",
            "saDataCommands": ["python shared.py"],
        },
        {
            "sName": "S2",
            "sDirectory": "src",
            "saPlotCommands": ["python shared.py"],
        },
    ]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    listPaths = [dictEntry["sPath"] for dictEntry in listEntries]
    assert listPaths.count("src/shared.py") == 1


def test_missing_script_file_raises_at_write_time(tmp_path):
    """A script declared by a command but absent from disk is an error.

    The manifest must not silently skip an artefact it was supposed to
    pin. ``fsComputeFileHash`` raises ``FileNotFoundError`` so the
    write fails loudly — better than producing a manifest that omits
    the script row.
    """
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "src",
        "saDataCommands": ["python missingScript.py"],
    }]}
    with pytest.raises(FileNotFoundError):
        fnWriteManifest(str(tmp_path), dictWorkflow)


# ----------------------------------------------------------------------
# 20. Manifest-completeness warning (legacy manifest gap)
# ----------------------------------------------------------------------


def test_flistDeclaredButMissingFromManifest_returns_gap(tmp_path):
    """The pure helper enumerates the paths absent from the manifest."""
    _fnWriteFile(tmp_path, "src/out/a.csv", b"x\n")
    _fnWriteFile(tmp_path, "src/run.py", b"pass\n")
    _fnWriteFile(tmp_path, "ref/quant.json", b"{}\n")
    fnWriteManifest(str(tmp_path), {"listSteps": [{
        "sName": "S1",
        "sDirectory": "src",
        "saOutputDataFiles": ["out/a.csv"],
    }]})
    dictWorkflowExpanded = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "src",
        "saDataCommands": ["python run.py"],
        "saOutputDataFiles": ["out/a.csv"],
        "dictTests": {
            "dictQuantitative": {"sStandardsPath": "ref/quant.json"},
        },
    }]}
    listMissing = flistDeclaredButMissingFromManifest(
        str(tmp_path), dictWorkflowExpanded,
    )
    assert set(listMissing) == {"src/run.py", "ref/quant.json"}


def test_reproduce_script_is_pinned_when_present(tmp_path):
    """reproduce.sh joins the manifest once it exists.

    fbVerifyReproduceScript requires the script's hash in the
    manifest; a writer that never pinned it made that Level 3 check
    unsatisfiable — Generate appeared to do nothing.
    """
    _fnWriteFile(tmp_path, "out/a.csv", b"x\n")
    _fnWriteFile(tmp_path, "reproduce.sh", "#!/usr/bin/env bash\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["out/a.csv"])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert "reproduce.sh" in setPaths


def test_missing_reproduce_script_does_not_break_the_manifest(tmp_path):
    """No reproduce.sh yet (pre-generation) must not abort the write."""
    _fnWriteFile(tmp_path, "out/a.csv", b"x\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["out/a.csv"])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {"out/a.csv"}


def test_flistDeclaredButMissingFromManifest_empty_when_complete(tmp_path):
    """When the manifest is complete, no paths are reported missing."""
    _fnWriteFile(tmp_path, "out/a.csv", b"x\n")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputDataFiles=["out/a.csv"])
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listMissing = flistDeclaredButMissingFromManifest(
        str(tmp_path), dictWorkflow,
    )
    assert listMissing == []


# ----------------------------------------------------------------------
# 21. Python flag tokens (-u, -m) must not be treated as script paths
# ----------------------------------------------------------------------


def test_python_dash_u_flag_does_not_hash_a_dash_u_file(tmp_path):
    """``python -u script.py`` hashes ``script.py`` and does not crash.

    Regression: an earlier extractor returned the second token of any
    ``python ...`` command, so a step whose data command was
    ``python -u foo.py`` made the writer try to hash ``<step>/-u``
    and abort with FileNotFoundError.
    """
    _fnWriteFile(tmp_path, "src/script.py", b"pass\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S",
        "sDirectory": "src",
        "saDataCommands": ["python -u script.py"],
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    listPaths = [d["sPath"] for d in listEntries]
    assert "src/script.py" in listPaths
    assert not any("-u" in s for s in listPaths)


def test_python_dash_m_module_does_not_appear_in_manifest(tmp_path):
    """``python -m mymod`` produces no manifest entry for the module token."""
    dictWorkflow = {"listSteps": [{
        "sName": "S",
        "sDirectory": "src",
        "saDataCommands": ["python -m mymod"],
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    listPaths = [d["sPath"] for d in listEntries]
    assert all("mymod" not in s for s in listPaths)
    assert all("-m" not in s for s in listPaths)

# ----------------------------------------------------------------------
# 22. Test files are archived by default (bArchiveTests)
# ----------------------------------------------------------------------


def _fdictWorkflowWithTests():
    """Return a one-step workflow declaring tests, standards, and data."""
    return {"listSteps": [{
        "sName": "S1",
        "sDirectory": "stepA",
        "saOutputDataFiles": ["data.csv"],
        "saTestCommands": ["pytest tests/test_step01.py"],
        "dictTests": {
            "dictQuantitative": {
                "sFilePath": "stepA/tests/test_quantitative.py",
                "sStandardsPath":
                    "stepA/tests/quantitative_standards.json",
            },
            "dictIntegrity": {
                "sFilePath": "stepA/tests/test_integrity.py",
            },
        },
    }]}


def _fnWriteTestArtifacts(tmp_path):
    """Create on disk every file _fdictWorkflowWithTests declares."""
    _fnWriteFile(tmp_path, "stepA/data.csv", "a,b\n1,2\n")
    _fnWriteFile(tmp_path, "stepA/tests/test_step01.py", "def test(): pass\n")
    _fnWriteFile(
        tmp_path, "stepA/tests/test_quantitative.py", "def test(): pass\n",
    )
    _fnWriteFile(
        tmp_path, "stepA/tests/test_integrity.py", "def test(): pass\n",
    )
    _fnWriteFile(
        tmp_path, "stepA/tests/quantitative_standards.json", '{"k": 1}\n',
    )


def test_test_files_are_hashed_into_manifest_by_default(tmp_path):
    """dictTests[*].sFilePath and saTestCommands files appear by default."""
    _fnWriteTestArtifacts(tmp_path)
    fnWriteManifest(str(tmp_path), _fdictWorkflowWithTests())
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {
        "stepA/data.csv",
        "stepA/tests/test_step01.py",
        "stepA/tests/test_quantitative.py",
        "stepA/tests/test_integrity.py",
        "stepA/tests/quantitative_standards.json",
    }
    assert flistVerifyManifest(str(tmp_path)) == []


def test_bArchiveTests_false_excludes_tests_and_standards(tmp_path):
    """The per-workflow opt-out flag removes tests and standards only."""
    _fnWriteTestArtifacts(tmp_path)
    dictWorkflow = _fdictWorkflowWithTests()
    dictWorkflow["bArchiveTests"] = False
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {"stepA/data.csv"}


def test_saTestCommands_path_resolves_against_step_directory(tmp_path):
    """``pytest tests/x.py`` in stepA lands at ``stepA/tests/x.py``."""
    _fnWriteFile(tmp_path, "stepB/tests/test_alpha.py", "def test(): pass\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "stepB",
        "saTestCommands": ["pytest -q tests/test_alpha.py"],
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {"stepB/tests/test_alpha.py"}


def test_flistDeclaredButMissingFromManifest_reports_test_files(tmp_path):
    """A legacy manifest without test entries surfaces them as a gap."""
    _fnWriteTestArtifacts(tmp_path)
    dictLegacy = {"listSteps": [{
        "sName": "S1",
        "saOutputDataFiles": ["stepA/data.csv"],
    }]}
    fnWriteManifest(str(tmp_path), dictLegacy)
    listMissing = flistDeclaredButMissingFromManifest(
        str(tmp_path), _fdictWorkflowWithTests(),
    )
    assert set(listMissing) == {
        "stepA/tests/test_step01.py",
        "stepA/tests/test_quantitative.py",
        "stepA/tests/test_integrity.py",
        "stepA/tests/quantitative_standards.json",
    }


def test_templated_test_file_path_is_skipped(tmp_path):
    """A templated sFilePath never enters the manifest envelope."""
    _fnWriteFile(tmp_path, "stepA/data.csv", "x\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "stepA",
        "saOutputDataFiles": ["data.csv"],
        "saTestCommands": ["pytest {scriptDir}/test_x.py"],
        "dictTests": {
            "dictIntegrity": {"sFilePath": "{scriptDir}/test_y.py"},
        },
    }]}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {"stepA/data.csv"}


# ----------------------------------------------------------------------
# Streaming write path — entry-by-entry, no whole-body materialization.
# ----------------------------------------------------------------------


def _fdictBuildLargeWorkflowFixture(tmp_path, iEntryCount):
    """Seed iEntryCount one-byte files and a workflow that declares them."""
    listPaths = []
    for iIndex in range(iEntryCount):
        sFileName = f"file_{iIndex:05d}.dat"
        _fnWriteFile(tmp_path, f"out/{sFileName}", b"x")
        listPaths.append(sFileName)
    dictStep = {
        "sName": "Big",
        "sDirectory": "out",
        "saOutputDataFiles": listPaths,
    }
    return {"listSteps": [dictStep]}


def test_streaming_write_matches_pre_streaming_string_body(tmp_path):
    """The streamed manifest is byte-identical to the legacy one-shot."""
    from vaibify.reproducibility import manifestWriter
    dictWorkflow = _fdictBuildLargeWorkflowFixture(tmp_path, 50)
    # Capture the body the legacy joined-string path would have built
    # using the same entries.
    listEntries = manifestWriter._flistBuildManifestEntries(
        manifestWriter.ffilesEnsureRepoFiles(str(tmp_path)),
        manifestWriter._flistCollectManifestPaths(dictWorkflow),
    )
    sExpected = manifestWriter._MANIFEST_HEADER + "".join(
        manifestWriter._fsFormatManifestLine(sHash, sRel)
        for sHash, sRel in listEntries
    )
    fnWriteManifest(str(tmp_path), dictWorkflow)
    sActual = (tmp_path / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert sActual == sExpected


def test_streaming_write_skips_full_body_materialization(tmp_path):
    """Streaming path must not concatenate the full body in memory.

    Proven by intercepting ``open`` for the temp file and asserting
    that ``write`` is invoked once per entry plus one for the header,
    never once with the entire body as a single call.
    """
    from vaibify.reproducibility import manifestWriter
    iEntryCount = 200
    dictWorkflow = _fdictBuildLargeWorkflowFixture(tmp_path, iEntryCount)
    listWriteCallSizes = []
    sTempPath = str(tmp_path / (_MANIFEST_FILENAME + ".tmp"))
    import builtins
    fnRealOpen = builtins.open

    class RecordingHandle:
        def __init__(self, fileHandle):
            self.fileHandle = fileHandle

        def write(self, sText):
            listWriteCallSizes.append(len(sText))
            return self.fileHandle.write(sText)

        def __enter__(self):
            self.fileHandle.__enter__()
            return self

        def __exit__(self, *args):
            return self.fileHandle.__exit__(*args)

    def fnFakeOpen(sPath, sMode, *args, **kwargs):
        fileHandle = fnRealOpen(sPath, sMode, *args, **kwargs)
        if sPath == sTempPath:
            return RecordingHandle(fileHandle)
        return fileHandle

    builtins.open = fnFakeOpen
    try:
        fnWriteManifest(str(tmp_path), dictWorkflow)
    finally:
        builtins.open = fnRealOpen
    # Header + N per-entry writes — never one giant write of the whole body.
    assert len(listWriteCallSizes) == iEntryCount + 1
    iMaxWrite = max(listWriteCallSizes)
    # The largest single write must be much smaller than the full body.
    iTotalSize = sum(listWriteCallSizes)
    assert iMaxWrite < iTotalSize / 2, (
        f"largest single write ({iMaxWrite}) too close to total "
        f"({iTotalSize}) — looks like one-shot materialization"
    )


def test_streaming_write_cleans_up_temp_on_failure(tmp_path, monkeypatch):
    """A mid-write OSError removes the streaming temp file."""
    from vaibify.reproducibility import manifestWriter
    dictWorkflow = _fdictBuildLargeWorkflowFixture(tmp_path, 5)
    import builtins
    fnRealOpen = builtins.open
    sTempPath = str(tmp_path / (_MANIFEST_FILENAME + ".tmp"))

    class _OneByteThenFail:
        def __init__(self, fileHandle):
            self.fileHandle = fileHandle
            self.iWrites = 0

        def write(self, sText):
            self.iWrites += 1
            if self.iWrites > 1:
                raise OSError("disk full")
            return self.fileHandle.write(sText)

        def __enter__(self):
            self.fileHandle.__enter__()
            return self

        def __exit__(self, *args):
            return self.fileHandle.__exit__(*args)

    def fnFakeOpen(sPath, sMode, *args, **kwargs):
        fileHandle = fnRealOpen(sPath, sMode, *args, **kwargs)
        if sPath == sTempPath:
            return _OneByteThenFail(fileHandle)
        return fileHandle

    manifestWriter.open = fnFakeOpen
    try:
        with pytest.raises(OSError):
            fnWriteManifest(str(tmp_path), dictWorkflow)
    finally:
        manifestWriter.open = fnRealOpen
    assert not os.path.exists(sTempPath), (
        "streaming temp file must be removed after a write failure"
    )


def test_manifest_pins_templated_figure_declarations(tmp_path):
    """A ``{sPlotDirectory}`` figure lands in the manifest, resolved.

    The previously unconditional skip of templated declarations meant
    a workflow declaring every figure through ``{sPlotDirectory}``
    never had ANY figure pinned — so the Overleaf/arXiv verifies had
    no expected hashes and could only report a vacuous comparison.
    """
    import os
    from vaibify.reproducibility import manifestWriter
    sRepo = str(tmp_path)
    os.makedirs(os.path.join(sRepo, "Plot"))
    with open(os.path.join(sRepo, "Plot", "corner.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 fake figure")
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "listSteps": [{
            "sDirectory": "KeplerFfdCorner",
            "saPlotFiles": ["{sPlotDirectory}/corner.{sFigureType}"],
        }],
    }
    manifestWriter.fnWriteManifest(sRepo, dictWorkflow)
    listEntries = manifestWriter.flistParseManifestLines(sRepo)
    listPaths = [d["sPath"] for d in listEntries]
    assert "Plot/corner.pdf" in listPaths
    assert "KeplerFfdCorner/Plot/corner.pdf" not in listPaths
