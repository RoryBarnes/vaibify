"""Unit tests for the INDEPENDENT per-level state projection.

``fdictComputeStepLevelStates`` projects the already-computed L1/L2/L3
blocker lists plus the step dicts onto one cell per step per level::

    {"sState": "not-started" | "unassessed" | "none" | "partial"
               | "attained" | "unknown" | "not-applicable",
     "iSatisfied": int, "iTotal": int, "bRegression": bool}

Levels are independent — a blocked or partial lower level never
propagates upward, so L1 attained + L2 partial + L3 attained can
coexist on one step. L3 counts only the criteria whose domain is
non-empty on the step; a step with nothing to reproduce reads
``not-applicable``, never a vacuous attained.
``fdictComputeStepLevelWarnings`` consolidates the regression column;
``fdictComputeWorkflowScopeLevelStates`` covers the header row, whose
cells are the workflow-attached requirements only — NOT an aggregate
of the step rows (that is the scalar ``fiAICSLevel`` gate).
"""

from vaibify.reproducibility.levelGates import (
    fdictComputeStepLevelStates,
    fdictComputeStepLevelWarnings,
    fdictComputeWorkflowScopeLevelStates,
    fiLowestNonAttainedLevel,
    fiStepAICSLevel,
)


def _fdictActiveCleanStep(sName="stepOne"):
    """Return a step with activity whose L1 requirements are all met.

    Declares one data file so exactly one L3 criterion
    (``missing-from-manifest``) has a domain on the step — an
    "attained" L3 cell on this fixture is earned, not vacuous.
    """
    return {
        "sName": sName, "sDirectory": sName,
        "saOutputDataFiles": [sName + "/output.json"], "saPlotFiles": [],
        "dictVerification": {"sUser": "passed", "sUnitTest": "passed"},
    }


def _fdictWorkflowWithSteps(listSteps, sProjectRepoPath="/repo"):
    """Return a merged workflow shell around explicit step dicts."""
    return {
        "sProjectRepoPath": sProjectRepoPath,
        "listSteps": listSteps,
    }


def _fdictWorkflowWithCleanSteps(iStepCount, sProjectRepoPath="/repo"):
    """Return a workflow with ``iStepCount`` active, L1-clean steps."""
    return _fdictWorkflowWithSteps(
        [_fdictActiveCleanStep(f"step{iIndex}")
         for iIndex in range(iStepCount)],
        sProjectRepoPath=sProjectRepoPath,
    )


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


def _fdictCell(sState, iSatisfied, iTotal, bRegression=False):
    """Return one expected wire cell."""
    return {
        "sState": sState,
        "iSatisfied": iSatisfied,
        "iTotal": iTotal,
        "bRegression": bRegression,
    }


# ------------------------------------------------------------------------
# Independence: cells never propagate up or down the ladder
# ------------------------------------------------------------------------


def testCleanStepAttainsAllThreeLevelsWithFullCounts():
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], [], [],
    )
    assert dictStates == {0: {
        "s1": _fdictCell("attained", 3, 3),
        "s2": _fdictCell("attained", 2, 2),
        "s3": _fdictCell("attained", 1, 1),
    }}


def testLevel2PartialWhileLevel1None():
    """INDEPENDENCE: a fully failed L1 must not drag L2 down with it."""
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "dictVerification": {
            "sUser": "untested", "sUnitTest": "failed",
            "bUpstreamModified": True,
        },
    }
    listLevel1 = [_fdictStepBlocker(1, 0, "upstream-modified")]
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), listLevel1, listLevel2, [],
    )
    assert dictStates[0]["s1"] == _fdictCell("none", 0, 3)
    assert dictStates[0]["s2"] == _fdictCell("partial", 1, 2)


def testLevel3AttainedWhileLevel2Partial():
    """INDEPENDENCE: a partial L2 never blocks an otherwise-clean L3."""
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-zenodo-deposit")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], listLevel2, [],
    )
    assert dictStates[0]["s2"] == _fdictCell("partial", 1, 2)
    assert dictStates[0]["s3"] == _fdictCell("attained", 1, 1)


def testLevel3BlockerLeavesLevelsOneAndTwoAttained():
    """The fixture's only applicable L3 criterion fails, so its L3
    cell reads none — a failed sole requirement is red, not a
    flattering 4-of-5 partial."""
    listLevel3 = [_fdictStepBlocker(3, 0, "missing-from-manifest")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], [], listLevel3,
    )
    assert dictStates[0]["s1"]["sState"] == "attained"
    assert dictStates[0]["s2"]["sState"] == "attained"
    assert dictStates[0]["s3"] == _fdictCell("none", 0, 1)


