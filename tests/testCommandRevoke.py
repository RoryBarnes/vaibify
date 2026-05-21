"""Tests for token revocation across github, overleaf, and zenodo.

Each provider's revoker must:
  * Make a best-effort upstream revocation call (mocked here).
  * Clear the local keyring slot via ``fnDeleteSecret``.
  * Return a dict with ``bUpstreamRevoked``, ``bLocalCleared``, and
    ``sMessage`` so the CLI surface can render a uniform report.

The CLI command must dispatch to the right provider based on the
positional argument and exit nonzero when the local slot was not
cleared (so a script can rely on ``$?`` to know the keyring is dirty).
"""

import subprocess
from unittest.mock import patch

from click.testing import CliRunner

from vaibify.cli.commandRevoke import revoke
from vaibify.reproducibility.githubAuth import fdictRevokeGitHubToken
from vaibify.reproducibility.overleafAuth import fdictRevokeOverleafToken
from vaibify.reproducibility.zenodoClient import fdictRevokeZenodoToken


def _fnFakeSuccessfulGhLogout(*args, **kwargs):
    return subprocess.CompletedProcess(
        args=args, returncode=0, stdout="", stderr="",
    )


def _fnFakeFailingGhLogout(*args, **kwargs):
    return subprocess.CompletedProcess(
        args=args, returncode=1, stdout="", stderr="auth not set",
    )


def test_fdictRevokeGitHubToken_clears_keyring_slot():
    with patch(
        "vaibify.reproducibility.githubAuth.subprocess.run",
        side_effect=_fnFakeSuccessfulGhLogout,
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ) as mockDelete:
        dictResult = fdictRevokeGitHubToken("github_token:victim/repo")
    mockDelete.assert_called_once_with(
        "github_token:victim/repo", "keyring",
    )
    assert dictResult["bLocalCleared"] is True
    assert dictResult["bUpstreamRevoked"] is True


def test_fdictRevokeGitHubToken_reports_gh_logout_failure():
    with patch(
        "vaibify.reproducibility.githubAuth.subprocess.run",
        side_effect=_fnFakeFailingGhLogout,
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ):
        dictResult = fdictRevokeGitHubToken("github_token:victim/repo")
    assert dictResult["bUpstreamRevoked"] is False
    assert "PAT" in dictResult["sMessage"]


def test_fdictRevokeGitHubToken_handles_missing_gh_binary():
    with patch(
        "vaibify.reproducibility.githubAuth.subprocess.run",
        side_effect=FileNotFoundError,
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ):
        dictResult = fdictRevokeGitHubToken("github_token:victim/repo")
    assert dictResult["bUpstreamRevoked"] is False
    assert "gh CLI" in dictResult["sMessage"]


def test_fdictRevokeGitHubToken_without_slot_announces_skip():
    """Empty slot must not raise — just report nothing was cleared."""
    with patch(
        "vaibify.reproducibility.githubAuth.subprocess.run",
        side_effect=_fnFakeSuccessfulGhLogout,
    ):
        dictResult = fdictRevokeGitHubToken("")
    assert dictResult["bLocalCleared"] is False


def test_fdictRevokeOverleafToken_clears_local_slot():
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ) as mockDelete:
        dictResult = fdictRevokeOverleafToken()
    mockDelete.assert_called_once_with("overleaf_token", "keyring")
    assert dictResult["bLocalCleared"] is True
    assert dictResult["bUpstreamRevoked"] is False
    assert "overleaf.com/user/account" in dictResult["sMessage"]


def test_fdictRevokeZenodoToken_clears_sandbox_slot():
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ) as mockDelete:
        dictResult = fdictRevokeZenodoToken("sandbox")
    mockDelete.assert_called_once_with(
        "zenodo_token_sandbox", "keyring",
    )
    assert dictResult["bLocalCleared"] is True
    assert "sandbox.zenodo.org" in dictResult["sMessage"]


def test_fdictRevokeZenodoToken_clears_production_slot():
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ) as mockDelete:
        dictResult = fdictRevokeZenodoToken("zenodo")
    mockDelete.assert_called_once_with(
        "zenodo_token_production", "keyring",
    )
    assert dictResult["bLocalCleared"] is True


def test_fdictRevokeZenodoToken_propagates_delete_failure():
    """If the keyring delete raises, bLocalCleared must be False."""
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
        side_effect=RuntimeError("keyring busy"),
    ):
        dictResult = fdictRevokeZenodoToken("sandbox")
    assert dictResult["bLocalCleared"] is False
    assert "local clear failed" in dictResult["sMessage"]


def test_cli_revoke_github_succeeds_when_keyring_cleared():
    runner = CliRunner()
    with patch(
        "vaibify.reproducibility.githubAuth.subprocess.run",
        side_effect=_fnFakeSuccessfulGhLogout,
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ):
        result = runner.invoke(
            revoke,
            ["github", "--keyring-slot", "github_token:owner/repo"],
        )
    assert result.exit_code == 0
    assert "github" in result.output
    assert "local cleared=yes" in result.output


def test_cli_revoke_overleaf_exits_zero_on_success():
    runner = CliRunner()
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ):
        result = runner.invoke(revoke, ["overleaf"])
    assert result.exit_code == 0
    assert "overleaf" in result.output


def test_cli_revoke_zenodo_targets_sandbox_by_default():
    runner = CliRunner()
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ) as mockDelete:
        result = runner.invoke(revoke, ["zenodo"])
    assert result.exit_code == 0
    mockDelete.assert_called_once_with(
        "zenodo_token_sandbox", "keyring",
    )


def test_cli_revoke_zenodo_targets_production_when_asked():
    runner = CliRunner()
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ) as mockDelete:
        result = runner.invoke(
            revoke, ["zenodo", "--instance", "production"],
        )
    assert result.exit_code == 0
    mockDelete.assert_called_once_with(
        "zenodo_token_production", "keyring",
    )


def test_cli_revoke_exits_nonzero_when_keyring_delete_fails():
    runner = CliRunner()
    with patch(
        "vaibify.config.secretManager.fnDeleteSecret",
        side_effect=RuntimeError("keyring locked"),
    ):
        result = runner.invoke(revoke, ["overleaf"])
    assert result.exit_code != 0


def test_cli_revoke_rejects_unknown_service():
    runner = CliRunner()
    result = runner.invoke(revoke, ["dropbox"])
    assert result.exit_code != 0
