"""The container-scope network diagnosis, as a graph rather than a ladder.

A researcher lost a session to a container that could not resolve
hostnames: its ``/etc/resolv.conf`` was a snapshot of a network the
laptop had since left. Every check vaibify had reported ok throughout,
because nothing it ran executed anything INSIDE the container.

The shape of the diagnosis matters as much as its existence. A linear
"first failing rung and stop" ladder misclassifies, in both directions.
A container-resolution failure cannot be called a container fault until
the host's own answer is known -- the same failure means "the daemon's
resolver is stale" or "this cafe's DNS is down" depending on a fact the
ladder never asked for. And a direct TCP dial can correctly fail on a
network where everything works through a proxy, so a transport failure
is not a verdict on its own either.

So the stages are:

1. **Static state**, no traffic at all: isolation, attachments, and the
   resolver configuration triple (``NetworkMode``, ``HostConfig.Dns``,
   the container's own ``/etc/resolv.conf``).
2. **Paired** host/container resolution, classified TOGETHER.
3. **The effective proxy path**, reported as facts. A mismatch between
   host and container proxy settings is not a fault; Docker Desktop
   applies host or PAC settings a shell never sees, and a container
   legitimately needs a different ``NO_PROXY``.
4. **Transport** (``--online`` only): connect and complete TLS along
   the path stage 3 identified, sending no request bytes.
5. **Address family**, used only to EXPLAIN a stage-4 failure -- an
   AAAA answer on a v4-only path -- never as a verdict of its own.

**Probe policy (researcher's ruling, 2026-09-10, option (a)).** A name
lookup is network activity, so stage 2 is not "local". The default
exception is narrow: the only name resolved is one this project
ALREADY depends on -- its first configured repository's host, or the
enabled agent provider's API host -- so no new third party is
introduced by running a diagnostic. With neither configured, doctor
falls back to static inspection and says so. Everything in stage 4
requires ``--online``. Nothing here ever authenticates, and no
remediation ever names a public resolver: ``8.8.8.8`` breaks
split-horizon DNS on exactly the campus and corporate networks this
product runs on.
"""

import os
import re
import socket
from urllib.parse import urlsplit

from vaibify.docker.containerManager import fjsonInspectContainer
from vaibify.reproducibility.credentialRedactor import fsRedactUrlCredentials

from .preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_INFO, S_LEVEL_NOT_CHECKED, S_LEVEL_OK,
    S_LEVEL_WARN, S_SCOPE_CONTAINER, PreflightResult,
)


__all__ = [
    "flistDiagnoseContainerNetwork", "fsClassifyResolverConfiguration",
    "fsReadContainerResolvConf",
    "fsClassifyDnsPair", "ftDescribeIsolation", "fdictResolveProbeTarget",
    "S_RESOLVER_EXPLICIT_DNS", "S_RESOLVER_EMBEDDED_RESOLVER",
    "S_RESOLVER_DEFAULT_BRIDGE", "S_RESOLVER_UNKNOWN",
    "S_DNS_BOTH_RESOLVE", "S_DNS_CONTAINER_FAULT",
    "S_DNS_HOST_FAULT_NOT_IMPAIRING", "S_DNS_BOTH_FAIL",
]


S_RESOLVER_EXPLICIT_DNS = "explicit-dns"
S_RESOLVER_EMBEDDED_RESOLVER = "embedded-resolver"
S_RESOLVER_DEFAULT_BRIDGE = "default-bridge"
S_RESOLVER_UNKNOWN = "unknown"

S_DNS_BOTH_RESOLVE = "both-resolve"
S_DNS_CONTAINER_FAULT = "container-resolver-fault"
S_DNS_HOST_FAULT_NOT_IMPAIRING = "host-fault-not-impairing-this-container"
S_DNS_BOTH_FAIL = "host-or-upstream-failure"

_S_ISOLATION_ENVIRONMENT_FLAG = "VAIBIFY_NETWORK_ISOLATED=true"
_S_EMBEDDED_RESOLVER_ADDRESS = "127.0.0.11"
_T_BUILT_IN_NETWORK_MODES = ("bridge", "default", "host", "none")
_F_PROBE_TIMEOUT_SECONDS = 2.0

