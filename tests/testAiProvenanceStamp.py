"""Tests for the machine-captured AI-provenance stamp.

Cover the pure builder (missing prompt files record empty hashes,
never errors), the staleness comparison the poll side-effect uses to
keep the stamp machine-written, and the atomic write path on a temp
repo. The capture glue is exercised with a stub docker connection so
the container facts land in the right keys.
"""

import hashlib
import json

import pytest

from vaibify.gui.aiProvenanceCapture import fdictCaptureAiProvenanceStamp
from vaibify.reproducibility.aiProvenanceStamp import (
    S_TRUST_BASE_STATEMENT,
    fbStampMatchesDeclaration,
    fdictBuildAiProvenanceStamp,
    fnWriteAiProvenanceStamp,
    fsStampRelativePath,
)


def _fdictWorkflowWithOneModel():
    return {"dictAiProvenance": {"listDeclaredModels": [{
        "sVendor": "ExampleVendor",
        "sModelId": "example-model-1",
        "sUseStartDate": "2026-01-01",
        "sUseEndDate": "2026-02-01",
    }]}}


def test_build_with_missing_prompt_files_records_empty_hashes(tmp_path):
    dictStamp = fdictBuildAiProvenanceStamp(
        _fdictWorkflowWithOneModel(), str(tmp_path),
    )
    assert dictStamp["sProjectContextSha256"] == ""
    assert dictStamp["sWorkspacePromptSha256"] == ""
    assert dictStamp["bNetworkIsolatedAtCapture"] is None
    assert dictStamp["sTrustBaseStatement"] == S_TRUST_BASE_STATEMENT
    assert len(dictStamp["listDeclaredModels"]) == 1


def test_build_hashes_present_project_context(tmp_path):
    (tmp_path / ".vaibify").mkdir()
    baContent = b"# project context\n"
    (tmp_path / ".vaibify" / "AGENTS.md").write_bytes(baContent)
    dictStamp = fdictBuildAiProvenanceStamp(
        _fdictWorkflowWithOneModel(), str(tmp_path),
    )
    assert dictStamp["sProjectContextSha256"] == hashlib.sha256(
        baContent,
    ).hexdigest()


def test_stamp_matches_only_the_current_declaration():
    dictWorkflow = _fdictWorkflowWithOneModel()
    dictStamp = fdictBuildAiProvenanceStamp(dictWorkflow, "/nonexistent")
    assert fbStampMatchesDeclaration(dictStamp, dictWorkflow) is True
    dictWorkflow["dictAiProvenance"]["listDeclaredModels"].append({
        "sVendor": "OtherVendor", "sModelId": "other-model",
        "sUseStartDate": "2026-01-01", "sUseEndDate": "2026-02-01",
    })
    assert fbStampMatchesDeclaration(dictStamp, dictWorkflow) is False
    assert fbStampMatchesDeclaration(None, dictWorkflow) is False


@pytest.mark.falsification
def test_edited_stamp_fields_are_detected_as_stale():
    """A hand edit to ANY captured field must trigger a rewrite.

    Every field here is folded into the L3 attestation, so a stamp
    edit that survives becomes an attested claim. Comparing only the
    declared model list left five of the six fields hand-editable
    forever, while the docstring promised the opposite.

    Kills: Delete the ``if not _fbStampShapeIntact(dictStamp): return
    False`` guard from ``fbStampMatchesDeclaration``
    (``aiProvenanceStamp.py``).
    """
    dictWorkflow = _fdictWorkflowWithOneModel()
    dictStamp = fdictBuildAiProvenanceStamp(dictWorkflow, "/nonexistent")
    assert fbStampMatchesDeclaration(dictStamp, dictWorkflow) is True
    dictTrustEdited = dict(
        dictStamp, sTrustBaseStatement="Everything is fine.",
    )
    assert fbStampMatchesDeclaration(dictTrustEdited, dictWorkflow) is False
    dictHashEdited = dict(dictStamp, sWorkspacePromptSha256="none")
    assert fbStampMatchesDeclaration(dictHashEdited, dictWorkflow) is False
    dictIsolationEdited = dict(
        dictStamp, bNetworkIsolatedAtCapture="yes, sealed",
    )
    assert fbStampMatchesDeclaration(
        dictIsolationEdited, dictWorkflow,
    ) is False
    dictTimeEdited = dict(dictStamp, sCapturedAtUtc="whenever")
    assert fbStampMatchesDeclaration(dictTimeEdited, dictWorkflow) is False
    dictFutureEdited = dict(
        dictStamp, sCapturedAtUtc="2099-01-01T00:00:00+00:00",
    )
    assert fbStampMatchesDeclaration(dictFutureEdited, dictWorkflow) is False


