"""Tests for host-side GitHub authentication helpers."""

import os
import stat
from unittest.mock import patch

import pytest

from vaibify.reproducibility import githubAuth


# ----------------------------------------------------------------------
# fnValidateOwnerRepo
# ----------------------------------------------------------------------


def test_fnValidateOwnerRepo_accepts_simple_names():
    githubAuth.fnValidateOwnerRepo("torvalds", "linux")


def test_fnValidateOwnerRepo_accepts_hyphens_and_underscores():
    githubAuth.fnValidateOwnerRepo("my-org", "my_repo.name")


def test_fnValidateOwnerRepo_rejects_empty_owner():
    with pytest.raises(ValueError):
        githubAuth.fnValidateOwnerRepo("", "repo")


def test_fnValidateOwnerRepo_rejects_empty_repo():
    with pytest.raises(ValueError):
        githubAuth.fnValidateOwnerRepo("owner", "")


def test_fnValidateOwnerRepo_rejects_path_separator():
    with pytest.raises(ValueError):
        githubAuth.fnValidateOwnerRepo("owner/with/slash", "repo")


def test_fnValidateOwnerRepo_rejects_shell_metacharacters():
    with pytest.raises(ValueError):
        githubAuth.fnValidateOwnerRepo("owner;rm -rf", "repo")


# ----------------------------------------------------------------------
# fsKeyringSlotFor
# ----------------------------------------------------------------------


def test_fsKeyringSlotFor_formats_namespaced_key():
    assert githubAuth.fsKeyringSlotFor("RoryBarnes", "vaibify") == (
        "github_token:RoryBarnes/vaibify"
    )


def test_fsKeyringSlotFor_rejects_invalid():
    with pytest.raises(ValueError):
        githubAuth.fsKeyringSlotFor("bad name", "repo")


# ----------------------------------------------------------------------
# fsKeyringSlotFromRemoteUrl
# ----------------------------------------------------------------------


def test_fsKeyringSlotFromRemoteUrl_https_with_dot_git():
    sSlot = githubAuth.fsKeyringSlotFromRemoteUrl(
        "https://github.com/RoryBarnes/vaibify.git"
    )
    assert sSlot == "github_token:RoryBarnes/vaibify"


def test_fsKeyringSlotFromRemoteUrl_https_without_dot_git():
    sSlot = githubAuth.fsKeyringSlotFromRemoteUrl(
        "https://github.com/RoryBarnes/vaibify"
    )
    assert sSlot == "github_token:RoryBarnes/vaibify"


def test_fsKeyringSlotFromRemoteUrl_ssh_form():
    sSlot = githubAuth.fsKeyringSlotFromRemoteUrl(
        "git@github.com:RoryBarnes/vaibify.git"
    )
    assert sSlot == "github_token:RoryBarnes/vaibify"


def test_fsKeyringSlotFromRemoteUrl_empty_on_nonsense():
    assert githubAuth.fsKeyringSlotFromRemoteUrl("") == ""
    assert githubAuth.fsKeyringSlotFromRemoteUrl("not a url") == ""


def test_fsKeyringSlotFromRemoteUrl_empty_on_invalid_owner():
    assert githubAuth.fsKeyringSlotFromRemoteUrl(
        "https://github.com/bad name/repo.git"
    ) == ""


# ----------------------------------------------------------------------
# fsWriteAskpassScript
# ----------------------------------------------------------------------


def test_fsWriteAskpassScript_is_executable_user_only():
    sPath = githubAuth.fsWriteAskpassScript(
        "github_token:example/repo"
    )
    try:
        iMode = os.stat(sPath).st_mode & 0o777
        assert iMode == 0o700
    finally:
        os.unlink(sPath)


def test_fsWriteAskpassScript_embeds_slot():
    sPath = githubAuth.fsWriteAskpassScript(
        "github_token:some/slot"
    )
    try:
        with open(sPath, "r") as handle:
            sContent = handle.read()
        assert "github_token:some/slot" in sContent
    finally:
        os.unlink(sPath)


def test_fsWriteAskpassScript_includes_gh_fallback():
    sPath = githubAuth.fsWriteAskpassScript("")
    try:
        with open(sPath, "r") as handle:
            sContent = handle.read()
        assert "gh_auth" in sContent
    finally:
        os.unlink(sPath)


