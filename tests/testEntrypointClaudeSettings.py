"""Tests for the Claude-settings bash helpers in docker/entrypoint.sh."""

import json
import os
import subprocess

import pytest


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
    )
)


def _fsReadEntrypoint():
    with open(_S_ENTRYPOINT) as fileHandle:
        return fileHandle.read()


def test_auto_update_function_is_defined():
    """Confirm fnConfigureClaudeAutoUpdate exists and sets autoUpdates."""
    sContent = _fsReadEntrypoint()
    assert "fnConfigureClaudeAutoUpdate()" in sContent
    assert "autoUpdates" in sContent
    assert "VAIBIFY_CLAUDE_AUTO_UPDATE" in sContent


def _fsMainBlock(sContent):
    """Return the text of the main if-BASH_SOURCE block."""
    iMainStart = sContent.find('"${BASH_SOURCE[0]}" == "${0}"')
    assert iMainStart != -1
    return sContent[iMainStart:]


def _fsStartupSequenceBody(sContent):
    """Return the body of the fnRunStartupSequence function."""
    iFuncStart = sContent.find("fnRunStartupSequence()")
    assert iFuncStart != -1
    return sContent[iFuncStart:iFuncStart + 1200]


def test_startup_sequence_invokes_auto_update_inside_claude_guard():
    """fnConfigureClaudeAutoUpdate must be guarded by command -v claude."""
    sContent = _fsReadEntrypoint()
    sBody = _fsStartupSequenceBody(sContent)
    iGuardStart = sBody.find("command -v claude")
    assert iGuardStart != -1
    sAfterGuard = sBody[iGuardStart:iGuardStart + 400]
    assert "fnConfigureClaudeAutoUpdate" in sAfterGuard


def test_persist_runs_before_theme_in_startup_sequence():
    """Symlink must be established before theme writes settings.json."""
    sContent = _fsReadEntrypoint()
    sBody = _fsStartupSequenceBody(sContent)
    iGuardStart = sBody.find("command -v claude")
    sAfterGuard = sBody[iGuardStart:iGuardStart + 400]
    iPersist = sAfterGuard.find("fnPersistClaudeConfig")
    iTheme = sAfterGuard.find("fnConfigureClaudeTheme")
    iAutoUpdate = sAfterGuard.find("fnConfigureClaudeAutoUpdate")
    assert 0 <= iPersist < iTheme < iAutoUpdate


def test_main_block_invokes_startup_sequence():
    """The BASH_SOURCE guard must call fnRunStartupSequence."""
    sContent = _fsReadEntrypoint()
    sMain = _fsMainBlock(sContent)
    assert "fnRunStartupSequence" in sMain


def test_theme_function_uses_container_user_home():
    """Theme must write to /home/${CONTAINER_USER}, not ${HOME}."""
    sContent = _fsReadEntrypoint()
    iFunc = sContent.find("fnConfigureClaudeTheme()")
    assert iFunc != -1
    sBody = sContent[iFunc:iFunc + 400]
    assert "/home/${CONTAINER_USER}/.claude" in sBody


def test_auto_update_function_uses_container_user_home():
    """Auto-update must also write under /home/${CONTAINER_USER}."""
    sContent = _fsReadEntrypoint()
    iFunc = sContent.find("fnConfigureClaudeAutoUpdate()")
    assert iFunc != -1
    sBody = sContent[iFunc:iFunc + 600]
    assert "/home/${CONTAINER_USER}/.claude" in sBody


def _ftRunMerge(sSettingsPath, sFlag):
    """Run the same JSON merge the entrypoint performs."""
    sScript = """
import json, os
sSettings = os.environ["VAIB_SETTINGS"]
bAutoUpdate = os.environ["VAIB_FLAG"] == "true"
with open(sSettings) as fileHandle:
    dictContents = json.load(fileHandle)
dictContents["autoUpdates"] = bAutoUpdate
with open(sSettings, "w") as fileHandle:
    json.dump(dictContents, fileHandle, indent=2)
"""
    dictEnv = os.environ.copy()
    dictEnv["VAIB_SETTINGS"] = sSettingsPath
    dictEnv["VAIB_FLAG"] = sFlag
    subprocess.run(
        ["python3", "-c", sScript],
        env=dictEnv, check=True,
    )
    with open(sSettingsPath) as fileHandle:
        return json.load(fileHandle)


def test_merge_sets_auto_updates_true(tmp_path):
    sPath = str(tmp_path / "settings.json")
    with open(sPath, "w") as fileHandle:
        json.dump({}, fileHandle)
    dictResult = _ftRunMerge(sPath, "true")
    assert dictResult["autoUpdates"] is True


def test_merge_sets_auto_updates_false(tmp_path):
    sPath = str(tmp_path / "settings.json")
    with open(sPath, "w") as fileHandle:
        json.dump({}, fileHandle)
    dictResult = _ftRunMerge(sPath, "false")
    assert dictResult["autoUpdates"] is False


def test_merge_preserves_existing_keys(tmp_path):
    sPath = str(tmp_path / "settings.json")
    with open(sPath, "w") as fileHandle:
        json.dump({"theme": "dark", "other": 42}, fileHandle)
    dictResult = _ftRunMerge(sPath, "false")
    assert dictResult["theme"] == "dark"
    assert dictResult["other"] == 42
    assert dictResult["autoUpdates"] is False
