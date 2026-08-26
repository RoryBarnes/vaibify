"""Extended tests for vaibify.gui.syncDispatcher pure functions."""

import os
import subprocess

import pytest

from vaibify.gui.syncDispatcher import (
    fsPythonCommand,
    _fsNormalizePath,
    fsBuildDagDot,
    fdictClassifyError,
    fdictSyncResult,
    flistCollectOutputFiles,
)


# -----------------------------------------------------------------------
# fsPythonCommand
# -----------------------------------------------------------------------

# Every program this builder composes imports vaibify, keyring or
# requests, so it names the interpreter that HAS them: the image's
# python3 in a container, and vaibify's own on the host. These
# assertions are about the QUOTING, so they ask about an id nothing
# registered, which resolves to the container answer; the mode
# branch itself is covered in testHostGitAndReposPanel.
S_UNREGISTERED_CONTAINER = "cid_unregistered_for_quoting"


def test_fsPythonCommand_basic():
    sResult = fsPythonCommand(
        "import os", "print(os.getcwd())", S_UNREGISTERED_CONTAINER,
    )
    assert sResult.startswith("python3 -c ")
    assert "import os" in sResult
    assert "print(os.getcwd())" in sResult


def test_fsPythonCommand_semicolon_separation():
    sResult = fsPythonCommand(
        "import sys", "sys.exit(0)", S_UNREGISTERED_CONTAINER,
    )
    assert "; " in sResult


def test_fsPythonCommand_uses_single_quote_wrapping():
    """Single-quote wrapping (not double) means $ and ` stay literal."""
    sResult = fsPythonCommand(
        "import os", "print(os.getcwd())", S_UNREGISTERED_CONTAINER,
    )
    sArgument = sResult[len("python3 -c "):]
    assert sArgument.startswith("'")
    assert sArgument.endswith("'")


def test_fsPythonCommand_resists_double_quote_injection(tmp_path):
    """A double quote in the call must NOT escape the argument.

    Prior to the fix, ``f'python3 -c "{sCall}"'`` allowed an attacker
    who controlled ``sFunctionCall`` to embed ``"; malicious_cmd #``
    and have the shell interpret it. The new implementation uses
    ``fsShellQuote`` (single-quote wrap), so even raw quotes are
    inert literals to the shell.
    """
    sCanary = str(tmp_path / "injection_proof")
    sMalicious = f'print(1)"; touch {sCanary} #'
    sResult = fsPythonCommand(
        "import os", sMalicious, S_UNREGISTERED_CONTAINER,
    )
    subprocess.run(
        ["bash", "-c", sResult],
        capture_output=True,
    )
    assert not os.path.exists(sCanary)


def test_fsPythonCommand_passes_dangerous_chars_as_literals():
    """Round-trip a dict literal containing dangerous shell chars."""
    sPayload = repr({"sName": 'x"; rm -rf / #', "sValue": "$(id)"})
    sCall = f"print({sPayload})"
    sResult = fsPythonCommand("", sCall, S_UNREGISTERED_CONTAINER)
    resultProcess = subprocess.run(
        ["bash", "-c", sResult],
        capture_output=True, text=True,
    )
    assert resultProcess.returncode == 0
    assert 'rm -rf' in resultProcess.stdout
    assert '$(id)' in resultProcess.stdout


# -----------------------------------------------------------------------
# _fsNormalizePath
# -----------------------------------------------------------------------


def test_fsNormalizePath_absolute_unchanged():
    sResult = _fsNormalizePath("/workspace", "/abs/script.py")
    assert sResult == "/abs/script.py"


def test_fsNormalizePath_relative_joined():
    sResult = _fsNormalizePath("/workspace/step1", "script.py")
    assert sResult == "/workspace/step1/script.py"


def test_fsNormalizePath_dotdot_normalized():
    sResult = _fsNormalizePath("/workspace/step1", "../lib/util.py")
    assert sResult == "/workspace/lib/util.py"


# -----------------------------------------------------------------------
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# -----------------------------------------------------------------------


def _fsAlwaysArchive(dictStep, sFile):
    return "archive"


def _fsAlwaysSupporting(dictStep, sFile):
    return "supporting"


# -----------------------------------------------------------------------
# fsBuildDagDot — multi-step with cross-references
# -----------------------------------------------------------------------


def test_fsBuildDagDot_cross_reference_edge():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Data",
                "saDataCommands": [],
                "saPlotCommands": [],
            },
            {
                "sName": "Plot",
                "saDataCommands": [],
                "saPlotCommands": [
                    "python plot.py {Step01.data}"
                ],
            },
        ]
    }
    sDot = fsBuildDagDot(dictWorkflow)
    assert "step1 -> step2" in sDot


def test_fsBuildDagDot_no_duplicate_edges():
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "saDataCommands": [],
             "saPlotCommands": []},
            {"sName": "B", "saDataCommands": [
                "cmd1 {Step01.x}", "cmd2 {Step01.y}"],
             "saPlotCommands": []},
        ]
    }
    sDot = fsBuildDagDot(dictWorkflow)
    assert sDot.count("step1 -> step2") == 1


