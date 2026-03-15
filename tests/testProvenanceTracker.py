"""Tests for vaibcask.reproducibility.provenanceTracker."""

import hashlib
import json

import pytest

from vaibcask.reproducibility.provenanceTracker import (
    fsComputeFileHash,
    fdictBuildDagFromScript,
    flistDetectChangedOutputs,
    fnGenerateDotFile,
    fnSaveProvenance,
    fdictLoadProvenance,
)


def test_fsComputeFileHash_returns_sha256(tmp_path):
    sContent = "Hello, provenance tracking!\n"
    pathFile = tmp_path / "testfile.txt"
    pathFile.write_text(sContent)

    sExpectedHash = hashlib.sha256(
        sContent.encode("utf-8")
    ).hexdigest()

    sActualHash = fsComputeFileHash(str(pathFile))

    assert sActualHash == sExpectedHash
    assert len(sActualHash) == 64


def test_fsComputeFileHash_raises_on_missing_file(tmp_path):
    sMissingPath = str(tmp_path / "nonexistent.dat")

    with pytest.raises(FileNotFoundError):
        fsComputeFileHash(sMissingPath)


def test_fdictBuildDagFromScript():
    dictScript = {
        "listScenes": [
            {
                "sName": "Generate Data",
                "saInputFiles": [],
                "saOutputFiles": ["data.csv"],
            },
            {
                "sName": "Plot Results",
                "saInputFiles": ["data.csv"],
                "saOutputFiles": ["figure.pdf"],
            },
        ],
    }

    dictDag = fdictBuildDagFromScript(dictScript)

    assert "listNodes" in dictDag
    assert "listEdges" in dictDag
    assert len(dictDag["listNodes"]) == 2
    assert "Generate Data" in dictDag["listNodes"]
    assert "Plot Results" in dictDag["listNodes"]

    listEdges = dictDag["listEdges"]
    bFoundDataEdgeOut = any(
        e["sFrom"] == "Generate Data" and e["sTo"] == "data.csv"
        for e in listEdges
    )
    assert bFoundDataEdgeOut

    bFoundDataEdgeIn = any(
        e["sFrom"] == "data.csv" and e["sTo"] == "Plot Results"
        for e in listEdges
    )
    assert bFoundDataEdgeIn


def test_fdictBuildDagFromScript_empty():
    dictScript = {"listScenes": []}

    dictDag = fdictBuildDagFromScript(dictScript)

    assert dictDag["listNodes"] == []
    assert dictDag["listEdges"] == []


def test_flistDetectChangedOutputs(tmp_path):
    pathOutput = tmp_path / "result.csv"
    sOriginalContent = "x,y\n1,2\n3,4\n"
    pathOutput.write_text(sOriginalContent)

    sOriginalHash = hashlib.sha256(
        sOriginalContent.encode("utf-8")
    ).hexdigest()

    dictProvenance = {
        "dictFileHashes": {
            str(pathOutput): sOriginalHash,
        },
    }
    dictScript = {
        "listScenes": [
            {
                "sName": "Compute",
                "saInputFiles": [],
                "saOutputFiles": [str(pathOutput)],
            },
        ],
    }

    listChanged = flistDetectChangedOutputs(dictProvenance, dictScript)
    assert listChanged == []

    pathOutput.write_text("x,y\n1,2\n3,999\n")

    listChanged = flistDetectChangedOutputs(dictProvenance, dictScript)
    assert str(pathOutput) in listChanged


def test_flistDetectChangedOutputs_missing_file(tmp_path):
    sMissingPath = str(tmp_path / "deleted.csv")

    dictProvenance = {
        "dictFileHashes": {
            sMissingPath: "abc123",
        },
    }
    dictScript = {
        "listScenes": [
            {
                "sName": "Gone",
                "saInputFiles": [],
                "saOutputFiles": [sMissingPath],
            },
        ],
    }

    listChanged = flistDetectChangedOutputs(dictProvenance, dictScript)

    assert sMissingPath in listChanged


def test_fnGenerateDotFile_creates_valid_dot(tmp_path):
    dictProvenance = {
        "saScenes": ["Generate", "Plot"],
        "dictFileHashes": {
            "data.csv": "aaa",
            "figure.pdf": "bbb",
        },
    }
    sOutputPath = str(tmp_path / "provenance.dot")

    fnGenerateDotFile(dictProvenance, sOutputPath)

    with open(sOutputPath, "r") as fileHandle:
        sContent = fileHandle.read()

    assert "digraph provenance {" in sContent
    assert "}" in sContent
    assert "Generate" in sContent
    assert "Plot" in sContent
    assert "data.csv" in sContent
    assert "figure.pdf" in sContent


def test_roundtrip_save_load(tmp_path):
    dictOriginal = {
        "saScenes": ["SceneA", "SceneB"],
        "dictFileHashes": {
            "/workspace/output/a.pdf": "hash_a",
            "/workspace/output/b.csv": "hash_b",
        },
        "sTimestamp": "2026-03-15T00:00:00+00:00",
    }
    sFilePath = str(tmp_path / "provenance.json")

    fnSaveProvenance(dictOriginal, sFilePath)
    dictLoaded = fdictLoadProvenance(sFilePath)

    assert dictLoaded["saScenes"] == dictOriginal["saScenes"]
    assert (
        dictLoaded["dictFileHashes"]
        == dictOriginal["dictFileHashes"]
    )
    assert dictLoaded["sTimestamp"] == dictOriginal["sTimestamp"]


def test_fdictLoadProvenance_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        fdictLoadProvenance(str(tmp_path / "missing.json"))
