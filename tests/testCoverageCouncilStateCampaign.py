"""The campaign record's bounded configuration surface refuses loudly.

``agentCouncilCampaign`` is the single authority over what a council
may be configured to do and which states a record may enter. Each test
here drives one refusal and asserts the sentence a researcher reads,
because a refusal that names the wrong setting sends them to fix the
wrong thing. Pure module: no doubles.
"""

import pytest

from vaibify.gui import agentCouncilCampaign
from vaibify.gui.agentCouncilCampaign import (
    CouncilConfigurationError,
    CouncilProtocolError,
)


def flistBuildTwoParticipants():
    """Return two participants covering two distinct models."""
    return [
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelOne"),
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelTwo"),
    ]


def fdictBuildCampaign(**dictKeywords):
    """Create a campaign with two valid participants and overrides."""
    return agentCouncilCampaign.fdictCreateCampaign(
        dictKeywords.pop("sQuestion", "Which configuration converges?"),
        dictKeywords.pop("listParticipants", flistBuildTwoParticipants()),
        **dictKeywords)


# ----- names and kinds -----------------------------------------------------


def testCampaignKindDefaultsOldRecordsToPlanning():
    assert agentCouncilCampaign.fsReadCampaignKind({}) == "planning"
    assert agentCouncilCampaign.fsReadCampaignKind(
        {"sCampaignKind": ""}) == "planning"
    assert agentCouncilCampaign.fsReadCampaignKind(
        {"sCampaignKind": "implementation"}) == "implementation"


def testUniqueNameGivesUpAfterTheSuffixSpaceIsExhausted():
    listTaken = ["Sampler study"] + [
        f"Sampler study {iSuffix}" for iSuffix in range(2, 1000)]
    with pytest.raises(CouncilConfigurationError) as excInfo:
        agentCouncilCampaign.fsComposeUniqueCampaignName(
            "Sampler study", "ignored", listTaken)
    assert "too many councils are already named like 'Sampler study'" in (
        str(excInfo.value))


def testUniqueNameTakesTheFirstFreeSuffixCaseInsensitively():
    assert agentCouncilCampaign.fsComposeUniqueCampaignName(
        "Sampler study", "ignored",
        ["sampler STUDY", "Sampler study 2"]) == "Sampler study 3"


@pytest.mark.parametrize("sProvider,sModel", [
    ("", "modelOne"), ("claude", ""), (None, "modelOne")])
def testParticipantNeedsBothProviderAndModel(sProvider, sModel):
    with pytest.raises(CouncilConfigurationError) as excInfo:
        agentCouncilCampaign.fdictCreateParticipant(sProvider, sModel)
    assert "needs a provider and a requested model" in str(excInfo.value)


# ----- settings --------------------------------------------------------------


@pytest.mark.parametrize("dictSettings,sFragment", [
    ({"sConsensusRule": "majority"}, "unknown council setting "
                                     "'sConsensusRule'"),
    ({"iMinimumRounds": 0}, "iMinimumRounds must be at least 1"),
    ({"iMinimumRounds": 3, "iMaximumRounds": 2},
     "iMaximumRounds cannot be below iMinimumRounds"),
    ({"iTurnWallClockSeconds": 59}, "iTurnWallClockSeconds must be"),
    ({"iTurnWallClockSeconds": 43201}, "iTurnWallClockSeconds must be"),
    ({"iTurnWallClockSeconds": 600.0}, "iTurnWallClockSeconds must be"),
    ({"iMaximumConcurrentTurns": 0},
     "iMaximumConcurrentTurns must be at least 1"),
    ({"sExecutionPermission": "writeTheProject"},
     "unknown execution permission"),
])
def testSettingsOutsideTheBoundedSurfaceAreRefused(dictSettings, sFragment):
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(dictSettings=dictSettings)
    assert sFragment in str(excInfo.value)


def testSettingsAtTheWallClockBoundsAreAdmitted():
    for iSeconds in (60, 43200):
        dictCampaign = fdictBuildCampaign(
            dictSettings={"iTurnWallClockSeconds": iSeconds})
        assert dictCampaign["dictSettings"]["iTurnWallClockSeconds"] == (
            iSeconds)


# ----- project identity ------------------------------------------------------


def testProjectIdentityMustBeAMapping():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(dictProjectIdentity=["projectAlpha"])
    assert "must be a mapping" in str(excInfo.value)


def testProjectIdentityRefusesAnUnknownKey():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(dictProjectIdentity={
            "sResourceName": "projectAlpha", "sUnexpectedKey": "value"})
    assert "must carry only" in str(excInfo.value)


def testProjectIdentityRefusesANonStringValue():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(dictProjectIdentity={"sResourceName": 42})
    assert "'sResourceName' must be a string" in str(excInfo.value)


def testProjectIdentityBackFillsMissingKeysAsUnbound():
    dictCampaign = fdictBuildCampaign(dictProjectIdentity={
        "sResourceName": "projectAlpha",
        "sProjectRepoPath": "/workspace/repoAlpha"})
    dictIdentity = dictCampaign["dictProjectIdentity"]
    assert sorted(dictIdentity) == sorted(
        agentCouncilCampaign.LIST_PROJECT_IDENTITY_KEYS)
    assert dictIdentity["sImageIdentity"] == ""
    assert dictIdentity["sResourceName"] == "projectAlpha"


# ----- campaign creation -----------------------------------------------------


def testCampaignRequiresAQuestion():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(sQuestion="")
    assert "question is required" in str(excInfo.value)


def testCampaignRefusesAnUnknownKind():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(sCampaignKind="brainstorm")
    assert "unknown campaign kind 'brainstorm'" in str(excInfo.value)


