"""Unit tests for the per-step and workflow-scope level-state projection.

``fdictComputeStepLevelStates`` is a PURE projection of the already-
computed L1/L2/L3 blocker lists onto a three-state vocabulary
(``attained`` / ``blocked`` / ``unknown``) per step per level. The
truth table pinned here is the contract a later agent wires into the
poll payload; ``fiStepAICSLevel`` and
``fdictComputeWorkflowScopeLevelStates`` complete the surface.
"""

from vaibify.reproducibility.levelGates import (
    fdictComputeStepLevelStates,
    fdictComputeWorkflowScopeLevelStates,
    fiStepAICSLevel,
)


def _fdictWorkflowWithStepCount(iStepCount, sProjectRepoPath="/repo"):
    """Return a merged workflow shell with ``iStepCount`` named steps."""
    return {
        "sProjectRepoPath": sProjectRepoPath,
        "listSteps": [
            {"sName": f"step{iIndex}", "sDirectory": f"step{iIndex}"}
            for iIndex in range(iStepCount)
        ],
    }


def _fdictStepBlocker(iLevel, iStepIndex, sCriterion):
    """Return a per-step blocker entry in the unified schema."""
    return {
        "iLevel": iLevel,
        "iStepIndex": iStepIndex,
        "sStepLabel": f"A{iStepIndex + 1:02d}",
        "sScope": "step",
        "sCriterion": sCriterion,
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": "fix it",
    }


def _fdictWorkflowBlocker(iLevel, sCriterion):
    """Return a workflow-scope blocker entry in the unified schema."""
    return {
        "iLevel": iLevel,
        "iStepIndex": -1,
        "sStepLabel": "(workflow)",
        "sScope": "workflow",
        "sCriterion": sCriterion,
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": "fix it",
    }


# ------------------------------------------------------------------------
# fdictComputeStepLevelStates truth table
# ------------------------------------------------------------------------


def testCleanStepAttainsAllThreeLevels():
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), [], [], [],
    )
    assert dictStates == {
        0: {"s1": "attained", "s2": "attained", "s3": "attained"},
    }


def testLevel1BlockerBlocksAllThreeLevels():
    listLevel1 = [_fdictStepBlocker(1, 0, "axis-not-green")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), listLevel1, [], [],
    )
    assert dictStates[0] == {
        "s1": "blocked", "s2": "blocked", "s3": "blocked",
    }


def testPerStepLevel2SyncBlockerBlocksLevelsTwoAndThree():
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), [], listLevel2, [],
    )
    assert dictStates[0] == {
        "s1": "attained", "s2": "blocked", "s3": "blocked",
    }


def testPerStepLevel3BlockerBlocksOnlyLevelThree():
    listLevel3 = [_fdictStepBlocker(3, 0, "missing-from-manifest")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), [], [], listLevel3,
    )
    assert dictStates[0] == {
        "s1": "attained", "s2": "attained", "s3": "blocked",
    }


def testGithubVerifyStaleMakesCleanStepUnknownNeverAttained():
    """STALE-CACHE PRECEDENCE: a stale verify suppresses the per-step
    sync rows at the source, so an otherwise-clean step must report
    ``unknown`` at s2 (and the propagated s3) — never ``attained``
    from a cache nobody can trust."""
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), [], listLevel2, [],
    )
    assert dictStates[0] == {
        "s1": "attained", "s2": "unknown", "s3": "unknown",
    }


def testZenodoVerifyStaleAlsoMakesCleanStepUnknown():
    listLevel2 = [_fdictWorkflowBlocker(2, "zenodo-verify-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), [], listLevel2, [],
    )
    assert dictStates[0]["s2"] == "unknown"


def testVerifyStaleLeavesLevel1DirtyStepBlocked():
    listLevel1 = [_fdictStepBlocker(1, 0, "user-not-approved")]
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(2), listLevel1, listLevel2, [],
    )
    assert dictStates[0] == {
        "s1": "blocked", "s2": "blocked", "s3": "blocked",
    }
    assert dictStates[1] == {
        "s1": "attained", "s2": "unknown", "s3": "unknown",
    }


def testOwnLevel2BlockerBeatsUnknownFromStaleCache():
    """A figure-not-frozen blocker coexists with a stale sync cache;
    the step's own blocker dominates the unknown."""
    listLevel2 = [
        _fdictWorkflowBlocker(2, "github-verify-stale"),
        _fdictStepBlocker(2, 0, "figure-not-frozen"),
    ]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(1), [], listLevel2, [],
    )
    assert dictStates[0]["s2"] == "blocked"


