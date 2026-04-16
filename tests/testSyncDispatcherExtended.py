"""Extended tests for vaibify.gui.syncDispatcher pure functions."""

import pytest

from vaibify.gui.syncDispatcher import (
    fsPythonCommand,
    _fsNormalizePath,
    _fsGenerateGitIgnore,
    _fsGenerateReadme,
    _fsBuildStepCopyCommands,
    _flistArchivePlotPaths,
    fsBuildDagDot,
    fdictClassifyError,
    fdictSyncResult,
    flistCollectOutputFiles,
)


# -----------------------------------------------------------------------
# fsPythonCommand
# -----------------------------------------------------------------------


def test_fsPythonCommand_basic():
    sResult = fsPythonCommand("import os", "print(os.getcwd())")
    assert sResult.startswith('python3 -c "')
    assert "import os" in sResult
    assert "print(os.getcwd())" in sResult


def test_fsPythonCommand_semicolon_separation():
    sResult = fsPythonCommand("import sys", "sys.exit(0)")
    assert "; " in sResult


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
# _fsGenerateGitIgnore
# -----------------------------------------------------------------------


def test_fsGenerateGitIgnore_contains_patterns():
    sResult = _fsGenerateGitIgnore()
    assert "*.npy" in sResult
    assert "*.h5" in sResult
    assert "Plot/*.pdf" in sResult
    assert ".vaibify/logs/" in sResult


# -----------------------------------------------------------------------
# _fsGenerateReadme
# -----------------------------------------------------------------------


def test_fsGenerateReadme_uses_project_title():
    dictWorkflow = {
        "sProjectTitle": "My Project",
        "listSteps": [],
    }
    sReadme = _fsGenerateReadme(dictWorkflow)
    assert "# My Project" in sReadme


def test_fsGenerateReadme_falls_back_to_name():
    dictWorkflow = {
        "sWorkflowName": "TestFlow",
        "listSteps": [],
    }
    sReadme = _fsGenerateReadme(dictWorkflow)
    assert "# TestFlow" in sReadme


def test_fsGenerateReadme_lists_steps():
    dictWorkflow = {
        "sWorkflowName": "Flow",
        "listSteps": [
            {"sName": "Alpha"},
            {"sName": "Beta"},
        ],
    }
    sReadme = _fsGenerateReadme(dictWorkflow)
    assert "1. Alpha" in sReadme
    assert "2. Beta" in sReadme


def test_fsGenerateReadme_contains_vaibify_link():
    dictWorkflow = {"listSteps": []}
    sReadme = _fsGenerateReadme(dictWorkflow)
    assert "Vaibify" in sReadme


# -----------------------------------------------------------------------
# _fsBuildStepCopyCommands
# -----------------------------------------------------------------------


def test_fsBuildStepCopyCommands_creates_mkdir():
    sResult = _fsBuildStepCopyCommands(
        "/workspace/step1", "stepOne",
        ["script.py"], [],
    )
    assert "mkdir -p" in sResult
    assert "stepOne" in sResult


def test_fsBuildStepCopyCommands_copies_scripts():
    sResult = _fsBuildStepCopyCommands(
        "/workspace/step1", "stepOne",
        ["analyze.py", "plot.py"], [],
    )
    assert "cp" in sResult
    assert "analyze.py" in sResult
    assert "plot.py" in sResult


def test_fsBuildStepCopyCommands_handles_archive_plots():
    sResult = _fsBuildStepCopyCommands(
        "/workspace/step1", "stepOne",
        [], ["/workspace/step1/fig.pdf"],
    )
    assert "pdftoppm" in sResult
    assert "fig" in sResult


def test_fsBuildStepCopyCommands_empty_lists():
    sResult = _fsBuildStepCopyCommands(
        "/workspace", "stepDir", [], [])
    assert "mkdir -p" in sResult


# -----------------------------------------------------------------------
# _flistArchivePlotPaths
# -----------------------------------------------------------------------


def _fsAlwaysArchive(dictStep, sFile):
    return "archive"


def _fsAlwaysSupporting(dictStep, sFile):
    return "supporting"


def test_flistArchivePlotPaths_collects_archive():
    dictStep = {"saPlotFiles": ["a.pdf", "b.pdf"]}
    listResult = _flistArchivePlotPaths(
        dictStep, "/workspace/step1", _fsAlwaysArchive)
    assert len(listResult) == 2


def test_flistArchivePlotPaths_skips_supporting():
    dictStep = {"saPlotFiles": ["a.pdf", "b.pdf"]}
    listResult = _flistArchivePlotPaths(
        dictStep, "/workspace", _fsAlwaysSupporting)
    assert len(listResult) == 0


def test_flistArchivePlotPaths_absolute_path_preserved():
    dictStep = {"saPlotFiles": ["/abs/fig.pdf"]}
    listResult = _flistArchivePlotPaths(
        dictStep, "/workspace", _fsAlwaysArchive)
    assert listResult[0] == "/abs/fig.pdf"


def test_flistArchivePlotPaths_relative_joined():
    dictStep = {"saPlotFiles": ["fig.pdf"]}
    listResult = _flistArchivePlotPaths(
        dictStep, "/workspace/step1", _fsAlwaysArchive)
    assert listResult[0] == "/workspace/step1/fig.pdf"


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
            "saDataFiles": ["data.npy"],
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
