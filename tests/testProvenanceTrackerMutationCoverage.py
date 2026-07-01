"""Mutation-coverage tests for provenanceTracker.fnUpdateProvenance.

Each test closes a specific surviving-mutant hole found by mutation
testing. Collectively they pin down that fnUpdateProvenance hashes the
correct artefacts (saPlotFiles, not saInputFiles), stores the computed
hashes (not an empty dict), records step identity by sName (not sId),
and stamps a real ISO 8601 timestamp.
"""

import hashlib
from datetime import datetime

import pytest

from vaibify.reproducibility.provenanceTracker import fnUpdateProvenance

pytestmark = pytest.mark.falsification


def _tdictBuildWorkflowWithPlotAndInput(tmp_path):
    """Create a real plot file and a distinct input file on disk.

    Returns a tuple of (dictWorkflow, sPlotPath, sInputPath,
    sExpectedPlotHash).
    """
    sPlotContent = "x,y\n1,2\n3,4\n"
    sInputContent = "raw,data\n9,9\n"
    pathPlot = tmp_path / "figure.csv"
    pathInput = tmp_path / "input.csv"
    pathPlot.write_text(sPlotContent)
    pathInput.write_text(sInputContent)

    sExpectedPlotHash = hashlib.sha256(
        sPlotContent.encode("utf-8")
    ).hexdigest()

    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Plot Results",
                "sId": "stepId123",
                "saInputFiles": [str(pathInput)],
                "saPlotFiles": [str(pathPlot)],
            },
        ],
    }
    return dictWorkflow, str(pathPlot), str(pathInput), sExpectedPlotHash


def test_fnUpdateProvenance_hashes_plot_files_not_input_files(tmp_path):
    """Kills: In _fnHashStepOutputs (line 233) iterate dictStep['saInputFiles'] instead of dictStep['saPlotFiles']."""
    dictWorkflow, sPlotPath, sInputPath, sExpectedPlotHash = (
        _tdictBuildWorkflowWithPlotAndInput(tmp_path)
    )
    dictProvenance = {}

    fnUpdateProvenance(dictProvenance, dictWorkflow, str(tmp_path))

    dictFileHashes = dictProvenance["dictFileHashes"]
    assert sPlotPath in dictFileHashes
    assert dictFileHashes[sPlotPath] == sExpectedPlotHash
    assert sInputPath not in dictFileHashes


def test_fnUpdateProvenance_stores_computed_hashes_not_empty(tmp_path):
    """Kills: In fnUpdateProvenance (line 227) assign dictProvenance['dictFileHashes'] = {} instead of the computed dictHashes."""
    dictWorkflow, sPlotPath, _, sExpectedPlotHash = (
        _tdictBuildWorkflowWithPlotAndInput(tmp_path)
    )
    dictProvenance = {}

    fnUpdateProvenance(dictProvenance, dictWorkflow, str(tmp_path))

    dictFileHashes = dictProvenance["dictFileHashes"]
    assert dictFileHashes != {}
    assert dictFileHashes == {sPlotPath: sExpectedPlotHash}


def test_fnUpdateProvenance_records_step_identity_by_sname(tmp_path):
    """Kills: fnUpdateProvenance (line 224) records step identity from dictStep.get('sId','unknown') instead of dictStep.get('sName','unknown')."""
    sPlotContentOne = "alpha\n"
    sPlotContentTwo = "beta\n"
    pathPlotOne = tmp_path / "a.csv"
    pathPlotTwo = tmp_path / "b.csv"
    pathPlotOne.write_text(sPlotContentOne)
    pathPlotTwo.write_text(sPlotContentTwo)

    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Generate Data",
                "sId": "id-1",
                "saInputFiles": [],
                "saPlotFiles": [str(pathPlotOne)],
            },
            {
                "sName": "Plot Results",
                "sId": "id-2",
                "saInputFiles": [],
                "saPlotFiles": [str(pathPlotTwo)],
            },
        ],
    }
    dictProvenance = {}

    fnUpdateProvenance(dictProvenance, dictWorkflow, str(tmp_path))

    assert dictProvenance["saSteps"] == ["Generate Data", "Plot Results"]


def test_fnUpdateProvenance_stamps_real_timestamp(tmp_path):
    """Kills: fnUpdateProvenance (line 228) sets dictProvenance['sTimestamp'] = '' instead of _fsCurrentTimestamp()."""
    dictWorkflow, _, _, _ = _tdictBuildWorkflowWithPlotAndInput(tmp_path)
    dictProvenance = {}

    fnUpdateProvenance(dictProvenance, dictWorkflow, str(tmp_path))

    sTimestamp = dictProvenance["sTimestamp"]
    assert isinstance(sTimestamp, str)
    assert sTimestamp != ""
    # A real ISO 8601 timestamp parses without error.
    datetime.fromisoformat(sTimestamp)
