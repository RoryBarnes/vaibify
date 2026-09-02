"""Low-level provider API transport authority (agent-council design 8.3).

The single module allowed to load optional provider SDKs and construct
provider API clients. It owns exactly three security-relevant mechanics:

1. lazy optional-SDK loading — a missing SDK is a visible "unavailable"
   capability, never an import error at module load;
2. fixed official-endpoint client construction — no caller can supply
   an endpoint, proxy, header, or redirect target; and
3. credential-safe transport errors — exception text is redacted so it
   can never contain the API key.

Its surface is deliberately smaller than any caller's: prompts,
streaming, tool calls, and response semantics stay with the callers
(``llmInvoker`` for test generation; council adapters later).
"""

__all__ = [
    "ProviderSdkUnavailableError",
    "ProviderTransportError",
    "S_PROVIDER_ANTHROPIC",
    "SET_SUPPORTED_PROVIDERS",
    "fbProviderSdkAvailable",
    "flistCreateAnthropicMessageContent",
    "flistDiscoverAnthropicModels",
    "fnValidateProviderName",
    "fsProviderKeySlotName",
    "fsRedactSecretText",
]

S_PROVIDER_ANTHROPIC = "anthropic"
SET_SUPPORTED_PROVIDERS = frozenset({S_PROVIDER_ANTHROPIC})

_S_REDACTION_PLACEHOLDER = "[redacted-credential]"


class ProviderTransportError(RuntimeError):
    """A provider API call failed; the message is credential-redacted."""


class ProviderSdkUnavailableError(ProviderTransportError):
    """The provider's optional SDK is not installed on this host."""


def fnValidateProviderName(sProvider):
    """Raise ValueError for a provider outside the closed registry."""
    if sProvider not in SET_SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown provider '{sProvider}'. "
            f"Supported providers: {sorted(SET_SUPPORTED_PROVIDERS)}"
        )


def fsProviderKeySlotName(sProvider):
    """Return the host keyring slot holding this provider's API key."""
    fnValidateProviderName(sProvider)
    return f"{sProvider}_api_key"


def fbProviderSdkAvailable(sProvider):
    """Return True when the provider's optional SDK imports cleanly."""
    fnValidateProviderName(sProvider)
    try:
        import anthropic  # noqa: F401 -- availability probe only
    except ImportError:
        return False
    return True


def _fmoduleRequireAnthropicSdk():
    """Import and return the anthropic SDK, or raise a visible error."""
    try:
        import anthropic
    except ImportError:
        raise ProviderSdkUnavailableError(
            "The 'anthropic' package is not installed. "
            "Install with: pip install anthropic"
        )
    return anthropic


def fsRedactSecretText(sText, sSecretValue):
    """Replace every occurrence of a secret value with a placeholder."""
    if not sSecretValue:
        return sText
    return sText.replace(sSecretValue, _S_REDACTION_PLACEHOLDER)


def flistCreateAnthropicMessageContent(
    sApiKey, sModelId, iMaxTokens, listMessages,
):
    """Send one non-streaming Anthropic request; return its content blocks.

    The client is constructed with the API key only, so it targets the
    SDK's fixed official endpoint: callers cannot select a base URL,
    proxy, or header here, and must not gain a parameter that would let
    them (agent-council design 9.5). Any SDK failure is re-raised as a
    ``ProviderTransportError`` whose text has the key redacted and whose
    cause chain is suppressed, so no traceback can print credential
    material carried by the SDK's own exception.
    """
    moduleAnthropic = _fmoduleRequireAnthropicSdk()
    try:
        return moduleAnthropic.Anthropic(api_key=sApiKey).messages.create(
            model=sModelId,
            max_tokens=iMaxTokens,
            messages=listMessages,
        ).content
    except ProviderTransportError:
        raise
    except Exception as errorProvider:
        raise ProviderTransportError(
            fsRedactSecretText(
                f"Anthropic API request failed: {errorProvider}", sApiKey,
            )
        ) from None


def flistDiscoverAnthropicModels(sApiKey):
    """List the resolved model ids the key can reach via ``GET /v1/models``.

    The council participant picker is populated from this live list, not
    a hardcoded alias table that goes stale (agent-council design 8.2).
    The client is constructed with the key alone, so it targets the
    SDK's fixed official endpoint; every page is walked so a paginated
    account is not silently truncated. Any SDK failure is re-raised as a
    ``ProviderTransportError`` whose text is credential-redacted and
    whose cause chain is suppressed.
    """
    moduleAnthropic = _fmoduleRequireAnthropicSdk()
    try:
        listModelIdentifiers = []
        for genericModel in moduleAnthropic.Anthropic(
            api_key=sApiKey,
        ).models.list():
            sModelIdentifier = getattr(genericModel, "id", None)
            if sModelIdentifier:
                listModelIdentifiers.append(sModelIdentifier)
        return listModelIdentifiers
    except ProviderTransportError:
        raise
    except Exception as errorProvider:
        raise ProviderTransportError(
            fsRedactSecretText(
                f"Anthropic model discovery failed: {errorProvider}",
                sApiKey,
            )
        ) from None
