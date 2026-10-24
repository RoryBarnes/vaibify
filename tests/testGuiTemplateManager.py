"""Tests for vaibify.gui.templateManager template hashing utilities."""

import json
import re
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.templateManager import (
    SET_PRIOR_TEMPLATE_HASHES,
    _fbFileMatchesTemplate,
    _fsComputeTemplateHash,
    _fsEmbedTemplateHash,
    fsBuildIntegrityTestCode,
    fsBuildQualitativeTestCode,
    fsBuildQuantitativeTestCode,
    fsIntegrityTemplateHash,
    fsQualitativeTemplateHash,
    fsQuantitativeTemplateHash,
)


# ---------------------------------------------------------------
# _fsComputeTemplateHash / _fsEmbedTemplateHash round-trip
# ---------------------------------------------------------------


def test_fsComputeTemplateHash_is_deterministic():
    sTemplate = '"""doc"""\n\nimport os\n'
    sHashA = _fsComputeTemplateHash(sTemplate)
    sHashB = _fsComputeTemplateHash(sTemplate)
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_fsComputeTemplateHash_ignores_leading_hash_line():
    """The hash-stripping regex requires the hash line to be first."""
    sWithHash = "# vaibify-template-hash: abc123def4567890\nimport os\n"
    sWithoutHash = "import os\n"
    assert _fsComputeTemplateHash(sWithHash) == _fsComputeTemplateHash(
        sWithoutHash,
    )


def test_fsEmbedTemplateHash_prepends_hash_on_second_line():
    sTemplate = '"""doc"""\nimport os\n'
    sEmbedded = _fsEmbedTemplateHash(sTemplate)
    assert sEmbedded.startswith('"""doc"""')
    assert "# vaibify-template-hash:" in sEmbedded
    assert "\nimport os" in sEmbedded


def test_fsEmbedTemplateHash_single_line_input():
    sEmbedded = _fsEmbedTemplateHash('"""doc"""')
    assert '"""doc"""' in sEmbedded
    assert "# vaibify-template-hash:" in sEmbedded


# ---------------------------------------------------------------
# _fbFileMatchesTemplate: lines 49-57
# ---------------------------------------------------------------


def test_fbFileMatchesTemplate_empty_file_returns_true():
    """Missing files are safe to overwrite."""
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value="",
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", "template content",
        )


def test_fbFileMatchesTemplate_matching_hash_returns_true():
    sTemplate = '"""doc"""\nimport os\n'
    sExpectedHash = _fsComputeTemplateHash(sTemplate)
    sExisting = (
        '"""doc"""\n'
        f"# vaibify-template-hash: {sExpectedHash}\n"
        "import os\n"
    )
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_mismatched_hash_returns_false():
    sTemplate = '"""doc"""\nimport os\n'
    sExisting = (
        '"""doc"""\n'
        "# vaibify-template-hash: deadbeefdeadbeef\n"
        "import os\n"
    )
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        assert not _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_no_hash_line_compares_content():
    """File without hash line is matched via whole-content comparison."""
    sTemplate = '"""doc"""\nimport os\n'
    sExisting = _fsEmbedTemplateHash(sTemplate)
    mockDocker = MagicMock()
    # Strip hash line, then the whole-content comparison should still
    # find a match because _fsEmbedTemplateHash(existing) == embedded.
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_unrelated_file_returns_false():
    sTemplate = '"""doc"""\nimport os\n'
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value="completely different file content\n",
    ):
        assert not _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_non_string_treated_as_empty():
    """Bytes or None are treated as empty -> safe to overwrite."""
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=None,
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", "template",
        )


# ---------------------------------------------------------------
# Public hash accessors return stable 16-char hex strings
# ---------------------------------------------------------------


def test_fsQuantitativeTemplateHash_is_stable():
    sHashA = fsQuantitativeTemplateHash()
    sHashB = fsQuantitativeTemplateHash()
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_fsIntegrityTemplateHash_is_stable():
    sHashA = fsIntegrityTemplateHash()
    sHashB = fsIntegrityTemplateHash()
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_fsQualitativeTemplateHash_is_stable():
    sHashA = fsQualitativeTemplateHash()
    sHashB = fsQualitativeTemplateHash()
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_different_templates_produce_different_hashes():
    assert (
        fsQuantitativeTemplateHash()
        != fsIntegrityTemplateHash()
    )
    assert (
        fsIntegrityTemplateHash()
        != fsQualitativeTemplateHash()
    )


