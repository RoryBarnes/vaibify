"""Tests for vaibify.reproducibility.manifestPaths.

These helpers are the single source of truth for the set of repo-relative
artefact paths a workflow declares. ``manifestWriter`` and
``vaibify.gui.stateContract`` both consume this module, so the tests here
also pin the lockstep contract: anything that breaks here breaks both.
"""

from vaibify.reproducibility.manifestPaths import (
    fsToRepoRelative,
    fsExtractScriptFromCommand,
    flistExtractStepScripts,
    flistStepScriptRepoPaths,
    flistStepStandardsRepoPaths,
)


# ---------------------------------------------------------------------------
# fsExtractScriptFromCommand: edge cases the round-1 reviewer surfaced
# ---------------------------------------------------------------------------


def test_fsExtractScriptFromCommand_simple_python_invocation():
    """``python foo.py`` returns ``foo.py``."""
    assert fsExtractScriptFromCommand("python foo.py") == "foo.py"


def test_fsExtractScriptFromCommand_python3_alias():
    """``python3 foo.py`` is recognised the same as ``python foo.py``."""
    assert fsExtractScriptFromCommand("python3 foo.py") == "foo.py"


def test_fsExtractScriptFromCommand_direct_py_executable():
    """``./foo.py arg`` returns ``./foo.py`` for directly-run scripts."""
    assert fsExtractScriptFromCommand("./foo.py --flag") == "./foo.py"


def test_fsExtractScriptFromCommand_envvar_prefix_returns_empty():
    """``OMP_NUM_THREADS=4 python foo.py`` is not parsed as a script.

    Workflow conventions reject env-var prefixes; an env-var assignment
    is the first token, neither ``python`` nor a ``.py`` file, so the
    helper returns empty rather than guessing.
    """
    assert fsExtractScriptFromCommand(
        "OMP_NUM_THREADS=4 python foo.py"
    ) == ""


def test_fsExtractScriptFromCommand_subshell_returns_empty():
    """``(cd subdir && python foo.py)`` is not a script invocation we parse."""
    assert fsExtractScriptFromCommand(
        "(cd subdir && python foo.py)"
    ) == ""


def test_fsExtractScriptFromCommand_empty_command():
    """An empty string returns empty; no IndexError on listTokens[0]."""
    assert fsExtractScriptFromCommand("") == ""


def test_fsExtractScriptFromCommand_python_with_no_argument():
    """``python`` alone returns empty; no IndexError on listTokens[1]."""
    assert fsExtractScriptFromCommand("python") == ""


# ---------------------------------------------------------------------------
# fsToRepoRelative
# ---------------------------------------------------------------------------


def test_fsToRepoRelative_strips_workspace_prefix():
    """``/workspace/foo.py`` → ``foo.py``."""
    assert fsToRepoRelative("/workspace/foo.py") == "foo.py"


def test_fsToRepoRelative_passes_relative_through():
    """Already-relative paths normalise but stay relative."""
    assert fsToRepoRelative("step1/foo.py") == "step1/foo.py"


def test_fsToRepoRelative_handles_empty():
    """Empty string returns empty without raising."""
    assert fsToRepoRelative("") == ""


def test_fsToRepoRelative_normalises_redundant_segments():
    """``/workspace/./a/../b/c`` collapses to ``b/c``."""
    assert fsToRepoRelative("/workspace/./a/../b/c") == "b/c"


def test_fsToRepoRelative_bare_workspace_returns_empty():
    """``/workspace`` (no trailing slash) collapses to repo root."""
    assert fsToRepoRelative("/workspace") == ""


# ---------------------------------------------------------------------------
# flistStepScriptRepoPaths: the integration with sDirectory + script tokens
# ---------------------------------------------------------------------------


def test_flistStepScriptRepoPaths_joins_with_step_directory():
    """A relative script joins with sDirectory to produce a repo-relative path."""
    dictStep = {
        "sDirectory": "stepOne",
        "saDataCommands": ["python compute.py"],
    }
    listResult = flistStepScriptRepoPaths(dictStep)
    assert listResult == ["stepOne/compute.py"]


def test_flistStepScriptRepoPaths_absolute_script_uses_workspace_strip():
    """An absolute ``/workspace/...`` script does not get joined twice."""
    dictStep = {
        "sDirectory": "stepOne",
        "saDataCommands": ["python /workspace/scripts/compute.py"],
    }
    listResult = flistStepScriptRepoPaths(dictStep)
    assert listResult == ["scripts/compute.py"]


def test_flistStepScriptRepoPaths_no_directory_keeps_relative():
    """When sDirectory is empty, the script path stays relative as given."""
    dictStep = {
        "saDataCommands": ["python tools/run.py"],
    }
    listResult = flistStepScriptRepoPaths(dictStep)
    assert listResult == ["tools/run.py"]


# ---------------------------------------------------------------------------
# flistStepStandardsRepoPaths: dictTests pinning
# ---------------------------------------------------------------------------


def test_flistStepStandardsRepoPaths_extracts_three_categories():
    """Each of the three test categories contributes its sStandardsPath."""
    dictStep = {
        "dictTests": {
            "dictQualitative": {"sStandardsPath": "ref/qual.json"},
            "dictQuantitative": {"sStandardsPath": "ref/quant.json"},
            "dictIntegrity": {"sStandardsPath": "ref/integ.json"},
        },
    }
    listResult = flistStepStandardsRepoPaths(dictStep)
    assert set(listResult) == {
        "ref/qual.json", "ref/quant.json", "ref/integ.json",
    }


def test_flistStepStandardsRepoPaths_empty_when_no_tests():
    """A step without dictTests produces an empty list."""
    assert flistStepStandardsRepoPaths({}) == []


def test_flistStepStandardsRepoPaths_skips_blank_paths():
    """Blank sStandardsPath values are not emitted."""
    dictStep = {
        "dictTests": {
            "dictQualitative": {"sStandardsPath": ""},
            "dictQuantitative": {"sStandardsPath": "ref/quant.json"},
        },
    }
    listResult = flistStepStandardsRepoPaths(dictStep)
    assert listResult == ["ref/quant.json"]


# ---------------------------------------------------------------------------
# flistExtractStepScripts: aggregation across both command keys
# ---------------------------------------------------------------------------


def test_flistExtractStepScripts_walks_data_and_plot_commands():
    """Both saDataCommands and saPlotCommands feed the script list."""
    dictStep = {
        "saDataCommands": ["python computeData.py"],
        "saPlotCommands": ["python plotResults.py"],
    }
    listResult = flistExtractStepScripts(dictStep)
    assert set(listResult) == {"computeData.py", "plotResults.py"}


def test_flistExtractStepScripts_drops_non_python_commands():
    """Bash one-liners and binaries do not appear in the script list."""
    dictStep = {
        "saDataCommands": [
            "make all",
            "./binary --config x",
            "python compute.py",
        ],
    }
    listResult = flistExtractStepScripts(dictStep)
    assert listResult == ["compute.py"]
