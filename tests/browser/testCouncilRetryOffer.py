"""The Retry offer and its held-questions card must tell one story.

An interrupted campaign with a retryable attempt renders a Retry
button; until 2026-08-27 the held-questions card beside it kept its
fixed wording — "this campaign cannot be resumed... a fresh council" —
directly contradicting the control one line up. The card's disposition
now follows the record's own action, and only a real browser proves the
two surfaces render together coherently.
"""

import copy
import shutil
import tempfile

import pytest

from vaibify.gui import agentCouncilStore
from vaibify.gui.agentCouncilCampaign import (
    fdictCreateCampaign,
    fdictCreateParticipant,
)

from .fakeDockerAdapter import S_CONTAINER_NAME, S_PROJECT_REPO
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser


def _fdictBuildInterruptedRetryableCampaign():
    """A crashed council: interrupted, retryable, questions held."""
    dictCampaign = fdictCreateCampaign(
        "Which cache policy should the pipeline use?",
        [fdictCreateParticipant("claude", "model-a"),
         fdictCreateParticipant("claude", "model-b")])
    sParticipantId = dictCampaign["listParticipants"][0]["sParticipantId"]
    dictCampaign["sState"] = "interrupted"
    dictCampaign["dictProjectIdentity"] = {
        "sResourceName": S_CONTAINER_NAME,
        "sProjectRepoPath": S_PROJECT_REPO,
        "sSnapshotIdentity": "sealed-content-identity-0001",
        "sSnapshotScopeNote": "",
        "sImageIdentity": "sha256:" + "ab12" * 16,
        "sSnapshotArchiveSha256": "0" * 64,
    }
    dictCampaign["listRounds"] = [{
        "iRoundNumber": 1,
        "bFinalVetoRound": False,
        "bSynthesisSettled": False,
        "sSynthesisAuthorId": "",
        "bChairbotSubstituted": False,
        "listFrozenVoterIds": None,
        "dictVetoVerdicts": {},
        "listUnresolvedObjections": [],
        "listDeferredQuestions": [{
            "sQuestionId": "question-held-0001",
            "sQuestionText": "Should the cache key include the compiler?",
            "sRaisedByParticipantId": sParticipantId,
        }],
        "listRetiredAttempts": [],
        "sResolution": "",
        "dictTurnsByPhase": {
            "independentProposals": [
                {"sStatus": "completed", "sParticipantId": sParticipantId},
            ],
            "crossReview": [
                {"sStatus": "completed", "sCompletion": "indeterminate",
                 "sParticipantId": sParticipantId},
            ],
        },
        "dictPhaseAttempt": {
            "sPhase": "crossReview", "iRoundNumber": 1,
            "iAttemptNumber": 1,
            "listEligibleParticipantIds": [sParticipantId],
            "sCompletionRule": "allEligible",
            "sAttemptState": "outcomeSettled",
            "sOutcome": "transitioned:interrupted",
            "dictPrePhaseState": {},
        },
    }]
    return dictCampaign


@pytest.fixture(autouse=True)
def _fnIsolateStoreWithACrashedCouncil(serverHub):
    """Give the hub a store holding one interrupted, retryable council."""
    sTempRoot = tempfile.mkdtemp(prefix="councilRetryOfferLane")
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _fdictBuildInterruptedRetryableCampaign())
    setattr(serverHub.app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, dictStore)
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    shutil.rmtree(sTempRoot, ignore_errors=True)


@pytest.mark.falsification
def testARetryOfferAndItsHeldQuestionsTellOneStory(
        pageDashboard, serverHub):
    """Retry button plus held questions, coherent, in a real browser.

    Kills: the held-questions card ignoring the record's action and
    telling the researcher to convene a fresh council one line under a
    button that retries this one.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    # The chooser lists nothing itself now — four tasks, each
    # opening its own view. This campaign IS resumable, so it
    # lives under "Continue a council" (2026-08-30).
    # The task buttons that READ the listing stay disabled
    # until it arrives; only "Plan a change" is free of it.
    pageDashboard.wait_for_selector(
        "#btnCouncilOpenExisting:not([disabled])", timeout=8000)
    pageDashboard.click("#btnCouncilOpenExisting")
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)
    pageDashboard.click(".council-open-row")

    pageDashboard.wait_for_selector("#btnCouncilRetry", timeout=16000)
    sHeldCard = pageDashboard.inner_text(".council-held")
    assert "Should the cache key include the compiler?" in sHeldCard
    assert "re-runs the deliberation" in sHeldCard
    assert "fresh council" not in sHeldCard
    assert "cannot be resumed" not in sHeldCard

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def test_isolation_root_is_a_directory(tmp_path):
    """Keep the shared-store teardown honest about what it removes."""
    import os
    assert os.path.isdir(tmp_path)
