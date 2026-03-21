"""Tests for vaibify.gui.commandUtilities shared functions."""

from vaibify.gui.commandUtilities import (
    fsExtractScriptPath,
    flistExtractScripts,
)


def test_fsExtractScriptPath_python_command():
    assert fsExtractScriptPath("python kepler_ffd.py") == "kepler_ffd.py"


def test_fsExtractScriptPath_python3_command():
    assert fsExtractScriptPath("python3 run.py --flag") == "run.py"


def test_fsExtractScriptPath_direct_script():
    assert fsExtractScriptPath("analysis.py arg1") == "analysis.py"


def test_fsExtractScriptPath_non_python():
    assert fsExtractScriptPath("maxlev config.json") == ""


def test_fsExtractScriptPath_empty():
    assert fsExtractScriptPath("") == ""


def test_flistExtractScripts_multiple():
    listCmds = [
        "python kepler_ffd.py",
        "python plotCorner.py output.pdf",
        "echo done",
    ]
    assert flistExtractScripts(listCmds) == [
        "kepler_ffd.py", "plotCorner.py"]


def test_flistExtractScripts_deduplicates():
    listCmds = ["python run.py", "python run.py --retry"]
    assert flistExtractScripts(listCmds) == ["run.py"]


def test_flistExtractScripts_empty():
    assert flistExtractScripts([]) == []
