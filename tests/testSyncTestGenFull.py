"""Tests for remaining uncovered functions in syncDispatcher and testGenerator."""

import json
import re

import pytest
from unittest.mock import MagicMock, patch

from vaibify.gui.syncDispatcher import (
    fnValidateServiceName,
    ftResultPushToOverleaf,
    ftResultPullFromOverleaf,
    ftResultArchiveToZenodo,
    ftResultPushToGithub,
    ftResultPushScriptsToGithub,
    ftResultAddFileToGithub,
    ftResultGenerateLatex,
    fdictCheckConnectivity,
    fnStoreCredentialInContainer,
    fbValidateOverleafCredentials,
    fbValidateZenodoToken,
    fnValidateOverleafProjectId,
    ftResultGenerateDagSvg,
    ftResultArchiveProject,
    fbStepInputsUnchanged,
    fdictComputeInputHashes,
    _fdictCheckGithub,
    _fdictCheckKeyring,
)
from vaibify.gui.testGenerator import (
    fbContainerHasClaude,
    fsReadFileFromContainer,
    fsPreviewDataFile,
    _fsPreviewNpy,
    _fsPreviewText,
    ftResultGenerateViaClaude,
    fsGenerateViaApi,
    fsTestFilePath,
    _fdictWriteTestFile,
    fdictGenerateTest,
    _fsInvokeLlm,
    _fsExtractScriptFromCommand,
)


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a mock Docker connection."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput
    )
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"content"
    return mockDocker


# -----------------------------------------------------------------------
# syncDispatcher: fnValidateServiceName
# -----------------------------------------------------------------------


def test_fnValidateServiceName_valid():
    fnValidateServiceName("github")
    fnValidateServiceName("overleaf")
    fnValidateServiceName("zenodo")


def test_fnValidateServiceName_invalid():
    with pytest.raises(ValueError):
        fnValidateServiceName("dropbox")


# -----------------------------------------------------------------------
# syncDispatcher: fnValidateOverleafProjectId
# -----------------------------------------------------------------------


def test_fnValidateOverleafProjectId_valid():
    fnValidateOverleafProjectId("abc123")
    fnValidateOverleafProjectId("proj-test_id")


def test_fnValidateOverleafProjectId_invalid():
    with pytest.raises(ValueError):
        fnValidateOverleafProjectId("abc;rm -rf /")


# -----------------------------------------------------------------------
# syncDispatcher: ftResultPushToOverleaf
# -----------------------------------------------------------------------


def test_ftResultPushToOverleaf_plain():
    mockDocker = _fMockDocker(0, "pushed")
    iExit, sOut = ftResultPushToOverleaf(
        mockDocker, "cid", ["fig.pdf"],
        "projid123", "figures",
    )
    assert iExit == 0


