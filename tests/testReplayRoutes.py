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


# ------------------------------------------------------------------
# Personal-layer routes (declare / hash)
# ------------------------------------------------------------------


def _fsPersonalLayerPath(sSuffix):
    return (
        "/api/workflow/" + S_CONTAINER_ID + "/personal-layer" + sSuffix
    )


def _fdictSampleCommitment(**dictOverrides):
    dictCommitment = {
        "sLabel": "personal instruction file",
        "sSha256": "a" * 64,
        "iByteCount": 1234,
        "sDeclaredIso": "2026-07-19T00:00:00+00:00",
    }
    dictCommitment.update(dictOverrides)
    return dictCommitment


def test_personal_layer_each_status_persists_and_round_trips(
    fixtureHarness,
):
    clientTest, dictWorkflow, dictSaved = fixtureHarness
    for sStatus in ("none", "declared-private", "included"):
        dictResponse = clientTest.post(
            _fsPersonalLayerPath("/declare"), json={"sStatus": sStatus},
        )
        assert dictResponse.status_code == 200
        dictLayer = dictResponse.json()["dictPersonalLayer"]
        assert dictLayer["sStatus"] == sStatus
        assert dictLayer["sDeclaredIso"]
        assert dictWorkflow["dictAiProvenance"]["dictPersonalLayer"][
            "sStatus"] == sStatus
    assert S_CONTAINER_ID in dictSaved


def test_personal_layer_unknown_status_is_400(fixtureHarness):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/declare"), json={"sStatus": "partially"},
    )
    assert dictResponse.status_code == 400
    assert "sStatus" in dictResponse.json()["detail"]


def test_personal_layer_commitments_append(fixtureHarness):
    clientTest, dictWorkflow, _ = fixtureHarness
    for iIndex in range(2):
        dictResponse = clientTest.post(
            _fsPersonalLayerPath("/declare"), json={
                "sStatus": "declared-private",
                "dictHashCommitment": _fdictSampleCommitment(
                    sLabel="commitment " + str(iIndex),
                ),
            },
        )
        assert dictResponse.status_code == 200
    listCommitments = dictWorkflow["dictAiProvenance"][
        "dictPersonalLayer"]["listHashCommitments"]
    assert [d["sLabel"] for d in listCommitments] == [
        "commitment 0", "commitment 1",
    ]


def test_personal_layer_commitment_needs_private_status(
    fixtureHarness,
):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/declare"), json={
            "sStatus": "none",
            "dictHashCommitment": _fdictSampleCommitment(),
        },
    )
    assert dictResponse.status_code == 400


def test_personal_layer_commitment_rejects_malformed_sha(
    fixtureHarness,
):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/declare"), json={
            "sStatus": "declared-private",
            "dictHashCommitment": _fdictSampleCommitment(
                sSha256="not-a-digest",
            ),
        },
    )
    assert dictResponse.status_code == 400
    assert "sSha256" in dictResponse.json()["detail"]


def test_personal_layer_persists_no_host_path(fixtureHarness):
    """A host path smuggled into the commitment body never persists.

    The whitelist copy in the declare route is the guarantee that the
    stored workflow — which lands in a public repository — carries
    only {sLabel, sSha256, iByteCount, sDeclaredIso}.
    """
    import json as jsonModule

    clientTest, dictWorkflow, _ = fixtureHarness
    dictSmuggled = _fdictSampleCommitment()
    dictSmuggled["sHostPath"] = "/home/example/personal-instructions.md"
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/declare"), json={
            "sStatus": "declared-private",
            "dictHashCommitment": dictSmuggled,
        },
    )
    assert dictResponse.status_code == 200
    sPersisted = jsonModule.dumps(dictWorkflow)
    assert "sHostPath" not in sPersisted
    assert "personal-instructions.md" not in sPersisted
    dictStored = dictWorkflow["dictAiProvenance"][
        "dictPersonalLayer"]["listHashCommitments"][0]
    assert set(dictStored.keys()) == {
        "sLabel", "sSha256", "iByteCount", "sDeclaredIso",
    }


def test_personal_layer_included_paths_must_be_repo_relative(
    fixtureHarness,
):
    clientTest, dictWorkflow, _ = fixtureHarness
    for listBad in (["/etc/example"], ["../outside.md"], [""]):
        dictResponse = clientTest.post(
            _fsPersonalLayerPath("/declare"), json={
                "sStatus": "included",
                "listIncludedPaths": listBad,
            },
        )
        assert dictResponse.status_code == 400, listBad
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/declare"), json={
            "sStatus": "included",
            "listIncludedPaths": ["agentConfig/personalContext.md"],
        },
    )
    assert dictResponse.status_code == 200
    assert dictWorkflow["dictAiProvenance"]["dictPersonalLayer"][
        "listIncludedPaths"] == ["agentConfig/personalContext.md"]


def test_personal_layer_included_paths_need_included_status(
    fixtureHarness,
):
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/declare"), json={
            "sStatus": "none",
            "listIncludedPaths": ["somewhere.md"],
        },
    )
    assert dictResponse.status_code == 400