def testUnknownAtLevelTwoPropagatesOverCleanLevelThree():
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    listLevel3 = [_fdictStepBlocker(3, 1, "script-not-pinned")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(2), [], listLevel2, listLevel3,
    )
    assert dictStates[0]["s3"] == "unknown"
    assert dictStates[1]["s3"] == "blocked"


def testBlockersOnlyAffectTheirOwnStep():
    listLevel1 = [_fdictStepBlocker(1, 1, "axis-not-green")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(3), listLevel1, [], [],
    )
    assert dictStates[0]["s1"] == "attained"
    assert dictStates[1]["s1"] == "blocked"
    assert dictStates[2]["s1"] == "attained"


def testProjectionCoversEveryStepIndex():
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithStepCount(4), [], [], [],
    )
    assert sorted(dictStates.keys()) == [0, 1, 2, 3]


# ------------------------------------------------------------------------
# fiStepAICSLevel contiguity
# ------------------------------------------------------------------------


def testStepLevelZeroWhenLevelOneBlocked():
    assert fiStepAICSLevel(
        {"s1": "blocked", "s2": "blocked", "s3": "blocked"},
    ) == 0


def testStepLevelThreeWhenAllAttained():
    assert fiStepAICSLevel(
        {"s1": "attained", "s2": "attained", "s3": "attained"},
    ) == 3


def testStepLevelStopsAtFirstNonAttainedRung():
    assert fiStepAICSLevel(
        {"s1": "attained", "s2": "unknown", "s3": "attained"},
    ) == 1
    assert fiStepAICSLevel(
        {"s1": "attained", "s2": "attained", "s3": "blocked"},
    ) == 2


def testStepLevelContiguityIgnoresAttainedAboveGap():
    assert fiStepAICSLevel(
        {"s1": "attained", "s2": "blocked", "s3": "attained"},
    ) == 1


def testStepLevelHandlesEmptyOrNoneStates():
    assert fiStepAICSLevel({}) == 0
    assert fiStepAICSLevel(None) == 0


# ------------------------------------------------------------------------
# fdictComputeWorkflowScopeLevelStates
# ------------------------------------------------------------------------


def testWorkflowScopeAllAttainedWhenCleanWithRepo():
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1), [], [],
    )
    assert dictStates == {
        "s1": "attained", "s2": "attained", "s3": "attained",
    }


def testWorkflowScopeRepoMissingBlocksHeaderLevelOne():
    """``flistLevel1Blockers`` returns [] when the repo is missing, so
    the header is the only honest home for that gap."""
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1, sProjectRepoPath=""), [], [],
    )
    assert dictStates == {
        "s1": "blocked", "s2": "blocked", "s3": "blocked",
    }


def testWorkflowScopeExcludesMissingAiDeclarationStep():
    """The missing-ai-declaration-step blocker is re-homed to a ghost
    step row; the header must not double-report it."""
    listLevel2 = [
        _fdictWorkflowBlocker(2, "missing-ai-declaration-step"),
    ]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1), listLevel2, [],
    )
    assert dictStates["s2"] == "attained"


def testWorkflowScopeVerifyStaleBlocksHeaderLevelTwo():
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1), listLevel2, [],
    )
    assert dictStates == {
        "s1": "attained", "s2": "blocked", "s3": "blocked",
    }


def testWorkflowScopeArxivBlockerBlocksHeaderLevelTwo():
    listLevel2 = [_fdictWorkflowBlocker(2, "arxiv-not-submitted")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1), listLevel2, [],
    )
    assert dictStates["s2"] == "blocked"


def testWorkflowScopeIgnoresPerStepBlockerEntries():
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-zenodo-deposit")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1), listLevel2, [],
    )
    assert dictStates["s2"] == "attained"


def testWorkflowScopeLevel3BlockerBlocksOnlyHeaderLevelThree():
    listLevel3 = [_fdictWorkflowBlocker(3, "dockerfile-not-pinned")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithStepCount(1), [], listLevel3,
    )
    assert dictStates == {
        "s1": "attained", "s2": "attained", "s3": "blocked",
    }


# ------------------------------------------------------------------------
# Integration with the real L1 blocker generator
# ------------------------------------------------------------------------


def testProjectionAgreesWithRealLevel1Blockers():
    from vaibify.reproducibility.levelGates import flistLevel1Blockers
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "saDataFiles": [], "saPlotFiles": [],
        "dictVerification": {"sUser": "untested"},
    }
    dictWorkflow = {
        "sProjectRepoPath": "/repo", "listSteps": [dictStep],
    }
    listLevel1 = flistLevel1Blockers(dictWorkflow, {}, "/repo")
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, listLevel1, [], [],
    )
    assert dictStates[0]["s1"] == "blocked"
    assert fiStepAICSLevel(dictStates[0]) == 0