def testBlockersOnlyAffectTheirOwnStep():
    listLevel1 = [_fdictStepBlocker(1, 1, "axis-not-green")]
    dictWorkflow = _fdictWorkflowWithCleanSteps(3)
    dictWorkflow["listSteps"][1]["dictVerification"]["sUnitTest"] = (
        "failed"
    )
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, listLevel1, [], [],
    )
    assert dictStates[0]["s1"]["sState"] == "attained"
    assert dictStates[1]["s1"] == _fdictCell("partial", 2, 3)
    assert dictStates[2]["s1"]["sState"] == "attained"


def testProjectionCoversEveryStepIndex():
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(4), [], [], [],
    )
    assert sorted(dictStates.keys()) == [0, 1, 2, 3]


# ------------------------------------------------------------------------
# L1 requirement counting (present axes + attestation + timing)
# ------------------------------------------------------------------------


def testLevel1TotalCountsEachPresentAxisPlusUserPlusTiming():
    dictStep = _fdictActiveCleanStep()
    dictStep["dictVerification"]["sQuantitative"] = "passed"
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("attained", 4, 4)


def testLevel1FailedAxisAndMissingAttestationCountedSeparately():
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "dictVerification": {
            "sUser": "untested", "sUnitTest": "failed",
            "sQualitative": "passed",
        },
    }
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 2, 4)


def testLevel1TimingRequirementFailsOnUpstreamModifiedFlag():
    dictStep = _fdictActiveCleanStep()
    dictStep["dictVerification"]["bUpstreamModified"] = True
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 2, 3)


def testLevel1TimingRequirementFailsOnScriptStaleBlocker():
    listLevel1 = [_fdictStepBlocker(1, 0, "script-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), listLevel1, [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 2, 3)


def testLevel1TimingRequirementFailsOnStaleAttestation():
    dictStep = _fdictActiveCleanStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"]["sLastUserUpdate"] = (
        "2026-06-01T00:00:00Z"
    )
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 1, 3)


# ------------------------------------------------------------------------
# not-started detection
# ------------------------------------------------------------------------


def testStepWithNoActivityIsNotStartedAtEveryLevel():
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "dictVerification": {
            "sUser": "untested", "sUnitTest": "untested",
        },
    }
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    for sLevelKey in ("s1", "s2", "s3"):
        assert dictStates[0][sLevelKey]["sState"] == "not-started"


def testBareStepDictIsNotStarted():
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([{"sName": "stepOne"}]), [], [], [],
    )
    assert dictStates[0]["s1"]["sState"] == "not-started"


def testInactiveStepWithOutputsOnDiskIsUnassessedAtEveryLevel():
    """No recorded activity + declared outputs on disk = unassessed.

    The poll's ``dictMaxMtimeByStep`` keys are step-index STRINGS —
    the discriminator must accept the wire shape verbatim, or every
    on-disk step silently falls back to not-started (the 54-grey-steps
    presentation this state exists to fix)."""
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "saOutputDataFiles": ["stepOne/output.json"],
        "dictVerification": {"sUser": "untested"},
    }
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
        dictMaxMtimeByStep={"0": "1750000000"},
    )
    for sLevelKey in ("s1", "s2", "s3"):
        assert dictStates[0][sLevelKey]["sState"] == "unassessed"


def testInactiveStepWithoutOnDiskEntryStaysNotStarted():
    """An mtime entry for a DIFFERENT step must not leak across."""
    dictStep = {"sName": "stepOne", "sDirectory": "stepOne"}
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
        dictMaxMtimeByStep={"7": "1750000000"},
    )
    assert dictStates[0]["s1"]["sState"] == "not-started"


def testRecordedActivityBeatsUnassessed():
    """Any recorded activity renders count-derived states, never the
    inactivity override — outputs on disk must not mask a real
    assessment."""
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "dictRunStats": {"fLastDurationSeconds": 1.0},
        "dictVerification": {"sUser": "untested"},
    }
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
        dictMaxMtimeByStep={"0": "1750000000"},
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 1, 2)


def testRunStatsAloneCountAsActivity():
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "dictRunStats": {"fLastDurationSeconds": 1.0},
        "dictVerification": {"sUser": "untested"},
    }
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 1, 2)


def testAttestationAloneCountsAsActivity():
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "dictVerification": {"sUser": "passed"},
    }
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("attained", 2, 2)


