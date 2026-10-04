"""A project that names no base image is built from the Dockerfile's pin.

``ProjectConfig.sBaseImage`` defaulted to the floating tag
``ubuntu:24.04``, and the build always passed it as
``--build-arg BASE_IMAGE=...``. That argument overrides the digest the
Dockerfile pins in its own ``ARG BASE_IMAGE``, so every ``vaibify
build`` used whatever the tag pointed at that day and only a raw
``docker build`` honoured the pin. The pin existed on paper and in no
build a researcher ran.

The ruling: a project that names no base image, or names the shipped
default, is built from the digest in the Dockerfile. The Dockerfile's
``ARG`` stays the one record of that digest; nothing in Python copies it.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaibify.cli import commandBuild
from vaibify.cli.baseImagePreflight import (
    S_SUPPORTED_UBUNTU_VERSION, fpreflightBaseImage,
)
from vaibify.cli.systemPackagePreflight import fsUbuntuVersionForBaseImage
from vaibify.config.projectConfig import (
    S_DEFAULT_BASE_IMAGE, FeaturesConfig, ProjectConfig,
)
from vaibify.docker import imageBuilder

_PATH_DOCKERFILE = (
    Path(__file__).resolve().parent.parent
    / "vaibify" / "containerImage" / "Dockerfile"
)
_S_OTHER_PIN = "ubuntu:24.04@sha256:" + "b" * 64
_REGEX_DOCKERFILE_BASE_ARG = re.compile(
    r"^ARG BASE_IMAGE=(\S+)$", re.MULTILINE,
)


def _fconfigWithBase(sBaseImage, bGpu=False):
    return ProjectConfig(
        sProjectName="pinProject", sBaseImage=sBaseImage,
        features=FeaturesConfig(bGpu=bGpu),
    )


def _fsetBuildArgumentNames(listArguments):
    return {
        sArgument.split("=", 1)[0]
        for sArgument in listArguments if sArgument != "--build-arg"
    }


def _fsDockerfileDefaultBaseImage():
    matchArg = _REGEX_DOCKERFILE_BASE_ARG.search(
        _PATH_DOCKERFILE.read_text(encoding="utf-8"),
    )
    assert matchArg, "the Dockerfile lost its `ARG BASE_IMAGE=` default"
    return matchArg.group(1)


@pytest.mark.falsification
def testTheDefaultBaseImageIsNotPassedSoTheDockerfilePinApplies():
    """Kills: passing the configured tag as BASE_IMAGE unconditionally,
    which overrides the Dockerfile's digest on every `vaibify build`."""
    for sBaseImage in ("", S_DEFAULT_BASE_IMAGE):
        listArguments = imageBuilder._flistBuildArgPairs(
            _fconfigWithBase(sBaseImage), sBaseImage,
        )

        assert "BASE_IMAGE" not in _fsetBuildArgumentNames(listArguments)
        assert "PYTHON_VERSION" in _fsetBuildArgumentNames(listArguments)


@pytest.mark.falsification
def testTheBuildCommandCarriesNoBaseImageForTheDefault(tmp_path):
    """Kills: reintroducing BASE_IMAGE in the command the build runs.

    Drives the assembled ``docker build`` argv, not only the helper that
    makes the pairs, so a second place that adds the argument is seen.
    """
    config = _fconfigWithBase(S_DEFAULT_BASE_IMAGE)

    saCommand = imageBuilder._flistBuildBaseCommand(
        config, str(tmp_path), imageBuilder._fsResolveBaseImage(config),
        False,
    )

    assert "BASE_IMAGE" not in _fsetBuildArgumentNames(saCommand)
    assert saCommand[-1] == str(tmp_path)


@pytest.mark.falsification
def testAnExplicitBaseImageIsHonouredVerbatim(tmp_path):
    """Kills: swallowing every BASE_IMAGE, which would ignore a pin the
    researcher chose on purpose."""
    config = _fconfigWithBase(_S_OTHER_PIN)

    saCommand = imageBuilder._flistBuildBaseCommand(
        config, str(tmp_path), imageBuilder._fsResolveBaseImage(config),
        False,
    )

    assert f"BASE_IMAGE={_S_OTHER_PIN}" in saCommand


