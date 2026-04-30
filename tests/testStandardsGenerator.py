"""Tests for the standards generator and round-trip regenerator."""

import json
import os

import numpy as np
import pytest

from vaibify.testing.standards import (
    fdictGenerateQuantitativeStandards,
    fnGenerateFromWorkflow,
    fnRegenerateStandardsFile,
    fnUpdateWorkflowStandards,
    fnWriteStandards,
)
from vaibify.testing import standards as standardsModule


@pytest.fixture
def fixtureStepWithNpy(tmp_path):
    """Create a fake step directory with a multi-column .npy."""
    daData = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]])
    np.save(os.path.join(tmp_path, "samples.npy"), daData)
    return str(tmp_path)


def test_fdictGenerateQuantitativeStandards_npy(fixtureStepWithNpy):
    """Multi-column .npy yields global aggregates plus per-column first/last."""
    dictResult = fdictGenerateQuantitativeStandards(
        fixtureStepWithNpy, ["samples.npy"], fDefaultRtol=1e-9)
    assert dictResult["fDefaultRtol"] == 1e-9
    listValues = {dictStd["sName"]: dictStd["fValue"]
                  for dictStd in dictResult["listStandards"]}
    assert "fsamplesMean" in listValues
    assert listValues["fsamplesMean"] == pytest.approx(11.0)
    assert listValues["fsamplesMin"] == 1.0
    assert listValues["fsamplesMax"] == 30.0


def test_fdictGenerateQuantitativeStandards_missing_file(tmp_path):
    """Missing data files are skipped with a warning, not a crash."""
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["does_not_exist.npy"])
    assert dictResult["listStandards"] == []


def test_fnWriteStandards_creates_dirs(tmp_path):
    sPath = os.path.join(tmp_path, "tests", "quantitative_standards.json")
    dictStandards = {
        "fDefaultRtol": 1e-6,
        "listStandards": [{
            "sName": "fA", "sDataFile": "x.npy",
            "sAccessPath": "index:0", "fValue": 1.0, "sUnit": "",
        }],
    }
    fnWriteStandards(dictStandards, sPath)
    assert os.path.isfile(sPath)
    with open(sPath) as fileHandle:
        dictRoundTripped = json.load(fileHandle)
    assert dictRoundTripped == dictStandards


def test_fnRegenerateStandardsFile_round_trip(tmp_path):
    """Round-trip: generate → modify data → regenerate → values track data."""
    np.save(os.path.join(tmp_path, "x.npy"),
            np.array([10.0, 20.0, 30.0]))
    sStandardsDir = os.path.join(tmp_path, "tests")
    os.makedirs(sStandardsDir, exist_ok=True)
    sStandardsPath = os.path.join(sStandardsDir, "quantitative_standards.json")
    dictBefore = {
        "fDefaultRtol": 1e-12,
        "listStandards": [
            {"sName": "fxFirst", "sDataFile": "x.npy",
             "sAccessPath": "index:0", "fValue": 0.0, "sUnit": ""},
            {"sName": "fxMean", "sDataFile": "x.npy",
             "sAccessPath": "index:mean", "fValue": 0.0, "sUnit": ""},
        ],
    }
    with open(sStandardsPath, "w") as fileHandle:
        json.dump(dictBefore, fileHandle)
    fnRegenerateStandardsFile(sStandardsPath, str(tmp_path))
    with open(sStandardsPath) as fileHandle:
        dictAfter = json.load(fileHandle)
    listAfter = {dictStd["sName"]: dictStd["fValue"]
                 for dictStd in dictAfter["listStandards"]}
    assert listAfter["fxFirst"] == 10.0
    assert listAfter["fxMean"] == pytest.approx(20.0)


def test_fnRegenerateStandardsFile_preserves_schema(tmp_path):
    """Regeneration must preserve fRtol, sUnit, and entry order."""
    np.save(os.path.join(tmp_path, "x.npy"), np.array([1.0]))
    sStandardsDir = os.path.join(tmp_path, "tests")
    os.makedirs(sStandardsDir, exist_ok=True)
    sStandardsPath = os.path.join(sStandardsDir, "quantitative_standards.json")
    dictBefore = {
        "fDefaultRtol": 1e-12,
        "listStandards": [{
            "sName": "fxOnly", "sDataFile": "x.npy",
            "sAccessPath": "index:0", "fValue": 999.0,
            "fRtol": 1e-3, "sUnit": "kg",
        }],
    }
    with open(sStandardsPath, "w") as fileHandle:
        json.dump(dictBefore, fileHandle)
    fnRegenerateStandardsFile(sStandardsPath, str(tmp_path))
    with open(sStandardsPath) as fileHandle:
        dictAfter = json.load(fileHandle)
    dictEntry = dictAfter["listStandards"][0]
    assert dictEntry["fValue"] == 1.0
    assert dictEntry["fRtol"] == 1e-3
    assert dictEntry["sUnit"] == "kg"
    assert dictAfter["fDefaultRtol"] == 1e-12


