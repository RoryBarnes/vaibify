"""A level the dashboard shows must be one the project actually holds.

Two ways the display overstated, both found by driving the real gates:

* The scalar Level 3 gate never consulted the per-step blockers, so a
  project whose binary had drifted from its captured hash (or whose pinned
  step script had been deleted) read Level 3 while its own step row listed
  ``binary-drifted`` / ``script-not-pinned``.
* A host project's single ``host-mode`` blocker sat in no criteria tuple,
  so its workflow cell read ``attained 12/12``, its step cells read
  ``attained``, and the ratchet stamped a Level 3 high-water mark for a
  level host mode can never hold.
"""

import hashlib
import json
import os

import pytest

from tests.levelGateStubs import fnMakeEveryLevel3ConjunctPass
from vaibify.gui import stateManager
from vaibify.reproducibility import levelGates
from vaibify.reproducibility.levelGates import (
    fbAtLeastLevel3,
    fdictComputeStepLevelStates,
    fdictComputeWorkflowScopeLevelStates,
    flistLevel3Blockers,
)


def _fnWrite(sRepo, sRelative, baContent):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(baContent)
    return hashlib.sha256(baContent).hexdigest()


def _fnWriteManifest(sRepo, dictHashByPath):
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileHandle:
        for sPath, sHash in dictHashByPath.items():
            fileHandle.write(f"{sHash}  {sPath}\n")


def _fnWriteCapturedBinary(sRepo, sBinaryPath, sCapturedHash):
    sDirectory = os.path.join(sRepo, ".vaibify")
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "environment.json"), "w") as fileOut:
        json.dump({"dictHostBinaries": {"listBinaries": [{
            "sBinaryPath": sBinaryPath, "sSha256": sCapturedHash,
            "sVersion": "3.0",
        }]}}, fileOut)


def _fdictWorkflowWithOneStep(sRepo, sBinaryPath=None):
    dictWorkflow = {
        "sProjectRepoPath": sRepo,
        "bNoStandaloneBinaries": sBinaryPath is None,
        "listDeclaredBinaries": [] if sBinaryPath is None else [{
            "sBinaryPath": sBinaryPath, "sPurpose": "forward model",
            "sExpectedVersion": "3.0",
        }],
        "listSteps": [{
            "sName": "Simulate", "sDirectory": "sim",
            "saDataCommands": ["python make.py"],
            "saOutputDataFiles": ["out.json"],
            "saBinaryDependencies": [] if sBinaryPath is None else [
                os.path.basename(sBinaryPath)],
            "bNoInputData": True,
            "dictVerification": {"sUser": "passed", "sUnitTest": "passed"},
            "dictRunStats": {"fWallClock": 1.0},
        }],
    }
    return dictWorkflow


@pytest.fixture
def sDriftedBinaryRepo(tmp_path):
    """A repo whose one binary no longer matches the hash captured for it."""
    sRepo = str(tmp_path / "repo")
    sBinary = str(tmp_path / "bin" / "forwardModel")
    os.makedirs(os.path.dirname(sBinary))
    with open(sBinary, "wb") as fileBinary:
        fileBinary.write(b"rebuilt binary")
    sOutHash = _fnWrite(sRepo, "sim/out.json", b"{}")
    sScriptHash = _fnWrite(sRepo, "sim/make.py", b"print(1)\n")
    _fnWriteManifest(sRepo, {
        "sim/out.json": sOutHash, "sim/make.py": sScriptHash,
    })
    _fnWriteCapturedBinary(sRepo, sBinary, "0" * 64)
    return sRepo, sBinary


@pytest.mark.falsification
def testADriftedBinaryDeniesTheScalarLevelThreeAsItsRowDoes(
    monkeypatch, sDriftedBinaryRepo,
):
    """The step row says ``binary-drifted``; the scalar must agree.

    Every workflow-scope conjunct is stubbed true, so the only thing that
    can refuse Level 3 here is the per-step binary criterion.

    Kills: levelGates.fbAtLeastLevel3: the per-step blocker conjunct
    `if _flistStepScopeBlockers(flistLevel3Blockers(...)): return False`
    removed.
    """
    sRepo, sBinary = sDriftedBinaryRepo
    dictWorkflow = _fdictWorkflowWithOneStep(sRepo, sBinary)
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    listCriteria = [
        dictEntry["sCriterion"]
        for dictEntry in flistLevel3Blockers(dictWorkflow, sRepo, False)
    ]
    assert "binary-drifted" in listCriteria, listCriteria
    assert fbAtLeastLevel3(dictWorkflow, sRepo, False) is False


