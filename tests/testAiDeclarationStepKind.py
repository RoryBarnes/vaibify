"""Tests for the AI Declaration step kind.

Covers the contract that a step with ``sStepKind == "ai-declaration"``:

1. Survives save/load round trips without losing its kind or its
   ``sDeclarationFile`` pointer.
2. Validates against ``fsDescribeValidationFailure`` (the same
   structural validator data steps go through) when constructed via
   ``fdictBuildAiDeclarationStep``.
3. Satisfies the L1 per-step gate when the user attests via
   ``sUser == "passed"`` (every test category defaults to
   ``"unnecessary"``, so the test-passing predicate is automatically
   green).
4. The renderer branch shipped in Phase 2 is exposed on
   ``VaibifyStepRenderer.fsRenderAiDeclarationBody`` so a future
   refactor that hides the function will be caught.
"""

import json
from unittest.mock import MagicMock

from vaibify.gui import workflowManager
from vaibify.gui.workflowManager import (
    fsDescribeValidationFailure,
)
from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
    S_DEFAULT_DECLARATION_FILENAME,
    fdictBuildAiDeclarationStep,
)
from vaibify.reproducibility.levelGates import (
    fbStepIsAtLeastLevel1,
)


def _fdictMakeWorkflow(dictStep):
    """Return a minimal valid workflow wrapping a single step."""
    return {
        "iWorkflowSchemaVersion": 4,
        "sPlotDirectory": "Plot",
        "listSteps": [dictStep],
    }


def test_fdictBuildAiDeclarationStep_passes_structural_validator():
    dictStep = fdictBuildAiDeclarationStep(
        "AI Declaration", S_DEFAULT_DECLARATION_FILENAME,
    )
    dictWorkflow = _fdictMakeWorkflow(dictStep)
    sFailure = fsDescribeValidationFailure(dictWorkflow)
    assert sFailure == "", (
        "ai-declaration step must satisfy the same structural "
        f"validator as data steps; got: {sFailure!r}"
    )


def test_ai_declaration_step_l1_passes_when_user_attests():
    dictStep = fdictBuildAiDeclarationStep(
        "AI Declaration", S_DEFAULT_DECLARATION_FILENAME,
    )
    assert fbStepIsAtLeastLevel1(dictStep) is False, (
        "untested sUser must block L1 even for ai-declaration steps."
    )
    dictStep["dictVerification"]["sUser"] = "passed"
    assert fbStepIsAtLeastLevel1(dictStep) is True


def test_save_then_load_round_trips_step_kind(tmp_path):
    """A workflow saved with an ai-declaration step round-trips clean.

    The save/load pair are mocked through the docker filesystem so the
    test exercises the persistence shape rather than any specific
    container backend.
    """
    sRepo = "/workspace/Project"
    sWorkflowPath = sRepo + "/.vaibify/workflows/w.json"
    dictPaths = {}

    mockDocker = MagicMock()

    def _fWrite(_sCid, sPath, baBody):
        dictPaths[sPath] = baBody

    def _fFetch(_sCid, sPath):
        if sPath in dictPaths:
            return dictPaths[sPath]
        raise FileNotFoundError(sPath)

    def _fExec(_sCid, sCommand):
        return (0, "")

    mockDocker.fnWriteFile.side_effect = _fWrite
    mockDocker.fbaFetchFile.side_effect = _fFetch
    mockDocker.ftResultExecuteCommand.side_effect = _fExec

    dictStep = fdictBuildAiDeclarationStep(
        "AI Declaration", S_DEFAULT_DECLARATION_FILENAME,
    )
    dictStep["dictVerification"]["sUser"] = "passed"
    dictWorkflow = _fdictMakeWorkflow(dictStep)
    dictWorkflow["sProjectRepoPath"] = sRepo

    workflowManager.fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow,
        sWorkflowPath=sWorkflowPath,
    )
    dictReloaded = workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath=sWorkflowPath,
    )
    dictReloadedStep = dictReloaded["listSteps"][0]
    assert dictReloadedStep["sStepKind"] == S_AI_DECLARATION_STEP_KIND
    assert dictReloadedStep["sDeclarationFile"] == (
        S_DEFAULT_DECLARATION_FILENAME
    )


def test_workflow_json_on_disk_preserves_declaration_file(tmp_path):
    """The serialized JSON keeps ``sDeclarationFile`` verbatim.

    Catches regressions where the field gets stripped by
    ``_fdictStripComputedFields`` or a future write-side migrator.
    """
    sWorkflowPath = "/workspace/Project/.vaibify/workflows/w.json"
    dictWritten = {}

    mockDocker = MagicMock()
    mockDocker.fnWriteFile.side_effect = (
        lambda _cid, sPath, baBody: dictWritten.setdefault(sPath, baBody)
    )
    mockDocker.ftResultExecuteCommand.return_value = (0, "")

    dictStep = fdictBuildAiDeclarationStep(
        "AI Declaration", "docs/AI_USAGE.md",
    )
    dictWorkflow = _fdictMakeWorkflow(dictStep)
    dictWorkflow["sProjectRepoPath"] = "/workspace/Project"

    workflowManager.fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow,
        sWorkflowPath=sWorkflowPath,
    )
    baBytes = dictWritten[sWorkflowPath]
    dictOnDisk = json.loads(baBytes.decode("utf-8"))
    dictPersistedStep = dictOnDisk["listSteps"][0]
    assert dictPersistedStep["sStepKind"] == (
        S_AI_DECLARATION_STEP_KIND
    )
    assert dictPersistedStep["sDeclarationFile"] == "docs/AI_USAGE.md"


def test_renderer_exposes_ai_declaration_body():
    """The JS step renderer must export the new ai-declaration body fn.

    The IIFE return object lives at module top level in the bundled
    static asset; a plain text grep is the cheapest way to assert the
    export survives future renderer refactors without spinning up a
    headless browser.
    """
    from pathlib import Path
    sSource = (
        Path(__file__).resolve().parent.parent
        / "vaibify" / "gui" / "static"
        / "scriptStepRenderer.js"
    ).read_text(encoding="utf-8")
    assert "fsRenderAiDeclarationBody: fsRenderAiDeclarationBody" in (
        sSource
    ), (
        "Renderer IIFE must export fsRenderAiDeclarationBody so "
        "ai-declaration steps render with a file viewer + sUser "
        "badge instead of empty command lists."
    )
    assert 'step.sStepKind === "ai-declaration"' in sSource, (
        "Renderer must branch on sStepKind so data steps and "
        "ai-declaration steps render differently."
    )