_T_PROXY_VARIABLE_NAMES = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
)


# -----------------------------------------------------------------------
# Stage 1 -- static state, decided from the specification alone.
# -----------------------------------------------------------------------


def ftDescribeIsolation(jsonInspect):
    """Return ``(bIsolated, sEvidence)`` for a container's networking.

    Isolation is a SUCCESSFUL assessment, not a check that does not
    apply: a project that asked for ``--network none`` got what it
    asked for. The caller emits one informational result and then
    emits no downstream network results at all -- running them would
    produce a wall of failures describing a working configuration.
    """
    dictHostConfig = jsonInspect.get("HostConfig") or {}
    sNetworkMode = str(dictHostConfig.get("NetworkMode") or "")
    listEnvironment = (jsonInspect.get("Config") or {}).get("Env") or []
    bFlagged = _S_ISOLATION_ENVIRONMENT_FLAG in listEnvironment
    if sNetworkMode == "none":
        if bFlagged:
            return (True, "vaibify created it with `--network none`")
        return (
            True,
            "its NetworkMode is `none`, though vaibify's own isolation "
            "flag is absent from its environment",
        )
    if bFlagged:
        return (
            False,
            "vaibify's isolation flag is set in its environment but "
            f"its NetworkMode is `{sNetworkMode}`",
        )
    return (False, "")


def fsClassifyResolverConfiguration(jsonInspect, sResolvConf):
    """Return WHICH of the three resolver configurations this container has.

    They are not degrees of the same thing; they have different
    remedies. A default-bridge resolver list is derived from the host
    and rewritten on start, so a restart replaces it. A user-defined
    network's resolver is Docker's own at ``127.0.0.11``, forwarding to
    upstreams the daemon holds -- the container's file will not show
    the stale value, and a restart clears it anyway. An explicit
    ``HostConfig.Dns`` is baked into the container: no restart can
    change it, so recommending one would be advice that cannot work.
    """
    dictHostConfig = jsonInspect.get("HostConfig") or {}
    if list(dictHostConfig.get("Dns") or []):
        return S_RESOLVER_EXPLICIT_DNS
    sNetworkMode = str(dictHostConfig.get("NetworkMode") or "")
    if _S_EMBEDDED_RESOLVER_ADDRESS in (sResolvConf or ""):
        return S_RESOLVER_EMBEDDED_RESOLVER
    if sNetworkMode and sNetworkMode not in _T_BUILT_IN_NETWORK_MODES:
        return S_RESOLVER_EMBEDDED_RESOLVER
    if sNetworkMode in ("bridge", "default"):
        return S_RESOLVER_DEFAULT_BRIDGE
    return S_RESOLVER_UNKNOWN


def fsClassifyDnsPair(bHostResolved, bContainerResolved):
    """Classify host and container resolution TOGETHER, never alone.

    The container's failure means different things depending on the
    host's answer, and only the pair distinguishes them. Reporting
    either half on its own is how a diagnostic tells a researcher to
    restart a container over a hotel network that is down.
    """
    if bHostResolved and bContainerResolved:
        return S_DNS_BOTH_RESOLVE
    if bHostResolved and not bContainerResolved:
        return S_DNS_CONTAINER_FAULT
    if not bHostResolved and bContainerResolved:
        return S_DNS_HOST_FAULT_NOT_IMPAIRING
    return S_DNS_BOTH_FAIL


# The port each git transport actually answers on, and whether the
# connection is TLS. A probe that assumed 443 for everything reported a
# working network as broken for every project whose remote is reached
# over ssh -- a git server answers 22 and serves nothing on 443, so the
# "failure" was vaibify dialling the wrong door.
_DICT_SCHEME_TRANSPORT = {
    "https": (443, True),
    "http": (80, False),
    "ssh": (22, False),
    "git": (9418, False),
}

