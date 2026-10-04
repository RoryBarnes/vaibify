"""Refuse a build whose base image the pinned toolchain cannot sit on.

The image's toolchain block installs exact Ubuntu 24.04 (noble) package
versions from a dated archive snapshot. A base image that is another
Ubuntu release passes the Dockerfile's own "is this Ubuntu" guard and
then stops at the pinned ``apt-get install``, after the base has been
fetched and the first layers built. The name of the base image says
which release it is, so the refusal is made before any of that work.

The GPU feature substitutes an NVIDIA CUDA image built on Ubuntu 22.04,
which is the same wall, so it is refused here too.

A base image whose name does not say which Ubuntu release it is -- a
digest-only reference, a registry mirror -- is not judged: the check
reports nothing rather than grading an image by a spelling it does not
carry.
"""

from vaibify.config.projectConfig import fbBaseImageUsesDockerfilePin

from .preflightResult import PreflightResult, S_LEVEL_FAIL, S_SCOPE_PROJECT
from .systemPackagePreflight import fsUbuntuVersionForBaseImage


__all__ = [
    "S_PREFLIGHT_NAME",
    "S_SUPPORTED_UBUNTU_VERSION",
    "fpreflightBaseImage",
]


S_PREFLIGHT_NAME = "base-image"

# The release the Dockerfile's pinned toolchain block is written for.
# tests/testBaseImagePreflight.py binds it to the Dockerfile's own
# default base image, so the two cannot drift apart unnoticed.
S_SUPPORTED_UBUNTU_VERSION = "24.04"


def _fpreflightGpuBuildUnsupported():
    """Return the fail result for a project that asks for a GPU build."""
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            "GPU builds are not supported yet: the GPU feature builds on "
            "an NVIDIA CUDA image based on Ubuntu 22.04, and this image's "
            "pinned toolchain is Ubuntu 24.04's. NVIDIA publishes no CUDA "
            "12.2 image for Ubuntu 24.04, so moving the GPU image would "
            "change the CUDA version, a choice about the science "
            "environment, and no GPU build has been verified."
        ),
        sRemediation="Disable in vaibify.yml: `features: { gpu: false }`.",
    )


def _fpreflightOtherUbuntuRelease(sBaseImage, sUbuntuVersion):
    """Return the fail result for a base image of another Ubuntu release."""
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"baseImage '{sBaseImage}' is Ubuntu {sUbuntuVersion}, but the "
            "image's pinned toolchain is Ubuntu "
            f"{S_SUPPORTED_UBUNTU_VERSION}'s, so the build would stop at "
            "the pinned apt-get step after the base image had been fetched."
        ),
        sRemediation=(
            "Remove baseImage from vaibify.yml to build on the pinned "
            f"Ubuntu {S_SUPPORTED_UBUNTU_VERSION}, or set it to "
            f"ubuntu:{S_SUPPORTED_UBUNTU_VERSION}."
        ),
    )


def fpreflightBaseImage(config):
    """Return a fail result for an unsupported base image, else None."""
    if getattr(getattr(config, "features", None), "bGpu", False):
        return _fpreflightGpuBuildUnsupported()
    sBaseImage = getattr(config, "sBaseImage", "") or ""
    if fbBaseImageUsesDockerfilePin(sBaseImage):
        return None
    sUbuntuVersion = fsUbuntuVersionForBaseImage(sBaseImage)
    if sUbuntuVersion and sUbuntuVersion != S_SUPPORTED_UBUNTU_VERSION:
        return _fpreflightOtherUbuntuRelease(sBaseImage, sUbuntuVersion)
    return None