def test_ftResultPushToOverleaf_annotated():
    mockDocker = _fMockDocker(0, "annotated")
    iExit, sOut = ftResultPushToOverleaf(
        mockDocker, "cid", ["fig.pdf"],
        "projid123", "figures",
        dictWorkflow={"sWorkflowName": "T"},
        sGithubBaseUrl="https://github.com/t",
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultPullFromOverleaf
# -----------------------------------------------------------------------


def test_ftResultPullFromOverleaf():
    mockDocker = _fMockDocker(0, "pulled")
    iExit, sOut = ftResultPullFromOverleaf(
        mockDocker, "cid", "projid123",
        ["main.tex"], "/workspace/tex",
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultArchiveToZenodo
# -----------------------------------------------------------------------


def test_ftResultArchiveToZenodo():
    mockDocker = _fMockDocker(0, "archived")
    iExit, sOut = ftResultArchiveToZenodo(
        mockDocker, "cid", "zenodo", ["data.npy"],
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultPushToGithub
# -----------------------------------------------------------------------


def test_ftResultPushToGithub():
    mockDocker = _fMockDocker(0, "abc1234")
    iExit, sOut = ftResultPushToGithub(
        mockDocker, "cid",
        ["script.py"], "commit msg", "/workspace",
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultAddFileToGithub
# -----------------------------------------------------------------------


def test_ftResultAddFileToGithub():
    mockDocker = _fMockDocker(0, "abc1234")
    iExit, sOut = ftResultAddFileToGithub(
        mockDocker, "cid",
        "data.npy", "add data", "/workspace",
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultGenerateLatex
# -----------------------------------------------------------------------


def test_ftResultGenerateLatex():
    mockDocker = _fMockDocker(0, "written")
    iExit, sOut = ftResultGenerateLatex(
        mockDocker, "cid",
        ["fig1.pdf", "fig2.pdf"], "/output/includes.tex",
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: fdictCheckConnectivity
# -----------------------------------------------------------------------


def test_fdictCheckConnectivity_github():
    mockDocker = _fMockDocker(0, "")
    dictResult = fdictCheckConnectivity(
        mockDocker, "cid", "github"
    )
    assert "bConnected" in dictResult


def test_fdictCheckConnectivity_overleaf():
    mockDocker = _fMockDocker(0, "ok\n")
    dictResult = fdictCheckConnectivity(
        mockDocker, "cid", "overleaf"
    )
    assert "bConnected" in dictResult


def test_fdictCheckConnectivity_zenodo():
    mockDocker = _fMockDocker(0, "ok\n")
    dictResult = fdictCheckConnectivity(
        mockDocker, "cid", "zenodo"
    )
    assert "bConnected" in dictResult


# -----------------------------------------------------------------------
# syncDispatcher: _fdictCheckGithub
# -----------------------------------------------------------------------


def test_fdictCheckGithub_success():
    mockDocker = _fMockDocker(0, "")
    dictResult = _fdictCheckGithub(mockDocker, "cid")
    assert dictResult["bConnected"] is True


def test_fdictCheckGithub_failure():
    mockDocker = _fMockDocker(1, "")
    dictResult = _fdictCheckGithub(mockDocker, "cid")
    assert dictResult["bConnected"] is False


# -----------------------------------------------------------------------
# syncDispatcher: _fdictCheckKeyring
# -----------------------------------------------------------------------


def test_fdictCheckKeyring_valid_token():
    mockDocker = _fMockDocker(0, "ok\n")
    dictResult = _fdictCheckKeyring(
        mockDocker, "cid", "overleaf_token"
    )
    assert dictResult["bConnected"] is True


def test_fdictCheckKeyring_missing_token():
    mockDocker = _fMockDocker(0, "missing\n")
    dictResult = _fdictCheckKeyring(
        mockDocker, "cid", "zenodo_token"
    )
    assert dictResult["bConnected"] is False


def test_fdictCheckKeyring_invalid_name():
    mockDocker = _fMockDocker()
    with pytest.raises(ValueError):
        _fdictCheckKeyring(mockDocker, "cid", "bad_token")


# -----------------------------------------------------------------------
# syncDispatcher: fnStoreCredentialInContainer
# -----------------------------------------------------------------------


def test_fnStoreCredentialInContainer_stores():
    mockDocker = _fMockDocker(0, "")
    fnStoreCredentialInContainer(
        mockDocker, "cid", "overleaf_token", "mytoken123"
    )
    mockDocker.fnWriteFile.assert_called_once()
    mockDocker.ftResultExecuteCommand.assert_called()


def test_fnStoreCredentialInContainer_invalid():
    mockDocker = _fMockDocker()
    with pytest.raises(ValueError):
        fnStoreCredentialInContainer(
            mockDocker, "cid", "invalid_name", "value"
        )


# -----------------------------------------------------------------------
# syncDispatcher: fbValidateOverleafCredentials
# -----------------------------------------------------------------------


def test_fbValidateOverleafCredentials_success():
    mockDocker = _fMockDocker(0, "")
    bResult = fbValidateOverleafCredentials(
        mockDocker, "cid", "projid123"
    )
    assert bResult is True


def test_fbValidateOverleafCredentials_failure():
    mockDocker = _fMockDocker(1, "")
    bResult = fbValidateOverleafCredentials(
        mockDocker, "cid", "projid123"
    )
    assert bResult is False


# -----------------------------------------------------------------------
# syncDispatcher: fbValidateZenodoToken
# -----------------------------------------------------------------------


def test_fbValidateZenodoToken_success():
    mockDocker = _fMockDocker(0, "ok\n")
    bResult = fbValidateZenodoToken(mockDocker, "cid")
    assert bResult is True


def test_fbValidateZenodoToken_failure():
    mockDocker = _fMockDocker(1, "error")
    bResult = fbValidateZenodoToken(mockDocker, "cid")
    assert bResult is False


# -----------------------------------------------------------------------
# syncDispatcher: ftResultGenerateDagSvg
# -----------------------------------------------------------------------


def test_ftResultGenerateDagSvg_success():
    mockDocker = MagicMock()
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    mockDocker.fbaFetchFile.return_value = b"<svg></svg>"
    dictWorkflow = {"listSteps": [{"sName": "A"}]}
    iExit, result = ftResultGenerateDagSvg(
        mockDocker, "cid", dictWorkflow
    )
    assert iExit == 0
    assert b"<svg>" in result


def test_ftResultGenerateDagSvg_failure():
    mockDocker = MagicMock()
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        1, "dot not found"
    )
    dictWorkflow = {"listSteps": []}
    iExit, result = ftResultGenerateDagSvg(
        mockDocker, "cid", dictWorkflow
    )
    assert iExit == 1


# -----------------------------------------------------------------------
# syncDispatcher: fbStepInputsUnchanged
# -----------------------------------------------------------------------


def test_fbStepInputsUnchanged_no_hashes():
    mockDocker = _fMockDocker()
    dictStep = {"dictRunStats": {}}
    bResult = fbStepInputsUnchanged(
        mockDocker, "cid", dictStep, 1
    )
    assert bResult is False


def test_fbStepInputsUnchanged_matching():
    mockDocker = _fMockDocker(0, "abc123\n")
    dictStep = {
        "sDirectory": "/workspace",
        "saDataCommands": ["python script.py"],
        "dictRunStats": {
            "dictInputHashes": {
                "/workspace/script.py": "abc123",
            },
        },
    }
    bResult = fbStepInputsUnchanged(
        mockDocker, "cid", dictStep, 1
    )
    assert bResult is True


def test_fbStepInputsUnchanged_changed():
    mockDocker = _fMockDocker(0, "xyz789\n")
    dictStep = {
        "sDirectory": "/workspace",
        "saDataCommands": ["python script.py"],
        "dictRunStats": {
            "dictInputHashes": {
                "/workspace/script.py": "abc123",
            },
        },
    }
    bResult = fbStepInputsUnchanged(
        mockDocker, "cid", dictStep, 1
    )
    assert bResult is False


# -----------------------------------------------------------------------
# syncDispatcher: fdictComputeInputHashes
# -----------------------------------------------------------------------


def test_fdictComputeInputHashes_computes():
    mockDocker = _fMockDocker(0, "hash123\n")
    dictStep = {
        "sDirectory": "/workspace",
        "saDataCommands": ["python script.py"],
    }
    dictResult = fdictComputeInputHashes(
        mockDocker, "cid", dictStep
    )
    assert "/workspace/script.py" in dictResult
    assert dictResult["/workspace/script.py"] == "hash123"


def test_fdictComputeInputHashes_failure():
    mockDocker = _fMockDocker(1, "")
    dictStep = {
        "sDirectory": "/workspace",
        "saDataCommands": ["python script.py"],
    }
    dictResult = fdictComputeInputHashes(
        mockDocker, "cid", dictStep
    )
    assert len(dictResult) == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultPushScriptsToGithub
# -----------------------------------------------------------------------


def test_ftResultPushScriptsToGithub_no_scripts():
    mockDocker = _fMockDocker()
    dictWorkflow = {"listSteps": []}
    iExit, sOut = ftResultPushScriptsToGithub(
        mockDocker, "cid", dictWorkflow,
        "commit msg", "/workspace",
    )
    assert iExit == 1
    assert "No scripts" in sOut


# -----------------------------------------------------------------------
# syncDispatcher: ftResultArchiveProject
# -----------------------------------------------------------------------


def test_ftResultArchiveProject():
    mockDocker = _fMockDocker(0, "Published: 12345")
    dictWorkflow = {
        "sWorkflowName": "Test",
        "listSteps": [],
    }
    iExit, sOut = ftResultArchiveProject(
        mockDocker, "cid", dictWorkflow
    )
    assert iExit == 0


# -----------------------------------------------------------------------
# testGenerator: fbContainerHasClaude
# -----------------------------------------------------------------------


def test_fbContainerHasClaude_yes():
    mockDocker = _fMockDocker(0, "/usr/bin/claude")
    assert fbContainerHasClaude(mockDocker, "cid") is True


def test_fbContainerHasClaude_no():
    mockDocker = _fMockDocker(1, "")
    assert fbContainerHasClaude(mockDocker, "cid") is False


# -----------------------------------------------------------------------
# testGenerator: fsReadFileFromContainer
# -----------------------------------------------------------------------


def test_fsReadFileFromContainer_success():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"hello world"
    sResult = fsReadFileFromContainer(
        mockDocker, "cid", "/file.py"
    )
    assert sResult == "hello world"


def test_fsReadFileFromContainer_failure():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = Exception("nope")
    sResult = fsReadFileFromContainer(
        mockDocker, "cid", "/missing.py"
    )
    assert sResult == ""


# -----------------------------------------------------------------------
# testGenerator: fsPreviewDataFile
# -----------------------------------------------------------------------


def test_fsPreviewDataFile_npy():
    mockDocker = _fMockDocker(0, "shape=(10,) dtype=float64")
    sResult = fsPreviewDataFile(
        mockDocker, "cid", "data.npy", "/workspace"
    )
    assert "shape=" in sResult


def test_fsPreviewDataFile_text():
    mockDocker = _fMockDocker(0, "col1,col2\n1,2\n")
    sResult = fsPreviewDataFile(
        mockDocker, "cid", "data.csv", "/workspace"
    )
    assert "col1" in sResult


def test_fsPreviewDataFile_hdf5():
    mockDocker = _fMockDocker(
        0, "dataset:/data shape=(100,) dtype=float64 first=1.0 last=9.0"
    )
    sResult = fsPreviewDataFile(
        mockDocker, "cid", "archive.h5", "/workspace"
    )
    assert "dataset:" in sResult


def test_fsPreviewDataFile_hdf5_extension():
    mockDocker = _fMockDocker(0, "dataset:/group shape=(5,)")
    sResult = fsPreviewDataFile(
        mockDocker, "cid", "output.hdf5", "/workspace"
    )
    assert "dataset:" in sResult


# -----------------------------------------------------------------------
# testGenerator: ftResultGenerateViaClaude
# -----------------------------------------------------------------------


def test_ftResultGenerateViaClaude():
    mockDocker = _fMockDocker(0, "import pytest\n")
    iExit, sOut = ftResultGenerateViaClaude(
        mockDocker, "cid", "generate tests"
    )
    assert iExit == 0
    assert "pytest" in sOut


# -----------------------------------------------------------------------
# testGenerator: fsTestFilePath
# -----------------------------------------------------------------------


def test_fsTestFilePath_format():
    sResult = fsTestFilePath("/workspace/step1", 0)
    assert sResult == "/workspace/step1/test_step01.py"


def test_fsTestFilePath_higher_index():
    sResult = fsTestFilePath("/workspace/step2", 4)
    assert sResult == "/workspace/step2/test_step05.py"


# -----------------------------------------------------------------------
# testGenerator: _fdictWriteTestFile
# -----------------------------------------------------------------------


def test_fdictWriteTestFile_writes():
    mockDocker = _fMockDocker()
    dictResult = _fdictWriteTestFile(
        mockDocker, "cid",
        "import pytest\ndef test_a():\n    pass",
        "/workspace/tests/test_integrity.py",
    )
    assert dictResult["sFilePath"] == "/workspace/tests/test_integrity.py"
    assert "pytest" in dictResult["sContent"]
    assert "pytest tests/test_integrity.py" in dictResult["saCommands"]
    mockDocker.fnWriteFile.assert_called_once()


# -----------------------------------------------------------------------
# testGenerator: _fsExtractScriptFromCommand
# -----------------------------------------------------------------------


def test_fsExtractScriptFromCommand_python():
    sResult = _fsExtractScriptFromCommand("python analyze.py")
    assert sResult == "analyze.py"


def test_fsExtractScriptFromCommand_none():
    sResult = _fsExtractScriptFromCommand("echo hello")
    assert sResult is None


# -----------------------------------------------------------------------
# testGenerator: _fsInvokeLlm — via Claude
# -----------------------------------------------------------------------


def test_fsInvokeLlm_claude_success():
    mockDocker = _fMockDocker(0, "import pytest\n")
    sResult = _fsInvokeLlm(
        mockDocker, "cid", "prompt", False, None,
    )
    assert "pytest" in sResult


def test_fsInvokeLlm_claude_failure():
    mockDocker = _fMockDocker(1, "not logged in")
    with pytest.raises(RuntimeError) as excInfo:
        _fsInvokeLlm(
            mockDocker, "cid", "prompt", False, None,
        )
    assert "not authenticated" in str(excInfo.value)


# -----------------------------------------------------------------------
# testGenerator: fdictGenerateTest
# -----------------------------------------------------------------------


def test_fdictGenerateTest_via_claude():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"import numpy"
    mockDocker.ftResultExecuteCommand.return_value = (
        0, "```python\nimport pytest\ndef test_a(): pass\n```"
    )
    mockDocker.fnWriteFile = MagicMock()
    dictWorkflow = {
        "listSteps": [{
            "sDirectory": "/workspace/step1",
            "saDataCommands": ["python analyze.py"],
            "saDataFiles": ["out.npy"],
        }],
    }
    dictResult = fdictGenerateTest(
        mockDocker, "cid", 0, dictWorkflow, {},
    )
    assert dictResult["sFilePath"].endswith("test_step01.py")
    assert "import pytest" in dictResult["sContent"]
    assert len(dictResult["saCommands"]) > 0
