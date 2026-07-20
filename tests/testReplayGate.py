"""Tests for ``vaibify/reproducibility/replayGate.py``.

The model-declaration criterion is the Replay axis's Level 2 gate:
closed- and open-weights declarations both pass; undeclared is the
only failing state. The per-field gap descriptions and the axis-state
resolver are exercised on plain dicts (the gate never reads files).
"""

import pytest

from vaibify.reproducibility.replayGate import (
    fbModelDeclarationValid,
    fbPromptRecordCurrent,
    fbSupervisionClean,
    fbWorkflowDeclaresAiModels,
    fbWorkflowDeclaresPersonalLayer,
    flistDescribeModelDeclarationGaps,
    fsReplayAxisState,
)


def _fdictClosedWeightsModel(**dictOverrides):
    dictModel = {
        "sVendor": "ExampleVendor",
        "sModelId": "example-model-1",
        "sUseStartDate": "2026-01-01",
        "sUseEndDate": "2026-02-01",
    }
    dictModel.update(dictOverrides)
    return dictModel


def _fdictOpenWeightsModel(**dictOverrides):
    dictModel = _fdictClosedWeightsModel(
        bOpenWeights=True,
        sWeightsSource="https://example.org/weights",
        sWeightsRevisionHash="abc123def456",
    )
    dictModel.update(dictOverrides)
    return dictModel


def _fdictWorkflowWithModels(listModels):
    return {"dictAiProvenance": {"listDeclaredModels": listModels}}


def test_closed_weights_declaration_is_valid():
    assert fbModelDeclarationValid(_fdictClosedWeightsModel()) is True


def test_open_weights_declaration_is_valid():
    assert fbModelDeclarationValid(_fdictOpenWeightsModel()) is True


def test_each_missing_required_field_is_reported():
    for sField in (
        "sVendor", "sModelId", "sUseStartDate", "sUseEndDate",
    ):
        dictModel = _fdictClosedWeightsModel(**{sField: ""})
        assert flistDescribeModelDeclarationGaps(dictModel) == [sField]
        assert fbModelDeclarationValid(dictModel) is False


def test_open_weights_requires_source_and_hash():
    dictModel = _fdictOpenWeightsModel(sWeightsSource="")
    assert flistDescribeModelDeclarationGaps(dictModel) == [
        "sWeightsSource",
    ]
    dictModel = _fdictOpenWeightsModel(sWeightsRevisionHash="  ")
    assert flistDescribeModelDeclarationGaps(dictModel) == [
        "sWeightsRevisionHash",
    ]


def test_non_dict_declaration_reports_every_required_field():
    assert flistDescribeModelDeclarationGaps(None) == [
        "sVendor", "sModelId", "sUseStartDate", "sUseEndDate",
    ]


def test_empty_model_list_fails_the_criterion():
    assert fbWorkflowDeclaresAiModels(
        _fdictWorkflowWithModels([]),
    ) is False
    assert fbWorkflowDeclaresAiModels({}) is False
    assert fbWorkflowDeclaresAiModels(None) is False


def test_one_invalid_model_fails_the_whole_criterion():
    listModels = [
        _fdictClosedWeightsModel(),
        _fdictClosedWeightsModel(sModelId=""),
    ]
    assert fbWorkflowDeclaresAiModels(
        _fdictWorkflowWithModels(listModels),
    ) is False


def test_multiple_valid_models_pass():
    listModels = [
        _fdictClosedWeightsModel(),
        _fdictOpenWeightsModel(sModelId="example-model-2"),
    ]
    assert fbWorkflowDeclaresAiModels(
        _fdictWorkflowWithModels(listModels),
    ) is True


def test_prompt_record_unconfigured_is_trivially_current():
    assert fbPromptRecordCurrent({}) is True
    assert fbPromptRecordCurrent(None) is True


def test_prompt_record_enabled_requires_first_capture_review():
    dictWorkflow = {"dictAiProvenance": {
        "dictPromptRecord": {"bEnabled": True},
    }}
    assert fbPromptRecordCurrent(dictWorkflow) is False
    dictWorkflow["dictAiProvenance"]["dictPromptRecord"][
        "bFirstCaptureReviewed"] = True
    assert fbPromptRecordCurrent(dictWorkflow) is True


