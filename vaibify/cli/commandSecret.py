"""CLI subcommand group: vaibify secret.

Host-side credential commands for provider API keys (agent-council
design 9.5). The key is read through a non-echoing prompt and stored
through ``secretManager`` — never accepted as a command-line argument
or an environment variable, and never echoed back.
"""

import sys

import click


def _fsResolveProviderSlotOrExit(sProvider):
    """Return the provider's keyring slot, or exit naming valid providers."""
    from vaibify.gui.providerApiTransport import fsProviderKeySlotName
    try:
        return fsProviderKeySlotName(sProvider)
    except ValueError as error:
        click.echo(f"Error: {error}")
        sys.exit(1)


@click.group("secret")
def fnSecretCommand():
    """Manage provider API keys stored in the host keyring."""
    pass


@fnSecretCommand.command("set-provider-key")
@click.option(
    "--provider", "sProvider", required=True,
    help="Provider name (e.g. anthropic).",
)
def fnSecretSetProviderKeyCommand(sProvider):
    """Store a provider API key, read from a non-echoing prompt."""
    import getpass
    from vaibify.config.secretManager import fnStoreSecret
    sSlotName = _fsResolveProviderSlotOrExit(sProvider)
    sApiKey = getpass.getpass(
        f"{sProvider} API key (input hidden): "
    ).strip()
    if not sApiKey:
        click.echo(
            "Error: The API key must not be empty. Nothing was stored."
        )
        sys.exit(1)
    fnStoreSecret(sSlotName, sApiKey, "keyring")
    click.echo(f"Stored the {sProvider} API key in the host keyring.")


@fnSecretCommand.command("delete-provider-key")
@click.option(
    "--provider", "sProvider", required=True,
    help="Provider name (e.g. anthropic).",
)
def fnSecretDeleteProviderKeyCommand(sProvider):
    """Remove a provider API key from the host keyring."""
    from vaibify.config.secretManager import fnDeleteSecret
    sSlotName = _fsResolveProviderSlotOrExit(sProvider)
    fnDeleteSecret(sSlotName, "keyring")
    click.echo(
        f"Removed the {sProvider} API key from the host keyring. "
        "This does not revoke the key with the provider; if it may be "
        "compromised, revoke it in the provider's own console."
    )


@fnSecretCommand.command("provider-key-status")
@click.option(
    "--provider", "sProvider", required=True,
    help="Provider name (e.g. anthropic).",
)
def fnSecretReportProviderKeyStatusCommand(sProvider):
    """Report whether a provider API key is configured."""
    from vaibify.config.secretManager import fbSecretExists
    sSlotName = _fsResolveProviderSlotOrExit(sProvider)
    if fbSecretExists(sSlotName, "keyring"):
        click.echo(f"A {sProvider} API key is configured.")
    else:
        click.echo(
            f"No {sProvider} API key is configured. Store one with: "
            f"vaibify secret set-provider-key --provider {sProvider}"
        )
