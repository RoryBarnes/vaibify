"""CLI subcommand: ``vaibify revoke <service>``.

Removes a stored credential from the local keyring and best-effort
revokes the credential upstream. Currently supports the same three
services that vaibify can push to: ``github``, ``overleaf``, and
``zenodo``. The Zenodo case takes an optional ``--instance`` flag so
the user can target the sandbox or production keyring slot.

The command never throws: it returns a structured status dict from
each provider and prints the human-readable summary so the operator
can verify what was and was not revoked.
"""

import sys

import click


_LIST_VALID_SERVICES = ["github", "overleaf", "zenodo"]
_LIST_VALID_ZENODO_INSTANCES = ["sandbox", "production"]


@click.command("revoke")
@click.argument(
    "sService",
    type=click.Choice(_LIST_VALID_SERVICES, case_sensitive=False),
)
@click.option(
    "--keyring-slot", "sKeyringSlot", default="",
    help=(
        "GitHub only: per-repo keyring slot to clear "
        "(e.g. 'github_token:owner/repo')."
    ),
)
@click.option(
    "--instance", "sZenodoInstance",
    type=click.Choice(_LIST_VALID_ZENODO_INSTANCES, case_sensitive=False),
    default="sandbox",
    help=(
        "Zenodo only: target the sandbox or production keyring slot."
    ),
)
def revoke(sService, sKeyringSlot, sZenodoInstance):
    """Revoke a stored credential and clear the local keyring slot."""
    dictResult = _fdictRevokeForService(
        sService.lower(), sKeyringSlot, sZenodoInstance.lower(),
    )
    fnPrintRevocationReport(sService, dictResult)
    if not dictResult["bLocalCleared"]:
        sys.exit(1)


def _fdictRevokeForService(sService, sKeyringSlot, sZenodoInstance):
    """Dispatch to the per-provider revoker and return its status dict."""
    if sService == "github":
        from vaibify.reproducibility.githubAuth import (
            fdictRevokeGitHubToken,
        )
        return fdictRevokeGitHubToken(sKeyringSlot)
    if sService == "overleaf":
        from vaibify.reproducibility.overleafAuth import (
            fdictRevokeOverleafToken,
        )
        return fdictRevokeOverleafToken()
    if sService == "zenodo":
        from vaibify.reproducibility.zenodoClient import (
            fdictRevokeZenodoToken,
        )
        sZenodoService = (
            "zenodo" if sZenodoInstance == "production" else "sandbox"
        )
        return fdictRevokeZenodoToken(sZenodoService)
    raise click.UsageError(f"Unknown service '{sService}'.")


def fnPrintRevocationReport(sService, dictResult):
    """Render the revocation status in a uniform, scannable form."""
    sUpstreamFlag = (
        "yes" if dictResult["bUpstreamRevoked"] else "no"
    )
    sLocalFlag = "yes" if dictResult["bLocalCleared"] else "no"
    click.echo(f"[vaib] {sService}: upstream revoked={sUpstreamFlag}, "
               f"local cleared={sLocalFlag}")
    click.echo(f"[vaib] {dictResult['sMessage']}")