# ``user@host:path`` -- git's scp-like remote syntax, which vaibify
# accepts (projectConfig._S_SCP_LIKE_URL) and ``urlsplit`` reports no
# hostname for at all, because there is no ``//``. Left unparsed, every
# project using that spelling silently had no probe target.
_RE_SCP_LIKE_REMOTE = re.compile(
    r"^[A-Za-z0-9._~-]+@(?P<host>[A-Za-z0-9._~-]+):(?!//).+$"
)


def _ftParseRemoteEndpoint(sUrl):
    """Return ``(sHostname, iPort, bUsesTls)`` for one git remote.

    ``("", 0, False)`` when nothing can be parsed, which the caller
    renders as "no target" rather than guessing at a host.
    """
    matchScp = _RE_SCP_LIKE_REMOTE.match(sUrl.strip())
    if matchScp is not None:
        return (matchScp.group("host"), 22, False)
    tSplit = urlsplit(sUrl.strip())
    if not tSplit.hostname:
        return ("", 0, False)
    iDefaultPort, bUsesTls = _DICT_SCHEME_TRANSPORT.get(
        tSplit.scheme, (443, True),
    )
    return (tSplit.hostname, tSplit.port or iDefaultPort, bUsesTls)


def fdictResolveProbeTarget(config):
    """Return the one endpoint this project already depends on, or none.

    Never a name of vaibify's choosing. The first configured
    repository's host is preferred because a project that clones it
    already resolves it on every start; the enabled Claude provider's
    API host is the fallback because an agent-enabled project already
    reaches it. With neither, there is no target and the caller must
    fall back to static inspection rather than inventing one.

    The PORT and whether the connection is TLS travel with the name,
    because the transport stage has to dial the door this project
    actually uses.
    """
    for dictRepository in getattr(config, "listRepositories", []) or []:
        sHostname, iPort, bUsesTls = _ftParseRemoteEndpoint(
            str(dictRepository.get("url") or ""),
        )
        if sHostname:
            return {
                "sHostname": sHostname, "iPort": iPort,
                "bUsesTls": bUsesTls,
                "sOrigin": "this project's configured repository",
            }
    if getattr(getattr(config, "features", None), "bClaude", False):
        from vaibify.gui.agentCouncilProviders import (
            I_ANTHROPIC_API_PORT, S_ANTHROPIC_API_HOSTNAME,
        )
        return {
            "sHostname": S_ANTHROPIC_API_HOSTNAME,
            "iPort": I_ANTHROPIC_API_PORT, "bUsesTls": True,
            "sOrigin": "the agent provider this project enables",
        }
    return {"sHostname": "", "iPort": 0, "bUsesTls": False, "sOrigin": ""}


# -----------------------------------------------------------------------
# Stage 1 reporting.
# -----------------------------------------------------------------------


def _fpreflightIsolation(sEvidence):
    """Return the informational result for a deliberately sealed container."""
    return PreflightResult(
        sName="network-isolation", sLevel=S_LEVEL_INFO,
        sScope=S_SCOPE_CONTAINER,
        sMessage=(
            "networking is intentionally disabled for this project ("
            + sEvidence + "); no network checks were run, and none "
            "would mean anything if they were."
        ),
        sMechanism=(
            "Reads HostConfig.NetworkMode and looks for vaibify's own "
            "VAIBIFY_NETWORK_ISOLATED flag in the created "
            "environment. Both are runtime truth, not configuration."
        ),
    )


def _flistReportAttachments(jsonInspect):
    """Report which networks the container is attached to, and its route."""
    dictNetworks = (
        jsonInspect.get("NetworkSettings") or {}
    ).get("Networks") or {}
    if not dictNetworks:
        return [PreflightResult(
            sName="network-attachment", sLevel=S_LEVEL_FAIL,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container is attached to no network at all, and "
                "it was not created isolated."
            ),
            sRemediation=(
                "Recreate the container from its vaibify "
                "configuration; an attachment lost at runtime cannot "
                "be restored by a restart."
            ),
        )]
    listNamed = sorted(dictNetworks)
    sGateway = ""
    for sNetwork in listNamed:
        sGateway = str((dictNetworks[sNetwork] or {}).get("Gateway") or "")
        if sGateway:
            break
    sAttachment = "attached to " + ", ".join(listNamed)
    if sGateway:
        return [PreflightResult(
            sName="network-attachment", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_CONTAINER,
            sMessage=f"{sAttachment}, default route via {sGateway}.",
        )]
    return [PreflightResult(
        sName="network-attachment", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_CONTAINER,
        sMessage=f"{sAttachment}, but with no default route.",
        sRemediation=(
            "Without a gateway the container can reach its own "
            "network and nothing beyond it. Recreate the container; a "
            "route lost at runtime is not restored by a restart."
        ),
    )]


