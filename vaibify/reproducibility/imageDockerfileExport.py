"""Export the image's build chain into the project repo as a Dockerfile.

PROOF Level 3 reads ``<projectRepo>/Dockerfile``, and a vaibify
project has none: the image is built from vaibify's OWN packaged
Dockerfiles, which live in the wheel and not in the researcher's
repository. So the L3 Dockerfile row opened red for every project with
no action a researcher could take that vaibify did not already know
how to do for them.

This module is the host half of that action. It answers three
questions the pure composer cannot: WHICH overlays built the pinned
image (from its own overlays label, else the project's ``vaibify.yml``
located through the registry), WHAT the packaged Dockerfiles say (through
``resources.fpathContainerImageRoot``, never a ``parents[N]`` walk),
and WHETHER writing is allowed (never over a file vaibify did not
generate).

**This reads HOST filesystem state.** The registry and the
``vaibify.yml`` it points at are the researcher's own files, outside
the workspace volume, so the route that calls this must reject the
agent-token lane at the handler — the action catalog cannot express
that capability on its own.
"""

import os

from vaibify import resources
from vaibify.reproducibility.dockerfileComposer import (
    fbTextWasGeneratedByVaibify,
    fsComposeImageDockerfile,
    fsComputeRecipeFingerprint,
)
from vaibify.reproducibility.dockerfileLint import S_DOCKERFILE_FILENAME
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "fsBuildImageDockerfileText",
    "fsRefusalIfDockerfileNotReplaceable",
    "flistResolveOverlayNamesForContainer",
]


def flistResolveOverlayNamesForContainer(sContainerName, sImageDigest=""):
    """Return the ordered overlay names the PINNED image was built with.

    Read off the pinned image's own overlays label when it carries one:
    the envelope pins the agent-free environment, and ``vaibify.yml``
    also names the coding agents stacked above it, which the pinned
    image does not hold -- a Dockerfile composed from the config would
    describe an image nobody pinned. An image built before the label
    existed falls back to the project's ``vaibify.yml``, which is what
    built it. Inferring the list from installed binaries would be a
    guess dressed as provenance. Returns an empty list for a project
    whose config cannot be read — a base-only chain is still an honest
    artifact, and the header names the overlays it composed.
    """
    from vaibify.config.projectConfig import fconfigLoadFromFile
    from vaibify.config.registryManager import flistGetAllProjects
    from vaibify.docker.imageBuilder import flistDetermineOverlays
    listLabelled = _flistReadPinnedOverlaysLabel(sImageDigest)
    if listLabelled is not None:
        return listLabelled
    sConfigPath = _fsConfigPathForContainer(
        flistGetAllProjects(), sContainerName,
    )
    if not sConfigPath or not os.path.isfile(sConfigPath):
        return []
    return flistDetermineOverlays(fconfigLoadFromFile(sConfigPath))


def _flistReadPinnedOverlaysLabel(sImageDigest):
    """Return the pinned image's overlays label as a list, or ``None``.

    ``None`` -- no pin, no label, an unreachable daemon, or a label this
    vaibify cannot parse -- means the image does not say, and the
    caller falls back; an empty list is the image saying "no overlays".
    """
    from vaibify.docker.imageBuilder import flistCanonicalOverlayOrder
    from vaibify.reproducibility.dockerfileComposer import (
        S_OVERLAYS_IMAGE_LABEL, flistParseOverlaysLabel,
    )
    from vaibify.reproducibility.environmentSnapshot import (
        _fsInspectFormatValue,
    )
    if not sImageDigest:
        return None
    try:
        sValue = _fsInspectFormatValue(
            sImageDigest,
            '{{index .Config.Labels "' + S_OVERLAYS_IMAGE_LABEL + '"}}',
        ).strip()
        if sValue == "<no value>":
            return None
        return flistParseOverlaysLabel(sValue, flistCanonicalOverlayOrder())
    except Exception:  # noqa: BLE001 — an unreadable label does not say
        return None


def _fsConfigPathForContainer(listProjects, sContainerName):
    """Return the registry's config path for a container name, or ''."""
    for dictProject in listProjects or []:
        if (dictProject or {}).get("sContainerName") == sContainerName:
            return (dictProject or {}).get("sConfigPath") or ""
    return ""


def fsBuildImageDockerfileText(sContainerName, sImageDigest=""):
    """Return the composed multi-stage Dockerfile for this container.

    The header carries the recipe fingerprint of the texts this
    composition read, computed by the same function the image builder
    stamps onto the image as a label — equal values later PROVE the
    exported file describes the pinned image's actual build inputs.
    """
    listOverlayNames = flistResolveOverlayNamesForContainer(
        sContainerName, sImageDigest,
    )
    pathImageRoot = resources.fpathContainerImageRoot()
    sBaseText = _fsReadPackagedDockerfile(
        pathImageRoot, S_DOCKERFILE_FILENAME,
    )
    listTOverlays = _flistTReadOverlays(pathImageRoot, listOverlayNames)
    return fsComposeImageDockerfile(
        sBaseText, listTOverlays,
        sImageDigest=sImageDigest,
        sRecipeFingerprint=fsComputeRecipeFingerprint(
            sBaseText, listTOverlays,
        ),
    )


def _flistTReadOverlays(pathImageRoot, listOverlayNames):
    """Return ``[(sOverlayName, sText)]`` in application order.

    An overlay whose packaged file is missing is SKIPPED rather than
    faked, and its name therefore drops out of the generated header
    too, so the artifact never claims a layer it did not include.
    """
    from vaibify.docker.imageBuilder import _DICT_OVERLAY_DOCKERFILE_MAP
    listPairs = []
    for sOverlayName in listOverlayNames or []:
        sRelativePath = _DICT_OVERLAY_DOCKERFILE_MAP.get(sOverlayName)
        if not sRelativePath:
            continue
        try:
            listPairs.append((
                sOverlayName,
                _fsReadPackagedDockerfile(pathImageRoot, sRelativePath),
            ))
        except OSError:
            continue
    return listPairs


def _fsReadPackagedDockerfile(pathImageRoot, sRelativePath):
    """Return the text of one packaged Dockerfile."""
    with open(
        os.path.join(str(pathImageRoot), sRelativePath),
        "r", encoding="utf-8",
    ) as fileHandle:
        return fileHandle.read()


def fsRefusalIfDockerfileNotReplaceable(filesRepo):
    """Return a refusal message, or '' when writing is allowed.

    A repository Dockerfile that vaibify did not generate is the
    researcher's own, and a button press must never overwrite it: the
    file may be what actually builds their image, and losing it would
    destroy the very provenance this feature exists to record.
    Vaibify's own artifact carries a marker on its first line, so
    refreshing one after a rebuild stays a single click, and deleting
    that line is how a researcher adopts the file as theirs.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.fbIsFile(S_DOCKERFILE_FILENAME):
        return ""
    try:
        sExisting = filesRepo.fsReadText(S_DOCKERFILE_FILENAME)
    except (OSError, KeyError, ValueError):
        return (
            "A Dockerfile already exists in the project repository and "
            "could not be read to check whether vaibify generated it. "
            "Refusing to overwrite it."
        )
    if fbTextWasGeneratedByVaibify(sExisting):
        return ""
    return (
        "The project repository already has a Dockerfile that vaibify "
        "did not generate. Refusing to overwrite it — it may be what "
        "builds your image. Move or delete it first if you want "
        "vaibify's composed copy in its place."
    )
