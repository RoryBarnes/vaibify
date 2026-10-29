"""Tests for remaining uncovered functions in syncDispatcher and testGenerator."""

import json
import re

import pytest
from unittest.mock import MagicMock, patch

from vaibify.docker.dockerConnection import ExecResult
from vaibify.gui.syncDispatcher import (
    fnValidateServiceName,
    ftResultPushToOverleaf,
    ftResultPullFromOverleaf,
    ftResultArchiveToZenodo,
    ftResultPushToGithub,
    ftResultAddFileToGithub,
    ftResultGenerateLatex,
    fdictCheckConnectivity,
    fbValidateOverleafCredentials,
    fbValidateZenodoToken,
    fnValidateOverleafProjectId,
    ftResultGenerateDagSvg,
    ftResultArchiveProject,
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
    mockDocker.ftRunProgramWithStdin.return_value = ExecResult(
        iExitCode=iExitCode, sStdout=sOutput, sStderr="")
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
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="test-tok",
    ):
        iExit, sOut = ftResultPushToOverleaf(
            mockDocker, "cid", ["fig.pdf"],
            "projid123", "figures",
        )
    assert iExit == 0


def test_ftResultPushToOverleaf_annotated():
    mockDocker = _fMockDocker(0, "annotated")
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="test-tok",
    ):
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
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="test-tok",
    ):
        iExit, sOut = ftResultPullFromOverleaf(
            mockDocker, "cid", "projid123",
            ["main.tex"], "/workspace/tex",
        )
    assert iExit == 0


# -----------------------------------------------------------------------
# syncDispatcher: ftResultArchiveToZenodo
# -----------------------------------------------------------------------


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
    with patch(
        "vaibify.gui.syncDispatcher._fbHostGithubCredentialAvailable",
        return_value=True,
    ):
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


def _fpatchHostGithubCredential(bAvailable):
    """Pin the host-credential lane so the probe is host-independent.

    ``_fdictCheckGithub`` now probes the host credential as well as
    the container, so leaving it unpatched would make these assertions
    depend on whether the developer happens to be ``gh auth login``-ed.
    """
    return patch(
        "vaibify.gui.syncDispatcher._fbHostGithubCredentialAvailable",
        return_value=bAvailable,
    )


def test_fdictCheckGithub_success():
    mockDocker = _fMockDocker(0, "")
    with _fpatchHostGithubCredential(True):
        dictResult = _fdictCheckGithub(mockDocker, "cid", "/workspace/exampleRepo")
    assert dictResult["bConnected"] is True


def test_fdictCheckGithub_failure():
    mockDocker = _fMockDocker(1, "")
    with _fpatchHostGithubCredential(True):
        dictResult = _fdictCheckGithub(mockDocker, "cid", "/workspace/exampleRepo")
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


# -----------------------------------------------------------------------
# syncDispatcher: fbValidateOverleafCredentials
# -----------------------------------------------------------------------


def test_fbValidateOverleafCredentials_success():
    from unittest.mock import patch, MagicMock
    mockRun = MagicMock(returncode=0, stderr="")
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.gui.syncDispatcher.subprocess.run",
        return_value=mockRun,
    ):
        bResult, sStderr = fbValidateOverleafCredentials(
            None, "cid", "projid123"
        )
    assert bResult is True
    assert sStderr == ""


def test_fbValidateOverleafCredentials_failure():
    from unittest.mock import patch, MagicMock
    mockRun = MagicMock(
        returncode=1, stderr="fatal: authentication failed",
    )
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.gui.syncDispatcher.subprocess.run",
        return_value=mockRun,
    ):
        bResult, sStderr = fbValidateOverleafCredentials(
            None, "cid", "projid123"
        )
    assert bResult is False
    assert "authentication" in sStderr


# -----------------------------------------------------------------------
# syncDispatcher: fbValidateZenodoToken
# -----------------------------------------------------------------------