# ------------------------------------------------------------------------
# unknown: only from a stale sync-verify cache, only at L2
# ------------------------------------------------------------------------


def testGithubVerifyStaleMakesLevelTwoUnknownNeverAttained():
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], listLevel2, [],
    )
    assert dictStates[0]["s2"] == _fdictCell("unknown", 1, 2)


def testZenodoVerifyStaleAlsoMakesLevelTwoUnknown():
    listLevel2 = [_fdictWorkflowBlocker(2, "zenodo-verify-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], listLevel2, [],
    )
    assert dictStates[0]["s2"]["sState"] == "unknown"


def testStaleCacheDoesNotTouchLevelsOneAndThree():
    """INDEPENDENCE: unknown no longer propagates to L3."""
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], listLevel2, [],
    )
    assert dictStates[0]["s1"]["sState"] == "attained"
    assert dictStates[0]["s3"]["sState"] == "attained"


def testStaleCacheDominatesOwnLevel2Blocker():
    """A stale cache makes the whole L2 cell unknown even when one
    criterion (figure freeze) is known to fail — never attained, and
    never a false-precision partial from an untrustworthy cache."""
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["dictRemotes"] = {"overleaf": {"sProjectId": "abc"}}
    dictWorkflow["listSteps"][0]["saPlotFiles"] = ["plot.pdf"]
    listLevel2 = [
        _fdictWorkflowBlocker(2, "github-verify-stale"),
        _fdictStepBlocker(2, 0, "figure-not-frozen"),
    ]
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, [],
    )
    assert dictStates[0]["s2"] == _fdictCell("unknown", 1, 3)


def testPerStepLevel2BlockerAloneIsPartialNotUnknown():
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], listLevel2, [],
    )
    assert dictStates[0]["s2"] == _fdictCell("partial", 1, 2)


def testFigureFreezeOnlyApplicableWithOverleafBindingAndPlots():
    dictWorkflow = _fdictWorkflowWithCleanSteps(2)
    dictWorkflow["dictRemotes"] = {"overleaf": {"sProjectId": "abc"}}
    dictWorkflow["listSteps"][0]["saPlotFiles"] = ["plot.pdf"]
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, [], [], [],
    )
    assert dictStates[0]["s2"]["iTotal"] == 3
    assert dictStates[1]["s2"]["iTotal"] == 2


# ------------------------------------------------------------------------
# not-applicable: L3 with no applicable criterion, honest L3 counting
# ------------------------------------------------------------------------


def _fdictAttestedArtifactFreeStep(sName="aiDeclaration"):
    """Return an active interactive step with nothing to reproduce."""
    return {
        "sName": sName, "sDirectory": sName,
        "dictVerification": {"sUser": "passed"},
    }


def testArtifactFreeStepReadsNotApplicableAtLevelThree():
    """FALSIFICATION TARGET (vacuous-L3 bug): an attested interactive
    step with no outputs, scripts, binaries, or randomness flag must
    read not-applicable — never a vacuous attained (the GJ 1132 I02
    symptom: L3 'verified' while the workflow was not even L2)."""
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([_fdictAttestedArtifactFreeStep()]),
        [], [], [],
    )
    assert dictStates[0]["s3"] == _fdictCell("not-applicable", 0, 0)
    assert dictStates[0]["s1"]["sState"] == "attained"


def testStrayStampOnNotApplicableCellStaysInert():
    """A high-water stamp minted while the cell read vacuously
    attained must not manufacture a regression warning."""
    dictStep = _fdictAttestedArtifactFreeStep()
    dictStep["dictLevelHighWater"] = {"3": "2026-06-12T00:00:00Z"}
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s3"]["sState"] == "not-applicable"
    assert dictStates[0]["s3"]["bRegression"] is False


def testNotStartedOutranksNotApplicable():
    """A never-touched artifact-free step reads not-started on all
    three cells — uniform, like every other untouched step."""
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([{"sName": "stepOne"}]), [], [], [],
    )
    assert dictStates[0]["s3"]["sState"] == "not-started"


def testEveryFailingCriterionCountsNotJustTheDominantOne():
    """FALSIFICATION TARGET (L3-never-red bug): a step failing every
    applicable criterion must read none with zero satisfied — the old
    dominant-only counting could never drop below 4 of 5."""
    dictStep = _fdictActiveCleanStep()
    dictStep["bUnseededRandomnessWarning"] = True
    dictBlocker = _fdictStepBlocker(3, 0, "missing-from-manifest")
    dictBlocker["listFailingCriteria"] = [
        "missing-from-manifest", "nondeterminism-undeclared",
    ]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [dictBlocker],
    )
    assert dictStates[0]["s3"] == _fdictCell("none", 0, 2)


