"""``vaibify open`` — move a container's session to a fresh browser tab.

Host-lane only (design §6/§6b): the transfer capability is minted over
the hub's peer-authenticated control socket, which no container and no
remote peer can reach, so possession of the capability proves a human
at this host's keyboard. The handshake is two socket round trips — a
DESCRIBE that reports the current owner generation, then a MINT bound
to exactly that generation — so a stale invocation can never displace
a successor owner without having seen it. The CLI then redeems the
capability over HTTP, which commits the transfer transaction and
surfaces its verdict here in the terminal, and finally launches the
browser with the capability in the URL FRAGMENT; the page replays the
bounded-replay window for the same credential tuple, so the secret
never rides a query string or a server access log.
"""

import os
import sys
import webbrowser

import click

from vaibify.config.containerLock import (
    fbIsValidProjectName,
    fdictReadLockHolder,
)

F_TRANSFER_REDEEM_TIMEOUT_SECONDS = 60.0


def _fiResolveHubPortOrExplain(sContainerName):
    """Return the owning hub's port, or 0 after printing the way out."""
    dictHolder = fdictReadLockHolder(sContainerName)
    if not dictHolder:
        return _fiExplainUnownedContainer(sContainerName)
    iHubPort = dictHolder.get("iPort") or 0
    if not isinstance(iHubPort, int) or iHubPort <= 0:
        click.echo(
            f"Error: a live process (pid={dictHolder.get('iPid')}) holds "
            f"container '{sContainerName}' but reports no hub port, so "
            "there is no host control socket to mint a transfer from. "
            "Stop that process, then claim the container normally.",
            err=True,
        )
        return 0
    return iHubPort


def _fiExplainUnownedContainer(sContainerName):
    """Print the claim-normally guidance for an unowned container."""
    from .hubSession import flistFindLiveHubSessions
    listSessions = flistFindLiveHubSessions()
    if not listSessions:
        click.echo(
            "Error: no live vaibify hub is running. Start one with "
            "'vaibify', then claim the container from its dashboard — "
            "'vaibify open' transfers an EXISTING session.", err=True,
        )
        return 0
    sPorts = ", ".join(
        str(dictSlot.get("iPort")) for dictSlot in listSessions
    )
    click.echo(
        f"Error: no vaibify session holds container '{sContainerName}', "
        "so there is nothing to transfer. Open the dashboard (hub "
        f"port(s) {sPorts}) and claim it normally.", err=True,
    )
    return 0


def _fsMintTransferCapability(iHubPort, sContainerName):
    """Describe then mint over the control socket; return the capability.

    Returns ``''`` after printing the refusal. The mint round trip
    carries the generation the describe round trip reported, so the
    hub refuses the mint if the owner changed in between, and the
    capability itself stays bound to the generation it named.
    """
    from vaibify.gui.hostControlChannel import fdictSendHostControlRequest
    dictDescribed = fdictSendHostControlRequest(iHubPort, {
        "sOperation": "mint-transfer",
        "sContainerName": sContainerName,
    })
    if not dictDescribed.get("bAccepted"):
        click.echo(
            f"Refused by the hub: {dictDescribed.get('sError', '')}",
            err=True,
        )
        return ""
    dictMinted = fdictSendHostControlRequest(iHubPort, {
        "sOperation": "mint-transfer",
        "sContainerName": sContainerName,
        "iExpectedOwnerGeneration": dictDescribed[
            "iCurrentOwnerGeneration"
        ],
    })
    if not dictMinted.get("bAccepted") or not dictMinted.get("bMinted"):
        click.echo(
            f"Refused by the hub: {dictMinted.get('sError', '')}",
            err=True,
        )
        return ""
    return dictMinted["sTransferCapability"]


def _ftRedeemTransferCapability(iHubPort, sCapability):
    """POST the capability to ``/api/transfer``; return (sOutcome, dict)."""
    from .hubSession import ftSendHttpRequest
    iStatusCode, objBody = ftSendHttpRequest(
        f"http://127.0.0.1:{iHubPort}", "", "POST", "/api/transfer",
        dictFields={"sCapability": sCapability},
        fTimeoutSeconds=F_TRANSFER_REDEEM_TIMEOUT_SECONDS,
    )
    if not isinstance(objBody, dict) or "sOutcome" not in objBody:
        click.echo(
            f"Error: the hub answered the transfer redemption with "
            f"status {iStatusCode} and an unreadable body.", err=True,
        )
        return ("", {})
    return (objBody["sOutcome"], objBody)


def _fnLaunchDashboardWithCapability(iHubPort, sCapability):
    """Open the dashboard carrying the capability in the URL fragment.

    When the browser cannot or must not be launched, the URL is
    printed instead — it contains the one-time capability, so it goes
    to the terminal only, never to a log file.
    """
    sUrl = f"http://127.0.0.1:{iHubPort}/#transfer={sCapability}"
    from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
    bSuppressed = bool(os.environ.get(S_SUPPRESS_BROWSER_ENV))
    if not bSuppressed and webbrowser.open(sUrl):
        click.echo("Opening the transferred session in your browser.")
        return
    click.echo(
        "Open this URL to attach the transferred session (it carries a "
        "one-time capability):"
    )
    click.echo(f"  {sUrl}")


def _fiReportTransferOutcome(sOutcome, dictBody, iHubPort, sCapability):
    """Translate the redemption verdict into terminal UX; return exit code."""
    sMessage = dictBody.get("sMessage", "")
    if sOutcome == "transferred":
        sContainerName = dictBody.get("sContainerName", "")
        click.echo(
            f"Transferred container '{sContainerName}' to a new browser "
            f"session (owner generation "
            f"{dictBody.get('iOwnerGeneration')}). Any previous "
            "dashboard tab for it is now signed out."
        )
        _fnLaunchDashboardWithCapability(iHubPort, sCapability)
        return 0
    if sOutcome == "busyRetry":
        click.echo(f"Container busy: {sMessage}", err=True)
        click.echo(
            "Nothing was transferred. Re-run 'vaibify open' shortly.",
            err=True,
        )
        return 1
    click.echo(f"Transfer {sOutcome}: {sMessage}", err=True)
    return 1


def fiRunOpenCommand(sContainerName):
    """The open entry: discover, mint, redeem, launch; return exit code."""
    from vaibify.gui.hostControlChannel import HostControlError
    from .hubSession import HubSessionError
    if not fbIsValidProjectName(sContainerName):
        click.echo(
            f"Error: invalid container name {sContainerName!r}.", err=True,
        )
        return 2
    iHubPort = _fiResolveHubPortOrExplain(sContainerName)
    if not iHubPort:
        return 1
    try:
        sCapability = _fsMintTransferCapability(iHubPort, sContainerName)
        if not sCapability:
            return 1
        sOutcome, dictBody = _ftRedeemTransferCapability(
            iHubPort, sCapability,
        )
    except (HostControlError, HubSessionError) as error:
        click.echo(f"Error: {error}", err=True)
        return 1
    if not sOutcome:
        return 1
    return _fiReportTransferOutcome(
        sOutcome, dictBody, iHubPort, sCapability,
    )


@click.command("open")
@click.argument("container")
def fnOpenContainerCommand(container):
    """Move a container's live session into a fresh browser tab."""
    sys.exit(fiRunOpenCommand(container))
