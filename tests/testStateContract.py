"""Tests for the vaibify workspace-as-git-repo state contract."""

import os

import pytest

from vaibify.gui import stateContract


# ----------------------------------------------------------------------
# fsToRepoRelative
# ----------------------------------------------------------------------


def test_fsToRepoRelative_strips_container_prefix():
    assert (
        stateContract.fsToRepoRelative("step1/plot.pdf")
        == "step1/plot.pdf"
    )


def test_fsToRepoRelative_handles_bare_workspace():
    assert stateContract.fsToRepoRelative("/workspace") == ""


def test_fsToRepoRelative_leaves_relative_path_alone():
    assert (
        stateContract.fsToRepoRelative("step1/plot.pdf") == "step1/plot.pdf"
    )


def test_fsToRepoRelative_strips_leading_slash_outside_workspace():
    assert (
        stateContract.fsToRepoRelative("/other/path.txt") == "other/path.txt"
    )


def test_fsToRepoRelative_empty_path():
    assert stateContract.fsToRepoRelative("") == ""


def test_fsToRepoRelative_normalizes_redundant_segments():
    assert (
        stateContract.fsToRepoRelative("/workspace/./step1/../step2/f.py")
        == "step2/f.py"
    )


# ----------------------------------------------------------------------
# flistCanonicalTrackedFiles
# ----------------------------------------------------------------------


def _fdictStep(
    sName="Step", sDirectory="/workspace/step1",
    listPlots=None, listData=None,
    listDataCommands=None, listPlotCommands=None,
    dictCategories=None, dictStandards=None,
    dictExcluded=None,
):
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "saPlotFiles": listPlots or [],
        "saDataFiles": listData or [],
        "saDataCommands": listDataCommands or [],
        "saPlotCommands": listPlotCommands or [],
        "dictPlotFileCategories": dictCategories or {},
        "dictTests": dictStandards or {},
        "dictExcludedFiles": dictExcluded or {},
    }


def _fnWriteFile(sRoot, sRelPath, sContent=""):
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath), exist_ok=True)
    with open(sAbsPath, "w") as f:
        f.write(sContent)


