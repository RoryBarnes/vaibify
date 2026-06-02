"""Tests for pipelineState with mock Docker connection."""

import json

from vaibify.gui.pipelineState import (
    fnWriteState,
    fnUpdateState,
    fnRecordStepResult,
    fdictReadState,
    fnClearState,
    S_STATE_PATH,
    S_STATE_PATH_TEMP,
)


class MockDockerConnection:
    """Mock Docker connection that models temp-file + rename writes."""

    def __init__(self):
        self.dictFiles = {}
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        if sCommand.startswith("mv "):
            return self._ftHandleRename(sContainerId, sCommand)
        if sCommand.startswith("cat "):
            return self._ftHandleRead(sContainerId, sCommand)
        if "rm -f" in sCommand:
            self._fnHandleRemove(sContainerId, sCommand)
            return (0, "")
        return (1, "")

    def _ftHandleRename(self, sContainerId, sCommand):
        listParts = sCommand.split()
        sSrc, sDst = listParts[1], listParts[2]
        sKey = (sContainerId, sSrc)
        if sKey not in self.dictFiles:
            return (1, "")
        self.dictFiles[(sContainerId, sDst)] = self.dictFiles.pop(sKey)
        return (0, "")

    def _ftHandleRead(self, sContainerId, sCommand):
        sPath = sCommand.split()[1]
        sKey = (sContainerId, sPath)
        if sKey not in self.dictFiles:
            return (1, "")
        return (0, self.dictFiles[sKey].decode("utf-8"))

    def _fnHandleRemove(self, sContainerId, sCommand):
        for sToken in sCommand.split():
            if sToken.startswith("/"):
                self.dictFiles.pop((sContainerId, sToken), None)


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
