"""Tests for vaibify.cli.commandUtilsDocker shared utilities."""

import json
import sys

import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.commandUtilsDocker import (
    fconnectionRequireDocker,
    fsRequireRunningContainer,
    fdictRequireWorkflow,
    fnPrintJson,
    fbShouldOutputJson,
)


def test_fbShouldOutputJson_true_when_flag_set():
    assert fbShouldOutputJson(True) is True


def test_fbShouldOutputJson_false_when_flag_unset_and_tty():
    with patch("sys.stdout") as mockStdout:
        mockStdout.isatty.return_value = True
        assert fbShouldOutputJson(False) is False


def test_fnPrintJson_outputs_valid_json(capsys):
    dictData = {"sKey": "value", "iCount": 3}
    fnPrintJson(dictData)
    sCaptured = capsys.readouterr().out
    dictParsed = json.loads(sCaptured)
    assert dictParsed["sKey"] == "value"
    assert dictParsed["iCount"] == 3


def test_fnPrintJson_outputs_indented_json(capsys):
    dictData = {"sKey": "value"}
    fnPrintJson(dictData)
    sCaptured = capsys.readouterr().out
    assert "  " in sCaptured


def test_fconnectionRequireDocker_exits_on_import_error():
    with patch(
        "vaibify.docker.dockerConnection.DockerConnection",
        side_effect=ImportError("no docker"),
    ):
        with pytest.raises(SystemExit) as excInfo:
            fconnectionRequireDocker()
        assert excInfo.value.code == 2


def test_fsRequireRunningContainer_exits_when_not_running():
    configProject = SimpleNamespace(sProjectName="testproject")
    mockConnection = MagicMock()
    mockConnection.flistGetRunningContainers.return_value = []
    with patch(
        "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
        return_value=mockConnection,
    ):
        with pytest.raises(SystemExit) as excInfo:
            fsRequireRunningContainer(configProject)
        assert excInfo.value.code == 2


def test_fsRequireRunningContainer_returns_name_when_running():
    configProject = SimpleNamespace(sProjectName="myproject")
    mockConnection = MagicMock()
    mockConnection.flistGetRunningContainers.return_value = [
        {"sName": "myproject", "sContainerId": "abc123"},
    ]
    with patch(
        "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
        return_value=mockConnection,
    ):
        sResult = fsRequireRunningContainer(configProject)
    assert sResult == "myproject"


def test_fdictRequireWorkflow_exits_when_no_workflows():
    mockConnection = MagicMock()
    with patch(
        "vaibify.gui.workflowManager.flistFindWorkflowsInContainer",
        return_value=[],
    ):
        with pytest.raises(SystemExit) as excInfo:
            fdictRequireWorkflow(mockConnection, "testcontainer")
        assert excInfo.value.code == 2
