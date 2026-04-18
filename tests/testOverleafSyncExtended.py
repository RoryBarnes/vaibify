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
    flistBuildCredentialHelperArgs,
    fnPushAnnotatedToOverleaf,
)


# -----------------------------------------------------------------------
# _fsBuildCredentialHelper
# -----------------------------------------------------------------------


def test_fsBuildCredentialHelper_embeds_token_path():
    sHelper = _fsBuildCredentialHelper("/tmp/overleaf-tok.abc")
    assert "/tmp/overleaf-tok.abc" in sHelper
    assert "password=" in sHelper


def test_fsBuildCredentialHelper_no_vaibify_import():
    sHelper = _fsBuildCredentialHelper("/tmp/tok")
    assert "from vaibify" not in sHelper
    assert "secretManager" not in sHelper


def test_fsBuildCredentialHelper_no_hardcoded_token():
    sHelper = _fsBuildCredentialHelper("/tmp/tok")
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
# flistBuildCredentialHelperArgs
# -----------------------------------------------------------------------


def test_flistBuildCredentialHelperArgs_embeds_token_path():
    listArgs = flistBuildCredentialHelperArgs("/tmp/tok-path")
    assert listArgs[0] == "-c"
    assert any("/tmp/tok-path" in sArg for sArg in listArgs)
    assert any("credential.https://" in sArg for sArg in listArgs)


def test_flistBuildCredentialHelperArgs_returns_inline_c_args():
    """Helper args must be inline ``-c`` flags, not a ``git config`` mutation."""
    listArgs = flistBuildCredentialHelperArgs("/tmp/tok")
    assert "config" not in listArgs
    assert "--global" not in listArgs
    assert len(listArgs) == 2
    assert listArgs[0] == "-c"


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
    "vaibify.reproducibility.overleafSync.flistBuildCredentialHelperArgs",
    return_value=["-c", "credential.https://git.overleaf.com.helper=!f"],
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
        "test-token-xyz",
    )
    mockAnnotate.assert_called_once()


# -----------------------------------------------------------------------
# _fnAnnotateTexInRepo — direct tests
# -----------------------------------------------------------------------


def test_fnAnnotateTexInRepo_missing_file_raises(tmp_path):
    from vaibify.reproducibility.overleafSync import (
        _fnAnnotateTexInRepo, OverleafError,
    )
    with pytest.raises(OverleafError, match="not found"):
        _fnAnnotateTexInRepo(
            str(tmp_path), "missing.tex", {}, "", "",
        )


@patch("vaibify.reproducibility.overleafSync.fsAnnotateTexFile")
def test_fnAnnotateTexInRepo_writes_when_changed(
    mockAnnotate, tmp_path,
):
    from vaibify.reproducibility.overleafSync import _fnAnnotateTexInRepo
    pathTex = tmp_path / "main.tex"
    pathTex.write_text("original content", encoding="utf-8")
    mockAnnotate.return_value = "annotated content"
    _fnAnnotateTexInRepo(
        str(tmp_path), "main.tex", {"listSteps": []},
        "https://github.com/u/r", "10.5281/z.1",
    )
    assert pathTex.read_text(encoding="utf-8") == "annotated content"


@patch("vaibify.reproducibility.overleafSync.fsAnnotateTexFile")
def test_fnAnnotateTexInRepo_skips_write_when_unchanged(
    mockAnnotate, tmp_path,
):
    from vaibify.reproducibility.overleafSync import _fnAnnotateTexInRepo
    pathTex = tmp_path / "main.tex"
    pathTex.write_text("same content", encoding="utf-8")
    mockAnnotate.return_value = "same content"
    sMtimeBefore = pathTex.stat().st_mtime
    _fnAnnotateTexInRepo(
        str(tmp_path), "main.tex", {}, "", "",
    )
    sMtimeAfter = pathTex.stat().st_mtime
    assert sMtimeBefore == sMtimeAfter


# -----------------------------------------------------------------------
# CLI entry-point tests
#
# The overleafSync.py script is invoked as a subprocess with a fake ``git``
# binary placed earlier on PATH. This lets tests assert on exit codes,
# stdout, and stderr without touching a real Overleaf repository.
# -----------------------------------------------------------------------


import sys as _sys

