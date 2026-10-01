"""A mentioned credential costs the span, not the plan.

``fjsonRedactCredentialsInRecord`` replaced a WHOLE string with the
redaction marker whenever any part of it matched a credential shape, so a
plan or patch that merely quoted an ``Authorization:`` header was written
as ``[redacted-credential]`` and nothing else. The sealed SHA-256 was then
computed over the composed text, bytes that were never written, and an
implementation council could be seeded with the marker as its "plan".
These tests pin the repaired behavior end to end: the real accept route,
the real store, the file on disk.
"""

import hashlib
import os
from unittest.mock import patch

import pytest

from tests.testCouncilRoutes import (  # noqa: F401 -- fixture wiring
    S_CONTAINER_ID,
    _fnWaitForCampaignState,
    _sStartOneCampaign,
    eventTurnGate,
    tOwnerClient,
)
from vaibify.gui import (
    agentCouncilCampaign, agentCouncilController, agentCouncilStore,
)
from vaibify.gui.agentCouncilStore import (
    S_CREDENTIAL_REDACTION_MARKER as S_MARKER,
    fsRedactCredentialSpans,
)
from vaibify.gui.routes import councilRoutes

S_TOKEN = "ghp_" + "Q" * 30
S_PLAN_WITH_HEADER = (
    "# Plan\n\nStep one: call the API.\n"
    "Send `Authorization: Bearer " + "z" * 24 + "` with each request.\n"
    "Step two: verify the answer.\n"
)


@pytest.mark.falsification
def testOnlyTheSpanOfAMentionedCredentialIsRemoved():
    """Kills: replacing the whole text when one span matches."""
    sRedacted = fsRedactCredentialSpans(S_PLAN_WITH_HEADER)
    assert "Step one: call the API." in sRedacted
    assert "Step two: verify the answer." in sRedacted
    assert "z" * 24 not in sRedacted
    assert S_MARKER in sRedacted
    assert sRedacted != S_MARKER


def testAnAuthorizationValueGoesThroughTheEndOfItsLine():
    sRedacted = fsRedactCredentialSpans(
        "before\nAuthorization: Basic dXNlcjpwYXNzd29yZA==\nafter\n")
    assert "dXNlcjpwYXNzd29yZA" not in sRedacted
    assert sRedacted.startswith("before\n")
    assert sRedacted.endswith("\nafter\n")


def testAPrivateKeyBlockGoesThroughItsEndLine():
    sKey = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkq\nabcdef\n"
            "-----END RSA PRIVATE KEY-----")
    sRedacted = fsRedactCredentialSpans("intro\n" + sKey + "\noutro\n")
    assert "MIIEvQ" not in sRedacted and "abcdef" not in sRedacted
    assert sRedacted.startswith("intro\n")
    assert sRedacted.endswith("\noutro\n")


def testAnUnterminatedPrivateKeyIsRemovedToTheEndOfTheText():
    sRedacted = fsRedactCredentialSpans(
        "intro\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n")
    assert "MIIEvQ" not in sRedacted
    assert sRedacted.startswith("intro\n")


def testTextWithNoCredentialIsUntouched():
    sPlain = "# Plan\n\nAuthorization is discussed in step two.\n"
    assert fsRedactCredentialSpans(sPlain) == sPlain


def testEveryDetectionPatternRedactsTheSecretItDetects():
    for sSample in (
        S_TOKEN, "AKIA" + "A" * 16, "sk-" + "b" * 24,
        "Bearer " + "c" * 20, "xoxb-" + "d" * 12,
    ):
        assert agentCouncilStore.fbDetectCredentialText(sSample)
        assert sSample not in fsRedactCredentialSpans(f"x {sSample} y")


@pytest.mark.falsification
def testTheAcceptedPlanKeepsItsWordsAndTheSealNamesTheBytesWritten(
        tOwnerClient, eventTurnGate):
    """The route end to end: the artifact, then the digest of that file.

    Kills: hashing the composed text instead of the file as written.
    """
    client, app, _ = tOwnerClient
    eventTurnGate.set()
    sCampaignId = _sStartOneCampaign(client)
    _fnWaitForCampaignState(
        app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
    with patch.object(
        agentCouncilController, "fsComposePlanMarkdown",
        return_value=S_PLAN_WITH_HEADER,
    ):
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
            "/accept-plan", json={"sPlanText": "ignored"})
    assert response.status_code == 200, response.text
    with open(response.json()["sLocalPlanPath"], "rb") as filePlan:
        baWritten = filePlan.read()
    sWritten = baWritten.decode("utf-8")
    assert "Step one: call the API." in sWritten
    assert "z" * 24 not in sWritten and sWritten != S_MARKER
    assert response.json()["sPlanSha256"] == hashlib.sha256(
        baWritten).hexdigest()
    assert response.json()["sPlanSha256"] != hashlib.sha256(
        S_PLAN_WITH_HEADER.encode("utf-8")).hexdigest(), (
        "the seal must not name the unredacted composed text")


def testAnAcceptedPatchKeepsItsDiffAroundTheRedactedSecret(tmp_path):
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path))
    from tests.testCoverageCouncilStateStore import (
        S_CAMPAIGN_ALPHA, fdictBuildCampaign)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, fdictBuildCampaign(S_CAMPAIGN_ALPHA))
    sPatch = "--- a/x\n+++ b/x\n@@\n+sKey = '" + S_TOKEN + "'\n+keep = 1\n"
    sPath = agentCouncilStore.fsAcceptCampaignPatchLocally(
        dictStore, S_CAMPAIGN_ALPHA, sPatch)
    with open(sPath, encoding="utf-8") as filePatch:
        sWritten = filePatch.read()
    assert "+keep = 1\n" in sWritten and S_TOKEN not in sWritten


# ---------------------------------------------------------------------
# An implementation council is never seeded with the marker
# ---------------------------------------------------------------------


def _fsSeedFor(sSealedText):
    jsonSource = {"sState": agentCouncilCampaign.S_STATE_PLAN_ACCEPTED,
                  "dictCandidatePlan": {}}
    with patch.object(
        agentCouncilStore, "fjsonGetCampaignRecord", return_value=jsonSource,
    ), patch.object(
        agentCouncilCampaign, "fbCampaignMatchesPrincipal",
        return_value=True,
    ), patch.object(
        agentCouncilStore, "fsReadAcceptedPlanText",
        return_value=sSealedText,
    ), patch.object(
        agentCouncilController, "fsComposePlanMarkdown",
        return_value="RECOMPOSED FROM THE RECORD",
    ):
        return councilRoutes._fsLoadAcceptedPlanSeed(
            {}, "source", "name", "/repo")


@pytest.mark.falsification
def testASealedPlanThatIsOnlyTheMarkerIsNeverTheSeed():
    """Kills: seeding the implementation council with the marker alone."""
    assert _fsSeedFor(S_MARKER) == "RECOMPOSED FROM THE RECORD"
    assert _fsSeedFor("  " + S_MARKER + "\n") == (
        "RECOMPOSED FROM THE RECORD")


def testASealedPlanWithRedactedSpansIsStillTheSeed():
    sSealed = "# Plan\nuse " + S_MARKER + " here\n"
    assert _fsSeedFor(sSealed) == sSealed


def testAnEmptyOrAbsentSealedPlanStillRecomposes():
    assert _fsSeedFor("") == "RECOMPOSED FROM THE RECORD"