_DICT_RESOLVER_DESCRIPTION = {
    S_RESOLVER_DEFAULT_BRIDGE: (
        "the default bridge: its resolver list is derived from this "
        "host and rewritten every time the container starts"
    ),
    S_RESOLVER_EMBEDDED_RESOLVER: (
        "a user-defined network: Docker's embedded resolver at "
        "127.0.0.11 forwards to upstreams the daemon holds, so the "
        "container's own resolv.conf will not show a stale one"
    ),
    S_RESOLVER_EXPLICIT_DNS: (
        "an explicit HostConfig.Dns baked in at creation, which no "
        "restart can change"
    ),
    S_RESOLVER_UNKNOWN: (
        "a configuration this diagnostic could not classify"
    ),
}


def _flistReportResolverConfiguration(sResolverKind, sNameservers):
    """Report the resolver configuration; flag one vaibify never sets."""
    sMessage = (
        "name resolution goes through "
        + _DICT_RESOLVER_DESCRIPTION[sResolverKind]
        + (f" (nameservers: {sNameservers})" if sNameservers else "")
        + "."
    )
    if sResolverKind != S_RESOLVER_EXPLICIT_DNS:
        return [PreflightResult(
            sName="resolver-configuration", sLevel=S_LEVEL_INFO,
            sScope=S_SCOPE_CONTAINER, sMessage=sMessage,
        )]
    return [PreflightResult(
        sName="resolver-configuration", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_CONTAINER,
        sMessage=(
            sMessage + " Vaibify never sets this, so this container's "
            "specification has drifted from its vaibify configuration: "
            "the setting was introduced outside vaibify."
        ),
        sRemediation=(
            "A restart cannot clear it. Recreate the container from "
            "the vaibify configuration, which sets no DNS servers at "
            "all -- `vaibify repair dns` does exactly that, and "
            "refuses to restart, because a restart here would look "
            "like the fix failing."
        ),
        sMechanism=(
            "Compares HostConfig.Dns against vaibify's own container "
            "construction, which has no dns field to write one from."
        ),
    )]


# -----------------------------------------------------------------------
# Stage 2 -- paired resolution.
# -----------------------------------------------------------------------


def _fdictResolveOnHost(sHostname):
    """Resolve one name on THIS host; the failure is part of the answer."""
    try:
        listInfo = socket.getaddrinfo(sHostname, None)
    except OSError as errorLookup:
        return {
            "bResolved": False, "listAddresses": [],
            "sError": f"{type(errorLookup).__name__}: {errorLookup}",
        }
    return {
        "bResolved": True,
        "listAddresses": sorted({tEntry[4][0] for tEntry in listInfo}),
        "sError": "",
    }


_DICT_DNS_VERDICT_MESSAGE = {
    S_DNS_BOTH_RESOLVE: (
        "both this host and the container resolve {sHostname}."
    ),
    S_DNS_CONTAINER_FAULT: (
        "this host resolves {sHostname} and the container does not. "
        "The fault is the container's or the daemon's resolver, not "
        "the network you are on."
    ),
    S_DNS_HOST_FAULT_NOT_IMPAIRING: (
        "the container resolves {sHostname} and this host does not. "
        "That is a host resolver problem, and it is not currently "
        "impairing this container."
    ),
    S_DNS_BOTH_FAIL: (
        "neither this host nor the container resolves {sHostname}. "
        "This is a host or upstream DNS failure: no vaibify command "
        "fixes it."
    ),
}


