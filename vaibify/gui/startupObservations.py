"""Host-side meaning for the codes the container entrypoint records.

The entrypoint writes a structured observation for every startup event
it notices -- a stable CODE, a UTC timestamp, the probe version, and a
verdict. It writes no remedy text, deliberately: a shell script baked
into an image cannot import this catalogue, and an image built months
ago would otherwise hand a researcher advice that has since changed.
The translation happens here, at read time, on the host.

Two properties this must keep.

**An unrecognised code is reported as unrecognised.** Images outlive
hub versions in both directions -- an old image writing a retired
code, a new image writing one this hub predates -- and inventing a
plausible meaning for either is how a diagnostic starts making things
up.

**These are historical, never current state.** The entrypoint runs on
``docker start``, so a laptop that changed networks an hour ago has an
observation saying resolution worked. A live probe supersedes every
row here, and any surface showing them must say when they were taken.
"""

__all__ = [
    "flistDescribeStartupObservations", "DICT_OBSERVATION_MEANINGS",
]


DICT_OBSERVATION_MEANINGS = {
    "dns-resolved": (
        "the container resolved a host this project depends on",
        "",
    ),
    "dns-resolution-failed": (
        "the container could not resolve a host this project depends on",
        "Run `vaibify doctor --container` for the live state; if it "
        "still cannot resolve, `vaibify repair dns` clears a stale "
        "resolver.",
    ),
    "dns-not-attempted": (
        "the resolver probe was skipped because this image has no "
        "`timeout`, so an unbounded lookup could have stalled startup",
        "",
    ),
    "dns-not-applicable": (
        "networking is disabled for this project, so no name was "
        "resolved",
        "",
    ),
    # A sealed container (web or allowlist) resolves no name itself, so
    # it is asked through its proxy, whose own refusal reasons each
    # carry a different remedy.
    "egress-reachable": (
        "the container reached a host this project depends on through "
        "its network proxy",
        "",
    ),
    "egress-proxy-unreachable": (
        "the container's network proxy did not answer, so nothing in "
        "the container reached the network",
        "The hub restores a missing proxy on its next sweep; if the "
        "Network row still says the proxy is down, restart the "
        "container.",
    ),
    "egress-not-allowlisted": (
        "the network proxy refused a host this project depends on: it "
        "is not on the allowlist",
        "Add the host to allowedHosts in vaibify.yml, or use the Allow "
        "host action on the Network row.",
    ),
    "egress-non-public-destination": (
        "the network proxy refused a host because its name resolves to "
        "a non-public address",
        "This is the seal working: a sealed project reaches only public "
        "addresses. A host on this computer or the local network needs "
        "networkMode: open.",
    ),
    "egress-proxy-saturated": (
        "the network proxy had too many connections open",
        "Usually transient; the next start checks again.",
    ),
    "egress-upstream-unreachable": (
        "the network proxy could not reach a host this project depends "
        "on",
        "Check this machine's connection, then restart the container.",
    ),
    "egress-tls-failed": (
        "the network proxy reached a host, but the secure connection to "
        "it failed",
        "The container log names the TLS error; a network that inspects "
        "TLS traffic is the usual cause.",
    ),
    "egress-refused": (
        "the network proxy refused a host this project depends on",
        "The container log quotes the proxy's reason.",
    ),
    "egress-not-checked": (
        "the network check through the proxy could not run",
        "",
    ),
    "clone-auth": (
        "a repository clone needed credentials the container did not "
        "have",
        "Authenticate on the host (`gh auth login`) and rebuild, or "
        "configure the credential as a project secret.",
    ),
    "clone-network": (
        "a repository clone could not reach the network",
        "Check this machine's connection, then `vaibify doctor "
        "--container --online`.",
    ),
    "clone-branch": (
        "a repository's configured branch does not exist on the remote",
        "Correct the `branch:` for that repository in vaibify.yml.",
    ),
    "clone-unknown": (
        "a repository clone failed for a reason the entrypoint could "
        "not classify",
        "The container log carries the first lines of git's own error.",
    ),
    "c-build": (
        "a compiled dependency failed to build",
        "The container log carries the compiler's output.",
    ),
    "pip-install": (
        "a Python package failed to install",
        "The container log carries pip's output.",
    ),
    "pip-requirements": (
        "a requirements file could not be installed in full",
        "The container log names which file and which line.",
    ),
    "requirements-dropped": (
        "packages a repository's .vaibify/requirements.txt declared are "
        "not installed in this image; that file is now a generated copy "
        "of the image's own list",
        "Add them to pythonPackages in vaibify.yml and Rebuild; the "
        "warning names each one.",
    ),
    "agent-update-deferred": (
        "an agent could not be updated because networking is disabled",
        "Expected for an isolated project; nothing to do.",
    ),
    "agent-update-failed": (
        "an agent's own updater failed",
        "Not fatal: the installed version still runs. Re-check after "
        "the next start.",
    ),
}


def flistDescribeStartupObservations(listObservations):
    """Return each observation with its host-side meaning attached."""
    listDescribed = []
    for dictObservation in listObservations or []:
        if not isinstance(dictObservation, dict):
            continue
        listDescribed.append(
            _fdictDescribeOneObservation(dictObservation),
        )
    return listDescribed


def _fdictDescribeOneObservation(dictObservation):
    """Attach the current meaning of one code, or say it is unknown."""
    sCode = str(dictObservation.get("sCode") or "")
    tMeaning = DICT_OBSERVATION_MEANINGS.get(sCode)
    if tMeaning is None:
        sSummary = (
            f"an observation this vaibify does not recognise ({sCode!r}); "
            "the image that wrote it is older or newer than this hub"
        )
        sRemedy = ""
    else:
        sSummary, sRemedy = tMeaning
    return {
        "sCode": sCode,
        "sSubject": str(dictObservation.get("sSubject") or ""),
        "sIso": str(dictObservation.get("sIso") or ""),
        "sVerdict": str(dictObservation.get("sVerdict") or ""),
        "sProbeVersion": str(dictObservation.get("sProbeVersion") or ""),
        "sSummary": sSummary,
        "sRemedy": sRemedy,
        "bRecognised": tMeaning is not None,
    }
