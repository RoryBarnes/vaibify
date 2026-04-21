"""Tests for uncovered branches in vaibify.gui.testGenerator."""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.testGenerator import (
    _fbShouldAddNoNanTest,
    _fdictBuildIntegrityStandards,
    _fdictBuildQualitativeStandards,
    _fdictWriteAllDeterministicTests,
    _fdictWriteIntegrityFiles,
    _fdictWriteQualitativeFiles,
    _fdictWriteQuantitativeFiles,
    _fnWarnIfAllUnloadable,
    _fsBuildScriptContents,
    _fsGenerateIntegrityCode,
    _fsGenerateQualitativeCode,
    fdictGenerateAllTests,
)


# ---------------------------------------------------------------
# _fbShouldAddNoNanTest: all three "disqualifier" branches
# ---------------------------------------------------------------


def test_fbShouldAddNoNanTest_requires_loadable():
    assert not _fbShouldAddNoNanTest({"bLoadable": False})


def test_fbShouldAddNoNanTest_rejects_nonzero_nan():
    assert not _fbShouldAddNoNanTest({
        "bLoadable": True,
        "iNanCount": 3,
        "sFormat": "npy",
    })


def test_fbShouldAddNoNanTest_rejects_nonzero_inf():
    """Line 306: iInfCount branch."""
    assert not _fbShouldAddNoNanTest({
        "bLoadable": True,
        "iInfCount": 2,
        "sFormat": "npy",
    })


def test_fbShouldAddNoNanTest_rejects_format_not_in_set():
    assert not _fbShouldAddNoNanTest({
        "bLoadable": True,
        "sFormat": "log",
    })


def test_fbShouldAddNoNanTest_accepts_clean_npy():
    assert _fbShouldAddNoNanTest({
        "bLoadable": True,
        "iNanCount": 0,
        "iInfCount": 0,
        "sFormat": "npy",
    })


# ---------------------------------------------------------------
# Deprecated code generators (lines 316-317, 326-327)
# ---------------------------------------------------------------


def test_fsGenerateIntegrityCode_returns_json_string():
    sResult = _fsGenerateIntegrityCode([
        {"sFileName": "a.npy", "bExists": True,
         "sFormat": "npy", "tShape": [10]},
    ])
    assert '"sFileName"' in sResult
    assert '"a.npy"' in sResult


def test_fsGenerateQualitativeCode_returns_json_string():
    sResult = _fsGenerateQualitativeCode([
        {"sFileName": "a.csv", "listColumnNames": ["col1"]},
    ])
    assert '"listExpectedColumns"' in sResult
    assert '"col1"' in sResult


# ---------------------------------------------------------------
# _fnWarnIfAllUnloadable: lines 405-406
# ---------------------------------------------------------------


def test_fnWarnIfAllUnloadable_logs_when_all_fail(caplog):
    listReports = [
        {"bLoadable": False, "sError": "permission denied"},
        {"bLoadable": False, "sError": "truncated file"},
    ]
    import logging
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        _fnWarnIfAllUnloadable(listReports)
    assert any(
        "All files unloadable" in r.message for r in caplog.records
    )