_S_OVERLEAF_SCRIPT = str(
    Path(__file__).resolve().parents[1]
    / "vaibify" / "reproducibility" / "overleafSync.py"
)


def _fsWriteGitShim(pathTmp, iExitCode, sStdout="", sStderr=""):
    """Write a fake ``git`` executable that returns a controlled result."""
    pathShim = pathTmp / "git"
    sScript = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({sStdout!r})\n"
        f"sys.stderr.write({sStderr!r})\n"
        f"sys.exit({iExitCode})\n"
    )
    pathShim.write_text(sScript)
    pathShim.chmod(0o755)
    return str(pathShim)


def _fsWriteSubcommandShim(pathTmp, dictSubcommandResults):
    """Write a git shim that dispatches on the first non-flag argument.

    ``dictSubcommandResults`` maps a git subcommand ("config",
    "ls-remote", ...) to ``(iExit, sStdout, sStderr)``. The shim
    skips leading ``-c key=value`` pairs so inline credential and
    hardening flags do not mask the real subcommand.
    """
    pathShim = pathTmp / "git"
    sMap = repr(dictSubcommandResults)
    sScript = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"dictMap = {sMap}\n"
        "listArgv = sys.argv[1:]\n"
        "iIdx = 0\n"
        "while iIdx < len(listArgv):\n"
        "    sArg = listArgv[iIdx]\n"
        "    if sArg == '-c' and iIdx + 1 < len(listArgv):\n"
        "        iIdx += 2\n"
        "        continue\n"
        "    break\n"
        "sKey = listArgv[iIdx] if iIdx < len(listArgv) else ''\n"
        "iExit, sOut, sErr = dictMap.get(sKey, (0, '', ''))\n"
        "sys.stdout.write(sOut)\n"
        "sys.stderr.write(sErr)\n"
        "sys.exit(iExit)\n"
    )
    pathShim.write_text(sScript)
    pathShim.chmod(0o755)
    return str(pathShim)


def _ftRunCli(listArgs, pathTmp, sStdin=""):
    """Run overleafSync.py with PATH prefixed by the shim directory."""
    import os as _os
    dictEnv = dict(_os.environ)
    dictEnv["PATH"] = str(pathTmp) + _os.pathsep + dictEnv.get("PATH", "")
    resultProcess = subprocess.run(
        [_sys.executable, _S_OVERLEAF_SCRIPT] + listArgs,
        input=sStdin, capture_output=True, text=True, env=dictEnv,
    )
    return resultProcess


def test_cli_help_lists_all_subcommands():
    resultProcess = subprocess.run(
        [_sys.executable, _S_OVERLEAF_SCRIPT, "--help"],
        capture_output=True, text=True,
    )
    assert resultProcess.returncode == 0
    for sSubcommand in ("ls-remote", "push", "push-annotated", "pull"):
        assert sSubcommand in resultProcess.stdout


def test_cli_ls_remote_success(tmp_path):
    _fsWriteSubcommandShim(tmp_path, {
        "config": (0, "", ""),
        "ls-remote": (0, "HEAD\n", ""),
    })
    resultProcess = _ftRunCli(
        ["ls-remote", "--project", "abc123"], tmp_path,
        sStdin="test-token\n",
    )
    assert resultProcess.returncode == 0


def test_cli_ls_remote_failure_passes_through_stderr(tmp_path):
    _fsWriteSubcommandShim(tmp_path, {
        "config": (0, "", ""),
        "ls-remote": (128, "", "fatal: repository not found\n"),
    })
    resultProcess = _ftRunCli(
        ["ls-remote", "--project", "abc123"], tmp_path,
        sStdin="test-token\n",
    )
    assert resultProcess.returncode == 128
    assert "repository not found" in resultProcess.stderr


def test_cli_ls_remote_auth_failure_maps_to_auth_exit(tmp_path):
    _fsWriteSubcommandShim(tmp_path, {
        "config": (0, "", ""),
        "ls-remote": (
            128, "", "fatal: Authentication failed for xyz\n",
        ),
    })
    resultProcess = _ftRunCli(
        ["ls-remote", "--project", "abc123"], tmp_path,
        sStdin="test-token\n",
    )
    assert resultProcess.returncode == 128
    assert "Authentication failed" in resultProcess.stderr


