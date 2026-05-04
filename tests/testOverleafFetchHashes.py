"""Tests for ``fdictFetchRemoteHashes`` in vaibify.reproducibility.overleafMirror.

The new on-demand hash-fetch path is the AICS L3 hook: it performs a
shallow but full-blob clone of an Overleaf project, hashes selected
files, and unconditionally tears the working copy down. All git calls
are mocked; the cloned tree is materialised on the host filesystem by
the mock so the hashing path can be exercised end-to-end without
network access.
"""

import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import overleafMirror
from vaibify.reproducibility.overleafMirror import (
    fbRefreshMirror,
    fdictFetchRemoteHashes,
)


# ── Helpers ─────────────────────────────────────────────────────


def _fsSha256OfBytes(baContent):
    """Return SHA-256 hex of a byte string (independent of module under test)."""
    hasher = hashlib.sha256()
    hasher.update(baContent)
    return hasher.hexdigest()


def _fnWriteAskpassFile(tmp_path):
    """Materialise an askpass-shaped temp file used by the mocks."""
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handleFile:
        handleFile.write("#!/usr/bin/env python3\n")
    return sAskpass


def _fnMakeCloneTreeFactory(dictRelPathToBytes):
    """Return a fake ``subprocess.run`` that materialises a clone tree.

    The clone target is the last argv element of the git ``clone``
    command; the factory walks ``dictRelPathToBytes`` and writes each
    file inside that directory so subsequent open()s see real bytes.
    """
    def fnFakeRun(listArgv, **kwargs):
        if "clone" in listArgv:
            sCloneTarget = listArgv[-1]
            os.makedirs(sCloneTarget, exist_ok=True)
            os.makedirs(os.path.join(sCloneTarget, ".git"), exist_ok=True)
            for sRelPath, baContent in dictRelPathToBytes.items():
                sFullPath = os.path.join(sCloneTarget, sRelPath)
                os.makedirs(os.path.dirname(sFullPath) or sCloneTarget,
                            exist_ok=True)
                with open(sFullPath, "wb") as handleFile:
                    handleFile.write(baContent)
        return MagicMock(returncode=0, stdout="", stderr="")
    return fnFakeRun


# ── Happy path ──────────────────────────────────────────────────


def test_fdictFetchRemoteHashes_happy_path(tmp_path):
    """All three requested paths exist; each gets the correct SHA-256."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    dictBytes = {
        "main.tex": b"\\documentclass{article}\n",
        "figures/fig1.pdf": b"%PDF-1.4 fake bytes",
        "figures/sub/fig2.pdf": bytes(range(256)),
    }
    fnFakeRun = _fnMakeCloneTreeFactory(dictBytes)
    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        dictHashes = fdictFetchRemoteHashes(
            "happyProj", list(dictBytes.keys()),
        )
    for sRelPath, baContent in dictBytes.items():
        assert dictHashes[sRelPath] == _fsSha256OfBytes(baContent)


# ── Missing file → None ─────────────────────────────────────────


def test_fdictFetchRemoteHashes_missing_file_yields_none(tmp_path):
    """A path absent from the cloned tree records ``None``."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    dictBytes = {"main.tex": b"present"}
    fnFakeRun = _fnMakeCloneTreeFactory(dictBytes)
    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        dictHashes = fdictFetchRemoteHashes(
            "missProj", ["main.tex", "figures/missing.pdf"],
        )
    assert dictHashes["main.tex"] == _fsSha256OfBytes(b"present")
    assert dictHashes["figures/missing.pdf"] is None


# ── Tempdir cleanup on success ──────────────────────────────────


def test_fdictFetchRemoteHashes_tempdir_removed_after_success(tmp_path):
    """The tempdir created for the clone is gone after a clean return."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    listObservedTempDirs = []
    dictBytes = {"main.tex": b"x"}

    fnInnerRun = _fnMakeCloneTreeFactory(dictBytes)

    def fnFakeRun(listArgv, **kwargs):
        if "clone" in listArgv:
            listObservedTempDirs.append(os.path.dirname(listArgv[-1]))
        return fnInnerRun(listArgv, **kwargs)

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fdictFetchRemoteHashes("cleanProj", ["main.tex"])
    assert listObservedTempDirs, "clone never invoked; mock setup wrong"
    sTempDir = listObservedTempDirs[0]
    assert not os.path.exists(sTempDir), (
        "tempdir survived after successful fetch"
    )


# ── Tempdir cleanup on error ────────────────────────────────────


def test_fdictFetchRemoteHashes_tempdir_removed_on_error(tmp_path):
    """Tempdir is removed even when ``subprocess.run`` itself raises."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    listObservedTempDirs = []

    def fnFakeRun(listArgv, **kwargs):
        if "clone" in listArgv:
            listObservedTempDirs.append(os.path.dirname(listArgv[-1]))
        raise OSError("simulated git failure")

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        with pytest.raises(OSError):
            fdictFetchRemoteHashes("errProj", ["main.tex"])
    assert listObservedTempDirs, "clone command never reached subprocess.run"
    sTempDir = listObservedTempDirs[0]
    assert not os.path.exists(sTempDir), (
        "tempdir survived after subprocess.run raised"
    )


