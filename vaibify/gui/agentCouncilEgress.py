"""Per-campaign egress boundary for Agent Council runners (design section 9.7).

A council runner may reach exactly one destination family: its own
provider's API endpoints. The boundary is built out of three pieces,
each created per campaign and destroyed with the campaign:

1. an INTERNAL Docker network (``networks.create(..., internal=True)``),
   which the kernel gives no route off the host — every direct dial to
   a bridge, external, or IPv6 address fails immediately with
   ``ENETUNREACH`` (errno 101, verified live on Docker 28.4.0);
2. the allowlisting HTTP CONNECT proxy of :mod:`vaibify.docker.egressProxy`
   — the script, its allowlist validation and its argv and environment
   composition live there, shared with every other sealed container —
   run in the digest-pinned proxy image attached to BOTH the internal
   network and the default bridge, created by the gateway with the
   runner's own hardened posture (unprivileged user, all capabilities
   dropped, no-new-privileges, memory/CPU/PID bounds). Nothing
   model-controlled ever selects an endpoint: the allowlist, the port
   set and the address map enter only through the container's command
   line, composed from validated server-owned values; and
3. runner wiring: the runner joins only the internal network, receives
   ``HTTPS_PROXY``/``HTTP_PROXY`` pointing at the proxy's NUMERIC
   internal address (so it never needs a resolver), and has its DNS
   black-holed (the egress module explains the embedded-DNS hazard).

This module holds what is the COUNCIL'S: campaign-id validation, the
per-campaign resource names, the runner's wiring, and the
council-stamped script tarball. Every Docker-SDK call that realizes the
boundary (network create/remove, proxy create/start/inspect, script
copy-in, bridge attach, absence-proving teardown) lives in
``agentCouncilDockerGateway``, the single council SDK authority
(``tests/testCouncilGatewayAuthority.py`` fails the build if SDK reach
reappears here). Teardown follows the prove-absence discipline of the
gateway's container destruction: after removal, the daemon is asked to
inspect the resources by name, and a daemon that did not answer with a
positive ``NotFound`` yields an INDETERMINATE report, never a claim of
absence (``agentCouncilDockerGateway.fdictRemoveCampaignEgressResources``).

The shared names below are re-exported so the council's callers and
tests keep one import; the values are the egress module's.
"""

import re

from vaibify.docker import egressProxy
from vaibify.docker.egressProxy import (
    EgressSetupError,
    F_PROXY_READY_DEADLINE_SECONDS,
    F_PROXY_READY_POLL_SECONDS,
    I_PROXY_LISTEN_PORT,
    S_BLACK_HOLE_NAMESERVER,
    S_CONNECT_PROXY_SCRIPT,
    S_PROXY_IMAGE,
    S_PROXY_READY_LINE,
    S_PROXY_SCRIPT_BASENAME,
    S_PROXY_SCRIPT_CONTAINER_PATH,
    S_PROXY_SCRIPT_DIRECTORY,
    fnValidateAllowlistOrRaise,
)
from vaibify.gui import agentCouncilRunner

__all__ = [
    "EgressSetupError",
    "F_PROXY_READY_DEADLINE_SECONDS",
    "F_PROXY_READY_POLL_SECONDS",
    "I_PROXY_LISTEN_PORT",
    "S_BLACK_HOLE_NAMESERVER",
    "S_CONNECT_PROXY_SCRIPT",
    "S_PROXY_IMAGE",
    "S_NETWORK_NAME_PREFIX",
    "S_PROXY_NAME_PREFIX",
    "S_PROXY_READY_LINE",
    "S_PROXY_SCRIPT_BASENAME",
    "S_PROXY_SCRIPT_CONTAINER_PATH",
    "S_PROXY_SCRIPT_DIRECTORY",
    "fbaBuildProxyScriptTarball",
    "fdictBuildRunnerProxyEnvironment",
    "flistBuildRunnerNetworkArguments",
    "flistComposeProxyCommand",
    "fnValidateAllowlistOrRaise",
    "fnValidateCampaignIdOrRaise",
    "fsComposeNetworkName",
    "fsComposeProxyContainerName",
]

S_NETWORK_NAME_PREFIX = "vaibifyCouncilEgress"
S_PROXY_NAME_PREFIX = "vaibifyCouncilProxy"

# A campaign id is server-minted. Validating it before it reaches a
# resource name keeps a crafted value from smuggling a stray token.
_RE_CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


def fnValidateCampaignIdOrRaise(sCampaignId):
    """Refuse a campaign id that could smuggle a token into a resource name."""
    if not isinstance(sCampaignId, str) or not _RE_CAMPAIGN_ID.match(
            sCampaignId):
        raise EgressSetupError(
            "campaign id must be 1-64 characters of letters, digits and "
            f"hyphens, starting with a letter or digit; got {sCampaignId!r}"
        )


def fsComposeNetworkName(sCampaignId):
    """Return the per-campaign internal network name."""
    fnValidateCampaignIdOrRaise(sCampaignId)
    return f"{S_NETWORK_NAME_PREFIX}-{sCampaignId}"


def fsComposeProxyContainerName(sCampaignId):
    """Return the per-campaign proxy container name."""
    fnValidateCampaignIdOrRaise(sCampaignId)
    return f"{S_PROXY_NAME_PREFIX}-{sCampaignId}"


def fbaBuildProxyScriptTarball():
    """Build the ownership-stamped tarball carrying the proxy script.

    Reuses the runner's ownership-stamping one-file tarball builder, so
    the single tar-entry discipline in the package stays in one place
    and ``tarfile.TarInfo``'s native uid/gid default of 0 can never
    leak — the 1000-stamped entries are what let the NON-ROOT proxy
    read its own script. The gateway extracts the archive at ``/``, so
    the script lands at ``S_PROXY_SCRIPT_CONTAINER_PATH``.
    """
    return agentCouncilRunner.fbaBuildStampedFileTarball(
        S_PROXY_SCRIPT_DIRECTORY, S_PROXY_SCRIPT_BASENAME,
        S_CONNECT_PROXY_SCRIPT.encode("utf-8"),
        iFileMode=0o644, iDirectoryMode=0o755,
    )


def flistComposeProxyCommand(saAllowedHostnames, iaAllowedPorts,
                             dictHostnameAddressMap, sBindCidr=""):
    """Compose the proxy container's argv (the egress module's, for the council)."""
    return egressProxy.flistComposeProxyCommand(
        saAllowedHostnames, iaAllowedPorts, dictHostnameAddressMap, sBindCidr,
    )


def fdictBuildRunnerProxyEnvironment(sProxyInternalAddress,
                                     iProxyPort=I_PROXY_LISTEN_PORT):
    """Return the proxy environment a runner container receives."""
    return egressProxy.fdictBuildProxyEnvironment(sProxyInternalAddress, iProxyPort)


def flistBuildRunnerNetworkArguments(sCampaignId):
    """Return the ``docker run`` arguments that seal a runner's network.

    The campaign's internal network only, then the black-holed resolver
    (the egress module explains both).
    """
    return egressProxy.flistBuildSealedNetworkArguments(
        fsComposeNetworkName(sCampaignId),
    )