def test_supervision_unconfigured_is_trivially_clean():
    assert fbSupervisionClean({}) is True


def test_supervision_enabled_with_flags_is_not_clean():
    dictWorkflow = {"dictAiProvenance": {
        "dictSupervision": {
            "bEnabled": True, "iUnattributedFlagCount": 2,
        },
    }}
    assert fbSupervisionClean(dictWorkflow) is False


def test_personal_layer_unanswered_fails_the_criterion():
    assert fbWorkflowDeclaresPersonalLayer({}) is False
    assert fbWorkflowDeclaresPersonalLayer(None) is False
    assert fbWorkflowDeclaresPersonalLayer(
        {"dictAiProvenance": {"dictPersonalLayer": {}}},
    ) is False


def test_personal_layer_each_status_satisfies_the_criterion():
    for sStatus in ("none", "declared-private", "included"):
        dictWorkflow = {"dictAiProvenance": {
            "dictPersonalLayer": {"sStatus": sStatus},
        }}
        assert fbWorkflowDeclaresPersonalLayer(dictWorkflow) is True


def test_personal_layer_private_with_zero_hashes_satisfies():
    # Disclosure is never required: the answer alone meets the
    # criterion, with or without hash commitments.
    dictWorkflow = {"dictAiProvenance": {
        "dictPersonalLayer": {
            "sStatus": "declared-private",
            "listHashCommitments": [],
        },
    }}
    assert fbWorkflowDeclaresPersonalLayer(dictWorkflow) is True


def test_personal_layer_unknown_status_fails_the_criterion():
    dictWorkflow = {"dictAiProvenance": {
        "dictPersonalLayer": {"sStatus": "partially"},
    }}
    assert fbWorkflowDeclaresPersonalLayer(dictWorkflow) is False


def test_axis_state_untracked_without_declaration():
    assert fsReplayAxisState({}) == "untracked"
    dictWorkflow = {"dictAiProvenance": {
        "dictPromptRecord": {
            "bEnabled": True, "bFirstCaptureReviewed": True,
        },
    }}
    # Recording without a declaration is still untracked: the
    # transcript of an undeclared agent is not honest provenance.
    assert fsReplayAxisState(dictWorkflow) == "untracked"


@pytest.mark.falsification
def test_axis_declared_requires_personal_layer_answer():
    """Models declared but the personal layer unanswered stays untracked.

    The Declared state claims the whole declaration is complete; a
    declared model with the instruction stack's fourth layer
    unaccounted for is an incomplete answer. The paired positive
    control — answering "none" and nothing else — proves the fixture
    was blocked for the personal-layer reason alone.

    Kills: Remove the `if not
    fbWorkflowDeclaresPersonalLayer(dictWorkflow): return "untracked"`
    conjunct from `fsReplayAxisState` in replayGate.py
    """
    dictWorkflow = _fdictWorkflowWithModels(
        [_fdictClosedWeightsModel()],
    )
    assert fsReplayAxisState(dictWorkflow) == "untracked"
    dictWorkflow["dictAiProvenance"]["dictPersonalLayer"] = {
        "sStatus": "none",
    }
    assert fsReplayAxisState(dictWorkflow) == "declared"


def test_axis_state_ladder_declared_recorded_supervised():
    dictWorkflow = _fdictWorkflowWithModels(
        [_fdictClosedWeightsModel()],
    )
    dictWorkflow["dictAiProvenance"]["dictPersonalLayer"] = {
        "sStatus": "none",
    }
    assert fsReplayAxisState(dictWorkflow) == "declared"
    dictWorkflow["dictAiProvenance"]["dictPromptRecord"] = {
        "bEnabled": True, "bFirstCaptureReviewed": True,
    }
    assert fsReplayAxisState(dictWorkflow) == "recorded"
    dictWorkflow["dictAiProvenance"]["dictSupervision"] = {
        "bEnabled": True,
    }
    assert fsReplayAxisState(dictWorkflow) == "supervised"
