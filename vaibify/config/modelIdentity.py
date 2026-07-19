"""Single source of truth for the AI model identity used by the hub.

Every hub-side component that names an Anthropic model — the test-generation
invoker and the AI-provenance stamp — must resolve the identifier through
this module, so the model recorded in provenance records can never drift
from the model actually invoked. Deliberately a leaf module with no
intra-package imports and no environment override: provenance must not
change with a shell variable.
"""

__all__ = ["S_DEFAULT_ANTHROPIC_MODEL_ID", "fsResolveApiModelId"]

S_DEFAULT_ANTHROPIC_MODEL_ID = "claude-sonnet-5"


def fsResolveApiModelId():
    """Return the model identifier for direct Anthropic API calls."""
    return S_DEFAULT_ANTHROPIC_MODEL_ID
