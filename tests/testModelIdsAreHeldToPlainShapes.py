"""A requested model id is a plain id on both request models.

A model id ends up as an argument of a provider command run in a runner.
Both request models bounded its length and nothing else, so whitespace,
quotes, a leading ``-`` or shell metacharacters were admitted. One
pattern, ``^[A-Za-z0-9][A-Za-z0-9._:/@\\[\\]-]{0,199}$``, now governs the
council start participant and the credential-test provider.
"""

import pytest
from pydantic import ValidationError

from tests.testCouncilRoutes import (  # noqa: F401 -- fixture wiring
    DICT_START_BODY,
    S_CONTAINER_ID,
    tOwnerClient,
)
from vaibify.gui import agentCouncilProviderRegistry
from vaibify.gui.routes import councilCredentialRoutes, councilRoutes

LIST_PLAIN_IDS = [
    "haiku", "claude-3.5-sonnet", "gpt-4o:latest", "org/model@v1",
    "models[1m]", "a", "0", "model_v2.1", "x" * 200,
]
LIST_HOSTILE_IDS = [
    "bad model", "a;b", "$(id)", "a`id`", "a\nb", "a\tb", "'quoted'",
    '"quoted"', "-flag", "--model=x", ".hidden", "/abs", "x" * 201,
    "a|b", "a&b", "a>b", "a<b", "é", "a\x00b", "", "haiku\n",
]


def _fnParticipant(sModel):
    return councilRoutes.CouncilParticipantRequest(
        sProvider="claude", sRequestedModel=sModel)


def _fnCredentialProvider(sModel):
    return councilCredentialRoutes.CredentialTestProviderRequest(
        sProvider="claude", sRequestedModel=sModel)


@pytest.mark.falsification
def testBothRequestModelsRefuseAHostileModelId():
    """Kills: dropping the model-id validator from either request model."""
    for sModel in LIST_HOSTILE_IDS:
        with pytest.raises(ValidationError):
            _fnParticipant(sModel)
        if sModel == "":
            continue  # the credential test's empty default is the provider's
        with pytest.raises(ValidationError):
            _fnCredentialProvider(sModel)


@pytest.mark.parametrize("sModel", LIST_PLAIN_IDS)
def testBothRequestModelsAcceptAPlainModelId(sModel):
    assert _fnParticipant(sModel).sRequestedModel == sModel
    assert _fnCredentialProvider(sModel).sRequestedModel == sModel


def testTheCredentialTestStillDefaultsToTheProvidersOwnModel():
    assert _fnCredentialProvider("").sRequestedModel == ""
    assert councilCredentialRoutes.CredentialTestProviderRequest(
        sProvider="claude").sRequestedModel == ""


def testTheSharedPatternIsTheReviewedOne():
    assert agentCouncilProviderRegistry.REGEX_MODEL_ID.pattern == (
        r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,199}$")


def testAHostileModelIsRefusedOverHttpBeforeAnythingStarts(tOwnerClient):
    client, app, _ = tOwnerClient
    dictBody = {**DICT_START_BODY, "listParticipants": [
        {"sProvider": "claude", "sRequestedModel": "haiku"},
        {"sProvider": "claude", "sRequestedModel": "x; touch /tmp/pwned"},
    ]}
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=dictBody)
    assert response.status_code == 422, response.text
    assert "model id starts with a letter or digit" in response.text
