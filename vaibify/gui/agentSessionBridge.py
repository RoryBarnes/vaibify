"""Write agent-session metadata into a connected container.

When the UI connects to a container, the backend calls
:func:`fnPushAgentSessionToContainer` to materialize two files that
the in-container ``vaibify-do`` CLI reads:

- ``/tmp/vaibify-session.env`` (mode 600, owned by ``$CONTAINER_USER``)
  — host URL, session token, container id. Shell ``VAR=value`` format.
  Default ``docker exec`` runs as root, but the in-container agent
  runs as the unprivileged container user via ``gosu``; without the
  chown the agent gets ``Permission denied`` on the session file.
- ``/tmp/vaibify-action-catalog.json`` — the
  :data:`actionCatalog.LIST_AGENT_ACTIONS` catalog serialized. Stays
  world-readable; it carries no credentials.

Both paths come from :mod:`actionCatalog`'s shared constants.
"""

__all__ = [
    "fnPushAgentSessionToContainer",
]

import json

from . import actionCatalog


def fsBuildSessionEnvBody(sHostUrl, sSessionToken, sContainerId):
    """Return the session.env shell-format body."""
    listLines = [
        f"VAIBIFY_HOST_URL={sHostUrl}",
        f"VAIBIFY_SESSION_TOKEN={sSessionToken}",
        f"VAIBIFY_CONTAINER_ID={sContainerId}",
    ]
    return "\n".join(listLines) + "\n"


def fsBuildHostUrl(iPort):
    """Return the host URL the agent should dial."""
    if not iPort:
        iPort = 8050
    return f"http://host.docker.internal:{iPort}"


_I_CONTAINER_USER_UID = 1000
_I_CONTAINER_USER_GID = 1000
_I_SECRET_FILE_MODE = 0o600


def fnWriteSessionEnv(
    connectionDocker, sContainerId, sSessionToken, iPort,
):
    """Write /tmp/vaibify-session.env inside the container.

    The tarball entry is stamped with mode 0600 and the container
    user's uid/gid so the file lands already-private — there is no
    readable window between put_archive and a follow-up chmod (audit
    finding M1). The container user is created with UID 1000 by the
    Dockerfile; that constant is mirrored here.
    """
    sBody = fsBuildSessionEnvBody(
        fsBuildHostUrl(iPort), sSessionToken, sContainerId,
    )
    connectionDocker.fnWriteFile(
        sContainerId,
        actionCatalog.S_SESSION_ENV_PATH,
        sBody.encode("utf-8"),
        iMode=_I_SECRET_FILE_MODE,
        iUid=_I_CONTAINER_USER_UID,
        iGid=_I_CONTAINER_USER_GID,
    )


def fnWriteActionCatalog(connectionDocker, sContainerId):
    """Write /tmp/vaibify-action-catalog.json inside the container."""
    dictCatalog = actionCatalog.fdictBuildCatalogJson()
    sContent = json.dumps(dictCatalog, indent=2)
    connectionDocker.fnWriteFile(
        sContainerId,
        actionCatalog.S_CATALOG_JSON_PATH,
        sContent.encode("utf-8"),
    )


def fnPushAgentSessionToContainer(
    connectionDocker, sContainerId, sSessionToken, iPort,
):
    """Materialize session.env + action-catalog.json in the container."""
    fnWriteSessionEnv(
        connectionDocker, sContainerId, sSessionToken, iPort,
    )
    fnWriteActionCatalog(connectionDocker, sContainerId)
