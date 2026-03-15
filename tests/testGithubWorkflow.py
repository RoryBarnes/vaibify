"""Tests for vaibcask.reproducibility.githubWorkflow generation."""

from pathlib import Path

import pytest

from vaibcask.reproducibility.githubWorkflow import (
    fsGenerateWorkflow,
    fnWriteWorkflow,
    fsGetWorkflowTemplate,
)


@pytest.fixture
def dictConfigDefault():
    """Return a minimal config dict for workflow generation."""
    return {
        "sBranch": "main",
        "sRunner": "ubuntu-latest",
        "sPythonVersion": "3.12",
        "bUploadArtifacts": True,
        "sArtifactsPath": "outputs/",
    }


def test_fsGenerateWorkflow_contains_required_steps(dictConfigDefault):
    sYamlContent = fsGenerateWorkflow(dictConfigDefault)

    assert "Checkout repository" in sYamlContent
    assert "Set up Python" in sYamlContent
    assert "Install VaibCask" in sYamlContent
    assert "Docker image" in sYamlContent or "Build" in sYamlContent
    assert "pipeline" in sYamlContent.lower() or "Run" in sYamlContent


def test_fsGenerateWorkflow_embedded_template_includes_upload():
    """When the on-disk template is absent, the embedded template
    renders the upload step if bUploadArtifacts is True."""
    from unittest.mock import patch

    dictConfig = {
        "sBranch": "main",
        "sPythonVersion": "3.12",
        "bUploadArtifacts": True,
        "sArtifactsPath": "outputs/",
    }

    with patch(
        "vaibcask.reproducibility.githubWorkflow."
        "_fpathLocateTemplateFile",
        return_value=None,
    ):
        sYamlContent = fsGenerateWorkflow(dictConfig)

    assert "Upload artifacts" in sYamlContent
    assert "upload-artifact@v4" in sYamlContent
    assert "outputs/" in sYamlContent


def test_fsGenerateWorkflow_omits_upload_when_disabled(
    dictConfigDefault,
):
    dictConfigDefault["bUploadArtifacts"] = False

    sYamlContent = fsGenerateWorkflow(dictConfigDefault)

    assert "Upload artifacts" not in sYamlContent


def test_fnWriteWorkflow_creates_file(tmp_path, dictConfigDefault):
    sOutputPath = str(
        tmp_path / ".github" / "workflows" / "ci.yml"
    )

    fnWriteWorkflow(dictConfigDefault, sOutputPath=sOutputPath)

    pathOutput = Path(sOutputPath)
    assert pathOutput.exists()

    sContent = pathOutput.read_text()
    assert "Checkout repository" in sContent
    assert "pipeline" in sContent.lower() or "Run" in sContent


def test_workflow_uses_project_name_from_config():
    dictConfig = {
        "sBranch": "main",
        "sPythonVersion": "3.11",
    }

    sYamlContent = fsGenerateWorkflow(dictConfig)

    assert "main" in sYamlContent
    assert "3.11" in sYamlContent


def test_workflow_uses_python_version():
    dictConfig = {
        "sPythonVersion": "3.11",
    }

    sYamlContent = fsGenerateWorkflow(dictConfig)

    assert "3.11" in sYamlContent


def test_fsGetWorkflowTemplate_returns_string():
    sTemplate = fsGetWorkflowTemplate()

    assert isinstance(sTemplate, str)
    assert len(sTemplate) > 50
    assert "Checkout" in sTemplate or "checkout" in sTemplate


def test_workflow_default_values():
    dictConfig = {}

    sYamlContent = fsGenerateWorkflow(dictConfig)

    assert "main" in sYamlContent
    assert "ubuntu-latest" in sYamlContent or "ubuntu" in sYamlContent
    assert "3.12" in sYamlContent
