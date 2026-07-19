"""Unit tests for the AICS Level 2 (Publication) gate.

Phase 2 ships ``fbAtLeastLevel2`` composed of three orthogonal
predicates on top of L1: GitHub mirror fully synced at the verified
SHA, Zenodo deposit fully synced with a published DOI on the workflow's
endpoint, and the workflow contains at least one ``ai-declaration``
step. The tests below exercise each predicate individually plus the
composition and the staleness threshold on the cached verify status.

The cached sync status is read from a per-workflow JSON file in
``<projectRepo>/.vaibify/syncStatus.json``; tests use ``tmp_path`` to
write a representative file rather than mocking the IO so the cache
schema and the gate stay in sync.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
    S_DECLARATION_TEMPLATE,
    S_DEFAULT_DECLARATION_FILENAME,
    fbDeclarationFileExists,
    fbStepIsAiDeclaration,
    fdictBuildAiDeclarationStep,
    fnWriteDeclarationTemplate,
)
from vaibify.reproducibility.levelGates import (
    fbAtLeastLevel1,
    fbAtLeastLevel2,
    fbWorkflowFullySyncedWithGithub,
    fbWorkflowFullySyncedWithZenodo,
    fbWorkflowHasAiDeclarationStep,
    fdictLevel2Gaps,
    fiAICSLevel,
)


def _fsBuildIsoTimestamp(fHoursAgo=0.0):
    """Return an ISO-8601 UTC timestamp fHoursAgo before now."""
    dtNow = datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    return dtNow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fnWriteSyncStatusFile(sProjectRepo, dictPerService):
    """Write a sample syncStatus.json under .vaibify/."""
    sDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    sPath = os.path.join(sDir, "syncStatus.json")
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictPerService, fileHandle)


def _fdictAllGreenStep(sStepKind=None):
    """Return one L1-satisfying step, with optional sStepKind."""
    dictStep = {
        "sName": "A", "sDirectory": "A",
        "bNoInputData": True,
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }
    if sStepKind:
        dictStep["sStepKind"] = sStepKind
    return dictStep


def _fdictBuildLevel2ReadyWorkflow():
    """Return a workflow with all four L2 criteria pre-satisfied at the dict level.

    The cache files still need to be written under the project repo
    for the sync predicates to read; each test that needs L2-green
    writes the appropriate files via :func:`_fnWriteSyncStatusFile`.
    """
    return {
        "listSteps": [
            _fdictAllGreenStep(),
            _fdictAllGreenStep(
                sStepKind=S_AI_DECLARATION_STEP_KIND,
            ),
        ],
        "dictRemotes": {
            "github": {
                "sOwner": "u", "sRepo": "r", "sBranch": "main",
                "sCommittedSha": "abc123",
            },
            "zenodo": {
                "sRecordId": "1234", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
        },
        "dictAiProvenance": {
            "listDeclaredModels": [{
                "sVendor": "ExampleVendor",
                "sModelId": "example-model-1",
                "sUseStartDate": "2026-01-01",
                "sUseEndDate": "2026-02-01",
            }],
        },
    }


# ------------------------------------------------------------------------
# fbStepIsAiDeclaration
# ------------------------------------------------------------------------


def test_fbStepIsAiDeclaration_recognizes_kind():
    assert fbStepIsAiDeclaration(
        {"sStepKind": "ai-declaration"}
    ) is True


def test_fbStepIsAiDeclaration_missing_kind_is_false():
    assert fbStepIsAiDeclaration(
        {"sName": "Legacy"}
    ) is False


def test_fbStepIsAiDeclaration_data_kind_is_false():
    assert fbStepIsAiDeclaration(
        {"sStepKind": "data"}
    ) is False


def test_fbStepIsAiDeclaration_handles_corrupt_step():
    assert fbStepIsAiDeclaration(None) is False
    assert fbStepIsAiDeclaration("not a dict") is False


# ------------------------------------------------------------------------
# fbWorkflowHasAiDeclarationStep
# ------------------------------------------------------------------------


def test_fbWorkflowHasAiDeclarationStep_finds_step():
    dictWorkflow = {
        "listSteps": [
            {"sName": "A"},
            {"sStepKind": "ai-declaration"},
        ],
    }
    assert fbWorkflowHasAiDeclarationStep(dictWorkflow) is True


def test_fbWorkflowHasAiDeclarationStep_missing_step_is_false():
    dictWorkflow = {"listSteps": [{"sName": "A"}]}
    assert fbWorkflowHasAiDeclarationStep(dictWorkflow) is False


def test_fbWorkflowHasAiDeclarationStep_handles_empty_workflow():
    assert fbWorkflowHasAiDeclarationStep({}) is False
    assert fbWorkflowHasAiDeclarationStep({"listSteps": []}) is False


# ------------------------------------------------------------------------
# fbWorkflowFullySyncedWithGithub
# ------------------------------------------------------------------------


def test_fbWorkflowFullySyncedWithGithub_fresh_full_match_returns_true(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is True


def test_fbWorkflowFullySyncedWithGithub_stale_returns_false(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=48.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


def test_fbWorkflowFullySyncedWithGithub_diverged_returns_false(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 2,
            "listDiverged": [{"sPath": "a"}],
            "sCommittedShaVerified": "abc123",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


def test_fbWorkflowFullySyncedWithGithub_sha_mismatch_returns_false(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "OLD_SHA",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


def test_fbWorkflowFullySyncedWithGithub_no_cache_returns_false(tmp_path):
    """An L2 gate cannot light off a never-verified cache."""
    sProjectRepo = str(tmp_path)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is False


# ------------------------------------------------------------------------
# fbWorkflowFullySyncedWithZenodo
# ------------------------------------------------------------------------


def test_fbWorkflowFullySyncedWithZenodo_fresh_full_match_returns_true(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepo,
    ) is True


def test_fbWorkflowFullySyncedWithZenodo_missing_doi_returns_false(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "",
            "sEndpointVerified": "sandbox",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepo,
    ) is False


def test_fbWorkflowFullySyncedWithZenodo_endpoint_mismatch_returns_false(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    })
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictWorkflow["dictRemotes"]["zenodo"]["sService"] = "production"
    assert fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepo,
    ) is False


# ------------------------------------------------------------------------
# fbAtLeastLevel2 composition
# ------------------------------------------------------------------------


def _fnWriteAllGreenSyncStatus(sProjectRepo):
    """Write fresh-and-matching cache files for both services."""
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    })


def test_fbAtLeastLevel2_all_criteria_green_returns_true(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncStatus(sProjectRepo)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    assert fbAtLeastLevel1(dictWorkflow, sProjectRepo) is True
    assert fbAtLeastLevel2(dictWorkflow, sProjectRepo) is True
    assert fiAICSLevel(dictWorkflow, sProjectRepo) == 2


def test_fbAtLeastLevel2_no_ai_declaration_step_returns_false(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncStatus(sProjectRepo)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictWorkflow["listSteps"] = [_fdictAllGreenStep()]
    assert fbAtLeastLevel2(dictWorkflow, sProjectRepo) is False


def test_fbAtLeastLevel2_l1_failure_blocks_l2(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncStatus(sProjectRepo)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictWorkflow["listSteps"][0]["dictVerification"]["sUser"] = "failed"
    assert fbAtLeastLevel2(dictWorkflow, sProjectRepo) is False


# ------------------------------------------------------------------------
# fdictLevel2Gaps shape contract
# ------------------------------------------------------------------------


def test_fdictLevel2Gaps_returns_per_criterion_dict(tmp_path):
    sProjectRepo = str(tmp_path)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictGaps = fdictLevel2Gaps(dictWorkflow, sProjectRepo)
    assert set(dictGaps.keys()) == {
        "bAtLeastLevel1",
        "bGithubFullySynced",
        "bZenodoFullySynced",
        "bArxivFullySynced",
        "bAiDeclarationAttested",
        "bAiModelsDeclared",
        "bPromptRecordCurrent",
        "bSupervisionClean",
        "bProjectContextFileExists",
        "bAtLeastLevel2",
    }
    assert dictGaps["bAiDeclarationAttested"] is True
    assert dictGaps["bAiModelsDeclared"] is True
    assert dictGaps["bProjectContextFileExists"] is False
    assert dictGaps["bGithubFullySynced"] is False
    assert dictGaps["bArxivFullySynced"] is True


def test_fdictLevel2Gaps_all_green_when_l2_satisfied(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncStatus(sProjectRepo)
    dictWorkflow = _fdictBuildLevel2ReadyWorkflow()
    dictGaps = fdictLevel2Gaps(dictWorkflow, sProjectRepo)
    assert dictGaps["bAtLeastLevel2"] is True


# ------------------------------------------------------------------------
# Template helpers
# ------------------------------------------------------------------------


def test_fnWriteDeclarationTemplate_writes_template(tmp_path):
    sAbsolute = fnWriteDeclarationTemplate(
        str(tmp_path), S_DEFAULT_DECLARATION_FILENAME,
    )
    assert os.path.isfile(sAbsolute)
    with open(sAbsolute, "r", encoding="utf-8") as fileHandle:
        assert fileHandle.read() == S_DECLARATION_TEMPLATE


def test_fnWriteDeclarationTemplate_refuses_to_overwrite(tmp_path):
    sRelative = S_DEFAULT_DECLARATION_FILENAME
    fnWriteDeclarationTemplate(str(tmp_path), sRelative)
    with pytest.raises(FileExistsError):
        fnWriteDeclarationTemplate(str(tmp_path), sRelative)


def test_fnWriteDeclarationTemplate_requires_repo(tmp_path):
    with pytest.raises(ValueError):
        fnWriteDeclarationTemplate("", "AI_USAGE.md")
    with pytest.raises(ValueError):
        fnWriteDeclarationTemplate(str(tmp_path), "")


def test_fbDeclarationFileExists_returns_false_for_missing(tmp_path):
    assert fbDeclarationFileExists(
        str(tmp_path), "AI_USAGE.md",
    ) is False
    fnWriteDeclarationTemplate(str(tmp_path), "AI_USAGE.md")
    assert fbDeclarationFileExists(
        str(tmp_path), "AI_USAGE.md",
    ) is True


def test_fdictBuildAiDeclarationStep_has_unnecessary_categories():
    dictStep = fdictBuildAiDeclarationStep("AI Use", "AI_USAGE.md")
    assert dictStep["sStepKind"] == S_AI_DECLARATION_STEP_KIND
    assert dictStep["sDeclarationFile"] == "AI_USAGE.md"
    assert dictStep["bInteractive"] is True
    assert dictStep["sDirectory"] == "aiDeclaration"
    dictV = dictStep["dictVerification"]
    for sKey in (
        "sUnitTest", "sIntegrity", "sQualitative", "sQuantitative",
    ):
        assert dictV[sKey] == "unnecessary"
