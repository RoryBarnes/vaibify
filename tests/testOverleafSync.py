"""Tests for vaibify.reproducibility.overleafSync (all git/subprocess mocked)."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from vaibify.reproducibility.overleafSync import (
    fnPushFiguresToOverleaf,
    fnPullTexFromOverleaf,
    _COMMIT_MARKER,
    _OVERLEAF_GIT_HOST,
    _fsBuildCredentialHelper,
)


@pytest.fixture
def listTempFigures(tmp_path):
    """Create temporary figure files and return their paths."""
    listPaths = []
    for sName in ["fig1.pdf", "fig2.png"]:
        pathFigure = tmp_path / sName
        pathFigure.write_bytes(b"fake figure content")
        listPaths.append(str(pathFigure))
    return listPaths


def test_fnPushFiguresToOverleaf_calls_git_with_credential_helper(
    listTempFigures,
):
    listCapturedCommands = []

    def fnFakeSubprocess(listCommand, **kwargs):
        listCapturedCommands.append(listCommand)
        mockResult = MagicMock()
        mockResult.returncode = 0
        mockResult.stdout = ""
        mockResult.stderr = ""
        return mockResult

    with patch(
        "vaibify.reproducibility.overleafSync.subprocess.run",
        side_effect=fnFakeSubprocess,
    ):
        with patch(
            "vaibify.reproducibility.overleafSync."
            "_fbHasUncommittedChanges",
            return_value=True,
        ):
            fnPushFiguresToOverleaf(
                listTempFigures, "abc123proj", "figures"
            )

    bFoundGitConfig = False
    for listCommand in listCapturedCommands:
        sCommandStr = " ".join(str(s) for s in listCommand)
        if "git" in sCommandStr and "config" in sCommandStr:
            bFoundGitConfig = True
            assert "credential" in sCommandStr
            assert _OVERLEAF_GIT_HOST in sCommandStr

        if "git" in sCommandStr and "clone" in sCommandStr:
            sCloneUrl = [
                s for s in listCommand
                if _OVERLEAF_GIT_HOST in str(s)
            ]
            if sCloneUrl:
                for sUrlPart in sCloneUrl:
                    sLower = str(sUrlPart).lower()
                    assert "token" not in sLower or "credential" in sLower

    assert bFoundGitConfig


def test_fnPullTexFromOverleaf_copies_specified_files(tmp_path):
    sTargetDir = str(tmp_path / "pulled")
    sOverleafId = "proj456"

    listCapturedCloneArgs = []

    def fnFakeSubprocess(listCommand, **kwargs):
        listCapturedCloneArgs.append((listCommand, kwargs))
        if "clone" in listCommand:
            sDest = listCommand[-1]
            pathDest = Path(sDest)
            pathDest.mkdir(parents=True, exist_ok=True)
            (pathDest / "main.tex").write_text("\\documentclass{}")
            (pathDest / "refs.bib").write_text("@article{}")
        mockResult = MagicMock()
        mockResult.returncode = 0
        mockResult.stdout = ""
        mockResult.stderr = ""
        return mockResult

    with patch(
        "vaibify.reproducibility.overleafSync.subprocess.run",
        side_effect=fnFakeSubprocess,
    ):
        fnPullTexFromOverleaf(
            sOverleafId,
            ["main.tex", "refs.bib"],
            sTargetDir,
        )

    assert (Path(sTargetDir) / "main.tex").exists()
    assert (Path(sTargetDir) / "refs.bib").exists()


def test_commit_marker_detection():
    assert _COMMIT_MARKER == "[vaibify]"

    sCommitMessage = f"{_COMMIT_MARKER} Update figures"
    assert _COMMIT_MARKER in sCommitMessage
    assert sCommitMessage.startswith("[vaibify]")


def test_credential_helper_does_not_embed_token():
    sHelper = _fsBuildCredentialHelper()

    assert "secretManager" in sHelper or "fsRetrieveSecret" in sHelper
    sLower = sHelper.lower()
    assert "ghp_" not in sLower
    assert "access_token=" not in sLower
