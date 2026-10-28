"""The Level 2 gaps dict reports the gate's own verdict, and names why.

``fdictLevel2Gaps["bAtLeastLevel2"]`` used to be a conjunction rebuilt
beside ``_fbComputeLevel2``, and it left out two of the gate's
conjuncts: basename collisions and the Overleaf blockers. A project
held below Level 2 by either read ``bAtLeastLevel2: true`` on
check-l2-readiness, which the in-container proof-ladder skill tells an
agent to read. These tests drive the real dict with every conjunct
stubbed, so each case is caused by exactly the conjunct it names.
"""

import pytest

from vaibify.reproducibility import levelGates, replayGate
from vaibify.reproducibility.repoFiles import HostRepoFiles


_T_PASSING_BOOLEAN_CONJUNCTS = (
    (levelGates, "fbAtLeastLevel1"),
    (levelGates, "fbWorkflowFullySyncedWithGithub"),
    (levelGates, "fbWorkflowFullySyncedWithZenodo"),
    (levelGates, "fbWorkflowFullySyncedWithArxiv"),
    (levelGates, "fbWorkflowAiDeclarationAttested"),
    (levelGates, "fbImageArchiveQuestionSettled"),
    (replayGate, "fbWorkflowDeclaresAiModels"),
    (replayGate, "fbWorkflowDeclaresPersonalLayer"),
    (replayGate, "fbPromptRecordCurrent"),
    (replayGate, "fbSupervisionClean"),
)


def _fnStubEveryConjunct(monkeypatch, listCollisions, listOverleafBlockers):
    for moduleOwner, sName in _T_PASSING_BOOLEAN_CONJUNCTS:
        monkeypatch.setattr(
            moduleOwner, sName, lambda *args, **kwargs: True)
    monkeypatch.setattr(
        levelGates, "flistLevel2BasenameCollisions",
        lambda dictWorkflow: listCollisions)
    monkeypatch.setattr(
        levelGates, "_flistOverleafLevel2Blockers",
        lambda dictWorkflow, filesRepo: listOverleafBlockers)
    monkeypatch.setattr(
        levelGates, "_fdictRecomputeSupervisionEvidence",
        lambda dictWorkflow, filesRepo: {})


def _fdictGaps():
    return levelGates.fdictLevel2Gaps(
        {"listSteps": []}, HostRepoFiles("/nonexistent-for-shape"))


@pytest.mark.falsification
def testABasenameCollisionReadsBelowLevelTwoInTheGapsDict(monkeypatch):
    """A collision the gate refuses on is refused by the dict too, with its key.

    Kills: rebuilding ``bAtLeastLevel2`` from the dict's own keys, which
    omit the collision conjunct and so report Level 2 attained.
    """
    _fnStubEveryConjunct(monkeypatch, ["Step/out.csv collides"], [])
    dictGaps = _fdictGaps()
    assert dictGaps["bNoBasenameCollisions"] is False
    assert dictGaps["bAtLeastLevel2"] is False


@pytest.mark.parametrize(
    "listCollisions, listOverleafBlockers",
    [([], []), (["collision"], []), ([], ["overleaf-not-synced"])],
)
def testEveryFalseVerdictHasAFalseKeyBesideIt(
    monkeypatch, listCollisions, listOverleafBlockers,
):
    _fnStubEveryConjunct(monkeypatch, listCollisions, listOverleafBlockers)
    dictGaps = _fdictGaps()
    bAnyKeyFalse = not (
        dictGaps["bNoBasenameCollisions"] and dictGaps["bOverleafFullySynced"])
    assert dictGaps["bAtLeastLevel2"] is (not bAnyKeyFalse)
