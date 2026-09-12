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

import hashlib
import re

from vaibify.reproducibility.dockerfileLint import S_DOCKERFILE_FILENAME


__all__ = [
    "S_GENERATED_MARKER",
    "S_BASE_STAGE_NAME",
    "S_OVERLAYS_HEADER_PREFIX",
    "S_OVERLAYS_IMAGE_LABEL",
    "S_RECIPE_HEADER_PREFIX",
    "S_RECIPE_IMAGE_LABEL",
    "fsComposeImageDockerfile",
    "fbTextWasGeneratedByVaibify",
    "flistExtractOverlayOrder",
    "flistParseOverlaysLabel",
    "fsComputeRecipeFingerprint",
    "fsExtractRecipeFingerprint",
    "fsRenderOverlaysLabelValue",
    "fsStageNameForOverlay",
]


# Stamped into the generated header. The copy route refuses to
# overwrite a Dockerfile that does NOT carry it, so a researcher's own
# file can never be clobbered by a button press, while vaibify's own
# artifact stays refreshable when the image changes.
S_GENERATED_MARKER = "# vaibify:generated-image-dockerfile"

S_BASE_STAGE_NAME = "vaibifybase"

# The recipe fingerprint ties the exported Dockerfile to the IMAGE it
# claims to describe, with build-time evidence on both ends: the
# builder computes it over the exact base + overlay texts it is about
# to build with and stamps it onto the image as this label; the export
# computes it over the texts it composed from and writes it into the
# generated header. Equal fingerprints mean the repo Dockerfile
# describes the pinned image's actual recipe; unequal means the image
# was rebuilt from different inputs (an overlay change, a vaibify
# upgrade) after the Dockerfile was exported. Recompose-and-compare
# could not make this distinction: it reports the Dockerfile stale
# whenever the INSTALLED vaibify's packaged texts changed, even though
# the file still describes the pinned image perfectly.
S_RECIPE_IMAGE_LABEL = "vaibify-recipe-sha256"
S_RECIPE_HEADER_PREFIX = "# vaibify:recipe-sha256="

# The overlays an image was built with, stamped beside the recipe
# fingerprint as a comma-separated list in canonical order. It is what
# lets a project containerized from the author's PINNED image know which
# agents that image already holds -- proven by the image, never taken
# from a comment line in a cloned repository.
S_OVERLAYS_IMAGE_LABEL = "vaibify-overlays"
S_OVERLAYS_HEADER_PREFIX = "#   base + overlays in order: "
_S_OVERLAYS_HEADER_NONE = "(none)"

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


def fsComputeRecipeFingerprint(sBaseText, listTOverlays):
    """Return the SHA-256 over the exact build-chain inputs, in order.

    Both the builder and the exporter call this over the same
    ``(sBaseText, [(sOverlayName, sText), ...])`` shape, which is what
    makes label-equals-header a proof rather than a coincidence. The
    name separators keep the digest injective over the pair list — two
    different overlay splits of the same concatenated bytes must not
    collide. Build ARGUMENT values are deliberately outside it: the
    exported Dockerfile does not capture them either, so including
    them here would fail files that agree with everything the file
    says.
    """
    listParts = [(sBaseText or "").encode("utf-8")]
    for sOverlayName, sOverlayText in listTOverlays or []:
        listParts.append(b"\x00" + str(sOverlayName).encode("utf-8"))
        listParts.append(b"\x00" + (sOverlayText or "").encode("utf-8"))
    return hashlib.sha256(b"".join(listParts)).hexdigest()


def flistExtractOverlayOrder(sText):
    """Return the overlay list the generated header claims, or ``None``.

    ``None`` when the header line is absent; ``[]`` when it says
    ``(none)``. A CLAIM only: the header sits outside the recipe
    fingerprint, so a reader binds it to the image (through the
    overlays label or a recomputation) before trusting it.
    """
    for sLine in (sText or "").splitlines():
        if not sLine.startswith(S_OVERLAYS_HEADER_PREFIX):
            continue
        sBody = sLine[len(S_OVERLAYS_HEADER_PREFIX):].strip()
        if not sBody or sBody == _S_OVERLAYS_HEADER_NONE:
            return []
        return [sName.strip() for sName in sBody.split(",") if sName.strip()]
    return None


def fsRenderOverlaysLabelValue(listOverlays):
    """Return the label value for an overlay list: names joined by commas."""
    return ",".join(str(sName) for sName in listOverlays or [])


def flistParseOverlaysLabel(sValue, listCanonicalOrder):
    """Return the overlays an image label names, or raise ``ValueError``.

    The label is a SET rendered in canonical order: it says which
    overlays the image holds and nothing about the order they were
    installed in, so a differential stack on an obtained base and a
    build from scratch that hold the same overlays carry the same
    label. A malformed label -- an unknown name, a repeat, names out
    of the canonical order -- is refused rather than reordered or
    pruned: vaibify wrote the label, so a value that breaks its own
    contract says the image is not one this vaibify understands.
    """
    listNames = [
        sName.strip() for sName in str(sValue or "").split(",")
        if sName.strip()
    ]
    listUnknown = [s for s in listNames if s not in listCanonicalOrder]
    if listUnknown:
        raise ValueError(
            f"the {S_OVERLAYS_IMAGE_LABEL} label names overlays this "
            f"vaibify does not know: {', '.join(listUnknown)}"
        )
    if len(set(listNames)) != len(listNames):
        raise ValueError(
            f"the {S_OVERLAYS_IMAGE_LABEL} label repeats an overlay name"
        )
    listCanonical = [s for s in listCanonicalOrder if s in listNames]
    if listCanonical != listNames:
        raise ValueError(
            f"the {S_OVERLAYS_IMAGE_LABEL} label lists overlays out of "
            "the canonical order"
        )
    return listNames


def fsExtractRecipeFingerprint(sText):
    """Return the recipe fingerprint a generated header carries, or ''.

    Empty for a file exported before the fingerprint existed — the
    checker reads that as "nothing determined", never as a mismatch.
    """
    for sLine in (sText or "").splitlines():
        if sLine.startswith(S_RECIPE_HEADER_PREFIX):
            return sLine[len(S_RECIPE_HEADER_PREFIX):].strip()
    return ""


def fsComposeImageDockerfile(
    sBaseText, listTOverlays, sImageDigest="", sRecipeFingerprint="",
):
    """Return the chain as one multi-stage Dockerfile.

    ``listTOverlays`` is an ordered list of ``(sOverlayName, sText)``
    pairs, in the same order ``imageBuilder`` applied them; the order
    IS the semantics, because each overlay builds on the previous
    image.
    """
    listParts = [
        _fsRenderHeader(listTOverlays, sImageDigest, sRecipeFingerprint),
    ]
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


def _fsRenderHeader(listTOverlays, sImageDigest, sRecipeFingerprint=""):
    """Return the provenance header stamped into the generated file."""
    sOverlays = ", ".join(
        sName for sName, _ in listTOverlays
    ) or _S_OVERLAYS_HEADER_NONE
    listLines = [
        S_GENERATED_MARKER,
        "#",
        f"# {S_DOCKERFILE_FILENAME} for this project's container image,",
        "# composed by vaibify from the image's own build chain:",
        S_OVERLAYS_HEADER_PREFIX + sOverlays,
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
    if sRecipeFingerprint:
        # Machine-read by the provenance check against the image's
        # S_RECIPE_IMAGE_LABEL; see fsComputeRecipeFingerprint.
        listLines.append(S_RECIPE_HEADER_PREFIX + sRecipeFingerprint)
    listLines.append("")
    return "\n".join(listLines)
