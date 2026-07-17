"""Tests for untested functions in vaibify.reproducibility.dataArchiver."""

import hashlib
import os
import tempfile

from vaibify.reproducibility.dataArchiver import (
    fsRecordDoi,
    fsGenerateArchiveReadme,
    fsGenerateChecksums,
    fdictBuildZenodoMetadata,
    fdictCollectOutputFiles,
)


def test_fsRecordDoi_stores():
    dictProvenance = {}
    sResult = fsRecordDoi(dictProvenance, "10.5281/zenodo.42")
    assert dictProvenance["sDoi"] == "10.5281/zenodo.42"
    assert sResult == "10.5281/zenodo.42"


def test_fsGenerateArchiveReadme_title():
    dictWorkflow = {"sProjectTitle": "Planet Study"}
    sReadme = fsGenerateArchiveReadme(dictWorkflow)
    assert "# Planet Study" in sReadme
    assert "vaibify" in sReadme.lower()


def test_fsGenerateArchiveReadme_steps():
    dictWorkflow = {
        "listSteps": [
            {"sName": "Step A"},
            {"sName": "Step B"},
        ]
    }
    sReadme = fsGenerateArchiveReadme(dictWorkflow)
    assert "1. Step A" in sReadme
    assert "2. Step B" in sReadme


def test_fsGenerateArchiveReadme_fallback_name():
    dictWorkflow = {"sWorkflowName": "Fallback"}
    sReadme = fsGenerateArchiveReadme(dictWorkflow)
    assert "# Fallback" in sReadme


def test_fsGenerateChecksums_computes():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "test.dat")
        with open(sFilePath, "wb") as fh:
            fh.write(b"hello world")
        sResult = fsGenerateChecksums([sFilePath])
        sExpectedHash = hashlib.sha256(b"hello world").hexdigest()
        assert sExpectedHash in sResult
        assert "test.dat" in sResult


def test_fsGenerateChecksums_skips_missing():
    sResult = fsGenerateChecksums(["/nonexistent/file.txt"])
    assert sResult.strip() == ""


def test_fdictBuildZenodoMetadata_fields():
    dictWorkflow = {
        "sProjectTitle": "Hab Zone",
        "sLicense": "MIT",
        "listKeywords": ["simulation"],
    }
    dictMeta = fdictBuildZenodoMetadata(dictWorkflow)
    assert dictMeta["title"] == "Data for: Hab Zone"
    assert dictMeta["upload_type"] == "dataset"
    assert dictMeta["license"] == "MIT"
    assert "simulation" in dictMeta["keywords"]


def test_fdictBuildZenodoMetadata_defaults():
    dictMeta = fdictBuildZenodoMetadata({})
    assert "Dataset" in dictMeta["title"]
    assert dictMeta["license"] == "CC-BY-4.0"
    assert dictMeta["creators"] == [{"name": "Vaibify User"}]


def test_fdictCollectOutputFiles_empty():
    dictWorkflow = {"listSteps": []}
    dictOutputs = fdictCollectOutputFiles(dictWorkflow, "/tmp")
    assert dictOutputs == {}

def _fdictBuildWorkflowWithTests():
    """Return a one-step workflow declaring outputs, tests, standards."""
    return {"listSteps": [{
        "sName": "S1",
        "sDirectory": "stepA",
        "saOutputDataFiles": ["stepA/data.csv"],
        "saPlotFiles": ["stepA/figure.pdf"],
        "saTestCommands": ["pytest tests/test_step01.py"],
        "dictTests": {
            "dictQuantitative": {
                "sFilePath": "stepA/tests/test_quantitative.py",
                "sStandardsPath":
                    "stepA/tests/quantitative_standards.json",
            },
        },
    }]}


def test_flistCollectArchiveFilePaths_includes_tests_by_default():
    from vaibify.reproducibility.dataArchiver import (
        flistCollectArchiveFilePaths,
    )
    listPaths = flistCollectArchiveFilePaths(
        _fdictBuildWorkflowWithTests(), "/work",
    )
    assert set(listPaths) == {
        "/work/stepA/data.csv",
        "/work/stepA/figure.pdf",
        "/work/stepA/tests/test_step01.py",
        "/work/stepA/tests/test_quantitative.py",
        "/work/stepA/tests/quantitative_standards.json",
    }


def test_flistCollectArchiveFilePaths_honors_opt_out_flag():
    from vaibify.reproducibility.dataArchiver import (
        flistCollectArchiveFilePaths,
    )
    dictWorkflow = _fdictBuildWorkflowWithTests()
    dictWorkflow["bArchiveTests"] = False
    listPaths = flistCollectArchiveFilePaths(dictWorkflow, "/work")
    assert set(listPaths) == {
        "/work/stepA/data.csv",
        "/work/stepA/figure.pdf",
    }


def test_fdictCollectOutputFiles_hashes_tests_and_standards(tmp_path):
    sTestsDir = os.path.join(str(tmp_path), "stepA", "tests")
    os.makedirs(sTestsDir, exist_ok=True)
    with open(os.path.join(str(tmp_path), "stepA", "data.csv"), "w") as fh:
        fh.write("a,b\n")
    with open(
        os.path.join(sTestsDir, "test_quantitative.py"), "w",
    ) as fh:
        fh.write("def test(): pass\n")
    with open(
        os.path.join(sTestsDir, "quantitative_standards.json"), "w",
    ) as fh:
        fh.write('{"k": 1}\n')
    dictOutputs = fdictCollectOutputFiles(
        _fdictBuildWorkflowWithTests(), str(tmp_path),
    )
    setBasenames = {os.path.basename(sPath) for sPath in dictOutputs}
    assert setBasenames == {
        "data.csv", "test_quantitative.py",
        "quantitative_standards.json",
    }


def test_fdictCollectOutputFiles_missing_test_file_is_not_an_error(tmp_path):
    """A declared-but-absent test file is omitted, never raised on."""
    dictOutputs = fdictCollectOutputFiles(
        _fdictBuildWorkflowWithTests(), str(tmp_path),
    )
    assert dictOutputs == {}