def test_edited_project_context_hash_is_detected_against_the_repo(tmp_path):
    """With the repo in hand, the context hash is checked by VALUE."""
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / ".vaibify" / "AGENTS.md").write_bytes(b"# context\n")
    dictWorkflow = _fdictWorkflowWithOneModel()
    dictStamp = fdictBuildAiProvenanceStamp(dictWorkflow, str(tmp_path))
    assert fbStampMatchesDeclaration(
        dictStamp, dictWorkflow, str(tmp_path),
    ) is True
    dictEdited = dict(dictStamp, sProjectContextSha256="0" * 64)
    assert fbStampMatchesDeclaration(
        dictEdited, dictWorkflow, str(tmp_path),
    ) is False


def test_write_persists_stamp_at_canonical_path(tmp_path):
    dictStamp = fdictBuildAiProvenanceStamp(
        _fdictWorkflowWithOneModel(), str(tmp_path),
    )
    fnWriteAiProvenanceStamp(str(tmp_path), dictStamp)
    pathStamp = tmp_path / fsStampRelativePath()
    assert pathStamp.is_file()
    dictRead = json.loads(pathStamp.read_text())
    assert dictRead["listDeclaredModels"] == (
        dictStamp["listDeclaredModels"]
    )


class _StubDockerConnection:
    """Answer fbaFetchFile with fixed bytes for the workspace prompt."""

    def __init__(self, baPrompt):
        self._baPrompt = baPrompt

    def fbaFetchFile(self, sContainerId, sFilePath):
        return self._baPrompt


def test_capture_records_workspace_prompt_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "vaibify.docker.containerManager.ftProbeNetworkIsolation",
        lambda sContainerId: (True, True),
    )
    baPrompt = b"# workspace prompt\n"
    dictStamp = fdictCaptureAiProvenanceStamp(
        _fdictWorkflowWithOneModel(), str(tmp_path), "cid",
        _StubDockerConnection(baPrompt),
    )
    assert dictStamp["sWorkspacePromptSha256"] == hashlib.sha256(
        baPrompt,
    ).hexdigest()
    assert dictStamp["bNetworkIsolatedAtCapture"] is True
    assert dictStamp["sHubInvokerModelId"] != ""


def test_capture_survives_unreachable_container(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "vaibify.docker.containerManager.ftProbeNetworkIsolation",
        lambda sContainerId: (True, False),
    )

    class _BrokenConnection:
        def fbaFetchFile(self, sContainerId, sFilePath):
            raise FileNotFoundError(sFilePath)

    dictStamp = fdictCaptureAiProvenanceStamp(
        _fdictWorkflowWithOneModel(), str(tmp_path), "cid",
        _BrokenConnection(),
    )
    assert dictStamp["sWorkspacePromptSha256"] == ""
    assert dictStamp["bNetworkIsolatedAtCapture"] is False


@pytest.mark.falsification
def test_unanswerable_isolation_probe_is_recorded_as_unknown(
    tmp_path, monkeypatch,
):
    """A probe that could not answer must not assert "not isolated".

    bNetworkIsolatedAtCapture is evidence folded into the L3
    attestation. fbContainerIsNetworkIsolated fails OPEN (False) by
    design, because the gating routes want a decision; recording that
    same False here would turn "docker inspect could not answer" into
    the asserted fact "this container had network access".

    Kills: in aiProvenanceCapture.fdictCaptureAiProvenanceStamp,
    replace ``bIsolated if bAnswered else None`` with ``bIsolated``.
    """
    monkeypatch.setattr(
        "vaibify.docker.containerManager.ftProbeNetworkIsolation",
        lambda sContainerId: (False, False),
    )
    dictStamp = fdictCaptureAiProvenanceStamp(
        _fdictWorkflowWithOneModel(), str(tmp_path), "cid",
        _StubDockerConnection(b""),
    )
    assert dictStamp["bNetworkIsolatedAtCapture"] is None