def test_hash_route_returns_correct_digest_and_byte_count(
    fixtureHarness, tmp_path, monkeypatch,
):
    import hashlib

    monkeypatch.setenv("HOME", str(tmp_path))
    baContent = b"standing personal instructions\n" * 7
    pathFile = tmp_path / "personalContext.md"
    pathFile.write_bytes(baContent)
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/hash"), json={
            "sHostPath": str(pathFile),
            "sLabel": "personal instruction file",
        },
    )
    assert dictResponse.status_code == 200
    dictCommitment = dictResponse.json()["dictHashCommitment"]
    # Independent oracle: the digest of the exact bytes the test
    # wrote, computed here, not by the code under test.
    assert dictCommitment["sSha256"] == hashlib.sha256(
        baContent,
    ).hexdigest()
    assert dictCommitment["iByteCount"] == len(baContent)
    assert dictCommitment["sLabel"] == "personal instruction file"
    assert dictCommitment["sDeclaredIso"]
    assert set(dictCommitment.keys()) == {
        "sLabel", "sSha256", "iByteCount", "sDeclaredIso",
    }


def test_hash_route_missing_file_echoes_basename_only(
    fixtureHarness, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    sHostPath = str(tmp_path / "privateArea" / "absentFile.md")
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/hash"), json={
            "sHostPath": sHostPath, "sLabel": "absent",
        },
    )
    assert dictResponse.status_code == 400
    sDetail = dictResponse.json()["detail"]
    assert "absentFile.md" in sDetail
    assert sHostPath not in sDetail
    assert "privateArea" not in sDetail


def test_hash_route_rejects_file_outside_home(
    fixtureHarness, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "homeArea"))
    (tmp_path / "homeArea").mkdir()
    pathOutside = tmp_path / "outsideFile.md"
    pathOutside.write_bytes(b"outside the jail")
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/hash"), json={
            "sHostPath": str(pathOutside), "sLabel": "outside",
        },
    )
    assert dictResponse.status_code == 400
    assert str(pathOutside) not in dictResponse.json()["detail"]


def test_hash_route_requires_label(
    fixtureHarness, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    pathFile = tmp_path / "personalContext.md"
    pathFile.write_bytes(b"content")
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/hash"), json={
            "sHostPath": str(pathFile), "sLabel": "  ",
        },
    )
    assert dictResponse.status_code == 400


def test_hash_route_is_excluded_from_agent_catalog():
    from vaibify.gui.actionCatalog import (
        LIST_AGENT_ACTIONS,
        SET_INTENTIONALLY_EXCLUDED_PATHS,
    )
    sPath = "/api/workflow/{sContainerId}/personal-layer/hash"
    assert ("POST", sPath) in SET_INTENTIONALLY_EXCLUDED_PATHS
    assert not any(
        dictAction.get("sPath") == sPath
        for dictAction in LIST_AGENT_ACTIONS
    )


@pytest.mark.falsification
def test_hash_route_rejects_agent_token_lane(
    fixtureHarness, tmp_path, monkeypatch,
):
    """The agent lane must never reach the host-file hash oracle.

    Kills: Remove the ``_fnRejectAgentTokenLane(requestHttp)`` call
    from ``fnHashPersonalLayerFile`` in ``replayRoutes.py`` — the
    request carrying the in-container agent header would then be
    served, handing a compromised agent a hash oracle over host
    files.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    pathFile = tmp_path / "personalContext.md"
    pathFile.write_bytes(b"private standing instructions")
    clientTest = fixtureHarness[0]
    dictResponse = clientTest.post(
        _fsPersonalLayerPath("/hash"),
        json={
            "sHostPath": str(pathFile),
            "sLabel": "personal instruction file",
        },
        headers={"X-Vaibify-Session": "per-container-agent-token"},
    )
    assert dictResponse.status_code == 403
    assert "dictHashCommitment" not in dictResponse.json()


@pytest.mark.falsification
def test_hash_route_digest_tracks_file_content(
    fixtureHarness, tmp_path, monkeypatch,
):
    """Two different files must yield two different commitments.

    Kills: Replace ``hasher.hexdigest()`` with a constant string in
    ``_fdictComputeHashCommitment`` — a constant would satisfy any
    single-file check, but cannot produce two distinct digests for
    two distinct contents that each match the independent hashlib
    oracle computed in this test.
    """
    import hashlib

    monkeypatch.setenv("HOME", str(tmp_path))
    clientTest = fixtureHarness[0]
    for sName, baContent in (
        ("firstFile.md", b"first personal layer content"),
        ("secondFile.md", b"second, different content"),
    ):
        pathFile = tmp_path / sName
        pathFile.write_bytes(baContent)
        dictResponse = clientTest.post(
            _fsPersonalLayerPath("/hash"), json={
                "sHostPath": str(pathFile), "sLabel": sName,
            },
        )
        assert dictResponse.status_code == 200
        assert dictResponse.json()["dictHashCommitment"][
            "sSha256"] == hashlib.sha256(baContent).hexdigest()
