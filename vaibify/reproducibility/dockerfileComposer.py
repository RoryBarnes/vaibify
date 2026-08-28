"""Compose the image's build chain into one multi-stage Dockerfile.

``imageBuilder`` does not build an image from a single file. It builds
a CHAIN: the base ``Dockerfile``, then one ``docker build`` per enabled
feature overlay, each passed ``--build-arg BASE_IMAGE=<project>:<the
previous tag>``, and the last is tagged ``latest``. So the honest
answer to "which Dockerfile built this image" is a list, in order,
with a build argument threading them together.

A researcher's repository has one ``Dockerfile`` at its root, and
PROOF Level 3 reads that path. Copying only the base would put a file
there that did NOT build the image -- a false artifact, and worse than
none, because it looks like provenance. This module therefore emits
the chain as a single multi-stage file: the base becomes the first
stage, and each overlay's ``FROM ${BASE_IMAGE}`` is rewritten to name
the stage before it. Read top to bottom it performs the same steps in
the same order, which is what a reader wants from provenance.

**What the emitted file is and is not.** It records HOW the image was
made. It is not the reproduction recipe -- ``reproduce.sh`` runs
``docker pull`` against the digest in ``.vaibify/environment.json`` and
never builds -- and rebuilding from it will not reproduce the image
byte-for-byte, because the apt installs resolve against live archives.
The generated header says so in the file itself, so the statement
travels with the artifact rather than living only here.
"""

import re

from vaibify.reproducibility.dockerfileLint import S_DOCKERFILE_FILENAME


__all__ = [
    "S_GENERATED_MARKER",
    "S_BASE_STAGE_NAME",
    "fsComposeImageDockerfile",
    "fbTextWasGeneratedByVaibify",
    "fsStageNameForOverlay",
]


# Stamped into the generated header. The copy route refuses to
# overwrite a Dockerfile that does NOT carry it, so a researcher's own
# file can never be clobbered by a button press, while vaibify's own
# artifact stays refreshable when the image changes.
S_GENERATED_MARKER = "# vaibify:generated-image-dockerfile"

S_BASE_STAGE_NAME = "vaibifybase"

# Docker compares stage names case-insensitively and accepts
# [a-zA-Z0-9][a-zA-Z0-9_.-]*; overlay names like "nestedSampling"
# are camelCase, so they are lower-cased rather than passed through.
_REGEX_UNSAFE_STAGE_CHARACTER = re.compile(r"[^a-z0-9_.-]")

_REGEX_OVERLAY_BASE_ARG = re.compile(
    r"^\s*ARG\s+BASE_IMAGE\s*=.*$", re.IGNORECASE,
)
_REGEX_OVERLAY_FROM = re.compile(
    r"^\s*FROM\s+\$\{?BASE_IMAGE\}?\s*$", re.IGNORECASE,
)
_REGEX_BASE_FROM = re.compile(
    r"^(\s*FROM\s+\S+)\s*$", re.IGNORECASE,
)


def fsStageNameForOverlay(sOverlayName):
    """Return a Docker-legal stage name for an overlay name."""
    sLowered = str(sOverlayName or "").strip().lower()
    sCleaned = _REGEX_UNSAFE_STAGE_CHARACTER.sub("-", sLowered)
    return "stage-" + (sCleaned or "overlay")


def fbTextWasGeneratedByVaibify(sText):
    """Return True iff sText carries the generated-artifact marker."""
    return S_GENERATED_MARKER in (sText or "")