def test_fbValidateZenodoToken_rejects_invalid_service():
    mockDocker = _fMockDocker()
    with pytest.raises(ValueError):
        fbValidateZenodoToken(mockDocker, "cid", "production")


# -----------------------------------------------------------------------
# syncDispatcher: ftResultValidateZenodoToken failure classification.
#
# Every failure used to render as "check that the token has deposit
# scopes", which sent a researcher whose container lacked `requests`
# (a valid token, never tested) to their Zenodo settings page.
# -----------------------------------------------------------------------


def test_fsZenodoInstanceToService_maps_sandbox():
    from vaibify.gui.syncDispatcher import fsZenodoInstanceToService
    assert fsZenodoInstanceToService("sandbox") == "sandbox"


def test_fsZenodoInstanceToService_maps_production():
    from vaibify.gui.syncDispatcher import fsZenodoInstanceToService
    assert fsZenodoInstanceToService("production") == "zenodo"


def test_fsZenodoInstanceToService_rejects_service_key():
    from vaibify.gui.syncDispatcher import fsZenodoInstanceToService
    with pytest.raises(ValueError):
        fsZenodoInstanceToService("zenodo")


def test_fsZenodoTokenNameForInstance_sandbox():
    from vaibify.gui.syncDispatcher import (
        fsZenodoTokenNameForInstance,
    )
    assert (
        fsZenodoTokenNameForInstance("sandbox")
        == "zenodo_token_sandbox"
    )


def test_fsZenodoTokenNameForInstance_production():
    from vaibify.gui.syncDispatcher import (
        fsZenodoTokenNameForInstance,
    )
    assert (
        fsZenodoTokenNameForInstance("production")
        == "zenodo_token_production"
    )


def test_ftResultArchiveToZenodo_rejects_invalid_service():
    mockDocker = _fMockDocker()
    with pytest.raises(ValueError):
        ftResultArchiveToZenodo(
            mockDocker, "cid", "production", ["/a.txt"],
        )


def test_ftResultArchiveToZenodo_rejects_null_byte_in_path():
    mockDocker = _fMockDocker()
    with pytest.raises(ValueError):
        ftResultArchiveToZenodo(
            mockDocker, "cid", "sandbox", ["/bad\x00path.txt"],
        )


def test_fdictBuildApiMetadata_injects_title():
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "My Workflow",
        "listCreators": [{"sName": "Jane Doe"}],
    })
    assert dictApi["title"] == "My Workflow"


def test_fdictBuildApiMetadata_defaults_title_and_creator():
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({})
    assert dictApi["title"] == "Vaibify archive"
    assert dictApi["creators"] == [{"name": "Vaibify User"}]


