"""The address at which a container reaches the host: the bridge gateway.

A vaibify container dials the hub as ``host.docker.internal``, which the
container runtime resolves through ``--add-host ...:host-gateway``. On
macOS the daemon lives in a virtual machine and the VM forwards that
name to the host's loopback interface, so a hub listening on
``127.0.0.1`` answers. On Linux there is no VM: ``host-gateway`` is the
IPv4 gateway of the daemon's default ``bridge`` network (``172.17.0.1``
unless the daemon was told otherwise), and a packet from a container
arrives on the ``docker0`` interface, where a loopback-only socket
refuses it. Every in-container ``vaibify-do`` call on Linux failed this
way for as long as the agent bridge existed (found 2026-09-10); the
dashboard, which talks to the hub over loopback, never noticed.

The daemon is the authority on which address that is. Reading the
``docker0`` interface would agree by default and disagree the moment a
researcher renamed the default bridge in ``daemon.json``; the SDK
answer follows the daemon's own configuration. One thing the SDK
cannot see is a ``host-gateway-ip`` daemon override, which points
``host-gateway`` somewhere other than the bridge; a researcher running
one is expected to know it, and the hub prints the addresses it bound.

The client is constructed HERE, in the one function that uses it,
so the mutation inventory can read the SDK root: a client passed in
as a parameter is a blind spot the scan cannot trace, and this module
exists to ask one read-only question.
"""

__all__ = [
    "S_DEFAULT_BRIDGE_NETWORK",
    "fsResolveDockerBridgeGateway",
]

S_DEFAULT_BRIDGE_NETWORK = "bridge"


def fsResolveDockerBridgeGateway(iTimeoutSeconds):
    """Return the default bridge network's IPv4 gateway, or ``""``.

    ``""`` covers every way of not knowing: no daemon to talk to, a
    daemon that does not answer within ``iTimeoutSeconds``, a network
    with no IPAM block, or one that carries only an IPv6 gateway. The
    caller treats an empty answer as "loopback only" and says so,
    rather than guessing an address the daemon did not confirm. The
    one SDK call is a read (``networks.get``); nothing here can change
    the daemon's state.
    """
    import docker
    try:
        dockerClient = docker.from_env(timeout=iTimeoutSeconds)
        dictNetworkAttributes = dockerClient.networks.get(
            S_DEFAULT_BRIDGE_NETWORK,
        ).attrs
    except Exception:
        return ""
    listIpamConfig = (
        (dictNetworkAttributes or {}).get("IPAM") or {}
    ).get("Config") or []
    for dictIpamEntry in listIpamConfig:
        sGateway = str((dictIpamEntry or {}).get("Gateway") or "")
        if sGateway and ":" not in sGateway:
            return sGateway.split("/", 1)[0]
    return ""
