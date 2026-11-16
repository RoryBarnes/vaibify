"""What a container's network IS, read from the daemon's inspect record.

The file says what a project ASKS for; this module says what the
running container HAS, so the doctor and the dashboard's Network row
can show both and say when they differ. Before it existed the doctor
decided "sealed" from the ``VAIBIFY_NETWORK_MODE`` flag alone, so a
container that kept the flag but held an unrestricted attachment read
as sealed, and the dashboard hid the row entirely unless the file named
a sealed mode, so a container created open under the old default had
no standing indicator at all.

It lives under ``vaibify/docker/`` so the CLI and the GUI both import
it and neither imports the other.

THE STATES
----------

``unknown``
    An empty or unreadable inspect record. Nothing is claimed.
``isolated``
    ``NetworkMode`` is ``none``: the container has no network.
``sealed``
    vaibify's sealed flag (``VAIBIFY_NETWORK_MODE=web`` or
    ``allowlist``) is present AND the container is attached to exactly
    one network, not a built-in one, whose attachment reports no
    gateway. Measured 2026-11-04 on Docker Engine 28.4.0: a container
    on a network created ``--internal`` with the bridge's gateway mode
    ``isolated`` reports ``Gateway: ""`` under its attachment, and one
    on the default bridge reports the bridge's address.
``disagreement``
    A flag the attachments contradict: a sealed flag beside a gateway,
    a built-in or absent attachment, or more than one; an isolation
    flag beside any attachment. The contradiction is the evidence.
``open``
    Everything else: the default bridge, ``host``, or a custom network
    with a gateway, and no sealed flag.
"""

__all__ = [
    "S_NETWORK_STATE_DISAGREEMENT",
    "S_NETWORK_STATE_ISOLATED",
    "S_NETWORK_STATE_OPEN",
    "S_NETWORK_STATE_SEALED",
    "S_NETWORK_STATE_UNKNOWN",
    "T_BUILT_IN_NETWORK_MODES",
    "fbFileModeAgreesWithContainerState",
    "fdictAssessContainerNetwork",
    "ftDescribeIsolation",
]

S_NETWORK_STATE_UNKNOWN = "unknown"
S_NETWORK_STATE_ISOLATED = "isolated"
S_NETWORK_STATE_SEALED = "sealed"
S_NETWORK_STATE_DISAGREEMENT = "disagreement"
S_NETWORK_STATE_OPEN = "open"

S_ISOLATION_ENVIRONMENT_FLAG = "VAIBIFY_NETWORK_ISOLATED=true"
# The flag a sealed-mode container is created with, one per sealed mode.
T_SEALED_ENVIRONMENT_FLAGS = (
    "VAIBIFY_NETWORK_MODE=allowlist", "VAIBIFY_NETWORK_MODE=web",
)
T_BUILT_IN_NETWORK_MODES = ("bridge", "default", "host", "none")

# The container state each file mode asks for; a mode absent here (the
# unstated one) agrees with nothing.
_DICT_STATE_FOR_FILE_MODE = {
    "isolated": S_NETWORK_STATE_ISOLATED,
    "open": S_NETWORK_STATE_OPEN,
    "web": S_NETWORK_STATE_SEALED,
    "allowlist": S_NETWORK_STATE_SEALED,
}


def _flistEnvironment(jsonInspect):
    """Return the environment the container was created with."""
    return (jsonInspect.get("Config") or {}).get("Env") or []


def _fdictAttachments(jsonInspect):
    """Return the container's network attachments by network name."""
    return (jsonInspect.get("NetworkSettings") or {}).get("Networks") or {}


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
    bFlagged = S_ISOLATION_ENVIRONMENT_FLAG in _flistEnvironment(jsonInspect)
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


def _fsDescribeSealedContradiction(dictNetworks):
    """Return why the attachments contradict the sealed flag, or ''."""
    listNamed = sorted(dictNetworks)
    if not listNamed:
        return "vaibify's sealed flag is set but it is attached to no network"
    if len(listNamed) > 1:
        return (
            "vaibify's sealed flag is set but it is attached to "
            f"{len(listNamed)} networks ({', '.join(listNamed)})"
        )
    sNetwork = listNamed[0]
    if sNetwork in T_BUILT_IN_NETWORK_MODES:
        return (
            "vaibify's sealed flag is set but it is attached to the "
            f"built-in network `{sNetwork}`"
        )
    sGateway = str((dictNetworks[sNetwork] or {}).get("Gateway") or "")
    if sGateway:
        return (
            "vaibify's sealed flag is set but its attachment to "
            f"`{sNetwork}` has a gateway ({sGateway})"
        )
    return ""


def _fsDescribeOpenAttachments(dictNetworks):
    """Return the evidence sentence for an open container."""
    listParts = []
    for sNetwork in sorted(dictNetworks):
        sGateway = str((dictNetworks[sNetwork] or {}).get("Gateway") or "")
        listParts.append(
            f"`{sNetwork}` (gateway {sGateway})" if sGateway
            else f"`{sNetwork}` (no gateway)")
    return "attached to " + ", ".join(listParts) + " with no sealed flag"


def fdictAssessContainerNetwork(jsonInspect):
    """Return ``{"sState", "sEvidence"}`` for one container's inspect record."""
    if not jsonInspect:
        return {"sState": S_NETWORK_STATE_UNKNOWN,
                "sEvidence": "the daemon did not describe this container"}
    bIsolated, sEvidence = ftDescribeIsolation(jsonInspect)
    if bIsolated:
        return {"sState": S_NETWORK_STATE_ISOLATED, "sEvidence": sEvidence}
    if sEvidence:
        return {"sState": S_NETWORK_STATE_DISAGREEMENT, "sEvidence": sEvidence}
    dictNetworks = _fdictAttachments(jsonInspect)
    bSealedFlag = any(
        sFlag in _flistEnvironment(jsonInspect)
        for sFlag in T_SEALED_ENVIRONMENT_FLAGS)
    if bSealedFlag:
        sContradiction = _fsDescribeSealedContradiction(dictNetworks)
        if sContradiction:
            return {"sState": S_NETWORK_STATE_DISAGREEMENT,
                    "sEvidence": sContradiction}
        return {"sState": S_NETWORK_STATE_SEALED, "sEvidence": (
            "vaibify's sealed flag is set and its one attachment, "
            f"`{next(iter(dictNetworks))}`, has no gateway")}
    if not dictNetworks:
        return {"sState": S_NETWORK_STATE_DISAGREEMENT, "sEvidence": (
            "it is attached to no network at all, and it was not "
            "created isolated")}
    return {"sState": S_NETWORK_STATE_OPEN,
            "sEvidence": _fsDescribeOpenAttachments(dictNetworks)}


def fbFileModeAgreesWithContainerState(sFileMode, sContainerState):
    """True when the file's mode and the container's state name one thing.

    ``unknown`` never disagrees (nothing was read to disagree with);
    ``disagreement`` never agrees; an unstated file mode agrees with
    no state.
    """
    if sContainerState == S_NETWORK_STATE_UNKNOWN:
        return True
    return _DICT_STATE_FOR_FILE_MODE.get(sFileMode) == sContainerState
