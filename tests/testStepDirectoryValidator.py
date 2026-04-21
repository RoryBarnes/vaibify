"""Unit tests for flistValidateStepDirectories.

Mirrors the coverage of flistValidateOutputFilePaths, applied to
each step's sDirectory field. Paths must be repo-relative and stay
inside the project repo; templates are skipped (resolved at runtime).
"""

from vaibify.gui.workflowManager import flistValidateStepDirectories


def test_accepts_simple_repo_relative():
    dictWorkflow = {
        "listSteps": [{"sName": "S1", "sDirectory": "analysis"}],
    }
    assert flistValidateStepDirectories(dictWorkflow) == []


def test_accepts_nested_repo_relative():
    dictWorkflow = {
        "listSteps": [{"sName": "S1", "sDirectory": "sub/analysis"}],
    }
    assert flistValidateStepDirectories(dictWorkflow) == []


def test_rejects_absolute_path():
    dictWorkflow = {
        "listSteps": [{
            "sName": "S1",
            "sDirectory": "/workspace/ProjectRepo/analysis",
        }],
    }
    listWarnings = flistValidateStepDirectories(dictWorkflow)
    assert len(listWarnings) == 1
    assert "Step01" in listWarnings[0]
    assert "repo-relative" in listWarnings[0]


def test_rejects_escaping_parent():
    dictWorkflow = {
        "listSteps": [{"sName": "S1", "sDirectory": "../outside"}],
    }
    listWarnings = flistValidateStepDirectories(dictWorkflow)
    assert len(listWarnings) == 1
    assert "escapes" in listWarnings[0]


def test_skips_template_paths():
    dictWorkflow = {
        "listSteps": [
            {"sName": "S1", "sDirectory": "{sWizardDir}/analysis"},
            {"sName": "S2", "sDirectory": "{sRepoRoot}/downstream"},
        ],
    }
    assert flistValidateStepDirectories(dictWorkflow) == []


def test_skips_empty_directory():
    dictWorkflow = {
        "listSteps": [{"sName": "S1", "sDirectory": ""}],
    }
    assert flistValidateStepDirectories(dictWorkflow) == []


def test_reports_all_violations_across_steps():
    dictWorkflow = {
        "listSteps": [
            {"sName": "S1", "sDirectory": "/absolute"},
            {"sName": "S2", "sDirectory": "ok"},
            {"sName": "S3", "sDirectory": "../escape"},
        ],
    }
    listWarnings = flistValidateStepDirectories(dictWorkflow)
    assert len(listWarnings) == 2
    assert "Step01" in listWarnings[0]
    assert "Step03" in listWarnings[1]


def test_empty_workflow_returns_empty():
    assert flistValidateStepDirectories({}) == []
    assert flistValidateStepDirectories({"listSteps": []}) == []
