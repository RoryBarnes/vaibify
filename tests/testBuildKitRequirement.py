"""Which Dockerfiles need BuildKit, and the refusal when buildx is absent.

The rules come from measurement, not from the code under test: each
syntax below was built with Docker's legacy builder
(``DOCKER_BUILDKIT=0``, Docker 28.4, 2026-10-26) and either refused or
built something else. ``tests/testLegacyBuilderLive.py`` repeats those
builds against a real daemon, so a Docker that changes its mind fails
there rather than here.
"""

import glob
import os

import pytest

from vaibify.docker import buildKitRequirement, imageBuilder, pinnedImageAcquisition
from vaibify.docker.buildKitRequirement import (
    S_FAILURE_BUILDX_MISSING,
    BuildKitRequiredError,
    flistDescribeBuildKitSyntax,
)
from vaibify.gui import acquisitionFailure


S_BASE = "FROM alpine:3.20\n"
S_CONTAINER_IMAGE_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "containerImage")
S_ARM_ON_INTEL = os.path.join(
    S_CONTAINER_IMAGE_DIRECTORY, "Dockerfile.claudeArmOnIntel")


@pytest.mark.parametrize("sDockerfile, sNamed", [
    ("FROM --platform=$BUILDPLATFORM alpine:3.20\n", "$BUILDPLATFORM"),
    ("FROM --platform=${BUILDPLATFORM} alpine:3.20\n", "$BUILDPLATFORM"),
    ("FROM --platform=$TARGETPLATFORM alpine:3.20\n", "$TARGETPLATFORM"),
    (S_BASE + "ARG TARGETARCH\nRUN echo $TARGETARCH\n", "$TARGETARCH"),
    (S_BASE + "RUN --mount=type=cache,target=/c true\n", "RUN --mount"),
    (S_BASE + "RUN --network=none true\n", "RUN --network"),
    (S_BASE + "COPY --chmod=755 f /f\n", "COPY --chmod"),
    (S_BASE + "COPY --chown=1000:1000 --chmod=755 f /f\n", "COPY --chmod"),
    (S_BASE + "COPY --parents f /\n", "COPY --parents"),
    (S_BASE + "ADD --checksum=sha256:00 f /f\n", "ADD --checksum"),
    (S_BASE + "RUN <<EOF\necho hi > /out\nEOF\n", "heredoc"),
    (S_BASE + "run --mount=type=secret,id=a true\n", "RUN --mount"),
])
def testEverySyntaxTheLegacyBuilderCannotBuildIsNamed(sDockerfile, sNamed):
    listFound = flistDescribeBuildKitSyntax(sDockerfile)
    assert len(listFound) == 1, listFound
    assert sNamed in listFound[0]


@pytest.mark.parametrize("sDockerfile", [
    S_BASE + "RUN true\n",
    S_BASE + "COPY --link f /f\n",
    S_BASE + "COPY --chown=1000:1000 f /f\n",
    S_BASE + "ARG TARGETARCH\n",
    S_BASE + 'RUN cat <<< "here-string"\n',
    "# Requires BuildKit, which provides $BUILDPLATFORM.\n" + S_BASE,
    S_BASE + "RUN set -eu; \\\n    # a comment naming $TARGETARCH\n    true\n",
])
def testSyntaxTheLegacyBuilderHandlesIsNotNamed(sDockerfile):
    assert flistDescribeBuildKitSyntax(sDockerfile) == []


def testAFlagOnAContinuationLineIsReadWithItsInstruction():
    assert flistDescribeBuildKitSyntax(
        S_BASE + "RUN \\\n    --mount=type=cache,target=/c true\n") == [
        "line 2 uses RUN --mount"]


@pytest.mark.falsification
def testTheArmOnIntelOverlayIsTheOnlyShippedDockerfileThatNeedsBuildKit():
    """Kills: a scanner that misses $BUILDPLATFORM, and one that flags all.

    Missing it lets the ARM-on-Intel overlay fail in Docker's words on a
    Mac without buildx; flagging an ordinary Dockerfile refuses every
    build on such a Mac, which builds them fine today. The oracle is
    the legacy builder's own result for each (measured; see the module
    docstring). A new Dockerfile that needs BuildKit fails here until it
    is added on purpose.
    """
    dictNeeding = {}
    for sPath in sorted(glob.glob(
            os.path.join(S_CONTAINER_IMAGE_DIRECTORY, "**", "Dockerfile*"),
            recursive=True)):
        listFound = buildKitRequirement.flistDescribeBuildKitSyntaxInFile(
            sPath)
        if listFound:
            dictNeeding[os.path.basename(sPath)] = listFound
    assert len(glob.glob(os.path.join(
        S_CONTAINER_IMAGE_DIRECTORY, "Dockerfile*"))) > 10
    assert dictNeeding == {
        "Dockerfile.claudeArmOnIntel": ["line 24 uses $BUILDPLATFORM"]}


def testAnUnreadableDockerfileNamesNothing(tmp_path):
    assert buildKitRequirement.flistDescribeBuildKitSyntaxInFile(
        str(tmp_path / "absent")) == []


