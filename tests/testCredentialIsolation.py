"""Tests for git credential isolation on vaibify-managed remotes.

The host's global gitconfig usually wires an ambient credential helper
(macOS ``osxkeychain``). Git consults configured helpers BEFORE any
``GIT_ASKPASS`` script, so an ambient keychain entry for
``git.overleaf.com`` silently answers instead of the vaibify-managed
token. The observed failure: mirror clones and verifies authenticate
while the "connected?" probe of the managed slot honestly reports
disconnected — and a live validation of a newly entered token
"validates" the ambient credential rather than the token being stored.
Every vaibify git call that authenticates with a managed credential
must therefore reset the inherited helper list.
"""

from unittest.mock import MagicMock, patch

from vaibify.reproducibility.gitHardening import (
    LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
)


def test_isolation_config_resets_ambient_helpers():
    """The reset flag is the empty-value form that clears the list."""
    assert LIST_GIT_CREDENTIAL_ISOLATION_CONFIG == [
        "-c", "credential.helper=",
    ]


def test_overleaf_mirror_git_runner_prepends_isolation():
    """Every overleafMirror git call carries the helper reset."""
    from vaibify.reproducibility import overleafMirror
    mockRun = MagicMock()
    mockRun.return_value.returncode = 0
    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        mockRun,
    ):
        overleafMirror._fnRunGit(["status"])
    listCommand = mockRun.call_args[0][0]
    assert listCommand[:3] == ["git", "-c", "credential.helper="]


def test_host_ls_remote_validation_carries_isolation():
    """The connect-flow validation must test ONLY the entered token.

    Without the reset, the validation authenticates via the ambient
    helper and a mistyped token is stored as "valid", failing later
    in the container where no ambient helper exists.
    """
    from vaibify.gui import syncDispatcher
    mockRun = MagicMock()
    mockRun.return_value.returncode = 0
    mockRun.return_value.stderr = ""
    with patch(
        "vaibify.gui.syncDispatcher.subprocess.run", mockRun,
    ):
        bSuccess, _ = syncDispatcher._ftRunHostLsRemote(
            "abcdef123456789012345678", "/tmp/askpass",
        )
    assert bSuccess is True
    listCommand = mockRun.call_args[0][0]
    assert listCommand[0] == "git"
    assert ["-c", "credential.helper="] == listCommand[1:3]
    assert "ls-remote" in listCommand


def test_credential_helper_args_reset_before_adding_helper():
    """The one-shot helper args reset ambient helpers FIRST.

    ``-c`` flags apply in order: the reset must precede the scoped
    helper so the supplied token is the only credential in play.
    """
    from vaibify.reproducibility.overleafSync import (
        flistBuildCredentialHelperArgs,
    )
    listArgs = flistBuildCredentialHelperArgs("/tmp/tokenfile")
    assert listArgs[:2] == ["-c", "credential.helper="]
    assert listArgs[2] == "-c"
    assert listArgs[3].startswith(
        "credential.https://git.overleaf.com.helper=",
    )


def test_container_credential_copy_never_echoes_the_value():
    """The in-container copy command must not print the secret.

    The whole point of copying inside one in-container python process
    is that the token never crosses the docker-exec boundary — the
    only prints allowed are the 'copied'/'missing' markers.
    """
    from vaibify.gui.syncDispatcher import fbCopyCredentialInContainer
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "copied")
    bResult = fbCopyCredentialInContainer(
        mockDocker, "cid",
        "zenodo_token_sandbox", "zenodo_token_sandbox_backup",
    )
    assert bResult is True
    sCommand = mockDocker.ftResultExecuteCommand.call_args[0][1]
    assert "print(v" not in sCommand
    assert "'copied'" in sCommand and "'missing'" in sCommand


def test_container_credential_copy_reports_missing_source():
    """A missing source entry returns False without failing."""
    from vaibify.gui.syncDispatcher import fbCopyCredentialInContainer
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "missing")
    assert fbCopyCredentialInContainer(
        mockDocker, "cid",
        "zenodo_token_sandbox", "zenodo_token_sandbox_backup",
    ) is False


def test_container_credential_copy_rejects_unknown_slots():
    """Slot names outside the allowlist are rejected before any exec."""
    import pytest
    from vaibify.gui.syncDispatcher import fbCopyCredentialInContainer
    mockDocker = MagicMock()
    with pytest.raises(ValueError, match="Invalid token name"):
        fbCopyCredentialInContainer(
            mockDocker, "cid", "zenodo_token_sandbox", "evil_slot",
        )
    mockDocker.ftResultExecuteCommand.assert_not_called()


def test_hermetic_keyring_guardrail_is_active(fixtureHermeticKeyring):
    """The suite-wide fake keyring intercepts real secretManager calls.

    Self-test of the conftest guardrail: an un-mocked store/probe/
    delete round trip must land in the in-memory fake, never in the
    researcher's OS keychain.
    """
    from vaibify.config.secretManager import (
        fbSecretExists,
        fnDeleteSecret,
        fnStoreSecret,
    )
    assert fbSecretExists("overleaf_token", "keyring") is False
    fnStoreSecret("overleaf_token", "hermetic-value", "keyring")
    assert fixtureHermeticKeyring.dictStore[
        ("vaibify", "overleaf_token")] == "hermetic-value"
    assert fbSecretExists("overleaf_token", "keyring") is True
    fnDeleteSecret("overleaf_token", "keyring")
    assert fbSecretExists("overleaf_token", "keyring") is False