def testTheDockerfilePinsTheShippedDefaultByDigest():
    """The config's default tag and the Dockerfile's pin must agree.

    The build leaves BASE_IMAGE out and trusts the Dockerfile, so a
    Dockerfile that stopped pinning, or pinned another release, would
    change what every default project builds on with no config change.
    """
    sDockerfileDefault = _fsDockerfileDefaultBaseImage()

    assert re.fullmatch(
        re.escape(S_DEFAULT_BASE_IMAGE) + r"@sha256:[0-9a-f]{64}",
        sDockerfileDefault,
    ), sDockerfileDefault


def testTheSupportedUbuntuReleaseIsTheOneTheDockerfilePins():
    """The preflight's idea of "supported" is the Dockerfile's release."""
    assert fsUbuntuVersionForBaseImage(
        _fsDockerfileDefaultBaseImage(),
    ) == S_SUPPORTED_UBUNTU_VERSION
    assert fsUbuntuVersionForBaseImage(
        S_DEFAULT_BASE_IMAGE,
    ) == S_SUPPORTED_UBUNTU_VERSION


@pytest.mark.parametrize("sBaseImage", ["", S_DEFAULT_BASE_IMAGE])
def testADefaultBaseImageIsNotFloating(sBaseImage):
    """A build on the Dockerfile's digest must not warn that it floats."""
    config = SimpleNamespace(sProjectName="p", sBaseImage=sBaseImage)

    assert commandBuild.fbBaseImageIsFloating(config) is False


def testAnExplicitTagIsStillFloating():
    """A tag the researcher chose has no digest and says so."""
    config = SimpleNamespace(sProjectName="p", sBaseImage="ubuntu:noble")

    assert commandBuild.fbBaseImageIsFloating(config) is True


@pytest.mark.falsification
def testAnotherUbuntuReleaseIsRefusedBeforeAnyBuildWork():
    """Kills: dropping the release check, so jammy stops at the pinned
    apt step after the base image was fetched and layers were built."""
    for sBaseImage in (
        "ubuntu:22.04", "ubuntu:20.04", "ubuntu:26.04",
        "docker.io/library/ubuntu:22.04@sha256:" + "c" * 64,
    ):
        presult = fpreflightBaseImage(_fconfigWithBase(sBaseImage))

        assert presult is not None and presult.sLevel == "fail", sBaseImage
        assert sBaseImage in presult.sMessage
        assert S_SUPPORTED_UBUNTU_VERSION in presult.sMessage
        assert "Remove baseImage" in presult.sRemediation


@pytest.mark.parametrize("sBaseImage", [
    "", S_DEFAULT_BASE_IMAGE, _S_OTHER_PIN,
    "docker.io/library/ubuntu:24.04",
    "ubuntu@sha256:" + "d" * 64,
    "registry.example.org/mirror/ubuntu:24.04", "debian:12",
])
def testASupportedOrUnjudgeableBaseImageIsNotRefused(sBaseImage):
    """Only a name that says it is another Ubuntu release is refused.

    A digest-only reference or a mirror does not carry its release in
    its spelling, so grading it would be guessing; the Dockerfile's own
    guard still stops a base that is not Ubuntu.
    """
    assert fpreflightBaseImage(_fconfigWithBase(sBaseImage)) is None


@pytest.mark.falsification
def testAGpuBuildIsRefusedWithTheReasonAndTheRemedy():
    """Kills: dropping the GPU refusal, which would build the GPU image
    on a 22.04 base under 24.04 toolchain pins."""
    presult = fpreflightBaseImage(_fconfigWithBase(S_DEFAULT_BASE_IMAGE, True))

    assert presult is not None and presult.sLevel == "fail"
    assert presult.sMessage.startswith("GPU builds are not supported yet")
    assert "CUDA" in presult.sMessage
    assert "gpu: false" in presult.sRemediation


def testTheCliBuildRunsTheBaseImageCheck():
    assert fpreflightBaseImage in commandBuild.T_CONFIGURATION_PREFLIGHTS
