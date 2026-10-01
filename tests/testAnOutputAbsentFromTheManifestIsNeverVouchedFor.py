"""A step whose output the manifest does not list is not fresh.

``fsetStaleOutputsAgainstManifest`` drops paths the manifest does not
list (untracked files are out of its scope), so on its own it would call
a step fresh when every file the manifest DOES cover matches. The guard
that closes that is ``fbAnyPathMissingFromManifest``. Mutating it to
never fire survived 630 tests, so the guard had no behavioural test of
its own: a modified script whose output was absent from the manifest
would have stopped showing ``script-stale``.

The fixture is built so the guard is the ONLY thing standing between the
step and a wrong "fresh": the manifest lists the step's input and its
script, both matching, and omits only the output.
"""

import hashlib
import os

import pytest

from vaibify.gui import hashStaleness
from vaibify.reproducibility.levelGates import flistLevel1Blockers

BA_INPUT = b"raw,data\n"
BA_SCRIPT = b"print(1)\n"
BA_OUTPUT = b"1,2\n"


def _fnWrite(sRepo, sRelative, baContent):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(baContent)


def _fsSha(baContent):
    return hashlib.sha256(baContent).hexdigest()


@pytest.fixture
def sRepoWithOutputOmittedFromTheManifest(tmp_path):
    sRepo = str(tmp_path)
    _fnWrite(sRepo, "stepOne/out.csv", BA_OUTPUT)
    _fnWrite(sRepo, "stepOne/make.py", BA_SCRIPT)
    _fnWrite(sRepo, "raw/in.csv", BA_INPUT)
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileHandle:
        fileHandle.write(
            f"{_fsSha(BA_SCRIPT)}  stepOne/make.py\n"
            f"{_fsSha(BA_INPUT)}  raw/in.csv\n"
        )
    return sRepo


def _fdictStep():
    return {
        "sName": "OnlyStep", "sDirectory": "stepOne",
        "saOutputDataFiles": ["out.csv"], "saPlotFiles": [],
        "saInputDataFiles": ["raw/in.csv"],
        "saDataCommands": ["python make.py"], "saPlotCommands": [],
        "dictVerification": {
            "sUser": "passed", "sUnitTest": "passed",
            "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
        },
    }


def testThePredicateNamesAnAbsentPath():
    assert hashStaleness.fbAnyPathMissingFromManifest(
        ["a.csv", "b.csv"], {"a.csv": "x"}) is True
    assert hashStaleness.fbAnyPathMissingFromManifest(
        ["a.csv"], {"a.csv": "x", "b.csv": "y"}) is False
    assert hashStaleness.fbAnyPathMissingFromManifest([], {}) is False


@pytest.mark.falsification
def testAnOutputTheManifestOmitsIsNeverFreshWhateverElseMatches(
    sRepoWithOutputOmittedFromTheManifest,
):
    """Kills: hashStaleness.fbAnyPathMissingFromManifest returning False always."""
    assert hashStaleness.fbStepHashesMatchManifest(
        _fdictStep(), sRepoWithOutputOmittedFromTheManifest,
        sRepoWithOutputOmittedFromTheManifest,
    ) is False


def testTheGateKeepsShowingScriptStaleForAnOmittedOutput(
    sRepoWithOutputOmittedFromTheManifest,
):
    """The consequence the guard protects: the blocker stays visible."""
    sRepo = sRepoWithOutputOmittedFromTheManifest
    dictWorkflow = {"sProjectRepoPath": sRepo, "listSteps": [_fdictStep()]}
    listBlockers = flistLevel1Blockers(
        dictWorkflow, {}, sRepo, {0: {"sStatus": "modified"}},
    )
    assert "script-stale" in [d["sCriterion"] for d in listBlockers]