# ---------------------------------------------------------------------------
# 1-D npy reshape, whitespace, csv loaders
# ---------------------------------------------------------------------------


def test_fdictGenerateQuantitativeStandards_npy_1d(tmp_path):
    """1-D npy is reshaped to (N, 1) and yields col0 per-column stats."""
    np.save(os.path.join(tmp_path, "vec.npy"),
            np.array([2.0, 4.0, 6.0, 8.0]))
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["vec.npy"])
    dictByName = {d["sName"]: d["fValue"]
                  for d in dictResult["listStandards"]}
    assert dictByName["fcol0Mean"] == pytest.approx(5.0)
    assert dictByName["fcol0First"] == 2.0
    assert dictByName["fcol0Last"] == 8.0


def test_fdictGenerateQuantitativeStandards_csv(tmp_path):
    """CSV with a header row produces per-column stats with column: prefix."""
    sPath = os.path.join(tmp_path, "table.csv")
    with open(sPath, "w") as fileHandle:
        fileHandle.write("a,b\n1.0,10.0\n2.0,20.0\n3.0,30.0\n")
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["table.csv"])
    listEntries = dictResult["listStandards"]
    setAccess = {d["sAccessPath"] for d in listEntries}
    assert "column:col0,index:mean" in setAccess
    assert "column:col1,index:max" in setAccess


def test_fdictGenerateQuantitativeStandards_whitespace(tmp_path):
    """Whitespace-delimited numeric text yields per-column stats."""
    sPath = os.path.join(tmp_path, "values.dat")
    with open(sPath, "w") as fileHandle:
        fileHandle.write("1.0 10.0\n2.0 20.0\n3.0 30.0\n")
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["values.dat"])
    listEntries = dictResult["listStandards"]
    assert any(d["sAccessPath"] == "column:col0,index:mean"
               for d in listEntries)


