"""Tests for the machine-captured AI-provenance stamp.

Cover the pure builder (missing prompt files record empty hashes,
never errors), the staleness comparison the poll side-effect uses to
keep the stamp machine-written, and the atomic write path on a temp
repo. The capture glue is exercised with a stub docker connection so
the container facts land in the right keys.
"""

import hashlib
import json
from types import SimpleNamespace

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

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        return SimpleNamespace(iExitCode=0, sStdout="codex\tcodex-cli 1.0\n")


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
    assert dictStamp["dictAgentCliVersions"] == {"codex": "codex-cli 1.0"}


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
def test_agent_version_stamp_rejects_unexpected_provider_name():
    """The provider-version record admits only known CLI identities.

    Kills: remove the known-provider check from ``_fbStampShapeIntact``
    in ``aiProvenanceStamp.py``.
    """
    dictWorkflow = _fdictWorkflowWithOneModel()
    dictStamp = fdictBuildAiProvenanceStamp(
        dictWorkflow, "/nonexistent",
        dictAgentCliVersions={"unexpected": "1.0"},
    )
    assert fbStampMatchesDeclaration(dictStamp, dictWorkflow) is False


def test_the_stamp_accepts_every_agent_the_builder_installs():
    """The stamp and the builder read one agent list, so none goes unrecorded.

    Antigravity was installable while both hand-written copies of the
    list omitted it, so its version could never be captured.
    """
    from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
    dictWorkflow = _fdictWorkflowWithOneModel()
    dictStamp = fdictBuildAiProvenanceStamp(
        dictWorkflow, "/nonexistent",
        dictAgentCliVersions={s: "1.0" for s in T_AGENT_OVERLAY_NAMES},
    )
    assert "antigravity" in T_AGENT_OVERLAY_NAMES
    assert fbStampMatchesDeclaration(dictStamp, dictWorkflow) is True


def test_agent_version_stamp_accepts_additional_provider_name():
    """Versions captured from an installed additional agent are valid."""
    dictWorkflow = _fdictWorkflowWithOneModel()
    dictStamp = fdictBuildAiProvenanceStamp(
        dictWorkflow, "/nonexistent", dictAgentCliVersions={"pi": "1.0"},
    )
    assert fbStampMatchesDeclaration(dictStamp, dictWorkflow) is True


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


@pytest.mark.falsification
def test_the_capture_asks_each_agent_by_the_command_it_installs(tmp_path):
    """Driven through real bash: Antigravity's command is ``agy``, not its name.

    Kills: asking every agent by its overlay name, so Antigravity's
    version is never captured.
    """
    import os
    import subprocess
    from types import SimpleNamespace
    from vaibify.gui.aiProvenanceCapture import _fdictCaptureAgentCliVersions
    for sBinary, sVersion in (("claude", "2.1.0 (Claude Code)"), ("agy", "0.9")):
        pathBinary = tmp_path / sBinary
        pathBinary.write_text(f"#!/bin/sh\necho '{sVersion}'\n")
        pathBinary.chmod(0o755)
    # The container's coreutils ``timeout``; macOS ships none.
    pathTimeout = tmp_path / "timeout"
    pathTimeout.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    pathTimeout.chmod(0o755)

    class _BashConnection:
        def ftRunInContainerStreamed(self, sContainerId, sCommand):
            processResult = subprocess.run(
                ["bash", "-c", sCommand], capture_output=True, text=True,
                env=dict(os.environ, PATH=f"{tmp_path}:/usr/bin:/bin"),
            )
            return SimpleNamespace(
                iExitCode=processResult.returncode,
                sStdout=processResult.stdout,
            )

    assert _fdictCaptureAgentCliVersions(_BashConnection(), "cid") == {
        "claude": "2.1.0 (Claude Code)", "antigravity": "0.9",
    }
