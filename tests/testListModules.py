"""Tests for tools/listModules.py structural discovery helper."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


PATH_REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_TOOL = PATH_REPO_ROOT / "tools" / "listModules.py"
PATH_GUI = PATH_REPO_ROOT / "vaibify" / "gui"
PATH_STATIC = PATH_GUI / "static"
PATH_PIPELINE_UTILS = PATH_GUI / "pipelineUtils.py"
PATH_PIPELINE_STATE = PATH_GUI / "pipelineState.py"
PATH_SCRIPT_UTILITIES = PATH_STATIC / "scriptUtilities.js"


def _fmoduleLoadListModules():
    """Import listModules.py by file path since tools/ is not a package."""
    specModule = importlib.util.spec_from_file_location(
        "listModules", str(PATH_TOOL)
    )
    moduleLoaded = importlib.util.module_from_spec(specModule)
    specModule.loader.exec_module(moduleLoaded)
    return moduleLoaded


moduleListModules = _fmoduleLoadListModules()


def testExtractPythonModuleReturnsDictWithExpectedKeys():
    """Python extractor must return dict with sPath, listSymbols, sPurpose."""
    dictEntry = moduleListModules.fdictExtractPythonModule(
        str(PATH_PIPELINE_UTILS)
    )
    assert set(dictEntry.keys()) == {"sPath", "listSymbols", "sPurpose"}
    assert isinstance(dictEntry["sPath"], str)
    assert isinstance(dictEntry["listSymbols"], list)
    assert isinstance(dictEntry["sPurpose"], str)


def testExtractsDunderAll():
    """__all__ contents must match the actual declaration in the file."""
    dictEntry = moduleListModules.fdictExtractPythonModule(
        str(PATH_PIPELINE_UTILS)
    )
    assert dictEntry["listSymbols"], "Expected non-empty __all__ list"
    listExpected = [
        "fsShellQuote",
        "fsComputeStepLabel",
        "fnClearOutputModifiedFlags",
    ]
    assert dictEntry["listSymbols"] == listExpected


def testExtractsDunderAllOnSecondReferenceModule():
    """A second known module also yields its declared __all__ list."""
    dictEntry = moduleListModules.fdictExtractPythonModule(
        str(PATH_PIPELINE_STATE)
    )
    assert "S_STATE_PATH" in dictEntry["listSymbols"]
    assert "fdictBuildInitialState" in dictEntry["listSymbols"]


def testFirstDocstringLineIsExtracted():
    """The purpose string should be the first non-empty docstring line."""
    dictEntry = moduleListModules.fdictExtractPythonModule(
        str(PATH_PIPELINE_UTILS)
    )
    assert dictEntry["sPurpose"].startswith("Pure utility functions")


def testExtractsJavaScriptIIFEModuleName():
    """The IIFE module name should be extracted from scriptUtilities.js."""
    dictEntry = moduleListModules.fdictExtractJavaScriptModule(
        str(PATH_SCRIPT_UTILITIES)
    )
    assert dictEntry["listSymbols"] == ["VaibifyUtilities"]
    assert dictEntry["sPath"].endswith("scriptUtilities.js")


def testMarkdownOutputIsValid():
    """Markdown output must start with a table header and have pipe rows."""
    listEntries = moduleListModules.flistCollectEntries(str(PATH_GUI))
    sOutput = moduleListModules.fsRenderMarkdown(listEntries)
    listLines = [sLine for sLine in sOutput.splitlines() if sLine]
    assert listLines[0].startswith("| Path | Module/Symbols | Purpose |")
    assert listLines[1].startswith("| ---")
    for sLine in listLines[2:]:
        assert sLine.startswith("|") and sLine.endswith("|")
        assert sLine.count("|") >= 4


def testJsonOutputParses():
    """JSON output must parse into a list of dicts with required keys."""
    listEntries = moduleListModules.flistCollectEntries(str(PATH_GUI))
    sOutput = moduleListModules.fsRenderJson(listEntries)
    listParsed = json.loads(sOutput)
    assert isinstance(listParsed, list)
    assert listParsed, "Expected non-empty list from vaibify/gui scan"
    for dictEntry in listParsed:
        assert set(dictEntry.keys()) == {"sPath", "listSymbols", "sPurpose"}


def testEveryEmittedPathExists():
    """Every path in the output must resolve to a real file on disk."""
    listEntries = moduleListModules.flistCollectEntries(str(PATH_GUI))
    assert listEntries, "Expected non-empty scan results"
    for dictEntry in listEntries:
        pathResolved = PATH_REPO_ROOT / dictEntry["sPath"]
        assert pathResolved.exists(), (
            "Missing on disk: {0}".format(dictEntry["sPath"])
        )


def testOutputIsSortedByPath():
    """Collected entries must be sorted by sPath for deterministic diffs."""
    listEntries = moduleListModules.flistCollectEntries(str(PATH_GUI))
    listPaths = [dictEntry["sPath"] for dictEntry in listEntries]
    assert listPaths == sorted(listPaths)


def testCliSubprocessSmokeJson():
    """Run the CLI end-to-end via subprocess and parse JSON output."""
    tResult = subprocess.run(
        [sys.executable, str(PATH_TOOL), str(PATH_GUI), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tResult.returncode == 0, tResult.stderr
    listParsed = json.loads(tResult.stdout)
    assert isinstance(listParsed, list)
    assert listParsed
    assert all("sPath" in dictEntry for dictEntry in listParsed)


def testCliRejectsNonDirectory(tmp_path):
    """CLI must exit non-zero when given a path that is not a directory."""
    pathFile = tmp_path / "not_a_dir.txt"
    pathFile.write_text("hello")
    tResult = subprocess.run(
        [sys.executable, str(PATH_TOOL), str(pathFile)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tResult.returncode != 0
    assert "not a directory" in tResult.stderr.lower()
