"""Tests for the campaign name: validation, derivation, disambiguation.

The name is display-only — ``sCampaignId`` stays the only identity —
but it is the one thing that tells two campaigns apart in a listing,
and a researcher iterating on one prompt gets a list where every row is
the same sentence (a wrong pick cost a live 13-question gate,
2026-08-25). The suffixing is BEST-EFFORT disambiguation, not enforced
uniqueness: the caller reads existing names then creates, so two
concurrent starts can both pass — which is why nothing may ever key on
the name.
"""

import pytest

from vaibify.gui.agentCouncilCampaign import (
    CouncilConfigurationError,
    I_MAX_CAMPAIGN_NAME_LENGTH,
    fsComposeUniqueCampaignName,
    fsValidateCampaignName,
)


def testAValidNameIsKeptAndWhitespaceCollapses():
    assert fsValidateCampaignName("  MCMC   512  Chains ") == (
        "MCMC 512 Chains")


def testAnEmptyNameARunawayNameAndABadFirstCharacterAreRefused():
    with pytest.raises(CouncilConfigurationError):
        fsValidateCampaignName("   ")
    with pytest.raises(CouncilConfigurationError):
        fsValidateCampaignName("x" * (I_MAX_CAMPAIGN_NAME_LENGTH + 1))
    with pytest.raises(CouncilConfigurationError):
        fsValidateCampaignName("-starts-with-a-hyphen")
    with pytest.raises(CouncilConfigurationError):
        fsValidateCampaignName("no_underscores")


def testABlankRequestDerivesFromTheQuestionsOpeningWords():
    sName = fsComposeUniqueCampaignName(
        "", "How should the retry policy handle provider rate limits "
        "over long deliberations?", [])
    assert sName == "How should the retry policy handle"


@pytest.mark.falsification
def testACollidingNameGainsASuffix():
    """Two same-named rows in one listing are indistinguishable.

    Kills: dropping the suffix loop, so the second council stores the
    identical name and the researcher picks between two twin rows.
    """
    sName = fsComposeUniqueCampaignName(
        "Retry policy", "irrelevant", ["Retry policy"])
    assert sName == "Retry policy 2"
    sThird = fsComposeUniqueCampaignName(
        "Retry policy", "irrelevant", ["Retry policy", "Retry policy 2"])
    assert sThird == "Retry policy 3"


@pytest.mark.falsification
def testTheCollisionScanIsCaseInsensitive():
    """"retry policy" and "Retry Policy" are the same row to a person.

    Kills: comparing names case-sensitively, which lets two rows differ
    only in capitalization — a distinction a listing font makes
    invisible exactly when the researcher is choosing quickly.

    The EXISTING name is the mixed-case one on purpose: the compose
    path casefolds the requested base before comparing, so an
    already-lowercase existing name still collides under a mutation
    that stops casefolding the existing set — a fixture collapse this
    test survived once (2026-08-26).
    """
    sName = fsComposeUniqueCampaignName(
        "retry policy", "irrelevant", ["Retry Policy"])
    assert sName == "retry policy 2"
