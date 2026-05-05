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


# ----------------------------------------------------------------------
# 11. Symlinked parent / intermediate directory rejection
# ----------------------------------------------------------------------


def test_symlinked_parent_directory_raises_value_error(tmp_path):
    pathTargetDir = tmp_path / "real_dir"
    pathTargetDir.mkdir()
    (pathTargetDir / "a.csv").write_bytes(b"hello\n")
    pathParent = tmp_path / "out"
    pathParent.symlink_to(pathTargetDir, target_is_directory=True)
    dictWorkflow = _fdictWorkflowFromPaths(saOutputFiles=["out/a.csv"])
    with pytest.raises(ValueError) as excInfo:
        fnWriteManifest(str(tmp_path), dictWorkflow)
    assert "out" in str(excInfo.value)


def test_symlinked_intermediate_directory_raises_value_error(tmp_path):
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
        saOutputFiles=["a/b/deep.csv"],
    )
    with pytest.raises(ValueError) as excInfo:
        fnWriteManifest(str(tmp_path), dictWorkflow)
    assert "b" in str(excInfo.value)


# ----------------------------------------------------------------------
# 12. GNU-escape round-trip for paths with backslash and newline
# ----------------------------------------------------------------------


def test_path_containing_backslash_round_trips(tmp_path):
    sRelativePath = "data/weird\\name.csv"
    _fnWriteFile(tmp_path, sRelativePath, b"col\n1\n")
    dictWorkflow = _fdictWorkflowFromPaths(saDataFiles=[sRelativePath])
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
    dictWorkflow = _fdictWorkflowFromPaths(saDataFiles=[sRelativePath])
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
    dictWorkflow = _fdictWorkflowFromPaths(saDataFiles=[sRelativePath])
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
    dictWorkflow = _fdictWorkflowFromPaths(saDataFiles=["a.csv", "b.csv"])
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
    dictWorkflow = _fdictWorkflowFromPaths(saDataFiles=[sRelativePath])
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
        saOutputFiles=["/etc/passwd"],
    )
    with pytest.raises(ValueError) as excInfo:
        fnWriteManifest(str(tmp_path), dictWorkflow)
    assert "/etc/passwd" in str(excInfo.value)


def test_legal_path_inside_repo_still_accepted(tmp_path):
    """The new escape guard does not false-positive on normal paths."""
    _fnWriteFile(tmp_path, "out/normal.csv", b"x\n")
    _fnWriteFile(tmp_path, "deep/nested/dir/file.csv", b"y\n")
    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputFiles=["out/normal.csv"],
        saDataFiles=["deep/nested/dir/file.csv"],
    )
    fnWriteManifest(str(tmp_path), dictWorkflow)
    listMismatches = flistVerifyManifest(str(tmp_path))
    assert listMismatches == []


# ----------------------------------------------------------------------
# 15. Adversarial: deep-component symlink to outside the repo
# ----------------------------------------------------------------------


def test_deep_component_symlink_outside_repo_is_rejected(tmp_path):
    """Symlink discovered late in the path-walk must abort the manifest.

    Threat model: workflow declares ``a/b/c.csv`` where ``a`` is a real
    directory and ``a/b`` is a symlink to ``/tmp/attacker``. The
    symlink check must catch this before the realpath escape check
    (which would only flag plain ``..`` traversal). Without
    component-by-component link inspection, an attacker could substitute
    a symlink for an intermediate directory and have manifest hashing
    follow it transparently.
    """
    pathOutside = tmp_path / "outside"
    pathOutside.mkdir()
    (pathOutside / "c.csv").write_bytes(b"loot\n")
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    (pathRepo / "a").mkdir()
    pathLink = pathRepo / "a" / "b"
    pathLink.symlink_to(pathOutside, target_is_directory=True)

    dictWorkflow = _fdictWorkflowFromPaths(
        saOutputFiles=["a/b/c.csv"],
    )
    with pytest.raises(ValueError) as excInfo:
        fnWriteManifest(str(pathRepo), dictWorkflow)
    assert "b" in str(excInfo.value)


def test_symlinked_leaf_pointing_inside_repo_still_rejected(tmp_path):
    """Even an in-repo symlink leaf is rejected — the rule is not "outside"."""
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    (pathRepo / "real.csv").write_bytes(b"data\n")
    pathLink = pathRepo / "alias.csv"
    pathLink.symlink_to(pathRepo / "real.csv")
    dictWorkflow = _fdictWorkflowFromPaths(saOutputFiles=["alias.csv"])
    with pytest.raises(ValueError):
        fnWriteManifest(str(pathRepo), dictWorkflow)


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
        saOutputFiles=["out/a.csv"],
        saPlotFiles=["plots/b.pdf"],
        saDataFiles=["data/c.dat"],
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
    _fnWriteFile(tmp_path, "out/result.csv", "x\n")
    _fnWriteFile(tmp_path, "out/figure.pdf", b"%PDF\n")
    _fnWriteFile(tmp_path, "ref/quant.json", '{"k": 1}\n')
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "src",
        "saDataCommands": ["python dataBuild.py"],
        "saPlotCommands": ["python plotShow.py"],
        "saDataFiles": ["out/result.csv"],
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
        "out/result.csv",
        "out/figure.pdf",
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
