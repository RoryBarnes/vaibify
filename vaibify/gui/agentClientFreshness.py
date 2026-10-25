"""Whether a container's ``vaibify-do`` is the one this hub ships.

``vaibify-do`` is baked into the container image when it is built, so a
container keeps the client it was built with however many times vaibify
is upgraded on the host. A client fix therefore reaches no existing
container until its image is rebuilt, and nothing else records which
client an image carries: the recipe fingerprint covers the Dockerfile
texts and the configuration fingerprint covers ``vaibify.yml``.

The client sends the sha256 of its own file on every call, and the hub
compares it with the sha256 of the ``vaibify-do`` it ships. A different
digest, or none at all (a client older than the digest itself), is
stale. The action still runs; the agent is told to ask for a rebuild.
The presented digest is untrusted text, so it is only ever compared for
equality -- never echoed, logged or stored.
"""

import functools
import hashlib
import logging

from .. import resources

__all__ = [
    "S_CLIENT_DIGEST_HEADER",
    "S_CLIENT_DIGEST_QUERY",
    "S_CLIENT_WARNING_HEADER",
    "S_STALE_CLIENT_WARNING",
    "fsShippedClientDigest",
    "fbClientIsStale",
    "fsStaleClientWarning",
    "fsStaleClientWarningForSocket",
    "S_STALE_CLIENT_NOTICE",
    "fnNoticeStaleClientOnce",
]

logger = logging.getLogger("vaibify")

S_CLIENT_DIGEST_HEADER = "X-Vaibify-Client-Digest"
S_CLIENT_DIGEST_QUERY = "sClientDigest"
S_CLIENT_WARNING_HEADER = "X-Vaibify-Client-Warning"
S_VAIBIFY_DO_FILENAME = "vaibifyDo.py"
S_STALE_CLIENT_WARNING = (
    "This container's vaibify-do is older than the installed vaibify; "
    "ask the researcher to rebuild the container image."
)
S_STALE_CLIENT_NOTICE = (
    "This container's vaibify-do is older than the installed vaibify, "
    "so the in-container agent is using an outdated client. Rebuild "
    "the container image to update it."
)
S_STALE_CLIENT_NOTICE_KEY = "stale-vaibify-do"


@functools.lru_cache(maxsize=1)
def fsShippedClientDigest():
    """Return the sha256 of the packaged ``vaibify-do``, once per process.

    Returns "" when the packaged file cannot be read, so a broken
    installation cannot turn every agent request into a server error.
    """
    try:
        pathClient = (
            resources.fpathContainerImageRoot() / S_VAIBIFY_DO_FILENAME
        )
        return hashlib.sha256(pathClient.read_bytes()).hexdigest()
    except OSError as errorRead:
        logger.warning(
            "Cannot read the packaged vaibify-do (%s); stale in-container "
            "clients will not be reported", errorRead,
        )
        return ""


def fbClientIsStale(sPresentedDigest):
    """Return True when a presented client digest is not the shipped one.

    A missing digest is stale: only a client older than the digest
    itself omits it. When the hub cannot read its own copy it cannot
    judge, and says nothing.
    """
    sShippedDigest = fsShippedClientDigest()
    if not sShippedDigest:
        return False
    return sPresentedDigest != sShippedDigest


def fsStaleClientWarning(sPresentedDigest):
    """Return the warning for a stale client, or "" for a current one."""
    if fbClientIsStale(sPresentedDigest):
        return S_STALE_CLIENT_WARNING
    return ""


def fsStaleClientWarningForSocket(websocket):
    """Return the warning for an agent's pipeline socket, or "".

    Only the agent lane carries a client: the researcher's browser
    socket is never warned, whatever its query says.
    """
    from . import serverMiddleware

    if not serverMiddleware.fbRequestRidesAgentLane(websocket):
        return ""
    sWarning = fsStaleClientWarning(
        websocket.query_params.get(S_CLIENT_DIGEST_QUERY, ""),
    )
    if sWarning:
        fnNoticeStaleClientOnce(
            getattr(websocket.app.state, "dictRouteContext", None),
            websocket.path_params.get("sContainerId", ""),
        )
    return sWarning


def fnNoticeStaleClientOnce(dictCtx, sContainerId):
    """Tell the researcher once per container that its client is stale.

    The agent is warned on every call; the researcher, who is the one
    who can rebuild, is told once per hub process rather than toasted
    on every agent request.
    """
    from . import researcherNotices

    if dictCtx is None or not sContainerId:
        return
    researcherNotices.fnQueueResearcherNoticeOnce(
        dictCtx, sContainerId, S_STALE_CLIENT_NOTICE_KEY,
        researcherNotices.S_NOTICE_LEVEL_WARNING, S_STALE_CLIENT_NOTICE,
    )
