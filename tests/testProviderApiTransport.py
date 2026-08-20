"""Tests for the low-level provider API transport authority.

Covers the three transport guarantees of agent-council design 8.3:
lazy optional-SDK loading (a missing SDK is a visible capability, not
an import error), fixed official-endpoint client construction, and
credential-safe error text.
"""

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui import providerApiTransport
from vaibify.gui.providerApiTransport import (
    ProviderSdkUnavailableError,
    ProviderTransportError,
    fbProviderSdkAvailable,
    flistCreateAnthropicMessageContent,
    fnValidateProviderName,
    fsProviderKeySlotName,
    fsRedactSecretText,
)


def _fmoduleBuildFakeAnthropicSdk(listContentBlocks):
    """Return a fake anthropic module whose create() yields the blocks."""
    mockMessage = MagicMock()
    mockMessage.content = listContentBlocks
    mockSdk = MagicMock()
    mockSdk.Anthropic.return_value.messages.create.return_value = (
        mockMessage
    )
    return mockSdk


# ---------------------------------------------------------------
# Lazy optional-SDK loading
# ---------------------------------------------------------------


def test_transport_module_has_no_top_level_sdk_import():
    """Module load must not import the SDK.

    Every ``import anthropic`` must sit inside a function body, so a
    host without the optional SDK still imports the transport and gets
    the visible unavailable capability instead of an ImportError. An
    in-place ``importlib.reload`` under a hidden-SDK patch would prove
    the same thing but replaces the module's exception classes mid
    session, so the structural assertion is checked on the source.
    """
    treeModule = ast.parse(
        Path(providerApiTransport.__file__).read_text()
    )
    for node in treeModule.body:
        assert not isinstance(node, ast.Import) or all(
            alias.name != "anthropic" for alias in node.names
        ), "top-level `import anthropic` breaks lazy SDK loading"
        assert not (
            isinstance(node, ast.ImportFrom)
            and node.module == "anthropic"
        ), "top-level `from anthropic import` breaks lazy SDK loading"


def test_fbProviderSdkAvailable_false_when_sdk_hidden():
    with patch.dict(sys.modules, {"anthropic": None}):
        assert fbProviderSdkAvailable("anthropic") is False


def test_fbProviderSdkAvailable_true_when_sdk_importable():
    with patch.dict(sys.modules, {"anthropic": MagicMock()}):
        assert fbProviderSdkAvailable("anthropic") is True


def test_missing_sdk_raises_visible_unavailable_error():
    """A call without the SDK names the package and the install fix."""
    with patch.dict(sys.modules, {"anthropic": None}):
        with pytest.raises(ProviderSdkUnavailableError) as excInfo:
            flistCreateAnthropicMessageContent(
                "sk-key", "model-id", 16, [],
            )
    assert "anthropic" in str(excInfo.value)
    assert "pip install anthropic" in str(excInfo.value)


def test_unavailable_error_is_a_runtime_error():
    """Legacy callers catching RuntimeError keep working."""
    assert issubclass(ProviderSdkUnavailableError, ProviderTransportError)
    assert issubclass(ProviderTransportError, RuntimeError)


# ---------------------------------------------------------------
# Fixed official-endpoint client construction
# ---------------------------------------------------------------


def test_client_constructed_with_api_key_only():
    """No endpoint, proxy, or header parameter reaches the client."""
    mockSdk = _fmoduleBuildFakeAnthropicSdk([MagicMock()])
    with patch.dict(sys.modules, {"anthropic": mockSdk}):
        flistCreateAnthropicMessageContent(
            "sk-key", "model-id", 16,
            [{"role": "user", "content": "hello"}],
        )
    mockSdk.Anthropic.assert_called_once_with(api_key="sk-key")


def test_success_returns_the_content_blocks():
    mockBlock = MagicMock()
    mockBlock.text = "generated"
    mockSdk = _fmoduleBuildFakeAnthropicSdk([mockBlock])
    with patch.dict(sys.modules, {"anthropic": mockSdk}):
        listContent = flistCreateAnthropicMessageContent(
            "sk-key", "model-id", 4096,
            [{"role": "user", "content": "hello"}],
        )
    assert listContent[0].text == "generated"
    dictKwargs = (
        mockSdk.Anthropic.return_value.messages.create.call_args[1]
    )
    assert dictKwargs["model"] == "model-id"
    assert dictKwargs["max_tokens"] == 4096
    assert dictKwargs["messages"][0]["content"] == "hello"


# ---------------------------------------------------------------
# Credential-safe error wrapping
# ---------------------------------------------------------------


def test_transport_error_text_never_contains_the_key():
    """An SDK failure that embeds the key is redacted before re-raise."""
    sSecretKey = "sk-ant-SECRET-VALUE-123"
    mockSdk = MagicMock()
    mockSdk.Anthropic.return_value.messages.create.side_effect = (
        Exception(f"401 unauthorized for credential {sSecretKey}")
    )
    with patch.dict(sys.modules, {"anthropic": mockSdk}):
        with pytest.raises(ProviderTransportError) as excInfo:
            flistCreateAnthropicMessageContent(
                sSecretKey, "model-id", 16, [],
            )
    assert sSecretKey not in str(excInfo.value)
    assert sSecretKey not in repr(excInfo.value)
    assert "[redacted-credential]" in str(excInfo.value)


def test_transport_error_suppresses_the_cause_chain():
    """The SDK's own exception must not ride the traceback chain."""
    mockSdk = MagicMock()
    mockSdk.Anthropic.return_value.messages.create.side_effect = (
        Exception("boom")
    )
    with patch.dict(sys.modules, {"anthropic": mockSdk}):
        with pytest.raises(ProviderTransportError) as excInfo:
            flistCreateAnthropicMessageContent(
                "sk-key", "model-id", 16, [],
            )
    assert excInfo.value.__cause__ is None
    assert excInfo.value.__suppress_context__ is True


def test_fsRedactSecretText_replaces_every_occurrence():
    sRedacted = fsRedactSecretText("a KEY b KEY c", "KEY")
    assert "KEY" not in sRedacted
    assert sRedacted.count("[redacted-credential]") == 2


def test_fsRedactSecretText_empty_secret_is_a_no_op():
    assert fsRedactSecretText("unchanged", "") == "unchanged"
    assert fsRedactSecretText("unchanged", None) == "unchanged"


# ---------------------------------------------------------------
# Provider registry and keyring slot naming
# ---------------------------------------------------------------


def test_fsProviderKeySlotName_for_anthropic():
    assert fsProviderKeySlotName("anthropic") == "anthropic_api_key"


def test_unknown_provider_is_refused_by_name():
    with pytest.raises(ValueError) as excInfo:
        fnValidateProviderName("openai")
    assert "openai" in str(excInfo.value)
    with pytest.raises(ValueError):
        fsProviderKeySlotName("not-a-provider")
    with pytest.raises(ValueError):
        fbProviderSdkAvailable("not-a-provider")
