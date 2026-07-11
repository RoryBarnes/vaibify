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
                listTempFigures, "abc123proj", "figures",
                "test-token-xyz",
            )

    bFoundInlineCredentialHelper = False
    bFoundGlobalConfigMutation = False
    for listCommand in listCapturedCommands:
        sCommandStr = " ".join(str(s) for s in listCommand)
        if (
            "credential.https://" in sCommandStr
            and _OVERLEAF_GIT_HOST in sCommandStr
        ):
            bFoundInlineCredentialHelper = True
        if "config --global" in sCommandStr:
            bFoundGlobalConfigMutation = True

        if "git" in sCommandStr and "clone" in sCommandStr:
            sCloneUrl = [
                s for s in listCommand
                if _OVERLEAF_GIT_HOST in str(s)
                and not str(s).startswith("credential.")
            ]
            if sCloneUrl:
                for sUrlPart in sCloneUrl:
                    sLower = str(sUrlPart).lower()
                    assert "token" not in sLower or "credential" in sLower

    assert bFoundInlineCredentialHelper
    assert not bFoundGlobalConfigMutation


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
            "test-token-xyz",
        )

    assert (Path(sTargetDir) / "main.tex").exists()
    assert (Path(sTargetDir) / "refs.bib").exists()


def test_commit_marker_detection():
    assert _COMMIT_MARKER == "[vaibify]"

    sCommitMessage = f"{_COMMIT_MARKER} Update figures"
    assert _COMMIT_MARKER in sCommitMessage
    assert sCommitMessage.startswith("[vaibify]")


def test_credential_helper_does_not_embed_token(tmp_path):
    sTokenFile = str(tmp_path / "tok")
    sHelper = _fsBuildCredentialHelper(sTokenFile)

    assert sTokenFile in sHelper
    assert "password=" in sHelper
    sLower = sHelper.lower()
    assert "ghp_" not in sLower
    assert "access_token=" not in sLower


def test_credential_helper_emits_username_and_password(tmp_path):
    """The helper fragment prints both ``username=git`` and the password.

    Git's credential-helper protocol requires both lines; emitting only
    the password sends git to its stdin prompt fallback and fails
    non-interactively with ``could not read Username``.
    """
    import subprocess
    sTokenFile = str(tmp_path / "overleaf_token")
    (tmp_path / "overleaf_token").write_text("abc-123-xyz")
    sHelper = _fsBuildCredentialHelper(sTokenFile)
    sBody = sHelper.lstrip("!")
    resultProcess = subprocess.run(
        ["bash", "-c", sBody],
        capture_output=True, text=True,
    )
    assert resultProcess.returncode == 0
    listLines = resultProcess.stdout.strip().splitlines()
    assert "username=git" in listLines
    assert "password=abc-123-xyz" in listLines


def test_fnValidateTargetDirectory_rejects_leading_slash():
    from vaibify.reproducibility.overleafSync import (
        fnValidateTargetDirectory, OverleafError,
    )
    with pytest.raises(OverleafError, match="must not start with a slash"):
        fnValidateTargetDirectory("/Figures")


def test_fnValidateTargetDirectory_rejects_parent_segments():
    from vaibify.reproducibility.overleafSync import (
        fnValidateTargetDirectory, OverleafError,
    )
    with pytest.raises(OverleafError, match="must not contain '..'"):
        fnValidateTargetDirectory("Figures/../escape")


def test_fnValidateTargetDirectory_accepts_valid_paths():
    from vaibify.reproducibility.overleafSync import (
        fnValidateTargetDirectory,
    )
    fnValidateTargetDirectory("")
    fnValidateTargetDirectory("Figures")
    fnValidateTargetDirectory("figures/v2")
    fnValidateTargetDirectory("a/b/c")