def testImplementationCouncilNeedsTheAcceptedPlan():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(sCampaignKind="implementation")
    assert "needs the accepted plan" in str(excInfo.value)
    dictCampaign = fdictBuildCampaign(
        sCampaignKind="implementation", sSeedPlanDocument="# Plan\n",
        sSourceCampaignId="campaign-source")
    assert dictCampaign["sSeedPlanDocument"] == "# Plan\n"
    assert dictCampaign["sSourceCampaignId"] == "campaign-source"


def testCampaignNeedsTwoParticipants():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(listParticipants=flistBuildTwoParticipants()[:1])
    assert "at least two participants" in str(excInfo.value)


def testCampaignNeedsTwoDistinctModels():
    listParticipants = [
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelOne"),
        agentCouncilCampaign.fdictCreateParticipant(
            "claude", "modelOne", "security"),
    ]
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(listParticipants=listParticipants)
    assert "two distinct models" in str(excInfo.value)


def testChairbotMustBeAConfiguredParticipant():
    with pytest.raises(CouncilConfigurationError) as excInfo:
        fdictBuildCampaign(sChairbotParticipantId="participant-stranger")
    assert "chairbot must be one of the configured" in str(excInfo.value)


def testChairbotDefaultsToTheFirstParticipant():
    listParticipants = flistBuildTwoParticipants()
    dictCampaign = fdictBuildCampaign(listParticipants=listParticipants)
    assert dictCampaign["sChairbotParticipantId"] == (
        listParticipants[0]["sParticipantId"])
    assert dictCampaign["sState"] == "draft"


# ----- transitions and restoration --------------------------------------------


def testTransitionRefusesAnUnknownStateAndRecordsNothing():
    dictCampaign = fdictBuildCampaign()
    with pytest.raises(CouncilProtocolError) as excInfo:
        agentCouncilCampaign.fnTransitionCampaignState(
            dictCampaign, "halfFinished", "because")
    assert "unknown campaign state 'halfFinished'" in str(excInfo.value)
    assert dictCampaign["listStateTransitions"] == []
    assert dictCampaign["sState"] == "draft"


def testRestoreRefusesMetadataThatIsNotAMapping():
    with pytest.raises(CouncilProtocolError) as excInfo:
        agentCouncilCampaign.fdictRestoreCampaignFromMetadata(["planning"])
    assert "must be a mapping" in str(excInfo.value)


def testRestoreRefusesAnUnknownState():
    dictCampaign = fdictBuildCampaign()
    dictCampaign["sState"] = "halfFinished"
    with pytest.raises(CouncilProtocolError) as excInfo:
        agentCouncilCampaign.fdictRestoreCampaignFromMetadata(dictCampaign)
    assert "unknown state 'halfFinished'" in str(excInfo.value)


def testRestoreRefusesARecordMissingARequiredKey():
    dictCampaign = fdictBuildCampaign()
    del dictCampaign["dictSettings"]
    with pytest.raises(CouncilProtocolError) as excInfo:
        agentCouncilCampaign.fdictRestoreCampaignFromMetadata(dictCampaign)
    assert "missing 'dictSettings'" in str(excInfo.value)


def testRestoreBackFillsANameFromTheQuestionAndCopies():
    dictCampaign = fdictBuildCampaign(
        sQuestion="Should stepAlpha stream its output, or buffer it?")
    del dictCampaign["sCampaignName"]
    dictRestored = agentCouncilCampaign.fdictRestoreCampaignFromMetadata(
        dictCampaign)
    assert dictRestored["sCampaignName"] == (
        "Should stepAlpha stream its output or")
    assert "sCampaignName" not in dictCampaign
    dictRestored["listParticipants"].clear()
    assert len(dictCampaign["listParticipants"]) == 2


# ----- the provider seam -----------------------------------------------------


def testTheProviderConnectionSeamIsAbstract():
    import asyncio
    connectionAbstract = agentCouncilCampaign.CouncilProviderConnection()
    for sCoroutineName in ("fdictPrepareImmutableContext", "fnStartTurn"):
        with pytest.raises(NotImplementedError):
            asyncio.run(getattr(connectionAbstract, sCoroutineName)({}))
    for sCoroutineName in ("fdictCollectStructuredResult",
                           "fsReportCompletion"):
        with pytest.raises(NotImplementedError):
            asyncio.run(getattr(connectionAbstract, sCoroutineName)())
    with pytest.raises(NotImplementedError):
        connectionAbstract.fiterStreamNormalizedEvents()


# ----- the cross-project principal predicate --------------------------------


def testAnUnboundIdentityMatchesNoPrincipal():
    dictCampaign = fdictBuildCampaign(dictProjectIdentity={
        "sResourceName": "projectAlpha",
        "sProjectRepoPath": "/workspace/repoAlpha"})
    assert agentCouncilCampaign.fbCampaignMatchesPrincipal(
        dictCampaign, "projectAlpha", "/workspace/repoAlpha") is True
    assert agentCouncilCampaign.fbCampaignMatchesPrincipal(
        dictCampaign, "", "/workspace/repoAlpha") is False
    assert agentCouncilCampaign.fbCampaignMatchesPrincipal(
        dictCampaign, "projectAlpha", "") is False
    assert agentCouncilCampaign.fbCampaignMatchesPrincipal(
        dictCampaign, "projectAlpha", "/workspace/repoBeta") is False
    dictUnbound = fdictBuildCampaign()
    assert agentCouncilCampaign.fbCampaignMatchesPrincipal(
        dictUnbound, "projectAlpha", "/workspace/repoAlpha") is False
