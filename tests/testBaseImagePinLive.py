"""The Docker side of the base-image pin, driven through the real builder.

``tests/testBaseImagePin.py`` shows that the build no longer passes
``BASE_IMAGE`` for a project that names none. It cannot show what Docker
does with the omission, and that is the claim the whole change rests on:
an ``ARG`` default declared before ``FROM`` must be what the build
starts from. These tests build real images through ``fnBuildBase`` -- the
function ``vaibify build`` calls, not a raw ``docker build`` -- and
compare the layers of what came out against the layers of each pinned
image.

The images are small stand-ins chosen so that the answer cannot be the
same by coincidence: the Dockerfile's default pins one image, the
explicit override names another, and an image built from either has
exactly that image's layers.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import json
import subprocess
import uuid

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config.projectConfig import FeaturesConfig, ProjectConfig
from vaibify.docker.imageBuilder import fnBuildBase

pytestmark = pytest.mark.docker_live

S_DEFAULT_STAND_IN = "busybox:1.36"
S_OVERRIDE_STAND_IN = "alpine:3.20"


def _fsRunDocker(listArguments):
    return subprocess.run(
        ["docker", *listArguments], capture_output=True, text=True,
        check=True, timeout=600,
    ).stdout.strip()


def _fsPinnedReference(sTag):
    _fsRunDocker(["pull", "--quiet", sTag])
    return _fsRunDocker(
        ["image", "inspect", "--format", "{{index .RepoDigests 0}}", sTag],
    )


def _flistLayers(sImage):
    return json.loads(_fsRunDocker(
        ["image", "inspect", "--format", "{{json .RootFS.Layers}}", sImage],
    ))


def _flistBuildBaseLayers(tmp_path, sDockerfileDefault, sBaseImage):
    """Build through fnBuildBase and return the built image's layers."""
    (tmp_path / "Dockerfile").write_text(
        f"ARG BASE_IMAGE={sDockerfileDefault}\n"
        "FROM ${BASE_IMAGE}\n"
        "ARG PYTHON_VERSION=3.12\n"
        "ARG CONTAINER_USER=researcher\n"
        "ARG WORKSPACE_ROOT=/workspace\n"
        "ARG INSTALL_LATEX=false\n"
        "ARG INSTALL_X11=true\n"
        "ARG PACKAGE_MANAGER=pip\n"
        "ARG VC_PROJECT_NAME=pinTest\n"
        "ARG APT_BUILD_SNAPSHOT=\n"
        "LABEL vaibify-pin-test=1\n",
        encoding="utf-8",
    )
    sProjectName = f"pintest{uuid.uuid4().hex[:8]}"
    config = ProjectConfig(
        sProjectName=sProjectName, sBaseImage=sBaseImage,
        features=FeaturesConfig(bGpu=False),
    )
    try:
        fnBuildBase(config, str(tmp_path), bNoCache=True)
        return _flistLayers(f"{sProjectName}:base")
    finally:
        subprocess.run(
            ["docker", "rmi", "-f", f"{sProjectName}:base"],
            capture_output=True,
        )


@pytest.mark.falsification
def testADefaultProjectIsBuiltFromTheDockerfilesPinNotTheConfiguredTag(
    tmp_path,
):
    """Kills: passing the default tag as BASE_IMAGE, which would build
    from the tag and ignore the Dockerfile's pin.

    The configured tag is the shipped default, ``ubuntu:24.04``; the
    Dockerfile's default here pins a different image. Built from the
    pin, the layers are that image's, and cannot be Ubuntu's.
    """
    fnRequireDaemonReachable()
    sPinnedStandIn = _fsPinnedReference(S_DEFAULT_STAND_IN)

    listBuiltLayers = _flistBuildBaseLayers(
        tmp_path, sPinnedStandIn, "ubuntu:24.04",
    )

    assert listBuiltLayers == _flistLayers(sPinnedStandIn)


def testAnExplicitBaseImageStillOverridesTheDockerfilesPin(tmp_path):
    """A base image the researcher names is what the build starts from."""
    fnRequireDaemonReachable()
    sPinnedStandIn = _fsPinnedReference(S_DEFAULT_STAND_IN)
    sPinnedOverride = _fsPinnedReference(S_OVERRIDE_STAND_IN)

    listBuiltLayers = _flistBuildBaseLayers(
        tmp_path, sPinnedStandIn, sPinnedOverride,
    )

    assert listBuiltLayers == _flistLayers(sPinnedOverride)
    assert listBuiltLayers != _flistLayers(sPinnedStandIn)