def testBlockerWithoutFailingCriteriaFieldFallsBackToDominant():
    """A warm cache can hold entries minted before
    ``listFailingCriteria`` existed; the dominant criterion must
    still count as failing."""
    listLevel3 = [_fdictStepBlocker(3, 0, "missing-from-manifest")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], [], listLevel3,
    )
    assert dictStates[0]["s3"]["iSatisfied"] == 0


def testAiDeclarationStepLevel1ReadsNotApplicable():
    """RULING 2026-07-02: the AI declaration only has meaning at
    publication, so the declaration step has no L1 requirements — its
    L1 cell is a dash, and its sign-off is counted on its L2 cell."""
    dictStep = _fdictAttestedArtifactFreeStep("aiDeclaration")
    dictStep["sStepKind"] = "ai-declaration"
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("not-applicable", 0, 0)
    assert dictStates[0]["s2"]["iTotal"] == 3


def testDeclarationFileMakesLevelThreeApplicable():
    """The declaration file is canonical, so pinning it in the
    manifest is a real Level 3 requirement — the declaration step's
    L3 cell stops being a dash once a file is declared."""
    dictStep = _fdictAttestedArtifactFreeStep("aiDeclaration")
    dictStep["sStepKind"] = "ai-declaration"
    dictStep["sDeclarationFile"] = "AI_USAGE.md"
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], [], [],
    )
    assert dictStates[0]["s3"] == _fdictCell("attained", 1, 1)
    assert dictStates[0]["s1"]["sState"] == "not-applicable"


def testUnattestedAiDeclarationDentsItsOwnLevel2Cell():
    dictStep = {
        "sName": "aiDeclaration", "sDirectory": "aiDeclaration",
        "sStepKind": "ai-declaration",
        "dictRunStats": {"fLastDurationSeconds": 1.0},
        "dictVerification": {"sUser": "untested"},
    }
    listLevel2 = [_fdictStepBlocker(2, 0, "ai-declaration-unattested")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithSteps([dictStep]), [], listLevel2, [],
    )
    assert dictStates[0]["s1"]["sState"] == "not-applicable"
    assert dictStates[0]["s2"] == _fdictCell("partial", 2, 3)


def testNotApplicableRungCannotBlockTheStepLadder():
    dictStates = _fdictThreeCells(
        "attained", "attained", "not-applicable",
    )
    dictStates["s3"] = _fdictCell("not-applicable", 0, 0)
    assert fiStepAICSLevel(dictStates) == 3
    assert fiLowestNonAttainedLevel(dictStates) == 4


# ------------------------------------------------------------------------
# Regression flag from the high-water ratchet stamps
# ------------------------------------------------------------------------


def testRegressionFlagWhenStampedLevelNotAttained():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["listSteps"][0]["dictLevelHighWater"] = {
        "2": "2026-06-01T00:00:00Z",
    }
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, [],
    )
    assert dictStates[0]["s2"]["bRegression"] is True
    assert dictStates[0]["s1"]["bRegression"] is False


def testNoRegressionFlagWhenStampedLevelStillAttained():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["listSteps"][0]["dictLevelHighWater"] = {
        "1": "2026-06-01T00:00:00Z",
    }
    dictStates = fdictComputeStepLevelStates(dictWorkflow, [], [], [])
    assert dictStates[0]["s1"]["sState"] == "attained"
    assert dictStates[0]["s1"]["bRegression"] is False


def testNoRegressionFlagWithoutStamp():
    listLevel3 = [_fdictStepBlocker(3, 0, "script-not-pinned")]
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], [], listLevel3,
    )
    assert dictStates[0]["s3"]["sState"] == "partial"
    assert dictStates[0]["s3"]["bRegression"] is False


# ------------------------------------------------------------------------
# fiStepAICSLevel / fiLowestNonAttainedLevel over the new cells
# ------------------------------------------------------------------------


def _fdictThreeCells(sStateOne, sStateTwo, sStateThree):
    """Return a step-states dict from three bare state names."""
    return {
        "s1": _fdictCell(sStateOne, 0, 1),
        "s2": _fdictCell(sStateTwo, 0, 1),
        "s3": _fdictCell(sStateThree, 0, 1),
    }