@pytest.mark.falsification
def testADeletedPinnedStepScriptDeniesTheScalarLevelThree(
    monkeypatch, sDriftedBinaryRepo,
):
    """A pinned script that vanished is the same class as a drifted binary.

    Kills: the same per-step blocker conjunct in fbAtLeastLevel3.
    """
    sRepo, _sBinary = sDriftedBinaryRepo
    dictWorkflow = _fdictWorkflowWithOneStep(sRepo)
    _fnWriteCapturedBinary(sRepo, "/unused", "0" * 64)
    os.remove(os.path.join(sRepo, "sim", "make.py"))
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    listCriteria = [
        dictEntry["sCriterion"]
        for dictEntry in flistLevel3Blockers(dictWorkflow, sRepo, False)
    ]
    assert "script-not-pinned" in listCriteria, listCriteria
    assert fbAtLeastLevel3(dictWorkflow, sRepo, False) is False


def testACleanProjectStillAttainsTheScalarLevelThree(
    monkeypatch, tmp_path,
):
    """The complement: the new conjunct must not refuse a clean project."""
    sRepo = str(tmp_path / "repo")
    sOutHash = _fnWrite(sRepo, "sim/out.json", b"{}")
    sScriptHash = _fnWrite(sRepo, "sim/make.py", b"print(1)\n")
    _fnWriteManifest(sRepo, {
        "sim/out.json": sOutHash, "sim/make.py": sScriptHash,
    })
    dictWorkflow = _fdictWorkflowWithOneStep(sRepo)
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    assert flistLevel3Blockers(dictWorkflow, sRepo, False) == []
    assert fbAtLeastLevel3(dictWorkflow, sRepo, False) is True


def testTheStepCellAndTheScalarFailTogetherOnADriftedBinary(
    monkeypatch, sDriftedBinaryRepo,
):
    """Cell and gate agree: the step's L3 cell is not attained either."""
    sRepo, sBinary = sDriftedBinaryRepo
    dictWorkflow = _fdictWorkflowWithOneStep(sRepo, sBinary)
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    listBlockers = flistLevel3Blockers(dictWorkflow, sRepo, False)
    dictCells = fdictComputeStepLevelStates(
        dictWorkflow, [], [], listBlockers,
    )[0]
    assert dictCells["s3"]["sState"] != "attained"
    assert fbAtLeastLevel3(dictWorkflow, sRepo, False) is False


# ---------------------------------------------------------------------
# A host project can never hold Level 3
# ---------------------------------------------------------------------


def _fdictHostWorkflow():
    return {
        "sProjectRepoPath": "/repo",
        "listSteps": [{
            "sName": "stepOne", "sDirectory": "stepOne",
            "saOutputDataFiles": ["stepOne/output.json"],
            "saPlotFiles": [], "bNoInputData": True,
            "dictVerification": {"sUser": "passed", "sUnitTest": "passed"},
        }],
    }


@pytest.mark.falsification
def testAHostProjectNeverReadsLevelThreeAttainedInAnyCell():
    """Kills: levelGates._ftWorkflowLevel3Counts: the host-mode zeroing
    `bHasRepo and not bHostMode` replaced by `bHasRepo`.
    """
    dictWorkflow = _fdictHostWorkflow()
    listBlockers = flistLevel3Blockers(dictWorkflow, "/repo", True)
    assert [d["sCriterion"] for d in listBlockers] == ["host-mode"]
    dictWorkflowCells = fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, [], listBlockers,
    )
    assert dictWorkflowCells["s3"]["sState"] != "attained"
    assert dictWorkflowCells["s3"]["iSatisfied"] == 0
    dictStepCells = fdictComputeStepLevelStates(
        dictWorkflow, [], [], listBlockers,
    )[0]
    assert dictStepCells["s3"]["sState"] != "attained"


@pytest.mark.falsification
def testTheRatchetStampsNoLevelThreeForAHostProject():
    """Kills: levelGates._flistStepLevel3Requirements: the host-mode
    zeroing `or dictContext.get("bHostMode")` removed.
    """
    dictWorkflow = _fdictHostWorkflow()
    listBlockers = flistLevel3Blockers(dictWorkflow, "/repo", True)
    dictStepStates = fdictComputeStepLevelStates(
        dictWorkflow, [], [], listBlockers,
    )
    dictWorkflowStates = fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, [], listBlockers,
    )
    stateManager.fbRatchetLevelHighWater(
        dictWorkflow, dictStepStates, dictWorkflowStates,
    )
    assert "3" not in dictWorkflow["listSteps"][0].get(
        "dictLevelHighWater", {})
    assert "3" not in dictWorkflow.get("dictWorkflowLevelHighWater", {})


def testAContainerProjectStillCountsItsWorkflowCriteriaAtLevelThree():
    """The zeroing is for host mode only: a container cell keeps its count."""
    dictWorkflow = _fdictHostWorkflow()
    dictCells = fdictComputeWorkflowScopeLevelStates(dictWorkflow, [], [])
    assert dictCells["s3"]["iTotal"] == len(
        levelGates._T_WORKFLOW_LEVEL3_CRITERIA)
    assert dictCells["s3"]["sState"] == "attained"