def _fsDnsRemediation(sVerdict, sResolverKind):
    """Return the remedy for one paired verdict, or '' when none applies."""
    if sVerdict == S_DNS_BOTH_FAIL:
        return (
            "Check the network this machine is on. Do not point "
            "anything at a public resolver: on a campus or corporate "
            "network that breaks split-horizon DNS and makes internal "
            "names unreachable."
        )
    if sVerdict == S_DNS_HOST_FAULT_NOT_IMPAIRING:
        return (
            "Nothing to do for this container -- it resolves names "
            "fine. Your own shell's tools (git, pip, curl) will fail "
            "on this name until the host resolver recovers."
        )
    if sVerdict != S_DNS_CONTAINER_FAULT:
        return ""
    if sResolverKind == S_RESOLVER_EXPLICIT_DNS:
        return (
            "The DNS servers are baked into this container, so a "
            "restart cannot change them. Recreate it from the vaibify "
            "configuration."
        )
    return (
        "The resolver state clears when the container restarts. Note "
        "that a restart re-runs the entrypoint and kills every live "
        "shell and agent in the container."
    )


def _flistReportDnsPair(dictTarget, dictHost, dictContainer, sResolverKind):
    """Report the paired verdict as ONE result about both facts."""
    sVerdict = fsClassifyDnsPair(
        dictHost["bResolved"], bool(dictContainer.get("listAddresses")),
    )
    sMessage = _DICT_DNS_VERDICT_MESSAGE[sVerdict].format(
        sHostname=dictTarget["sHostname"],
    )
    sLevel = (
        S_LEVEL_OK if sVerdict == S_DNS_BOTH_RESOLVE
        else S_LEVEL_WARN if sVerdict == S_DNS_HOST_FAULT_NOT_IMPAIRING
        else S_LEVEL_FAIL
    )
    sCommand = (
        "vaibify repair dns" if sVerdict == S_DNS_CONTAINER_FAULT
        and sResolverKind != S_RESOLVER_EXPLICIT_DNS else ""
    )
    return [PreflightResult(
        sName="dns-resolution", sLevel=sLevel, sScope=S_SCOPE_CONTAINER,
        sMessage=(
            sMessage + " The name resolved is "
            + dictTarget["sOrigin"] + "."
        ),
        sRemediation=_fsDnsRemediation(sVerdict, sResolverKind),
        sCommand=sCommand,
        sMechanism=(
            "Resolves the SAME name from both sides -- "
            "socket.getaddrinfo on this host, and a typed-read "
            "getaddrinfo inside the container -- and classifies the "
            "pair. Either answer alone means different things "
            "depending on the other."
        ),
    )]


def _flistExplainAddressFamily(dictHost, dictContainer, dictTransport):
    """Explain a transport failure with the address family, or say nothing.

    Never a verdict of its own. An AAAA-only answer is perfectly
    correct on a v6 network, and reporting it as a fault where nothing
    failed would put a warning in front of every dual-stack user.
    """
    if not dictTransport or dictTransport.get("bConnected"):
        return []
    listAddresses = list(dictContainer.get("listAddresses") or [])
    if not listAddresses or any(
        ":" not in sAddress for sAddress in listAddresses
    ):
        return []
    return [PreflightResult(
        sName="address-family", sLevel=S_LEVEL_INFO,
        sScope=S_SCOPE_CONTAINER,
        sMessage=(
            "the container resolved only IPv6 addresses ("
            + ", ".join(listAddresses)
            + "), which explains the connection failure above if this "
            "network carries no IPv6 route."
        ),
    )]


# -----------------------------------------------------------------------
# Stage 3 -- the effective proxy path, reported as facts.
# -----------------------------------------------------------------------


def _fdictReadProxyEnvironment(listEnvironment):
    """Return the proxy variables set in one environment, redacted."""
    dictProxy = {}
    for sEntry in listEnvironment or []:
        sName, _, sValue = str(sEntry).partition("=")
        if sName in _T_PROXY_VARIABLE_NAMES and sValue:
            dictProxy[sName] = fsRedactUrlCredentials(sValue)
    return dictProxy


