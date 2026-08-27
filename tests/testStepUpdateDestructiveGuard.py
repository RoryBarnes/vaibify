"""Tests for the destructive-edit guard on PUT /api/steps/{id}/{index}.

update-step is agent-invokable but must refuse edits that empty
``saTestCommands`` or ``saOutputDataFiles`` unless the request explicitly
sets ``bConfirmDestructive=True``. Non-destructive updates remain
unaffected so agent-driven content edits continue to work.
"""

import pytest
from fastapi import HTTPException

from vaibify.gui.routes.stepRoutes import _fnRequireDestructiveConfirm


def _fdictWorkflowWithStep(saTestCommands=None, saOutputDataFiles=None):
    return {
        "listSteps": [{
            "sName": "S",
            "sDirectory": "s",
            "saTestCommands": saTestCommands or [],
            "saOutputDataFiles": saOutputDataFiles or [],
        }],
    }


def test_empty_test_commands_blocked_without_confirm():
    dictWorkflow = _fdictWorkflowWithStep(
        saTestCommands=["pytest -q"],
    )
    with pytest.raises(HTTPException) as excInfo:
        _fnRequireDestructiveConfirm(
            dictWorkflow, 0, {"saTestCommands": []}, False,
        )
    assert excInfo.value.status_code == 400
    assert "saTestCommands" in excInfo.value.detail


def test_empty_data_files_blocked_without_confirm():
    dictWorkflow = _fdictWorkflowWithStep(
        saOutputDataFiles=["data.csv"],
    )
    with pytest.raises(HTTPException) as excInfo:
        _fnRequireDestructiveConfirm(
            dictWorkflow, 0, {"saOutputDataFiles": []}, False,
        )
    assert excInfo.value.status_code == 400
    assert "saOutputDataFiles" in excInfo.value.detail


def test_confirm_flag_allows_emptying():
    dictWorkflow = _fdictWorkflowWithStep(
        saTestCommands=["pytest"],
    )
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0, {"saTestCommands": []}, True,
    )


def test_non_destructive_update_passes():
    dictWorkflow = _fdictWorkflowWithStep(
        saTestCommands=["pytest"],
    )
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0, {"sName": "RenamedStep"}, False,
    )


def test_replacing_not_emptying_passes():
    dictWorkflow = _fdictWorkflowWithStep(
        saTestCommands=["pytest"],
    )
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0, {"saTestCommands": ["pytest -v"]}, False,
    )


def test_emptying_already_empty_field_passes():
    dictWorkflow = _fdictWorkflowWithStep(saTestCommands=[])
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0, {"saTestCommands": []}, False,
    )


def test_empty_input_data_files_blocked_without_confirm():
    dictWorkflow = {
        "listSteps": [{
            "sName": "S",
            "sDirectory": "s",
            "saTestCommands": [],
            "saOutputDataFiles": [],
            "saInputDataFiles": ["data/raw.csv"],
        }],
    }
    with pytest.raises(HTTPException) as excInfo:
        _fnRequireDestructiveConfirm(
            dictWorkflow, 0, {"saInputDataFiles": []}, False,
        )
    assert excInfo.value.status_code == 400
    assert "saInputDataFiles" in excInfo.value.detail


def _fdictWorkflowWithTestCategories(dictTests):
    return {"listSteps": [{
        "sName": "S", "sDirectory": "s",
        "saTestCommands": [], "saOutputDataFiles": [],
        "dictTests": dictTests,
    }]}


S_QUANTITATIVE_COMMAND = "python -m pytest testQuantitative.py -v"
S_QUALITATIVE_COMMAND = "python -m pytest testQualitative.py -v"


def test_declaring_one_category_cannot_silently_drop_the_others():
    """The hazard this guard exists for, stated as the caller hits it.

    ``dictTests`` is assigned wholesale, so an agent declaring a
    qualitative suite by sending only that category erases the
    quantitative one. Nothing downstream raises -- the aggregators read
    a missing category as absent and the derivation marks the vanished
    axis ``unnecessary``, which counts GREEN. So the deletion would be
    reported to the researcher as a passing step.
    """
    dictWorkflow = _fdictWorkflowWithTestCategories({
        "dictQuantitative": {"saCommands": [S_QUANTITATIVE_COMMAND],
                             "sFilePath": "testQuantitative.py"},
    })
    with pytest.raises(HTTPException) as excInfo:
        _fnRequireDestructiveConfirm(
            dictWorkflow, 0,
            {"dictTests": {"dictQualitative": {
                "saCommands": [S_QUALITATIVE_COMMAND],
                "sFilePath": "testQualitative.py"}}},
            False,
        )
    assert excInfo.value.status_code == 400
    assert "dictQuantitative" in excInfo.value.detail
    # The refusal must teach the fix, because an agent that cannot see
    # why it was refused improvises a reason instead.
    assert "wholesale" in excInfo.value.detail


def test_sending_every_category_declares_without_refusal():
    """The correct call is not refused: keep what you are not changing."""
    dictWorkflow = _fdictWorkflowWithTestCategories({
        "dictQuantitative": {"saCommands": [S_QUANTITATIVE_COMMAND],
                             "sFilePath": "testQuantitative.py"},
    })
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0,
        {"dictTests": {
            "dictQuantitative": {"saCommands": [S_QUANTITATIVE_COMMAND],
                                 "sFilePath": "testQuantitative.py"},
            "dictQualitative": {"saCommands": [S_QUALITATIVE_COMMAND],
                                "sFilePath": "testQualitative.py"}}},
        False,
    )


def test_dropping_a_category_is_allowed_when_confirmed():
    """Deleting a suite stays possible -- it just has to be meant."""
    dictWorkflow = _fdictWorkflowWithTestCategories({
        "dictQuantitative": {"saCommands": [S_QUANTITATIVE_COMMAND],
                             "sFilePath": "testQuantitative.py"},
    })
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0, {"dictTests": {}}, True,
    )


def test_declaring_a_category_on_a_step_with_none_is_not_destructive():
    dictWorkflow = _fdictWorkflowWithTestCategories({
        "dictQualitative": {"saCommands": [], "sFilePath": ""},
    })
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0,
        {"dictTests": {"dictQualitative": {
            "saCommands": [S_QUALITATIVE_COMMAND],
            "sFilePath": "testQualitative.py"}}},
        False,
    )


def test_an_update_that_does_not_touch_dict_tests_is_unaffected():
    dictWorkflow = _fdictWorkflowWithTestCategories({
        "dictQuantitative": {"saCommands": [S_QUANTITATIVE_COMMAND],
                             "sFilePath": "testQuantitative.py"},
    })
    _fnRequireDestructiveConfirm(
        dictWorkflow, 0, {"sDescription": "prose"}, False,
    )


def test_step_update_request_accepts_input_declaration_fields():
    """The Pydantic whitelist must not silently drop the new fields."""
    from vaibify.gui.pipelineServer import StepUpdateRequest
    requestUpdate = StepUpdateRequest(
        saInputDataFiles=["data/raw.csv"],
        bNoInputData=True,
        listRemoteData=[{
            "sPath": "data/raw.csv",
            "sSourceUrl": "https://archive.example/query",
            "sDigestBecameCurrentUtc": "",
            "sSha256": "",
        }],
    )
    dictDump = requestUpdate.model_dump()
    assert dictDump["saInputDataFiles"] == ["data/raw.csv"]
    assert dictDump["bNoInputData"] is True
    assert dictDump["listRemoteData"][0]["sPath"] == "data/raw.csv"
