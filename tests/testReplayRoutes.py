"""Tests for vaibify.gui.routes.replayRoutes — AI-model declarations.

Covers the declare/remove endpoints: upsert-on-(vendor, model id),
validation failures with the missing-field list, date-format
enforcement, and the 404 on removing an undeclared model. The saved
workflow dict is asserted directly so the persistence contract (the
``dictAiProvenance`` block) is pinned, not just the response body.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.replayRoutes import fnRegisterAll


S_CONTAINER_ID = "replay_cid"


def _fdictClosedWeightsBody(**dictOverrides):
    dictBody = {
        "sVendor": "ExampleVendor",
        "sModelId": "example-model-1",
        "sUseStartDate": "2026-01-01",
        "sUseEndDate": "2026-02-01",
    }
    dictBody.update(dictOverrides)
    return dictBody


@pytest.fixture
def fixtureHarness():
    """Return ``(clientTest, dictWorkflow, dictSaved)`` for the routes."""
    app = FastAPI()
    dictWorkflow = {"listSteps": []}
    dictSaved = {}

    def _fnSave(sId, dictWf):
        dictSaved[sId] = dictWf

    dictCtx = {
        "docker": None,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "require": lambda: None,
        "save": _fnSave,
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app), dictWorkflow, dictSaved


def _fsDeclarePath():
    return "/api/workflow/" + S_CONTAINER_ID + "/ai-models/declare"


def _fsRemovePath():
    return "/api/workflow/" + S_CONTAINER_ID + "/ai-models/remove"


def test_declare_persists_model_and_saves(fixtureHarness):
    clientTest, dictWorkflow, dictSaved = fixtureHarness
    dictResponse = clientTest.post(
        _fsDeclarePath(), json=_fdictClosedWeightsBody(),
    )
    assert dictResponse.status_code == 200
    listModels = dictResponse.json()["listDeclaredModels"]
    assert len(listModels) == 1
    assert listModels[0]["sVendor"] == "ExampleVendor"
    assert dictWorkflow["dictAiProvenance"]["listDeclaredModels"] == (
        listModels
    )
    assert S_CONTAINER_ID in dictSaved


def test_declare_upserts_on_vendor_and_model_id(fixtureHarness):
    clientTest = fixtureHarness[0]
    clientTest.post(_fsDeclarePath(), json=_fdictClosedWeightsBody())
    dictResponse = clientTest.post(
        _fsDeclarePath(),
        json=_fdictClosedWeightsBody(sUseEndDate="2026-03-01"),
    )
    listModels = dictResponse.json()["listDeclaredModels"]
    assert len(listModels) == 1
    assert listModels[0]["sUseEndDate"] == "2026-03-01"


def test_declare_second_model_appends(fixtureHarness):
    clientTest = fixtureHarness[0]
    clientTest.post(_fsDeclarePath(), json=_fdictClosedWeightsBody())
    dictResponse = clientTest.post(
        _fsDeclarePath(),
        json=_fdictClosedWeightsBody(sModelId="example-model-2"),
    )
    assert len(dictResponse.json()["listDeclaredModels"]) == 2


def test_declare_missing_field_is_400_naming_the_gap(fixtureHarness):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsDeclarePath(), json=_fdictClosedWeightsBody(sVendor=""),
    )
    assert dictResponse.status_code == 400
    assert "sVendor" in dictResponse.json()["detail"]


def test_declare_malformed_date_is_400(fixtureHarness):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsDeclarePath(),
        json=_fdictClosedWeightsBody(sUseStartDate="01/01/2026"),
    )
    assert dictResponse.status_code == 400
    assert "sUseStartDate" in dictResponse.json()["detail"]


def test_declare_open_weights_missing_hash_is_400(fixtureHarness):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsDeclarePath(),
        json=_fdictClosedWeightsBody(
            bOpenWeights=True,
            sWeightsSource="https://example.org/weights",
        ),
    )
    assert dictResponse.status_code == 400
    assert "sWeightsRevisionHash" in dictResponse.json()["detail"]


def test_declare_ignores_unknown_fields(fixtureHarness):
    clientTest, dictWorkflow, _ = fixtureHarness
    dictBody = _fdictClosedWeightsBody(sUnexpected="ignored")
    clientTest.post(_fsDeclarePath(), json=dictBody)
    dictModel = dictWorkflow["dictAiProvenance"][
        "listDeclaredModels"][0]
    assert "sUnexpected" not in dictModel


def test_remove_deletes_declared_model(fixtureHarness):
    clientTest, dictWorkflow, _ = fixtureHarness
    clientTest.post(_fsDeclarePath(), json=_fdictClosedWeightsBody())
    dictResponse = clientTest.post(_fsRemovePath(), json={
        "sVendor": "ExampleVendor", "sModelId": "example-model-1",
    })
    assert dictResponse.status_code == 200
    assert dictResponse.json()["listDeclaredModels"] == []
    assert dictWorkflow["dictAiProvenance"][
        "listDeclaredModels"] == []


def test_remove_unknown_model_is_404(fixtureHarness):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(_fsRemovePath(), json={
        "sVendor": "NoSuchVendor", "sModelId": "none",
    })
    assert dictResponse.status_code == 404


# ------------------------------------------------------------------
# Prompt Record routes (configure / capture / approve)
# ------------------------------------------------------------------


def _fsPromptRecordPath(sSuffix):
    return (
        "/api/workflow/" + S_CONTAINER_ID + "/prompt-record" + sSuffix
    )


def test_configure_enable_refused_without_sanitizer(
    fixtureHarness, monkeypatch,
):
    clientTest = fixtureHarness[0]
    monkeypatch.setattr(
        "vaibify.gui.transcriptSanitizer.fbSanitizerAvailable",
        lambda: False,
    )
    dictResponse = clientTest.post(
        _fsPromptRecordPath("/configure"), json={"bEnabled": True},
    )
    assert dictResponse.status_code == 409
    assert "vaibify[replay]" in dictResponse.json()["detail"]


def test_configure_enable_sets_config(fixtureHarness, monkeypatch):
    clientTest, dictWorkflow, dictSaved = fixtureHarness
    monkeypatch.setattr(
        "vaibify.gui.transcriptSanitizer.fbSanitizerAvailable",
        lambda: True,
    )
    dictResponse = clientTest.post(
        _fsPromptRecordPath("/configure"), json={"bEnabled": True},
    )
    assert dictResponse.status_code == 200
    dictRecord = dictWorkflow["dictAiProvenance"]["dictPromptRecord"]
    assert dictRecord["bEnabled"] is True
    assert dictRecord["bFirstCaptureReviewed"] is False
    assert dictRecord["sEnabledAtUtc"]
    assert S_CONTAINER_ID in dictSaved


def test_capture_refused_when_disabled(fixtureHarness):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(_fsPromptRecordPath("/capture"))
    assert dictResponse.status_code == 409


def test_approve_flips_review_flag(fixtureHarness, monkeypatch):
    clientTest, dictWorkflow, _ = fixtureHarness
    monkeypatch.setattr(
        "vaibify.gui.transcriptSanitizer.fbSanitizerAvailable",
        lambda: True,
    )
    clientTest.post(
        _fsPromptRecordPath("/configure"), json={"bEnabled": True},
    )
    dictResponse = clientTest.post(
        _fsPromptRecordPath("/approve-first-capture"),
    )
    assert dictResponse.status_code == 200
    assert dictWorkflow["dictAiProvenance"]["dictPromptRecord"][
        "bFirstCaptureReviewed"] is True


def test_approve_route_is_excluded_from_agent_catalog():
    from vaibify.gui.actionCatalog import (
        SET_INTENTIONALLY_EXCLUDED_PATHS,
    )
    assert (
        "POST",
        "/api/workflow/{sContainerId}/prompt-record/"
        "approve-first-capture",
    ) in SET_INTENTIONALLY_EXCLUDED_PATHS