def test_fdictGenerateQuantitativeStandards_keyvalue_fallback(tmp_path):
    """Non-numeric text falls back to the key=value parser."""
    sPath = os.path.join(tmp_path, "report.txt")
    with open(sPath, "w") as fileHandle:
        fileHandle.write("dMass = 0.5\n")
        fileHandle.write("dRadius = 1.2e9\n")
        fileHandle.write("# ignored comment line\n")
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["report.txt"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "fdMass" in setNames
    assert "fdRadius" in setNames


# ---------------------------------------------------------------------------
# JSON standards builders (scalar, short list, long list)
# ---------------------------------------------------------------------------


def test_fdictGenerateQuantitativeStandards_json_scalar_and_short_list(tmp_path):
    """JSON scalars produce one entry; short lists yield per-index entries."""
    sPath = os.path.join(tmp_path, "data.json")
    dictPayload = {"fAlpha": 1.5, "listShort": [10.0, 20.0, 30.0]}
    with open(sPath, "w") as fileHandle:
        json.dump(dictPayload, fileHandle)
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["data.json"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "ffAlpha" in setNames
    assert "flistShort_0" in setNames
    assert "flistShort_2" in setNames


def test_fdictGenerateQuantitativeStandards_json_long_list_aggregates(tmp_path):
    """Long JSON lists (>10 entries) emit aggregate stats, not per-index."""
    sPath = os.path.join(tmp_path, "long.json")
    listLong = [float(i) for i in range(20)]
    with open(sPath, "w") as fileHandle:
        json.dump({"daSamples": listLong}, fileHandle)
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["long.json"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "fdaSamples_Mean" in setNames
    assert "fdaSamples_First" in setNames
    assert "fdaSamples_5" not in setNames


def test_json_skips_non_numeric_and_infinite_values(tmp_path):
    """Non-numeric scalars and NaN/Inf entries must be skipped."""
    sPath = os.path.join(tmp_path, "mixed.json")
    dictPayload = {
        "sLabel": "ignored", "fGood": 1.0,
        "fNan": float("nan"), "fInf": float("inf"),
    }
    with open(sPath, "w") as fileHandle:
        json.dump(dictPayload, fileHandle)
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["mixed.json"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "ffGood" in setNames
    assert "ffNan" not in setNames
    assert "ffInf" not in setNames


# ---------------------------------------------------------------------------
# NPZ scalar / 1-D / 2-D dispatcher
# ---------------------------------------------------------------------------


def test_npz_scalar_array_emits_single_entry(tmp_path):
    """0-d arrays inside an npz produce one entry keyed by archive name."""
    sPath = os.path.join(tmp_path, "scalar.npz")
    np.savez(sPath, fValue=np.array(3.14))
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["scalar.npz"])
    listEntries = dictResult["listStandards"]
    assert len(listEntries) == 1
    assert listEntries[0]["sName"] == "ffValue"
    assert listEntries[0]["fValue"] == pytest.approx(3.14)


def test_npz_1d_array_emits_first_last_mean_min_max(tmp_path):
    """1-D arrays inside an npz emit per-key stat suite."""
    sPath = os.path.join(tmp_path, "vector.npz")
    np.savez(sPath, daX=np.array([1.0, 2.0, 3.0, 4.0]))
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["vector.npz"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert {"fdaXFirst", "fdaXLast", "fdaXMean",
            "fdaXMin", "fdaXMax"} <= setNames


def test_npz_2d_array_emits_global_aggregates_and_per_column(tmp_path):
    """2-D arrays inside an npz emit global aggregates + per-col first/last."""
    sPath = os.path.join(tmp_path, "matrix.npz")
    np.savez(sPath, daM=np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]]))
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["matrix.npz"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "fdaMMean" in setNames
    assert "fdaM0First" in setNames
    assert "fdaM1Last" in setNames


# ---------------------------------------------------------------------------
# Unsupported format & warnings
# ---------------------------------------------------------------------------


def test_unsupported_format_logs_warning_and_returns_empty(tmp_path, capsys):
    """A binary format with no loader emits a warning and zero entries."""
    sPath = os.path.join(tmp_path, "blob.h5")
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(b"\x89HDF\r\n\x1a\n")
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["blob.h5"])
    assert dictResult["listStandards"] == []
    captured = capsys.readouterr()
    assert "unsupported format" in captured.out.lower() or \
        "warning" in captured.out.lower()


def test_keyvalue_text_unparseable_warns(tmp_path, capsys):
    """A text file that's neither numeric nor key=value emits a warning."""
    sPath = os.path.join(tmp_path, "junk.dat")
    with open(sPath, "w") as fileHandle:
        fileHandle.write("hello world\nthis is not parseable\n")
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["junk.dat"])
    assert dictResult["listStandards"] == []
    captured = capsys.readouterr()
    assert "could not parse" in captured.out.lower()


# ---------------------------------------------------------------------------
# fnUpdateWorkflowStandards & fnGenerateFromWorkflow
# ---------------------------------------------------------------------------


@pytest.fixture
def fixtureWorkflow(tmp_path):
    """Lay out a minimal workflow JSON with one step that has a data file."""
    sStepDir = tmp_path / "stepA"
    sStepDir.mkdir()
    np.save(os.path.join(str(sStepDir), "result.npy"),
            np.array([1.0, 2.0, 3.0]))
    sWorkflowPath = os.path.join(str(tmp_path), "workflow.json")
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Compute", "sDirectory": "stepA",
            "saPlotCommands": [], "saPlotFiles": [],
            "saDataCommands": ["python run.py"],
            "saDataFiles": ["result.npy"],
        }],
    }
    with open(sWorkflowPath, "w") as fileHandle:
        json.dump(dictWorkflow, fileHandle)
    return sWorkflowPath


def test_fnUpdateWorkflowStandards_persists_blob(fixtureWorkflow):
    """fnUpdateWorkflowStandards inlines the JSON content into the step."""
    fnUpdateWorkflowStandards(fixtureWorkflow, 0, '{"x": 1}')
    with open(fixtureWorkflow) as fileHandle:
        dictWorkflow = json.load(fileHandle)
    dictTests = dictWorkflow["listSteps"][0]["dictTests"]
    assert dictTests["dictQuantitative"]["sStandardsContent"] == '{"x": 1}'


def test_fnGenerateFromWorkflow_writes_standards_file(fixtureWorkflow):
    """End-to-end: workflow → step standards JSON written under tests/."""
    fnGenerateFromWorkflow(fixtureWorkflow, 0)
    sStandardsPath = os.path.join(
        os.path.dirname(fixtureWorkflow), "stepA", "tests",
        "quantitative_standards.json",
    )
    assert os.path.isfile(sStandardsPath)
    with open(sStandardsPath) as fileHandle:
        dictStandards = json.load(fileHandle)
    assert len(dictStandards["listStandards"]) > 0