def test_fsBuildDagDot_escapes_quotes_in_label():
    dictWorkflow = {
        "listSteps": [
            {"sName": 'Has "quotes"',
             "saDataCommands": [], "saPlotCommands": []},
        ]
    }
    sDot = fsBuildDagDot(dictWorkflow)
    assert '\\"' in sDot


# -----------------------------------------------------------------------
# fdictClassifyError — additional categories
# -----------------------------------------------------------------------


def test_fdictClassifyError_rate_limit():
    dictResult = fdictClassifyError(1, "rate limit exceeded")
    assert dictResult["sErrorType"] == "rateLimit"


def test_fdictClassifyError_not_found():
    dictResult = fdictClassifyError(1, "repository not found")
    assert dictResult["sErrorType"] == "notFound"


def test_fdictClassifyError_network():
    dictResult = fdictClassifyError(1, "connection refused")
    assert dictResult["sErrorType"] == "network"


def test_fdictClassifyError_unknown():
    dictResult = fdictClassifyError(1, "something else")
    assert dictResult["sErrorType"] == "unknown"


# -----------------------------------------------------------------------
# flistCollectOutputFiles
# -----------------------------------------------------------------------


def test_flistCollectOutputFiles_combines_data_and_plot():
    dictWorkflow = {
        "listSteps": [{
            "saOutputDataFiles": ["data.npy"],
            "saPlotFiles": ["plot.pdf"],
        }]
    }
    listFiles = flistCollectOutputFiles(dictWorkflow, {})
    assert len(listFiles) == 2
    listPaths = [d["sPath"] for d in listFiles]
    assert "data.npy" in listPaths
    assert "plot.pdf" in listPaths


def test_flistCollectOutputFiles_empty_workflow():
    dictWorkflow = {"listSteps": []}
    listFiles = flistCollectOutputFiles(dictWorkflow, {})
    assert listFiles == []


def _fdictArchivalStep():
    return {
        "sDirectory": "step1",
        "saOutputDataFiles": ["data.h5"],
        "saPlotFiles": [],
        "saDataCommands": ["python run.py"],
        "saTestCommands": ["pytest tests/test_run.py"],
        "dictTests": {
            "dictQuantitative": {
                "sFilePath": "step1/tests/test_quant.py",
                "sStandardsPath": "step1/tests/standards/quant.json",
            },
        },
    }


def test_flistCollectOutputFiles_offers_scripts_tests_standards():
    dictWorkflow = {"listSteps": [_fdictArchivalStep()]}
    listFiles = flistCollectOutputFiles(dictWorkflow, {})
    dictCategoryByPath = {
        dictFile["sPath"]: dictFile["sCategory"]
        for dictFile in listFiles
    }
    assert dictCategoryByPath["step1/run.py"] == "script"
    assert dictCategoryByPath["step1/tests/test_run.py"] == "test"
    assert dictCategoryByPath["step1/tests/test_quant.py"] == "test"
    assert dictCategoryByPath[
        "step1/tests/standards/quant.json"] == "standards"


def test_flistCollectOutputFiles_archive_tests_opt_out():
    dictWorkflow = {
        "bArchiveTests": False,
        "listSteps": [_fdictArchivalStep()],
    }
    listFiles = flistCollectOutputFiles(dictWorkflow, {})
    setCategories = {dictFile["sCategory"] for dictFile in listFiles}
    setPaths = {dictFile["sPath"] for dictFile in listFiles}
    assert "step1/run.py" in setPaths
    assert "test" not in setCategories
    assert "standards" not in setCategories


def test_flistCollectOutputFiles_archival_paths_use_workflow_root():
    dictWorkflow = {"listSteps": [_fdictArchivalStep()]}
    listFiles = flistCollectOutputFiles(
        dictWorkflow, {}, {}, None, "/workspace/project",
    )
    setPaths = {dictFile["sPath"] for dictFile in listFiles}
    assert "/workspace/project/step1/run.py" in setPaths
    assert "/workspace/project/step1/tests/test_run.py" in setPaths


def test_flistCollectOutputFiles_deduplicates_archival_paths():
    dictStep = _fdictArchivalStep()
    dictStep["saTestCommands"] = ["pytest step1/tests/test_quant.py"]
    dictStep["sDirectory"] = ""
    dictWorkflow = {"listSteps": [dictStep]}
    listFiles = flistCollectOutputFiles(dictWorkflow, {})
    listPaths = [dictFile["sPath"] for dictFile in listFiles]
    assert listPaths.count("step1/tests/test_quant.py") == 1


def test_flistCollectOutputFiles_overleaf_drops_archival_files():
    dictWorkflow = {"listSteps": [_fdictArchivalStep()]}
    listFiles = flistCollectOutputFiles(
        dictWorkflow, {}, {}, "overleaf",
    )
    assert listFiles == []
