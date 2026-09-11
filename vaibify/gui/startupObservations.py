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
        "a requirements file was skipped entirely",
        "The container log names which file and why.",
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