def test_cli_ls_remote_rejects_missing_token(tmp_path):
    _fsWriteSubcommandShim(tmp_path, {
        "config": (0, "", ""),
        "ls-remote": (0, "HEAD\n", ""),
    })
    resultProcess = _ftRunCli(
        ["ls-remote", "--project", "abc123"], tmp_path,
        sStdin="",
    )
    assert resultProcess.returncode == 3
    assert "token" in resultProcess.stderr.lower()


def test_cli_rejects_malformed_project_id(tmp_path):
    resultProcess = _ftRunCli(
        ["ls-remote", "--project", "bad;id"], tmp_path,
    )
    assert resultProcess.returncode == 2
    assert "Invalid" in resultProcess.stderr


def test_cli_requires_subcommand():
    resultProcess = subprocess.run(
        [_sys.executable, _S_OVERLEAF_SCRIPT],
        capture_output=True, text=True,
    )
    assert resultProcess.returncode != 0


@patch(
    "vaibify.reproducibility.overleafSync.fnPushFiguresToOverleaf",
)
def test_cli_push_reads_stdin_paths(mockPush):
    """The push subcommand forwards token (line 1) + newline paths."""
    from vaibify.reproducibility.overleafSync import main

    class _FakeStdin:
        def read(self):
            return "test-tok\n/a/fig1.pdf\n/a/fig2.png\n\n"

    with patch("vaibify.reproducibility.overleafSync.sys.stdin", _FakeStdin()):
        with patch(
            "vaibify.reproducibility.overleafSync.sys.stdout.write"
        ):
            main([
                "push", "--project", "abc123",
                "--target", "figures",
            ])
    mockPush.assert_called_once()
    tPositional = mockPush.call_args[0]
    listPaths, sProject, sTarget, sToken = tPositional[:4]
    assert listPaths == ["/a/fig1.pdf", "/a/fig2.png"]
    assert sProject == "abc123"
    assert sTarget == "figures"
    assert sToken == "test-tok"


@patch(
    "vaibify.reproducibility.overleafSync.fnPullTexFromOverleaf",
)
def test_cli_pull_reads_stdin_paths(mockPull):
    """The pull subcommand forwards token (line 1) + newline paths."""
    from vaibify.reproducibility.overleafSync import main

    class _FakeStdin:
        def read(self):
            return "test-tok\nmain.tex\nrefs.bib\n"

    with patch("vaibify.reproducibility.overleafSync.sys.stdin", _FakeStdin()):
        with patch(
            "vaibify.reproducibility.overleafSync.sys.stdout.write"
        ):
            main([
                "pull", "--project", "abc123",
                "--target", "/work/tex",
            ])
    mockPull.assert_called_once()
    sProject, listPaths, sTarget, sToken = mockPull.call_args[0]
    assert sProject == "abc123"
    assert listPaths == ["main.tex", "refs.bib"]
    assert sTarget == "/work/tex"
    assert sToken == "test-tok"


@patch(
    "vaibify.reproducibility.overleafSync.fnPushAnnotatedToOverleaf",
)
def test_cli_push_annotated_reads_json_payload(mockAnnotated):
    """The push-annotated subcommand parses token (line 1) + JSON payload."""
    from vaibify.reproducibility.overleafSync import main
    import json as _json
    dictPayload = {
        "listFigurePaths": ["/a/fig.pdf"],
        "dictWorkflow": {"listSteps": [{"sName": "First"}]},
    }

    class _FakeStdin:
        def read(self):
            return "test-tok\n" + _json.dumps(dictPayload)

    with patch("vaibify.reproducibility.overleafSync.sys.stdin", _FakeStdin()):
        with patch(
            "vaibify.reproducibility.overleafSync.sys.stdout.write"
        ):
            main([
                "push-annotated", "--project", "abc123",
                "--target", "figures",
                "--github-base-url", "https://github.com/u/r",
                "--doi", "10.5281/zenodo.1",
                "--tex-filename", "main.tex",
            ])
    mockAnnotated.assert_called_once()
    tPositional = mockAnnotated.call_args[0]
    (
        listPaths, sProject, sTarget, dictWf,
        sUrl, sDoi, sToken, sTex,
    ) = tPositional[:8]
    assert listPaths == ["/a/fig.pdf"]
    assert sProject == "abc123"
    assert dictWf == {"listSteps": [{"sName": "First"}]}
    assert sUrl == "https://github.com/u/r"
    assert sDoi == "10.5281/zenodo.1"
    assert sToken == "test-tok"
    assert sTex == "main.tex"