def testStepLevelZeroWhenLevelOneNotAttained():
    assert fiStepAICSLevel(
        _fdictThreeCells("partial", "attained", "attained"),
    ) == 0


def testStepLevelThreeWhenAllAttained():
    dictStates = _fdictThreeCells("attained", "attained", "attained")
    assert fiStepAICSLevel(dictStates) == 3
    assert fiLowestNonAttainedLevel(dictStates) == 4


def testStepLevelContiguityIgnoresAttainedAboveGap():
    dictStates = _fdictThreeCells("attained", "partial", "attained")
    assert fiStepAICSLevel(dictStates) == 1
    assert fiLowestNonAttainedLevel(dictStates) == 2


def testStepLevelHandlesEmptyOrNoneStates():
    assert fiStepAICSLevel({}) == 0
    assert fiStepAICSLevel(None) == 0
    assert fiLowestNonAttainedLevel({}) == 1


# ------------------------------------------------------------------------
# fdictComputeStepLevelWarnings — the consolidated regression column
# ------------------------------------------------------------------------


def testNoWarningWhenEverythingAttained():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictStates = fdictComputeStepLevelStates(dictWorkflow, [], [], [])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStates, [],
    )
    assert dictWarnings[0] == {
        "iLowestNonAttainedLevel": 4,
        "iWarningLevel": None,
        "sWarningSeverity": None,
        "sWarningHint": "",
    }


def testRegressionAboveLowestNonAttainedLevelEmitsNoWarning():
    """USER EXAMPLE: L1 attained + L2 partial + L3 regressed must show
    NO warning — the lowest non-attained level is 2 and the regression
    lives at 3, above the researcher's next action."""
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["listSteps"][0]["dictLevelHighWater"] = {
        "3": "2026-06-01T00:00:00Z",
    }
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    listLevel3 = [_fdictStepBlocker(3, 0, "missing-from-manifest")]
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, listLevel3,
    )
    assert dictStates[0]["s3"]["bRegression"] is True
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStates, [],
    )
    assert dictWarnings[0]["iLowestNonAttainedLevel"] == 2
    assert dictWarnings[0]["iWarningLevel"] is None


def testRegressionAtLowestNonAttainedLevelWarnsOrange():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["listSteps"][0]["dictLevelHighWater"] = {
        "2": "2026-06-01T00:00:00Z",
    }
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, [],
    )
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStates, [],
    )
    assert dictWarnings[0]["iWarningLevel"] == 2
    assert dictWarnings[0]["sWarningSeverity"] == "orange"
    assert "regressed" in dictWarnings[0]["sWarningHint"]


def testTimingBlockerAtLevelOneWarnsOrangeWithoutRegression():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["listSteps"][0]["dictVerification"][
        "bUpstreamModified"] = True
    listLevel1 = [_fdictStepBlocker(1, 0, "upstream-modified")]
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, listLevel1, [], [],
    )
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStates, listLevel1,
    )
    assert dictWarnings[0]["iWarningLevel"] == 1
    assert dictWarnings[0]["sWarningSeverity"] == "orange"
    assert "re-run" in dictWarnings[0]["sWarningHint"]


def testFailedTestsAtLevelOneRegressionWarnsRed():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["listSteps"][0]["dictVerification"]["sUnitTest"] = (
        "failed"
    )
    dictWorkflow["listSteps"][0]["dictLevelHighWater"] = {
        "1": "2026-06-01T00:00:00Z",
    }
    dictStates = fdictComputeStepLevelStates(dictWorkflow, [], [], [])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStates, [],
    )
    assert dictWarnings[0]["iWarningLevel"] == 1
    assert dictWarnings[0]["sWarningSeverity"] == "red"


def testNonAttainedWithoutRegressionOrTimingEmitsNoWarning():
    """A merely partial level (e.g. tests never run) is not a warning;
    the level cells already carry that truth."""
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-github-mirror")]
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, [], listLevel2, [],
    )
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStates, [],
    )
    assert dictWarnings[0]["iLowestNonAttainedLevel"] == 2
    assert dictWarnings[0]["iWarningLevel"] is None


# ------------------------------------------------------------------------
# fdictComputeWorkflowScopeLevelStates
# ------------------------------------------------------------------------


def testWorkflowScopeAllAttainedWhenCleanWithRepo():
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], [],
    )
    assert dictStates == {
        "s1": _fdictCell("attained", 1, 1),
        "s2": _fdictCell("attained", 2, 2),
        "s3": _fdictCell("attained", 6, 6),
    }


