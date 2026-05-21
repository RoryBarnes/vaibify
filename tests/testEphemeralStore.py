"""Tests for the shared ephemeral-file root (audit M2)."""

import os
import stat

from vaibify.config.ephemeralStore import fsGetEphemeralRoot


def test_root_lives_under_user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    assert sRoot.startswith(str(tmp_path))
    assert sRoot.endswith(os.path.join(".vaibify", "tmp"))


def test_root_is_mode_0700(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    iMode = stat.S_IMODE(os.stat(sRoot).st_mode)
    assert iMode == 0o700


def test_root_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sFirst = fsGetEphemeralRoot()
    sSecond = fsGetEphemeralRoot()
    assert sFirst == sSecond


def test_secret_manager_temp_dir_uses_ephemeral_root(monkeypatch, tmp_path):
    """secretManager._fsGetTempDirectory routes through the shared root."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaibify.config.secretManager import _fsGetTempDirectory
    sDir = _fsGetTempDirectory()
    assert sDir.startswith(str(tmp_path))
    assert sDir.endswith(os.path.join(".vaibify", "tmp"))


def test_askpass_helper_writes_under_ephemeral_root(monkeypatch, tmp_path):
    """askpassHelper drops scripts under ~/.vaibify/tmp on Linux too."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaibify.reproducibility.askpassHelper import (
        fsWriteExecutableScript,
    )
    sScriptPath = fsWriteExecutableScript(
        "print('ok')\n", "vc_test_askpass_",
    )
    try:
        assert sScriptPath.startswith(str(tmp_path))
    finally:
        os.remove(sScriptPath)


def test_overleaf_write_token_file_uses_ephemeral_root(monkeypatch, tmp_path):
    """overleafSync._fsWriteTokenFile drops the token under ~/.vaibify/tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaibify.reproducibility.overleafSync import _fsWriteTokenFile
    sTokenPath = _fsWriteTokenFile("ghp_fake")
    try:
        assert sTokenPath.startswith(str(tmp_path))
    finally:
        os.remove(sTokenPath)
