"""Whatever the Level 2 gate can refuse for, the blockers must name.

A researcher's only route out of Level 1 is the blocker list. When the
gate refuses and the list is empty, there is no route: every listed
problem is cleared, the level does not move, and nothing on screen says
why. That is not hypothetical — it was observed on a real project.
GitHub's cache had gone stale, which the list DID report; re-verifying
it left the gate still refusing (project.json diverged from the Zenodo
deposit) and the blocker list completely empty, because the divergence
projection intersected with each step's declared files and
``.vaibify/projects/project.json`` belongs to no step.

The bug is a class, not an instance. ``project.json`` is simply the
first Level 2 path that no step owns; any future one behaves the same
way. So the test below asserts the RELATIONSHIP — gate refuses implies
blockers non-empty — rather than asserting that this particular path
appears. A test written against the instance would pass again the next
time the set of unowned paths changed.
"""

import pytest

from vaibify.reproducibility import levelGates


S_UNOWNED_LEVEL2_PATH = ".vaibify/projects/project.json"
S_STEP_OUTPUT = "Step/result.csv"


def _fdictWorkflow():
    """A workflow whose single step declares one published output."""
    return {
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [{
            "sName": "GenerateSamples",
            "sDirectory": "Step",
            "bRunEnabled": True,
            "saOutputDataFiles": ["result.csv"],
        }],
    }


def _fdictSyncStatus(listDivergedPaths, sNowIso="2099-01-01T00:00:00Z"):
    """A fresh, current-scope cache reporting the given divergences."""
    from vaibify.reproducibility import publicationScope

    listCompared = sorted(
        {S_UNOWNED_LEVEL2_PATH, S_STEP_OUTPUT} | set(listDivergedPaths))
    return {
        "sService": "zenodo",
        "iScopeVersion": publicationScope.I_PUBLICATION_SCOPE_VERSION,
        "iTotalFiles": len(listCompared),
        "iMatching": len(listCompared) - len(listDivergedPaths),
        "listComparedPaths": listCompared,
        "listDiverged": [
            {"sPath": sPath, "sExpected": "a" * 64, "sActual": "b" * 64}
            for sPath in listDivergedPaths
        ],
        "sLastVerified": sNowIso,
        "sEndpointVerified": "sandbox",
    }


@pytest.fixture
def fnDriveBlockers(monkeypatch):
    """Return a callable giving the blockers for one canned cache."""
    from vaibify.reproducibility import scheduledReverify

    def fnDrive(listDivergedPaths):
        from datetime import datetime, timezone
        sNow = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        dictStatus = _fdictSyncStatus(listDivergedPaths, sNow)
        monkeypatch.setattr(
            scheduledReverify, "fdictReadCachedSyncStatus",
            lambda filesRepo, sService: (
                dictStatus if sService == "zenodo"
                else _fdictSyncStatus([], sNow)),
        )
        return levelGates._flistZenodoLevel2Blockers(_fdictWorkflow(), None)
    return fnDrive


@pytest.mark.falsification
def testADivergedPathNoStepOwnsStillProducesABlocker(fnDriveBlockers):
    """The defect itself: the projection's leftovers must be homed.

    ``project.json`` is a Level 2 path declared by no step, so the
    per-step intersection drops it. Before the fix this returned an
    empty list while the gate went on refusing the level.
    
    Kills: dropping the workflow-scope homing of the projection's
    leftovers, which returns the blocker list to reporting nothing
    while the gate goes on refusing the level.
    """
    listBlockers = fnDriveBlockers([S_UNOWNED_LEVEL2_PATH])
    assert listBlockers, (
        "a diverged Level 2 path that no step declares produced no "
        "blocker, so the researcher is refused Level 2 with nothing to act on"
    )
    assert listBlockers[0]["sScope"] == "workflow"
    assert listBlockers[0]["listOffendingFiles"] == [S_UNOWNED_LEVEL2_PATH]
    assert listBlockers[0]["sRemediationHint"]


def testAPathAStepOwnsIsStillReportedOnThatStep(fnDriveBlockers):
    """The per-step projection must survive the fix.

    Homing the leftovers at workflow scope would be a regression if it
    also swept up paths a step DOES own: those belong on the step's own
    row, which is where the researcher looks for them.
    """
    listBlockers = fnDriveBlockers([S_STEP_OUTPUT])
    assert [b["sScope"] for b in listBlockers] == ["step"]
    assert listBlockers[0]["listOffendingFiles"] == [S_STEP_OUTPUT]


def testBothKindsAreReportedTogether(fnDriveBlockers):
    """A mixed divergence must not let either kind hide the other."""
    listBlockers = fnDriveBlockers(
        [S_UNOWNED_LEVEL2_PATH, S_STEP_OUTPUT])
    dictByScope = {b["sScope"]: b for b in listBlockers}
    assert set(dictByScope) == {"step", "workflow"}
    assert dictByScope["workflow"]["listOffendingFiles"] == [
        S_UNOWNED_LEVEL2_PATH]
    assert dictByScope["step"]["listOffendingFiles"] == [S_STEP_OUTPUT]


def testAnEnvelopeDivergenceIsNotHomedAtLevelTwo(fnDriveBlockers):
    """Level 3's envelope must not re-enter the Level 2 blocker list.

    The two rungs were split apart precisely so a stale
    ``requirements.lock`` could not deny Level 2. Sweeping unclaimed
    paths up without narrowing to Level 2 would re-couple them through
    the back door — and every envelope file is unclaimed, so this is
    the likely way to get the fix wrong.
    """
    from vaibify.reproducibility import publicationScope

    listEnvelope = list(publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS)
    assert listEnvelope, "the envelope tuple is empty; this test is vacuous"
    assert fnDriveBlockers(listEnvelope) == []


def testNoDivergenceProducesNoBlocker(fnDriveBlockers):
    """A clean verify must stay clean.

    Stated because every other test here asserts that something IS
    reported, and a change that reported unconditionally would satisfy
    all of them.
    """
    assert fnDriveBlockers([]) == []