def test_fnWarnIfAllUnloadable_silent_when_any_loadable(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        _fnWarnIfAllUnloadable([
            {"bLoadable": False},
            {"bLoadable": True},
        ])
    assert not any(
        "All files unloadable" in r.message for r in caplog.records
    )


def test_fnWarnIfAllUnloadable_silent_on_empty_list(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        _fnWarnIfAllUnloadable([])
    assert not caplog.records


# ---------------------------------------------------------------
# _fsBuildScriptContents: line 188 (empty script name continue)
# ---------------------------------------------------------------


def test_fsBuildScriptContents_no_scripts_returns_placeholder():
    dictStep = {"saDataCommands": []}
    sResult = _fsBuildScriptContents(
        MagicMock(), "cid", dictStep, "/ws",
    )
    assert sResult == "(no scripts found)"


def test_fsBuildScriptContents_skips_non_python_command():
    dictStep = {"saDataCommands": ["rm -rf /tmp/foo"]}
    sResult = _fsBuildScriptContents(
        MagicMock(), "cid", dictStep, "/ws",
    )
    # Non-python commands yield no extractable script, so placeholder.
    assert sResult == "(no scripts found)"


def test_fsBuildScriptContents_includes_python_script_contents():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"print('hi')\n"
    dictStep = {"saDataCommands": ["python script.py --flag"]}
    sResult = _fsBuildScriptContents(
        mockDocker, "cid", dictStep, "/ws",
    )
    assert "script.py" in sResult
    assert "print('hi')" in sResult


def test_fsBuildScriptContents_skips_empty_content():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = RuntimeError("no file")
    dictStep = {"saDataCommands": ["python missing.py"]}
    sResult = _fsBuildScriptContents(
        mockDocker, "cid", dictStep, "/ws",
    )
    assert sResult == "(no scripts found)"


# ---------------------------------------------------------------
# Write helpers: "bNeedsOverwriteConfirm" branches
# (lines 490, 535, 577)
# ---------------------------------------------------------------


def test_writeQuantitativeFiles_requests_confirm_on_template_mismatch():
    mockDocker = MagicMock()
    dictStandards = {"fDefaultRtol": 1e-6, "listStandards": []}
    with patch(
        "vaibify.gui.testGenerator._fbFileMatchesTemplate",
        return_value=False,
    ):
        dictResult = _fdictWriteQuantitativeFiles(
            mockDocker, "cid", "/ws/step", dictStandards,
            bForceOverwrite=False,
        )
    assert dictResult.get("bNeedsOverwriteConfirm") is True
    # The standards JSON is written before the confirmation check.
    sPaths = [c.args[1] for c in mockDocker.fnWriteFile.call_args_list]
    assert any("quantitative_standards.json" in p for p in sPaths)


def test_writeQuantitativeFiles_force_overwrite_writes_file():
    mockDocker = MagicMock()
    dictStandards = {"fDefaultRtol": 1e-6, "listStandards": []}
    with patch(
        "vaibify.gui.testGenerator._fbFileMatchesTemplate",
        return_value=False,
    ):
        dictResult = _fdictWriteQuantitativeFiles(
            mockDocker, "cid", "/ws/step", dictStandards,
            bForceOverwrite=True,
        )
    # Force skips confirmation and writes both files.
    assert "sFilePath" in dictResult
    assert "bNeedsOverwriteConfirm" not in dictResult
    assert mockDocker.fnWriteFile.call_count >= 2


def test_writeIntegrityFiles_requests_confirm_on_mismatch():
    mockDocker = MagicMock()
    dictStandards = {"listStandards": []}
    with patch(
        "vaibify.gui.testGenerator._fbFileMatchesTemplate",
        return_value=False,
    ):
        dictResult = _fdictWriteIntegrityFiles(
            mockDocker, "cid", "/ws/step", dictStandards,
            bForceOverwrite=False,
        )
    assert dictResult.get("bNeedsOverwriteConfirm") is True


def test_writeQualitativeFiles_requests_confirm_on_mismatch():
    mockDocker = MagicMock()
    dictStandards = {"listStandards": []}
    with patch(
        "vaibify.gui.testGenerator._fbFileMatchesTemplate",
        return_value=False,
    ):
        dictResult = _fdictWriteQualitativeFiles(
            mockDocker, "cid", "/ws/step", dictStandards,
            bForceOverwrite=False,
        )
    assert dictResult.get("bNeedsOverwriteConfirm") is True


# ---------------------------------------------------------------
# _fdictWriteAllDeterministicTests bubbles up overwrite confirmations
# (lines 451, 457, 465, 469-470)
# ---------------------------------------------------------------


def test_writeAllDeterministic_aggregates_needs_confirm_flag():
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.testGenerator.fnWriteConftestMarker",
    ), patch(
        "vaibify.gui.testGenerator._fbFileMatchesTemplate",
        return_value=False,
    ):
        dictResult = _fdictWriteAllDeterministicTests(
            mockDocker, "cid", "/ws/step",
            [{"sFileName": "a.npy", "bExists": True,
              "sFormat": "npy", "tShape": [10]}],
            fTolerance=1e-6, bForceOverwrite=False,
            sProjectRepoPath="/workspace/DemoRepo",
        )
    assert dictResult.get("bNeedsOverwriteConfirm") is True
    # All three categories should contribute to listModifiedFiles.
    listPaths = dictResult["listModifiedFiles"]
    assert any("integrity" in p.lower() for p in listPaths)
    assert any("qualitative" in p.lower() for p in listPaths)
    assert any("quantitative" in p.lower() for p in listPaths)


def test_writeAllDeterministic_force_overwrite_no_confirm():
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.testGenerator.fnWriteConftestMarker",
    ), patch(
        "vaibify.gui.testGenerator._fbFileMatchesTemplate",
        return_value=True,
    ):
        dictResult = _fdictWriteAllDeterministicTests(
            mockDocker, "cid", "/ws/step",
            [{"sFileName": "a.npy", "bExists": True,
              "sFormat": "npy", "tShape": [10]}],
            fTolerance=1e-6, bForceOverwrite=True,
            sProjectRepoPath="/workspace/DemoRepo",
        )
    assert "bNeedsOverwriteConfirm" not in dictResult


