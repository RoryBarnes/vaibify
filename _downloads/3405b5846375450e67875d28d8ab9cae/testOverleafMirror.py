"""Tests for vaibify.reproducibility.overleafMirror (all subprocess mocked)."""

import os
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import overleafMirror
from vaibify.reproducibility.overleafMirror import (
    _fbIsConflict,
    _fdictParseLsTreeLine,
    _fsRemotePathFor,
    fbRefreshMirror,
    fdictDiffAgainstMirror,
    flistDetectCaseCollisions,
    flistDetectConflicts,
    flistListMirrorTree,
    fnDeleteMirror,
    fsComputeBlobSha,
    fsGetMirrorRoot,
    fsRedactStderr,
)

# Backwards-compatible private alias still exposed by the module.
_fsComputeBlobSha = fsComputeBlobSha


# ── Mirror root & path hygiene ──────────────────────────────────


def test_fsGetMirrorRoot_uses_os_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetMirrorRoot()
    assert sRoot.endswith(os.path.join(".vaibify", "overleaf-mirrors"))
    assert str(tmp_path) in sRoot


def test_project_id_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError):
        flistListMirrorTree("../evil")


def test_project_id_rejects_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError):
        flistListMirrorTree("proj/id")


def test_project_id_rejects_whitespace(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError):
        flistListMirrorTree("proj id")


def test_project_id_rejects_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError):
        flistListMirrorTree("")


# ── Blob SHA: matches git hash-object ────────────────────────────


def test_fsComputeBlobSha_empty_file(tmp_path):
    pathFile = tmp_path / "empty.txt"
    pathFile.write_bytes(b"")
    sSha = _fsComputeBlobSha(str(pathFile))
    # sha1("blob 0\x00") == e69de29bb2d1d6434b8b29ae775ad8c2e48c5391
    assert sSha == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_fsComputeBlobSha_hello_world(tmp_path):
    pathFile = tmp_path / "hello.txt"
    pathFile.write_bytes(b"hello world")
    # sha1("blob 11\0hello world")
    assert _fsComputeBlobSha(str(pathFile)) == (
        "95d09f2b10159347eece71399a7e2e907ea3df4f"
    )


def test_fsComputeBlobSha_binary(tmp_path):
    pathFile = tmp_path / "bin.dat"
    baContent = bytes(range(256)) * 4
    pathFile.write_bytes(baContent)
    sSha = _fsComputeBlobSha(str(pathFile))
    import hashlib
    hasher = hashlib.sha1()
    hasher.update(f"blob {len(baContent)}\x00".encode("utf-8"))
    hasher.update(baContent)
    assert sSha == hasher.hexdigest()


# ── ls-tree parsing ──────────────────────────────────────────────


def test_parse_ls_tree_blob_line():
    sLine = "100644 blob abc123def456    1024\tfigures/fig1.pdf"
    dictEntry = _fdictParseLsTreeLine(sLine)
    assert dictEntry is not None
    assert dictEntry["sPath"] == "figures/fig1.pdf"
    assert dictEntry["sType"] == "blob"
    assert dictEntry["sDigest"] == "abc123def456"
    assert dictEntry["iSize"] == 1024


def test_parse_ls_tree_dash_size():
    sLine = "040000 tree abc123    -\tfigures"
    dictEntry = _fdictParseLsTreeLine(sLine)
    assert dictEntry is not None
    assert dictEntry["iSize"] == 0


def test_parse_ls_tree_malformed_returns_none():
    assert _fdictParseLsTreeLine("garbage") is None
    assert _fdictParseLsTreeLine("100644 blob abc123") is None


# ── flistListMirrorTree ──────────────────────────────────────────


def _fnFakeMirrorDir(tmp_path, sProjectId):
    """Create a fake mirror directory with a .git subdir."""
    sMirror = os.path.join(
        str(tmp_path), ".vaibify", "overleaf-mirrors", sProjectId)
    os.makedirs(os.path.join(sMirror, ".git"))
    return sMirror


