"""Tests for GET /api/registry/{sName}/quarantine.

The route surfaces WHY a container is quarantined so a researcher does
not have to reach an agent to learn a leftover process is holding it.
It must name the unsettled operations (with their allowlisted notes)
and the exact host command that clears the quarantine, and must never
mislabel a clean container as quarantined.
"""

import os

import pytest

from vaibify.config import operationJournal

S_PROJECT = "demo"


@pytest.fixture
def fixtureHubApp():
    """Build a minimal hub app carrying the registry routes."""
    from fastapi import FastAPI
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *aArgs: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def fixtureClient(fixtureHubApp):
    from starlette.testclient import TestClient
    return TestClient(fixtureHubApp)


def _fnSeedQuarantinedTerminalRecord(sNote):
    """Poison one terminal record so the container reads QUARANTINED."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "terminal", "cid",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {
            "sDockerExecId": "execid",
            "sDockerContainerId": "cid",
            "iHolderProcessGroup": 1234,
        },
    )
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_PROJECT, sOperationId, sNote,
    )
    return sOperationId


def test_quarantine_detail_names_the_unsettled_records(fixtureClient):
    """The response carries the record's kind, its note, and the remedy."""
    sNote = "the terminal process group could not be proven empty: 2 live members"
    _fnSeedQuarantinedTerminalRecord(sNote)
    dictBody = fixtureClient.get(
        f"/api/registry/{S_PROJECT}/quarantine",
    ).json()
    assert dictBody["bQuarantined"] is True
    assert dictBody["sJournalState"] == operationJournal.S_RESOLUTION_QUARANTINED
    assert dictBody["sRemedy"] == f"vaibify reconcile {S_PROJECT}"
    assert len(dictBody["listRecords"]) == 1
    dictRecord = dictBody["listRecords"][0]
    assert dictRecord["sKind"] == "terminal"
    assert dictRecord["sNote"] == sNote


def test_quarantine_detail_reports_a_clean_container_as_not_quarantined(
    fixtureClient,
):
    """A container with no journal is SETTLED, never quarantined."""
    dictBody = fixtureClient.get(
        f"/api/registry/{S_PROJECT}/quarantine",
    ).json()
    assert dictBody["bQuarantined"] is False
    assert dictBody["sJournalState"] == operationJournal.S_RESOLUTION_SETTLED
    assert dictBody["listRecords"] == []


def test_quarantine_detail_survives_a_malformed_journal(fixtureClient):
    """A malformed journal still quarantines and does not 500."""
    sPath = operationJournal.fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(b"this is not a journal")
    response = fixtureClient.get(f"/api/registry/{S_PROJECT}/quarantine")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bQuarantined"] is True
    assert dictBody["sReadState"] != "valid"
    assert dictBody["listRecords"] == []


def test_quarantine_detail_rejects_an_invalid_name(fixtureClient):
    """An unsafe project name is refused before any journal read."""
    response = fixtureClient.get("/api/registry/..%2Fescape/quarantine")
    assert response.status_code in (400, 404)
