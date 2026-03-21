"""Tests for vaibify.gui.director pure functions."""

import pytest

from vaibify.gui.director import (
    fsResolveVariables,
    fbValidateWorkflow,
    fiResolveCoreCount,
    fsExtractExecutableName,
    fsResolveOutputPath,
    fsGenerateLogPath,
)


def test_fiResolveCoreCount_auto():
    iCount = fiResolveCoreCount(-1)
    assert iCount >= 1


def test_fiResolveCoreCount_explicit():
    assert fiResolveCoreCount(4) == 4


def test_fiResolveCoreCount_clamps_to_total():
    iResult = fiResolveCoreCount(99999)
    import multiprocessing
    assert iResult <= multiprocessing.cpu_count()


def test_fbValidateWorkflow_valid():
    dictWorkflow = {"listSteps": [{
        "sName": "Test", "sDirectory": "/tmp",
        "saPlotCommands": ["echo"], "saPlotFiles": [],
    }]}
    assert fbValidateWorkflow(dictWorkflow) is True


def test_fbValidateWorkflow_missing_listSteps():
    assert fbValidateWorkflow({}) is False


def test_fbValidateWorkflow_missing_step_field():
    dictWorkflow = {"listSteps": [{"sName": "Test"}]}
    assert fbValidateWorkflow(dictWorkflow) is False


def test_fsResolveVariables_replaces_tokens():
    sResult = fsResolveVariables(
        "{sDir}/file.txt", {"sDir": "/workspace"})
    assert sResult == "/workspace/file.txt"


def test_fsResolveVariables_raises_on_unknown():
    with pytest.raises(KeyError):
        fsResolveVariables("{missing}", {})


def test_fsExtractExecutableName_python():
    assert fsExtractExecutableName("python script.py") == "script.py"


def test_fsExtractExecutableName_bash_chain():
    sResult = fsExtractExecutableName("cd dir && python run.py")
    assert sResult == "python"


def test_fsExtractExecutableName_empty():
    assert fsExtractExecutableName("") == "unknown"


def test_fsGenerateLogPath_contains_timestamp():
    sPath = fsGenerateLogPath("/logs", "My Workflow")
    assert sPath.startswith("/logs/")
    assert "My_Workflow" in sPath
    assert sPath.endswith(".log")


def test_fsResolveOutputPath_absolute():
    sResult = fsResolveOutputPath(
        "/abs/file.txt", {}, "/workspace")
    assert sResult == "/abs/file.txt"


def test_fsResolveOutputPath_relative():
    sResult = fsResolveOutputPath(
        "file.txt", {}, "/workspace")
    assert sResult == "/workspace/file.txt"
