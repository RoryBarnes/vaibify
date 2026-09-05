"""The two environment-archive endpoints, driven over HTTP.

The answer route records a decision; the deposit route publishes to an
archive under the researcher's credentials. What is asserted here is
what each REFUSES, because both refusals are the feature's guardrails
rather than input validation:

* ``archived`` is not an answer a caller may assert. It is what the
  backend writes once a deposit has been published, and accepting it
  would turn the Level 2 row green over an archive that does not
  exist -- the exact overclaim this feature was built to prevent.
* A reference is verified against the record it names, so a concept
  DOI and an unreadable deposit are refused rather than trusted.
* A deposit with no stored Zenodo token is a 409 with an instruction,
  never a 500.
"""

import json
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui.routes import environmentArchiveRoutes
from vaibify.reproducibility import imageArchive


S_CONTAINER_ID = "archive_cid"
_S_DIGEST = "registry.example/project@sha256:" + "a" * 64


@pytest.fixture
def sProjectRepo(tmp_path):
    """A project repo whose envelope pins an image."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    with open(
        os.path.join(sRepo, ".vaibify", "environment.json"), "w",
    ) as fileOut:
        json.dump({"dictContainer": {
            "sImageDigest": _S_DIGEST, "sArchitecture": "arm64",
        }}, fileOut)
    return sRepo


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    """Stand the carrier down for the routes this module drives bare.

    Requested only by tests that reach a carrier, so the ones
    asserting a 4xx still prove the route refuses BEFORE it gets
    there. What the admission itself does lives in
    ``tests/testCarrierMigratedRoutes.py``.
    """
    fnStandCarrierDown(monkeypatch, environmentArchiveRoutes)


def _fclientBuild(sProjectRepo, dictWorkflow=None, connectionDocker=None):
    """Build the test app around one workflow; return (client, workflow)."""
    dictWorkflow = dictWorkflow if dictWorkflow is not None else {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [],
        "sZenodoService": "sandbox",
    }
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictCtx = {
        "docker": connectionDocker,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {S_CONTAINER_ID: sProjectRepo + "/project.json"},
        "pipelineTasks": {},
        "sSessionToken": "tok",
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: sProjectRepo,
    }
    environmentArchiveRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictWorkflow


def _fsAnswerPath():
    return f"/api/workflow/{S_CONTAINER_ID}/environment-archive/answer"


def _fsDepositPath():
    return f"/api/workflow/{S_CONTAINER_ID}/environment-archive/deposit"


def test_archived_is_refused_as_a_directly_recorded_answer(sProjectRepo):
    """It is earned by a deposit, never asserted by a caller."""
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsAnswerPath(), json={"sAnswer": "archived"},
    )
    assert responseHttp.status_code == 422
    assert "deposit" in responseHttp.json()["detail"]


def test_an_unknown_answer_is_refused(sProjectRepo):
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    assert clientTest.post(
        _fsAnswerPath(), json={"sAnswer": "maybe"},
    ).status_code == 422


def test_declining_is_recorded_and_is_a_complete_answer(
    sProjectRepo, fixtureCarrierStoodDown,
):
    """Answering is the criterion; declining answers it."""
    clientTest, dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsAnswerPath(), json={"sAnswer": "declined"},
    )
    assert responseHttp.status_code == 200
    assert dictWorkflow[imageArchive.S_IMAGE_ARCHIVE_KEY]["sAnswer"] == (
        imageArchive.S_ANSWER_DECLINED
    )
    assert imageArchive.fbWorkflowAnswersImageArchive(dictWorkflow) is True


def test_referencing_without_a_doi_is_refused(sProjectRepo):
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    assert clientTest.post(
        _fsAnswerPath(), json={"sAnswer": "referenced"},
    ).status_code == 422


def test_referencing_a_non_zenodo_doi_is_refused(sProjectRepo):
    """A DOI that is not Zenodo's names no record this can read."""
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(_fsAnswerPath(), json={
        "sAnswer": "referenced", "sVersionDoi": "10.1000/example",
    })
    assert responseHttp.status_code == 422
    assert "Zenodo DOI" in responseHttp.json()["detail"]