def _fsDescribeProxySettings(dictProxy):
    """Return a readable, already-redacted summary of proxy settings."""
    if not dictProxy:
        return "none set"
    return ", ".join(
        f"{sName}={dictProxy[sName]}" for sName in sorted(dictProxy)
    )


def ftSelectEffectiveProxy(dictContainerProxy, dictTarget):
    """Return ``(sProxyHost, iProxyPort)`` the container would use, or ('', 0).

    The container's OWN proxy variables decide, because the container
    is what does the connecting. ``NO_PROXY`` is honoured -- a host
    listed there is reached directly, and probing it through a proxy
    would manufacture a failure the real traffic never meets.

    Values here are already redacted for display, so the credential
    part of a proxy URL is gone before this sees it; the host and port
    are all a CONNECT needs, and no credential is ever sent.
    """
    sTargetHost = (dictTarget.get("sHostname") or "").lower()
    for sName in ("NO_PROXY", "no_proxy"):
        for sEntry in (dictContainerProxy.get(sName) or "").split(","):
            sEntry = sEntry.strip().lower().lstrip(".")
            if sEntry and (
                sTargetHost == sEntry or sTargetHost.endswith("." + sEntry)
            ):
                return ("", 0)
    listNames = (
        ["HTTPS_PROXY", "https_proxy"] if dictTarget.get("bUsesTls")
        else ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]
    )
    for sName in listNames:
        tSplit = urlsplit(dictContainerProxy.get(sName) or "")
        if tSplit.hostname:
            return (tSplit.hostname, tSplit.port or 8080)
    return ("", 0)


def _fdictReadContainerProxyEnvironment(jsonInspect):
    """Return the container's redacted proxy variables."""
    return _fdictReadProxyEnvironment(
        (jsonInspect.get("Config") or {}).get("Env") or [],
    )


def _flistReportProxyPath(jsonInspect):
    """Report host and container proxy settings; a mismatch is not a fault.

    Docker Desktop applies host or PAC proxy settings that appear in no
    shell, and a container legitimately needs a different ``NO_PROXY``
    than the machine hosting it. Grading a difference would put a
    warning in front of every correctly-configured corporate laptop.
    It becomes a diagnosis only when a proxy-aware connection attempt
    produces evidence, which is stage 4's job.

    Every value is redacted on the way out: a proxy URL routinely
    carries ``user:password@``.
    """
    dictHostProxy = _fdictReadProxyEnvironment(
        [f"{sName}={os.environ[sName]}" for sName in _T_PROXY_VARIABLE_NAMES
         if os.environ.get(sName)],
    )
    dictContainerProxy = _fdictReadProxyEnvironment(
        (jsonInspect.get("Config") or {}).get("Env") or [],
    )
    if not dictHostProxy and not dictContainerProxy:
        return []
    return [PreflightResult(
        sName="proxy-path", sLevel=S_LEVEL_INFO,
        sScope=S_SCOPE_CONTAINER,
        sMessage=(
            "proxy settings -- this shell: "
            + _fsDescribeProxySettings(dictHostProxy)
            + "; the container: "
            + _fsDescribeProxySettings(dictContainerProxy)
            + ". A difference is not itself a fault."
        ),
        sMechanism=(
            "Reads the proxy variables from this shell and from the "
            "container's created environment, and redacts both. It "
            "cannot see settings Docker Desktop applies from the "
            "operating system or a PAC file."
        ),
    )]


# -----------------------------------------------------------------------
# Stage 4 -- transport, only with --online.
# -----------------------------------------------------------------------


def _fsDescribeProbedPath(dictTarget, dictProbe):
    """Return the endpoint and, when one was used, the path to it."""
    sPath = (
        str(dictTarget.get("sHostname") or "?") + ":"
        + str(dictTarget.get("iPort") or "?")
    )
    if dictProbe.get("bThroughProxy"):
        return sPath + " through the container's configured proxy"
    return sPath


