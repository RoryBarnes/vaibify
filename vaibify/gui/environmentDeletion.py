"""Permanently deleting one container environment, and saying what went.

"Remove from list" un-registers a project and leaves every byte where
it was, which is the right default and was for a long time the only
door: the modal told the researcher to open a terminal and run
``vaibify destroy`` for the rest. This module is the rest, done from
the dashboard.

WHAT IS DELETED, and why the set is wider than one image. A project's
image is not a single tag -- a build leaves ``:base``, one tag per
overlay and ``:latest``, all in the repository named after the project
-- and its state is not a single volume: the workspace volume holds the
work and a second, credentials volume holds the in-container keyring so
tokens survive a rebuild. Deleting only ``<name>:latest`` and
``<name>-workspace`` would leave most of the bytes on the daemon under
a dashboard that no longer lists them, which is the worst of both
outcomes -- unreachable AND not reclaimed.

WHAT IS NOT DELETED, stated because a destructive action must be exact
about its blast radius. The researcher's own project directory on the
host, their git history, and anything they pulled out of the container
are untouched; so are host-side credentials (an Overleaf token lives in
the host keyring, not in the credentials volume). Host projects are
refused entirely at the route: they own no container, no volume and no
image, so "delete" there could only mean deleting the researcher's own
files, which no dashboard button will ever do.

THE ORDER IS NOT ARBITRARY and the registry entry goes LAST. A volume
cannot be removed while a container holds it, so the container goes
first; and if any Docker removal fails, the registry entry is KEPT, so
the environment is still listed, still nameable, and the researcher can
retry or clean up by hand. An entry removed over a failed removal would
leave orphaned bytes with nothing in the dashboard pointing at them.
The report says which of the four happened, so the dashboard reports
the deletion that actually occurred rather than the one that was asked
for.
"""

__all__ = [
    "fdictDeleteEnvironment",
    "fsConfirmationPhraseFor",
]

import logging

from vaibify.config import registryManager
from vaibify.config.keepAliveManager import fnStopKeepAlive
from vaibify.docker import containerManager, imageBuilder, volumeManager

logger = logging.getLogger(__name__)


def fsConfirmationPhraseFor(sName):
    """Return the exact phrase a researcher must type to delete ``sName``.

    Defined HERE and validated on the server, so the confirmation is a
    real gate rather than a dialog the client could skip. The modal
    builds the same sentence to show it, the way the step-slug rule has
    a display-only mirror in the frontend: this side is the authority.
    """
    return f"permanently delete {sName}"


def fdictDeleteEnvironment(dictProject):
    """Delete one container environment; return what was actually removed.

    Blocking Docker work throughout, so callers on the event loop must
    run it in a thread.
    """
    sName = dictProject["sName"]
    sContainerName = dictProject.get("sContainerName") or sName
    listFailures = []
    bContainerRemoved = _fbRemoveContainer(sContainerName, listFailures)
    listVolumes = (
        _flistRemoveVolumes(sName, listFailures)
        if bContainerRemoved else []
    )
    listImages = (
        _flistRemoveImages(sContainerName, listFailures)
        if bContainerRemoved else []
    )
    return {
        "sName": sName,
        "bContainerRemoved": bContainerRemoved,
        "listVolumesRemoved": listVolumes,
        "listImagesRemoved": listImages,
        "bRegistryEntryRemoved": (
            not listFailures and _fbRemoveRegistryEntry(sName, listFailures)
        ),
        "listFailures": listFailures,
    }


def _fbRemoveContainer(sContainerName, listFailures):
    """Stop the container, end its keep-alive, and PROVE it is gone.

    The proof is the point. ``fnRemoveStopped`` swallows every error by
    design (it is called on paths where a missing container is normal),
    so a deletion that trusted it would go on to report volumes and
    images removed while the container quietly survived. A daemon that
    does not answer the presence probe at all is a failure, never a
    "no such container".
    """
    fnStopKeepAlive(sContainerName)
    try:
        dictStatus = containerManager.fdictGetContainerStatus(sContainerName)
        if dictStatus["bRunning"]:
            containerManager.fnStopContainer(sContainerName)
        elif dictStatus["bExists"]:
            containerManager.fnRemoveStopped(sContainerName)
    except Exception as error:  # noqa: BLE001 — reported, never swallowed
        logger.error("Delete: stopping %s failed: %s", sContainerName, error)
        listFailures.append(f"could not remove the container: {error}")
        return False
    dictPresence = containerManager.fdictProbeContainerPresence(
        sContainerName,
    )
    if not dictPresence["bAnswered"]:
        listFailures.append(
            "the Docker daemon did not answer, so the container could "
            "not be proven removed"
        )
        return False
    if dictPresence["bPresent"]:
        listFailures.append("the container is still present after removal")
        return False
    return True


def _flistRemoveVolumes(sProjectName, listFailures):
    """Remove the workspace and credentials volumes that still exist."""
    listRemoved = []
    for sVolumeName in (
        volumeManager.fsWorkspaceVolumeNameForProject(sProjectName),
        volumeManager.fsCredentialsVolumeNameForProject(sProjectName),
    ):
        if not volumeManager.fbVolumeExists(sVolumeName):
            continue
        try:
            volumeManager.fnDestroyVolume(sVolumeName)
            listRemoved.append(sVolumeName)
        except Exception as error:  # noqa: BLE001 — reported to the caller
            logger.error("Delete: volume %s failed: %s", sVolumeName, error)
            listFailures.append(
                f"could not remove the volume {sVolumeName}: {error}"
            )
    return listRemoved


def _flistRemoveImages(sProjectName, listFailures):
    """Remove every tagged image in the project's own repository."""
    try:
        listReferences = imageBuilder.flistProjectImageReferences(
            sProjectName,
        )
    except Exception as error:  # noqa: BLE001 — reported to the caller
        logger.error("Delete: listing images for %s: %s", sProjectName, error)
        listFailures.append(f"could not list the project's images: {error}")
        return []
    listRemoved = []
    for sReference in listReferences:
        if imageBuilder.fbRemoveImage(sReference):
            listRemoved.append(sReference)
        else:
            listFailures.append(f"could not remove the image {sReference}")
    return listRemoved


def _fbRemoveRegistryEntry(sName, listFailures):
    """Un-register the project, taking its image-origin record with it."""
    try:
        registryManager.fnRemoveProject(sName)
        return True
    except KeyError:
        # Already absent: the environment is un-registered either way,
        # which is what this step is here to achieve.
        return True
    except Exception as error:  # noqa: BLE001 — reported to the caller
        logger.error("Delete: un-registering %s failed: %s", sName, error)
        listFailures.append(f"could not remove the registry entry: {error}")
        return False