def test_a_project_with_no_pinned_image_has_nothing_to_reference(tmp_path):
    """Refuse with the reason, rather than record an empty claim."""
    sRepo = str(tmp_path / "bare")
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    clientTest, _dictWorkflow = _fclientBuild(sRepo)
    responseHttp = clientTest.post(_fsAnswerPath(), json={
        "sAnswer": "referenced", "sVersionDoi": "10.5281/zenodo.7000001",
    })
    assert responseHttp.status_code == 409
    assert "no container image" in responseHttp.json()["detail"]


def test_a_reference_vaibify_cannot_read_is_refused(
    sProjectRepo, fixtureCarrierStoodDown,
):
    """A deposit that does not say which image it holds proves nothing."""
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    with patch(
        "vaibify.reproducibility.zenodoClient.ZenodoClient"
        ".fdictFetchPublishedRecord",
        return_value={
            "doi": "10.5281/zenodo.7000001",
            "metadata": {"description": "A dataset."},
        },
    ):
        responseHttp = clientTest.post(_fsAnswerPath(), json={
            "sAnswer": "referenced",
            "sVersionDoi": "10.5281/zenodo.7000001",
        })
    assert responseHttp.status_code == 409
    assert "does not describe which container image" in (
        responseHttp.json()["detail"]
    )


def test_a_verified_reference_lands_in_the_envelope(
    sProjectRepo, fixtureCarrierStoodDown,
):
    """The record on disk is what the Level 3 criterion grades."""
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi="10.5281/zenodo.7000001",
        sConceptDoi="10.5281/zenodo.7000000",
        sTarballSha256="sha256:" + "b" * 64,
        iTarballBytes=861079552,
        sDepositedIso="2026-09-05T00:00:00+00:00",
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest=_S_DIGEST, sArchitecture="arm64",
        sTarballName="environment-image.tar.zst",
        sImageStreamSha256="sha256:" + "c" * 64,
    )
    dictMetadata = imageArchive.fdictStampDepositMetadata(
        {"sDescription": "The container image."}, dictRecord,
    )
    clientTest, dictWorkflow = _fclientBuild(sProjectRepo)
    with patch(
        "vaibify.reproducibility.zenodoClient.ZenodoClient"
        ".fdictFetchPublishedRecord",
        return_value={
            "doi": "10.5281/zenodo.7000001",
            "conceptdoi": "10.5281/zenodo.7000000",
            "metadata": {"description": dictMetadata["sDescription"]},
        },
    ):
        responseHttp = clientTest.post(_fsAnswerPath(), json={
            "sAnswer": "referenced",
            "sVersionDoi": "10.5281/zenodo.7000001",
        })
    assert responseHttp.status_code == 200
    from vaibify.reproducibility import levelGates
    assert levelGates.fbImageArchiveDeposited(sProjectRepo) is True


def test_a_deposit_without_a_stored_token_is_a_409_not_a_500(
    sProjectRepo,
):
    """A researcher who has not connected Zenodo gets an instruction."""

    class _ConnectionWithoutAToken:
        """Answers the keyring read the way an unconfigured project does."""

        def fsFetchKeyringSecret(self, sContainerId, sSlotName):
            del sContainerId, sSlotName
            return ""

    clientTest, _dictWorkflow = _fclientBuild(
        sProjectRepo, connectionDocker=_ConnectionWithoutAToken(),
    )
    responseHttp = clientTest.post(_fsDepositPath())
    assert responseHttp.status_code == 409
    assert "Connect Zenodo" in responseHttp.json()["detail"]


def test_a_deposit_asks_the_slot_its_service_names(sProjectRepo):
    """Sandbox and production keep separate tokens; the wrong one 401s.

    Asserted on the SLOT rather than on a success, because the value
    never appears in a response and a deposit that reached for the
    production slot on a sandbox project would fail much later, with
    a Zenodo error that names nothing about slots.
    """
    listAsked = []

    class _RecordingConnection:
        def fsFetchKeyringSecret(self, sContainerId, sSlotName):
            del sContainerId
            listAsked.append(sSlotName)
            return ""

    dictWorkflow = {
        "sProjectRepoPath": sProjectRepo, "listSteps": [],
        "sZenodoService": "zenodo",
    }
    clientTest, _dictWorkflow = _fclientBuild(
        sProjectRepo, dictWorkflow=dictWorkflow,
        connectionDocker=_RecordingConnection(),
    )
    clientTest.post(_fsDepositPath())
    assert listAsked == ["zenodo_token_production"]
