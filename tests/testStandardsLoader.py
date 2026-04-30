"""Tests for the access-path parser and per-format value loaders."""

import json
import os
import tempfile

import numpy as np
import pytest

from vaibify.testing.standards import (
    fdictParseAccessPath,
    fLoadValue,
)


# ---------------------------------------------------------------------------
# Access path parser
# ---------------------------------------------------------------------------


def test_fdictParseAccessPath_simple_index():
    dictResult = fdictParseAccessPath("index:0")
    assert dictResult == {"listIndices": [0]}


def test_fdictParseAccessPath_multi_index():
    dictResult = fdictParseAccessPath("index:0,1")
    assert dictResult == {"listIndices": [0, 1]}


def test_fdictParseAccessPath_negative_index():
    dictResult = fdictParseAccessPath("index:-1")
    assert dictResult == {"listIndices": [-1]}


def test_fdictParseAccessPath_aggregate_mean():
    dictResult = fdictParseAccessPath("index:mean")
    assert dictResult == {"sAggregate": "mean"}


def test_fdictParseAccessPath_aggregate_min_max():
    assert fdictParseAccessPath("index:min")["sAggregate"] == "min"
    assert fdictParseAccessPath("index:max")["sAggregate"] == "max"


def test_fdictParseAccessPath_column_with_index():
    dictResult = fdictParseAccessPath("column:col0,index:0")
    assert dictResult == {"column": "col0", "listIndices": [0]}


def test_fdictParseAccessPath_simple_key():
    dictResult = fdictParseAccessPath("key:foo")
    assert dictResult == {"key": "foo"}


def test_fdictParseAccessPath_key_with_embedded_commas():
    """Keys can contain commas (compound names) — capture greedily."""
    dictResult = fdictParseAccessPath("key:a,b,c,index:0")
    assert dictResult["key"] == "a,b,c"
    assert dictResult["listIndices"] == [0]


def test_fdictParseAccessPath_key_with_aggregate():
    dictResult = fdictParseAccessPath("key:foo,bar,index:mean")
    assert dictResult["key"] == "foo,bar"
    assert dictResult["sAggregate"] == "mean"


# ---------------------------------------------------------------------------
# Numpy value loader
# ---------------------------------------------------------------------------


@pytest.fixture
def fixtureNumpyArray(tmp_path):
    """Write a small npy file under a fake step layout."""
    sStepDir = tmp_path
    daData = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    np.save(os.path.join(sStepDir, "array.npy"), daData)
    return str(sStepDir)


def test_fLoadValue_numpy_index(fixtureNumpyArray):
    fValue = fLoadValue("array.npy", "index:0,0", fixtureNumpyArray)
    assert fValue == 1.0


def test_fLoadValue_numpy_negative_index(fixtureNumpyArray):
    fValue = fLoadValue("array.npy", "index:-1,1", fixtureNumpyArray)
    assert fValue == 6.0


def test_fLoadValue_numpy_mean(fixtureNumpyArray):
    fValue = fLoadValue("array.npy", "index:mean", fixtureNumpyArray)
    assert fValue == pytest.approx(3.5)


def test_fLoadValue_numpy_min_max(fixtureNumpyArray):
    assert fLoadValue("array.npy", "index:min", fixtureNumpyArray) == 1.0
    assert fLoadValue("array.npy", "index:max", fixtureNumpyArray) == 6.0


# ---------------------------------------------------------------------------
# JSON value loader
# ---------------------------------------------------------------------------


@pytest.fixture
def fixtureJsonScalar(tmp_path):
    """Write a JSON file with scalar and list entries."""
    dictPayload = {
        "fScalar": 42.5,
        "listShort": [1.0, 2.0, 3.0],
        "compound,key,name": [10.0, 20.0, 30.0, 40.0],
    }
    sPath = os.path.join(tmp_path, "data.json")
    with open(sPath, "w") as fileHandle:
        json.dump(dictPayload, fileHandle)
    return str(tmp_path)


def test_fLoadValue_json_scalar_key(fixtureJsonScalar):
    fValue = fLoadValue("data.json", "key:fScalar", fixtureJsonScalar)
    assert fValue == 42.5


def test_fLoadValue_json_list_index(fixtureJsonScalar):
    fValue = fLoadValue("data.json", "key:listShort,index:1",
                        fixtureJsonScalar)
    assert fValue == 2.0


def test_fLoadValue_json_compound_key(fixtureJsonScalar):
    """Compound keys with embedded commas are common in vconverge output."""
    fValue = fLoadValue("data.json", "key:compound,key,name,index:0",
                        fixtureJsonScalar)
    assert fValue == 10.0


def test_fLoadValue_json_aggregate_mean(fixtureJsonScalar):
    fValue = fLoadValue("data.json", "key:listShort,index:mean",
                        fixtureJsonScalar)
    assert fValue == pytest.approx(2.0)


def test_fLoadValue_json_doubly_serialised(tmp_path):
    """Vconverge's Converged_Param_Dictionary.json wraps an inner JSON."""
    sInner = json.dumps({"fA": 7.0})
    sPath = os.path.join(tmp_path, "wrapped.json")
    with open(sPath, "w") as fileHandle:
        json.dump(sInner, fileHandle)
    fValue = fLoadValue("wrapped.json", "key:fA", str(tmp_path))
    assert fValue == 7.0


# ---------------------------------------------------------------------------
# Key-value text loader
# ---------------------------------------------------------------------------


def test_fLoadValue_keyvalue_text(tmp_path):
    """Maximum-likelihood-style ``key = value`` text reports."""
    sPath = os.path.join(tmp_path, "report.txt")
    with open(sPath, "w") as fileHandle:
        fileHandle.write("star.dMass = 0.1945\n")
        fileHandle.write("star.dAge = 5.0e9\n")
        fileHandle.write("# comment line that should be ignored\n")
    assert fLoadValue("report.txt", "key:star.dMass",
                      str(tmp_path)) == 0.1945
    assert fLoadValue("report.txt", "key:star.dAge",
                      str(tmp_path)) == 5.0e9


def test_fLoadValue_keyvalue_text_missing_key(tmp_path):
    sPath = os.path.join(tmp_path, "report.txt")
    with open(sPath, "w") as fileHandle:
        fileHandle.write("foo = 1.0\n")
    with pytest.raises(KeyError, match="not found"):
        fLoadValue("report.txt", "key:absent", str(tmp_path))