def test_cli_auth_error_maps_to_specific_exit_code():
    """OverleafAuthError raised inside a subcommand exits with code 3."""
    from vaibify.reproducibility.overleafSync import (
        main, OverleafAuthError,
    )
    with patch(
        "vaibify.reproducibility.overleafSync.fnPushFiguresToOverleaf",
        side_effect=OverleafAuthError("auth failed xyz"),
    ):
        class _FakeStdin:
            def read(self):
                return "/a/fig.pdf\n"

        with patch("vaibify.reproducibility.overleafSync.sys.stdin", _FakeStdin()):
            with pytest.raises(SystemExit) as excInfo:
                main([
                    "push", "--project", "abc123",
                    "--target", "figures",
                ])
    assert excInfo.value.code == 3


def test_cli_generic_overleaf_error_exits_nonzero():
    """Plain OverleafError exits non-zero with a readable message."""
    from vaibify.reproducibility.overleafSync import main, OverleafError
    with patch(
        "vaibify.reproducibility.overleafSync.fnPullTexFromOverleaf",
        side_effect=OverleafError("clone failed"),
    ):
        class _FakeStdin:
            def read(self):
                return "main.tex\n"

        with patch("vaibify.reproducibility.overleafSync.sys.stdin", _FakeStdin()):
            with pytest.raises(SystemExit) as excInfo:
                main([
                    "pull", "--project", "abc123",
                    "--target", "/work/tex",
                ])
    assert excInfo.value.code == 1


# -----------------------------------------------------------------------
# Security: pull-path validation, symlink rejection, git hardening
# -----------------------------------------------------------------------


def test_fnValidatePullRelativePath_rejects_leading_slash():
    from vaibify.reproducibility.overleafSync import (
        fnValidatePullRelativePath, OverleafError,
    )
    with pytest.raises(OverleafError, match="must not start with a slash"):
        fnValidatePullRelativePath("/etc/passwd")


def test_fnValidatePullRelativePath_rejects_parent_segments():
    from vaibify.reproducibility.overleafSync import (
        fnValidatePullRelativePath, OverleafError,
    )
    with pytest.raises(OverleafError, match="must not contain '..'"):
        fnValidatePullRelativePath("figures/../../secret")


def test_fnValidatePullRelativePath_rejects_null_bytes():
    from vaibify.reproducibility.overleafSync import (
        fnValidatePullRelativePath, OverleafError,
    )
    with pytest.raises(OverleafError, match="null bytes"):
        fnValidatePullRelativePath("main\x00.tex")


def test_fnValidatePullRelativePath_accepts_valid():
    from vaibify.reproducibility.overleafSync import (
        fnValidatePullRelativePath,
    )
    fnValidatePullRelativePath("main.tex")
    fnValidatePullRelativePath("figures/fig1.pdf")
    fnValidatePullRelativePath("a/b/c/file.tex")


