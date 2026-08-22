"""The credential-enablement gate defaults OFF and names every mismatch.

Remediation R10: the runner backend reuses the researcher's provider
subscription, so it is enabled only against a machine-readable evidence
record the maintainer writes after personally running the live
credential check on a paid account. These tests prove the gate's
falsifiable half: no record, an unreadable record, a missing key, and
every keyed mismatch evaluate to DISABLED with the reason named; a
fully matching record enables; and the HTTP surface carries the truth —
capabilities report unavailable with the reason, and start refuses 409.

NO GREEN TEST HERE IMPLIES THE LIVE PROPERTIES HOLD. The live check
(one runner, the copied access token only, a trivial headless turn, the
project login intact afterwards, the token not rotated, the staged
files gone, across a failure and a crash-recovery) is the maintainer's
paid-account action; this file only proves the gate that reads its
result.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.gui import (
    agentCouncilContext,
    agentCouncilCredentialGate,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from vaibify.config import registryManager
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testCouncilRoutes import (
    MockDockerCouncil,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_PROJECT_REPO,
    _fdictWriteFixtureSnapshot,
)

import sys


def _fdictBuildValidRecord():
    return {
        "sProvider": "claude",
        "sBackend": "runner",
        "sCliVersion": "1.0.0",
        "sImageIdentity": "ubuntu:24.04",
        "sCredentialSchema":
            agentCouncilCredentialGate.S_EXPECTED_CREDENTIAL_SCHEMA,
        "sCredentialSource": "persisted project login (.credentials.json)",
        "sHostPlatform": sys.platform,
        "sVerificationDate": "2026-08-20",
    }


@pytest.fixture(autouse=True)
def pathEvidence(tmp_path, monkeypatch):
    """Point the gate at a per-test evidence path; none exists yet."""
    pathRecord = tmp_path / "credentialEvidence.json"
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: str(pathRecord))
    return pathRecord


def test_no_record_means_disabled_with_the_reason_named(pathEvidence):
    dictAnswer = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude"))
    assert dictAnswer["bEnabled"] is False
    assert "no credential-verification evidence record" in (
        dictAnswer["sReason"])


def test_unreadable_record_means_disabled(pathEvidence):
    pathEvidence.write_text("{not json")
    dictAnswer = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude"))
    assert dictAnswer["bEnabled"] is False
    assert "unreadable" in dictAnswer["sReason"]


@pytest.mark.parametrize("sMissingKey",
                         agentCouncilCredentialGate
                         .LIST_EVIDENCE_REQUIRED_KEYS)
def test_any_missing_key_means_disabled(pathEvidence, sMissingKey):
    dictRecord = _fdictBuildValidRecord()
    del dictRecord[sMissingKey]
    pathEvidence.write_text(json.dumps(dictRecord))
    dictAnswer = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude"))
    assert dictAnswer["bEnabled"] is False
    assert sMissingKey in dictAnswer["sReason"]


@pytest.mark.parametrize("sKey,sWrongValue", [
    ("sProvider", "codex"),
    ("sBackend", "api"),
    ("sCredentialSchema", "wholeCredentialsFile"),
    ("sHostPlatform", "not-this-platform"),
])
def test_any_keyed_mismatch_means_disabled(pathEvidence, sKey, sWrongValue):
    dictRecord = _fdictBuildValidRecord()
    dictRecord[sKey] = sWrongValue
    pathEvidence.write_text(json.dumps(dictRecord))
    dictAnswer = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude"))
    assert dictAnswer["bEnabled"] is False
    assert repr(sWrongValue) in dictAnswer["sReason"] or (
        sWrongValue in dictAnswer["sReason"])


def test_matching_record_enables_and_image_mismatch_disables(pathEvidence):
    pathEvidence.write_text(json.dumps(_fdictBuildValidRecord()))
    dictEnabled = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude"))
    assert dictEnabled["bEnabled"] is True
    dictImageMatch = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude", sImageIdentity="ubuntu:24.04"))
    assert dictImageMatch["bEnabled"] is True
    dictImageMismatch = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude", sImageIdentity="python:3.10-slim"))
    assert dictImageMismatch["bEnabled"] is False
    assert "re-run" in dictImageMismatch["sReason"]


# ── the HTTP surface carries the gate's truth ─────────────────────


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    sRegistryDirectory = str(tmp_path / ".vaibify-registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))


def _tBuildOwnedClient(tmp_path, monkeypatch):
    """An owned client with the REAL gate (only its path redirected)."""
    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictWriteFixtureSnapshot)
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerCouncil,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    sCredential = fsBootstrapCredential(app)
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential)
    sLease = containerOwnership.fsMintLease()
    app.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=sLease, fileHandleLock=None, sAgentToken="tok",
            sContainerId=S_CONTAINER_ID,
            sBrowserSessionId=sBrowserSessionId))
    return TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease})


def test_capabilities_report_disabled_by_default_with_the_reason(
        tmp_path, monkeypatch, pathEvidence):
    client = _tBuildOwnedClient(tmp_path, monkeypatch)
    response = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")
    assert response.status_code == 200, response.text
    dictCapabilities = response.json()
    assert dictCapabilities["bAvailable"] is False
    assert "evidence record" in dictCapabilities["sReason"]
    listProviders = dictCapabilities["listProviders"]
    assert [dictProvider["sProvider"]
            for dictProvider in listProviders] == ["claude"], (
        "no adapter-less provider may be advertised (R7)")
    assert listProviders[0]["bAvailable"] is False


def test_start_refuses_409_while_the_gate_is_off(
        tmp_path, monkeypatch, pathEvidence):
    client = _tBuildOwnedClient(tmp_path, monkeypatch)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={
            "sQuestion": "anything",
            "listParticipants": [
                {"sProvider": "claude", "sRequestedModel": "modelOne"},
                {"sProvider": "claude", "sRequestedModel": "modelTwo"},
            ],
        })
    assert response.status_code == 409, response.text
    assert "evidence record" in response.json()["detail"]


def test_codex_is_not_advertised_and_refused_at_validation(
        tmp_path, monkeypatch, pathEvidence):
    """R7/R9: the deferred provider neither appears nor convenes."""
    client = _tBuildOwnedClient(tmp_path, monkeypatch)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={
            "sQuestion": "anything",
            "listParticipants": [
                {"sProvider": "codex", "sRequestedModel": "modelOne"},
                {"sProvider": "claude", "sRequestedModel": "modelTwo"},
            ],
        })
    assert response.status_code == 422, response.text


def test_capability_contract_defaults_unavailable():
    """The `or True` fiction is dead: the contract defaults False."""
    from vaibify.gui import agentCouncilProviders
    dictContract = agentCouncilProviders.fdictClaudeCapabilityContract()
    assert dictContract["bAvailable"] is False
    dictEnabled = agentCouncilProviders.fdictClaudeCapabilityContract(
        bRunnerBackendEnabled=True)
    assert dictEnabled["bAvailable"] is True

# ── several images on one machine (2026-08-22) ────────────────────────

def test_verifying_a_second_image_does_not_disable_the_first(pathEvidence):
    """The eviction bug: one record meant one enabled project, ever.

    A researcher who ran the ceremony for a second project found the
    first silently disabled, because the document held a single record
    with a single image identity. Per-image verification is the
    security property and it is kept — what was broken is that the
    file could only remember one image at a time, which made the
    property unusable rather than strict.
    """
    dictFirst = _fdictBuildValidRecord()
    dictFirst["sImageIdentity"] = "sha256:" + "11" * 32
    dictSecond = _fdictBuildValidRecord()
    dictSecond["sImageIdentity"] = "sha256:" + "22" * 32
    pathEvidence.write_text(json.dumps(
        {"listRecords": [dictFirst, dictSecond]}))

    for sImageIdentity in (dictFirst["sImageIdentity"],
                           dictSecond["sImageIdentity"]):
        dictAnswer = (
            agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
                "claude", sImageIdentity))
        assert dictAnswer["bEnabled"] is True, (
            f"{sImageIdentity} was disabled by the presence of the "
            f"other record: {dictAnswer['sReason']}")


def test_an_unverified_image_is_still_refused_beside_verified_ones(
    pathEvidence,
):
    """The property that must NOT loosen with several records.

    Holding more facts must not make the gate generous: an image nobody
    ran the check for is refused even when its neighbours are verified,
    and the refusal says evidence does not carry over.
    """
    dictVerified = _fdictBuildValidRecord()
    dictVerified["sImageIdentity"] = "sha256:" + "33" * 32
    pathEvidence.write_text(json.dumps({"listRecords": [dictVerified]}))

    dictAnswer = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude", "sha256:" + "44" * 32))

    assert dictAnswer["bEnabled"] is False
    assert "does not carry over" in dictAnswer["sReason"]


def test_a_single_record_document_still_works_untouched(pathEvidence):
    """Back-compat: the v1 shape is a machine's existing attestation.

    Rewriting it would ask the researcher to redo a ceremony they have
    already performed, which is exactly the friction this change exists
    to remove.
    """
    dictRecord = _fdictBuildValidRecord()
    pathEvidence.write_text(json.dumps(dictRecord))

    dictAnswer = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude", dictRecord["sImageIdentity"]))

    assert dictAnswer["bEnabled"] is True
