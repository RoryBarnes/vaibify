"""Tests for the standards generator and round-trip regenerator."""

import json
import os

import numpy as np
import pytest

from vaibify.testing.standards import (
    fdictGenerateQuantitativeStandards,
    fnRegenerateStandardsFile,
    fnWriteStandards,
)


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