def test_fnPullTexFromOverleaf_rejects_traversal_path(tmp_path, monkeypatch):
    """Pull must refuse ``..`` entries before any git operation."""
    from vaibify.reproducibility.overleafSync import (
        fnPullTexFromOverleaf, OverleafError,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(OverleafError, match="must not contain '..'"):
        fnPullTexFromOverleaf(
            "proj123", ["../../etc/passwd"],
            "out", "tok",
        )


def test_fnPullTexFromOverleaf_rejects_null_byte_path(tmp_path, monkeypatch):
    """Pull must refuse NUL bytes in listPullPaths."""
    from vaibify.reproducibility.overleafSync import (
        fnPullTexFromOverleaf, OverleafError,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(OverleafError, match="null bytes"):
        fnPullTexFromOverleaf(
            "proj123", ["main\x00.tex"], "out", "tok",
        )


def test_fnCopySingleFile_rejects_symlink_source(tmp_path):
    """Push side: refuse to dereference a symlinked figure."""
    from vaibify.reproducibility.overleafSync import (
        _fnCopySingleFile, OverleafError,
    )
    sReal = tmp_path / "secret.txt"
    sReal.write_text("sensitive")
    sSymlink = tmp_path / "fig.pdf"
    os.symlink(str(sReal), str(sSymlink))
    pathTarget = tmp_path / "dest"
    pathTarget.mkdir()
    with pytest.raises(OverleafError, match="Refusing to push symlink"):
        _fnCopySingleFile(str(sSymlink), pathTarget)


def test_fnCopyPulledFile_rejects_symlink_source(tmp_path):
    """Pull side: refuse to copy a symlink out of the cloned repo."""
    from vaibify.reproducibility.overleafSync import (
        _fnCopyPulledFile, OverleafError,
    )
    sRepo = tmp_path / "repo"
    sRepo.mkdir()
    sOutside = tmp_path / "outside.txt"
    sOutside.write_text("outside secret")
    sSymlinkPath = sRepo / "main.tex"
    os.symlink(str(sOutside), str(sSymlinkPath))
    pathTarget = tmp_path / "out"
    pathTarget.mkdir()
    with pytest.raises(OverleafError):
        _fnCopyPulledFile(str(sRepo), "main.tex", pathTarget)


def test_fnCopyPulledFile_rejects_realpath_escape(tmp_path):
    """Pull side: realpath must stay inside the repo even via symlink dir."""
    from vaibify.reproducibility.overleafSync import (
        _fnCopyPulledFile, OverleafError,
    )
    sRepo = tmp_path / "repo"
    sRepo.mkdir()
    sOutsideDir = tmp_path / "outside"
    sOutsideDir.mkdir()
    (sOutsideDir / "secret.tex").write_text("leaked")
    sSymlinkDir = sRepo / "link"
    os.symlink(str(sOutsideDir), str(sSymlinkDir))
    pathTarget = tmp_path / "out"
    pathTarget.mkdir()
    with pytest.raises(OverleafError):
        _fnCopyPulledFile(
            str(sRepo), "link/secret.tex", pathTarget,
        )


def test_clone_command_includes_hardening_flags(tmp_path):
    """Every clone invocation must include the submodule/symlink hardening."""
    from vaibify.reproducibility import overleafSync as mod
    listCaptured = []

    def fnFake(listCommand, **kwargs):
        listCaptured.append(listCommand)
        mockR = MagicMock()
        mockR.returncode = 0
        mockR.stdout = ""
        mockR.stderr = ""
        if "clone" in listCommand:
            sDest = listCommand[-1]
            Path(sDest).mkdir(parents=True, exist_ok=True)
        return mockR

    with patch.object(mod.subprocess, "run", side_effect=fnFake):
        with patch.object(
            mod, "_fbHasUncommittedChanges", return_value=False,
        ):
            mod.fnPushFiguresToOverleaf(
                [], "proj123", "figures", "tok",
            )
    listCloneCmd = [c for c in listCaptured if "clone" in c]
    assert listCloneCmd, "clone must have been invoked"
    sJoined = " ".join(str(s) for s in listCloneCmd[0])
    assert "protocol.file.allow=never" in sJoined
    assert "core.symlinks=false" in sJoined
    assert "submodule.recurse=false" in sJoined
    assert "--no-recurse-submodules" in sJoined


def test_clone_uses_inline_credential_helper_not_global(tmp_path):
    """Credential helper is passed as ``-c``, not written to global config."""
    from vaibify.reproducibility import overleafSync as mod
    listCaptured = []

    def fnFake(listCommand, **kwargs):
        listCaptured.append(listCommand)
        mockR = MagicMock()
        mockR.returncode = 0
        mockR.stdout = ""
        mockR.stderr = ""
        if "clone" in listCommand:
            sDest = listCommand[-1]
            Path(sDest).mkdir(parents=True, exist_ok=True)
        return mockR

    with patch.object(mod.subprocess, "run", side_effect=fnFake):
        with patch.object(
            mod, "_fbHasUncommittedChanges", return_value=False,
        ):
            mod.fnPushFiguresToOverleaf(
                [], "proj123", "figures", "tok",
            )
    for listCommand in listCaptured:
        sJoined = " ".join(str(s) for s in listCommand)
        assert "config --global" not in sJoined, (
            f"Must not mutate global git config: {sJoined}"
        )
    listCloneCmd = [c for c in listCaptured if "clone" in c]
    sJoined = " ".join(str(s) for s in listCloneCmd[0])
    assert "credential.https://git.overleaf.com.helper=" in sJoined