# ---------------------------------------------------------------
# Earlier template versions are still vaibify's, not custom
# ---------------------------------------------------------------


DICT_PINNED_TEMPLATE_HASHES = {
    "quantitative": "a1eeee271d0ca0d3",
    "integrity": "a77c119b8c787faf",
    "qualitative": "15914651d5c3886f",
}

DICT_TEMPLATE_BUILDERS = {
    "quantitative": fsBuildQuantitativeTestCode,
    "integrity": fsBuildIntegrityTestCode,
    "qualitative": fsBuildQualitativeTestCode,
}


def _fsReadStampedHash(sFileText):
    """Return the hash stamped into a generated test file's text."""
    return re.search(
        r"^# vaibify-template-hash: ([0-9a-f]+)", sFileText, re.MULTILINE,
    ).group(1)


def testGeneratedFileCarriesTheCurrentTemplateHash():
    """The stamp written into a file is the hash the dashboard expects.

    The two once differed: the current hash was computed over the
    file WITH its stamp line, so every generated file read as custom.
    """
    from vaibify.gui.routes.pipelineRoutes import _fdictExpectedTemplateHashes
    dictExpected = _fdictExpectedTemplateHashes()
    for sCategory, fnBuild in DICT_TEMPLATE_BUILDERS.items():
        assert _fsReadStampedHash(fnBuild()) == (
            dictExpected[f"test_{sCategory}.py"]
        )


def testFreshlyGeneratedFilesAreNotCustom():
    from vaibify.gui.routes.pipelineRoutes import (
        _fdictExpectedTemplateHashes, _flistFindCustomTestFiles,
    )
    dictStamps = {
        f"test_{sCategory}_fitModel.py": _fsReadStampedHash(fnBuild())
        for sCategory, fnBuild in DICT_TEMPLATE_BUILDERS.items()
    }
    assert _flistFindCustomTestFiles(
        dictStamps, _fdictExpectedTemplateHashes(),
    ) == []


def testFreshlyGeneratedFileIsSafeToOverwrite():
    for fnBuild in DICT_TEMPLATE_BUILDERS.values():
        sTemplate = fnBuild()
        with patch(
            "vaibify.gui.llmInvoker.fsReadFileFromContainer",
            return_value=sTemplate,
        ):
            assert _fbFileMatchesTemplate(
                MagicMock(), "cid", "/ws/test.py", sTemplate,
            )


def testTemplateHashesArePinned():
    """A template change must record the hash it retires.

    Every project generated from the old template carries the old hash
    in its test files. Unless that hash joins SET_PRIOR_TEMPLATE_HASHES,
    the dashboard calls those untouched files "custom test scripts" and
    regenerating them asks for an overwrite confirmation.
    """
    dictCurrent = {
        "quantitative": fsQuantitativeTemplateHash(),
        "integrity": fsIntegrityTemplateHash(),
        "qualitative": fsQualitativeTemplateHash(),
    }
    assert dictCurrent == DICT_PINNED_TEMPLATE_HASHES, (
        "A test template changed. Add each outgoing hash from "
        "DICT_PINNED_TEMPLATE_HASHES to SET_PRIOR_TEMPLATE_HASHES in "
        "vaibify/gui/templateManager.py, then pin the new hashes here: "
        f"{dictCurrent}"
    )


def testNoCurrentTemplateHashIsListedAsPrior():
    setCurrent = set(DICT_PINNED_TEMPLATE_HASHES.values())
    assert not setCurrent & SET_PRIOR_TEMPLATE_HASHES


def testFileFromAnEarlierTemplateIsNotCustom():
    from vaibify.gui.routes.pipelineRoutes import _flistFindCustomTestFiles
    sPriorHash = sorted(SET_PRIOR_TEMPLATE_HASHES)[0]
    dictExpected = {"test_integrity.py": fsIntegrityTemplateHash()}
    assert _flistFindCustomTestFiles(
        {"test_integrity_fitModel.py": sPriorHash}, dictExpected,
    ) == []
    assert _flistFindCustomTestFiles(
        {"test_integrity_fitModel.py": "0123456789abcdef"}, dictExpected,
    ) == ["test_integrity_fitModel.py"]