def testWorkflowScopeRepoMissingZeroesEveryLevel():
    """``flistLevel{2,3}Blockers`` return [] when the repo is missing;
    empty lists must not be mistaken for attainment."""
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1, sProjectRepoPath=""), [], [],
    )
    assert dictStates["s1"] == _fdictCell("none", 0, 1)
    assert dictStates["s2"] == _fdictCell("none", 0, 2)
    assert dictStates["s3"] == _fdictCell("none", 0, 6)


def testWorkflowScopeExcludesMissingAiDeclarationStep():
    """The missing-ai-declaration-step blocker is re-homed to a ghost
    step row; the header must not double-report it."""
    listLevel2 = [
        _fdictWorkflowBlocker(2, "missing-ai-declaration-step"),
    ]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1), listLevel2, [],
    )
    assert dictStates["s2"] == _fdictCell("attained", 2, 2)


def testWorkflowScopeVerifyStaleIsAnUnsatisfiedRequirement():
    listLevel2 = [_fdictWorkflowBlocker(2, "github-verify-stale")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1), listLevel2, [],
    )
    assert dictStates["s2"] == _fdictCell("partial", 1, 2)


def testWorkflowScopeBothCachesStaleReadsNone():
    listLevel2 = [
        _fdictWorkflowBlocker(2, "github-verify-stale"),
        _fdictWorkflowBlocker(2, "zenodo-verify-stale"),
    ]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1), listLevel2, [],
    )
    assert dictStates["s2"] == _fdictCell("none", 0, 2)


def testWorkflowScopeArxivCriteriaApplicableOnlyWithArxivConnection():
    """The two arXiv criteria join the L2 cell only when an arXiv
    submission is recorded — an Overleaf binding alone must not
    widen the requirement set (the arXiv claim is opt-in)."""
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["dictRemotes"] = {
        "overleaf": {"sProjectId": "abc"},
        "arxiv": {"sArxivId": "2401.00001"},
    }
    listLevel2 = [_fdictWorkflowBlocker(2, "arxiv-mismatch")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, listLevel2, [],
    )
    assert dictStates["s2"] == _fdictCell("partial", 3, 4)


def testWorkflowScopeOverleafBindingAloneAddsNoArxivCriteria():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["dictRemotes"] = {"overleaf": {"sProjectId": "abc"}}
    dictStates = fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, [], [],
    )
    assert dictStates["s2"] == _fdictCell("attained", 2, 2)


def testWorkflowScopeIgnoresPerStepBlockerEntries():
    listLevel2 = [_fdictStepBlocker(2, 0, "not-in-zenodo-deposit")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1), listLevel2, [],
    )
    assert dictStates["s2"]["sState"] == "attained"


def testWorkflowScopeLevel3BlockerOnlyDentsLevelThree():
    listLevel3 = [_fdictWorkflowBlocker(3, "dockerfile-not-pinned")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        _fdictWorkflowWithCleanSteps(1), [], listLevel3,
    )
    assert dictStates["s2"]["sState"] == "attained"
    assert dictStates["s3"] == _fdictCell("partial", 5, 6)


def testWorkflowScopeRegressionFlagFromWorkflowHighWater():
    dictWorkflow = _fdictWorkflowWithCleanSteps(1)
    dictWorkflow["dictWorkflowLevelHighWater"] = {
        "3": "2026-06-01T00:00:00Z",
    }
    listLevel3 = [_fdictWorkflowBlocker(3, "dependency-lock-missing")]
    dictStates = fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, [], listLevel3,
    )
    assert dictStates["s3"]["bRegression"] is True
    assert dictStates["s2"]["bRegression"] is False


# ------------------------------------------------------------------------
# Integration with the real L1 blocker generator
# ------------------------------------------------------------------------


def testProjectionAgreesWithRealLevel1Blockers():
    from vaibify.reproducibility.levelGates import flistLevel1Blockers
    dictStep = {
        "sName": "stepOne", "sDirectory": "stepOne",
        "saOutputDataFiles": [], "saPlotFiles": [],
        "dictVerification": {"sUser": "untested", "sUnitTest": "failed"},
    }
    dictWorkflow = {
        "sProjectRepoPath": "/repo", "listSteps": [dictStep],
    }
    listLevel1 = flistLevel1Blockers(dictWorkflow, {}, "/repo")
    dictStates = fdictComputeStepLevelStates(
        dictWorkflow, listLevel1, [], [],
    )
    assert dictStates[0]["s1"] == _fdictCell("partial", 1, 3)
    assert fiStepAICSLevel(dictStates[0]) == 0
