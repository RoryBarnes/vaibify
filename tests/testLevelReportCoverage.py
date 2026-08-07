"""Behaviour tests for the host-side AICS level report.

vaibify/cli/levelReport.py answers "what level is this project, and what
blocks the next one?" from the same gates the dashboard poll uses, so
the CLI and dashboard cannot disagree. These tests drive its assembly
(from mocked gate outputs — the gates have their own coverage), its
container-input loader, and its printer, asserting the payload shape
and the printed lines, not merely touching the code.
"""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.cli import levelReport


def _fdictWorkflow():
    return {
        "sWorkflowName": "demo",
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [
            {"sName": "Generate", "sLabel": "A01"},
            {"sName": "Plot", "sLabel": "A02"},
        ],
    }


def test_build_level_report_assembles_gate_outputs():
    dictWorkflow = _fdictWorkflow()
    listL1 = [{"sScope": "workflow", "sCriterion": "manifest-missing",
               "sRemediationHint": "generate a manifest"}]
    with patch.object(levelReport, "_flistBuildStepReports",
                      return_value=[{"iStepIndex": 0}]), \
         patch("vaibify.reproducibility.levelGates.flistLevel1Blockers",
               return_value=listL1), \
         patch("vaibify.reproducibility.levelGates.flistLevel2Blockers",
               return_value=[]), \
         patch("vaibify.reproducibility.levelGates.flistLevel3Blockers",
               return_value=[]), \
         patch("vaibify.reproducibility.levelGates.fiAICSLevel",
               return_value=1), \
         patch("vaibify.reproducibility.levelGates."
               "fdictComputeStepLevelStates", return_value={}):
        dictReport = levelReport.fdictBuildLevelReport(
            dictWorkflow, MagicMock())
    assert dictReport["iAICSLevel"] == 1
    assert dictReport["sAICSLevelName"] == "Self-Consistent"
    assert dictReport["sWorkflowName"] == "demo"
    assert dictReport["listLevel1Blockers"] == listL1
    assert dictReport["listLevel2Blockers"] == []
    assert dictReport["listUnevaluatedCriteria"] == ["script-stale"]


def test_build_step_reports_carries_label_and_cells():
    dictWorkflow = _fdictWorkflow()
    listReports = levelReport._flistBuildStepReports(
        dictWorkflow, {0: {"1": "ok"}, 1: {}})
    assert len(listReports) == 2
    assert listReports[0]["sLabel"] == "A01"
    assert listReports[0]["sName"] == "Generate"
    assert listReports[0]["dictLevelStates"] == {"1": "ok"}


def test_step_reports_derive_label_when_absent():
    """A step with no persisted sLabel gets one from its index."""
    dictWorkflow = {"listSteps": [{"sName": "X"}]}
    with patch("vaibify.gui.pipelineUtils.fsLabelFromStepIndex",
               return_value="A01") as mockLabel:
        listReports = levelReport._flistBuildStepReports(
            dictWorkflow, {})
    assert listReports[0]["sLabel"] == "A01"
    mockLabel.assert_called_once()


def test_summarize_blocker_prefers_step_label():
    sLine = levelReport._fsSummarizeBlocker({
        "sStepLabel": "A02", "sCriterion": "output-stale",
        "sRemediationHint": "re-run the step",
    })
    assert "A02" in sLine
    assert "output-stale" in sLine
    assert "re-run the step" in sLine


def test_summarize_blocker_falls_back_to_scope():
    sLine = levelReport._fsSummarizeBlocker({
        "sScope": "workflow", "sCriterion": "manifest-missing",
    })
    assert "workflow" in sLine


@pytest.mark.parametrize("iLevel,sName", [
    (0, "Sandbox"), (1, "Self-Consistent"),
    (2, "Published"), (3, "Reproducible"),
])
def test_level_names_cover_every_tier(iLevel, sName):
    assert levelReport._DICT_LEVEL_NAMES[iLevel] == sName


def test_print_level_report_emits_level_and_blocker_groups(capsys):
    dictReport = {
        "iAICSLevel": 2,
        "sAICSLevelName": "Published",
        "sWorkflowName": "demo",
        "listLevel1Blockers": [],
        "listLevel2Blockers": [
            {"sScope": "workflow", "sCriterion": "no-arxiv",
             "sRemediationHint": "record an arXiv id"},
        ],
        "listLevel3Blockers": [],
        "listUnevaluatedCriteria": ["script-stale"],
    }
    levelReport.fnPrintLevelReport(dictReport)
    sOut = capsys.readouterr().out
    assert "AICS level: 2 (Published) - demo" in sOut
    assert "Level 2 blockers: 1" in sOut
    assert "no-arxiv" in sOut
    assert "script-stale" in sOut


def test_print_level_report_names_unnamed_project(capsys):
    levelReport.fnPrintLevelReport({
        "iAICSLevel": 0, "sAICSLevelName": "Sandbox",
        "sWorkflowName": "",
        "listLevel1Blockers": [], "listLevel2Blockers": [],
        "listLevel3Blockers": [], "listUnevaluatedCriteria": [],
    })
    assert "(unnamed project)" in capsys.readouterr().out


def test_load_container_inputs_raises_without_a_project():
    with patch("vaibify.gui.workflowManager."
               "flistFindWorkflowsInContainer", return_value=[]):
        with pytest.raises(LookupError):
            levelReport.ftLoadContainerLevelInputs(MagicMock(), "cid")


def test_load_container_inputs_returns_workflow_and_repo():
    dictFound = {
        "sPath": "/workspace/repo/.vaibify/workflows/project.json",
        "sProjectRepoPath": "/workspace/repo",
    }
    with patch("vaibify.gui.workflowManager."
               "flistFindWorkflowsInContainer",
               return_value=[dictFound]), \
         patch("vaibify.gui.workflowManager."
               "fdictLoadWorkflowFromContainer",
               return_value={"listSteps": []}):
        dictWorkflow, filesRepo = levelReport.ftLoadContainerLevelInputs(
            MagicMock(), "cid")
    assert dictWorkflow["sProjectRepoPath"] == "/workspace/repo"
    assert filesRepo is not None
