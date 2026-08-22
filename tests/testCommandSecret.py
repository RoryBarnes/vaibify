"""Tests for the vaibify secret CLI credential commands.

The key must travel only from the non-echoing prompt into the real
``secretManager`` (landing in the suite's hermetic fake keyring), and
must appear in neither argv, stdout, stderr, nor logs.
"""

import logging
from unittest.mock import patch

from click.testing import CliRunner

from vaibify.cli.commandSecret import (
    fnSecretCommand,
    fnSecretSetProviderKeyCommand,
)
from vaibify.config.secretManager import fbSecretExists, fnStoreSecret

_S_TEST_KEY_VALUE = "sk-ant-hermetic-test-key-000"


def _tInvokeSecret(listArguments, sPromptedKey=None):
    """Run the secret group; return (result, mockGetpass)."""
    with patch(
        "getpass.getpass", return_value=sPromptedKey or "",
    ) as mockGetpass:
        result = CliRunner().invoke(fnSecretCommand, listArguments)
    return result, mockGetpass


def test_set_provider_key_stores_through_the_real_secret_manager(
    fixtureHermeticKeyring,
):
    result, mockGetpass = _tInvokeSecret(
        ["set-provider-key", "--provider", "anthropic"],
        sPromptedKey=_S_TEST_KEY_VALUE,
    )
    assert result.exit_code == 0, result.output
    mockGetpass.assert_called_once()
    assert fixtureHermeticKeyring.dictStore[
        ("vaibify", "anthropic_api_key")] == _S_TEST_KEY_VALUE


def test_set_provider_key_never_echoes_the_key(caplog):
    with caplog.at_level(logging.DEBUG):
        result, _ = _tInvokeSecret(
            ["set-provider-key", "--provider", "anthropic"],
            sPromptedKey=_S_TEST_KEY_VALUE,
        )
    assert result.exit_code == 0
    assert _S_TEST_KEY_VALUE not in result.output
    assert _S_TEST_KEY_VALUE not in caplog.text


def test_set_provider_key_accepts_no_key_bearing_argument():
    """The command surface offers no argv path for the key itself."""
    listParamNames = [
        param.name for param in fnSecretSetProviderKeyCommand.params
    ]
    assert listParamNames == ["sProvider"]


def test_set_provider_key_reads_through_the_hidden_prompt():
    """The key comes from getpass, never from click's echoing input."""
    result, mockGetpass = _tInvokeSecret(
        ["set-provider-key", "--provider", "anthropic"],
        sPromptedKey=_S_TEST_KEY_VALUE,
    )
    assert result.exit_code == 0
    assert "input hidden" in mockGetpass.call_args[0][0]


def test_set_provider_key_refuses_an_empty_key():
    result, _ = _tInvokeSecret(
        ["set-provider-key", "--provider", "anthropic"],
        sPromptedKey="   ",
    )
    assert result.exit_code == 1
    assert "must not be empty" in result.output
    assert fbSecretExists("anthropic_api_key", "keyring") is False


def test_set_provider_key_refuses_an_unknown_provider():
    result, _ = _tInvokeSecret(
        ["set-provider-key", "--provider", "not-a-provider"],
        sPromptedKey=_S_TEST_KEY_VALUE,
    )
    assert result.exit_code == 1
    assert "Unknown provider" in result.output
    assert fbSecretExists("anthropic_api_key", "keyring") is False


def test_delete_provider_key_removes_and_names_provider_revocation():
    fnStoreSecret("anthropic_api_key", _S_TEST_KEY_VALUE, "keyring")
    result, _ = _tInvokeSecret(
        ["delete-provider-key", "--provider", "anthropic"],
    )
    assert result.exit_code == 0, result.output
    assert fbSecretExists("anthropic_api_key", "keyring") is False
    assert "does not revoke" in result.output
    assert _S_TEST_KEY_VALUE not in result.output


def test_provider_key_status_reports_unconfigured_with_the_fix():
    result, _ = _tInvokeSecret(
        ["provider-key-status", "--provider", "anthropic"],
    )
    assert result.exit_code == 0
    assert "No anthropic API key is configured" in result.output
    assert "vaibify secret set-provider-key" in result.output


def test_provider_key_status_reports_configured_without_the_value():
    fnStoreSecret("anthropic_api_key", _S_TEST_KEY_VALUE, "keyring")
    result, _ = _tInvokeSecret(
        ["provider-key-status", "--provider", "anthropic"],
    )
    assert result.exit_code == 0
    assert "is configured" in result.output
    assert _S_TEST_KEY_VALUE not in result.output