def test_push_emits_push_status_no_changes_when_clone_is_pristine(
    tmp_path,
):
    """No commit should still emit PUSH_STATUS=no-changes to stdout."""
    import io
    import contextlib
    from vaibify.reproducibility import overleafSync as mod
    listCmds = []

    def fnFakeRun(listCmd, **kwargs):
        listCmds.append(listCmd)
        mockR = MagicMock()
        mockR.returncode = 0
        sJoined = " ".join(str(s) for s in listCmd)
        if "clone" in listCmd:
            sDest = listCmd[-1]
            Path(sDest).mkdir(parents=True, exist_ok=True)
        if "status" in sJoined and "--porcelain" in sJoined:
            mockR.stdout = ""
        elif "rev-parse" in sJoined:
            mockR.stdout = "abc1234\n"
        else:
            mockR.stdout = ""
        mockR.stderr = ""
        return mockR

    buf = io.StringIO()
    with patch.object(mod.subprocess, "run", side_effect=fnFakeRun), \
         contextlib.redirect_stdout(buf):
        mod.fnPushFiguresToOverleaf(
            [], "proj123", "figures", "tok", sMirrorSha="",
        )
    sOutput = buf.getvalue()
    assert "PUSH_STATUS=no-changes" in sOutput
    assert "HEAD_SHA=abc1234" in sOutput


# ----------------------------------------------------------------------
# Push manifest: local→remote path map round trip
# ----------------------------------------------------------------------


def test_push_manifest_records_local_to_remote_map(tmp_path):
    """Recording stores {local: remote}; both readers recover it."""
    from vaibify.reproducibility.overleafSync import (
        fdictOverleafRemotePathsAt,
        flistOverleafPushedFiguresAt,
        fnRecordOverleafPushManifest,
    )
    sRepo = str(tmp_path)
    fnRecordOverleafPushManifest(
        sRepo, "commitabc",
        ["Plot/A12/foo.pdf", "Plot/A13/bar.png"], "figures",
    )
    assert fdictOverleafRemotePathsAt(sRepo, "commitabc") == {
        "Plot/A12/foo.pdf": "figures/foo.pdf",
        "Plot/A13/bar.png": "figures/bar.png",
    }
    assert flistOverleafPushedFiguresAt(sRepo, "commitabc") == [
        "Plot/A12/foo.pdf", "Plot/A13/bar.png",
    ]


def test_push_manifest_empty_target_uses_bare_basename(tmp_path):
    """An empty target directory lands figures at the project root."""
    from vaibify.reproducibility.overleafSync import (
        fdictOverleafRemotePathsAt,
        fnRecordOverleafPushManifest,
    )
    sRepo = str(tmp_path)
    fnRecordOverleafPushManifest(
        sRepo, "commitabc", ["Plot/foo.pdf"],
    )
    assert fdictOverleafRemotePathsAt(sRepo, "commitabc") == {
        "Plot/foo.pdf": "foo.pdf",
    }


def test_push_manifest_tolerates_legacy_list_shape(tmp_path):
    """A legacy list-of-paths entry still yields the pushed-figure list.

    The remote path falls back to the local path — a lookup miss at
    verify time, which fails closed (diverged), never falsely synced.
    """
    import json as jsonModule
    from vaibify.reproducibility.overleafSync import (
        fdictOverleafRemotePathsAt,
        flistOverleafPushedFiguresAt,
    )
    sDir = os.path.join(str(tmp_path), ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    with open(
        os.path.join(sDir, "overleafPushManifest.json"),
        "w", encoding="utf-8",
    ) as fileHandle:
        jsonModule.dump(
            {"commitabc": ["Plot/foo.pdf"]}, fileHandle,
        )
    assert flistOverleafPushedFiguresAt(
        str(tmp_path), "commitabc",
    ) == ["Plot/foo.pdf"]
    assert fdictOverleafRemotePathsAt(
        str(tmp_path), "commitabc",
    ) == {"Plot/foo.pdf": "Plot/foo.pdf"}


def test_remote_path_authority_flattens_directories():
    """fsOverleafRemotePathFor mirrors the push's basename flattening."""
    from vaibify.reproducibility.overleafSync import (
        fsOverleafRemotePathFor,
    )
    assert fsOverleafRemotePathFor(
        "Plot/A12/foo.pdf", "figures",
    ) == "figures/foo.pdf"
    assert fsOverleafRemotePathFor("foo.pdf", "") == "foo.pdf"
