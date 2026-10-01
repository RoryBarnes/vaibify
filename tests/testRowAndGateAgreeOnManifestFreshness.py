"""The step row and the Level 1 gate must answer "is this step fresh?" alike.

``fileStatusManager`` decides whether an mtime-stale step is really
modified or merely checked out; the Level 1 ``script-stale`` criterion
asks the same question of the same manifest to decide whether to fire.
They kept separate copies of the comparison, and the copies disagreed:
the row counted a step's declared INPUTS, the gate counted outputs only.
A step whose tracked input had changed but whose outputs were identical
showed ``modified`` while the gate suppressed ``script-stale`` -- a row
and a level that failed on different sets.

Both now call ``hashStaleness.fbStepHashesMatchManifest``. The decision
recorded in its docstring is that tracked inputs COUNT: an input that has
drifted since the manifest was pinned means the pinned outputs were
produced from other bytes, and a fresh-clone shortcut that ignored it
would mark a step clean on the strength of outputs that no longer follow
from its inputs.

The test composes the two consumers exactly as the poll does: the row's
verdict feeds the gate as its ``dictScriptStatus``.
"""

import hashlib
import os

import pytest

from vaibify.gui.fileStatusManager import _fdictBuildScriptStatus
from vaibify.reproducibility.levelGates import flistLevel1Blockers

S_STEP_DIRECTORY = "stepOne"
BA_OUTPUT = b"1,2\n"
BA_INPUT = b"raw,data\n"
BA_SCRIPT = b"print(1)\n"
I_OUTPUT_MTIME = 1_900_000_000
I_SCRIPT_MTIME = 1_900_000_500


def _fsSha(baContent):
    return hashlib.sha256(baContent).hexdigest()


def _fnWrite(sRepo, sRelative, baContent):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(baContent)
    return sPath


def _fsBuildRepo(tmp_path, baInputNow, baOutputNow):
    """A repo whose manifest pins the ORIGINAL bytes of all three files."""
    sRepo = str(tmp_path)
    _fnWrite(sRepo, "stepOne/out.csv", baOutputNow)
    _fnWrite(sRepo, "stepOne/make.py", BA_SCRIPT)
    _fnWrite(sRepo, "raw/in.csv", baInputNow)
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileHandle:
        fileHandle.write(
            f"{_fsSha(BA_OUTPUT)}  stepOne/out.csv\n"
            f"{_fsSha(BA_SCRIPT)}  stepOne/make.py\n"
            f"{_fsSha(BA_INPUT)}  raw/in.csv\n"
        )
    return sRepo


def _fdictWorkflow(sRepo):
    return {
        "sProjectRepoPath": sRepo,
        "listSteps": [{
            "sName": "OnlyStep", "sDirectory": S_STEP_DIRECTORY,
            "saOutputDataFiles": ["out.csv"], "saPlotFiles": [],
            "saInputDataFiles": ["raw/in.csv"],
            "saDataCommands": ["python make.py"], "saPlotCommands": [],
            "dictVerification": {
                "sUser": "passed", "sUnitTest": "passed",
                "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
            },
        }],
    }


def _fdictRowAndGate(sRepo):
    """Return ``(sRowStatus, bGateFiresScriptStale)`` as the poll composes them."""
    dictWorkflow = _fdictWorkflow(sRepo)
    dictModTimes = {
        os.path.join(sRepo, "stepOne", "out.csv"): str(I_OUTPUT_MTIME),
        os.path.join(sRepo, "stepOne", "make.py"): str(I_SCRIPT_MTIME),
    }
    dictVars = {
        "sPlotDirectory": "Plot", "sFigureType": "pdf",
        "sRepoRoot": sRepo,
    }
    dictScriptStatus = _fdictBuildScriptStatus(
        dictWorkflow, dictModTimes, dictVars, filesRepo=sRepo,
    )
    listBlockers = flistLevel1Blockers(
        dictWorkflow, {}, sRepo, dictScriptStatus,
    )
    return (
        dictScriptStatus[0]["sStatus"],
        any(d["sCriterion"] == "script-stale" for d in listBlockers),
    )


@pytest.mark.falsification
def testAChangedInputMakesTheRowAndTheGateAgreeTheStepIsStale(tmp_path):
    """Outputs identical, input drifted: the row says modified, so must the gate.

    Kills: levelGates._fbStepHashesMatchManifest: the shared call
    `hashStaleness.fbStepHashesMatchManifest(...)` replaced by
    `return True`, a gate that always suppresses script-stale. (The
    original defect was a partial form of that: suppress whenever the
    outputs matched, whatever the inputs did.)
    """
    sRepo = _fsBuildRepo(tmp_path, b"raw,CHANGED\n", BA_OUTPUT)
    sRowStatus, bGateFires = _fdictRowAndGate(sRepo)
    assert sRowStatus == "modified"
    assert bGateFires is True


def testAFreshCheckoutIsFreshToTheRowAndTheGateAlike(tmp_path):
    """Everything matches the manifest: neither the row nor the gate objects."""
    sRepo = _fsBuildRepo(tmp_path, BA_INPUT, BA_OUTPUT)
    sRowStatus, bGateFires = _fdictRowAndGate(sRepo)
    assert sRowStatus == "unchanged"
    assert bGateFires is False


def testAChangedOutputMakesTheRowAndTheGateAgreeTheStepIsStale(tmp_path):
    sRepo = _fsBuildRepo(tmp_path, BA_INPUT, b"1,CHANGED\n")
    sRowStatus, bGateFires = _fdictRowAndGate(sRepo)
    assert sRowStatus == "modified"
    assert bGateFires is True
