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

import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility.gitHardening import (
    LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
    LIST_GIT_HARDENING_CONFIG,
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
        overleafMirror._fprocessRunGit(["status"])
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


def _fnRunHostLsRemoteCapturingTheCall(monkeypatch):
    from vaibify.gui import syncDispatcher
    mockRun = MagicMock()
    mockRun.return_value.returncode = 0
    mockRun.return_value.stderr = ""
    with patch("vaibify.gui.syncDispatcher.subprocess.run", mockRun):
        syncDispatcher._ftRunHostLsRemote(
            "abcdef123456789012345678", "/tmp/askpass")
    return mockRun.call_args


@pytest.mark.falsification
def test_host_ls_remote_validation_carries_the_hardening_list_after_the_reset(
    monkeypatch,
):
    """The reset comes first, then the hardening, then the question.

    Kills: dropping the hardening list from the validation's command.
    """
    callArguments = _fnRunHostLsRemoteCapturingTheCall(monkeypatch)
    listCommand = callArguments[0][0]
    iHardening = listCommand.index(LIST_GIT_HARDENING_CONFIG[1]) - 1
    assert listCommand[:3] == ["git", "-c", "credential.helper="]
    assert listCommand[iHardening:iHardening + len(
        LIST_GIT_HARDENING_CONFIG)] == LIST_GIT_HARDENING_CONFIG
    assert listCommand[-3:] == ["--", listCommand[-2], "HEAD"]
    assert listCommand.index("ls-remote") > iHardening


@pytest.mark.falsification
def test_host_ls_remote_validation_runs_from_an_empty_directory(
    monkeypatch, tmp_path,
):
    """Kills: running the validation in the hub's working directory."""
    monkeypatch.chdir(tmp_path)
    callArguments = _fnRunHostLsRemoteCapturingTheCall(monkeypatch)
    assert os.path.realpath(callArguments[1]["cwd"]) != os.path.realpath(
        str(tmp_path))


@pytest.mark.falsification
def test_host_ls_remote_validation_keeps_its_token_and_drops_the_repository(
    monkeypatch,
):
    """The askpass helper survives; an inherited ``GIT_DIR`` does not.

    Kills: handing the validation the unscrubbed process environment.
    """
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    dictEnvironment = _fnRunHostLsRemoteCapturingTheCall(
        monkeypatch)[1]["env"]
    assert "GIT_DIR" not in dictEnvironment
    assert dictEnvironment["GIT_ASKPASS"] == "/tmp/askpass"
    assert dictEnvironment["GIT_TERMINAL_PROMPT"] == "0"


def test_every_host_site_that_supplies_a_git_token_resets_the_helper_list():
    """The inventory of host paths that carry vaibify's own credential.

    A host git call that authenticates with a token vaibify manages
    (``GIT_ASKPASS`` set to a vaibify helper) must reset the inherited
    credential-helper list first, or an ambient keychain entry answers
    in its place. Today that is the Overleaf mirror funnel and the
    Overleaf validation; GitHub's host paths authenticate through the
    researcher's own credentials and are deliberately left alone. A new
    site is a decision, so it fails here until it is listed and reset.
    """
    pathPackage = Path(__file__).resolve().parent.parent / "vaibify"
    setSites = set()
    for pathModule in pathPackage.rglob("*.py"):
        sSource = pathModule.read_text(encoding="utf-8")
        if re.search(r"\[\"GIT_ASKPASS\"\]\s*=", sSource):
            setSites.add(str(pathModule.relative_to(pathPackage)))
            assert "LIST_GIT_CREDENTIAL_ISOLATION_CONFIG" in sSource, (
                f"{pathModule.name} supplies GIT_ASKPASS without the "
                "credential-helper reset")
    assert setSites == {
        "gui/syncDispatcher.py", "reproducibility/overleafMirror.py",
    }


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


def test_failed_snapshot_restore_reports_false_and_keeps_the_snapshot():
    """A crashed snapshot copy must never claim the token was restored.

    When copying the backup slot over the failed token raises, the
    helper must return False (the caller words the researcher-facing
    disposition from it) and must NOT delete the backup slot — the
    snapshot is the only surviving copy of the previous token.
    """
    from vaibify.gui.routes.syncRoutes import _fbRestoreContainerSnapshot
    mockDispatcher = MagicMock()
    mockDispatcher.fbCopyCredentialForProject.side_effect = RuntimeError(
        "exec failed",
    )
    bRestored = _fbRestoreContainerSnapshot(
        mockDispatcher, {"docker": MagicMock()}, "cid",
        ("zenodo_token_sandbox", "zenodo_token_sandbox_backup"),
    )
    assert bRestored is False
    mockDispatcher.fnDeleteCredentialForProject.assert_not_called()


def test_overleaf_rollback_restores_the_host_keyring_token():
    """An Overleaf rollback with a captured token restores via keyring.

    The service dispatch is string equality: for ``overleaf`` with a
    previous token the helper must store that token back in the host
    keyring and report the restored disposition — never fall through
    to the container-snapshot lane (Overleaf keeps no snapshot slots,
    so the fall-through would end in a false "not saved" message).
    """
    from vaibify.gui.routes.syncRoutes import _fnRollBackFailedCredential
    dictResult = {"sMessage": "Validation failed"}
    with patch(
        "vaibify.config.secretManager.fnStoreSecret",
    ) as mockStore:
        _fnRollBackFailedCredential(
            MagicMock(), {"docker": MagicMock()}, "cid", "overleaf",
            "", "previous-token", dictResult,
        )
    mockStore.assert_called_once_with(
        "overleaf_token", "previous-token", "keyring",
    )
    assert "your previously saved token was restored" in (
        dictResult["sMessage"]
    )


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
