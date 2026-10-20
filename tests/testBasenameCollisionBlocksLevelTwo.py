"""Published files that share a basename block Level 2, in every display at once.

A Zenodo deposit is flat: the archive refuses a selection whose
basenames collide, because the second file would silently overwrite
the first. Level 2 now says so before the researcher clicks (approval
F4), and the rule this repository keeps for every criterion holds: the
scalar gate, the step rows and the cells beside them fail on the SAME
set (``dashboard-state-honesty``). The colliding names here are two
steps' ``out.csv``, the shape of the real defect (two steps'
``test_qualitative.py``, one of which never reached the deposit).
"""

import pytest

from tests.testProjectHeaderNeverOutranksItsRows import (  # noqa: F401
    _fdictBuildWorkflow,
    _fnWriteBothServices,
    fixtureProjectRepo,
)
from vaibify.reproducibility import levelGates


def _fdictCollidingWorkflow(sProjectRepo):
    dictWorkflow = _fdictBuildWorkflow(sProjectRepo)
    dictWorkflow["listSteps"].append({
        "sName": "Other", "sDirectory": "Other",
        "saOutputDataFiles": ["out.csv"], "saPlotFiles": [],
    })
    return dictWorkflow


def _fnPassEveryOtherLevel2Conjunct(monkeypatch):
    """So the gate's answer depends on the collision alone."""
    for sName in ("fbAtLeastLevel1", "fbWorkflowFullySyncedWithGithub",
                  "fbWorkflowFullySyncedWithZenodo",
                  "fbWorkflowAiDeclarationAttested",
                  "fbImageArchiveQuestionSettled",
                  "fbWorkflowFullySyncedWithArxiv"):
        monkeypatch.setattr(levelGates, sName, lambda *args: True)
    for sName in ("fbWorkflowDeclaresAiModels",
                  "fbWorkflowDeclaresPersonalLayer"):
        monkeypatch.setattr(levelGates.replayGate, sName, lambda *args: True)


def testTheCollidingFilesAreFoundAcrossSteps(fixtureProjectRepo):
    assert levelGates.flistLevel2BasenameCollisions(
        _fdictCollidingWorkflow(fixtureProjectRepo)) == [
        ["Other/out.csv", "Step/out.csv"]]
    assert levelGates.flistLevel2BasenameCollisions(
        _fdictBuildWorkflow(fixtureProjectRepo)) == []


@pytest.mark.falsification
def testACollisionBlocksTheScalarGate(fixtureProjectRepo, monkeypatch):
    """Kills: dropping the collision conjunct from the Level 2 gate."""
    _fnPassEveryOtherLevel2Conjunct(monkeypatch)
    assert levelGates._fbComputeLevel2(
        _fdictBuildWorkflow(fixtureProjectRepo), fixtureProjectRepo) is True
    assert levelGates._fbComputeLevel2(
        _fdictCollidingWorkflow(fixtureProjectRepo),
        fixtureProjectRepo) is False


@pytest.mark.falsification
def testEachOwningStepCarriesTheBlockerAndItsCellDrops(fixtureProjectRepo):
    """Kills: dropping the collision requirement from the step's L2 cell."""
    _fnWriteBothServices(fixtureProjectRepo, [])
    dictWorkflow = _fdictCollidingWorkflow(fixtureProjectRepo)
    listLevel2 = levelGates.flistLevel2Blockers(
        dictWorkflow, fixtureProjectRepo)
    listCollision = [dictEntry for dictEntry in listLevel2 if dictEntry[
        "sCriterion"] == levelGates.S_BASENAME_COLLISION_CRITERION]
    assert [(dictEntry["iStepIndex"], dictEntry["listOffendingFiles"])
            for dictEntry in listCollision] == [
        (0, ["Step/out.csv"]), (1, ["Other/out.csv"])]
    assert "Other/out.csv, Step/out.csv" in listCollision[0][
        "sRemediationHint"]
    dictStates = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, [])
    for iStep in (0, 1):
        assert dictStates[iStep]["s2"]["sState"] != "attained", dictStates
        assert dictStates[iStep]["s2"]["iSatisfied"] < dictStates[iStep][
            "s2"]["iTotal"]


def testUniqueNamesKeepTheTotalsTheyHad(fixtureProjectRepo):
    _fnWriteBothServices(fixtureProjectRepo, [])
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    listLevel2 = levelGates.flistLevel2Blockers(
        dictWorkflow, fixtureProjectRepo)
    dictCell = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, [])[0]["s2"]
    assert (dictCell["iSatisfied"], dictCell["iTotal"]) == (2, 2)


def testAWorkflowScopeCollisionCountsInTheHeader():
    dictBlocker = {"iStepIndex": -1, "sCriterion":
                   levelGates.S_BASENAME_COLLISION_CRITERION}
    tWithout = levelGates._ftWorkflowLevel2Counts({}, [], True)
    tWith = levelGates._ftWorkflowLevel2Counts({}, [dictBlocker], True)
    assert tWith == (tWithout[0], tWithout[1] + 1)