def _flistReportTransport(dictTarget, dictProbe, bOnline):
    """Report the transport probe, or say plainly that it did not run."""
    if not bOnline:
        # INFO, not "not checked", and the difference is deliberate.
        # "Not checked" means a check that could not be assessed, and
        # it is what drives the exit code for a scope the researcher
        # asked about by name. This probe was not attempted because
        # nobody asked for it -- declining to open a connection is a
        # policy the researcher set, not an answer vaibify failed to
        # get -- and grading it otherwise would make every ordinary
        # `doctor --container` run exit non-zero, which is how an exit
        # code stops meaning anything. The message still establishes
        # nothing about transport, and says so.
        return [PreflightResult(
            sName="egress-transport", sLevel=S_LEVEL_INFO,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "reaching " + dictTarget["sHostname"] + ":"
                + str(dictTarget.get("iPort") or "?") + " over TCP was "
                "not attempted, so nothing here says whether this "
                "container can; pass --online to permit it."
            ),
        )]
    if not dictProbe.get("bAnswered"):
        return [PreflightResult(
            sName="egress-transport", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the transport probe could not run inside the "
                "container: " + str(dictProbe.get("sError") or "")
            ),
        )]
    sPath = _fsDescribeProbedPath(dictTarget, dictProbe)
    if dictProbe.get("bConnected") and not dictTarget.get("bUsesTls"):
        return [PreflightResult(
            sName="egress-transport", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container opened a connection to " + sPath
                + " (this transport carries no TLS, so none was "
                "negotiated)."
            ),
        )]
    if dictProbe.get("bTlsVerified"):
        return [PreflightResult(
            sName="egress-transport", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container reached " + sPath + " at "
                + str(dictProbe.get("sAddress") or "?")
                + " and completed TLS."
            ),
        )]
    if dictProbe.get("sProxyError"):
        return [PreflightResult(
            sName="egress-transport", sLevel=S_LEVEL_FAIL,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container's proxy refused to open a connection to "
                + sPath + ": " + str(dictProbe["sProxyError"])
            ),
            sRemediation=(
                "This is the proxy answering, not the destination -- "
                "name resolution and the route to the proxy both "
                "worked. A 407 means the proxy wants credentials the "
                "container does not have; vaibify sends none by "
                "design. Configure the proxy credentials in the "
                "container's environment, or use a proxy that does "
                "not require them for this host."
            ),
        )]
    if dictProbe.get("bConnected"):
        return [PreflightResult(
            sName="egress-transport", sLevel=S_LEVEL_WARN,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container opened a connection to " + sPath
                + " but TLS did not verify: "
                + str(dictProbe.get("sTlsError") or "")
            ),
            sRemediation=(
                "A verification failure on a working connection is "
                "the signature of an intercepting proxy. Install its "
                "certificate authority in the image rather than "
                "disabling verification."
            ),
        )]
    return [PreflightResult(
        sName="egress-transport", sLevel=S_LEVEL_FAIL,
        sScope=S_SCOPE_CONTAINER,
        sMessage=(
            "the container could not open a connection to " + sPath
            + ": " + str(dictProbe.get("sError") or "")
        ),
        sRemediation=(
            "Name resolution succeeded, so this is the transport, not "
            "DNS. If this network requires a proxy, the container "
            "needs its settings too."
        ),
    )]


# -----------------------------------------------------------------------
# The graph walk.
# -----------------------------------------------------------------------


def fsReadContainerResolvConf(connectionDocker, sContainerName):
    """Return the container's /etc/resolv.conf, or '' when unreadable."""
    try:
        return connectionDocker.fbaFetchFile(
            sContainerName, "/etc/resolv.conf",
        ).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _fsSummarizeNameservers(sResolvConf):
    """Return the nameserver addresses named in a resolv.conf, comma-joined."""
    listServers = [
        sLine.split()[1] for sLine in (sResolvConf or "").splitlines()
        if sLine.strip().startswith("nameserver") and len(sLine.split()) > 1
    ]
    return ", ".join(listServers)


