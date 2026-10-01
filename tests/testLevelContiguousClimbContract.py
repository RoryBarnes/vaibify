"""The scalar level, the cells and the blockers follow one contract.

Levels are computed as INDEPENDENT cells, and a level is the highest
CONTIGUOUS climb of them: each rung must be ``attained`` or
``not-applicable``, so an attained L3 above a partial L2 is level 1
(``fiStepProofLevel``). The properties asserted here are what that
contract implies, not that the scalar equals the highest attained cell,
which the contract contradicts.

Each fixture drives the REAL local-world gates (Level 1, the per-step
Level 3 criteria, host mode) against real files, with the remote world
(sync caches, archives, permanence) stubbed uniformly true: stubbing
only the scalar would make the scalar and the cells describe different
projects and the properties meaningless.

* P1  every step's level equals the contiguous climb of its own cells,
      derived here from the contract, not by calling the function.
* P2  the workflow scalar never exceeds the contiguous climb of the
      workflow cells, or of any step's cells. (Display above the scalar
      is a different defect, covered where it was found.)
* P3  every Level 2 / Level 3 blocker is visible to the cells: removing
      it from the lists changes some cell. A criterion the gates emit
      and the cells ignore is the header-outranks-its-rows defect.
"""

import hashlib
import json
import os

from tests.levelGateStubs import fnMakeEveryLevel3ConjunctPass
from vaibify.reproducibility import levelGates
from vaibify.reproducibility.levelGates import (
    fdictComputeStepLevelStates,
    fdictComputeWorkflowScopeLevelStates,
    fiProofLevel,
    fiStepProofLevel,
    flistLevel1Blockers,
    flistLevel3Blockers,
)

# Blockers deliberately absent from the cells: the AI Declaration is a
# STEP, so its absence lives on the ghost row alone and is never counted
# at workflow scope (2026-08-27 double-count ruling).
SET_CRITERIA_THE_CELLS_DELIBERATELY_OMIT = frozenset({
    "missing-ai-declaration-step",
})


def _iClimbByTheContract(dictCells):
    """Highest k with every rung 1..k attained or not-applicable."""
    iLevel = 0
    for iRung in (1, 2, 3):
        sState = (dictCells.get(f"s{iRung}") or {}).get("sState")
        if sState not in ("attained", "not-applicable"):
            break
        iLevel = iRung
    return iLevel


def _fnWrite(sRepo, sRelative, baContent):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(baContent)
    return hashlib.sha256(baContent).hexdigest()


def _fnPinManifest(sRepo, dictBytesByPath):
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileHandle:
        for sPath, baContent in dictBytesByPath.items():
            fileHandle.write(
                f"{hashlib.sha256(baContent).hexdigest()}  {sPath}\n")


def _fdictStep(**dictOverrides):
    dictStep = {
        "sName": "Simulate", "sDirectory": "sim",
        "saDataCommands": ["python make.py"],
        "saOutputDataFiles": ["out.json"],
        "bNoInputData": True,
        "dictVerification": {"sUser": "passed", "sUnitTest": "passed"},
        "dictRunStats": {"fWallClock": 1.0},
    }
    dictStep.update(dictOverrides)
    return dictStep


def _fsBuildRepo(tmp_path, bPinOutput=True, bKeepScript=True,
                 sBinaryPath=None, sCapturedHash=None):
    sRepo = str(tmp_path / "repo")
    dictPinned = {}
    baOut, baScript = b"{}", b"print(1)\n"
    _fnWrite(sRepo, "sim/out.json", baOut)
    _fnWrite(sRepo, "sim/make.py", baScript)
    if bPinOutput:
        dictPinned["sim/out.json"] = baOut
    dictPinned["sim/make.py"] = baScript
    _fnPinManifest(sRepo, dictPinned)
    if not bKeepScript:
        os.remove(os.path.join(sRepo, "sim", "make.py"))
    if sBinaryPath:
        sDirectory = os.path.join(sRepo, ".vaibify")
        os.makedirs(sDirectory, exist_ok=True)
        with open(os.path.join(sDirectory, "environment.json"), "w") as f:
            json.dump({"dictHostBinaries": {"listBinaries": [{
                "sBinaryPath": sBinaryPath, "sSha256": sCapturedHash,
                "sVersion": "3.0"}]}}, f)
    return sRepo


def _fdictWorkflow(sRepo, dictStep, sBinaryPath=None):
    return {
        "sProjectRepoPath": sRepo,
        "bNoStandaloneBinaries": sBinaryPath is None,
        "listDeclaredBinaries": [] if sBinaryPath is None else [{
            "sBinaryPath": sBinaryPath, "sPurpose": "forward model",
            "sExpectedVersion": "3.0"}],
        "listSteps": [dictStep],
    }


def _flistFixtures(tmp_path):
    """Return ``[(sName, dictWorkflow, sRepo, bHostProject)]``."""
    sBinary = str(tmp_path / "forwardModel")
    with open(sBinary, "wb") as fileBinary:
        fileBinary.write(b"rebuilt")
    listFixtures = []

    def fnAdd(sName, tmpSub, dictStep=None, bHost=False, **dictRepo):
        sRepo = _fsBuildRepo(tmpSub, **dictRepo)
        dictWorkflow = _fdictWorkflow(
            sRepo, dictStep or _fdictStep(),
            sBinaryPath=dictRepo.get("sBinaryPath"),
        )
        listFixtures.append((sName, dictWorkflow, sRepo, bHost))

    for iIndex, (sName, dictKw) in enumerate([
        ("clean", {}),
        ("host", {"bHost": True}),
        ("output-not-pinned", {"bPinOutput": False}),
        ("script-deleted", {"bKeepScript": False}),
        ("binary-drifted", {
            "sBinaryPath": sBinary, "sCapturedHash": "0" * 64,
            "dictStep": _fdictStep(saBinaryDependencies=["forwardModel"]),
        }),
        ("user-not-approved", {
            "dictStep": _fdictStep(dictVerification={
                "sUser": "untested", "sUnitTest": "passed"}),
        }),
        ("attestation-stale", {
            "dictStep": _fdictStep(dictVerification={
                "sUser": "stale", "sUnitTest": "passed",
                "sLastUserUpdate": "2026-01-01 00:00:00 UTC"}),
        }),
    ]):
        tmpSub = tmp_path / f"fixture{iIndex}"
        tmpSub.mkdir()
        fnAdd(sName, tmpSub, **dictKw)
    return listFixtures