def test_flistCanonicalTrackedFiles_includes_plot_files(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            listPlots=["Plot/figure_1.pdf", "Plot/figure_2.pdf"],
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/Plot/figure_1.pdf" in listResult
    assert "step1/Plot/figure_2.pdf" in listResult


def test_flistCanonicalTrackedFiles_includes_data_files(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            listData=["Data/output.csv", "Data/scratch.h5"],
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/Data/output.csv" in listResult
    assert "step1/Data/scratch.h5" in listResult


def test_flistCanonicalTrackedFiles_includes_supporting_and_archive(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            listPlots=["Plot/archive.pdf", "Plot/scratch.pdf"],
            dictCategories={
                "Plot/archive.pdf": "archive",
                "Plot/scratch.pdf": "supporting",
            },
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/Plot/archive.pdf" in listResult
    assert "step1/Plot/scratch.pdf" in listResult


def test_flistCanonicalTrackedFiles_skips_template_paths(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            listPlots=["Plot/{iteration}.pdf"],
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert not any("{" in p for p in listResult)


def test_flistCanonicalTrackedFiles_includes_scripts(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            listDataCommands=["python run.py"],
            listPlotCommands=["python3 plot.py"],
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/run.py" in listResult
    assert "step1/plot.py" in listResult


def test_flistCanonicalTrackedFiles_includes_test_standards(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            dictStandards={
                "dictQualitative": {
                    "sStandardsPath": "step1/tests/standards/qual.json",
                },
                "dictQuantitative": {
                    "sStandardsPath": "step1/tests/standards/quant.json",
                },
            },
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/tests/standards/qual.json" in listResult
    assert "step1/tests/standards/quant.json" in listResult


def test_flistCanonicalTrackedFiles_includes_root_config(tmp_path):
    _fnWriteFile(str(tmp_path), "requirements.txt", "numpy\n")
    _fnWriteFile(str(tmp_path), "Dockerfile", "FROM python\n")
    dictWorkflow = {"listSteps": []}
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "requirements.txt" in listResult
    assert "Dockerfile" in listResult


def test_flistCanonicalTrackedFiles_includes_workflow_jsons(tmp_path):
    _fnWriteFile(str(tmp_path), ".vaibify/workflows/main.json", "{}")
    _fnWriteFile(str(tmp_path), ".vaibify/workflows/alt.json", "{}")
    dictWorkflow = {"listSteps": []}
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert ".vaibify/workflows/main.json" in listResult
    assert ".vaibify/workflows/alt.json" in listResult


def test_flistCanonicalTrackedFiles_includes_test_markers(tmp_path):
    _fnWriteFile(
        str(tmp_path), ".vaibify/test_markers/demo/step1.json", "{}")
    dictWorkflow = {"listSteps": []}
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert ".vaibify/test_markers/demo/step1.json" in listResult


def test_flistCanonicalTrackedFiles_separates_per_workflow_markers(
    tmp_path,
):
    """Two workflows in the same project repo each contribute markers."""
    _fnWriteFile(
        str(tmp_path), ".vaibify/test_markers/wfa/shared.json", "{}")
    _fnWriteFile(
        str(tmp_path), ".vaibify/test_markers/wfb/shared.json", "{}")
    dictWorkflow = {"listSteps": []}
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert ".vaibify/test_markers/wfa/shared.json" in listResult
    assert ".vaibify/test_markers/wfb/shared.json" in listResult


def test_flistCanonicalTrackedFiles_includes_zenodo_refs(tmp_path):
    _fnWriteFile(str(tmp_path), ".vaibify/zenodo-refs.json", "{}")
    dictWorkflow = {"listSteps": []}
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert ".vaibify/zenodo-refs.json" in listResult


def test_flistCanonicalTrackedFiles_omits_excluded_files(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="step1",
            listPlots=["Plot/keep.pdf", "Plot/drop.pdf"],
            dictExcluded={"step1/Plot/drop.pdf": True},
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/Plot/keep.pdf" in listResult
    assert "step1/Plot/drop.pdf" not in listResult


def test_flistCanonicalTrackedFiles_deduplicates(tmp_path):
    dictWorkflow = {
        "listSteps": [
            _fdictStep(
                sDirectory="step1",
                listPlots=["Plot/fig.pdf"],
                listData=["Plot/fig.pdf"],
            ),
        ],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert listResult.count("step1/Plot/fig.pdf") == 1


def test_flistCanonicalTrackedFiles_handles_container_absolute_paths(tmp_path):
    dictWorkflow = {
        "listSteps": [_fdictStep(
            sDirectory="/workspace/step1",
            listPlots=["Plot/fig.pdf"],
        )],
    }
    listResult = stateContract.flistCanonicalTrackedFiles(
        dictWorkflow, str(tmp_path))
    assert "step1/Plot/fig.pdf" in listResult


# ----------------------------------------------------------------------
# flistOversizedFiles
# ----------------------------------------------------------------------


def test_flistOversizedFiles_returns_empty_when_under_threshold(tmp_path):
    _fnWriteFile(str(tmp_path), "step1/small.csv", "hello")
    listResult = stateContract.flistOversizedFiles(
        ["step1/small.csv"], str(tmp_path), iThresholdBytes=100)
    assert listResult == []


def test_flistOversizedFiles_flags_files_over_threshold(tmp_path):
    _fnWriteFile(str(tmp_path), "step1/big.h5", "x" * 200)
    listResult = stateContract.flistOversizedFiles(
        ["step1/big.h5"], str(tmp_path), iThresholdBytes=100)
    assert listResult == ["step1/big.h5"]


def test_flistOversizedFiles_skips_missing_files(tmp_path):
    listResult = stateContract.flistOversizedFiles(
        ["step1/ghost.h5"], str(tmp_path), iThresholdBytes=10)
    assert listResult == []


def test_flistOversizedFiles_default_threshold_is_50MB():
    assert stateContract.I_LARGE_FILE_THRESHOLD_BYTES == 50 * 1024 * 1024


# ----------------------------------------------------------------------
# fsGenerateGitignore
# ----------------------------------------------------------------------


def test_fsGenerateGitignore_includes_always_ignored_paths():
    sResult = stateContract.fsGenerateGitignore({"listSteps": []})
    for sPath in stateContract.TUPLE_ALWAYS_IGNORED:
        assert sPath in sResult


def test_fsGenerateGitignore_no_longer_blanket_excludes_data_extensions():
    sResult = stateContract.fsGenerateGitignore({"listSteps": []})
    assert "*.npy" not in sResult
    assert "*.h5" not in sResult
    assert "Plot/*.pdf" not in sResult


def test_fsGenerateGitignore_lists_oversized_files():
    sResult = stateContract.fsGenerateGitignore(
        {"listSteps": []},
        listOversized=["step1/huge.h5", "step2/big.npy"],
    )
    assert "step1/huge.h5" in sResult
    assert "step2/big.npy" in sResult


def test_fsGenerateGitignore_deduplicates_oversized_files():
    sResult = stateContract.fsGenerateGitignore(
        {"listSteps": []},
        listOversized=["step1/x.h5", "step1/x.h5"],
    )
    assert sResult.count("step1/x.h5") == 1


def test_fsGenerateGitignore_lists_excluded_files():
    dictWorkflow = {
        "listSteps": [{
            "sDirectory": "step1",
            "dictExcludedFiles": {"step1/drop.pdf": True},
        }],
    }
    sResult = stateContract.fsGenerateGitignore(dictWorkflow)
    assert "step1/drop.pdf" in sResult


def test_fsGenerateGitignore_omits_falsy_excluded_entries():
    dictWorkflow = {
        "listSteps": [{
            "sDirectory": "step1",
            "dictExcludedFiles": {
                "step1/drop.pdf": True,
                "step1/keep.pdf": False,
            },
        }],
    }
    sResult = stateContract.fsGenerateGitignore(dictWorkflow)
    assert "step1/drop.pdf" in sResult
    assert "step1/keep.pdf" not in sResult


def test_fsGenerateGitignore_header_identifies_generator():
    sResult = stateContract.fsGenerateGitignore({"listSteps": []})
    assert "vaibify" in sResult.lower()
    assert "stateContract" in sResult


def test_fsGenerateGitignore_ends_with_newline():
    sResult = stateContract.fsGenerateGitignore({"listSteps": []})
    assert sResult.endswith("\n")