def test_fdictBuildApiMetadata_accepts_tricky_strings():
    """Zenodo API shape survives quotes, backslashes, unicode."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "Rory's \"tricky\" Title",
        "sDescription": "contains \\ backslash",
        "listCreators": [{"sName": "O'Brien"}],
    })
    assert dictApi["title"] == "Rory's \"tricky\" Title"
    assert dictApi["creators"][0]["name"] == "O'Brien"


# ----------------------------------------------------------------------
# Phase 6 — error recovery / draft cleanup
# ----------------------------------------------------------------------


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
# -----------------------------------------------------------------------


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
    assert "python3 -m pytest tests/test_integrity.py" in dictResult["saCommands"]
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
            "sDirectory": "step1",
            "saDataCommands": ["python analyze.py"],
            "saOutputDataFiles": ["out.npy"],
        }],
    }
    dictResult = fdictGenerateTest(
        mockDocker, "cid", 0, dictWorkflow, {},
    )
    assert dictResult["sFilePath"].endswith("test_step01.py")
    assert "import pytest" in dictResult["sContent"]
    assert len(dictResult["saCommands"]) > 0


def test_fdictBuildApiMetadata_includes_creator_affiliation_and_orcid():
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{
            "sName": "Jane Doe",
            "sAffiliation": "UW",
            "sOrcid": "0000-0001-2345-6789",
        }],
    })
    assert dictApi["creators"] == [{
        "name": "Jane Doe",
        "affiliation": "UW",
        "orcid": "0000-0001-2345-6789",
    }]


def test_fdictBuildApiMetadata_includes_keywords_and_related_url():
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{"sName": "Jane Doe"}],
        "listKeywords": ["alpha", "beta"],
        "sRelatedGithubUrl": "https://github.com/u/r",
    })
    assert dictApi["keywords"] == ["alpha", "beta"]
    assert dictApi["related_identifiers"][0]["identifier"] == (
        "https://github.com/u/r"
    )


# ----------------------------------------------------------------------
# Non-Zenodo coverage gaps (syncDispatcher helpers)
# ----------------------------------------------------------------------


def test_fsFetchOverleafToken_reads_overleaf_keyring_slot():
    from vaibify.gui.syncDispatcher import _fsFetchOverleafToken
    with patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        return_value="tok123",
    ) as mockRetrieve:
        sToken = _fsFetchOverleafToken()
    assert sToken == "tok123"
    mockRetrieve.assert_called_once_with(
        "overleaf_token", "keyring",
    )


def test_fdictCheckHostKeyring_absent_token_returns_disconnected():
    from vaibify.gui.syncDispatcher import _fdictCheckHostKeyring
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=False,
    ):
        dictResult = _fdictCheckHostKeyring("overleaf_token")
    assert dictResult["bConnected"] is False
    assert "not found" in dictResult["sMessage"].lower()


def test_fdictCheckHostKeyring_present_token_returns_connected():
    from vaibify.gui.syncDispatcher import _fdictCheckHostKeyring
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ):
        dictResult = _fdictCheckHostKeyring("overleaf_token")
    assert dictResult["bConnected"] is True


def test_ftValidateOverleafOnHost_missing_token_skips_network():
    """No token stored => return early, never invoke git/askpass."""
    from vaibify.gui.syncDispatcher import _ftValidateOverleafOnHost
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=False,
    ), patch(
        "vaibify.gui.syncDispatcher.fsWriteAskpassScript",
    ) as mockAskpass:
        bPass, sDetail = _ftValidateOverleafOnHost("projid")
    assert bPass is False
    assert "No Overleaf token" in sDetail
    mockAskpass.assert_not_called()


def test_fnRemovePath_absent_file_silent():
    from vaibify.gui.syncDispatcher import _fnRemovePath
    # Path that definitely doesn't exist; must not raise
    _fnRemovePath("/nonexistent/path/forced/absent.tmp")


def test_fnRemovePath_removes_existing_file(tmp_path):
    from vaibify.gui.syncDispatcher import _fnRemovePath
    pathFile = tmp_path / "removable.tmp"
    pathFile.write_text("content")
    assert pathFile.exists()
    _fnRemovePath(str(pathFile))
    assert not pathFile.exists()


def test_fdictComputeContainerDigests_skips_lines_without_space():
    """Malformed output (no space between sha and path) is ignored."""
    from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
    mockDocker = _fMockDocker(
        0, "abc123 /good/path.txt\nmalformed-line-no-space\n",
    )
    dictDigests = fdictComputeContainerDigests(
        mockDocker, "cid", ["/good/path.txt", "/bad/path.txt"],
    )
    assert dictDigests == {"/good/path.txt": "abc123"}


def test_fdictComputeContainerDigests_empty_paths_short_circuits():
    from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
    mockDocker = MagicMock()
    dictDigests = fdictComputeContainerDigests(
        mockDocker, "cid", [],
    )
    assert dictDigests == {}
    mockDocker.ftResultExecuteCommand.assert_not_called()


def test_fdictComputeContainerDigests_non_zero_exit_returns_empty():
    from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
    mockDocker = _fMockDocker(1, "error")
    dictDigests = fdictComputeContainerDigests(
        mockDocker, "cid", ["/a.txt"],
    )
    assert dictDigests == {}


def test_fdictDiffOverleafPush_with_docker_uses_container_digests():
    """When docker context is provided, scan inside the container."""
    from vaibify.gui.syncDispatcher import fdictDiffOverleafPush
    mockDocker = _fMockDocker(0, "abc /workspace/fig.pdf\n")
    with patch(
        "vaibify.reproducibility.overleafMirror.fdictDiffAgainstMirror",
        return_value={"new": [], "overwrite": [], "unchanged": []},
    ) as mockMirror:
        dictDiff = fdictDiffOverleafPush(
            "projid", ["/workspace/fig.pdf"], "figures",
            connectionDocker=mockDocker, sContainerId="cid",
        )
    assert "new" in dictDiff
    dictDigestsArg = mockMirror.call_args[0][1]
    assert dictDigestsArg == {"/workspace/fig.pdf": "abc"}


def test_fdictDiffOverleafPush_without_docker_uses_local_digests(tmp_path):
    """No docker context => hash from the host filesystem."""
    from vaibify.gui.syncDispatcher import fdictDiffOverleafPush
    pathFile = tmp_path / "fig.pdf"
    pathFile.write_bytes(b"fake-pdf-bytes")
    with patch(
        "vaibify.reproducibility.overleafMirror.fsComputeBlobSha",
        return_value="host-sha",
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictDiffAgainstMirror",
        return_value={"new": [], "overwrite": [], "unchanged": []},
    ) as mockMirror:
        fdictDiffOverleafPush(
            "projid", [str(pathFile)], "figures",
        )
    dictDigestsArg = mockMirror.call_args[0][1]
    assert dictDigestsArg == {str(pathFile): "host-sha"}


def test_flistBuildDagEdges_merges_cached_deps():
    """_flistBuildDagEdges must union cached deps into the edge list."""
    from vaibify.gui.syncDispatcher import _flistBuildDagEdges
    dictWorkflow = {"listSteps": [
        {"sName": "A", "saDataCommands": [],
         "saPlotCommands": [], "saOutputDataFiles": [], "saPlotFiles": []},
        {"sName": "B", "saDataCommands": [],
         "saPlotCommands": [], "saOutputDataFiles": [], "saPlotFiles": []},
        {"sName": "C", "saDataCommands": [],
         "saPlotCommands": [], "saOutputDataFiles": [], "saPlotFiles": []},
    ]}
    # Cached: Step1 (0) -> Step3 (2)
    dictCached = {0: {2}}
    listLines = _flistBuildDagEdges(dictWorkflow, dictCached)
    assert any("step1 -> step3" in sLine for sLine in listLines)


# -----------------------------------------------------------------------
# syncDispatcher: fdictClassifyError — a local file problem is local.
# -----------------------------------------------------------------------


@pytest.mark.falsification
def test_a_local_file_error_never_classifies_as_remote_not_found():
    """The archive script's marker outranks the not-found patterns.

    The payload deliberately contains BOTH the marker and the
    "not found" / "no such" text a repr'd FileNotFoundError carries,
    because that collision is the live failure: a phantom local path
    was reported as a failed remote lookup and the researcher was told
    to check their DOI.

    Kills: dropping the local-file-error check from
    ``fdictClassifyError``, which returns the misclassification.
    """
    from vaibify.gui.syncDispatcher import fdictClassifyError
    sOutput = (
        "LOCAL-FILE-ERROR (not a Zenodo problem): FileNotFoundError("
        "\"File not found: '/workspace/proj/tests/test_a.py'\") "
        "| orphan draft 42 deleted"
    )
    dictResult = fdictClassifyError(1, sOutput)
    assert dictResult["sErrorType"] == "localFileMissing", dictResult


def test_a_real_remote_not_found_still_classifies_as_not_found():
    """The complement: the marker check must not swallow real 404s."""
    from vaibify.gui.syncDispatcher import fdictClassifyError
    dictResult = fdictClassifyError(
        1, "HTTP Zenodo record '99' not found. (404)",
    )
    assert dictResult["sErrorType"] == "notFound", dictResult
