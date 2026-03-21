"""Tests for pure functions in vaibify.gui.syncDispatcher."""

import pytest

from vaibify.gui.syncDispatcher import (
    fnValidateServiceName,
    fnValidateOverleafProjectId,
    fsBuildDagDot,
    fdictClassifyError,
    fdictSyncResult,
)


def test_fnValidateServiceName_valid():
    fnValidateServiceName("github")
    fnValidateServiceName("overleaf")
    fnValidateServiceName("zenodo")


def test_fnValidateServiceName_invalid_raises():
    with pytest.raises(ValueError):
        fnValidateServiceName("dropbox")


def test_fnValidateOverleafProjectId_valid():
    fnValidateOverleafProjectId("abc123_def-456")


def test_fnValidateOverleafProjectId_invalid_raises():
    with pytest.raises(ValueError):
        fnValidateOverleafProjectId("abc; rm -rf /")


def test_fsBuildDagDot_empty_workflow():
    dictWorkflow = {"listSteps": []}
    sDot = fsBuildDagDot(dictWorkflow)
    assert "digraph" in sDot


def test_fsBuildDagDot_with_steps():
    dictWorkflow = {"listSteps": [
        {"sName": "Step A", "saDataCommands": [],
         "saPlotCommands": [], "saDataFiles": [],
         "saPlotFiles": []},
    ]}
    sDot = fsBuildDagDot(dictWorkflow)
    assert "Step A" in sDot


def test_fdictClassifyError_auth():
    dictResult = fdictClassifyError(128, "authentication failed")
    assert dictResult["sErrorType"] == "auth"


def test_fdictSyncResult_success():
    dictResult = fdictSyncResult(0, "ok")
    assert dictResult["bSuccess"] is True
    assert dictResult["sOutput"] == "ok"


def test_fdictSyncResult_failure():
    dictResult = fdictSyncResult(1, "error")
    assert dictResult["bSuccess"] is False