# ---------------------------------------------------------------
# fdictGenerateAllTests dispatcher: bDeterministic=False branch (615)
# ---------------------------------------------------------------


def test_fdictGenerateAllTests_dispatches_to_llm_when_not_deterministic():
    dictWorkflow = {
        "listSteps": [{
            "sDirectory": "/ws/step", "saDataFiles": [],
            "saDataCommands": [], "saPlotCommands": [],
            "sName": "Step 1",
        }],
        "fTolerance": 1e-6,
    }
    dictVars = {}
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.testGenerator._fdictGenerateAllTestsViaLlm",
        return_value={"dictIntegrity": {}, "dictQualitative": {},
                      "dictQuantitative": {}},
    ) as mockLlm:
        fdictGenerateAllTests(
            mockDocker, "cid", 0, dictWorkflow, dictVars,
            bDeterministic=False, bUseApi=True, sApiKey="k",
        )
    mockLlm.assert_called_once()


def test_fdictGenerateAllTests_dispatches_to_deterministic_by_default():
    dictWorkflow = {
        "listSteps": [{
            "sDirectory": "/ws/step", "saDataFiles": [],
            "saDataCommands": [], "saPlotCommands": [],
            "sName": "Step 1",
        }],
        "fTolerance": 1e-6,
    }
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.testGenerator.fdictGenerateAllTestsDeterministic",
        return_value={},
    ) as mockDet:
        fdictGenerateAllTests(
            mockDocker, "cid", 0, dictWorkflow, {},
        )
    mockDet.assert_called_once()


# ---------------------------------------------------------------
# Integrity/qualitative standards builders (sanity / edge cases)
# ---------------------------------------------------------------


def test_fdictBuildIntegrityStandards_filters_missing_files():
    listReports = [
        {"sFileName": "a.npy", "bExists": True, "sFormat": "npy"},
        {"sFileName": "b.npy", "bExists": False, "sFormat": "npy"},
    ]
    dictResult = _fdictBuildIntegrityStandards(listReports)
    listFilenames = [s["sFileName"] for s in dictResult["listStandards"]]
    assert listFilenames == ["a.npy"]


def test_fdictBuildQualitativeStandards_excludes_reports_without_content():
    listReports = [
        {"sFileName": "a.csv", "listColumnNames": ["col"]},
        {"sFileName": "b.csv", "listColumnNames": [],
         "listJsonTopKeys": []},
        {"sFileName": "c.json", "listJsonTopKeys": ["topKey"]},
    ]
    dictResult = _fdictBuildQualitativeStandards(listReports)
    listFilenames = [s["sFileName"] for s in dictResult["listStandards"]]
    assert "a.csv" in listFilenames
    assert "c.json" in listFilenames
    assert "b.csv" not in listFilenames