class TestListMirrorTree:

    def test_empty_when_no_mirror(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert flistListMirrorTree("abc123") == []

    def test_empty_tree(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _fnFakeMirrorDir(tmp_path, "abc123")
        mockResult = MagicMock(returncode=0, stdout="")
        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            return_value=mockResult,
        ):
            assert flistListMirrorTree("abc123") == []

    def test_single_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _fnFakeMirrorDir(tmp_path, "abc123")
        sOutput = "100644 blob abc123sha    42\tmain.tex\n"
        mockResult = MagicMock(returncode=0, stdout=sOutput)
        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            return_value=mockResult,
        ):
            listEntries = flistListMirrorTree("abc123")
        assert len(listEntries) == 1
        assert listEntries[0]["sPath"] == "main.tex"
        assert listEntries[0]["sDigest"] == "abc123sha"

    def test_nested_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _fnFakeMirrorDir(tmp_path, "abc123")
        sOutput = (
            "100644 blob aaa    10\tmain.tex\n"
            "100644 blob bbb    20\tfigures/fig1.pdf\n"
            "100644 blob ccc    30\tfigures/sub/fig2.pdf\n"
        )
        mockResult = MagicMock(returncode=0, stdout=sOutput)
        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            return_value=mockResult,
        ):
            listEntries = flistListMirrorTree("abc123")
        listPaths = [d["sPath"] for d in listEntries]
        assert "main.tex" in listPaths
        assert "figures/fig1.pdf" in listPaths
        assert "figures/sub/fig2.pdf" in listPaths

    def test_malformed_lines_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _fnFakeMirrorDir(tmp_path, "abc123")
        sOutput = (
            "100644 blob aaa    10\tmain.tex\n"
            "this is malformed\n"
            "\n"
            "100644 blob ccc    30\tfig.pdf\n"
        )
        mockResult = MagicMock(returncode=0, stdout=sOutput)
        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            return_value=mockResult,
        ):
            listEntries = flistListMirrorTree("abc123")
        assert len(listEntries) == 2


# ── fbRefreshMirror: clone vs fetch paths ───────────────────────