def test_fnGenerateFromWorkflow_no_data_files_returns_early(tmp_path, capsys):
    """A step with no saDataFiles is a no-op and prints a skip message."""
    sWorkflowPath = os.path.join(str(tmp_path), "workflow.json")
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Empty", "sDirectory": "stepEmpty",
            "saPlotCommands": [], "saPlotFiles": [],
            "saDataFiles": [],
        }],
    }
    with open(sWorkflowPath, "w") as fileHandle:
        json.dump(dictWorkflow, fileHandle)
    fnGenerateFromWorkflow(sWorkflowPath, 0)
    captured = capsys.readouterr()
    assert "no data files" in captured.out.lower()


def test_fnGenerateFromWorkflow_check_seeds_invokes_detector(
    tmp_path, capsys,
):
    """bCheckSeeds=True runs the stochastic detector against data scripts."""
    sStepDir = tmp_path / "stepSeed"
    sStepDir.mkdir()
    sScript = os.path.join(str(sStepDir), "run.py")
    with open(sScript, "w") as fileHandle:
        fileHandle.write(
            "import numpy as np\n"
            "x = np.random.normal(size=10)\n"
        )
    np.save(os.path.join(str(sStepDir), "out.npy"), np.array([1.0]))
    sWorkflowPath = os.path.join(str(tmp_path), "workflow.json")
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S", "sDirectory": "stepSeed",
            "saPlotCommands": [], "saPlotFiles": [],
            "saDataCommands": ["python run.py"],
            "saDataFiles": ["out.npy"],
        }],
    }
    with open(sWorkflowPath, "w") as fileHandle:
        json.dump(dictWorkflow, fileHandle)
    fnGenerateFromWorkflow(sWorkflowPath, 0, bCheckSeeds=True)
    captured = capsys.readouterr()
    assert "Stochastic Detection Report" in captured.out


# ---------------------------------------------------------------------------
# _fsResolveStepDir fallback
# ---------------------------------------------------------------------------


def test_resolve_step_dir_handles_nested_workflow_layout(tmp_path):
    """When the workflow lives under .vaibify/workflows/, step dirs resolve
    against the project repo root, not the workflow file's parent."""
    sRepoRoot = tmp_path
    sStepDir = sRepoRoot / "stepNested"
    sStepDir.mkdir()
    np.save(os.path.join(str(sStepDir), "out.npy"), np.array([5.0]))
    sWorkflowDir = sRepoRoot / ".vaibify" / "workflows"
    sWorkflowDir.mkdir(parents=True)
    sWorkflowPath = os.path.join(str(sWorkflowDir), "main.json")
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S", "sDirectory": "stepNested",
            "saPlotCommands": [], "saPlotFiles": [],
            "saDataFiles": ["out.npy"],
        }],
    }
    with open(sWorkflowPath, "w") as fileHandle:
        json.dump(dictWorkflow, fileHandle)
    fnGenerateFromWorkflow(sWorkflowPath, 0)
    sStandardsPath = os.path.join(
        str(sStepDir), "tests", "quantitative_standards.json",
    )
    assert os.path.isfile(sStandardsPath)


def test_doubly_serialised_json_is_unwrapped_for_standards(tmp_path):
    """vconverge's wrapped-JSON layout (inner JSON-as-string) generates standards."""
    sInner = json.dumps({"fA": 1.5, "listShort": [10.0, 20.0, 30.0]})
    sPath = os.path.join(str(tmp_path), "wrapped.json")
    with open(sPath, "w") as fileHandle:
        json.dump(sInner, fileHandle)
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["wrapped.json"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "ffA" in setNames
    assert "flistShort_0" in setNames


def test_npz_scalar_nan_is_skipped(tmp_path):
    """A NaN scalar in an npz emits no standards entry."""
    sPath = os.path.join(str(tmp_path), "nan.npz")
    np.savez(sPath, fNoisy=np.array(float("nan")))
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["nan.npz"])
    assert dictResult["listStandards"] == []


def test_listColumnStats_drops_nonfinite_entries(tmp_path):
    """First/last NaN values are filtered out of column stats."""
    daData = np.array([[float("nan"), 1.0], [2.0, 2.0], [3.0, 3.0]])
    np.save(os.path.join(str(tmp_path), "withnan.npy"), daData)
    dictResult = fdictGenerateQuantitativeStandards(
        str(tmp_path), ["withnan.npy"])
    setNames = {d["sName"] for d in dictResult["listStandards"]}
    assert "fwithnan0First" not in setNames or any(
        d["sName"] == "fwithnan0First"
        and not np.isnan(d["fValue"])
        for d in dictResult["listStandards"]
    )
