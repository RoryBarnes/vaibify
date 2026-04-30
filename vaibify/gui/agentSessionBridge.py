"""Write agent-session metadata into a connected container.

When the UI connects to a container, the backend calls
:func:`fnPushAgentSessionToContainer` to materialize two files that
the in-container ``vaibify-do`` CLI reads:

- ``/tmp/vaibify-session.env`` (mode 600) — host URL, session token,
  container id. Shell ``VAR=value`` format.
- ``/tmp/vaibify-action-catalog.json`` — the
  :data:`actionCatalog.LIST_AGENT_ACTIONS` catalog serialized.

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


def fnWriteSessionEnv(
    connectionDocker, sContainerId, sSessionToken, iPort,
):
    """Write /tmp/vaibify-session.env inside the container (mode 600)."""
    sBody = fsBuildSessionEnvBody(
        fsBuildHostUrl(iPort), sSessionToken, sContainerId,
    )
    connectionDocker.fnWriteFile(
        sContainerId,
        actionCatalog.S_SESSION_ENV_PATH,
        sBody.encode("utf-8"),
    )
    connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"chmod 600 {actionCatalog.S_SESSION_ENV_PATH}",
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