class TestRefreshMirror:

    def _fmockAskpass(self, tmp_path):
        sAskpass = str(tmp_path / "askpass.py")
        with open(sAskpass, "w") as handle:
            handle.write("#!/usr/bin/env python3\n")
        return sAskpass

    def test_first_clone_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sAskpass = self._fmockAskpass(tmp_path)
        listCapturedCommands = []

        def fnFakeRun(listArgv, **kwargs):
            listCapturedCommands.append(listArgv)
            if "clone" in listArgv:
                os.makedirs(os.path.join(listArgv[-1], ".git"),
                            exist_ok=True)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "deadbeefcafebabe0000000000000000deadbeef\n"
            mock.stderr = ""
            return mock

        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            side_effect=fnFakeRun,
        ), patch(
            "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
            return_value=sAskpass,
        ):
            dictResult = fbRefreshMirror("proj123", "tok")
        assert dictResult["sHeadSha"]
        assert any("clone" in c for c in listCapturedCommands)

    def test_refetch_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sAskpass = self._fmockAskpass(tmp_path)
        _fnFakeMirrorDir(tmp_path, "proj456")
        listCapturedCommands = []

        def fnFakeRun(listArgv, **kwargs):
            listCapturedCommands.append(listArgv)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "c0ffeec0ffeec0ffeec0ffeec0ffeec0ffeec0ff\n"
            mock.stderr = ""
            return mock

        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            side_effect=fnFakeRun,
        ), patch(
            "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
            return_value=sAskpass,
        ):
            dictResult = fbRefreshMirror("proj456", "tok")
        assert dictResult["sHeadSha"]
        assert any("fetch" in c for c in listCapturedCommands)
        assert any("reset" in c for c in listCapturedCommands)

    def test_auth_error_raises_runtime(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sAskpass = self._fmockAskpass(tmp_path)

        def fnFakeRun(listArgv, **kwargs):
            mock = MagicMock()
            mock.returncode = 128
            mock.stdout = ""
            mock.stderr = "fatal: Authentication failed"
            return mock

        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            side_effect=fnFakeRun,
        ), patch(
            "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
            return_value=sAskpass,
        ):
            with pytest.raises(RuntimeError) as excInfo:
                fbRefreshMirror("projX", "tok")
        assert "Authentication failed" in str(excInfo.value)

    def test_not_found_error_raises_runtime(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sAskpass = self._fmockAskpass(tmp_path)

        def fnFakeRun(listArgv, **kwargs):
            mock = MagicMock()
            mock.returncode = 128
            mock.stdout = ""
            mock.stderr = "fatal: repository not found"
            return mock

        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            side_effect=fnFakeRun,
        ), patch(
            "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
            return_value=sAskpass,
        ):
            with pytest.raises(RuntimeError) as excInfo:
                fbRefreshMirror("projY", "tok")
        assert "not found" in str(excInfo.value).lower()

    def test_mirror_dir_mode_0700(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sAskpass = self._fmockAskpass(tmp_path)

        def fnFakeRun(listArgv, **kwargs):
            if "clone" in listArgv:
                os.makedirs(
                    os.path.join(listArgv[-1], ".git"),
                    exist_ok=True,
                )
            mock = MagicMock(returncode=0, stdout="", stderr="")
            return mock

        with patch(
            "vaibify.reproducibility.overleafMirror.subprocess.run",
            side_effect=fnFakeRun,
        ), patch(
            "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
            return_value=sAskpass,
        ):
            fbRefreshMirror("projZ", "tok")
        sRoot = fsGetMirrorRoot()
        iMode = os.stat(sRoot).st_mode & 0o777
        assert iMode == 0o700


# ── fdictDiffAgainstMirror ──────────────────────────────────────


def _fpatchMirrorTree(listEntries):
    """Context-style helper to patch ls-tree output for diff tests."""
    return patch(
        "vaibify.reproducibility.overleafMirror.flistListMirrorTree",
        return_value=listEntries,
    )


class TestDiffAgainstMirror:

    def test_all_new_when_target_dir_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        with _fpatchMirrorTree([]):
            dictResult = fdictDiffAgainstMirror(
                "proj123", {"/local/fig1.pdf": "sha1"},
                "figures",
            )
        assert len(dictResult["listNew"]) == 1
        assert not dictResult["listOverwrite"]
        assert not dictResult["listUnchanged"]

    def test_all_unchanged_when_digests_match(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/fig1.pdf", "sType": "blob",
             "iSize": 10, "sDigest": "sha1"},
        ]
        with _fpatchMirrorTree(listEntries):
            dictResult = fdictDiffAgainstMirror(
                "proj123", {"/local/fig1.pdf": "sha1"},
                "figures",
            )
        assert len(dictResult["listUnchanged"]) == 1

    def test_overwrite_when_digests_differ(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/fig1.pdf", "sType": "blob",
             "iSize": 10, "sDigest": "remote_sha"},
        ]
        with _fpatchMirrorTree(listEntries):
            dictResult = fdictDiffAgainstMirror(
                "proj123", {"/local/fig1.pdf": "local_sha"},
                "figures",
            )
        assert len(dictResult["listOverwrite"]) == 1
        assert dictResult["listOverwrite"][0]["sRemoteDigest"] == (
            "remote_sha"
        )

    def test_mixed_classification(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/a.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sA"},
            {"sPath": "figures/b.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sB_old"},
        ]
        with _fpatchMirrorTree(listEntries):
            dictResult = fdictDiffAgainstMirror(
                "proj123",
                {
                    "/local/a.pdf": "sA",
                    "/local/b.pdf": "sB_new",
                    "/local/c.pdf": "sC",
                },
                "figures",
            )
        assert len(dictResult["listUnchanged"]) == 1
        assert len(dictResult["listOverwrite"]) == 1
        assert len(dictResult["listNew"]) == 1


# ── flistDetectConflicts ────────────────────────────────────────


