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
