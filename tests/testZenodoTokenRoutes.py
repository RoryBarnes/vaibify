"""The Zenodo token migration routes: researcher-only, and the decision is theirs.

``GET /api/zenodo-tokens/{id}`` describes the slots moved out of the
container; ``POST .../decide`` records the researcher's decision and
runs a pass; ``POST .../migrate`` runs a pass on demand. The agent lane
is refused in the handler as well as in the catalog. The carrier is
stood down here (``tests/carrierStandDown.py``), so these tests prove
what the routes DO and nothing about the admission the pass runs under.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.config import zenodoMigrationJournal as journal
from vaibify.config import zenodoTokenSlots as slots
from vaibify.gui import zenodoTokenAccess, zenodoTokenMigration
from vaibify.gui.routes import zenodoTokenRoutes
from vaibify.reproducibility import zenodoClient

S_UUID = "d" * 32
S_CONTAINER_ID = "cid-route-not-the-name"
S_SLOT = "zenodo_token_sandbox"


class _FakeContainerSource:
    bRemovesSource = True

    def __init__(self, dictSlots):
        self.dictSlots = dict(dictSlots)

    def fsRead(self, sSlot):
        return self.dictSlots.get(sSlot, "")

    def fnDelete(self, sSlot):
        self.dictSlots.pop(sSlot, None)


@pytest.fixture
def tClient(tmp_path, monkeypatch, fixtureHermeticKeyring):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"))
    source = _FakeContainerSource({S_SLOT: "migrated-token"})
    monkeypatch.setattr(
        zenodoTokenAccess, "fsProjectUuidForContainer",
        lambda connectionDocker, sContainerId: S_UUID,
    )
    monkeypatch.setattr(
        zenodoTokenMigration, "ftSourceFor",
        lambda connectionDocker, sContainerId: source,
    )
    monkeypatch.setattr(
        zenodoTokenMigration.zenodoClient, "fdictProbeToken",
        lambda sService, sToken: {
            "sOutcome": zenodoClient.S_PROBE_ACCEPTED, "sDetail": "",
            "listDepositTitles": ["Their deposit"],
        },
    )
    fnStandCarrierDown(monkeypatch, zenodoTokenRoutes)
    app = FastAPI()
    dictCtx = {"require": lambda sContainerId: None, "docker": object()}
    zenodoTokenRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), source


def _fsPath(sSuffix=""):
    return f"/api/zenodo-tokens/{S_CONTAINER_ID}" + sSuffix


def testTheDescriptionShowsWhatAwaitsTheResearcher(tClient):
    client, source = tClient
    client.post(_fsPath("/migrate"))
    dictDescribed = client.get(_fsPath()).json()
    assert dictDescribed["bApplicable"] is True
    assert dictDescribed["bAwaitingDecision"] is True
    dictSlot = dictDescribed["listSlots"][0]
    assert dictSlot["sSlot"] == S_SLOT
    assert dictSlot["bPending"] is True
    assert dictSlot["dictValidation"]["sOutcome"] == "accepted"
    assert dictSlot["listDepositTitles"] == ["Their deposit"]
    assert "migrated-token" not in client.get(_fsPath()).text
    assert dictDescribed["dictTokenPages"]["sandbox"].startswith("https://sandbox.zenodo.org/")


def testConfirmActivatesAndDeclineDiscardsOnlyThePendingSlot(tClient):
    client, _ = tClient
    client.post(_fsPath("/migrate"))
    responseHttp = client.post(_fsPath("/decide"), json={
        "sSlot": S_SLOT, "sDecision": "confirm",
    })
    assert responseHttp.status_code == 200, responseHttp.text
    assert slots.fsReadActiveToken("sandbox", S_UUID) == "migrated-token"
    assert responseHttp.json()["dictActive"] == {"sandbox": True, "production": False}
    assert responseHttp.json()["bAwaitingDecision"] is False
    assert journal.fdictSlotRecord(S_UUID, S_SLOT)["sState"] == journal.S_STATE_ACTIVATED


def testDeclineLeavesTheActiveSlotsAlone(tClient):
    client, _ = tClient
    slots.fnStoreActiveToken("production", S_UUID, "keep-me")
    client.post(_fsPath("/migrate"))
    responseHttp = client.post(_fsPath("/decide"), json={
        "sSlot": S_SLOT, "sDecision": "decline",
    })
    assert responseHttp.status_code == 200
    assert slots.fsReadSlot(slots.fsPendingSlot(S_SLOT, S_UUID)) == ""
    assert slots.fsReadActiveToken("production", S_UUID) == "keep-me"
    assert journal.fdictSlotRecord(S_UUID, S_SLOT)["sState"] == journal.S_STATE_DECLINED


@pytest.mark.falsification
def testTheAgentLaneCannotDecideAboutAToken(tClient):
    """The decision is the researcher's; an agent token is refused and nothing moves.

    Kills: dropping the agent-lane refusal from the decide handler.
    """
    client, _ = tClient
    client.post(_fsPath("/migrate"))
    responseHttp = client.post(
        _fsPath("/decide"), json={"sSlot": S_SLOT, "sDecision": "confirm"},
        headers={"X-Vaibify-Session": "agent-token"},
    )
    assert responseHttp.status_code == 403
    assert slots.fsReadActiveToken("sandbox", S_UUID) == ""
    assert client.get(
        _fsPath(), headers={"X-Vaibify-Session": "agent-token"},
    ).status_code == 403
    assert client.post(
        _fsPath("/migrate"), headers={"X-Vaibify-Session": "agent-token"},
    ).status_code == 403


def testAnUnknownDecisionIsA400AndAnImpossibleOneA409(tClient):
    client, _ = tClient
    assert client.post(_fsPath("/decide"), json={
        "sSlot": S_SLOT, "sDecision": "shrug",
    }).status_code == 400
    responseHttp = client.post(_fsPath("/decide"), json={
        "sSlot": S_SLOT, "sDecision": "confirm",
    })
    assert responseHttp.status_code == 409
    assert "nothing is pending" in responseHttp.json()["detail"]


def testAnUnreadableHostKeyringIsA409NotAnEmptyAnswer(tClient, fixtureHermeticKeyring):
    client, _ = tClient

    def fnRefuse(sService, sName):
        raise RuntimeError("keychain locked")

    fixtureHermeticKeyring.get_password = fnRefuse
    responseHttp = client.get(_fsPath())
    assert responseHttp.status_code == 409
    assert "could not be read" in responseHttp.json()["detail"]
