"""Mutation-coverage tests for vaibify.gui.director.

Each test closes a specific coverage hole found by mutation testing:
the host-path dataset-containment guard (sibling-prefix without os.sep),
saPlotFiles/saPlotCommands required-field validation, the bPlotOnly
default, the single-core core-count floor, and the small-file warning
threshold.
"""

import os
import tempfile

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.falsification

from vaibify.gui.director import (
    fbValidateWorkflow,
    fiResolveCoreCount,
    fnExecuteStep,
    fnDownloadDatasets,
    _fnRegisterFiles,
)


# -----------------------------------------------------------------------
# _fbDatasetPathInsideRoot — sibling-prefix containment guard
# -----------------------------------------------------------------------


@patch("vaibify.gui.director._fnDownloadFromZenodo")
def test_fnDownloadDatasets_refuses_sibling_prefix_destination(
    mockDownload, tmp_path, capsys,
):
    """A sibling dir sharing the root's name prefix must be refused.

    Kills: Drop the os.sep in the containment guard of
    _fbDatasetPathInsideRoot (line 545): startswith(sRootReal) instead of
    startswith(sRootReal + os.sep).
    """
    sSibling = tmp_path.parent / (tmp_path.name + "-evil")
    sSibling.mkdir()
    dictWorkflow = {
        "listDatasets": [{
            "sDoi": "10.5281/zenodo.123",
            "sFileName": "data.hdf5",
            "sDestination": os.path.join("..", tmp_path.name + "-evil"),
        }],
    }
    fnDownloadDatasets(dictWorkflow, str(tmp_path))
    sOutput = capsys.readouterr().out
    assert "refusing dataset write outside repo" in sOutput
    mockDownload.assert_not_called()


# -----------------------------------------------------------------------
# fbValidateWorkflow — per-field required validation
# -----------------------------------------------------------------------


def test_fbValidateWorkflow_requires_saPlotFiles():
    """A step missing only saPlotFiles must be rejected.

    Kills: Drop 'saPlotFiles' from the required-field tuple in
    fbValidateWorkflow (line 137).
    """
    dictWorkflow = {"listSteps": [{
        "sName": "Test",
        "sDirectory": "sub",
        "saPlotCommands": ["echo"],
    }]}
    assert fbValidateWorkflow(dictWorkflow) is False


def test_fbValidateWorkflow_requires_saPlotCommands():
    """A step missing only saPlotCommands must be rejected.

    Kills: Drop 'saPlotCommands' from the required-field tuple in
    fbValidateWorkflow (line 137).
    """
    dictWorkflow = {"listSteps": [{
        "sName": "Test",
        "sDirectory": "sub",
        "saPlotFiles": [],
    }]}
    assert fbValidateWorkflow(dictWorkflow) is False


# -----------------------------------------------------------------------
# fnExecuteStep — bPlotOnly default is True
# -----------------------------------------------------------------------


@patch("vaibify.gui.director.fnExecuteCommand")
def test_fnExecuteStep_defaults_to_plot_only(mockExecute):
    """An omitted bPlotOnly defaults to True: data commands are skipped.

    Kills: Flip bPlotOnly's default from True to False in fnExecuteStep
    (line 295: dictStep.get('bPlotOnly', False)).
    """
    dictStep = {
        "sName": "Test",
        "sDirectory": ".",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "saPlotFiles": [],
    }
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictVars = {"sFigureType": "pdf"}
        fnExecuteStep(dictStep, dictVars, sTmpDir)
        listCalls = [c[0][0] for c in mockExecute.call_args_list]
        assert "python data.py" not in listCalls
        assert "python plot.py" in listCalls


# -----------------------------------------------------------------------
# fiResolveCoreCount — single-core floor
# -----------------------------------------------------------------------


def test_fiResolveCoreCount_floors_at_one_on_single_core():
    """On a 1-CPU host, auto (-1) must floor at 1, never 0.

    Kills: Remove the max(1, ...) floor in the -1 auto branch of
    fiResolveCoreCount (line 163: return iTotal - 1).
    """
    with patch(
        "vaibify.gui.director.multiprocessing.cpu_count",
        return_value=1,
    ):
        assert fiResolveCoreCount(-1) == 1


# -----------------------------------------------------------------------
# _fnRegisterFiles — small-file warning threshold is 1024 bytes
# -----------------------------------------------------------------------


def test_fnRegisterFiles_small_file_threshold_boundary(capsys):
    """A 500-byte file warns; a 1024-byte file does not.

    Kills: Change the small-file warning threshold from 1024 to 100 bytes
    in _fnRegisterFiles (line 326).
    """
    with tempfile.TemporaryDirectory() as sTmpDir:
        sSmallPath = os.path.join(sTmpDir, "small.pdf")
        with open(sSmallPath, "wb") as fh:
            fh.write(b"x" * 500)
        _fnRegisterFiles(
            {"small": "small.pdf"}, {}, "Step01", sTmpDir)
        assert "WARNING" in capsys.readouterr().out

        sBoundaryPath = os.path.join(sTmpDir, "boundary.pdf")
        with open(sBoundaryPath, "wb") as fh:
            fh.write(b"x" * 1024)
        _fnRegisterFiles(
            {"boundary": "boundary.pdf"}, {}, "Step01", sTmpDir)
        assert "WARNING" not in capsys.readouterr().out