def test_fsWriteAskpassScript_does_not_embed_token():
    """The askpass file must never contain a literal secret."""
    sPath = githubAuth.fsWriteAskpassScript("github_token:x/y")
    try:
        with open(sPath, "r") as handle:
            sContent = handle.read()
        assert "fsRetrieveSecret" in sContent
        assert "TOKEN=" not in sContent.upper()
    finally:
        os.unlink(sPath)


# ----------------------------------------------------------------------
# Dispatcher hardening flags
# ----------------------------------------------------------------------


def test_dispatcher_github_push_uses_hardening_flags():
    from vaibify.gui import syncDispatcher

    class _FakeDocker:
        def __init__(self):
            self.listCommands = []

        def ftResultExecuteCommand(self, sId, sCmd):
            self.listCommands.append(sCmd)
            return (0, "abcd123\n")

    docker = _FakeDocker()
    syncDispatcher.ftResultPushToGithub(
        docker, "container", ["a.py"], "msg", "/workspace",
    )
    sCmd = docker.listCommands[0]
    assert "protocol.file.allow=never" in sCmd
    assert "core.symlinks=false" in sCmd
    assert "submodule.recurse=false" in sCmd


def test_dispatcher_github_staged_push_uses_hardening_flags():
    from vaibify.gui import syncDispatcher

    class _FakeDocker:
        def __init__(self):
            self.listCommands = []

        def ftResultExecuteCommand(self, sId, sCmd):
            self.listCommands.append(sCmd)
            return (0, "abcd123\n")

    docker = _FakeDocker()
    syncDispatcher.ftResultPushStagedToGithub(
        docker, "container", "msg", "/workspace",
    )
    sCmd = docker.listCommands[0]
    assert "protocol.file.allow=never" in sCmd
    assert "core.symlinks=false" in sCmd


# ----------------------------------------------------------------------
# fsKeyringSlotFromRemoteUrl: defensive ValueError catch
# ----------------------------------------------------------------------


def test_fsKeyringSlotFromRemoteUrl_catches_validate_failure():
    """Belt-and-suspenders: if fsKeyringSlotFor raises, return ''."""
    with patch.object(
        githubAuth, "fsKeyringSlotFor", side_effect=ValueError("synthetic")
    ):
        assert githubAuth.fsKeyringSlotFromRemoteUrl(
            "https://github.com/owner/repo.git"
        ) == ""


# ----------------------------------------------------------------------
# fsResolveToken: keyring/gh fallback paths
# ----------------------------------------------------------------------


def test_fsResolveToken_returns_keyring_token_when_present():
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        return_value="kr-token",
    ):
        assert githubAuth.fsResolveToken(
            "github_token:o/r"
        ) == "kr-token"


def test_fsResolveToken_falls_back_to_gh_when_keyring_raises():
    def _fsRetrieve(sName, sMethod):
        if sMethod == "keyring":
            raise RuntimeError("keyring locked")
        return "gh-token"

    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        side_effect=_fsRetrieve,
    ):
        assert githubAuth.fsResolveToken(
            "github_token:o/r"
        ) == "gh-token"


def test_fsResolveToken_uses_gh_when_keyring_slot_empty():
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=False,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        return_value="gh-token",
    ):
        assert githubAuth.fsResolveToken("") == "gh-token"


def test_fsResolveToken_returns_empty_when_both_unavailable():
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=False,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        side_effect=RuntimeError("gh not installed"),
    ):
        assert githubAuth.fsResolveToken("github_token:o/r") == ""


def test_dispatcher_github_add_file_uses_hardening_flags():
    from vaibify.gui import syncDispatcher

    class _FakeDocker:
        def __init__(self):
            self.listCommands = []

        def ftResultExecuteCommand(self, sId, sCmd):
            self.listCommands.append(sCmd)
            return (0, "abcd123\n")

    docker = _FakeDocker()
    syncDispatcher.ftResultAddFileToGithub(
        docker, "container", "a.py", "msg", "/workspace",
    )
    sCmd = docker.listCommands[0]
    assert "protocol.file.allow=never" in sCmd
    assert "submodule.recurse=false" in sCmd