# ── Token / credential redaction in raised errors ───────────────


def test_fdictFetchRemoteHashes_redacts_credentials_in_error(tmp_path):
    """Embedded ``user:token@`` URLs must not appear in the raised message."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    sLeakyStderr = (
        "fatal: unable to access "
        "'https://git:supersecret@git.overleaf.com/leakyProj'"
    )

    def fnFakeRun(listArgv, **kwargs):
        return MagicMock(returncode=128, stdout="", stderr=sLeakyStderr)

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        with pytest.raises(RuntimeError) as excInfo:
            fdictFetchRemoteHashes("leakyProj", ["main.tex"])
    sMessage = str(excInfo.value)
    assert "supersecret" not in sMessage
    assert "git:supersecret" not in sMessage
    assert "<redacted>" in sMessage


# ── Empty list short-circuits before any clone ──────────────────


def test_fdictFetchRemoteHashes_empty_list_skips_clone(tmp_path):
    """Empty ``listRelPaths`` returns ``{}`` and never calls subprocess."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    listCalls = []

    def fnFakeRun(listArgv, **kwargs):
        listCalls.append(listArgv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        dictResult = fdictFetchRemoteHashes("emptyProj", [])
    assert dictResult == {}
    assert listCalls == [], "subprocess.run was invoked despite empty list"


# ── Hardening flags applied to the on-demand clone ──────────────


def test_fdictFetchRemoteHashes_passes_hardening_config(tmp_path):
    """Each git invocation must include LIST_GIT_HARDENING_CONFIG flags."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    listCaptured = []
    dictBytes = {"main.tex": b"x"}
    fnInnerRun = _fnMakeCloneTreeFactory(dictBytes)

    def fnFakeRun(listArgv, **kwargs):
        listCaptured.append(listArgv)
        return fnInnerRun(listArgv, **kwargs)

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fdictFetchRemoteHashes("hardenProj", ["main.tex"])
    assert listCaptured
    listCloneCalls = [c for c in listCaptured if "clone" in c]
    assert listCloneCalls, "no clone command captured"
    sJoined = " ".join(str(s) for s in listCloneCalls[0])
    assert "protocol.file.allow=never" in sJoined
    assert "protocol.allow=user" in sJoined
    assert "core.symlinks=false" in sJoined
    assert "submodule.recurse=false" in sJoined
    assert "--no-recurse-submodules" in sJoined


# ── On-demand path uses FULL clone (no --filter=blob:none) ──────


def test_fdictFetchRemoteHashes_does_not_pass_blob_filter(tmp_path):
    """The on-demand path must fetch real bytes, so blob filtering is off."""
    sAskpass = _fnWriteAskpassFile(tmp_path)
    listCaptured = []
    fnInnerRun = _fnMakeCloneTreeFactory({"main.tex": b"x"})

    def fnFakeRun(listArgv, **kwargs):
        listCaptured.append(listArgv)
        return fnInnerRun(listArgv, **kwargs)

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fdictFetchRemoteHashes("fullProj", ["main.tex"])
    assert listCaptured
    listCloneCalls = [c for c in listCaptured if "clone" in c]
    assert listCloneCalls
    for listCall in listCloneCalls:
        assert "--filter=blob:none" not in listCall
        assert "--depth=1" in listCall


# ── Existing per-poll path still uses the partial-clone filter ──


def test_fbRefreshMirror_still_uses_blob_filter(tmp_path, monkeypatch):
    """Sanity check: the existing cheap-poll behavior is preserved."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = _fnWriteAskpassFile(tmp_path)
    listCaptured = []

    def fnFakeRun(listArgv, **kwargs):
        listCaptured.append(listArgv)
        if "clone" in listArgv:
            os.makedirs(
                os.path.join(listArgv[-1], ".git"), exist_ok=True,
            )
        return MagicMock(returncode=0, stdout="sha\n", stderr="")

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        fbRefreshMirror("pollProj", "tok")
    listCloneCalls = [c for c in listCaptured if "clone" in c]
    assert listCloneCalls, "fbRefreshMirror failed to invoke clone"
    assert all("--filter=blob:none" in c for c in listCloneCalls)


# ── New symbol exported from the module ─────────────────────────


def test_fdictFetchRemoteHashes_in_module_all():
    """The new public function is part of ``__all__``."""
    assert "fdictFetchRemoteHashes" in overleafMirror.__all__


# ── _fnRemovePath swallows OSError (Wave-1 hardening regression) ─


def test_remove_path_tolerates_permission_error(tmp_path):
    """``_fnRemovePath`` must not propagate ``PermissionError``.

    Regression test for the Wave-1 hardening: cleanup helpers (askpass
    files, tempdir entries) call ``_fnRemovePath`` from ``finally``
    blocks. A leaked exception there would mask the original error
    and leak sibling resources.
    """
    pathTarget = tmp_path / "askpass.py"
    pathTarget.write_text("#!/usr/bin/env python3\n")
    with patch(
        "vaibify.reproducibility.overleafMirror.os.remove",
        side_effect=PermissionError("simulated EACCES"),
    ):
        overleafMirror._fnRemovePath(str(pathTarget))