def _flistProbeResolutionStages(
    config, sContainerName, connectionDocker, sResolverKind, bOnline,
    dictContainerProxy=None,
):
    """Run stages 2, 4 and 5 for a project that HAS a probe target.

    ``dictContainerProxy`` is stage 3's finding, threaded down because
    stage 4 must dial along the EFFECTIVE path rather than making a
    naive direct connection -- a direct dial from behind a corporate
    proxy fails for every container, working or not, and reporting
    that as broken egress is the misclassification this stage exists
    to avoid.
    """
    dictContainerProxy = dictContainerProxy or {}
    dictTarget = fdictResolveProbeTarget(config)
    if not dictTarget["sHostname"]:
        return [PreflightResult(
            sName="dns-resolution", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "this project configures no repository and enables no "
                "agent provider, so there is no name it already "
                "depends on to resolve. Doctor will not introduce a "
                "third party of its own to test with."
            ),
        )]
    dictHost = _fdictResolveOnHost(dictTarget["sHostname"])
    dictContainer = connectionDocker.fdictResolveHostnameInContainer(
        sContainerName, dictTarget["sHostname"], _F_PROBE_TIMEOUT_SECONDS,
    )
    if not dictContainer.get("bAnswered"):
        return [PreflightResult(
            sName="dns-resolution", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container could not answer a name lookup at all: "
                + str(dictContainer.get("sError") or "")
            ),
        )]
    listResults = _flistReportDnsPair(
        dictTarget, dictHost, dictContainer, sResolverKind,
    )
    dictTransport = {}
    if bOnline and dictContainer.get("listAddresses"):
        sProxyHost, iProxyPort = ftSelectEffectiveProxy(
            dictContainerProxy, dictTarget,
        )
        dictTransport = connectionDocker.fdictProbeTcpHandshakeInContainer(
            sContainerName, dictTarget["sHostname"],
            dictTarget["iPort"], _F_PROBE_TIMEOUT_SECONDS,
            dictTarget["bUsesTls"], sProxyHost, iProxyPort,
        )
    if dictContainer.get("listAddresses"):
        listResults.extend(
            _flistReportTransport(dictTarget, dictTransport, bOnline),
        )
        listResults.extend(_flistExplainAddressFamily(
            dictHost, dictContainer, dictTransport,
        ))
    return listResults


def flistDiagnoseContainerNetwork(
    config, sContainerName, connectionDocker, bOnline=False,
):
    """Walk the network decision graph for one running container.

    Stops after stage 1 for an intentionally isolated container, and
    emits exactly one informational result for it -- the whole point
    of treating "not applicable" as a successful assessment rather
    than as a state.
    """
    jsonInspect = fjsonInspectContainer(sContainerName)
    if not jsonInspect:
        return [PreflightResult(
            sName="network-attachment", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the daemon did not describe this container, so none "
                "of its network state could be read."
            ),
        )]
    bIsolated, sEvidence = ftDescribeIsolation(jsonInspect)
    if bIsolated:
        return [_fpreflightIsolation(sEvidence)]
    listResults = _flistReportAttachments(jsonInspect)
    if sEvidence:
        listResults.append(PreflightResult(
            sName="network-isolation", sLevel=S_LEVEL_WARN,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "this container claims isolation but does not have "
                "it: " + sEvidence + "."
            ),
            sRemediation=(
                "Recreate the container so its runtime state matches "
                "the isolation the project asked for."
            ),
        ))
    sResolvConf = fsReadContainerResolvConf(
        connectionDocker, sContainerName,
    )
    sResolverKind = fsClassifyResolverConfiguration(jsonInspect, sResolvConf)
    listResults.extend(_flistReportResolverConfiguration(
        sResolverKind, _fsSummarizeNameservers(sResolvConf),
    ))
    dictContainerProxy = _fdictReadContainerProxyEnvironment(jsonInspect)
    listResults.extend(_flistProbeResolutionStages(
        config, sContainerName, connectionDocker, sResolverKind, bOnline,
        dictContainerProxy,
    ))
    listResults.extend(_flistReportProxyPath(jsonInspect))
    return listResults
