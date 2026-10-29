"""Tests targeting uncovered lines in vaibify.gui.syncDispatcher.

Covers:
- Line 386: fdictCheckConnectivity unreachable "Unknown service" fallback
- Lines 645-667: ftResultExportDag (new function)
"""

import pytest
from unittest.mock import MagicMock, patch

from vaibify.docker.dockerConnection import ExecResult
from vaibify.gui.syncDispatcher import (
    DICT_DAG_MEDIA_TYPES,
    _flistBuildDagEdges,
    fdictCheckConnectivity,
    fsBuildDagDot,
    ftResultExportDag,
)


# ── Helpers ──────────────────────────────────────────────────────


def _fmockDocker(iExitCode=0, sOutput="", baContent=b"<svg/>"):
    """Return a mock Docker connection with configurable results."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput)
    mockDocker.ftRunProgramWithStdin.return_value = ExecResult(
        iExitCode=iExitCode, sStdout=sOutput, sStderr="")
    mockDocker.fbaFetchFile.return_value = baContent
    mockDocker.fnWriteFile.return_value = None
    return mockDocker


DICT_STEP_WITH_SCRIPTS = {
    "sName": "Run Simulation",
    "sDirectory": "step01",
    "saDataCommands": ["python run.py"],
    "saPlotCommands": [],
    "saTestCommands": [],
    "saDependencies": [],
    "saOutputDataFiles": [],
    "saPlotFiles": [],
}

DICT_STEP_EMPTY = {
    "sName": "Empty Step",
    "sDirectory": "step02",
    "saDataCommands": [],
    "saPlotCommands": [],
    "saTestCommands": [],
    "saDependencies": [],
    "saOutputDataFiles": [],
    "saPlotFiles": [],
}


# ── fdictCheckConnectivity: line 386 (unreachable fallback) ──────


class TestCheckConnectivityUnknownService:
    """Line 386 is unreachable because fnValidateServiceName guards it.

    We bypass the guard by patching fnValidateServiceName to be a no-op.
    """

    @patch(
        "vaibify.gui.syncDispatcher.fnValidateServiceName",
        side_effect=lambda s: None,
    )
    def test_unknown_service_returns_not_connected(self, _mockValidate):
        mockDocker = _fmockDocker()
        dictResult = fdictCheckConnectivity(
            mockDocker, "cid123", "dropbox")
        assert dictResult["bConnected"] is False
        assert "Unknown" in dictResult["sMessage"]


# ── ftResultExportDag: lines 645-667 ────────────────────────────


class TestExportDag:
    """Tests for the new ftResultExportDag function."""

    def test_unsupported_format_returns_error(self):
        mockDocker = _fmockDocker()
        dictWorkflow = {"listSteps": []}
        iExit, sOut = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "bmp")
        assert iExit == 1
        assert "Unsupported" in sOut
        mockDocker.ftResultExecuteCommand.assert_not_called()

    def test_svg_format_success(self):
        baSvgContent = b"<svg>test</svg>"
        mockDocker = _fmockDocker(
            iExitCode=0, baContent=baSvgContent)
        dictWorkflow = {"listSteps": [
            {"sName": "Step One"},
        ]}
        iExit, baResult = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "svg")
        assert iExit == 0
        assert baResult == baSvgContent
        mockDocker.fnWriteFile.assert_called_once()
        sWrittenDot = mockDocker.fnWriteFile.call_args[0][2]
        assert b"digraph" in sWrittenDot
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "dot -Tsvg" in sCommand

    def test_png_format_success(self):
        baPngContent = b"\x89PNG"
        mockDocker = _fmockDocker(
            iExitCode=0, baContent=baPngContent)
        dictWorkflow = {"listSteps": []}
        iExit, baResult = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "png")
        assert iExit == 0
        assert baResult == baPngContent
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "dot -Tpng" in sCommand

    def test_pdf_format_success(self):
        baPdfContent = b"%PDF-1.4"
        mockDocker = _fmockDocker(
            iExitCode=0, baContent=baPdfContent)
        dictWorkflow = {"listSteps": []}
        iExit, baResult = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "pdf")
        assert iExit == 0
        assert baResult == baPdfContent

    def test_dot_conversion_failure(self):
        mockDocker = _fmockDocker(
            iExitCode=1, sOutput="dot: command not found")
        dictWorkflow = {"listSteps": []}
        iExit, sOut = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "svg")
        assert iExit == 1
        assert "dot: command not found" in sOut
        mockDocker.fbaFetchFile.assert_not_called()

    def test_format_normalized_with_leading_dot(self):
        mockDocker = _fmockDocker(iExitCode=0, baContent=b"<svg/>")
        dictWorkflow = {"listSteps": []}
        iExit, _ = ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, ".SVG")
        assert iExit == 0
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "dot -Tsvg" in sCommand

    def test_persist_path_includes_format(self):
        mockDocker = _fmockDocker(iExitCode=0, baContent=b"data")
        dictWorkflow = {"listSteps": []}
        ftResultExportDag(
            mockDocker, "cid123", dictWorkflow, "png")
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "/workspace/.vaibify/dag.png" in sCommand


class TestDagMediaTypes:
    """Verify the DICT_DAG_MEDIA_TYPES constant."""

    def test_svg_media_type(self):
        assert DICT_DAG_MEDIA_TYPES["svg"] == "image/svg+xml"

    def test_png_media_type(self):
        assert DICT_DAG_MEDIA_TYPES["png"] == "image/png"

    def test_pdf_media_type(self):
        assert DICT_DAG_MEDIA_TYPES["pdf"] == "application/pdf"


# ── _flistBuildDagEdges: implicit deps from shared directories ────


class TestBuildDagEdgesIncludesImplicitDeps:
    """DAG edges should include implicit directory-overlap dependencies."""

    def test_flistBuildDagEdges_includes_implicit_deps(self):
        dictWorkflow = {
            "listSteps": [
                {
                    "sName": "Produce",
                    "sDirectory": "/workspace/shared/sub",
                    "saOutputDataFiles": ["output.csv"],
                    "saPlotFiles": [],
                    "saDataCommands": [],
                    "saPlotCommands": [],
                },
                {
                    "sName": "Consume",
                    "sDirectory": "/workspace/shared",
                    "saOutputDataFiles": [],
                    "saPlotFiles": [],
                    "saDataCommands": [],
                    "saPlotCommands": [],
                },
            ],
        }
        listEdges = _flistBuildDagEdges(dictWorkflow)
        assert any("step1 -> step2" in sEdge for sEdge in listEdges)


# ── fnStoreCredentialInContainer: exit-code propagation ─────────


# ── _fdictCheckKeyring: backend health vs token presence ────────


class TestCheckKeyringBackendHealth:
    """Distinguish backend-unavailable from token-missing."""

    def _fmockDockerForScripts(self, dictScriptResults):
        """Docker mock that returns different results per command."""
        listResults = []

        def _fnExecute(sContainerId, sCommand):
            if "get_keyring" in sCommand:
                return dictScriptResults["backend"]
            if "get_password" in sCommand:
                return dictScriptResults["token"]
            return (0, "")

        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.side_effect = _fnExecute
        return mockDocker

    def test_fail_keyring_backend_reports_unavailable(self):
        from vaibify.gui.syncDispatcher import _fdictCheckKeyring
        mockDocker = self._fmockDockerForScripts({
            "backend": (0, "keyring.backends.fail Keyring"),
            "token": (0, "missing"),
        })
        dictResult = _fdictCheckKeyring(
            mockDocker, "cid", "overleaf_token",
        )
        assert dictResult["bConnected"] is False
        assert "Rebuild" in dictResult["sMessage"]

    def test_healthy_backend_but_missing_token(self):
        from vaibify.gui.syncDispatcher import _fdictCheckKeyring
        mockDocker = self._fmockDockerForScripts({
            "backend": (
                0, "keyrings.alt.file EncryptedKeyring",
            ),
            "token": (0, "missing"),
        })
        dictResult = _fdictCheckKeyring(
            mockDocker, "cid", "overleaf_token",
        )
        assert dictResult["bConnected"] is False
        assert dictResult["sMessage"] == "Token not found"

    def test_healthy_backend_and_present_token(self):
        from vaibify.gui.syncDispatcher import _fdictCheckKeyring
        mockDocker = self._fmockDockerForScripts({
            "backend": (
                0, "keyrings.alt.file EncryptedKeyring",
            ),
            "token": (0, "ok"),
        })
        dictResult = _fdictCheckKeyring(
            mockDocker, "cid", "overleaf_token",
        )
        assert dictResult["bConnected"] is True
        assert dictResult["sMessage"] == "Connected"

    def test_backend_probe_command_failure_reports_unavailable(self):
        from vaibify.gui.syncDispatcher import _fdictCheckKeyring
        mockDocker = self._fmockDockerForScripts({
            "backend": (1, "ImportError: No module named keyring"),
            "token": (0, "missing"),
        })
        dictResult = _fdictCheckKeyring(
            mockDocker, "cid", "overleaf_token",
        )
        assert dictResult["bConnected"] is False
        assert "Rebuild" in dictResult["sMessage"]

    def test_invalid_token_name_rejected(self):
        from vaibify.gui.syncDispatcher import _fdictCheckKeyring
        mockDocker = MagicMock()
        with pytest.raises(ValueError):
            _fdictCheckKeyring(mockDocker, "cid", "bogus_token")


# ── fnDeleteCredentialFromContainer ─────────────────────────────


class TestDeleteCredentialFromContainer:
    """Delete a stored credential with tolerant error handling."""

    def test_successful_delete_returns_none(self):
        from vaibify.gui.syncDispatcher import (
            fnDeleteCredentialFromContainer,
        )
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "")
        result = fnDeleteCredentialFromContainer(
            mockDocker, "cid", "overleaf_token",
        )
        assert result is None
        sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert "delete_password" in sCommand
        assert "overleaf_token" in sCommand

    def test_missing_credential_suppressed(self):
        from vaibify.gui.syncDispatcher import (
            fnDeleteCredentialFromContainer,
        )
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            1,
            "keyring.errors.PasswordDeleteError: "
            "Password not found",
        )
        fnDeleteCredentialFromContainer(
            mockDocker, "cid", "overleaf_token",
        )

    def test_other_failure_reraises(self):
        from vaibify.gui.syncDispatcher import (
            fnDeleteCredentialFromContainer,
        )
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            1, "PermissionError: read-only filesystem"
        )
        with pytest.raises(RuntimeError) as excInfo:
            fnDeleteCredentialFromContainer(
                mockDocker, "cid", "overleaf_token",
            )
        assert "PermissionError" in str(excInfo.value)

    def test_invalid_token_name_rejected(self):
        from vaibify.gui.syncDispatcher import (
            fnDeleteCredentialFromContainer,
        )
        mockDocker = MagicMock()
        with pytest.raises(ValueError):
            fnDeleteCredentialFromContainer(
                mockDocker, "cid", "bogus_token",
            )


# ── Overleaf dispatch: CLI invocation shape ─────────────────────


# ── Overleaf mirror dispatch ─────────────────────────────────────


class TestRefreshOverleafMirror:

    def test_missing_token_returns_false(self):
        from vaibify.gui.syncDispatcher import (
            ftRefreshOverleafMirror,
        )
        with patch(
            "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
            return_value="",
        ):
            bSuccess, result = ftRefreshOverleafMirror("projid123")
        assert bSuccess is False
        assert "No Overleaf token" in result

    def test_successful_refresh_returns_payload(self):
        from vaibify.gui.syncDispatcher import (
            ftRefreshOverleafMirror,
        )
        with patch(
            "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
            return_value="tok",
        ), patch(
            "vaibify.reproducibility.overleafMirror.fbRefreshMirror",
            return_value={
                "sHeadSha": "abc123",
                "iFileCount": 5,
                "sRefreshedAt": "2026-04-17T00:00:00Z",
            },
        ):
            bSuccess, result = ftRefreshOverleafMirror("projid123")
        assert bSuccess is True
        assert result["sHeadSha"] == "abc123"
        assert result["iFileCount"] == 5

    def test_runtime_error_propagated_as_false(self):
        from vaibify.gui.syncDispatcher import (
            ftRefreshOverleafMirror,
        )
        with patch(
            "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
            return_value="tok",
        ), patch(
            "vaibify.reproducibility.overleafMirror.fbRefreshMirror",
            side_effect=RuntimeError("Mirror clone failed: auth"),
        ):
            bSuccess, result = ftRefreshOverleafMirror("projid123")
        assert bSuccess is False
        assert "Mirror clone failed" in result

    def test_bad_project_id_rejected(self):
        from vaibify.gui.syncDispatcher import (
            ftRefreshOverleafMirror,
        )
        with pytest.raises(ValueError):
            ftRefreshOverleafMirror("../evil")


class TestListOverleafTree:

    def test_delegates_to_mirror_module(self):
        from vaibify.gui.syncDispatcher import flistListOverleafTree
        with patch(
            "vaibify.reproducibility.overleafMirror.flistListMirrorTree",
            return_value=[{"sPath": "a.tex"}],
        ):
            listEntries = flistListOverleafTree("projid123")
        assert listEntries == [{"sPath": "a.tex"}]

    def test_bad_project_id_rejected(self):
        from vaibify.gui.syncDispatcher import flistListOverleafTree
        with pytest.raises(ValueError):
            flistListOverleafTree("/evil")


class TestDiffOverleafPush:

    def test_returns_diff_dict(self, tmp_path):
        from vaibify.gui.syncDispatcher import fdictDiffOverleafPush
        pathFile = tmp_path / "fig.pdf"
        pathFile.write_bytes(b"content")
        with patch(
            "vaibify.reproducibility.overleafMirror."
            "fdictDiffAgainstMirror",
            return_value={
                "listNew": [], "listOverwrite": [], "listUnchanged": [],
            },
        ) as mockDiff:
            dictResult = fdictDiffOverleafPush(
                "projid123", [str(pathFile)], "figures",
            )
        assert "listNew" in dictResult
        assert mockDiff.call_args[0][0] == "projid123"
        dictDigests = mockDiff.call_args[0][1]
        assert str(pathFile) in dictDigests

    def test_unreadable_file_skipped(self, tmp_path):
        from vaibify.gui.syncDispatcher import fdictDiffOverleafPush
        with patch(
            "vaibify.reproducibility.overleafMirror."
            "fdictDiffAgainstMirror",
            return_value={
                "listNew": [], "listOverwrite": [], "listUnchanged": [],
            },
        ) as mockDiff:
            fdictDiffOverleafPush(
                "projid123", ["/does/not/exist.pdf"], "figures",
            )
        dictDigests = mockDiff.call_args[0][1]
        assert dictDigests == {}


class TestCheckOverleafConflicts:

    def test_delegates_to_mirror_detect_conflicts(self):
        from vaibify.gui.syncDispatcher import (
            flistCheckOverleafConflicts,
        )
        with patch(
            "vaibify.reproducibility.overleafMirror."
            "flistDetectConflicts",
            return_value=[{"sLocalPath": "/a.pdf"}],
        ) as mockDetect:
            listResult = flistCheckOverleafConflicts(
                "projid123", ["/a.pdf"], "figures", {},
            )
        assert listResult == [{"sLocalPath": "/a.pdf"}]
        mockDetect.assert_called_once()


# ── ftResultPushToOverleaf: sMirrorSha + HEAD_SHA parsing ────────


def test_fsParseHeadShaFromOutput_extracts_sha():
    from vaibify.gui.syncDispatcher import fsParseHeadShaFromOutput
    sOutput = (
        "some warning\n"
        "HEAD_SHA=deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        "ok\n"
    )
    assert fsParseHeadShaFromOutput(sOutput) == (
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    )


def test_fsParseHeadShaFromOutput_empty_when_missing():
    from vaibify.gui.syncDispatcher import fsParseHeadShaFromOutput
    assert fsParseHeadShaFromOutput("just ok\n") == ""
    assert fsParseHeadShaFromOutput("") == ""


# ── fdictClassifyError conflict pattern ─────────────────────────


def test_classify_error_conflict_pattern():
    from vaibify.gui.syncDispatcher import fdictClassifyError
    dictResult = fdictClassifyError(
        1, "! [rejected]        master -> master (non-fast-forward)"
    )
    assert dictResult["sErrorType"] == "conflict"


def test_classify_error_conflict_updates_rejected():
    from vaibify.gui.syncDispatcher import fdictClassifyError
    dictResult = fdictClassifyError(
        1, "error: updates were rejected because the remote..."
    )
    assert dictResult["sErrorType"] == "conflict"


# ── fsWriteAskpassScript promotion ──────────────────────────────


def test_fsWriteAskpassScript_creates_mode_700_file():
    import os
    import stat
    from vaibify.gui.syncDispatcher import fsWriteAskpassScript
    sPath = fsWriteAskpassScript()
    try:
        iMode = os.stat(sPath).st_mode & 0o777
        assert iMode == 0o700
        with open(sPath) as handle:
            sContent = handle.read()
        assert "fsRetrieveSecret" in sContent
    finally:
        os.remove(sPath)


class TestComputeContainerDigests:
    """fdictComputeContainerDigests runs the SHA python snippet in container."""

    def test_fbParsesShaPathLines(self):
        from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
        from unittest.mock import MagicMock
        connectionDocker = MagicMock()
        connectionDocker.ftResultExecuteCommand.return_value = (
            0,
            "aaaa1111 /workspace/a.pdf\n"
            "bbbb2222 /workspace/b.png\n",
        )
        dictResult = fdictComputeContainerDigests(
            connectionDocker, "cid",
            ["/workspace/a.pdf", "/workspace/b.png"],
        )
        assert dictResult == {
            "/workspace/a.pdf": "aaaa1111",
            "/workspace/b.png": "bbbb2222",
        }

    def test_fbSkipsUnreadableEntries(self):
        from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
        from unittest.mock import MagicMock
        connectionDocker = MagicMock()
        connectionDocker.ftResultExecuteCommand.return_value = (
            0,
            "aaaa1111 /workspace/a.pdf\n- /workspace/missing.pdf\n",
        )
        dictResult = fdictComputeContainerDigests(
            connectionDocker, "cid",
            ["/workspace/a.pdf", "/workspace/missing.pdf"],
        )
        assert dictResult == {"/workspace/a.pdf": "aaaa1111"}

    def test_fbEmptyInputShortCircuits(self):
        from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
        from unittest.mock import MagicMock
        connectionDocker = MagicMock()
        dictResult = fdictComputeContainerDigests(
            connectionDocker, "cid", [],
        )
        assert dictResult == {}
        connectionDocker.ftResultExecuteCommand.assert_not_called()

    def test_fbExitNonZeroReturnsEmpty(self):
        from vaibify.gui.syncDispatcher import fdictComputeContainerDigests
        from unittest.mock import MagicMock
        connectionDocker = MagicMock()
        connectionDocker.ftResultExecuteCommand.return_value = (
            1, "python not found",
        )
        dictResult = fdictComputeContainerDigests(
            connectionDocker, "cid", ["/workspace/a.pdf"],
        )
        assert dictResult == {}
