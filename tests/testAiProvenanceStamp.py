"""Tests for the machine-captured AI-provenance stamp.

Cover the pure builder (missing prompt files record empty hashes,
never errors), the staleness comparison the poll side-effect uses to
keep the stamp machine-written, and the atomic write path on a temp
repo. The capture glue is exercised with a stub docker connection so
the container facts land in the right keys.
"""

import hashlib
import json

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
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        lambda sContainerId: True,
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
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        lambda sContainerId: False,
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
