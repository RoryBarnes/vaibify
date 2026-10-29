"""How a route or dispatcher finds the Zenodo token it may spend for a container.

The tokens live in per-project ACTIVE slots of the host keyring
(:mod:`vaibify.config.zenodoTokenSlots`), keyed by the registry's
project identity; a route knows a container id. This module makes the
translation in one place, so every token-bearing Zenodo action asks the
same question the same way, and it answers a missing token with the
sentence the researcher can act on: a pending migrated token awaits
confirmation, or none is stored and one must be entered.

Nothing here reads a pending slot's value: pending tokens are the
confirmation flow's business (:mod:`vaibify.gui.zenodoTokenMigration`).
"""

__all__ = [
    "S_ZENODO_TOKEN_MISSING",
    "ZenodoTokenUnavailable",
    "fsProjectUuidForContainer",
    "fsActiveZenodoToken",
    "fsDescribeMissingZenodoToken",
    "fsRequireActiveZenodoToken",
    "fnStoreActiveZenodoToken",
    "fnDeleteActiveZenodoToken",
    "fbHasActiveZenodoToken",
    "fsInstanceForService",
    "fsServiceForInstance",
]

from vaibify.config import zenodoTokenSlots
from vaibify.config.registryManager import fdictGetProject, fsProjectUuidFor

# The refusal the dashboard turns into a credential prompt rather than a
# toast. Named rather than matched on prose.
S_ZENODO_TOKEN_MISSING = "ZENODO-TOKEN-MISSING"

_DICT_INSTANCE_BY_SERVICE = {"zenodo": "production", "sandbox": "sandbox"}
_DICT_SERVICE_BY_INSTANCE = {"production": "zenodo", "sandbox": "sandbox"}


class ZenodoTokenUnavailable(LookupError):
    """No active token is stored for this project and instance."""

    def __init__(self, sInstance, sMessage):
        super().__init__(sMessage)
        self.sInstance = sInstance
        self.sMessage = sMessage


def fsInstanceForService(sService):
    """Return the instance a ZenodoClient service key names (sandbox|production)."""
    if sService in zenodoTokenSlots.T_ZENODO_INSTANCES:
        return sService
    if sService not in _DICT_INSTANCE_BY_SERVICE:
        raise ValueError(f"Invalid Zenodo service: {sService}")
    return _DICT_INSTANCE_BY_SERVICE[sService]


def fsServiceForInstance(sInstance):
    """Return the ZenodoClient service key an instance name maps to."""
    if sInstance not in _DICT_SERVICE_BY_INSTANCE:
        raise ValueError(f"Invalid Zenodo instance: {sInstance}")
    return _DICT_SERVICE_BY_INSTANCE[sInstance]


def fsProjectUuidForContainer(connectionDocker, sContainerId):
    """Return the project identity serving a container, or '' when unregistered.

    A container the registry does not know has no identity and no
    tokens: its Zenodo actions refuse rather than fall back to a
    project-independent slot, which is exactly the shared store this
    module exists to close.
    """
    from .pipelineServer import fsContainerNameForId
    sName = fsContainerNameForId(connectionDocker, sContainerId)
    if not sName or fdictGetProject(sName) is None:
        return ""
    return fsProjectUuidFor(sName)


def fsActiveZenodoToken(connectionDocker, sContainerId, sService):
    """Return the ACTIVE token for ``sService``, or '' when none is stored."""
    sProjectUuid = fsProjectUuidForContainer(connectionDocker, sContainerId)
    if not sProjectUuid:
        return ""
    return zenodoTokenSlots.fsReadActiveToken(
        fsInstanceForService(sService), sProjectUuid,
    )


def fsDescribeMissingZenodoToken(connectionDocker, sContainerId, sService):
    """Return the sentence that says why no token can be spent, and the remedy."""
    sInstance = fsInstanceForService(sService)
    sProjectUuid = fsProjectUuidForContainer(connectionDocker, sContainerId)
    if not sProjectUuid:
        return (
            "This container is not a registered project, so no Zenodo "
            "token is stored for it; register the project first."
        )
    if _fbHasPendingToken(sProjectUuid):
        return (
            f"A Zenodo token moved out of this project's container is "
            f"awaiting your confirmation before it can be used on "
            f"{sInstance}; confirm or decline it in the Zenodo panel "
            f"(vaibify do decide-migrated-zenodo-token), or enter a token."
        )
    return (
        f"No {sInstance} Zenodo token is stored on this host for this "
        f"project. Add one and try again; where this project publishes "
        f"is unchanged."
    )


def _fbHasPendingToken(sProjectUuid):
    return bool(zenodoTokenSlots.flistPendingSlotsFor(sProjectUuid))


def fsRequireActiveZenodoToken(connectionDocker, sContainerId, sService):
    """Return the active token or raise :class:`ZenodoTokenUnavailable`.

    An unreadable host keyring raises the same class with its own
    sentence: it is a reason the action cannot run, and it is not the
    absence of a token.
    """
    sInstance = fsInstanceForService(sService)
    try:
        sToken = fsActiveZenodoToken(connectionDocker, sContainerId, sService)
    except zenodoTokenSlots.TokenStoreUnreadable as error:
        raise ZenodoTokenUnavailable(sInstance, str(error)) from error
    if sToken:
        return sToken
    raise ZenodoTokenUnavailable(
        sInstance,
        fsDescribeMissingZenodoToken(connectionDocker, sContainerId, sService),
    )


def _fsRequireProjectUuid(connectionDocker, sContainerId):
    sProjectUuid = fsProjectUuidForContainer(connectionDocker, sContainerId)
    if not sProjectUuid:
        raise ValueError(
            "This container is not a registered project, so no Zenodo "
            "token can be stored for it."
        )
    return sProjectUuid


def fnStoreActiveZenodoToken(
    connectionDocker, sContainerId, sInstance, sToken,
):
    """Write the project's active slot for an instance; entering is confirming."""
    zenodoTokenSlots.fnStoreActiveToken(
        sInstance, _fsRequireProjectUuid(connectionDocker, sContainerId),
        sToken,
    )


def fnDeleteActiveZenodoToken(connectionDocker, sContainerId, sInstance):
    """Remove the project's active slot for an instance."""
    zenodoTokenSlots.fnDeleteActiveToken(
        sInstance, _fsRequireProjectUuid(connectionDocker, sContainerId),
    )


def fbHasActiveZenodoToken(connectionDocker, sContainerId, sInstance):
    """Return True when an active token is stored for the instance."""
    sProjectUuid = fsProjectUuidForContainer(connectionDocker, sContainerId)
    return bool(sProjectUuid) and zenodoTokenSlots.fbHasActiveToken(
        sInstance, sProjectUuid,
    )
