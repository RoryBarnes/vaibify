"""The Zenodo row's two remedies for a record on the other instance.

A promoted project's deposit lives on zenodo.org while its declared
target stayed on the sandbox, so every publish was refused -- and the
only button offered was "Start a new concept", Zenodo jargon for giving
up the version chain. Setting the instance back lived in a settings
dialog nobody could find (researcher-reported, 2026-09-29). The row now
offers both, and this module drives the new one over HTTP.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import syncBookkeeping


S_CONTAINER_ID = "zenodo_cid"
S_PATH = f"/api/zenodo/{S_CONTAINER_ID}/publish-where-the-record-is"


def _fdictPromotedWorkflow(sProjectRepo):
    """Record on production, declaration still on the sandbox."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [],
        "sZenodoService": "sandbox",
        "sZenodoDepositionId": "991",
        "dictRemotes": {"zenodo": {
            "sRecordId": "991",
            "sDoi": "10.5281/zenodo.991",
            "sService": "zenodo",
        }},
    }


@pytest.fixture
def fixtureClientAndWorkflow(tmp_path, monkeypatch):
    fnStandCarrierDown(monkeypatch, syncRoutes)
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    dictWorkflow = _fdictPromotedWorkflow(sRepo)
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictCtx = {
        "docker": None,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {},
        "pipelineTasks": {},
        "sourceCodeDeps": {},
        "sSessionToken": "tok",
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: sRepo,
    }
    syncRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictWorkflow


def test_the_row_is_told_which_instance_holds_the_record():
    """The facts ride beside the sentence, so buttons can name sites."""
    dictCrossInstance = syncBookkeeping.fdictDescribeCrossInstanceParent(
        _fdictPromotedWorkflow("/repo"), "sandbox",
    )
    assert dictCrossInstance["sRecordedService"] == "zenodo"
    assert dictCrossInstance["sTargetService"] == "sandbox"
    assert dictCrossInstance["sRecordDoi"] == "10.5281/zenodo.991"
    assert "zenodo.org (permanent)" in dictCrossInstance["sMessage"]
    assert "concept" not in dictCrossInstance["sMessage"]


@pytest.mark.falsification
def test_a_new_version_keeps_the_chain_and_publishes_where_the_record_is(
    fixtureClientAndWorkflow,
):
    """The next publish is a new version of the record, on its site.

    Kills: setting the declaration to the target it already had, which
    leaves the refusal standing and the button doing nothing.
    """
    clientTest, dictWorkflow = fixtureClientAndWorkflow
    responseHttp = clientTest.post(S_PATH)
    assert responseHttp.status_code == 200, responseHttp.text
    assert dictWorkflow["sZenodoService"] == "zenodo"
    assert syncBookkeeping.fsDescribeCrossInstanceParent(
        dictWorkflow, dictWorkflow["sZenodoService"],
    ) == ""
    assert syncBookkeeping.fiResolveZenodoParentDepositId(
        dictWorkflow,
    ) == 991, "the version chain must survive: the parent is kept"


def test_nothing_to_change_is_a_refusal_not_a_silent_success(
    fixtureClientAndWorkflow,
):
    clientTest, dictWorkflow = fixtureClientAndWorkflow
    dictWorkflow["sZenodoService"] = "zenodo"
    responseHttp = clientTest.post(S_PATH)
    assert responseHttp.status_code == 409
    assert "nothing to change" in responseHttp.json()["detail"]