@pytest.mark.falsification
def testABuildOnlyBuildxCanDoIsRefusedBeforeDockerRuns(monkeypatch):
    """Kills: handing the ARM-on-Intel overlay to the legacy builder.

    The legacy builder fails it at step 1 with "failed to parse
    platform", which names neither buildx nor a remedy.
    """
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: False)
    with pytest.raises(BuildKitRequiredError) as excinfo:
        imageBuilder._flistBuildPrefix(S_ARM_ON_INTEL)
    sSentence = str(excinfo.value)
    assert sSentence.startswith("Dockerfile.claudeArmOnIntel needs docker buildx")
    assert "line 24 uses $BUILDPLATFORM" in sSentence
    assert "vaibify setup-docker" in sSentence
    assert excinfo.value.dictFailure == {
        "sFailureClass": S_FAILURE_BUILDX_MISSING, "sSentence": sSentence}


def testWithBuildxTheSameDockerfileBuilds(monkeypatch):
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: True)
    assert imageBuilder._flistBuildPrefix(S_ARM_ON_INTEL) == [
        "docker", "buildx", "build"]


def testAnOrdinaryDockerfileStillFallsBackWithoutBuildx(monkeypatch):
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: False)
    assert imageBuilder._flistBuildPrefix(os.path.join(
        S_CONTAINER_IMAGE_DIRECTORY, "Dockerfile.claude")) == [
        "docker", "build"]


def testAnOrdinaryDockerfileNeverProbesForBuildx(monkeypatch):
    listProbes = []
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable",
                        lambda: listProbes.append(1) or False)
    imageBuilder.fnRefuseWhenBuildKitIsNeeded(os.path.join(
        S_CONTAINER_IMAGE_DIRECTORY, "Dockerfile.claude"))
    assert listProbes == []


def testTheRefusalReachesTheAcquisitionWindowAsARetryableBuildxFailure():
    errorRefusal = BuildKitRequiredError(
        S_ARM_ON_INTEL, ["line 24 uses $BUILDPLATFORM"],
        "sudo port install docker-buildx-plugin")
    dictFailure = acquisitionFailure.fdictDescribeAcquisitionFailure(
        errorRefusal)
    assert dictFailure["sFailureClass"] == S_FAILURE_BUILDX_MISSING
    assert dictFailure["sSentence"] == str(errorRefusal)
    assert dictFailure["bRetryable"] is True


def testTheArmOnIntelStackResolvesToTheBuildKitDockerfile():
    assert imageBuilder.flistStackedOverlayDockerfiles(
        ["claude"], S_CONTAINER_IMAGE_DIRECTORY, "linux/amd64", "arm64") == [
        S_ARM_ON_INTEL]
    assert imageBuilder.flistStackedOverlayDockerfiles(
        ["claude"], S_CONTAINER_IMAGE_DIRECTORY, "linux/amd64", "amd64") != [
        S_ARM_ON_INTEL]


def _fdictPinnedOnIntel():
    return {
        "bObtainable": True, "sRefusal": "", "sRequiredPlatform": "linux/amd64",
        "sPinnedImageReference": "sha256:" + "a" * 64,
    }


def _fdictProjectWantingClaude(tmp_path):
    sConfigPath = str(tmp_path / "vaibify.yml")
    with open(sConfigPath, "w", encoding="utf-8") as fileConfig:
        fileConfig.write("projectName: proj\nfeatures:\n  claude: true\n")
    return {"sName": "proj", "sDirectory": str(tmp_path),
            "sConfigPath": sConfigPath}


@pytest.mark.falsification
def testAnArmMacWithoutBuildxIsRefusedBeforeTheDownload(
        tmp_path, monkeypatch):
    """Kills: refusing only at the stack, after gigabytes were downloaded.

    The lane would otherwise obtain the author's image first; the
    refusal must come before ``_fdictObtain`` is ever called.
    """
    listObtained = []
    monkeypatch.setattr(pinnedImageAcquisition, "_fdictRecheckTheClone",
                        lambda dictProject, dictSource, fnReport: (
                            _fdictPinnedOnIntel()))
    monkeypatch.setattr(pinnedImageAcquisition, "_fdictObtain",
                        lambda *aArgs, **kwargs: listObtained.append(aArgs))
    monkeypatch.setattr(
        pinnedImageAcquisition.disposableContainer,
        "fsReadDaemonArchitecture", lambda dockerDisposable: "arm64")
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: False)
    with pytest.raises(BuildKitRequiredError):
        pinnedImageAcquisition.fdictObtainAndStage(
            _fdictProjectWantingClaude(tmp_path),
            {"listAuthorOverlays": [], "listAdditionalAgents": []},
            False, lambda sLine: None, object(),
            S_CONTAINER_IMAGE_DIRECTORY, "vaibify-staging")
    assert listObtained == []


def testAnIntelDaemonIsNeverAskedForBuildx(tmp_path, monkeypatch):
    listProbes = []
    monkeypatch.setattr(
        pinnedImageAcquisition.disposableContainer,
        "fsReadDaemonArchitecture", lambda dockerDisposable: "amd64")
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable",
                        lambda: listProbes.append(1) or False)
    pinnedImageAcquisition._fnRefuseBeforeDownloadWhenTheStackNeedsBuildx(
        _fdictProjectWantingClaude(tmp_path),
        {"listAuthorOverlays": [], "listAdditionalAgents": []},
        _fdictPinnedOnIntel(), S_CONTAINER_IMAGE_DIRECTORY, object())
    assert listProbes == []