_S_EARLIER_TEMPLATE = (
    '"""Integrity tests generated by an earlier vaibify."""\n'
    "import os\n\n\ndef test_placeholder():\n    assert os\n"
)


def _fbExistingMatches(sExisting, sTemplate=None):
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        return _fbFileMatchesTemplate(
            MagicMock(), "cid", "/ws/test.py",
            sTemplate or fsBuildIntegrityTestCode(),
        )


def testFileFromAnEarlierTemplateIsSafeToOverwrite():
    """A pristine file from a retired template is vaibify's own.

    No retired template's text is kept, so one is synthesized and its
    hash entered in the prior set: the property under test is that a
    known prior stamp over a body that still hashes to it is
    unmodified. (This test used to stamp a prior hash over a body that
    never produced it, which is exactly an edited file.)
    """
    from vaibify.gui import templateManager
    sStamp = _fsComputeTemplateHash(_S_EARLIER_TEMPLATE)
    with patch.object(
        templateManager, "SET_PRIOR_TEMPLATE_HASHES",
        SET_PRIOR_TEMPLATE_HASHES | {sStamp},
    ):
        assert _fbExistingMatches(_fsEmbedTemplateHash(_S_EARLIER_TEMPLATE))


def testAPristineCurrentFileIsSafeToOverwrite():
    assert _fbExistingMatches(fsBuildIntegrityTestCode())
    assert _fbExistingMatches(
        fsBuildQuantitativeTestCode(), fsBuildQuantitativeTestCode(),
    )


@pytest.mark.falsification
def testAStampedFileWithAnAppendedTestIsTheResearchers():
    """Kills: trusting the stamp without hashing the file's own body.

    A researcher who appended a test kept vaibify's stamp; regeneration
    must ask before overwriting it.
    """
    sEdited = fsBuildIntegrityTestCode() + (
        "\n\ndef test_my_own_check():\n    assert True\n"
    )
    assert not _fbExistingMatches(sEdited)


def testAnEditedFileFromAnEarlierTemplateIsTheResearchers():
    from vaibify.gui import templateManager
    sStamp = _fsComputeTemplateHash(_S_EARLIER_TEMPLATE)
    sEdited = _fsEmbedTemplateHash(_S_EARLIER_TEMPLATE) + "# my note\n"
    with patch.object(
        templateManager, "SET_PRIOR_TEMPLATE_HASHES",
        SET_PRIOR_TEMPLATE_HASHES | {sStamp},
    ):
        assert not _fbExistingMatches(sEdited)


# ---------------------------------------------------------------
# The generated integrity test, run for real against a binary output
# ---------------------------------------------------------------


def _fnWriteIntegrityStep(pathStep, sFileName, baContent):
    """Lay out a step directory holding one output and its integrity test."""
    pathTests = pathStep / "tests"
    pathTests.mkdir(parents=True)
    (pathStep / sFileName).write_bytes(baContent)
    (pathTests / "test_integrity_fitModel.py").write_text(
        fsBuildIntegrityTestCode(), encoding="utf-8",
    )
    (pathTests / "integrity_standards_fitModel.json").write_text(
        json.dumps({"listStandards": [{"sFileName": sFileName}]}),
        encoding="utf-8",
    )


def _ftRunGeneratedIntegrityTest(pathStep):
    """Run the generated test file with pytest and return its exit code."""
    resultProcess = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_integrity_fitModel.py"],
        cwd=str(pathStep), capture_output=True, text=True, timeout=120,
    )
    return resultProcess.returncode, resultProcess.stdout


def testGeneratedIntegrityTestPassesANonEmptyBinaryOutput(tmp_path):
    """A PNG has no dedicated checker; it must not crash on decoding."""
    _fnWriteIntegrityStep(
        tmp_path, "figure.png", b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01",
    )
    iExitCode, sOutput = _ftRunGeneratedIntegrityTest(tmp_path)
    assert iExitCode == 0, sOutput
    assert "UnicodeDecodeError" not in sOutput


def testGeneratedIntegrityTestStillFailsAnEmptyTextOutput(tmp_path):
    _fnWriteIntegrityStep(tmp_path, "notes.txt", b"   \n\n")
    iExitCode, sOutput = _ftRunGeneratedIntegrityTest(tmp_path)
    assert iExitCode == 1, sOutput
    assert "empty file" in sOutput
