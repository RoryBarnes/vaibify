"""Extended tests for vaibify.reproducibility.overleafSync."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import subprocess

from vaibify.reproducibility.overleafSync import (
    OverleafError,
    OverleafAuthError,
    OverleafRateLimitError,
    _OVERLEAF_GIT_HOST,
    _COMMIT_MARKER,
    _RATE_LIMIT_HINT,
    _fsBuildCredentialHelper,
    _fnDetectAuthFailure,
    _fnDetectRateLimit,
    _fsCombineErrorOutput,
    _fnCopyFiguresToRepo,
    _fnCopySingleFile,
    _fnCopyPulledFiles,
    _fnCopyPulledFile,
    _fbHasUncommittedChanges,
    _fnCommitAndPush,
    fnConfigureGitCredentials,
    fnPushAnnotatedToOverleaf,
)


# -----------------------------------------------------------------------
# _fsBuildCredentialHelper
# -----------------------------------------------------------------------


def test_fsBuildCredentialHelper_uses_secret_manager():
    sHelper = _fsBuildCredentialHelper()
    assert "fsRetrieveSecret" in sHelper
    assert "overleaf_token" in sHelper


def test_fsBuildCredentialHelper_no_hardcoded_token():
    sHelper = _fsBuildCredentialHelper()
    sLower = sHelper.lower()
    assert "ghp_" not in sLower
    assert "password123" not in sLower


# -----------------------------------------------------------------------
# _fnDetectAuthFailure
# -----------------------------------------------------------------------


def test_fnDetectAuthFailure_raises_on_401():
    with pytest.raises(OverleafAuthError):
        _fnDetectAuthFailure("HTTP 401 Unauthorized")


def test_fnDetectAuthFailure_raises_on_authentication():
    with pytest.raises(OverleafAuthError):
        _fnDetectAuthFailure("Authentication required")


def test_fnDetectAuthFailure_no_raise_on_normal():
    _fnDetectAuthFailure("Everything is fine")


# -----------------------------------------------------------------------
# _fnDetectRateLimit
# -----------------------------------------------------------------------


def test_fnDetectRateLimit_raises():
    with pytest.raises(OverleafRateLimitError):
        _fnDetectRateLimit("Error: rate limit exceeded")


def test_fnDetectRateLimit_no_raise_on_normal():
    _fnDetectRateLimit("Success")


# -----------------------------------------------------------------------
# _fsCombineErrorOutput
# -----------------------------------------------------------------------


def test_fsCombineErrorOutput_both():
    mockError = MagicMock()
    mockError.stdout = "out"
    mockError.stderr = "err"
    sResult = _fsCombineErrorOutput(mockError)
    assert "out" in sResult
    assert "err" in sResult


def test_fsCombineErrorOutput_none_values():
    mockError = MagicMock()
    mockError.stdout = None
    mockError.stderr = None
    sResult = _fsCombineErrorOutput(mockError)
    assert sResult == ""


# -----------------------------------------------------------------------
# _fnCopyFiguresToRepo
# -----------------------------------------------------------------------


def test_fnCopyFiguresToRepo_copies_files(tmp_path):
    sRepoDir = str(tmp_path / "repo")
    os.makedirs(sRepoDir)
    sFigPath = str(tmp_path / "fig.pdf")
    with open(sFigPath, "wb") as fh:
        fh.write(b"pdf content")
    _fnCopyFiguresToRepo([sFigPath], sRepoDir, "figures")
    sExpected = os.path.join(sRepoDir, "figures", "fig.pdf")
    assert os.path.isfile(sExpected)


def test_fnCopyFiguresToRepo_creates_target_dir(tmp_path):
    sRepoDir = str(tmp_path / "repo")
    os.makedirs(sRepoDir)
    sFigPath = str(tmp_path / "fig.png")
    with open(sFigPath, "wb") as fh:
        fh.write(b"png")
    _fnCopyFiguresToRepo([sFigPath], sRepoDir, "deep/sub")
    sExpected = os.path.join(sRepoDir, "deep", "sub", "fig.png")
    assert os.path.isfile(sExpected)


# -----------------------------------------------------------------------
# _fnCopySingleFile
# -----------------------------------------------------------------------


def test_fnCopySingleFile_raises_on_missing():
    with pytest.raises(FileNotFoundError):
        _fnCopySingleFile("/nonexistent.pdf", Path("/tmp"))


def test_fnCopySingleFile_copies(tmp_path):
    sSrc = str(tmp_path / "src.txt")
    with open(sSrc, "w") as fh:
        fh.write("data")
    sTarget = tmp_path / "dest"
    sTarget.mkdir()
    _fnCopySingleFile(sSrc, sTarget)
    assert (sTarget / "src.txt").is_file()


# -----------------------------------------------------------------------
# _fnCopyPulledFiles and _fnCopyPulledFile
# -----------------------------------------------------------------------


def test_fnCopyPulledFiles_copies_to_target(tmp_path):
    sRepoDir = str(tmp_path / "repo")
    os.makedirs(sRepoDir)
    with open(os.path.join(sRepoDir, "main.tex"), "w") as fh:
        fh.write("\\begin{document}")
    sTargetDir = str(tmp_path / "output")
    _fnCopyPulledFiles(sRepoDir, ["main.tex"], sTargetDir)
    assert os.path.isfile(os.path.join(sTargetDir, "main.tex"))


def test_fnCopyPulledFile_raises_on_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        _fnCopyPulledFile(
            str(tmp_path), "missing.tex", tmp_path,
        )


# -----------------------------------------------------------------------
# _fbHasUncommittedChanges
# -----------------------------------------------------------------------


@patch("vaibify.reproducibility.overleafSync.subprocess.run")
def test_fbHasUncommittedChanges_true(mockRun):
    mockRun.return_value = MagicMock(
        stdout="M file.txt\n",
    )
    assert _fbHasUncommittedChanges("/repo") is True


@patch("vaibify.reproducibility.overleafSync.subprocess.run")
def test_fbHasUncommittedChanges_false(mockRun):
    mockRun.return_value = MagicMock(stdout="")
    assert _fbHasUncommittedChanges("/repo") is False


# -----------------------------------------------------------------------
# _fnCommitAndPush — no changes
# -----------------------------------------------------------------------


@patch(
    "vaibify.reproducibility.overleafSync._fbHasUncommittedChanges",
    return_value=False,
)
def test_fnCommitAndPush_skips_when_clean(mockChanges):
    _fnCommitAndPush("/repo")


# -----------------------------------------------------------------------
# fnConfigureGitCredentials
# -----------------------------------------------------------------------


@patch("vaibify.reproducibility.overleafSync._fnRunGitConfig")
def test_fnConfigureGitCredentials_calls_config(mockConfig):
    fnConfigureGitCredentials("proj123")
    mockConfig.assert_called_once()


# -----------------------------------------------------------------------
# fnPushAnnotatedToOverleaf
# -----------------------------------------------------------------------


@patch(
    "vaibify.reproducibility.overleafSync._fnCommitAndPush",
)
@patch(
    "vaibify.reproducibility.overleafSync._fnCopyFiguresToRepo",
)
@patch(
    "vaibify.reproducibility.overleafSync._fnCloneOverleafRepo",
)
@patch(
    "vaibify.reproducibility.overleafSync.fnConfigureGitCredentials",
)
@patch(
    "vaibify.reproducibility.overleafSync._fnAnnotateTexInRepo",
)
def test_fnPushAnnotatedToOverleaf_calls_annotate(
    mockAnnotate, mockCreds, mockClone, mockCopy, mockCommit,
):
    fnPushAnnotatedToOverleaf(
        ["/fig.pdf"], "proj123", "figures",
        {"listSteps": []},
        "https://github.com/user/repo",
        "10.5281/zenodo.123",
    )
    mockAnnotate.assert_called_once()
