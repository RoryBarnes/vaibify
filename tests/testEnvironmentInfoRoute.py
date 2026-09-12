"""Admin > Environment Info describes the IMAGE, never the host recipe.

The trap this endpoint exists next to is subtle and would look
perfectly correct in review: the toolchain epoch is declared in
`vaibify/containerImage/Dockerfile` as an ARG, and that file is
sitting right there on the host, readable. Reading it would answer a
DIFFERENT question -- the epoch vaibify would build today -- and the
modal would confidently state an epoch the researcher's container
never had. So the decisive test here sets the image's label and the
host's Dockerfile to DIFFERENT values and asserts which one wins,
rather than to the same value, where the assertion would be vacuous.

The other property is that nothing is ever half-claimed. A probe that
could not reach the container yields no tool rows, not a subset, and
an absent label yields "" which the frontend renders as "unknown".
"""

import re
from pathlib import Path
from unittest import mock

from vaibify.gui.routes.systemRoutes import _fdictDescribeEnvironment


__all__ = [
    "testTheEpochComesFromTheImageNotTheHostDockerfile",
    "testAnUnlabelledImageReportsUnknownRatherThanAGuess",
    "testAFailedToolProbeYieldsNoRowsRatherThanAPartialClaim",
    "testTheIdentityIsReadFromTheConnectTimeCache",
    "testTheShippedDockerfileStampsTheEpochLabel",
]


S_CONTAINER = "env-info-container"
S_IMAGE_ID = "sha256:" + "ab" * 32

# Deliberately NOT the Dockerfile's value. See the module docstring.
S_IMAGE_EPOCH = "20240101"


def _fdictContext():
    return {
        "dictLiveImageIdentities": {
            S_CONTAINER: {
                "sImageId": S_IMAGE_ID,
                "sImageDigest": "sha256:" + "cd" * 32,
            },
        },
        "workflows": {S_CONTAINER: {"sProjectRepoPath": "/workspace/p"}},
    }


def _fnPatchProbes(sEpoch=S_IMAGE_EPOCH, dictTools=None, errorTools=None):
    """Patch every daemon/container read this helper performs."""
    sModule = "vaibify.reproducibility.environmentSnapshot"
    listPatches = [
        mock.patch(f"{sModule}.fsReadImageToolchainEpoch",
                   return_value=sEpoch),
        mock.patch(f"{sModule}.fsReadImageRecipeLabel", return_value=""),
        mock.patch(f"{sModule}.fsReadImageArchitecture",
                   return_value="arm64"),
        mock.patch("vaibify.gui.routeContext.ffilesForWorkflow",
                   return_value=object()),
        mock.patch(
            f"{sModule}.fdictCaptureSystemTools",
            side_effect=errorTools,
            **({} if errorTools else {"return_value": dictTools or {}}),
        ),
    ]
    return listPatches


def _fdictDrive(**kwargs):
    listPatches = _fnPatchProbes(**kwargs)
    for patchOne in listPatches:
        patchOne.start()
    try:
        return _fdictDescribeEnvironment(_fdictContext(), S_CONTAINER)
    finally:
        for patchOne in listPatches:
            patchOne.stop()


def testTheEpochComesFromTheImageNotTheHostDockerfile():
    """Kill-confirmed against reading the ARG off the installed file."""
    sDockerfile = (
        Path(__file__).resolve().parent.parent
        / "vaibify" / "containerImage" / "Dockerfile"
    ).read_text(encoding="utf-8")
    sHostEpoch = re.search(
        r"^ARG APT_SNAPSHOT_DATE=(\d{8})$", sDockerfile, re.M,
    ).group(1)
    assert sHostEpoch != S_IMAGE_EPOCH, (
        "the fixture must differ from the shipped value or this test "
        "passes against an implementation that reads the host file"
    )
    dictInfo = _fdictDrive()
    assert dictInfo["sToolchainEpoch"] == S_IMAGE_EPOCH
    assert dictInfo["sToolchainEpoch"] != sHostEpoch


def testAnUnlabelledImageReportsUnknownRatherThanAGuess():
    dictInfo = _fdictDrive(sEpoch="")
    assert dictInfo["sToolchainEpoch"] == ""
    assert dictInfo["sRecipeFingerprint"] == ""


def testAFailedToolProbeYieldsNoRowsRatherThanAPartialClaim():
    """A subset of tool versions reads as a statement about the ones it
    omits. An unreachable probe must produce nothing at all."""
    dictInfo = _fdictDrive(errorTools=RuntimeError("container gone"))
    assert dictInfo["dictSystemTools"] == {}


def testTheIdentityIsReadFromTheConnectTimeCache():
    """A container's image cannot change while it runs, so this must
    need no daemon call of its own -- the poll path depends on that."""
    dictInfo = _fdictDrive()
    assert dictInfo["sImageId"] == S_IMAGE_ID
    assert dictInfo["sImageDigest"] == "sha256:" + "cd" * 32
    assert dictInfo["sContainerId"] == S_CONTAINER


def testTheShippedDockerfileStampsTheEpochLabel():
    """Without the LABEL there is nothing for the reader to read, and
    every image would answer 'unknown' forever."""
    from vaibify.reproducibility.dockerfileComposer import (
        S_TOOLCHAIN_EPOCH_IMAGE_LABEL,
    )
    sDockerfile = (
        Path(__file__).resolve().parent.parent
        / "vaibify" / "containerImage" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert (
        f'LABEL {S_TOOLCHAIN_EPOCH_IMAGE_LABEL}="${{APT_SNAPSHOT_DATE}}"'
        in sDockerfile
    )