def fsComposeImageDockerfile(sBaseText, listTOverlays, sImageDigest=""):
    """Return the chain as one multi-stage Dockerfile.

    ``listTOverlays`` is an ordered list of ``(sOverlayName, sText)``
    pairs, in the same order ``imageBuilder`` applied them; the order
    IS the semantics, because each overlay builds on the previous
    image.
    """
    listParts = [_fsRenderHeader(listTOverlays, sImageDigest)]
    listParts.append(_fsNameBaseStage(sBaseText))
    sPreviousStage = S_BASE_STAGE_NAME
    for sOverlayName, sOverlayText in listTOverlays:
        sStageName = fsStageNameForOverlay(sOverlayName)
        listParts.append(
            _fsRewriteOverlay(
                sOverlayName, sOverlayText, sPreviousStage, sStageName,
            )
        )
        sPreviousStage = sStageName
    return "\n".join(listParts)


def _fsNameBaseStage(sBaseText):
    """Return the base Dockerfile with its ``FROM`` given a stage name.

    Only the FIRST ``FROM`` is named. A base that already declares its
    own stages keeps them; appending ``AS`` to every FROM would rename
    a stage the file's own later lines refer to.
    """
    listOut = []
    bNamed = False
    for sLine in sBaseText.splitlines():
        if not bNamed and _REGEX_BASE_FROM.match(
            _fsStripComment(sLine),
        ):
            listOut.append(sLine.rstrip() + f" AS {S_BASE_STAGE_NAME}")
            bNamed = True
            continue
        listOut.append(sLine)
    return "\n".join(listOut)


def _fsRewriteOverlay(
    sOverlayName, sOverlayText, sPreviousStage, sStageName,
):
    """Return one overlay re-pointed at the preceding stage.

    Its ``ARG BASE_IMAGE=...`` is dropped and its ``FROM ${BASE_IMAGE}``
    becomes ``FROM <previous stage> AS <this stage>``. Dropping the ARG
    matters: left in place its default (``vaibify:latest``) would be a
    floating tag the L3 lint rightly rejects, and it would describe a
    base this file no longer uses.
    """
    listOut = [
        "",
        f"# ---- overlay: {sOverlayName} " + "-" * 40,
        "",
    ]
    bRepointed = False
    for sLine in sOverlayText.splitlines():
        sStripped = _fsStripComment(sLine)
        if _REGEX_OVERLAY_BASE_ARG.match(sStripped):
            continue
        if not bRepointed and _REGEX_OVERLAY_FROM.match(sStripped):
            listOut.append(f"FROM {sPreviousStage} AS {sStageName}")
            bRepointed = True
            continue
        listOut.append(sLine)
    return "\n".join(listOut)


def _fsStripComment(sLine):
    """Return sLine with any trailing ``#`` comment removed."""
    iHash = sLine.find("#")
    return sLine if iHash < 0 else sLine[:iHash]


def _fsRenderHeader(listTOverlays, sImageDigest):
    """Return the provenance header stamped into the generated file."""
    sOverlays = ", ".join(
        sName for sName, _ in listTOverlays
    ) or "(none)"
    listLines = [
        S_GENERATED_MARKER,
        "#",
        f"# {S_DOCKERFILE_FILENAME} for this project's container image,",
        "# composed by vaibify from the image's own build chain:",
        f"#   base + overlays in order: {sOverlays}",
        "#",
        "# WHAT THIS FILE IS: a record of how the image was built. Each",
        "# stage below is one step of that chain, in the order it ran.",
        "#",
        "# WHAT IT IS NOT: the reproduction recipe. reproduce.sh runs",
        "# 'docker pull' against the image DIGEST recorded in",
        "# .vaibify/environment.json and never builds from this file.",
        "# Rebuilding from it will NOT reproduce the image byte-for-",
        "# byte -- the apt installs resolve against live archives, which",
        "# the 'allow-unpinned' markers declare rather than hide.",
        "#",
        "# Regenerate it after a rebuild rather than editing it; vaibify",
        "# refuses to overwrite a Dockerfile that lacks the marker on",
        "# the first line, so removing that line makes this file yours.",
    ]
    if sImageDigest:
        listLines.extend(["#", f"# image digest: {sImageDigest}"])
    listLines.append("")
    return "\n".join(listLines)
