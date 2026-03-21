"""Tests for pipelineState with mock Docker connection."""

import json

from vaibify.gui.pipelineState import (
    fnWriteState,
    fnUpdateState,
    fnRecordStepResult,
    fdictReadState,
    fnClearState,
    S_STATE_PATH,
)


class MockDockerConnection:
    """Mock Docker connection for testing state persistence."""

    def __init__(self):
        self.dictFiles = {}
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        sKey = (sContainerId, S_STATE_PATH)
        if "cat" in sCommand and sKey in self.dictFiles:
            sContent = self.dictFiles[sKey].decode("utf-8")
            return (0, sContent)
        if "rm -f" in sCommand:
            self.dictFiles.pop(sKey, None)
            return (0, "")
        return (1, "")


def test_fnWriteState_stores():
    mockDocker = MockDockerConnection()
    dictState = {"bRunning": True, "iStep": 1}
    fnWriteState(mockDocker, "ctr1", dictState)
    baStored = mockDocker.dictFiles[("ctr1", S_STATE_PATH)]
    dictParsed = json.loads(baStored.decode("utf-8"))
    assert dictParsed["bRunning"] is True


def test_fnUpdateState_merges():
    mockDocker = MockDockerConnection()
    dictState = {"bRunning": True, "iActiveStep": 0}
    fnUpdateState(mockDocker, "ctr1", dictState, {"iActiveStep": 3})
    assert dictState["iActiveStep"] == 3
    baStored = mockDocker.dictFiles[("ctr1", S_STATE_PATH)]
    dictParsed = json.loads(baStored.decode("utf-8"))
    assert dictParsed["iActiveStep"] == 3


def test_fnRecordStepResult_adds():
    mockDocker = MockDockerConnection()
    dictState = {"dictStepResults": {}}
    dictResult = {
        "iStepNumber": 2, "sStatus": "passed", "iExitCode": 0,
    }
    fnRecordStepResult(mockDocker, "ctr1", dictState, dictResult)
    assert "2" in dictState["dictStepResults"]
    assert dictState["dictStepResults"]["2"]["sStatus"] == "passed"


def test_fdictReadState_success():
    mockDocker = MockDockerConnection()
    dictState = {"bRunning": False, "iExitCode": 0}
    fnWriteState(mockDocker, "ctr1", dictState)
    dictRead = fdictReadState(mockDocker, "ctr1")
    assert dictRead is not None
    assert dictRead["bRunning"] is False


def test_fdictReadState_missing():
    mockDocker = MockDockerConnection()
    dictRead = fdictReadState(mockDocker, "ctr1")
    assert dictRead is None


def test_fnClearState_removes():
    mockDocker = MockDockerConnection()
    dictState = {"bRunning": True}
    fnWriteState(mockDocker, "ctr1", dictState)
    fnClearState(mockDocker, "ctr1")
    dictRead = fdictReadState(mockDocker, "ctr1")
    assert dictRead is None