def _fnStubTheRemoteWorld(monkeypatch):
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    monkeypatch.setattr(
        levelGates, "flistLevel2Blockers", lambda *a, **k: [])


def _fdictEvaluate(dictWorkflow, sRepo, bHost):
    listL1 = flistLevel1Blockers(dictWorkflow, {}, sRepo)
    listL2 = []
    listL3 = flistLevel3Blockers(dictWorkflow, sRepo, bHost)
    return {
        "listL1": listL1, "listL2": listL2, "listL3": listL3,
        "dictStepCells": fdictComputeStepLevelStates(
            dictWorkflow, listL1, listL2, listL3),
        "dictWorkflowCells": fdictComputeWorkflowScopeLevelStates(
            dictWorkflow, listL2, listL3),
        "iScalar": fiProofLevel(dictWorkflow, sRepo, bHostProject=bHost),
    }


def testTheFixturesCoverBothSidesOfEveryProperty(monkeypatch, tmp_path):
    """Guard against a fixture set that cannot fail: the scalars differ."""
    _fnStubTheRemoteWorld(monkeypatch)
    setScalars = {
        _fdictEvaluate(dictWorkflow, sRepo, bHost)["iScalar"]
        for _sName, dictWorkflow, sRepo, bHost in _flistFixtures(tmp_path)
    }
    assert {0, 2, 3} <= setScalars, setScalars


def testEveryStepLevelIsTheContiguousClimbOfItsCells(
    monkeypatch, tmp_path,
):
    _fnStubTheRemoteWorld(monkeypatch)
    for sName, dictWorkflow, sRepo, bHost in _flistFixtures(tmp_path):
        dictResult = _fdictEvaluate(dictWorkflow, sRepo, bHost)
        for iStep, dictCells in dictResult["dictStepCells"].items():
            assert fiStepProofLevel(dictCells) == _iClimbByTheContract(
                dictCells), (sName, iStep, dictCells)


def testTheScalarNeverExceedsTheClimbOfTheCellsBeneathIt(
    monkeypatch, tmp_path,
):
    _fnStubTheRemoteWorld(monkeypatch)
    for sName, dictWorkflow, sRepo, bHost in _flistFixtures(tmp_path):
        dictResult = _fdictEvaluate(dictWorkflow, sRepo, bHost)
        iCeiling = min(
            [_iClimbByTheContract(dictResult["dictWorkflowCells"])]
            + [_iClimbByTheContract(dictCells)
               for dictCells in dictResult["dictStepCells"].values()]
        )
        assert dictResult["iScalar"] <= iCeiling, (
            sName, dictResult["iScalar"], iCeiling)


def testTheScalarIsNotRequiredToEqualTheHighestAttainedCell(
    monkeypatch, tmp_path,
):
    """The contract the plan warns about, pinned so nobody 'fixes' it.

    ``output-not-pinned`` leaves L3 partial while L1 is attained: the
    scalar is the climb, and a cell above a gap does not lift it.
    """
    _fnStubTheRemoteWorld(monkeypatch)
    for sName, dictWorkflow, sRepo, bHost in _flistFixtures(tmp_path):
        dictResult = _fdictEvaluate(dictWorkflow, sRepo, bHost)
        if sName != "output-not-pinned":
            continue
        dictCells = dictResult["dictStepCells"][0]
        assert dictCells["s1"]["sState"] == "attained"
        assert dictCells["s3"]["sState"] != "attained"
        assert dictResult["iScalar"] < 3


def _fdictCellsWithout(dictWorkflow, dictResult, dictRemoved):
    listL2 = [d for d in dictResult["listL2"] if d is not dictRemoved]
    listL3 = [d for d in dictResult["listL3"] if d is not dictRemoved]
    return (
        fdictComputeStepLevelStates(
            dictWorkflow, dictResult["listL1"], listL2, listL3),
        fdictComputeWorkflowScopeLevelStates(dictWorkflow, listL2, listL3),
    )


def testEveryBlockerAtLevelTwoOrThreeIsVisibleToTheCells(
    monkeypatch, tmp_path,
):
    _fnStubTheRemoteWorld(monkeypatch)
    iChecked = 0
    for sName, dictWorkflow, sRepo, bHost in _flistFixtures(tmp_path):
        dictResult = _fdictEvaluate(dictWorkflow, sRepo, bHost)
        tWith = (dictResult["dictStepCells"], dictResult["dictWorkflowCells"])
        for dictBlocker in dictResult["listL2"] + dictResult["listL3"]:
            if dictBlocker["sCriterion"] in (
                SET_CRITERIA_THE_CELLS_DELIBERATELY_OMIT
            ):
                continue
            tWithout = _fdictCellsWithout(
                dictWorkflow, dictResult, dictBlocker)
            assert tWithout != tWith, (
                f"{sName}: {dictBlocker['sCriterion']} is shown on a row "
                "and counted by no cell"
            )
            iChecked += 1
    assert iChecked >= 4