class TestDetectConflicts:

    def test_no_baseline_yields_no_conflict(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [{"sPath": "figures/a.pdf", "sType": "blob",
                        "iSize": 1, "sDigest": "remote"}]
        with _fpatchMirrorTree(listEntries):
            listConflicts = flistDetectConflicts(
                "proj123", ["/local/a.pdf"], "figures", {},
            )
        assert listConflicts == []

    def test_baseline_matches_yields_no_conflict(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [{"sPath": "figures/a.pdf", "sType": "blob",
                        "iSize": 1, "sDigest": "remote_sha"}]
        dictSync = {
            "/local/a.pdf": {"sOverleafLastPushedDigest": "remote_sha"},
        }
        with _fpatchMirrorTree(listEntries):
            listConflicts = flistDetectConflicts(
                "proj123", ["/local/a.pdf"], "figures", dictSync,
            )
        assert listConflicts == []

    def test_baseline_differs_yields_conflict(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [{"sPath": "figures/a.pdf", "sType": "blob",
                        "iSize": 1, "sDigest": "newer_sha"}]
        dictSync = {
            "/local/a.pdf": {"sOverleafLastPushedDigest": "old_sha"},
        }
        with _fpatchMirrorTree(listEntries):
            listConflicts = flistDetectConflicts(
                "proj123", ["/local/a.pdf"], "figures", dictSync,
            )
        assert len(listConflicts) == 1
        assert listConflicts[0]["sBaselineDigest"] == "old_sha"
        assert listConflicts[0]["sCurrentDigest"] == "newer_sha"

    def test_file_not_in_remote_yields_no_conflict(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        dictSync = {
            "/local/a.pdf": {"sOverleafLastPushedDigest": "old"},
        }
        with _fpatchMirrorTree([]):
            listConflicts = flistDetectConflicts(
                "proj123", ["/local/a.pdf"], "figures", dictSync,
            )
        assert listConflicts == []


# ── fbIsConflict helper ─────────────────────────────────────────


def test_fbIsConflict_variants():
    assert _fbIsConflict("a", "") is False
    assert _fbIsConflict("", "a") is False
    assert _fbIsConflict("a", "a") is False
    assert _fbIsConflict("a", "b") is True


# ── fnDeleteMirror ──────────────────────────────────────────────


class TestDeleteMirror:

    def test_deletes_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sMirror = _fnFakeMirrorDir(tmp_path, "proj123")
        assert os.path.isdir(sMirror)
        fnDeleteMirror("proj123")
        assert not os.path.isdir(sMirror)

    def test_idempotent_on_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        fnDeleteMirror("proj123")
        fnDeleteMirror("proj123")

    def test_rejects_bad_project_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(ValueError):
            fnDeleteMirror("../evil")


# ── _fsRemotePathFor ────────────────────────────────────────────


def test_remote_path_joins_with_basename():
    assert _fsRemotePathFor("/abs/plot.pdf", "figures") == (
        "figures/plot.pdf")


def test_remote_path_no_target_is_basename_only():
    assert _fsRemotePathFor("/abs/plot.pdf", "") == "plot.pdf"


def test_remote_path_nested_directory():
    assert _fsRemotePathFor("/abs/p.pdf", "figures/v2") == (
        "figures/v2/p.pdf")


# ── fsRedactStderr ──────────────────────────────────────────────


def test_redact_stderr_strips_embedded_credentials():
    sStderr = (
        "fatal: unable to access "
        "'https://user:secrettoken@git.overleaf.com/abc': the end"
    )
    sRedacted = fsRedactStderr(sStderr)
    assert "secrettoken" not in sRedacted
    assert "user:" not in sRedacted
    assert "<redacted>" in sRedacted


def test_redact_stderr_replaces_password_lines():
    sStderr = "harmless line\npassword: hunter2\nother line"
    sRedacted = fsRedactStderr(sStderr)
    assert "hunter2" not in sRedacted
    assert "<redacted>" in sRedacted
    assert "harmless line" in sRedacted
    assert "other line" in sRedacted


def test_redact_stderr_empty_returns_empty():
    assert fsRedactStderr("") == ""
    assert fsRedactStderr(None) == ""


def test_redact_stderr_passes_through_innocuous_text():
    sStderr = "fatal: repository not found"
    assert fsRedactStderr(sStderr) == sStderr


def test_refresh_mirror_redacts_stderr_on_failure(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    sLeakyStderr = (
        "fatal: could not read "
        "'https://git:tokenleak@git.overleaf.com/abc'"
    )

    def fnFakeRun(listArgv, **kwargs):
        mock = MagicMock()
        mock.returncode = 128
        mock.stdout = ""
        mock.stderr = sLeakyStderr
        return mock

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        with pytest.raises(RuntimeError) as excInfo:
            fbRefreshMirror("leakyProj", "tok")
    sMessage = str(excInfo.value)
    assert "tokenleak" not in sMessage
    assert "<redacted>" in sMessage


# ── Partial-clone blob-filter flag is passed on every refresh ────


def test_refresh_passes_blob_filter_on_clone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    listCapturedCommands = []

    def fnFakeRun(listArgv, **kwargs):
        listCapturedCommands.append(listArgv)
        if "clone" in listArgv:
            os.makedirs(
                os.path.join(listArgv[-1], ".git"),
                exist_ok=True,
            )
        mock = MagicMock(returncode=0, stdout="sha\n", stderr="")
        return mock

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fbRefreshMirror("blobClone", "tok")
    listCloneCalls = [
        c for c in listCapturedCommands if "clone" in c
    ]
    assert listCloneCalls, "clone command never invoked"
    assert all(
        "--filter=blob:none" in c for c in listCloneCalls
    )


def test_refresh_passes_blob_filter_on_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    _fnFakeMirrorDir(tmp_path, "blobFetch")
    listCapturedCommands = []

    def fnFakeRun(listArgv, **kwargs):
        listCapturedCommands.append(listArgv)
        mock = MagicMock(returncode=0, stdout="sha\n", stderr="")
        return mock

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fbRefreshMirror("blobFetch", "tok")
    listFetchCalls = [c for c in listCapturedCommands if "fetch" in c]
    assert listFetchCalls, "fetch command never invoked"
    assert all(
        "--filter=blob:none" in c for c in listFetchCalls
    )


def test_git_commands_disable_terminal_prompt(tmp_path, monkeypatch):
    """Every git subprocess call passes GIT_TERMINAL_PROMPT=0."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    _fnFakeMirrorDir(tmp_path, "promptOff")
    listCapturedEnvs = []

    def fnFakeRun(listArgv, **kwargs):
        listCapturedEnvs.append(kwargs.get("env") or {})
        mock = MagicMock(returncode=0, stdout="sha\n", stderr="")
        return mock

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fbRefreshMirror("promptOff", "tok")
    assert listCapturedEnvs, "no git commands captured"
    for dictEnv in listCapturedEnvs:
        assert dictEnv.get("GIT_TERMINAL_PROMPT") == "0"


# ── Conflict baseline edge cases ────────────────────────────────


def test_detect_conflicts_sync_status_empty_dict_no_conflict(
    monkeypatch, tmp_path,
):
    """dictSyncStatus = {sLocalPath: {}} (baseline field missing) → no conflict."""
    monkeypatch.setenv("HOME", str(tmp_path))
    listEntries = [{
        "sPath": "figures/a.pdf", "sType": "blob",
        "iSize": 1, "sDigest": "remote_sha",
    }]
    dictSync = {"/local/a.pdf": {}}
    with _fpatchMirrorTree(listEntries):
        listConflicts = flistDetectConflicts(
            "proj123", ["/local/a.pdf"], "figures", dictSync,
        )
    assert listConflicts == []


# ── Missing-mirror safety ───────────────────────────────────────


def test_fsReadMirrorHeadSha_returns_empty_when_mirror_missing(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        overleafMirror, "fsGetMirrorRoot",
        lambda: str(tmp_path),
    )
    sSha = overleafMirror.fsReadMirrorHeadSha("abc123")
    assert sSha == ""


def test_fnRunGit_does_not_raise_on_missing_cwd(
    monkeypatch, tmp_path,
):
    """_fnRunGit must convert FileNotFoundError into returncode=127."""
    sMissing = str(tmp_path / "does-not-exist")
    result = overleafMirror._fnRunGit(
        ["rev-parse", "HEAD"], sCwd=sMissing,
    )
    assert result.returncode == 127
    assert result.stdout == ""
    assert "does-not-exist" in result.stderr


# ── flistDetectCaseCollisions ───────────────────────────────────


class TestDetectCaseCollisions:

    def test_collisions_detected_when_case_differs(
        self, monkeypatch, tmp_path,
    ):
        """Target 'Figures/' clashes with existing 'figures/' entries."""
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/a.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sA"},
            {"sPath": "figures/b.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sB"},
        ]
        with _fpatchMirrorTree(listEntries):
            listCollisions = flistDetectCaseCollisions(
                "proj123",
                ["/local/a.pdf", "/local/b.pdf"],
                "Figures",
            )
        listSortedCollisions = sorted(
            listCollisions, key=lambda d: d["sLocalPath"],
        )
        assert len(listSortedCollisions) == 2
        assert listSortedCollisions[0] == {
            "sLocalPath": "/local/a.pdf",
            "sTypedRemotePath": "Figures/a.pdf",
            "sCanonicalRemotePath": "figures/a.pdf",
        }
        assert listSortedCollisions[1] == {
            "sLocalPath": "/local/b.pdf",
            "sTypedRemotePath": "Figures/b.pdf",
            "sCanonicalRemotePath": "figures/b.pdf",
        }

    def test_no_collisions_when_target_matches_canonical(
        self, monkeypatch, tmp_path,
    ):
        """Exact-case target against existing mirror entries: no collision."""
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/a.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sA"},
        ]
        with _fpatchMirrorTree(listEntries):
            listCollisions = flistDetectCaseCollisions(
                "proj123", ["/local/a.pdf"], "figures",
            )
        assert listCollisions == []

    def test_no_collisions_when_target_directory_is_new(
        self, monkeypatch, tmp_path,
    ):
        """Pushing to a directory absent from the mirror: no collision."""
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/a.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sA"},
        ]
        with _fpatchMirrorTree(listEntries):
            listCollisions = flistDetectCaseCollisions(
                "proj123", ["/local/new.pdf"], "plots",
            )
        assert listCollisions == []

    def test_partial_collisions_mixed(
        self, monkeypatch, tmp_path,
    ):
        """Only files whose typed path collides are reported."""
        monkeypatch.setenv("HOME", str(tmp_path))
        listEntries = [
            {"sPath": "figures/existing.pdf", "sType": "blob",
             "iSize": 1, "sDigest": "sE"},
        ]
        with _fpatchMirrorTree(listEntries):
            listCollisions = flistDetectCaseCollisions(
                "proj123",
                ["/local/existing.pdf", "/local/fresh.pdf"],
                "Figures",
            )
        assert len(listCollisions) == 1
        assert listCollisions[0]["sLocalPath"] == "/local/existing.pdf"

    def test_rejects_bad_project_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(ValueError):
            flistDetectCaseCollisions(
                "../evil", ["/local/a.pdf"], "figures",
            )


# ── Git hardening flags: submodule / symlink / file-transport ────


def test_clone_includes_hardening_flags(tmp_path, monkeypatch):
    """Every clone must disable file-transport submodules and symlinks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    listCaptured = []

    def fnFakeRun(listArgv, **kwargs):
        listCaptured.append(listArgv)
        if "clone" in listArgv:
            os.makedirs(
                os.path.join(listArgv[-1], ".git"),
                exist_ok=True,
            )
        return MagicMock(returncode=0, stdout="sha\n", stderr="")

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fbRefreshMirror("hardenClone", "tok")
    listCloneCalls = [c for c in listCaptured if "clone" in c]
    assert listCloneCalls
    sJoined = " ".join(str(s) for s in listCloneCalls[0])
    assert "protocol.file.allow=never" in sJoined
    assert "core.symlinks=false" in sJoined
    assert "submodule.recurse=false" in sJoined
    assert "--no-recurse-submodules" in sJoined


def test_fetch_and_reset_include_hardening_flags(tmp_path, monkeypatch):
    """Refetch path must pass hardening to fetch and reset."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    sMirrorDir = os.path.join(
        str(tmp_path), ".vaibify", "overleaf-mirrors", "hardenFetch",
    )
    os.makedirs(os.path.join(sMirrorDir, ".git"), exist_ok=True)
    listCaptured = []

    def fnFakeRun(listArgv, **kwargs):
        listCaptured.append(listArgv)
        return MagicMock(returncode=0, stdout="sha\n", stderr="")

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fbRefreshMirror("hardenFetch", "tok")
    listFetchCalls = [c for c in listCaptured if "fetch" in c]
    listResetCalls = [c for c in listCaptured if "reset" in c]
    assert listFetchCalls
    assert listResetCalls
    for listCall in listFetchCalls + listResetCalls:
        sJoined = " ".join(str(s) for s in listCall)
        assert "protocol.file.allow=never" in sJoined
        assert "core.symlinks=false" in sJoined
